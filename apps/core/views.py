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

from apps.core.permissions import is_owner
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

    context = {"open_transfers": open_transfers, "mapping_remaining": mapping_remaining}
    if is_owner(request.user):
        from apps.core import dashboard

        context["board"] = dashboard.build()
        return render(request, "core/dashboard.html", context)
    return render(request, "stock/home.html", context)


# Implements: NFR-02.
def healthz(request):
    """
    For the host's health check: the app is up AND can reach its database.
    Plain text, no login, no personal data. A 503 tells the host to restart
    or not send traffic yet.
    """
    from django.db import connection
    from django.http import HttpResponse

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:  # noqa: BLE001 -- any failure means "not healthy", which is the answer
        return HttpResponse("database unreachable", status=503, content_type="text/plain")
    return HttpResponse("ok", content_type="text/plain")


# --- The kitchen assistant ---------------------------------------------------

SESSION_KEY = "assistant_proposals"


@login_required
def assistant_page(request):
    """Tell it what happened; check what it prepared; confirm. Nothing is saved without a tap."""
    from django.contrib import messages
    from django.shortcuts import redirect

    from apps.core import ai, assistant

    pending = assistant.from_session(request.session.get(SESSION_KEY, []))
    context = {"available": ai.available()}
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "ask":
            said = request.POST.get("said", "")
            context["said"] = said
            try:
                reply = assistant.ask(said)
            except assistant.AssistantError as e:
                context["error"] = str(e)
            except Exception:  # network, quota: the screens still work
                context["error"] = "The assistant can't be reached right now. Please use the screens below."
            else:
                context["reply"] = reply.text
                pending = reply.proposals + pending
        elif action in ("confirm", "discard"):
            chosen = next((p for p in pending if p.id == request.POST.get("id")), None)
            if chosen is not None:
                if action == "confirm":
                    try:
                        messages.success(request, assistant.carry_out(chosen, user=request.user))
                    except assistant.AssistantError as e:
                        messages.error(request, str(e))
                        return redirect("assistant")
                pending = [p for p in pending if p.id != chosen.id]
            request.session[SESSION_KEY] = assistant.to_session(pending)
            return redirect("assistant")
        request.session[SESSION_KEY] = assistant.to_session(pending[:10])
    context["pending"] = pending
    return render(request, "core/assistant.html", context)
