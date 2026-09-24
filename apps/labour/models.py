"""
Labour: clock in, clock out, and showing the owners the hours.

Deliberately narrow. This module does NOT calculate wages, apply overtime
rules, produce payslips or file anything -- the owners were explicit that
payroll is not wanted, and a time module that quietly grows into a payroll
engine is how a small project acquires a compliance obligation nobody asked
for.

The shape of the day, as the owners describe it: a morning shift and an
evening shift, with the restaurant closed between them, and each job keeping
its own hours. Somebody who works both clocks out at the closure and back in
afterwards. Nothing is deducted from anybody's hours, because nobody is on
the clock during a break they are not at work for. See ADR 0008.

Every change to the clock goes through `apps.labour.services`.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.models import Location, Position, TimeStamped


class Period(models.TextChoices):
    MORNING = "MORNING", "Morning"
    EVENING = "EVENING", "Evening"


class Source(models.TextChoices):
    """
    How the times got onto the record. The owners asked to start with staff
    writing in their own hours at the end of a shift; tapping in and out as it
    happens can follow. Both are kept apart so the owners always know which
    they are looking at.
    """

    CLOCK = "CLOCK", "Clocked in and out"
    ENTERED = "ENTERED", "Entered by the worker"


class Weekday(models.IntegerChoices):
    """Python's numbering, so `date.weekday()` can be compared directly."""

    MONDAY = 0, "Monday"
    TUESDAY = 1, "Tuesday"
    WEDNESDAY = 2, "Wednesday"
    THURSDAY = 3, "Thursday"
    FRIDAY = 4, "Friday"
    SATURDAY = 5, "Saturday"
    SUNDAY = 6, "Sunday"


class ShiftTemplate(TimeStamped):
    """
    When a position's morning or evening normally starts and ends.

    `weekday` left empty means every day. A row for a particular weekday
    overrides the every-day row for that day only -- so weekends can run
    later without repeating the other five days.
    """

    position = models.ForeignKey(Position, on_delete=models.CASCADE, related_name="shift_templates")
    period = models.CharField(max_length=8, choices=Period.choices)
    weekday = models.PositiveSmallIntegerField(
        choices=Weekday.choices, null=True, blank=True, help_text="Leave empty for every day."
    )
    starts_at = models.TimeField()
    ends_at = models.TimeField()

    class Meta:
        ordering = ["position", "weekday", "starts_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["position", "period", "weekday"], name="uniq_template_per_position_period_day"
            ),
            # A NULL weekday is not equal to another NULL in a unique index,
            # so "every day" needs its own rule or two could coexist.
            models.UniqueConstraint(
                fields=["position", "period"],
                condition=Q(weekday__isnull=True),
                name="uniq_everyday_template_per_position_period",
            ),
            # No shift here runs past midnight. If one ever does, this is the
            # line to revisit -- deliberately, not by accident.
            models.CheckConstraint(
                condition=Q(ends_at__gt=models.F("starts_at")), name="template_ends_after_start"
            ),
        ]

    def __str__(self) -> str:
        day = self.get_weekday_display() if self.weekday is not None else "Every day"
        return f"{self.position} · {self.get_period_display()} · {day} {self.starts_at:%H:%M}–{self.ends_at:%H:%M}"


# Implements: FR-101, FR-102, FR-106, FR-108, D-04.
class Shift(TimeStamped):
    employee = models.ForeignKey("core.User", on_delete=models.PROTECT, related_name="shifts")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="shifts")

    # The restaurant's calendar day the shift belongs to, in Chatsworth time.
    # Stored rather than derived so "who worked Tuesday" is one indexed query.
    business_date = models.DateField(db_index=True)

    clocked_in_at = models.DateTimeField(db_index=True)
    clocked_out_at = models.DateTimeField(null=True, blank=True)

    # Copied from the schedule when the shift opened (ADR 0008). Changing a
    # position's hours later must not change whether somebody was late then.
    period = models.CharField(max_length=8, choices=Period.choices)
    position = models.ForeignKey(Position, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    scheduled_start = models.TimeField(null=True, blank=True)
    scheduled_end = models.TimeField(null=True, blank=True)

    # Clock-in photograph. Solves buddy-punching for essentially nothing, with
    # no specialist hardware and -- importantly in California -- no biometric
    # data on anybody's books.
    clock_in_photo = models.ImageField(upload_to="clockin/%Y/%m/", null=True, blank=True)

    is_catering_event = models.BooleanField(default=False)
    note = models.CharField(max_length=240, blank=True)

    # `created_at` is when this row was written. For an entered shift that is
    # when the worker filled it in, which may be days after they worked.
    source = models.CharField(max_length=8, choices=Source.choices, default=Source.CLOCK)

    # The payment that covered this shift. Empty means not paid yet -- which
    # is decided by this link, not by the date, so a shift written in late
    # for a week already paid is still picked up by the next payment.
    pay_run = models.ForeignKey(
        "PayRun", null=True, blank=True, on_delete=models.PROTECT, related_name="shifts"
    )

    class Meta:
        ordering = ["-clocked_in_at"]
        indexes = [models.Index(fields=["employee", "business_date"])]
        constraints = [
            models.CheckConstraint(
                condition=Q(clocked_out_at__isnull=True) | Q(clocked_out_at__gt=models.F("clocked_in_at")),
                name="shift_ends_after_start",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.employee} · {self.get_period_display()} · {self.business_date:%a %d %b}"

    # Implements: FR-113.
    def delete(self, *args, **kwargs):
        raise PermissionError("A clock record is never deleted. Correct it instead; the correction is kept.")

    @property
    def is_open(self) -> bool:
        return self.clocked_out_at is None

    @property
    def days_late_entered(self) -> int:
        """How many days after the shift it was written down. 0 for the same day."""
        return (timezone.localdate(self.created_at) - self.business_date).days

    @property
    def has_schedule(self) -> bool:
        return self.scheduled_start is not None

    # Implements: FR-106.
    @property
    def worked_minutes(self) -> int | None:
        """Whole minutes on the clock. None while the shift is still open."""
        if self.clocked_out_at is None:
            return None
        return int((self.clocked_out_at - self.clocked_in_at).total_seconds() // 60)

    # Implements: FR-106.
    @property
    def hours_worked(self) -> Decimal | None:
        """The plain span of the shift, in hours to two places. Nothing is deducted (ADR 0008)."""
        minutes = self.worked_minutes
        if minutes is None:
            return None
        return (Decimal(minutes) / Decimal(60)).quantize(Decimal("0.01"))


# Implements: FR-105, FR-113.
class ShiftEdit(TimeStamped):
    """
    Every correction to a clock record, kept forever. A time record that can
    be changed without trace is not a record. `created_by` is who corrected it.
    """

    shift = models.ForeignKey(Shift, on_delete=models.PROTECT, related_name="edits")
    field_name = models.CharField(max_length=40)
    old_value = models.CharField(max_length=80, blank=True)
    new_value = models.CharField(max_length=80, blank=True)
    reason = models.CharField(max_length=240)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.shift}: {self.field_name} {self.old_value or '—'} → {self.new_value or '—'}"


# Implements: FR-106, FR-112.
class PayRun(TimeStamped):
    """
    One payment: the owners chose a date, and every unpaid, finished shift up
    to it was paid. `created_by` is the owner who marked it paid.

    A payment made by mistake is undone, not deleted: `voided_at` is set, its
    shifts become unpaid again, and this row and its lines stay as the record
    that it happened.
    """

    paid_up_to = models.DateField(help_text="Every unpaid shift up to and including this day.")
    paid_on = models.DateField()
    note = models.CharField(max_length=240, blank=True)

    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    void_reason = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["-paid_up_to", "-created_at"]

    def __str__(self) -> str:
        return f"Paid {self.paid_on:%-d %b %Y}, up to {self.paid_up_to:%-d %b}"

    @property
    def is_void(self) -> bool:
        return self.voided_at is not None

    def delete(self, *args, **kwargs):
        raise PermissionError("A payment is never deleted. Undo it instead; the record is kept.")


class PayRunLine(TimeStamped):
    """
    What one person was paid for in one payment, fixed at the moment it was
    paid. The shifts are linked too, but this is the figure that was handed
    over, and it does not move if anything is corrected afterwards.
    """

    pay_run = models.ForeignKey(PayRun, on_delete=models.PROTECT, related_name="lines")
    employee = models.ForeignKey("core.User", on_delete=models.PROTECT, related_name="pay_lines")
    first_day = models.DateField()
    last_day = models.DateField()
    shift_count = models.PositiveIntegerField()
    minutes = models.PositiveIntegerField()
    morning_minutes = models.PositiveIntegerField()
    evening_minutes = models.PositiveIntegerField()
    catering_minutes = models.PositiveIntegerField()

    class Meta:
        ordering = ["pay_run", "employee__display_name"]
        constraints = [
            models.UniqueConstraint(fields=["pay_run", "employee"], name="one_line_per_person_per_payment"),
        ]

    def __str__(self) -> str:
        return f"{self.employee} · {self.pay_run}"
