"""
Operations on the catalogue that are too consequential to be an admin edit.

Both of these exist because of what happened the first time 318 till names
were mapped by hand in one sitting: the menu spells "Chana Masala" two ways,
so one food became two items; and a tub of sambar sold over the counter looked
like a dish, so it became a separate thing from the sambar that goes into
Idly Sambar. It is the same pot. Neither mistake is avoidable by being careful
-- there were 250 decisions -- so both need a way back.

The rule both obey: **history is never rewritten.** An item that has stock
movements against it has a past, and merging or re-basing it would silently
change what the ledger says happened. Both operations refuse in that case and
say why.
"""

from __future__ import annotations

import re
from decimal import Decimal

from django.db import transaction
from django.utils.text import slugify

from apps.catalog.models import Item, ItemAlias, ItemKind, Unit


class CatalogError(Exception):
    pass


def _has_history(item: Item) -> bool:
    from apps.stock.models import StockMovement

    return StockMovement.objects.filter(item=item).exists()


@transaction.atomic
def merge_items(source: Item, target: Item, *, user=None) -> dict:
    """
    Fold `source` into `target` and retire it.

    The source's name survives as an alias on the target, which is the point:
    somebody typed "Channa Masala" into the till for years and will type it
    into the search box too. Losing the spelling would make the item harder to
    find, not cleaner (FR-202).
    """
    if source.pk == target.pk:
        raise CatalogError("An item cannot be merged into itself.")
    if _has_history(source):
        raise CatalogError(
            f"{source.name} already has stock movements against it. Merging would "
            f"rewrite what the ledger says happened. Correct the movements instead."
        )

    from apps.catalog.models import ItemMeasure, ParLevel, Recipe, RecipeLine
    from apps.sales.models import PosItem

    moved = {
        "pos_items": PosItem.objects.filter(item=source).update(item=target),
        "recipe_lines": RecipeLine.objects.filter(component=source).update(component=target),
        "recipes": Recipe.objects.filter(item=source).update(item=target),
        "measures": ItemMeasure.objects.filter(item=source).update(item=target),
        "par_levels": ParLevel.objects.filter(item=source).update(item=target),
        "aliases": ItemAlias.objects.filter(item=source).update(item=target),
    }

    if source.name.lower() != target.name.lower():
        ItemAlias.objects.get_or_create(
            item=target, alias=source.name, defaults={"source": "Merged from a duplicate item"}
        )

    # Retired, not deleted. A code that was in use should not become available
    # again, and somebody will want to know where the item went.
    source.is_active = False
    source.notes = (source.notes + f"\nMerged into {target.name} ({target.code}).").strip()
    source.save(update_fields=["is_active", "notes", "updated_at"])

    return moved


@transaction.atomic
def convert_to_component(item: Item, unit: Unit, *, user=None) -> int:
    """
    Turn a dish into a prepared component measured in a real unit.

    A dish is a plate of food: one sale, one dish, quantities in its recipe. A
    prepared component is a quantity of something the kitchen made -- sambar,
    batter, chutney -- which is both sold over the counter by the tub and used
    inside other dishes. Anything sold in ounces is the second kind, and
    leaving it as the first means a 4 oz tub and a 32 oz tub deplete the same
    amount, and the tub sambar is a different thing from the dish sambar.

    Returns the number of POS lines whose quantity per sale was recomputed.
    """
    if _has_history(item):
        raise CatalogError(
            f"{item.name} already has stock movements in {item.base_unit.code}. Changing "
            f"the unit it is held in would change every one of them. This needs a data "
            f"migration, not a screen."
        )

    from apps.sales.models import PosItem
    from apps.sales.services import quantity_for

    item.kind = ItemKind.PREPARED
    item.base_unit = unit
    item.is_stocked = True  # unlike a dish, this is held on a shelf
    item.full_clean(exclude=["code"])
    item.save(update_fields=["kind", "base_unit", "is_stocked", "updated_at"])

    recomputed = 0
    for pos in PosItem.objects.filter(item=item):
        quantity = quantity_for(pos.pos_name, item)
        if quantity != pos.quantity_per_sale:
            pos.quantity_per_sale = quantity
            pos.save(update_fields=["quantity_per_sale", "updated_at"])
            recomputed += 1
    return recomputed


@transaction.atomic
def retire_item(item: Item, *, reason: str = "") -> None:
    """
    Take an item out of use without deleting it.

    Deletion is not offered anywhere in this system. A code that was in use
    should not become available again, and "where did that item go?" should
    always have an answer. An item with movements against it especially: the
    ledger refers to it, and the ledger is the record.
    """
    item.is_active = False
    if reason:
        item.notes = (item.notes + "\n" + reason).strip()
    item.save(update_fields=["is_active", "notes", "updated_at"])


# Implements: FR-204, FR-205.
def in_base_units(item: Item, quantity: Decimal, measure=None) -> Decimal:
    """
    "3 buckets" of sambar, "2 cases" of toor dal, as the item's base unit.
    No measure means the quantity is already in the base unit. A measure
    belonging to another item is refused: a bucket of sambar is not a bucket
    of rasam, which is the whole reason measures belong to items.
    """
    if measure is None:
        return quantity
    if measure.item_id != item.pk:
        raise ValueError(f"{measure.name} is a measure of another item, not {item}.")
    return (quantity * measure.quantity_in_base_units).quantize(Decimal("0.0001"))


# --- Owners managing what is counted -----------------------------------------

# What an owner can add, in the words they would use, and what it becomes.
# A vegetable is a grocery in the "Fresh produce" category, which is what puts
# it on the weekly count by default (models.default_count_every).
ADDABLE = {
    "PREPARED": ("Made in the kitchen", ItemKind.PREPARED, "Bases and gravies (in-house)", "prep"),
    "RAW": ("Grocery", ItemKind.RAW, "", "raw"),
    "VEGETABLE": ("Vegetable or herb", ItemKind.RAW, "Fresh produce", "veg"),
    "PACKAGING": ("Packaging or disposable", ItemKind.PACKAGING, "Packaging", "pack"),
}


class ItemError(Exception):
    """Raised with a sentence that can be shown to the owner as it is."""

    def __init__(self, message: str, *, existing: Item | None = None):
        super().__init__(message)
        self.existing = existing


def _same_name(name: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", name.casefold()).split())


def find_by_name(name: str) -> Item | None:
    """An item already called this, ignoring capitals, spacing and punctuation -- active or not."""
    key = _same_name(name)
    if not key:
        return None
    # A few hundred items: comparing them all is instant, and it catches
    # "Toor  dal" and "toor-dal" that a database lookup would not.
    # Sample data (seed_demo, codes starting DEMO-) never counts: a demo
    # "Onions" must not stop the restaurant adding its real onions.
    real = Item.objects.exclude(code__startswith="DEMO-")
    return next((item for item in real if _same_name(item.name) == key), None)


# Implements: FR-201, FR-204.
@transaction.atomic
def add_item(
    *,
    name: str,
    what: str,
    base_unit: Unit,
    count_every: str,
    pack_name: str = "",
    pack_quantity: Decimal | None = None,
    user=None,
) -> Item:
    """
    A new thing to count, from the owner: what it is, the unit it is kept
    in, which list it goes on, and -- if they know it -- how it comes or what
    it is kept in ("case" of 40 lb, "bucket" of 32 lb), so it can be counted
    in cases or buckets from the first day.
    """
    from apps.catalog.models import CountEvery, ItemCategory, ItemMeasure, MeasureKind

    name = " ".join((name or "").split())
    if not name:
        raise ItemError("Please give the item a name.")
    if what not in ADDABLE:
        raise ItemError("Please choose what kind of item it is.")
    if count_every not in CountEvery.values:
        raise ItemError("Please choose which list it goes on.")
    existing = find_by_name(name)
    if existing:
        state = "" if existing.is_active else " but is no longer used — bring it back instead"
        raise ItemError(f"{existing.name} is already on the system{state}.", existing=existing)
    pack_name = " ".join((pack_name or "").split())
    if bool(pack_name) != (pack_quantity is not None):
        raise ItemError("For how it comes, give both the name (e.g. case) and how much one holds.")
    if pack_quantity is not None and pack_quantity <= 0:
        raise ItemError("How much one holds must be more than zero.")

    label, kind, category_name, prefix = ADDABLE[what]
    category = ItemCategory.objects.get_or_create(name=category_name)[0] if category_name else None
    stem = f"{prefix}-{slugify(name)}"[:44] or prefix
    code, n = stem, 2
    while Item.objects.filter(code=code).exists():
        code, n = f"{stem}-{n}", n + 1
    item = Item.objects.create(
        code=code,
        name=name,
        kind=kind,
        category=category,
        base_unit=base_unit,
        count_every=count_every,
        created_by=user,
    )
    ItemAlias.objects.get_or_create(item=item, alias=name, defaults={"source": "owner"})
    if pack_name:
        ItemMeasure.objects.create(
            item=item,
            name=pack_name,
            kind=MeasureKind.KITCHEN if kind == ItemKind.PREPARED else MeasureKind.PURCHASE,
            quantity_in_base_units=pack_quantity,
            created_by=user,
        )
    return item


# Implements: FR-701.
def move_to_list(item: Item, count_every: str) -> Item:
    from apps.catalog.models import CountEvery

    if count_every not in CountEvery.values:
        raise ItemError("That is not one of the lists.")
    if item.kind == ItemKind.DISH:
        raise ItemError("Dishes are not counted; what they use comes from sales.")
    item.count_every = count_every
    item.save(update_fields=["count_every", "updated_at"])
    return item


def bring_back(item: Item) -> Item:
    """An item stopped earlier, in use again -- on the list it was on before."""
    item.is_active = True
    item.save(update_fields=["is_active", "updated_at"])
    return item
