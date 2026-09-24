"""
The hours the owners pay out on.

Everything is added up in whole minutes and only turned into hours at the
end. Rounding each shift to two decimal places first and then adding would
drift by a few minutes a fortnight -- small, but it is somebody's pay, and
the total must equal the shifts it came from exactly.

This reports hours, not money. The owners were explicit that payroll is not
wanted; they multiply by their own rates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Count, Prefetch

from apps.core.models import Role, User
from apps.labour.models import Period, Shift

# Monday. The owners have not said which day their pay week starts; this is
# the one number to change when they do.
WEEK_STARTS = getattr(settings, "LABOUR_WEEK_STARTS", 0)

# A shift written down this many days or more after it was worked is shown
# to the owners, since memory after that is less reliable than on the day.
LATE_ENTRY_DAYS = 2

PERIODS = {
    "this_week": "This week",
    "last_week": "Last week",
    "last_2_weeks": "Last 2 weeks",
    "this_month": "This month",
    "last_month": "Last month",
}


def period_dates(name: str, today: date) -> tuple[date, date]:
    """First and last day, inclusive, of a named period ending at or before today."""
    week_start = today - timedelta(days=(today.weekday() - WEEK_STARTS) % 7)
    if name == "this_week":
        return week_start, today
    if name == "last_week":
        return week_start - timedelta(days=7), week_start - timedelta(days=1)
    if name == "last_2_weeks":
        return week_start - timedelta(days=14), week_start - timedelta(days=1)
    if name == "this_month":
        return today.replace(day=1), today
    if name == "last_month":
        last = today.replace(day=1) - timedelta(days=1)
        return last.replace(day=1), last
    raise ValueError(name)


def hours(minutes: int) -> Decimal:
    return (Decimal(minutes) / Decimal(60)).quantize(Decimal("0.01"))


@dataclass
class PersonHours:
    person: User
    shifts: list = field(default_factory=list)
    total: int = 0
    morning: int = 0
    evening: int = 0
    catering: int = 0
    open_shifts: int = 0
    late_entries: int = 0
    corrected: int = 0

    @property
    def days(self) -> int:
        return len({s.business_date for s in self.shifts if not s.is_open})

    @property
    def total_hours(self) -> Decimal:
        return hours(self.total)

    @property
    def needs_attention(self) -> bool:
        return bool(self.open_shifts or self.late_entries or self.person.needs_review)


@dataclass
class Report:
    start: date
    end: date
    rows: list[PersonHours]

    @property
    def total(self) -> int:
        return sum(r.total for r in self.rows)

    @property
    def total_hours(self) -> Decimal:
        return hours(self.total)

    @property
    def open_shifts(self) -> int:
        return sum(r.open_shifts for r in self.rows)


def _shifts(start: date, end: date):
    return (
        Shift.objects.filter(business_date__range=(start, end))
        .select_related("employee", "employee__position", "pay_run")
        .annotate(edit_count=Count("edits"))
        .order_by("clocked_in_at")
    )


def _add(row: PersonHours, shift: Shift) -> None:
    row.shifts.append(shift)
    if getattr(shift, "edit_count", 0):
        row.corrected += 1
    if shift.days_late_entered >= LATE_ENTRY_DAYS:
        row.late_entries += 1
    minutes = shift.worked_minutes
    if minutes is None:
        # Not counted: nobody knows when it ended. Shown instead, so it is
        # fixed before payday rather than silently paid as nothing.
        row.open_shifts += 1
        return
    row.total += minutes
    if shift.period == Period.MORNING:
        row.morning += minutes
    else:
        row.evening += minutes
    if shift.is_catering_event:
        row.catering += minutes


def aggregate(shifts, row_class=PersonHours) -> list:
    """One row per person, alphabetical, from any set of shifts."""
    rows: dict = {}
    for shift in shifts:
        row = rows.setdefault(shift.employee_id, row_class(person=shift.employee))
        _add(row, shift)
    return sorted(rows.values(), key=lambda r: str(r.person).casefold())


# Implements: FR-106, FR-112.
def hours_report(start: date, end: date) -> Report:
    return Report(start=start, end=end, rows=aggregate(_shifts(start, end)))


# Implements: FR-109, FR-112.
def person_hours(person: User, start: date, end: date) -> PersonHours:
    row = PersonHours(person=person)
    for shift in (
        _shifts(start, end).filter(employee=person).prefetch_related(Prefetch("edits", to_attr="edit_list"))
    ):
        _add(row, shift)
    return row


def people_to_review():
    """Added on the tablet and not yet looked at by an owner."""
    return (
        User.objects.filter(needs_review=True, is_active_staff=True)
        .exclude(role=Role.OWNER)
        .select_related("position", "possible_duplicate_of")
        .order_by("date_joined")
    )
