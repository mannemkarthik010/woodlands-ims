"""
The shared tablet: a worker writes in their own hours.

The owners asked to start simply -- at the end of a shift, the worker records
the date, when they started and when they left. Tapping in and out live can
come later, on the same records (see `services.clock_in`).

The tablet itself stays signed in with one kitchen account. A worker proves
who they are with their name and PIN, and that lasts for one visit only: it
ends when they tap Done, or after IDLE_SECONDS untouched, so the next person
to pick up the tablet never lands on somebody else's hours.

Workers forget. So the first thing a worker sees is their last seven days,
with the empty ones marked and one tap away from being filled in.
"""

import time as clock
from datetime import date, timedelta

from django import forms
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.models import Location, Role, User
from apps.core.pins import LOCKOUT, check_pin, is_locked
from apps.labour.models import Shift
from apps.labour.services import ENTRY_WINDOW_DAYS, ClockError, recent_days, record_shift

SESSION_KEY = "hours_worker"
IDLE_SECONDS = 180


def _staff():
    return (
        User.objects.filter(is_active=True, is_active_staff=True)
        .exclude(role=Role.OWNER)
        .exclude(pin="")
        .order_by("display_name", "username")
    )


def _worker(request) -> User | None:
    """The worker who entered their PIN on this tablet, if still within the visit."""
    visit = request.session.get(SESSION_KEY)
    if not visit:
        return None
    if clock.time() - visit["at"] > IDLE_SECONDS:
        request.session.pop(SESSION_KEY, None)
        return None
    visit["at"] = clock.time()
    request.session[SESSION_KEY] = visit
    return _staff().filter(pk=visit["pk"]).first()


class ShiftForm(forms.Form):
    day = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    start = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time"}))
    end = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time"}))
    catering = forms.BooleanField(required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        today = timezone.localdate()
        self.fields["day"].widget.attrs.update(
            max=today.isoformat(), min=(today - timedelta(days=ENTRY_WINDOW_DAYS - 1)).isoformat()
        )


@login_required
def names(request):
    request.session.pop(SESSION_KEY, None)
    return render(request, "labour/names.html", {"staff": _staff()})


# Implements: FR-102, D-04.
@login_required
def pin(request, pk):
    person = get_object_or_404(_staff(), pk=pk)
    error = None
    if request.method == "POST":
        if is_locked(person):
            error = f"Too many wrong tries. Please wait {int(LOCKOUT.total_seconds() // 60)} minutes."
        elif check_pin(person, request.POST.get("pin", "")):
            request.session[SESSION_KEY] = {"pk": person.pk, "at": clock.time()}
            return redirect("hours_me")
        else:
            person.refresh_from_db()
            error = (
                f"Too many wrong tries. Please wait {int(LOCKOUT.total_seconds() // 60)} minutes."
                if is_locked(person)
                else "That PIN is not right. Please try again."
            )
    return render(request, "labour/pin.html", {"person": person, "error": error})


# Implements: FR-109.
@login_required
def me(request):
    worker = _worker(request)
    if worker is None:
        return redirect("hours_names")
    days = recent_days(worker)
    saved = None
    if request.GET.get("saved", "").isdigit():
        saved = Shift.objects.filter(pk=request.GET["saved"], employee=worker).first()
    return render(
        request,
        "labour/me.html",
        {
            "worker": worker,
            "days": days,
            # Today is left out of the reminder: the shift may still be going.
            "empty_days": [d for d in days[1:] if d.is_empty],
            "week_hours": sum(d.hours for d in days),
            "saved": saved,
        },
    )


# Implements: FR-101, FR-108.
@login_required
def add(request):
    worker = _worker(request)
    if worker is None:
        return redirect("hours_names")
    location = Location.objects.filter(kind=Location.Kind.RESTAURANT, is_active=True).first()
    if location is None:
        return render(request, "stock/no_locations.html", status=400)

    if request.method != "POST":
        try:
            day = date.fromisoformat(request.GET["date"])
        except (KeyError, ValueError):
            day = timezone.localdate()
        return render(request, "labour/add.html", {"worker": worker, "form": ShiftForm(initial={"day": day})})

    form = ShiftForm(request.POST)
    if not form.is_valid() or request.POST.get("step") == "edit":
        return render(request, "labour/add.html", {"worker": worker, "form": form})
    data = form.cleaned_data
    saving = request.POST.get("step") == "save"
    try:
        shift = record_shift(
            worker,
            day=data["day"],
            start=data["start"],
            end=data["end"],
            location=location,
            is_catering_event=data["catering"],
            save=saving,
        )
    except ClockError as e:
        return render(request, "labour/add.html", {"worker": worker, "form": form, "error": str(e)})

    if saving:
        return redirect(f"{reverse('hours_me')}?saved={shift.pk}")
    return render(request, "labour/confirm.html", {"worker": worker, "form": form, "shift": shift})


@login_required
@require_POST
def done(request):
    request.session.pop(SESSION_KEY, None)
    return redirect("hours_names")
