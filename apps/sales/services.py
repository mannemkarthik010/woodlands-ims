"""
Turning three hundred POS names into something the stock system can act on.

The Shift4 menu has 318 lines. They are not 318 foods. "Masala Dosa",
"$10 Masala Dosa", "DosaNights-Masala Dosa", "Weekday Lunch Masala Dosa" and
"NYPF - Masala Dosa" are one plate of food sold under five promotions, and
"Coconut Chutney" at 4, 8 and 16 oz is one chutney sold in three tubs.

So the work is not 318 decisions. It is roughly 250, and this module is what
makes each one a single action instead of five.

WHAT IS AUTOMATIC AND WHAT IS NOT

Grouping is automatic: names that reduce to the same food after promotional
prefixes and sizes are stripped are shown together. Grouping is safe to
automate because a wrong group is *visible* -- five dosas in one card, one of
which is obviously not a dosa.

Mapping is not automatic, ever. Which dish a name refers to is a judgement,
and a wrong mapping is invisible for months while it quietly poisons every
variance figure downstream. The most this module will do is say "there is
already an item called Chana Masala, did you mean that one?" and wait to be
told. See ADR 0002 and the third invariant in docs/architecture.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from difflib import SequenceMatcher

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

from apps.catalog.models import Item, ItemKind, Unit, UnitKind
from apps.sales.models import PosItem
from apps.sales.naming import (  # noqa: F401  (re-exported)
    FLUID_OUNCE_ML,
    OUNCE_G,
    SIZE_RE,
    normalise,
    size_in_name,
)


class MappingError(Exception):
    pass


def quantity_for(pos_name: str, item: Item, *, ounces: Decimal | None = None) -> Decimal:
    """
    How much of `item` one sale of `pos_name` consumes, in the item's OWN base
    unit.

    A dish is one dish -- the recipe carries the quantities, and a bigger
    plate is a different dish. A tub is whatever the label says, converted:
    "Coconut Chutney 16 oz" against a chutney held in quarts is 0.5, not 16.

    That conversion is the whole point of this function. Carrying "16"
    straight across to an item measured in quarts would be a thirty-twofold
    error, stated confidently, in a number nobody would think to re-check.
    Whether an ounce is fluid or weight is settled by what the item is
    measured in, which is the only evidence there is.
    """
    ounces = size_in_name(pos_name) if ounces is None else ounces
    unit = item.base_unit
    if ounces is None or unit.kind == UnitKind.COUNT or not unit.to_canonical:
        return Decimal("1")

    per_ounce = FLUID_OUNCE_ML if unit.kind == UnitKind.VOLUME else OUNCE_G
    return (ounces * per_ounce / unit.to_canonical).quantize(Decimal("0.0001"))


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------


@dataclass
class Group:
    """One decision. Usually several POS lines, sometimes one."""

    key: str
    lines: list[PosItem]
    # Filled in by the view that is about to render this group. Left off the
    # queue query on purpose: every suggestion is a scan of the catalogue, and
    # sixty of them on a page load for a list most of which is scrolled past
    # is work for nobody.
    suggestions: list = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.key.title()

    @property
    def categories(self) -> str:
        return ", ".join(sorted({p.pos_category for p in self.lines if p.pos_category}))

    @property
    def has_sizes(self) -> bool:
        return any(size_in_name(p.pos_name) is not None for p in self.lines)

    @property
    def price_range(self) -> str:
        prices = sorted(p.default_price for p in self.lines if p.default_price is not None)
        if not prices:
            return ""
        if prices[0] == prices[-1]:
            return f"${prices[0]:.2f}"
        return f"${prices[0]:.2f}–${prices[-1]:.2f}"

    @property
    def mapped_to(self) -> Item | None:
        for line in self.lines:
            if line.item_id:
                return line.item
        return None

    @property
    def is_ignored(self) -> bool:
        return all(line.ignore for line in self.lines)


def groups(*, done: bool = False, category: str = "", query: str = "") -> list[Group]:
    """
    The work queue, biggest groups first -- most lines cleared per decision.

    `done=True` returns what has already been decided, because a decision
    that cannot be revisited is a decision nobody will make quickly.
    """
    qs = PosItem.objects.select_related("item", "item__base_unit")
    if done:
        qs = qs.filter(Q(item__isnull=False) | Q(ignore=True))
    else:
        qs = qs.filter(item__isnull=True, ignore=False)

    if category:
        qs = qs.filter(pos_category=category)
    if query:
        qs = qs.filter(pos_name__icontains=query)

    buckets: dict[str, list[PosItem]] = {}
    for pos in qs.order_by("pos_name"):
        buckets.setdefault(pos.group_key or normalise(pos.pos_name), []).append(pos)

    return sorted(
        (Group(key=k, lines=v) for k, v in buckets.items()),
        key=lambda g: (-len(g.lines), g.key),
    )


def group_lines(key: str) -> list[PosItem]:
    """Every POS line in a group, decided or not."""
    return list(
        PosItem.objects.select_related("item", "item__base_unit").filter(group_key=key).order_by("pos_name")
    )


def progress() -> dict:
    qs = PosItem.objects.all()
    total = qs.count()
    mapped = qs.filter(item__isnull=False).count()
    ignored = qs.filter(item__isnull=True, ignore=True).count()
    return {
        "total": total,
        "mapped": mapped,
        "ignored": ignored,
        "remaining": total - mapped - ignored,
        "groups_remaining": len(groups()),
    }


# ---------------------------------------------------------------------------
# Suggestions -- offered to a person, never applied
# ---------------------------------------------------------------------------


def suggestions(key: str, limit: int = 3) -> list[Item]:
    """
    Items whose names look like this group's.

    This exists because the menu spells the same food two ways -- "Chana
    Masala 16 oz" and "Channa Masala 4 oz" do not group together, and without
    a nudge somebody creates two items for one food and the variance report
    is quietly wrong from then on.

    It returns a shortlist for a human to look at. Nothing here writes
    anything, and a suggestion is never taken automatically.
    """
    candidates = Item.objects.filter(
        kind__in=[ItemKind.DISH, ItemKind.PREPARED], is_active=True
    ).select_related("base_unit")

    scored = []
    for item in candidates:
        score = SequenceMatcher(None, key, normalise(item.name)).ratio()
        if score >= 0.82:
            scored.append((score, item))
    scored.sort(key=lambda pair: -pair[0])
    return [item for _, item in scored[:limit]]


# ---------------------------------------------------------------------------
# The decisions themselves
# ---------------------------------------------------------------------------


@transaction.atomic
def map_group(key: str, *, item: Item, user=None, quantities: dict[int, Decimal] | None = None) -> int:
    """
    Point every line in a group at one item.

    `quantities` carries corrected tub sizes, IN OUNCES, as they appear on the
    screen -- what somebody reads off a label is ounces, whatever the item
    happens to be measured in. The conversion into the item's base unit
    happens here, once, in `quantity_for`.
    """
    lines = [p for p in group_lines(key) if not p.ignore]
    if not lines:
        raise MappingError(f"Nothing to map in '{key}'.")

    quantities = quantities or {}
    now = timezone.now()
    for pos in lines:
        pos.item = item
        pos.quantity_per_sale = quantity_for(pos.pos_name, item, ounces=quantities.get(pos.pk))
        pos.mapped_by = user
        pos.mapped_at = now
        pos.save(update_fields=["item", "quantity_per_sale", "mapped_by", "mapped_at", "updated_at"])
    return len(lines)


@transaction.atomic
def ignore_group(key: str, *, user=None) -> int:
    """
    Mark a group as never stock-bearing -- a corkage line, a gift card, a
    plate charge. Ignoring is a decision, recorded like any other, and it is
    reversible.
    """
    lines = group_lines(key)
    if not lines:
        raise MappingError(f"No such group: '{key}'.")
    now = timezone.now()
    for pos in lines:
        pos.ignore = True
        pos.item = None
        pos.mapped_by = user
        pos.mapped_at = now
        pos.save(update_fields=["ignore", "item", "mapped_by", "mapped_at", "updated_at"])
    return len(lines)


@transaction.atomic
def reopen_group(key: str) -> int:
    """
    Put a group back in the queue. Somebody will get one of these wrong, and
    the cost of fixing it has to be one button.
    """
    lines = group_lines(key)
    for pos in lines:
        pos.item = None
        pos.ignore = False
        pos.quantity_per_sale = Decimal("1")
        pos.mapped_by = None
        pos.mapped_at = None
        pos.save(
            update_fields=[
                "item",
                "ignore",
                "quantity_per_sale",
                "mapped_by",
                "mapped_at",
                "updated_at",
            ]
        )
    return len(lines)


def unique_code(base: str) -> str:
    """A slug nobody is using yet. Collisions are likely: two 'Special' anything."""
    root = slugify(base)[:44] or "item"
    code = f"dish-{root}"[:48]
    suffix = 2
    while Item.objects.filter(code=code).exists():
        tail = f"-{suffix}"
        code = f"dish-{root}"[: 48 - len(tail)] + tail
        suffix += 1
    return code


@transaction.atomic
def create_dish(name: str, *, category=None) -> Item:
    """
    Create the menu dish a group refers to.

    Most of the menu does not exist in the catalogue yet, and asking somebody
    to leave this screen, create an item, and come back 250 times is how a
    mapping queue never gets finished. The dish is created with no recipe --
    recipes come from the chef, later, and an empty one is honest about that.
    """
    name = " ".join(name.split())
    if not name:
        raise MappingError("A dish needs a name.")

    each = Unit.objects.filter(code="each").first()
    if each is None:
        raise MappingError("No 'each' unit exists. Run `python manage.py seed` first.")

    return Item.objects.create(
        code=unique_code(name),
        name=name,
        kind=ItemKind.DISH,
        category=category,
        base_unit=each,
        is_stocked=False,  # a dish is sold, not held -- see Item.clean()
    )


# ---------------------------------------------------------------------------
# The second look
# ---------------------------------------------------------------------------


@dataclass
class Flag:
    """Something that looks wrong, with enough context to judge it in one read."""

    kind: str
    headline: str
    detail: str
    item: Item | None = None
    other: Item | None = None
    lines: list = field(default_factory=list)


def review() -> list[Flag]:
    """
    What to look at again after a long mapping session.

    Two hundred and fifty decisions in one sitting produces a predictable set
    of mistakes, and none of them are carelessness. This finds them:

      * A tub sold in ounces pointing at an item counted in "each" -- so the
        4 oz and the 32 oz deplete the same amount, and the tub sambar is a
        different thing from the sambar in the dish.
      * The same food spelled two ways, now two items. The variance report
        would show half the usage against each, and neither would make sense.
      * Items nothing points at, left behind by a decision that was changed.
      * A quantity of one against an item measured in ounces -- possible, but
        usually a size that was never filled in.

    Everything here is a question, not a correction. Nothing is changed until
    somebody presses something.
    """
    from apps.catalog.models import Recipe
    from apps.stock.models import StockMovement

    flags: list[Flag] = []
    lines = list(PosItem.objects.filter(item__isnull=False).select_related("item", "item__base_unit"))

    # 1. Sold by the ounce, counted by the one.
    by_item: dict[int, list[PosItem]] = {}
    for pos in lines:
        if pos.detected_size and pos.item.base_unit.kind == UnitKind.COUNT:
            by_item.setdefault(pos.item_id, []).append(pos)
    drink_classes = {"beer", "liquor", "wine", "beverage", "beverages"}
    for rows in by_item.values():
        item = rows[0].item
        sizes = ", ".join(f"{r.detected_size:g} oz" for r in sorted(rows, key=lambda r: r.detected_size))
        is_drink = all(
            (r.revenue_class or "").lower() in drink_classes
            or (r.pos_category or "").lower() in drink_classes
            for r in rows
        )
        if is_drink:
            detail = (
                f"{sizes} all deplete the same amount as each other. This is a drink, "
                f"though — a 12 oz bottle and a 22 oz bottle are usually two things that "
                f"are bought, not one thing that is poured. Worth settling with the owners "
                f"whether bar stock is being tracked at all before changing anything."
            )
        else:
            detail = (
                f"{sizes} all deplete the same amount as each other. If this is something "
                f"the kitchen makes, it is a prepared component measured in ounces — and "
                f"then it is the same stock that goes into the dishes, not a separate thing."
            )
        flags.append(
            Flag(
                kind="sized-drink" if is_drink else "sized",
                headline=f"{item.name} is sold by the ounce but counted in “{item.base_unit.code}”",
                detail=detail,
                item=item,
                lines=rows,
            )
        )

    # 2. The same food, spelled twice.
    # Demo fixtures are excluded. They are seeded to click around with and
    # removed by `seed_demo --clear`; flagging them as duplicates of the real
    # catalogue would bury the findings that matter under scaffolding.
    active = list(
        Item.objects.filter(is_active=True, kind__in=[ItemKind.DISH, ItemKind.PREPARED])
        .exclude(code__istartswith="demo-")
        .select_related("base_unit")
        .order_by("name")
    )
    counts = {i.pk: 0 for i in active}
    for pos in lines:
        if pos.item_id in counts:
            counts[pos.item_id] += 1

    seen = set()
    for a_index, first in enumerate(active):
        for second in active[a_index + 1 :]:
            pair = (first.pk, second.pk)
            if pair in seen:
                continue
            one, two = normalise(first.name), normalise(second.name)
            if SequenceMatcher(None, one, two).ratio() < 0.9:
                continue
            # "Poori 2Pc" and "Poori 4Pc" are 90% the same string and 100%
            # different products, and so are the 4- and 8-serving family
            # packages. Where the numbers differ, the numbers are the point.
            if re.findall(r"\d+", one) != re.findall(r"\d+", two):
                continue
            seen.add(pair)
            # Fold the one fewer till lines point at into the other.
            source, target = sorted([first, second], key=lambda i: counts.get(i.pk, 0))
            flags.append(
                Flag(
                    kind="duplicate",
                    headline=f"“{source.name}” and “{target.name}” look like one food",
                    detail=(
                        f"{counts.get(source.pk, 0)} till line(s) point at {source.name}, "
                        f"{counts.get(target.pk, 0)} at {target.name}. Merging keeps "
                        f"“{source.name}” as a searchable name on {target.name} — nobody "
                        f"loses the spelling they are used to typing."
                    ),
                    item=source,
                    other=target,
                )
            )

    # 3. Left behind by a decision that was changed.
    with_recipe = set(Recipe.objects.values_list("item_id", flat=True))
    with_history = set(StockMovement.objects.values_list("item_id", flat=True).distinct())
    for item in active:
        untouched = counts.get(item.pk, 0) == 0 and item.pk not in with_recipe and item.pk not in with_history
        if untouched and item.kind == ItemKind.DISH and item.code.startswith("dish-"):
            flags.append(
                Flag(
                    kind="orphan",
                    headline=f"Nothing points at “{item.name}”",
                    detail=(
                        "No till line, no recipe, no stock. Usually what is left when a "
                        "mapping decision was made and then changed."
                    ),
                    item=item,
                )
            )

    # 4. One ounce per sale is possible, but usually means nobody filled it in.
    unfilled = [
        pos
        for pos in lines
        if pos.item.base_unit.kind != UnitKind.COUNT and pos.quantity_per_sale == Decimal("1")
    ]
    if unfilled:
        flags.append(
            Flag(
                kind="quantity",
                headline=f"{len(unfilled)} line(s) deplete exactly one {'unit'}",
                detail=(
                    "Each of these takes one unit of something measured by volume or weight "
                    "— one fluid ounce of sambar, for instance. Possible, but it usually "
                    "means the serving size is still a question for the chef."
                ),
                lines=unfilled,
            )
        )

    order = {"sized": 0, "duplicate": 1, "quantity": 2, "sized-drink": 3, "orphan": 4}
    return sorted(flags, key=lambda f: (order.get(f.kind, 9), f.headline))
