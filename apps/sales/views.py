"""
The POS mapping queue.

Every name on the till has to point at something before a day's sales can
deplete a single gram of stock, and there are 318 of them. Until this screen
exists the whole sales half of the system is blocked behind an afternoon of
data entry nobody has agreed to do.

This is not a kitchen screen. It is done sitting down, by an owner, on a
laptop, probably in two or three sittings -- so it is denser than the transfer
and count screens, and it keeps its place.

The design is about how few decisions there are, not how fast each one is:

  * 311 unmapped lines collapse into 252 groups. Confirming "masala dosa"
    once settles five POS lines.
  * Most of the menu does not exist in the catalogue yet, so the common case
    -- create the dish and map the group to it -- is one button, not a trip
    to the admin and back.
  * Tub sizes are read off the name and pre-filled, then shown for confirming.
    "Coconut Chutney 16 oz" depletes sixteen ounces; the 4 oz line next to it
    depletes four. Left to a default of 1 they would all deplete the same
    amount and the chutney figures would be quietly meaningless.
  * Near-identical names are surfaced as a question -- "Chana Masala" and
    "Channa Masala" are the same food spelled two ways -- because the failure
    this screen exists to prevent is one food becoming two items.

Nothing here maps anything by itself. See docs/architecture.md §3.3.
"""

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.catalog import services as catalog
from apps.catalog.models import Item, ItemKind, Unit
from apps.sales import services
from apps.sales.models import PosItem


def _card(request, key: str, *, message: str = "", error: str = ""):
    """Re-render one group card in place, after a decision or a mistake."""
    group = services.Group(key=key, lines=services.group_lines(key))
    if not error:
        group.suggestions = services.suggestions(key)
    return render(
        request,
        "sales/_group.html",
        {
            "group": group,
            "message": message,
            "error": error,
            "progress": services.progress(),
            "oob": True,  # this is an htmx response; update the counter at the top
        },
    )


# Implements: FR-610, NFR-05.
@login_required
def mapping_queue(request):
    """
    The queue itself. Biggest groups first, so the first few decisions clear
    the most lines and the count visibly moves.
    """
    done = request.GET.get("show") == "done"
    category = request.GET.get("category", "")
    query = request.GET.get("q", "").strip()

    found = services.groups(done=done, category=category, query=query)
    shown_groups = found[:60]
    for group in shown_groups:
        group.suggestions = services.suggestions(group.key)
    categories = (
        PosItem.objects.exclude(pos_category="")
        .values_list("pos_category", flat=True)
        .distinct()
        .order_by("pos_category")
    )

    return render(
        request,
        "sales/mapping_queue.html",
        {
            "groups": shown_groups,
            "shown": len(shown_groups),
            "total_groups": len(found),
            "categories": categories,
            "category": category,
            "query": query,
            "done": done,
            "progress": services.progress(),
        },
    )


# Implements: FR-202, FR-610.
@login_required
def mapping_search(request):
    """
    Search the catalogue for the thing a group should point at.

    Dishes and prepared components only. A POS line never points at a sack of
    flour -- if it looks like it should, the dish is missing and wants
    creating instead.
    """
    query = request.GET.get("q", "").strip()
    key = request.GET.get("key", "")
    items = []
    if len(query) >= 2:
        items = list(
            Item.objects.filter(
                Q(name__icontains=query) | Q(aliases__alias__icontains=query),
                kind__in=[ItemKind.DISH, ItemKind.PREPARED],
                is_active=True,
            )
            .select_related("base_unit")
            .distinct()[:8]
        )
    return render(request, "sales/_search_results.html", {"items": items, "key": key})


# Implements: FR-610.
@require_POST
@login_required
def mapping_apply(request, key):
    """
    Map a group to an item, creating the dish first if that is what was asked
    for. Quantities come from the form where somebody has corrected one.
    """
    # Sizes arrive in ounces, as read off the label. Blank means the name says
    # nothing about size, which is not the same as zero.
    quantities = {}
    for field, raw in request.POST.items():
        if not field.startswith("qty-") or not raw.strip():
            continue
        try:
            value = Decimal(raw.strip())
        except InvalidOperation:
            return _card(request, key, error=f"'{raw}' is not a quantity.")
        if value <= 0:
            return _card(request, key, error="A size has to be more than zero.")
        quantities[int(field[4:])] = value

    try:
        chosen = request.POST.get("item_id")
        if chosen:
            # An existing item always wins. The create button carries its own
            # flag rather than the form carrying a hidden one, so that picking
            # a suggestion cannot also create a duplicate of it.
            item = get_object_or_404(Item, pk=chosen)
            note = f"Mapped to {item.name}"
        elif request.POST.get("create"):
            name = request.POST.get("new_name", "").strip() or key.title()
            item = services.create_dish(name)
            note = f"Created {item.name} and mapped"
        else:
            return _card(request, key, error="Choose an item, or create the dish.")

        count = services.map_group(key, item=item, user=request.user, quantities=quantities)
    except services.MappingError as problem:
        return _card(request, key, error=str(problem))

    return _card(request, key, message=f"{note} · {count} POS line{'s' if count != 1 else ''}")


# Implements: FR-610.
@require_POST
@login_required
def mapping_ignore(request, key):
    try:
        count = services.ignore_group(key, user=request.user)
    except services.MappingError as problem:
        return _card(request, key, error=str(problem))
    return _card(request, key, message=f"Ignored · {count} line{'s' if count != 1 else ''}")


# Implements: FR-610.
@require_POST
@login_required
def mapping_reopen(request, key):
    services.reopen_group(key)
    return _card(request, key, message="Back in the queue")


# Implements: FR-202, FR-209, FR-211.
@login_required
def mapping_review(request):
    """
    The second look.

    Two hundred and fifty decisions in one sitting produces a predictable set
    of mistakes, none of which are carelessness -- a tub counted in "each", one
    food spelled two ways, an item left behind when a decision was changed.
    This finds them and offers the fix. It corrects nothing on its own.
    """
    units = Unit.objects.filter(code__in=["floz", "oz", "g", "lb", "ml", "l"]).order_by("code")
    return render(
        request,
        "sales/mapping_review.html",
        {"flags": services.review(), "units": units, "progress": services.progress()},
    )


# Implements: FR-202, FR-211.
@require_POST
@login_required
def item_merge(request):
    """Fold one item into another, keeping the old name as a searchable alias."""
    source = get_object_or_404(Item, pk=request.POST.get("source"))
    target = get_object_or_404(Item, pk=request.POST.get("target"))
    try:
        catalog.merge_items(source, target, user=request.user)
    except catalog.CatalogError as problem:
        messages.error(request, str(problem))
    else:
        messages.success(request, f"{source.name} folded into {target.name}, and kept as a name for it.")
    return redirect("mapping_review")


# Implements: FR-203, FR-209, FR-501.
@require_POST
@login_required
def item_convert(request):
    """Turn a dish sold by the ounce into the prepared component it actually is."""
    item = get_object_or_404(Item, pk=request.POST.get("item"))
    unit = get_object_or_404(Unit, pk=request.POST.get("unit"))
    try:
        changed = catalog.convert_to_component(item, unit, user=request.user)
    except (catalog.CatalogError, ValidationError) as problem:
        messages.error(request, str(problem))
    else:
        messages.success(
            request,
            f"{item.name} is now a prepared component measured in {unit.code}; "
            f"{changed} till line(s) now deplete their own size.",
        )
    return redirect("mapping_review")


# Implements: FR-211.
@require_POST
@login_required
def item_retire(request):
    item = get_object_or_404(Item, pk=request.POST.get("item"))
    catalog.retire_item(item, reason="Retired from the mapping review.")
    messages.success(request, f"{item.name} retired. Nothing is deleted; it stops appearing.")
    return redirect("mapping_review")
