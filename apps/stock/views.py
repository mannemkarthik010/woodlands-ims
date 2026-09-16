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

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.catalog.models import Item
from apps.core.models import Location
from apps.stock.models import DocumentStatus, StockCount, Transfer, TransferLine
from apps.stock.services import (
    StockError,
    build_count_sheet,
    on_hand,
    post_count,
    post_transfer,
)


@login_required
def home(request):
    open_transfers = (
        Transfer.objects.filter(status=DocumentStatus.DRAFT)
        .select_related("from_location", "to_location")
        .order_by("-occurred_at")[:5]
    )
    return render(request, "stock/home.html", {"open_transfers": open_transfers})


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


@login_required
def item_search(request):
    """
    Live search as they type. Searches code, name and every alias, because
    the same thing is called three different names by three different people.
    """
    q = (request.GET.get("q") or "").strip()
    transfer_pk = request.GET.get("transfer")

    items = Item.objects.none()
    if len(q) >= 2:
        items = (
            Item.objects.filter(is_active=True, is_stocked=True)
            .filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(aliases__alias__icontains=q))
            .select_related("base_unit")
            .distinct()[:12]
        )

    return render(
        request,
        "stock/_item_results.html",
        {"items": items, "q": q, "transfer_pk": transfer_pk},
    )


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


@login_required
def count_new(request):
    """Pick a location and cadence, then build the sheet."""
    if request.method == "POST":
        location = get_object_or_404(Location, pk=request.POST.get("location"))
        cadence = request.POST.get("cadence") or StockCount.Cadence.WEEKLY
        count = StockCount.objects.create(
            location=location,
            cadence=cadence,
            counted_at=timezone.now(),
            created_by=request.user,
        )
        build_count_sheet(count)
        return redirect("count_sheet", pk=count.pk)

    return render(
        request,
        "stock/count_new.html",
        {
            "locations": Location.objects.filter(is_active=True),
            "cadences": StockCount.Cadence.choices,
        },
    )


@login_required
def count_sheet(request, pk):
    count = get_object_or_404(StockCount.objects.select_related("location"), pk=pk)
    if count.status != DocumentStatus.DRAFT:
        return redirect("count_review", pk=count.pk)

    lines = count.lines.select_related("item", "item__base_unit", "item__category")
    done = lines.exclude(counted_quantity__isnull=True).count()
    return render(
        request,
        "stock/count_sheet.html",
        {"count": count, "lines": lines, "done": done, "total": lines.count()},
    )


@login_required
@require_POST
def count_save_line(request, pk, line_pk):
    """Save one line as it is typed. A count is done standing up, over time."""
    count = get_object_or_404(StockCount, pk=pk, status=DocumentStatus.DRAFT)
    line = get_object_or_404(count.lines.select_related("item", "item__base_unit"), pk=line_pk)

    raw = (request.POST.get("counted") or "").strip()
    if raw == "":
        line.counted_quantity = None
    else:
        from decimal import Decimal, InvalidOperation

        try:
            line.counted_quantity = Decimal(raw)
        except (InvalidOperation, ValueError):
            return render(request, "stock/_count_line.html", {"line": line, "bad": True})
    line.save(update_fields=["counted_quantity"])

    remaining = count.lines.filter(counted_quantity__isnull=True).count()
    return render(
        request,
        "stock/_count_line.html",
        {"line": line, "saved": True, "remaining": remaining},
    )


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


@login_required
@require_POST
def count_post(request, pk):
    count = get_object_or_404(StockCount, pk=pk)
    try:
        post_count(count, user=request.user)
    except StockError:
        return redirect("count_review", pk=count.pk)
    return redirect("count_review", pk=count.pk)
