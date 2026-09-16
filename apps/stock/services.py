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


def on_hand(item: Item, location: Location | None = None) -> Decimal:
    """Authoritative figure, summed from the ledger rather than read from the cache."""
    qs = StockMovement.objects.filter(item=item)
    if location is not None:
        qs = qs.filter(location=location)
    return qs.aggregate(total=Sum("quantity"))["total"] or Decimal("0")


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
