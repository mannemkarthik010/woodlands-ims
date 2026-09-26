"""
The only code permitted to change stock.

Every movement in the system goes through `post_movement`. Nothing else
writes to StockMovement, and nothing at all writes to StockBalance except
this module. Keeping that true is what makes the ledger trustworthy.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from apps.catalog.models import Item
from apps.core.models import Location, User
from apps.stock.models import MovementType, StockBalance, StockMovement


class StockError(Exception):
    pass


# Implements: FR-1203, FR-1204, NFR-18.
@transaction.atomic
def post_movement(
    *,
    item: Item,
    location: Location,
    quantity: Decimal,
    movement_type: str,
    occurred_at=None,
    batch=None,
    unit_cost: Decimal | None = None,
    source=None,
    user: User | None = None,
    note: str = "",
    allow_negative: bool = True,
) -> StockMovement:
    """
    Append one movement and update the derived balance in the same
    transaction. `quantity` is signed and in the item's base unit.
    """
    if quantity == 0:
        raise StockError("A movement of zero is not a movement.")
    if not item.is_stocked:
        raise StockError(f"{item} is not a stocked item; it cannot have a balance.")

    occurred_at = occurred_at or timezone.now()

    source_type, source_id = "", None
    if source is not None:
        source_type = f"{source._meta.app_label}.{source._meta.model_name}"
        source_id = source.pk

    balance, _ = StockBalance.objects.select_for_update().get_or_create(
        item=item, location=location, defaults={"quantity": Decimal("0")}
    )

    if not allow_negative and balance.quantity + quantity < 0:
        raise StockError(f"{item} at {location}: {balance.quantity} on hand, cannot remove {abs(quantity)}.")

    movement = StockMovement.objects.create(
        item=item,
        location=location,
        batch=batch,
        movement_type=movement_type,
        quantity=quantity,
        unit_cost=unit_cost,
        occurred_at=occurred_at,
        source_type=source_type,
        source_id=source_id,
        note=note,
        created_by=user,
    )

    StockBalance.objects.filter(pk=balance.pk).update(quantity=F("quantity") + quantity)
    return movement


# Implements: FR-1204, FR-706.
@transaction.atomic
def reverse_movement(movement: StockMovement, *, user=None, reason: str = "") -> StockMovement:
    """
    Correct a mistake by writing its opposite. The original stays. This is the
    only supported way to undo anything -- nothing in this system is deleted
    or edited after the fact.
    """
    if movement.reversed_by.exists():
        raise StockError("That movement has already been reversed.")

    reversal = post_movement(
        item=movement.item,
        location=movement.location,
        quantity=-movement.quantity,
        movement_type=MovementType.CORRECTION,
        occurred_at=timezone.now(),
        batch=movement.batch,
        unit_cost=movement.unit_cost,
        user=user,
        note=reason or f"Reversal of movement {movement.pk}",
    )
    reversal.reverses = movement
    reversal.save(update_fields=["reverses"])
    return reversal


# Implements: FR-403, FR-404.
@transaction.atomic
def post_transfer(transfer, *, user=None) -> list[StockMovement]:
    """
    A transfer is two movements, always written together. Stock cannot leave
    one location without arriving at the other -- that is the whole point of
    the record.
    """
    from apps.stock.models import DocumentStatus

    if transfer.status == DocumentStatus.POSTED:
        raise StockError("This transfer has already been posted.")

    movements = []
    for line in transfer.lines.select_related("item"):
        movements.append(
            post_movement(
                item=line.item,
                location=transfer.from_location,
                quantity=-line.quantity,
                movement_type=MovementType.TRANSFER_OUT,
                occurred_at=transfer.occurred_at,
                batch=line.batch,
                source=transfer,
                user=user,
            )
        )
        movements.append(
            post_movement(
                item=line.item,
                location=transfer.to_location,
                quantity=line.quantity,
                movement_type=MovementType.TRANSFER_IN,
                occurred_at=transfer.occurred_at,
                batch=line.batch,
                source=transfer,
                user=user,
            )
        )

    transfer.status = DocumentStatus.POSTED
    transfer.save(update_fields=["status"])
    return movements


# Implements: FR-408.
def on_hand(item: Item, location: Location | None = None) -> Decimal:
    """Authoritative figure, summed from the ledger rather than read from the cache."""
    qs = StockMovement.objects.filter(item=item)
    if location is not None:
        qs = qs.filter(location=location)
    return qs.aggregate(total=Sum("quantity"))["total"] or Decimal("0")


# Implements: FR-1204.
def rebuild_balances() -> int:
    """
    Recompute every cached balance from the ledger.

    Worth being able to run at any time. If the cache and the ledger ever
    disagree, the ledger is right and this repairs the cache -- which is only
    possible because the ledger was never the thing being edited.
    """
    totals = StockMovement.objects.values("item_id", "location_id").annotate(total=Sum("quantity")).order_by()
    count = 0
    with transaction.atomic():
        StockBalance.objects.all().update(quantity=Decimal("0"))
        for row in totals:
            StockBalance.objects.update_or_create(
                item_id=row["item_id"],
                location_id=row["location_id"],
                defaults={"quantity": row["total"] or Decimal("0")},
            )
            count += 1
    return count


# ---------------------------------------------------------------------------
# Recipe explosion
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExplodedComponent:
    item: Item
    quantity: Decimal
    depth: int


# Implements: FR-602, FR-603, FR-610.
def explode(item: Item, quantity: Decimal, *, _seen=None, _depth=0) -> list[ExplodedComponent]:
    """
    Walk a dish down to the things that are actually held in stock.

    A masala dosa explodes into dosa batter and potato masala; dosa batter
    explodes further into rice, urad dal and fenugreek. Recursion stops at
    any item with no active recipe -- those are the leaves, and they are what
    gets depleted.

    Cycle-protected. A recipe that somehow contained itself would otherwise
    recurse until the process died, and the guard costs nothing.
    """
    from apps.catalog.models import Recipe

    _seen = _seen or set()
    if item.pk in _seen:
        raise StockError(f"Recipe loop detected at {item}.")
    if _depth > 12:
        raise StockError(f"Recipe nested more than 12 deep at {item}; refusing to continue.")

    recipe = Recipe.objects.filter(item=item, is_active=True).prefetch_related("lines").first()
    if recipe is None or not recipe.yield_quantity:
        return [ExplodedComponent(item=item, quantity=quantity, depth=_depth)]

    out: list[ExplodedComponent] = []
    scale = quantity / recipe.yield_quantity
    for line in recipe.lines.select_related("component"):
        out.extend(
            explode(
                line.component,
                line.quantity * scale,
                _seen=_seen | {item.pk},
                _depth=_depth + 1,
            )
        )
    return out


# Implements: FR-704, FR-706.
@transaction.atomic
def post_count(count, *, user=None) -> list[StockMovement]:
    """
    Commit a physical count.

    A count does not set stock to the counted figure -- it writes the
    difference as an adjustment movement, so the ledger still explains how
    the balance got where it is. Setting a balance directly would lose that,
    and losing it is the whole reason the restaurant cannot currently say
    where anything went.

    Lines left blank were not counted. They are skipped rather than treated
    as zero, which is the single most destructive assumption a counting
    system can make.
    """
    from apps.stock.models import DocumentStatus

    if count.status == DocumentStatus.POSTED:
        raise StockError("This count has already been posted.")

    movements = []
    for line in count.lines.select_related("item"):
        if line.counted_quantity is None:
            continue
        difference = line.counted_quantity - line.expected_quantity
        if difference == 0:
            continue
        movements.append(
            post_movement(
                item=line.item,
                location=count.location,
                quantity=difference,
                movement_type=MovementType.COUNT_ADJUSTMENT,
                occurred_at=count.counted_at,
                source=count,
                user=user,
                note=line.note or f"Counted {line.counted_quantity}, expected {line.expected_quantity}",
            )
        )

    count.status = DocumentStatus.POSTED
    count.save(update_fields=["status"])
    return movements


# Implements: FR-701, FR-703.
def build_count_sheet(count, *, items=None) -> int:
    """
    Fill a count sheet with the items to be counted, and snapshot what the
    system believes is there at this moment.

    Snapshotting at generation rather than at posting matters: the variance
    should be measured against what was believed when somebody walked the
    shelves, not against what it drifted to while the sheet sat half-finished
    in a pocket.
    """
    from apps.catalog.models import CountEvery, Item
    from apps.stock.models import StockCount, StockCountLine

    if items is None:
        # Each count holds only its own items: the daily count is what the
        # kitchen makes, not every sack in the building. An ad hoc count
        # takes everything that is counted at all.
        items = Item.objects.filter(is_active=True, is_stocked=True).exclude(count_every=CountEvery.NEVER)
        on_this_count = {
            StockCount.Cadence.DAILY: CountEvery.DAILY,
            StockCount.Cadence.WEEKLY: CountEvery.WEEKLY,
            StockCount.Cadence.MONTHLY: CountEvery.MONTHLY,
        }.get(count.cadence)
        if on_this_count:
            items = items.filter(count_every=on_this_count)
        items = items.select_related("base_unit", "category").order_by("category__sort_order", "name")

    balances = {b.item_id: b.quantity for b in StockBalance.objects.filter(location=count.location)}

    StockCountLine.objects.bulk_create(
        [
            StockCountLine(
                count=count,
                item=item,
                expected_quantity=balances.get(item.pk, Decimal("0")),
            )
            for item in items
        ]
    )
    return count.lines.count()


# Implements: FR-702, FR-204.
def record_counted(line, *, quantity: Decimal | None, measure=None) -> None:
    """
    One line of a count, as the person typed it: a number and what they
    counted in -- buckets, bags, cases, or the base unit. The line keeps both
    what was typed and what it comes to, so "3 buckets" is never lost inside
    "96 lb". None clears the line back to not counted.
    """
    if measure is not None and measure.item_id != line.item_id:
        raise StockError("That measure belongs to a different item.")
    if quantity is not None and quantity < 0:
        raise StockError("A count cannot be negative.")
    line.entered_quantity = quantity
    line.entered_measure = measure if quantity is not None else None
    if quantity is None:
        line.counted_quantity = None
    elif measure is None:
        line.counted_quantity = quantity
    else:
        line.counted_quantity = (quantity * measure.quantity_in_base_units).quantize(Decimal("0.0001"))
    line.save(update_fields=["entered_quantity", "entered_measure", "counted_quantity"])


# Implements: FR-701.
@transaction.atomic
def start_count(*, location: Location, cadence: str, user: User | None = None):
    """
    A new count sheet for one place and one rhythm. An earlier sheet of the
    same kind left unfinished is retired, not continued: it was made from
    that day's list and that day's stock, and a daily count from last week
    is not today's daily count. It is kept, marked void, saying what replaced it.
    """
    from apps.stock.models import DocumentStatus, StockCount

    count = StockCount.objects.create(
        location=location, cadence=cadence, counted_at=timezone.now(), created_by=user
    )
    build_count_sheet(count)
    if cadence != StockCount.Cadence.ADHOC:
        for old in StockCount.objects.filter(
            location=location, cadence=cadence, status=DocumentStatus.DRAFT
        ).exclude(pk=count.pk):
            old.status = DocumentStatus.VOIDED
            old.note = (old.note + "\n" if old.note else "") + f"Not finished; replaced by count {count.pk}."
            old.save(update_fields=["status", "note"])
    return count
