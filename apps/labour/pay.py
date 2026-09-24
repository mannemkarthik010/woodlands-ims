"""
Paying out: the owners choose a date, and every unpaid hour up to it is paid.

The owners run their own pay cycle -- the 15th this time, the end of the
month next -- so the system never decides when a period ends. It keeps one
fact per shift: which payment covered it. Everything follows from that.

- "Unpaid" is a shift with no payment, not a date range. A shift written in
  late for a fortnight already paid was never paid, so the next payment
  picks it up and says where it came from. Nothing is paid twice and
  nothing falls through the gap between two periods.
- A payment is final. Its shifts cannot be corrected, and a duplicate
  person holding paid hours cannot be merged, until the payment is undone
  -- with a reason, and the record of it kept -- because what was handed
  over must always match the shifts it was for.
- People added on the tablet and not yet looked at cannot be paid: a
  duplicate paid separately is the mistake this whole module exists to
  prevent.
- A shift with no end time is never paid. It is shown, and waits for an
  owner to say when the person left.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.core.models import Role, User
from apps.labour.models import PayRun, PayRunLine, Shift
from apps.labour.reports import PersonHours, aggregate
from apps.labour.services import ClockError, NotAllowed


class PayError(ClockError):
    """Raised with a sentence that can be shown to the owner as it is."""


def _must_be_owner(by: User) -> None:
    if by.role != Role.OWNER and not by.is_superuser:
        raise NotAllowed("Only an owner can do that.")


def last_paid_up_to(person: User) -> date | None:
    return PayRunLine.objects.filter(employee=person, pay_run__voided_at__isnull=True).aggregate(
        d=Max("pay_run__paid_up_to")
    )["d"]


@dataclass
class UnpaidHours(PersonHours):
    last_paid_up_to: date | None = None
    # Shifts dated on or before the last day this person was already paid up
    # to: written in late, or unfinished at the time, and caught now.
    from_paid_period: int = 0

    @property
    def first_day(self) -> date | None:
        days = [s.business_date for s in self.shifts if not s.is_open]
        return min(days) if days else None

    @property
    def last_day(self) -> date | None:
        days = [s.business_date for s in self.shifts if not s.is_open]
        return max(days) if days else None


@dataclass
class Unpaid:
    up_to: date
    rows: list[UnpaidHours] = field(default_factory=list)

    @property
    def payable(self) -> list[UnpaidHours]:
        return [r for r in self.rows if r.total]

    @property
    def total(self) -> int:
        return sum(r.total for r in self.rows)

    @property
    def open_shifts(self) -> int:
        return sum(r.open_shifts for r in self.rows)

    @property
    def first_day(self) -> date | None:
        days = [r.first_day for r in self.rows if r.first_day]
        return min(days) if days else None

    @property
    def to_review(self) -> list[User]:
        return [r.person for r in self.rows if r.person.needs_review]


def _unpaid_shifts(up_to: date):
    return (
        Shift.objects.filter(pay_run__isnull=True, business_date__lte=up_to)
        .select_related("employee", "employee__position")
        .order_by("clocked_in_at")
    )


# Implements: FR-106.
def unpaid(up_to: date) -> Unpaid:
    """Everybody's unpaid hours up to and including `up_to`, one row each."""
    result = Unpaid(up_to=up_to, rows=aggregate(_unpaid_shifts(up_to), row_class=UnpaidHours))
    for row in result.rows:
        row.last_paid_up_to = last_paid_up_to(row.person)
        if row.last_paid_up_to:
            row.from_paid_period = sum(
                1 for s in row.shifts if not s.is_open and s.business_date <= row.last_paid_up_to
            )
    return result


def default_up_to(today: date | None = None) -> date:
    return today or timezone.localdate()


# Implements: FR-106, FR-113.
@transaction.atomic
def pay(up_to: date, *, people: list[User], by: User, note: str = "") -> PayRun:
    """
    Mark every unpaid, finished shift up to `up_to` as paid, for `people`.
    Returns the payment. Pressing the button twice pays nothing the second
    time: the shifts are already linked, so there is nothing left to pay.
    """
    _must_be_owner(by)
    today = timezone.localdate()
    if up_to > today:
        raise PayError("You can only pay for days that have happened.")
    if not people:
        raise PayError("Choose at least one person to pay.")
    unconfirmed = [p for p in people if p.needs_review]
    if unconfirmed:
        names = ", ".join(str(p) for p in unconfirmed)
        raise PayError(f"Please confirm or merge {names} first. They were added on the tablet.")

    shifts = list(
        Shift.objects.select_for_update()
        .filter(
            pay_run__isnull=True,
            clocked_out_at__isnull=False,
            business_date__lte=up_to,
            employee__in=people,
        )
        .select_related("employee")
        .order_by("clocked_in_at")
    )
    if not shifts:
        raise PayError("There is nothing unpaid up to that day for the people chosen.")

    run = PayRun.objects.create(paid_up_to=up_to, paid_on=today, note=note[:240], created_by=by)
    for row in aggregate(shifts):
        days = [s.business_date for s in row.shifts]
        PayRunLine.objects.create(
            pay_run=run,
            employee=row.person,
            first_day=min(days),
            last_day=max(days),
            shift_count=len(row.shifts),
            minutes=row.total,
            morning_minutes=row.morning,
            evening_minutes=row.evening,
            catering_minutes=row.catering,
            created_by=by,
        )
    Shift.objects.filter(pk__in=[s.pk for s in shifts]).update(pay_run=run, updated_at=timezone.now())
    return run


@transaction.atomic
def undo_payment(run: PayRun, *, by: User, reason: str) -> None:
    """
    A payment made by mistake. Its shifts become unpaid again; the payment
    and its lines stay, marked as undone, with who and why.
    """
    _must_be_owner(by)
    reason = (reason or "").strip()
    if not reason:
        raise PayError("Please say why this payment is being undone. It is kept with the record.")
    run = PayRun.objects.select_for_update().get(pk=run.pk)
    if run.is_void:
        raise PayError("This payment was already undone.")
    run.voided_at = timezone.now()
    run.voided_by = by
    run.void_reason = reason[:240]
    run.save(update_fields=["voided_at", "voided_by", "void_reason", "updated_at"])
    Shift.objects.filter(pay_run=run).update(pay_run=None, updated_at=timezone.now())
