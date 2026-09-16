"""
The screen everything starts from.

It lives in `core` rather than in `stock` deliberately. Every other view
belongs to one app and imports only downwards -- stock knows nothing about
sales, sales knows nothing about labour. The home screen is the exception by
definition: its whole job is to show what is outstanding across all of them.

Putting it here keeps that one crossing in a place where it is expected,
instead of quietly turning `stock` into the app that imports everything.
"""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.sales.models import PosItem
from apps.stock.models import DocumentStatus, Transfer


@login_required
def home(request):
    open_transfers = (
        Transfer.objects.filter(status=DocumentStatus.DRAFT)
        .select_related("from_location", "to_location")
        .order_by("-occurred_at")[:5]
    )
    # The mapping queue earns a place on this screen only while there is
    # something in it. A tile that always reads "0 left" is furniture.
    mapping_remaining = PosItem.objects.filter(item__isnull=True, ignore=False).count()

    return render(
        request,
        "stock/home.html",
        {"open_transfers": open_transfers, "mapping_remaining": mapping_remaining},
    )
