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
from apps.labour.models import Period, Shift, ShiftEdit, ShiftTemplate

# Used only for a position with no schedule set. The restaurant closes at 3pm,
# so anybody clocking in from then on is there for the evening.
UNSCHEDULED_EVENING_FROM = time(15, 0)

# Longer than any real shift, so an evening that runs past midnight can still
# be clocked out; shorter than the gap to the next morning, so a shift nobody
# closed yesterday is recognised as missed rather than still going.
STALE_AFTER = timedelta(hours=16)


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

    shift = Shift.objects.select_for_update().get(pk=shift.pk)
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
