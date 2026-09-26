"""
Screens for stock movement.

The transfer flow is the one that matters most. Goods move from the
Devonshire Street unit to the restaurant several times a week and none of it
is recorded today, which is where most of what looks like shrinkage in a
two-site food business actually goes.

The design constraint is not features, it is seconds. This has to be
completable at the unit door, one-handed, on a phone, before loading the car.
If it takes longer than about thirty seconds it will not be done, and a
transfer log that is only sometimes filled in is worse than none at all --
it produces false confidence instead of known ignorance.

Everything below follows from that:

  * The common direction (storage -> restaurant) is the default, not a choice.
  * A draft transfer is created immediately, so nothing is lost if the phone
    locks or the signal drops halfway through.
  * Items are added one at a time and appear instantly, no page reload.
  * Quantity opens a numeric keypad.
  * Nothing touches stock until Post is pressed, and Post goes through
    services.post_transfer like everything else.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.catalog.models import CountEvery, Item, ItemKind, ItemMeasure, Unit
from apps.catalog.services import ADDABLE, ItemError, add_item, bring_back, move_to_list, retire_item
from apps.core.models import Location, Supplier
from apps.core.permissions import is_owner, owner_required
from apps.stock.models import DocumentStatus, GoodsReceipt, StockCount, StockCountLine, Transfer, TransferLine
from apps.stock.services import (
    RECEIVABLE_KINDS,
    StockError,
    add_receipt_line,
    on_hand,
    post_count,
    post_receipt,
    post_transfer,
    record_counted,
    start_count,
)


# Implements: FR-403, FR-405, NFR-02, NFR-06.
@login_required
def transfer_new(request):
    """
    Start a draft and go straight to it. No form to fill in first -- the
    defaults are right nearly every time, and they can be changed on the
    next screen in the rare case they are not.
    """
    storage = Location.objects.filter(kind=Location.Kind.STORAGE, is_active=True).first()
    restaurant = Location.objects.filter(kind=Location.Kind.RESTAURANT, is_active=True).first()
    if not (storage and restaurant):
        return render(request, "stock/no_locations.html", status=400)

    transfer = Transfer.objects.create(
        from_location=storage,
        to_location=restaurant,
        occurred_at=timezone.now(),
        created_by=request.user,
    )
    return redirect("transfer_edit", pk=transfer.pk)


# Implements: FR-403, FR-405.
@login_required
def transfer_edit(request, pk):
    transfer = get_object_or_404(Transfer.objects.select_related("from_location", "to_location"), pk=pk)
    if transfer.status != DocumentStatus.DRAFT:
        return redirect("transfer_done", pk=transfer.pk)

    if request.method == "POST":
        # Direction swap, for the occasional run the other way.
        if request.POST.get("swap"):
            transfer.from_location, transfer.to_location = (
                transfer.to_location,
                transfer.from_location,
            )
            transfer.save(update_fields=["from_location", "to_location"])
        return redirect("transfer_edit", pk=transfer.pk)

    return render(request, "stock/transfer_edit.html", _transfer_context(transfer))


def _transfer_context(transfer):
    lines = transfer.lines.select_related("item", "item__base_unit")
    return {
        "transfer": transfer,
        "lines": lines,
        "line_count": lines.count(),
    }


# Implements: FR-202, NFR-05.
@login_required
def item_search(request):
    """
    Live search as they type. Searches code, name and every alias, because
    the same thing is called three different names by three different people.
    """
    q = (request.GET.get("q") or "").strip()
    # What the search is for decides what can be found and where "Add" goes:
    # a delivery only brings layer 1, a batch takes groceries and bases.
    purpose = request.GET.get("for") or "transfer"
    doc = request.GET.get("id") or request.GET.get("transfer") or ""
    if not doc.isdigit() or purpose not in SEARCHES:
        return HttpResponseBadRequest("Unknown search.")
    kinds, add_name, with_measures = SEARCHES[purpose]

    items = Item.objects.none()
    if len(q) >= 2:
        items = (
            Item.objects.filter(is_active=True, is_stocked=True)
            .filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(aliases__alias__icontains=q))
            .select_related("base_unit")
            .distinct()
        )
        if kinds:
            items = items.filter(kind__in=kinds)
        items = list(items[:12])
        if with_measures:
            by_item: dict[int, list] = {}
            for m in ItemMeasure.objects.filter(item__in=items, is_active=True).order_by(
                "-quantity_in_base_units"
            ):
                by_item.setdefault(m.item_id, []).append(m)
            for item in items:
                item.measure_choices = by_item.get(item.pk, [])

    return render(
        request,
        "stock/_item_results.html",
        {"items": items, "q": q, "add_url": reverse(add_name, args=[doc]), "with_measures": with_measures},
    )


# purpose: (kinds that can be found, where Add posts, offer measures)
SEARCHES = {
    "transfer": ((), "transfer_add_line", False),
    "receipt": (RECEIVABLE_KINDS, "receipt_add_line", True),
    "batch": (("RAW", "PREPARED", "CONSUMABLE"), "batch_add_input", True),
}


# Implements: FR-403, FR-405, NFR-06.
@login_required
@require_POST
def transfer_add_line(request, pk):
    transfer = get_object_or_404(Transfer, pk=pk, status=DocumentStatus.DRAFT)
    item = get_object_or_404(Item, pk=request.POST.get("item"), is_stocked=True)

    raw = (request.POST.get("quantity") or "").strip()
    try:
        from decimal import Decimal, InvalidOperation

        quantity = Decimal(raw)
    except (InvalidOperation, ValueError):
        quantity = None

    if quantity is None or quantity <= 0:
        return render(
            request,
            "stock/_lines.html",
            _transfer_context(transfer) | {"error": "Enter a quantity greater than zero."},
        )

    # Same item twice in one run just adds to the existing line, rather than
    # leaving two rows that have to be mentally summed.
    line = transfer.lines.filter(item=item, batch__isnull=True).first()
    if line:
        line.quantity += quantity
        line.save(update_fields=["quantity"])
    else:
        TransferLine.objects.create(transfer=transfer, item=item, quantity=quantity)

    return render(request, "stock/_lines.html", _transfer_context(transfer))


@login_required
@require_POST
def transfer_remove_line(request, pk, line_pk):
    transfer = get_object_or_404(Transfer, pk=pk, status=DocumentStatus.DRAFT)
    transfer.lines.filter(pk=line_pk).delete()
    return render(request, "stock/_lines.html", _transfer_context(transfer))


# Implements: FR-404, FR-409.
@login_required
@require_POST
def transfer_post(request, pk):
    transfer = get_object_or_404(Transfer, pk=pk)
    if not transfer.lines.exists():
        return render(
            request,
            "stock/transfer_edit.html",
            _transfer_context(transfer) | {"error": "Add at least one item first."},
        )
    try:
        post_transfer(transfer, user=request.user)
    except StockError as exc:
        return render(
            request,
            "stock/transfer_edit.html",
            _transfer_context(transfer) | {"error": str(exc)},
        )
    return redirect("transfer_done", pk=transfer.pk)


@login_required
def transfer_done(request, pk):
    transfer = get_object_or_404(Transfer.objects.select_related("from_location", "to_location"), pk=pk)
    lines = transfer.lines.select_related("item", "item__base_unit")
    rows = [
        {
            "line": line,
            "now_at_destination": on_hand(line.item, transfer.to_location),
        }
        for line in lines
    ]
    return render(request, "stock/transfer_done.html", {"transfer": transfer, "rows": rows})


# ---------------------------------------------------------------------------
# Stock counts
# ---------------------------------------------------------------------------
#
# One screen doing four jobs: the opening balance when the system is first
# filled, the short daily check on the things that move fast, the weekly
# restaurant count, and the monthly count that includes the storage unit.
#
# The important design decision here is that THE EXPECTED QUANTITY IS NOT
# SHOWN WHILE COUNTING. If a sheet says "we think there are 18", a tired
# person at the end of a shift writes 18. That is not counting, it is
# confirming, and it produces numbers that agree with themselves and with
# nothing on the shelf. The variance is shown afterwards, on the review
# screen, where it belongs.


@dataclass
class CountCard:
    """One of the counts the kitchen does, and where it stands today."""

    cadence: str
    label: str
    what: str
    location: Location
    items: int
    last: StockCount | None
    draft: StockCount | None
    due: bool


def _due(cadence: str, last: StockCount | None, today) -> bool:
    if last is None:
        return True
    done = timezone.localdate(last.counted_at)
    if cadence == StockCount.Cadence.DAILY:
        return done < today
    if cadence == StockCount.Cadence.WEEKLY:
        return (today - done).days >= 7
    return (done.year, done.month) != (today.year, today.month)


# Implements: FR-701, FR-703.
@login_required
def count_home(request):
    """
    The three counts, each with only its own items, and whether it is due:
    daily -- what the kitchen makes; weekly -- vegetables; monthly --
    groceries and packaging, at the restaurant and at the storage unit.
    """
    today = timezone.localdate()
    restaurant = Location.objects.filter(kind=Location.Kind.RESTAURANT, is_active=True).first()
    storage = Location.objects.filter(kind=Location.Kind.STORAGE, is_active=True).first()
    if restaurant is None:
        return render(request, "stock/no_locations.html", status=400)
    counted = Item.objects.filter(is_active=True, is_stocked=True)
    plan = [
        (
            StockCount.Cadence.DAILY,
            CountEvery.DAILY,
            "Daily",
            "Batters, sambar, chutneys — what the kitchen makes",
            restaurant,
        ),
        (StockCount.Cadence.WEEKLY, CountEvery.WEEKLY, "Weekly", "Vegetables", restaurant),
        (StockCount.Cadence.MONTHLY, CountEvery.MONTHLY, "Monthly", "Groceries and packaging", restaurant),
    ]
    if storage:
        plan.append(
            (StockCount.Cadence.MONTHLY, CountEvery.MONTHLY, "Monthly", "Groceries and packaging", storage)
        )
    cards = []
    for cadence, every, label, what, location in plan:
        of_this = StockCount.objects.filter(cadence=cadence, location=location).select_related("created_by")
        last = of_this.filter(status=DocumentStatus.POSTED).order_by("-counted_at").first()
        # Only a sheet from this day, week or month is carried on. An older
        # one was made from an older list and older stock; starting afresh
        # retires it (services.start_count).
        draft = of_this.filter(status=DocumentStatus.DRAFT).order_by("-counted_at").first()
        if draft and _due(cadence, draft, today):
            draft = None
        cards.append(
            CountCard(
                cadence=cadence,
                label=label,
                what=what,
                location=location,
                items=counted.filter(count_every=every).count(),
                last=last,
                draft=draft,
                due=_due(cadence, last, today),
            )
        )
    return render(request, "stock/count_home.html", {"cards": cards, "can_manage": is_owner(request.user)})


# Implements: FR-701.
@login_required
def count_new(request):
    """Pick a location and cadence, then build the sheet."""
    if request.method == "POST":
        location = get_object_or_404(Location, pk=request.POST.get("location"))
        cadence = request.POST.get("cadence") or StockCount.Cadence.WEEKLY
        if cadence not in StockCount.Cadence.values:
            cadence = StockCount.Cadence.ADHOC
        count = start_count(location=location, cadence=cadence, user=request.user)
        return redirect("count_sheet", pk=count.pk)

    return render(
        request,
        "stock/count_new.html",
        {
            "locations": Location.objects.filter(is_active=True),
            "cadences": StockCount.Cadence.choices,
        },
    )


# Implements: FR-702, FR-703, NFR-06.
@login_required
def count_sheet(request, pk):
    count = get_object_or_404(StockCount.objects.select_related("location"), pk=pk)
    if count.status != DocumentStatus.DRAFT:
        return redirect("count_review", pk=count.pk)

    lines = list(count.lines.select_related("item", "item__base_unit", "item__category", "entered_measure"))
    _attach_measures(lines)
    done = sum(1 for line in lines if line.counted_quantity is not None)
    return render(
        request,
        "stock/count_sheet.html",
        {"count": count, "lines": lines, "done": done, "total": len(lines)},
    )


def _attach_measures(lines) -> None:
    """
    Each line gets the measures its item is counted in, and which one to
    offer first: what this line was counted in, else what the item was
    counted in last time, else the base unit. Only the measure is carried
    over, never the number -- the count stays blind.
    """
    item_ids = [line.item_id for line in lines]
    measures: dict[int, list] = {}
    for m in ItemMeasure.objects.filter(item_id__in=item_ids, is_active=True).order_by(
        "-quantity_in_base_units"
    ):
        measures.setdefault(m.item_id, []).append(m)
    previous = dict(
        StockCountLine.objects.filter(
            item_id__in=item_ids, entered_measure__isnull=False, count__status=DocumentStatus.POSTED
        )
        .order_by("item_id", "-count__counted_at")
        .values_list("item_id", "entered_measure_id")
    )
    for line in lines:
        line.measures = measures.get(line.item_id, [])
        line.default_measure_id = line.entered_measure_id or previous.get(line.item_id)


# Implements: FR-702, NFR-04.
@login_required
@require_POST
def count_save_line(request, pk, line_pk):
    """Save one line as it is typed. A count is done standing up, over time."""
    count = get_object_or_404(StockCount, pk=pk, status=DocumentStatus.DRAFT)
    line = get_object_or_404(count.lines.select_related("item", "item__base_unit"), pk=line_pk)

    raw = (request.POST.get("counted") or "").strip().replace(",", ".")
    measure_id = request.POST.get("measure") or ""
    measure = (
        ItemMeasure.objects.filter(pk=measure_id, item_id=line.item_id, is_active=True).first()
        if measure_id.isdigit()
        else None
    )
    try:
        quantity = Decimal(raw) if raw else None
        record_counted(line, quantity=quantity, measure=measure)
    except (InvalidOperation, ValueError, StockError):
        _attach_measures([line])
        return render(request, "stock/_count_line.html", {"line": line, "bad": True})

    _attach_measures([line])
    remaining = count.lines.filter(counted_quantity__isnull=True).count()
    return render(
        request,
        "stock/_count_line.html",
        {"line": line, "saved": True, "remaining": remaining},
    )


# Implements: FR-704, FR-705.
@login_required
def count_review(request, pk):
    """Variance, shown only once counting is finished."""
    count = get_object_or_404(StockCount.objects.select_related("location"), pk=pk)
    lines = count.lines.select_related("item", "item__base_unit").exclude(counted_quantity__isnull=True)
    rows = sorted(
        ({"line": line, "variance": line.variance} for line in lines),
        key=lambda r: abs(r["variance"] or 0),
        reverse=True,
    )
    return render(
        request,
        "stock/count_review.html",
        {
            "count": count,
            "rows": rows,
            "counted": len(rows),
            "uncounted": count.lines.filter(counted_quantity__isnull=True).count(),
            "posted": count.status == DocumentStatus.POSTED,
        },
    )


# Implements: FR-706.
@login_required
@require_POST
def count_post(request, pk):
    count = get_object_or_404(StockCount, pk=pk)
    try:
        post_count(count, user=request.user)
    except StockError:
        return redirect("count_review", pk=count.pk)
    return redirect("count_review", pk=count.pk)


# --- Deliveries (layer 1 in) -------------------------------------------------
#
# What physically arrived, counted off the truck in the packs it came in.
# Same shape as the storage run: start, add lines as they are unpacked, record.


def _measure_for(item: Item, raw: str):
    return ItemMeasure.objects.filter(pk=raw, item=item, is_active=True).first() if raw.isdigit() else None


def _receipt_context(receipt):
    lines = receipt.lines.select_related("item", "item__base_unit", "purchase_unit")
    return {
        "receipt": receipt,
        "lines": lines,
        "line_count": lines.count(),
        "suppliers": Supplier.objects.filter(is_active=True),
        "locations": Location.objects.filter(
            is_active=True, kind__in=[Location.Kind.RESTAURANT, Location.Kind.STORAGE]
        ),
    }


# Implements: FR-302.
@login_required
def receipt_new(request):
    restaurant = Location.objects.filter(kind=Location.Kind.RESTAURANT, is_active=True).first()
    if restaurant is None:
        return render(request, "stock/no_locations.html", status=400)
    receipt = GoodsReceipt.objects.create(
        location=restaurant, received_at=timezone.now(), created_by=request.user
    )
    return redirect("receipt_edit", pk=receipt.pk)


# Implements: FR-302, FR-306.
@login_required
def receipt_edit(request, pk):
    receipt = get_object_or_404(GoodsReceipt.objects.select_related("location", "supplier"), pk=pk)
    if receipt.status != DocumentStatus.DRAFT:
        return redirect("receipt_done", pk=receipt.pk)
    if request.method == "POST":
        location = Location.objects.filter(pk=request.POST.get("location") or 0, is_active=True).first()
        supplier = Supplier.objects.filter(pk=request.POST.get("supplier") or 0).first()
        receipt.location = location or receipt.location
        receipt.supplier = supplier
        receipt.supplier_reference = (request.POST.get("reference") or "")[:80]
        receipt.save(update_fields=["location", "supplier", "supplier_reference"])
        return redirect("receipt_edit", pk=receipt.pk)
    return render(request, "stock/receipt_edit.html", _receipt_context(receipt))


@login_required
@require_POST
def receipt_add_line(request, pk):
    receipt = get_object_or_404(GoodsReceipt, pk=pk, status=DocumentStatus.DRAFT)
    item = get_object_or_404(Item, pk=request.POST.get("item"), is_active=True)
    try:
        quantity = Decimal((request.POST.get("quantity") or "").strip().replace(",", "."))
        add_receipt_line(
            receipt, item=item, quantity=quantity, measure=_measure_for(item, request.POST.get("measure", ""))
        )
    except (InvalidOperation, ValueError):
        return render(
            request,
            "stock/_receipt_lines.html",
            _receipt_context(receipt) | {"error": "Enter a quantity greater than zero."},
        )
    except StockError as e:
        return render(request, "stock/_receipt_lines.html", _receipt_context(receipt) | {"error": str(e)})
    return render(request, "stock/_receipt_lines.html", _receipt_context(receipt))


@login_required
@require_POST
def receipt_remove_line(request, pk, line_pk):
    receipt = get_object_or_404(GoodsReceipt, pk=pk, status=DocumentStatus.DRAFT)
    receipt.lines.filter(pk=line_pk).delete()
    return render(request, "stock/_receipt_lines.html", _receipt_context(receipt))


# Implements: FR-302, FR-303.
@login_required
@require_POST
def receipt_post(request, pk):
    receipt = get_object_or_404(GoodsReceipt, pk=pk)
    try:
        post_receipt(receipt, user=request.user)
    except StockError as e:
        return render(request, "stock/receipt_edit.html", _receipt_context(receipt) | {"error": str(e)})
    return redirect("receipt_done", pk=receipt.pk)


@login_required
def receipt_done(request, pk):
    receipt = get_object_or_404(GoodsReceipt.objects.select_related("location", "supplier"), pk=pk)
    rows = [
        {"line": line, "now_here": on_hand(line.item, receipt.location)}
        for line in receipt.lines.select_related("item", "item__base_unit", "purchase_unit")
    ]
    return render(request, "stock/receipt_done.html", {"receipt": receipt, "rows": rows})


# --- Owners: what is on each count ------------------------------------------


def _lists_context():
    items = (
        Item.objects.filter(is_stocked=True)
        .exclude(kind=ItemKind.DISH)
        .select_related("base_unit")
        .prefetch_related("measures")
        .order_by("kind", "name")
    )
    active = [i for i in items if i.is_active]
    sections = []
    for value, label, hint in (
        (CountEvery.DAILY, "Daily", "Counted every day"),
        (CountEvery.WEEKLY, "Weekly", "Counted once a week"),
        (CountEvery.MONTHLY, "Monthly", "Counted once a month, at the restaurant and the storage unit"),
        (CountEvery.NEVER, "Not counted", "Kept on the system, but on no count"),
    ):
        on = sorted((i for i in active if i.count_every == value), key=lambda i: (i.layer, i.name.casefold()))
        sections.append({"value": value, "label": label, "hint": hint, "items": on})
    stopped = [i for i in items if not i.is_active and not i.code.startswith("DEMO-")]
    return {"sections": sections, "stopped": stopped, "choices": CountEvery.choices}


# Implements: FR-701.
@owner_required
def count_lists(request):
    """Which items are on the daily, weekly and monthly counts -- the owners decide, here."""
    return render(request, "stock/count_lists.html", _lists_context())


@owner_required
@require_POST
def count_list_move(request, pk):
    item = get_object_or_404(Item, pk=pk, is_active=True)
    context = {}
    try:
        move_to_list(item, request.POST.get("count_every", ""))
        context["moved"] = item
    except ItemError as e:
        context["error"] = str(e)
    return render(request, "stock/_count_lists.html", _lists_context() | context)


@owner_required
@require_POST
def item_stop(request, pk):
    item = get_object_or_404(Item, pk=pk, is_active=True)
    retire_item(item, reason=f"Stopped from the count lists by {request.user}.")
    return render(request, "stock/_count_lists.html", _lists_context() | {"stopped_now": item})


@owner_required
@require_POST
def item_bring_back(request, pk):
    item = get_object_or_404(Item, pk=pk, is_active=False)
    bring_back(item)
    messages.success(request, f"{item} is back, on the {item.get_count_every_display().lower()} list.")
    return redirect("count_lists")


class ItemForm(forms.Form):
    name = forms.CharField(max_length=160)
    what = forms.ChoiceField(
        label="What is it?", choices=[(k, v[0]) for k, v in ADDABLE.items()], widget=forms.RadioSelect
    )
    base_unit = forms.ModelChoiceField(
        label="Kept track of in", queryset=Unit.objects.order_by("kind", "code"), to_field_name="code"
    )
    count_every = forms.ChoiceField(label="Which list?", choices=CountEvery.choices, widget=forms.RadioSelect)
    pack_name = forms.CharField(label="Comes in / kept in (optional)", max_length=60, required=False)
    pack_quantity = forms.DecimalField(label="One holds", required=False, min_value=Decimal("0.0001"))


# Implements: FR-201, FR-204.
@owner_required
def item_add(request):
    initial = {"base_unit": "lb", "what": request.GET.get("what", "RAW")}
    initial["count_every"] = {"PREPARED": "DAILY", "VEGETABLE": "WEEKLY"}.get(initial["what"], "MONTHLY")
    form = ItemForm(request.POST or None, initial=initial)
    context = {"form": form}
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            item = add_item(
                name=data["name"],
                what=data["what"],
                base_unit=data["base_unit"],
                count_every=data["count_every"],
                pack_name=data["pack_name"],
                pack_quantity=data["pack_quantity"],
                user=request.user,
            )
        except ItemError as e:
            context |= {"error": str(e), "existing": e.existing}
        else:
            messages.success(request, f"Added {item} to the {item.get_count_every_display().lower()} list.")
            if request.POST.get("again"):
                return redirect(f"{reverse('item_add')}?what={data['what']}")
            return redirect("count_lists")
    return render(request, "stock/item_add.html", context)
