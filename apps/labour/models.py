"""
Labour: clock in, clock out, and showing the owners the hours.

Deliberately narrow. This module does NOT calculate wages, apply overtime
rules, produce payslips or file anything -- the owners were explicit that
payroll is not wanted, and a time module that quietly grows into a payroll
engine is how a small project acquires a compliance obligation nobody asked
for.

The one piece of local knowledge encoded here: the restaurant closes between
3pm and 5pm and everyone breaks together. Asking each member of staff to clock
out and back in for that would add four taps a day per person to record
something the system already knows, so the closure is applied as a standard
deduction and anything unusual is recorded as an exception instead.
"""

from datetime import time
from decimal import Decimal

from django.db import models

from apps.core.models import Location, TimeStamped


class BreakPolicy(TimeStamped):
    """
    The standard closure. Configurable by weekday, because lunch service does
    not run every day and the closure may not either -- flagged to the owners
    for confirmation rather than assumed.
    """

    name = models.CharField(max_length=60, default="Standard 3–5pm closure")
    start_time = models.TimeField(default=time(15, 0))
    end_time = models.TimeField(default=time(17, 0))
    applies_monday = models.BooleanField(default=False)  # closed Mondays
    applies_tuesday = models.BooleanField(default=True)
    applies_wednesday = models.BooleanField(default=True)
    applies_thursday = models.BooleanField(default=True)
    applies_friday = models.BooleanField(default=True)
    applies_saturday = models.BooleanField(default=True)
    applies_sunday = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    @property
    def duration_hours(self) -> Decimal:
        start = Decimal(self.start_time.hour) + Decimal(self.start_time.minute) / 60
        end = Decimal(self.end_time.hour) + Decimal(self.end_time.minute) / 60
        return end - start

    def __str__(self) -> str:
        return self.name


class Shift(TimeStamped):
    employee = models.ForeignKey("core.User", on_delete=models.PROTECT, related_name="shifts")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="shifts")

    clocked_in_at = models.DateTimeField(db_index=True)
    clocked_out_at = models.DateTimeField(null=True, blank=True)

    # Clock-in photograph. Solves buddy-punching for essentially nothing, with
    # no specialist hardware and -- importantly in California -- no biometric
    # data on anybody's books.
    clock_in_photo = models.ImageField(upload_to="clockin/%Y/%m/", null=True, blank=True)

    break_policy = models.ForeignKey(
        BreakPolicy, null=True, blank=True, on_delete=models.PROTECT, related_name="shifts"
    )
    worked_through_break = models.BooleanField(
        default=False, help_text="Exception: the standard closure was not taken."
    )
    break_override_minutes = models.PositiveSmallIntegerField(null=True, blank=True)

    is_catering_event = models.BooleanField(default=False)
    note = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["-clocked_in_at"]
        indexes = [models.Index(fields=["employee", "clocked_in_at"])]

    def __str__(self) -> str:
        return f"{self.employee} {self.clocked_in_at:%d %b %H:%M}"

    @property
    def is_open(self) -> bool:
        return self.clocked_out_at is None

    @property
    def hours_worked(self):
        """Gross span minus the break that applies. None while still open."""
        if self.clocked_out_at is None:
            return None
        gross = Decimal((self.clocked_out_at - self.clocked_in_at).total_seconds()) / Decimal(3600)
        if self.break_override_minutes is not None:
            return gross - Decimal(self.break_override_minutes) / Decimal(60)
        if self.break_policy and not self.worked_through_break:
            return gross - self.break_policy.duration_hours
        return gross


class ShiftEdit(TimeStamped):
    """
    Every correction to a clock record, kept forever. A time record that can
    be changed without trace is not a record.
    """

    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name="edits")
    field_name = models.CharField(max_length=40)
    old_value = models.CharField(max_length=80, blank=True)
    new_value = models.CharField(max_length=80, blank=True)
    reason = models.CharField(max_length=240)

    class Meta:
        ordering = ["-created_at"]
