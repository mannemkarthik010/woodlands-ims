"""
"Made today": recording what the kitchen made, and what went into it.

A batch is started by tapping what is being made, filled in with how much
came out -- in the kitchen's containers, "2 buckets" -- and, where the cook
knows it, what went in. Finishing it writes the stock: the made item goes up
at the restaurant, and everything that went in comes down.

What went in is optional for now. The chef's recipes are not loaded yet, and
recording nothing is more honest than recording a guess (see the note on
ProductionInput). Once recipes exist, they can offer what went in as a
starting point -- still to be confirmed, never assumed.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.catalog.models import Item, ItemKind
from apps.catalog.services import in_base_units
from apps.core.models import Location, User
from apps.production.models import BatchStatus, ProductionBatch, ProductionInput
from apps.stock.models import MovementType
from apps.stock.services import post_movement

# What can go into a batch: groceries, and other things the kitchen made --
# kurma uses sambar powder, butter masala uses basic gravy.
INPUT_KINDS = (ItemKind.RAW, ItemKind.PREPARED, ItemKind.CONSUMABLE)


class BatchError(Exception):
    """Raised with a sentence that can be shown to the cook as it is."""


def _code(item: Item, when) -> str:
    stem = f"{item.code.upper()[:24]}-{timezone.localdate(when):%y%m%d}"
    n = ProductionBatch.objects.filter(batch_code__startswith=stem).count() + 1
    return f"{stem}-{n}"


# Implements: FR-501.
def start_batch(item: Item, *, location: Location, by: User | None = None) -> ProductionBatch:
    if item.kind != ItemKind.PREPARED:
        raise BatchError(f"{item} is not something the kitchen makes.")
    now = timezone.now()
    return ProductionBatch.objects.create(
        item=item,
        batch_code=_code(item, now),
        location=location,
        started_at=now,
        status=BatchStatus.IN_PROGRESS,
        produced_by=by,
        created_by=by,
    )


def _open(batch: ProductionBatch) -> ProductionBatch:
    batch = ProductionBatch.objects.select_for_update().get(pk=batch.pk)
    if batch.status != BatchStatus.IN_PROGRESS:
        raise BatchError("This batch has already been recorded.")
    return batch


# Implements: FR-502.
@transaction.atomic
def add_input(batch: ProductionBatch, *, item: Item, quantity: Decimal, measure=None) -> ProductionInput:
    """One thing that went in: "1 bag" of toor dal. The same again adds to it."""
    batch = _open(batch)
    if item.kind not in INPUT_KINDS or item.pk == batch.item_id:
        raise BatchError(f"{item} cannot go into {batch.item}.")
    if quantity is None or quantity <= 0:
        raise BatchError("Enter a quantity greater than zero.")
    try:
        base = in_base_units(item, quantity, measure)
    except ValueError as e:
        raise BatchError(str(e)) from None
    line = batch.inputs.filter(item=item, entered_measure=measure).first()
    if line:
        line.entered_quantity += quantity
        line.quantity += base
        line.save(update_fields=["entered_quantity", "quantity"])
        return line
    return ProductionInput.objects.create(
        batch=batch, item=item, quantity=base, entered_quantity=quantity, entered_measure=measure
    )


@transaction.atomic
def remove_input(batch: ProductionBatch, input_pk: int) -> None:
    _open(batch).inputs.filter(pk=input_pk).delete()


# Implements: FR-503, FR-504, FR-506.
@transaction.atomic
def finish_batch(
    batch: ProductionBatch, *, quantity: Decimal, measure=None, by: User | None = None, note: str = ""
) -> ProductionBatch:
    """
    How much came out, and the stock that follows from it: the made item up
    by that much, every input down by what went in. All or nothing.
    """
    batch = _open(batch)
    if quantity is None or quantity <= 0:
        raise BatchError("How much was made? Enter a quantity greater than zero.")
    try:
        made = in_base_units(batch.item, quantity, measure)
    except ValueError as e:
        raise BatchError(str(e)) from None

    now = timezone.now()
    batch.actual_yield = made
    batch.yield_entered_quantity = quantity
    batch.yield_entered_measure = measure
    batch.finished_at = now
    batch.status = BatchStatus.AVAILABLE
    if note:
        batch.note = note[:2000]
    batch.save()

    post_movement(
        item=batch.item,
        location=batch.location,
        quantity=made,
        movement_type=MovementType.PRODUCTION_YIELD,
        occurred_at=now,
        batch=batch,
        source=batch,
        user=by,
        note=f"Made {quantity.normalize():f} {measure.name if measure else batch.item.base_unit.code}",
    )
    for line in batch.inputs.select_related("item"):
        post_movement(
            item=line.item,
            location=batch.location,
            quantity=-line.quantity,
            movement_type=MovementType.PRODUCTION_CONSUME,
            occurred_at=now,
            source=batch,
            user=by,
            note=f"Into {batch.batch_code}",
        )
    return batch


@transaction.atomic
def discard_batch(batch: ProductionBatch) -> None:
    """A batch started by mistake and never recorded. Nothing was written to stock, so nothing is lost."""
    batch = _open(batch)
    batch.inputs.all().delete()
    batch.delete()
