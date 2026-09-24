"""
The only code permitted to open, close or correct a shift.

The tablet screen, the admin and anything later (a Slack button to fix a
missed clock-out) all come through here, so the rules below are true however
a record was made:

- one open shift per person at a time -- a second tap is an answer, not a
  second shift;
- a shift still open after STALE_AFTER is a missed clock-out. It never blocks
  the next clock-in; it stays open and flagged until an owner corrects it,
  because the system does not know when that person went home and will not
  invent a time;
- which shift somebody is starting is decided by the schedule of their
  position, and that schedule is copied onto the shift (ADR 0008);
- only an owner corrects a record, a reason is required, and every changed
  field is written to `ShiftEdit` and kept.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import Location, Role, User
from apps.labour.models import Period, Shift, ShiftEdit, ShiftTemplate, Source

# Used only for a position with no schedule set. The restaurant closes at 3pm,
# so anybody clocking in from then on is there for the evening.
UNSCHEDULED_EVENING_FROM = time(15, 0)

# Longer than any real shift, so an evening that runs past midnight can still
# be clocked out; shorter than the gap to the next morning, so a shift nobody
# closed yesterday is recognised as missed rather than still going.
STALE_AFTER = timedelta(hours=16)

# Entered shifts (a worker writing in their own hours afterwards).
# A shift longer than this is almost certainly a typo -- 5pm to 10am instead
# of 10pm -- and is refused rather than paid.
MAX_SHIFT = timedelta(hours=16)
# How far back a worker may fill in a day themselves. Older than this, the
# memory is not reliable enough to take on trust; an owner enters it.
ENTRY_WINDOW_DAYS = 14
# Somebody filling in their hours as they walk out may be a few minutes early.
FUTURE_GRACE = timedelta(minutes=15)


class ClockError(Exception):
    """Raised with a sentence that can be shown to the person as it is."""


class AlreadyClockedIn(ClockError):
    pass


class NotClockedIn(ClockError):
    pass


class NotAllowed(ClockError):
    pass


@dataclass(frozen=True)
class Schedule:
    period: str
    starts_at: time | None
    ends_at: time | None


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def schedule_for(user: User, at: datetime) -> Schedule:
    """
    Which shift a clock-in at `at` is starting, for this person.

    The template whose start is nearest wins; on an exact tie, the later one,
    since somebody between two shifts is arriving for the next rather than
    the one that has already begun. A weekday-specific template replaces the
    every-day one for that period on that day.
    """
    local = timezone.localtime(at)
    templates: dict[str, ShiftTemplate] = {}
    if user.position_id:
        rows = ShiftTemplate.objects.filter(
            Q(weekday__isnull=True) | Q(weekday=local.weekday()), position_id=user.position_id
        )
        for row in rows:
            if row.period not in templates or row.weekday is not None:
                templates[row.period] = row

    if not templates:
        period = Period.EVENING if local.time() >= UNSCHEDULED_EVENING_FROM else Period.MORNING
        return Schedule(period=period, starts_at=None, ends_at=None)

    now = _minutes(local.time())
    best = min(templates.values(), key=lambda t: (abs(now - _minutes(t.starts_at)), -_minutes(t.starts_at)))
    return Schedule(period=best.period, starts_at=best.starts_at, ends_at=best.ends_at)


def current_shift(user: User, at: datetime | None = None) -> Shift | None:
    """The shift this person is on now: open, and started within STALE_AFTER."""
    at = at or timezone.now()
    return (
        Shift.objects.filter(
            employee=user,
            clocked_out_at__isnull=True,
            clocked_in_at__gt=at - STALE_AFTER,
            clocked_in_at__lte=at,
        )
        .order_by("-clocked_in_at")
        .first()
    )


# Implements: FR-107.
def missed_clock_outs(at: datetime | None = None):
    """Shifts nobody closed. Each needs an owner to say when the person left."""
    at = at or timezone.now()
    return Shift.objects.filter(clocked_out_at__isnull=True, clocked_in_at__lte=at - STALE_AFTER)


def _check_may_clock(user: User) -> None:
    if not user.can_use_pin:
        raise NotAllowed("Owners are not on the clock.")
    if not user.is_active or not user.is_active_staff:
        raise NotAllowed("This person is no longer active. An owner can reactivate them.")


# Implements: FR-101, FR-108.
@transaction.atomic
def clock_in(
    user: User,
    *,
    location: Location,
    at: datetime | None = None,
    is_catering_event: bool = False,
    photo=None,
) -> Shift:
    at = at or timezone.now()
    _check_may_clock(user)
    # Lock the person's row so two taps a moment apart cannot both get
    # through the check below.
    User.objects.select_for_update().get(pk=user.pk)

    existing = current_shift(user, at)
    if existing:
        since = timezone.localtime(existing.clocked_in_at)
        raise AlreadyClockedIn(f"Already clocked in since {since:%-I:%M %p}.")

    schedule = schedule_for(user, at)
    return Shift.objects.create(
        employee=user,
        location=location,
        business_date=timezone.localdate(at),
        clocked_in_at=at,
        period=schedule.period,
        position_id=user.position_id,
        scheduled_start=schedule.starts_at,
        scheduled_end=schedule.ends_at,
        is_catering_event=is_catering_event,
        clock_in_photo=photo,
    )


# Implements: FR-101.
@transaction.atomic
def clock_out(user: User, *, at: datetime | None = None) -> Shift:
    at = at or timezone.now()
    _check_may_clock(user)
    User.objects.select_for_update().get(pk=user.pk)

    shift = current_shift(user, at)
    if shift is None:
        raise NotClockedIn("Not clocked in.")
    if at <= shift.clocked_in_at:
        raise ClockError("Clock-out cannot be before clock-in.")
    shift.clocked_out_at = at
    shift.save(update_fields=["clocked_out_at", "updated_at"])
    return shift


def _display(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return f"{timezone.localtime(value):%Y-%m-%d %H:%M}"
    return str(value)


CORRECTABLE = ("clocked_in_at", "clocked_out_at", "period", "is_catering_event")


# Implements: FR-105, FR-113.
@transaction.atomic
def correct_shift(shift: Shift, *, by: User, reason: str, **changes) -> Shift:
    """
    Change a clock record, keeping what it was. `changes` may name any of
    CORRECTABLE. A field set to the value it already has is not an edit and
    writes nothing.
    """
    if by.role != Role.OWNER:
        raise NotAllowed("Only an owner can correct a clock record.")
    reason = (reason or "").strip()
    if not reason:
        raise ClockError("A correction needs a reason. It is kept with the record.")
    unknown = set(changes) - set(CORRECTABLE)
    if unknown:
        raise ClockError(f"Cannot correct {', '.join(sorted(unknown))}.")

    shift = Shift.objects.select_for_update().select_related("pay_run").get(pk=shift.pk)
    if shift.pay_run_id:
        raise ClockError(
            f"This shift is already paid ({shift.pay_run}). Undo that payment first, then correct it."
        )
    clocked_in = changes.get("clocked_in_at", shift.clocked_in_at)
    clocked_out = changes.get("clocked_out_at", shift.clocked_out_at)
    if clocked_out is not None and clocked_out <= clocked_in:
        raise ClockError("Clock-out cannot be before clock-in.")
    if "period" in changes and changes["period"] not in Period.values:
        raise ClockError("A shift is morning or evening.")

    edits = []
    for field, new in changes.items():
        old = getattr(shift, field)
        if old == new:
            continue
        edits.append(
            ShiftEdit(
                shift=shift,
                field_name=field,
                old_value=_display(old),
                new_value=_display(new),
                reason=reason,
                created_by=by,
            )
        )
        setattr(shift, field, new)

    if edits:
        shift.business_date = timezone.localdate(shift.clocked_in_at)
        shift.save()
        ShiftEdit.objects.bulk_create(edits)
    return shift


# Implements: FR-106.
def hours_for_day(user: User, day: date) -> Decimal:
    """Closed shifts only; an open shift has no hours until somebody closes it."""
    total = sum((s.worked_minutes or 0) for s in Shift.objects.filter(employee=user, business_date=day))
    return (Decimal(total) / Decimal(60)).quantize(Decimal("0.01"))


def _overlapping(user: User, start: datetime, end: datetime):
    return Shift.objects.filter(employee=user, clocked_in_at__lt=end).filter(
        Q(clocked_out_at__gt=start) | Q(clocked_out_at__isnull=True, clocked_in_at__gte=start)
    )


# Implements: FR-101, FR-108.
def record_shift(
    user: User,
    *,
    day: date,
    start: time,
    end: time,
    location: Location,
    is_catering_event: bool = False,
    now: datetime | None = None,
    save: bool = True,
) -> Shift:
    """
    A shift written in by the worker afterwards: the date, when they started,
    when they left.

    An end time at or before the start time means the shift ran past
    midnight. With `save=False` the shift is checked and returned unsaved, so
    the screen can show exactly what will be recorded before it is.
    """
    now = now or timezone.now()
    _check_may_clock(user)
    today = timezone.localdate(now)

    if day > today:
        raise ClockError("That date hasn't happened yet.")
    if (today - day).days >= ENTRY_WINDOW_DAYS:
        raise ClockError(f"That is more than {ENTRY_WINDOW_DAYS} days ago. Please ask an owner to add it.")

    started = timezone.make_aware(datetime.combine(day, start))
    ended = timezone.make_aware(datetime.combine(day, end))
    if ended <= started:
        ended = timezone.make_aware(datetime.combine(day + timedelta(days=1), end))
    if ended - started > MAX_SHIFT:
        hours = int((ended - started).total_seconds() // 3600)
        raise ClockError(f"That is {hours} hours. Please check the start and end times.")
    if ended > now + FUTURE_GRACE:
        raise ClockError("The end time hasn't happened yet. Record the shift when you leave.")

    clash = _overlapping(user, started, ended).first()
    if clash:
        a = timezone.localtime(clash.clocked_in_at)
        b = (
            f"{timezone.localtime(clash.clocked_out_at):%-I:%M %p}"
            if clash.clocked_out_at
            else "not finished"
        )
        raise ClockError(f"This overlaps a shift already recorded: {a:%a %-d %b, %-I:%M %p} – {b}.")

    schedule = schedule_for(user, started)
    shift = Shift(
        employee=user,
        location=location,
        business_date=day,
        clocked_in_at=started,
        clocked_out_at=ended,
        period=schedule.period,
        position_id=user.position_id,
        scheduled_start=schedule.starts_at,
        scheduled_end=schedule.ends_at,
        is_catering_event=is_catering_event,
        source=Source.ENTERED,
        created_by=user,
    )
    if save:
        with transaction.atomic():
            # Two quick submissions of the same form must not both pass the
            # overlap check above.
            User.objects.select_for_update().get(pk=user.pk)
            if _overlapping(user, started, ended).exists():
                raise ClockError("This overlaps a shift already recorded.")
            shift.save()
    return shift


@dataclass(frozen=True)
class Day:
    day: date
    shifts: list
    hours: Decimal
    minutes: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.shifts


# Implements: FR-109.
def recent_days(user: User, *, days: int = 7, today: date | None = None) -> list[Day]:
    """
    The last `days` days for one person, newest first, including the days
    with nothing recorded -- those are the ones a forgetful week leaves
    behind, and the screen offers to fill them in.
    """
    today = today or timezone.localdate()
    first = today - timedelta(days=days - 1)
    by_day: dict[date, list[Shift]] = {}
    for shift in Shift.objects.filter(employee=user, business_date__range=(first, today)).order_by(
        "clocked_in_at"
    ):
        by_day.setdefault(shift.business_date, []).append(shift)

    out = []
    for i in range(days):
        d = today - timedelta(days=i)
        shifts = by_day.get(d, [])
        minutes = sum(s.worked_minutes or 0 for s in shifts)
        out.append(
            Day(
                day=d,
                shifts=shifts,
                hours=(Decimal(minutes) / Decimal(60)).quantize(Decimal("0.01")),
                minutes=minutes,
            )
        )
    return out


def _must_be_owner(by: User) -> None:
    if by.role != Role.OWNER and not by.is_superuser:
        raise NotAllowed("Only an owner can do that.")


# Implements: FR-1201.
def confirm_person(person: User, *, by: User) -> None:
    """An owner has looked at somebody added on the tablet and they are real and new."""
    _must_be_owner(by)
    person.needs_review = False
    person.possible_duplicate_of = None
    person.save(update_fields=["needs_review", "possible_duplicate_of"])


# Implements: FR-105, FR-113, FR-1208.
@transaction.atomic
def merge_person(duplicate: User, *, into: User, by: User) -> int:
    """
    `duplicate` was the same person as `into` all along. Every shift moves
    across, each move is written to that shift's corrections, and the
    duplicate is switched off rather than deleted -- the record that the
    name once existed is part of the history.

    Refused if any moved shift would overlap one `into` already has: that
    is two records of the same hours, and an owner must decide which is
    right before they are added together. Returns the number of shifts moved.
    """
    _must_be_owner(by)
    if duplicate.pk == into.pk:
        raise ClockError("Pick a different person to merge into.")
    if not into.can_use_pin or not duplicate.can_use_pin:
        raise NotAllowed("Owners are not on the clock.")

    shifts = list(Shift.objects.select_for_update().filter(employee=duplicate).order_by("clocked_in_at"))
    if any(s.pay_run_id for s in shifts):
        raise ClockError(
            f"Some of {duplicate}'s hours are already paid. Undo that payment first, then merge, "
            "so the payment is not left naming the wrong person."
        )
    for shift in shifts:
        end = shift.clocked_out_at or shift.clocked_in_at + timedelta(minutes=1)
        if _overlapping(into, shift.clocked_in_at, end).exists():
            when = timezone.localtime(shift.clocked_in_at)
            raise ClockError(
                f"{duplicate} and {into} both have hours on {when:%a %-d %b} around {when:%-I:%M %p}. "
                "Correct one of them first, then merge."
            )

    reason = f"Merged: {duplicate} was a second entry for {into}"
    for shift in shifts:
        ShiftEdit.objects.create(
            shift=shift,
            field_name="employee",
            old_value=str(duplicate)[:80],
            new_value=str(into)[:80],
            reason=reason[:240],
            created_by=by,
        )
    Shift.objects.filter(pk__in=[s.pk for s in shifts]).update(employee=into, updated_at=timezone.now())

    duplicate.is_active_staff = False
    duplicate.needs_review = False
    duplicate.pin = ""
    duplicate.save(update_fields=["is_active_staff", "needs_review", "pin"])
    return len(shifts)
