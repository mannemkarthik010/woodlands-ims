"""
"Made today": what the kitchen made, in its own containers, and what went in.

Built for the tablet at the stove: tap what you made, say how much came out
("2 buckets"), add what went in if you know it, save. The stock follows.
"""

from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.catalog.models import Item, ItemKind, ItemMeasure
from apps.core.models import Location
from apps.production.models import BatchStatus, ProductionBatch
from apps.production.services import (
    BatchError,
    add_input,
    discard_batch,
    finish_batch,
    remove_input,
    start_batch,
)


def _measures(item: Item):
    return list(ItemMeasure.objects.filter(item=item, is_active=True).order_by("-quantity_in_base_units"))


def _measure_for(item: Item, raw: str):
    return ItemMeasure.objects.filter(pk=raw, item=item, is_active=True).first() if raw.isdigit() else None


def _quantity(raw: str) -> Decimal | None:
    try:
        return Decimal((raw or "").strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


# Implements: FR-501.
@login_required
def made_today(request):
    """What the kitchen makes, as big buttons; what has been made today; anything left half-recorded."""
    today = timezone.localdate()
    start = timezone.make_aware(datetime.combine(today, time.min))
    batches = ProductionBatch.objects.select_related("item", "yield_entered_measure", "produced_by")
    return render(
        request,
        "production/made_today.html",
        {
            "items": Item.objects.filter(is_active=True, kind=ItemKind.PREPARED).order_by("name"),
            "open": batches.filter(status=BatchStatus.IN_PROGRESS).order_by("-started_at"),
            "made": batches.exclude(status=BatchStatus.IN_PROGRESS)
            .filter(finished_at__gte=start, finished_at__lt=start + timedelta(days=1))
            .order_by("-finished_at"),
        },
    )


@login_required
@require_POST
def batch_start(request):
    item = get_object_or_404(Item, pk=request.POST.get("item"), is_active=True)
    location = Location.objects.filter(kind=Location.Kind.RESTAURANT, is_active=True).first()
    if location is None:
        return render(request, "stock/no_locations.html", status=400)
    try:
        batch = start_batch(item, location=location, by=request.user)
    except BatchError as e:
        messages.error(request, str(e))
        return redirect("made_today")
    return redirect("batch_edit", pk=batch.pk)


def _batch_context(batch, **extra):
    return {
        "batch": batch,
        "inputs": batch.inputs.select_related("item", "item__base_unit", "entered_measure"),
        "measures": _measures(batch.item),
        **extra,
    }


# Implements: FR-502, FR-503.
@login_required
def batch_edit(request, pk):
    batch = get_object_or_404(ProductionBatch.objects.select_related("item", "item__base_unit"), pk=pk)
    if batch.status != BatchStatus.IN_PROGRESS:
        return redirect("made_today")
    return render(request, "production/batch_edit.html", _batch_context(batch))


@login_required
@require_POST
def batch_add_input(request, pk):
    batch = get_object_or_404(ProductionBatch, pk=pk, status=BatchStatus.IN_PROGRESS)
    item = get_object_or_404(Item, pk=request.POST.get("item"), is_active=True)
    error = None
    try:
        add_input(
            batch,
            item=item,
            quantity=_quantity(request.POST.get("quantity", "")),
            measure=_measure_for(item, request.POST.get("measure", "")),
        )
    except BatchError as e:
        error = str(e)
    return render(request, "production/_inputs.html", _batch_context(batch, error=error))


@login_required
@require_POST
def batch_remove_input(request, pk, input_pk):
    batch = get_object_or_404(ProductionBatch, pk=pk, status=BatchStatus.IN_PROGRESS)
    remove_input(batch, input_pk)
    return render(request, "production/_inputs.html", _batch_context(batch))


# Implements: FR-503, FR-504.
@login_required
@require_POST
def batch_finish(request, pk):
    batch = get_object_or_404(ProductionBatch.objects.select_related("item"), pk=pk)
    try:
        finish_batch(
            batch,
            quantity=_quantity(request.POST.get("made", "")),
            measure=_measure_for(batch.item, request.POST.get("measure", "")),
            by=request.user,
            note=request.POST.get("note", ""),
        )
    except BatchError as e:
        return render(request, "production/batch_edit.html", _batch_context(batch, error=str(e)))
    messages.success(request, f"Recorded: {batch.item} — {batch.batch_code}.")
    return redirect("made_today")


@login_required
@require_POST
def batch_discard(request, pk):
    batch = get_object_or_404(ProductionBatch, pk=pk)
    try:
        discard_batch(batch)
    except BatchError as e:
        messages.error(request, str(e))
    return redirect("made_today")
