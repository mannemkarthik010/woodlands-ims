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

import csv
import time as clock
from contextlib import suppress
from datetime import date, timedelta

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import make_password
from django.db.models import Count, Prefetch, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from apps.core.models import Location, Position, Role, User
from apps.core.people import AlreadyListed, LooksLike, PersonError, add_person
from apps.core.permissions import owner_required
from apps.core.pins import LOCKOUT, PinError, check_pin, is_locked, validate_pin
from apps.labour.models import PayRun, PayRunLine, Period, Shift
from apps.labour.pay import default_up_to, pay, undo_payment, unpaid
from apps.labour.reports import PERIODS, hours, hours_report, people_to_review, period_dates, person_hours
from apps.labour.services import (
    ENTRY_WINDOW_DAYS,
    ClockError,
    cancel_shift,
    confirm_person,
    correct_shift,
    merge_person,
    owner_add_shift,
    recent_days,
    record_shift,
    span,
)

SESSION_KEY = "hours_worker"
PENDING_KEY = "hours_new_person"
IDLE_SECONDS = 180


def _people():
    """Everybody on staff, whether or not they have a PIN yet. What owners act on."""
    return (
        User.objects.filter(is_active=True, is_active_staff=True)
        .exclude(role=Role.OWNER)
        .order_by("display_name", "username")
    )


def _staff():
    """The names on the tablet: only people who can sign in there."""
    return _people().exclude(pin="")


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


def _begin_visit(request, person: User) -> None:
    request.session[SESSION_KEY] = {"pk": person.pk, "at": clock.time()}


LOCKED = f"Too many wrong tries. Please wait {int(LOCKOUT.total_seconds() // 60)} minutes."


# Implements: FR-102, D-04.
@login_required
def start(request):
    """
    Pick your name from the list and enter your PIN, on one screen. A list
    rather than typing, so the same person is never spelled two ways.
    """
    request.session.pop(SESSION_KEY, None)
    # Somebody who reached "Is this you?" and walked away leaves nothing behind.
    request.session.pop(PENDING_KEY, None)
    context = {"staff": _staff(), "selected": request.GET.get("person", "")}
    if request.method != "POST":
        return render(request, "labour/start.html", context)

    context["selected"] = request.POST.get("person", "")
    person = _staff().filter(pk=context["selected"]).first() if context["selected"].isdigit() else None
    if person is None:
        context["error"] = "Please choose your name from the list."
    elif is_locked(person):
        context["error"] = LOCKED
    elif check_pin(person, request.POST.get("pin", "")):
        _begin_visit(request, person)
        return redirect("hours_me")
    else:
        person.refresh_from_db()
        context["error"] = LOCKED if is_locked(person) else "That PIN is not right. Please try again."
    return render(request, "labour/start.html", context)


class NewPersonForm(forms.Form):
    first = forms.CharField(label="First name", max_length=60)
    last = forms.CharField(label="Last name", max_length=60, required=False)
    position = forms.ModelChoiceField(
        label="Your job",
        queryset=Position.objects.filter(is_active=True),
        required=False,
        empty_label="Not sure",
        widget=forms.Select(attrs={"class": "select"}),
    )
    pin = forms.CharField(
        label="Choose a PIN (4–6 digits)",
        widget=forms.PasswordInput(attrs={"inputmode": "numeric", "autocomplete": "off"}),
    )
    pin_again = forms.CharField(
        label="Type the PIN again",
        widget=forms.PasswordInput(attrs={"inputmode": "numeric", "autocomplete": "off"}),
    )

    def clean(self):
        data = super().clean()
        if data.get("pin") and data.get("pin") != data.get("pin_again"):
            self.add_error("pin_again", "The two PINs are different.")
        return data


def _welcome(request, person: User):
    request.session.pop(PENDING_KEY, None)
    _begin_visit(request, person)
    return redirect(f"{reverse('hours_me')}?welcome=1")


# Implements: FR-1201.
@login_required
def new_person(request):
    """
    Somebody not on the list adds themselves, so nobody is ever stuck at the
    end of a shift. Same and similar names are caught (`apps.core.people`),
    and every person added here is marked for an owner to look over.
    """
    request.session.pop(SESSION_KEY, None)

    if request.method == "POST" and request.POST.get("step") == "different":
        # They have seen "Is this you?" and said no. Their details and the
        # (already hashed) PIN waited on the server, not in the page.
        pending = request.session.get(PENDING_KEY)
        if not pending:
            return redirect("hours_new")
        seen = list(User.objects.filter(pk__in=pending["seen"]))
        try:
            person = add_person(
                pending["first"],
                pending["last"],
                pin_hash=pending["pin_hash"],
                position=Position.objects.filter(pk=pending["position"]).first(),
                different_from=seen,
            )
        except LooksLike as e:
            pending["seen"] = sorted({*pending["seen"], *(p.pk for p in e.people)})
            request.session[PENDING_KEY] = pending
            return render(request, "labour/looks_like.html", {"people": e.people, "pending": pending})
        except PersonError as e:
            request.session.pop(PENDING_KEY, None)
            return render(request, "labour/new_person.html", {"form": NewPersonForm(), "error": str(e)})
        return _welcome(request, person)

    form = NewPersonForm(request.POST or None)
    if request.method != "POST" or not form.is_valid():
        return render(request, "labour/new_person.html", {"form": form})

    data = form.cleaned_data
    try:
        validate_pin(data["pin"], role=Role.KITCHEN)
        person = add_person(data["first"], data["last"], pin=data["pin"], position=data["position"])
    except AlreadyListed as e:
        return render(request, "labour/new_person.html", {"form": form, "error": str(e), "listed": e.person})
    except LooksLike as e:
        request.session[PENDING_KEY] = {
            "first": data["first"],
            "last": data["last"],
            "position": data["position"].pk if data["position"] else None,
            "pin_hash": make_password(data["pin"]),
            "seen": [p.pk for p in e.people],
        }
        return render(
            request, "labour/looks_like.html", {"people": e.people, "pending": request.session[PENDING_KEY]}
        )
    except (PersonError, PinError) as e:
        return render(request, "labour/new_person.html", {"form": form, "error": str(e)})
    return _welcome(request, person)


# Implements: FR-109.
@login_required
def me(request):
    worker = _worker(request)
    if worker is None:
        return redirect("hours_start")
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
            # So are days before the person joined -- they cannot have missed them.
            "empty_days": [
                d for d in days[1:] if d.is_empty and d.day >= timezone.localdate(worker.date_joined)
            ],
            "week_minutes": sum(d.minutes for d in days),
            "saved": saved,
            "welcome": request.GET.get("welcome") and worker.needs_review,
        },
    )


# Implements: FR-101, FR-108.
@login_required
def add(request):
    worker = _worker(request)
    if worker is None:
        return redirect("hours_start")
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
    return redirect("hours_start")


# --- The owners' side --------------------------------------------------------


def _period(request) -> tuple[date, date, str]:
    """The dates asked for: a named period, or a from/to range. Last week by default."""
    today = timezone.localdate()
    name = request.GET.get("period", "last_week")
    if name == "custom":
        try:
            start = date.fromisoformat(request.GET["from"])
            end = date.fromisoformat(request.GET["to"])
        except (KeyError, ValueError):
            name = "last_week"
        else:
            if start > end:
                start, end = end, start
            return start, end, name
    if name not in PERIODS:
        name = "last_week"
    start, end = period_dates(name, today)
    return start, end, name


def _csv(filename: str, header: list[str], rows) -> HttpResponse:
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(header)
    writer.writerows(rows)
    return response


# Implements: FR-106, FR-107, FR-112.
@owner_required
def report(request):
    start, end, name = _period(request)
    result = hours_report(start, end)
    if request.GET.get("format") == "csv":
        return _csv(
            f"hours-{start:%Y-%m-%d}-to-{end:%Y-%m-%d}.csv",
            [
                "Name",
                "Job",
                "Days",
                "Morning h",
                "Evening h",
                "of which Catering h (already in Morning/Evening)",
                "Total h",
                "Total h:m",
                "Unfinished shifts (not counted)",
            ],
            [
                [
                    str(r.person),
                    str(r.person.position or ""),
                    r.days,
                    hours(r.morning),
                    hours(r.evening),
                    hours(r.catering),
                    hours(r.total),
                    f"{r.total // 60}:{r.total % 60:02d}",
                    r.open_shifts,
                ]
                for r in result.rows
            ],
        )
    return render(
        request,
        "labour/report.html",
        {
            "report": result,
            "period": name,
            "periods": PERIODS,
            "review": people_to_review(),
            "staff": _people(),
            "query": request.GET.urlencode(),
            "tab": "period",
        },
    )


# Implements: FR-109, FR-112.
@owner_required
def person_report(request, pk):
    """
    One person's hours for the period, shift by shift. Printed or saved as a
    PDF from the browser, it is the statement the owners hand to the worker.
    """
    person = get_object_or_404(User, pk=pk)
    start, end, name = _period(request)
    row = person_hours(person, start, end)
    if request.GET.get("format") == "csv":
        return _csv(
            f"hours-{slugify(str(person))}-{start:%Y-%m-%d}-to-{end:%Y-%m-%d}.csv",
            ["Date", "Shift", "Started", "Left", "Hours", "Catering", "Recorded", "Corrected"],
            [
                [
                    s.business_date.isoformat(),
                    s.get_period_display(),
                    f"{timezone.localtime(s.clocked_in_at):%H:%M}",
                    f"{timezone.localtime(s.clocked_out_at):%H:%M}" if s.clocked_out_at else "not finished",
                    s.hours_worked if s.hours_worked is not None else "",
                    "yes" if s.is_catering_event else "",
                    s.get_source_display(),
                    "yes" if s.edit_list else "",
                ]
                for s in row.shifts
            ],
        )
    cancelled = (
        Shift.all_objects.filter(
            employee=person, business_date__range=(start, end), cancelled_at__isnull=False
        )
        .select_related("cancelled_by")
        .order_by("clocked_in_at")
    )
    return render(
        request,
        "labour/person_report.html",
        {
            "row": row,
            "person": person,
            "start": start,
            "end": end,
            "query": request.GET.urlencode(),
            "here": request.get_full_path(),
            "cancelled": cancelled,
        },
    )


def _back(request) -> str:
    """Where the owner came from, if it is a page on this site; the pay screen otherwise."""
    target = request.POST.get("next", "")
    if url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return target
    return reverse("hours_pay")


@owner_required
@require_POST
def confirm(request, pk):
    person = get_object_or_404(User, pk=pk)
    confirm_person(person, by=request.user)
    messages.success(request, f"{person} confirmed.")
    return redirect(_back(request))


# Implements: FR-105.
@owner_required
@require_POST
def merge(request, pk):
    duplicate = get_object_or_404(User, pk=pk)
    into = _people().filter(pk=request.POST.get("into") or 0).first()
    back = _back(request)
    if into is None:
        messages.error(request, "Choose who they really are.")
        return redirect(back)
    try:
        moved = merge_person(duplicate, into=into, by=request.user)
    except ClockError as e:
        messages.error(request, str(e))
        return redirect(back)
    messages.success(
        request, f"{duplicate} merged into {into}: {moved} shift{'s' if moved != 1 else ''} moved."
    )
    return redirect(back)


# --- Paying out -------------------------------------------------------------


def _up_to(value: str | None) -> date:
    try:
        return min(date.fromisoformat(value or ""), timezone.localdate())
    except ValueError:
        return default_up_to()


# Implements: FR-106.
@owner_required
def pay_screen(request):
    """
    Everybody's unpaid hours up to a day the owner chooses. Tick who is being
    paid, check the summary, mark as paid.
    """
    source = request.POST if request.method == "POST" else request.GET
    up_to = _up_to(source.get("up_to"))
    preview = unpaid(up_to)
    context = {
        "preview": preview,
        "up_to": up_to,
        "review": people_to_review(),
        "staff": _people(),
        "query": f"up_to={up_to.isoformat()}",
        "tab": "pay",
        "needs_fixing": [s for r in preview.rows for s in r.shifts if s.is_open],
        "here": request.get_full_path(),
    }

    if request.method != "POST":
        return render(request, "labour/pay.html", context)

    chosen_ids = {int(i) for i in request.POST.getlist("person") if i.isdigit()}
    rows = [r for r in preview.payable if r.person.pk in chosen_ids]
    if request.POST.get("step") == "pay":
        try:
            run = pay(
                up_to, people=[r.person for r in rows], by=request.user, note=request.POST.get("note", "")
            )
        except ClockError as e:
            context["error"] = str(e)
            return render(request, "labour/pay.html", context)
        messages.success(request, f"Marked as paid: {run.lines.count()} people, up to {up_to:%-d %b}.")
        return redirect("hours_payment", pk=run.pk)

    if not rows:
        context["error"] = "Choose at least one person to pay."
        return render(request, "labour/pay.html", context)
    return render(
        request,
        "labour/pay_confirm.html",
        {
            "rows": rows,
            "up_to": up_to,
            "total": sum(r.total for r in rows),
            "tab": "pay",
            "skipped_open": sum(r.open_shifts for r in rows),
        },
    )


@owner_required
def payments(request):
    # Explicit order: a query with totals ignores the model's default ordering.
    runs = (
        PayRun.objects.annotate(people=Count("lines"), minutes=Sum("lines__minutes"))
        .select_related("created_by", "voided_by")
        .order_by("-paid_up_to", "-created_at")
    )
    return render(request, "labour/payments.html", {"runs": runs, "tab": "payments"})


@owner_required
def payment(request, pk):
    run = get_object_or_404(PayRun.objects.select_related("created_by", "voided_by"), pk=pk)
    lines = run.lines.select_related("employee", "employee__position")
    if request.GET.get("format") == "csv":
        return _csv(
            f"paid-{run.paid_on:%Y-%m-%d}-up-to-{run.paid_up_to:%Y-%m-%d}.csv",
            [
                "Name",
                "From",
                "To",
                "Shifts",
                "Morning h",
                "Evening h",
                "of which Catering h (already in Morning/Evening)",
                "Total h",
                "Total h:m",
            ],
            [
                [
                    str(li.employee),
                    li.first_day.isoformat(),
                    li.last_day.isoformat(),
                    li.shift_count,
                    hours(li.morning_minutes),
                    hours(li.evening_minutes),
                    hours(li.catering_minutes),
                    hours(li.minutes),
                    f"{li.minutes // 60}:{li.minutes % 60:02d}",
                ]
                for li in lines
            ],
        )
    return render(
        request,
        "labour/payment.html",
        {"run": run, "lines": lines, "total": sum(li.minutes for li in lines), "tab": "payments"},
    )


@owner_required
@require_POST
def payment_undo(request, pk):
    run = get_object_or_404(PayRun, pk=pk)
    try:
        undo_payment(run, by=request.user, reason=request.POST.get("reason", ""))
    except ClockError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, "Payment undone. Its hours are unpaid again and show under To pay.")
    return redirect("hours_payment", pk=run.pk)


# Implements: FR-109, FR-112.
@owner_required
def pay_statement(request, pk, person_pk):
    """What one person was paid for in one payment: the page to print and hand them."""
    run = get_object_or_404(PayRun, pk=pk)
    line = get_object_or_404(
        PayRunLine.objects.select_related("employee", "employee__position"),
        pay_run=run,
        employee_id=person_pk,
    )
    shifts = (
        Shift.objects.filter(pay_run=run, employee_id=person_pk)
        .prefetch_related(Prefetch("edits", to_attr="edit_list"))
        .order_by("clocked_in_at")
    )
    return render(request, "labour/pay_statement.html", {"run": run, "line": line, "shifts": shifts})


# --- The owners correcting the record ---------------------------------------


class OwnerShiftForm(forms.Form):
    """What an owner can set on a shift. The reason is not optional."""

    day = forms.DateField(label="Date", widget=forms.DateInput(attrs={"type": "date"}))
    start = forms.TimeField(label="Started", widget=forms.TimeInput(attrs={"type": "time"}))
    end = forms.TimeField(label="Left", widget=forms.TimeInput(attrs={"type": "time"}))
    period = forms.ChoiceField(label="Shift", choices=Period.choices, required=False)
    catering = forms.BooleanField(label="Catering job", required=False)
    reason = forms.CharField(
        label="Why? (kept with the record)",
        max_length=240,
        widget=forms.TextInput(attrs={"placeholder": 'e.g. "Forgot to record leaving; left at 3"'}),
    )

    def __init__(self, *args, adding=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["day"].widget.attrs["max"] = timezone.localdate().isoformat()
        if adding:
            # A new shift's morning or evening comes from the schedule, as on the tablet.
            del self.fields["period"]


def _next_or(request, default: str) -> str:
    target = request.POST.get("next") or request.GET.get("next") or ""
    if url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return target
    return default


def _person_page(person: User, day: date) -> str:
    """The person's page for the week around a day -- where a fixed shift is seen in context."""
    start, end = day - timedelta(days=day.weekday()), day - timedelta(days=day.weekday()) + timedelta(days=6)
    return f"{reverse('hours_person', args=[person.pk])}?period=custom&from={start}&to={end}"


# Implements: FR-105, FR-107.
@owner_required
def shift_fix(request, pk):
    """
    One shift, as recorded and as corrected, with a form to correct it again
    or cancel it. A paid or cancelled shift is shown read-only, with why.
    """
    shift = get_object_or_404(
        Shift.all_objects.select_related("employee", "pay_run", "created_by", "cancelled_by"), pk=pk
    )
    back = _next_or(request, _person_page(shift.employee, shift.business_date))
    start_local = timezone.localtime(shift.clocked_in_at)
    initial = {
        "day": shift.business_date,
        "start": start_local.time().replace(second=0, microsecond=0),
        "end": timezone.localtime(shift.clocked_out_at).time().replace(second=0, microsecond=0)
        if shift.clocked_out_at
        else None,
        "period": shift.period,
        "catering": shift.is_catering_event,
    }
    form = OwnerShiftForm(request.POST or None, initial=initial)
    context = {"shift": shift, "form": form, "next": back, "edits": shift.edits.select_related("created_by")}

    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        started, ended = span(data["day"], data["start"], data["end"])
        try:
            correct_shift(
                shift,
                by=request.user,
                reason=data["reason"],
                clocked_in_at=started,
                clocked_out_at=ended,
                period=data["period"] or shift.period,
                is_catering_event=data["catering"],
            )
        except ClockError as e:
            context["error"] = str(e)
        else:
            messages.success(request, f"{shift.employee}'s shift on {data['day']:%a %-d %b} corrected.")
            return redirect(back)
    return render(request, "labour/shift_fix.html", context)


# Implements: FR-105, FR-113.
@owner_required
@require_POST
def shift_cancel(request, pk):
    shift = get_object_or_404(Shift.all_objects.select_related("employee"), pk=pk)
    back = _next_or(request, _person_page(shift.employee, shift.business_date))
    try:
        cancel_shift(shift, by=request.user, reason=request.POST.get("reason", ""))
    except ClockError as e:
        messages.error(request, str(e))
        return redirect(f"{reverse('hours_shift', args=[pk])}?next={back}")
    messages.success(request, f"{shift.employee}'s shift on {shift.business_date:%a %-d %b} cancelled.")
    return redirect(back)


# Implements: FR-105.
@owner_required
def shift_add(request, pk):
    person = get_object_or_404(_people(), pk=pk)
    location = Location.objects.filter(kind=Location.Kind.RESTAURANT, is_active=True).first()
    if location is None:
        return render(request, "stock/no_locations.html", status=400)
    initial = {}
    with suppress(ValueError):
        initial["day"] = date.fromisoformat(request.GET.get("date", ""))
    form = OwnerShiftForm(request.POST or None, initial=initial, adding=True)
    context = {"person": person, "form": form, "next": _next_or(request, "")}
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            shift = owner_add_shift(
                person,
                day=data["day"],
                start=data["start"],
                end=data["end"],
                location=location,
                by=request.user,
                reason=data["reason"],
                is_catering_event=data["catering"],
            )
        except ClockError as e:
            context["error"] = str(e)
        else:
            messages.success(request, f"Shift added for {person} on {data['day']:%a %-d %b}.")
            return redirect(_next_or(request, _person_page(person, shift.business_date)))
    return render(request, "labour/shift_add.html", context)
