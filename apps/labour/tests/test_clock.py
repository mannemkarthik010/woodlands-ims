"""
Clocking in and out, as the owners describe the day (ADR 0008).

Each position has a morning and an evening. Most people work one; a few work
both. These tests use the dosa station's hours -- 10:00–15:00 and
17:00–22:00, starting at 9:00 on Saturdays -- which are made up for the test
and are not the restaurant's real hours.

22 September 2026 is a Tuesday; 26 September is a Saturday.
"""

from datetime import datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.core.models import Location, Position, Role, User
from apps.labour.models import Period, Shift, ShiftEdit, ShiftTemplate, Weekday
from apps.labour.services import (
    AlreadyClockedIn,
    ClockError,
    NotAllowed,
    NotClockedIn,
    clock_in,
    clock_out,
    correct_shift,
    hours_for_day,
    missed_clock_outs,
)

LA = ZoneInfo("America/Los_Angeles")


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """A moment in September 2026, Chatsworth time."""
    return datetime(2026, 9, day, hour, minute, tzinfo=LA)


class ClockTestCase(TestCase):
    def setUp(self):
        self.restaurant = Location.objects.create(
            code="restaurant", name="Restaurant", kind=Location.Kind.RESTAURANT
        )
        self.dosa = Position.objects.create(name="Dosa station")
        ShiftTemplate.objects.create(
            position=self.dosa, period=Period.MORNING, starts_at=time(10, 0), ends_at=time(15, 0)
        )
        ShiftTemplate.objects.create(
            position=self.dosa, period=Period.EVENING, starts_at=time(17, 0), ends_at=time(22, 0)
        )
        ShiftTemplate.objects.create(
            position=self.dosa,
            period=Period.MORNING,
            weekday=Weekday.SATURDAY,
            starts_at=time(9, 0),
            ends_at=time(15, 0),
        )
        self.ravi = User.objects.create_user("ravi", role=Role.KITCHEN, position=self.dosa)
        self.owner = User.objects.create_user("owner", role=Role.OWNER)

    def clock_in(self, when, user=None):
        return clock_in(user or self.ravi, location=self.restaurant, at=when)


class WhichShiftTests(ClockTestCase):
    # Covers: FR-101.
    def test_a_morning_clock_in_records_the_exact_time_and_the_morning_schedule(self):
        shift = self.clock_in(at(22, 10, 7))
        self.assertEqual(shift.clocked_in_at, at(22, 10, 7))
        self.assertEqual(shift.period, Period.MORNING)
        self.assertEqual((shift.scheduled_start, shift.scheduled_end), (time(10, 0), time(15, 0)))
        self.assertEqual(shift.position, self.dosa)
        self.assertEqual(str(shift.business_date), "2026-09-22")

    def test_arriving_after_the_closure_starts_the_evening(self):
        shift = self.clock_in(at(22, 16, 40))
        self.assertEqual(shift.period, Period.EVENING)
        self.assertEqual(shift.scheduled_start, time(17, 0))

    def test_the_day_splits_halfway_between_the_two_start_times(self):
        # 10:00 and 17:00 start times split the day at 13:30. A tie goes to
        # the later shift: somebody arriving then is early for the evening,
        # not three and a half hours late for the morning.
        cases = [(13, 29, Period.MORNING), (13, 30, Period.EVENING), (13, 31, Period.EVENING)]
        for hour, minute, expected in cases:
            with self.subTest(time=f"{hour}:{minute}"):
                shift = self.clock_in(at(22, hour, minute))
                self.assertEqual(shift.period, expected)
                Shift.objects.filter(pk=shift.pk).update(clocked_out_at=at(22, 23, 0))

    def test_a_weekday_override_replaces_the_every_day_hours_for_that_day_only(self):
        saturday = self.clock_in(at(26, 8, 55))
        self.assertEqual(saturday.scheduled_start, time(9, 0))
        clock_out(self.ravi, at=at(26, 15, 0))

        # The evening has no Saturday row, so the every-day evening applies.
        evening = self.clock_in(at(26, 16, 50))
        self.assertEqual((evening.period, evening.scheduled_start), (Period.EVENING, time(17, 0)))

    def test_a_position_with_no_hours_set_still_clocks_in_and_says_so(self):
        new_role = Position.objects.create(name="Tandoor")
        meera = User.objects.create_user("meera", role=Role.KITCHEN, position=new_role)
        morning = self.clock_in(at(22, 11, 0), user=meera)
        self.assertEqual(morning.period, Period.MORNING)
        self.assertFalse(morning.has_schedule)
        clock_out(meera, at=at(22, 14, 0))
        self.assertEqual(self.clock_in(at(22, 15, 0), user=meera).period, Period.EVENING)

    def test_the_schedule_is_copied_so_changing_the_hours_later_rewrites_nothing(self):
        shift = self.clock_in(at(22, 10, 20))
        ShiftTemplate.objects.filter(position=self.dosa, weekday__isnull=True, period=Period.MORNING).update(
            starts_at=time(11, 0)
        )
        shift.refresh_from_db()
        self.assertEqual(shift.scheduled_start, time(10, 0))


class ClockingTests(ClockTestCase):
    def test_a_second_tap_is_an_answer_not_a_second_shift(self):
        self.clock_in(at(22, 10, 0))
        with self.assertRaisesMessage(AlreadyClockedIn, "Already clocked in since 10:00 AM."):
            self.clock_in(at(22, 10, 1))
        self.assertEqual(Shift.objects.count(), 1)

    # Covers: FR-106.
    def test_a_double_shift_is_two_shifts_and_nothing_is_deducted(self):
        self.clock_in(at(22, 10, 0))
        morning = clock_out(self.ravi, at=at(22, 15, 0))
        self.clock_in(at(22, 17, 0))
        evening = clock_out(self.ravi, at=at(22, 22, 30))

        self.assertEqual((morning.period, evening.period), (Period.MORNING, Period.EVENING))
        self.assertEqual(morning.hours_worked, Decimal("5.00"))
        self.assertEqual(evening.hours_worked, Decimal("5.50"))
        self.assertEqual(hours_for_day(self.ravi, morning.business_date), Decimal("10.50"))

    def test_an_evening_that_runs_past_midnight_can_still_be_clocked_out(self):
        shift = self.clock_in(at(22, 17, 0))
        clock_out(self.ravi, at=at(23, 0, 30))
        shift.refresh_from_db()
        self.assertEqual(shift.hours_worked, Decimal("7.50"))
        self.assertEqual(str(shift.business_date), "2026-09-22")

    # Covers: FR-107.
    def test_a_missed_clock_out_is_flagged_and_never_blocks_the_next_day(self):
        forgotten = self.clock_in(at(22, 17, 0))

        today = self.clock_in(at(23, 10, 0))
        self.assertIn(forgotten, missed_clock_outs(at=at(23, 10, 0)))
        self.assertNotIn(today, missed_clock_outs(at=at(23, 10, 0)))

        # Clocking out closes today's shift. Yesterday's stays open for an
        # owner, because nobody knows when that person actually left.
        closed = clock_out(self.ravi, at=at(23, 15, 0))
        self.assertEqual(closed, today)
        forgotten.refresh_from_db()
        self.assertTrue(forgotten.is_open)
        self.assertIsNone(forgotten.hours_worked)

    def test_clocking_out_without_clocking_in_is_refused(self):
        with self.assertRaises(NotClockedIn):
            clock_out(self.ravi, at=at(22, 15, 0))

    def test_owners_and_people_who_have_left_are_not_on_the_clock(self):
        with self.assertRaises(NotAllowed):
            self.clock_in(at(22, 10, 0), user=self.owner)
        self.ravi.is_active_staff = False
        self.ravi.save()
        with self.assertRaises(NotAllowed):
            self.clock_in(at(22, 10, 0))

    # Covers: FR-108.
    def test_catering_work_is_marked_as_such(self):
        shift = clock_in(self.ravi, location=self.restaurant, at=at(22, 8, 0), is_catering_event=True)
        self.assertTrue(shift.is_catering_event)


class CorrectionTests(ClockTestCase):
    # Covers: FR-105, FR-113.
    def test_an_owner_closes_a_missed_clock_out_and_the_correction_is_kept(self):
        shift = self.clock_in(at(22, 10, 0))
        correct_shift(
            shift,
            by=self.owner,
            reason="Forgot to clock out; left at 3 per Jaspinder",
            clocked_out_at=at(22, 15, 0),
        )
        shift.refresh_from_db()
        self.assertEqual(shift.hours_worked, Decimal("5.00"))

        edit = ShiftEdit.objects.get(shift=shift)
        self.assertEqual(
            (edit.field_name, edit.old_value, edit.new_value), ("clocked_out_at", "", "2026-09-22 15:00")
        )
        self.assertEqual(edit.created_by, self.owner)
        self.assertEqual(edit.reason, "Forgot to clock out; left at 3 per Jaspinder")

    def test_only_an_owner_corrects_and_only_with_a_reason(self):
        shift = self.clock_in(at(22, 10, 0))
        with self.assertRaises(NotAllowed):
            correct_shift(shift, by=self.ravi, reason="mine", clocked_out_at=at(22, 15, 0))
        with self.assertRaises(ClockError):
            correct_shift(shift, by=self.owner, reason="   ", clocked_out_at=at(22, 15, 0))
        with self.assertRaises(ClockError):
            correct_shift(shift, by=self.owner, reason="typo", clocked_out_at=at(22, 9, 0))
        with self.assertRaises(ClockError):
            correct_shift(shift, by=self.owner, reason="typo", employee=self.owner)
        self.assertFalse(ShiftEdit.objects.exists())

    def test_setting_a_field_to_what_it_already_is_writes_nothing(self):
        shift = self.clock_in(at(22, 10, 0))
        correct_shift(shift, by=self.owner, reason="checking", period=Period.MORNING)
        self.assertFalse(ShiftEdit.objects.exists())

    def test_moving_a_clock_in_to_another_day_moves_the_shift_with_it(self):
        shift = self.clock_in(at(23, 10, 0))
        correct_shift(shift, by=self.owner, reason="Entered on the wrong day", clocked_in_at=at(22, 10, 0))
        shift.refresh_from_db()
        self.assertEqual(str(shift.business_date), "2026-09-22")

    # Covers: FR-113.
    def test_a_clock_record_cannot_be_deleted(self):
        shift = self.clock_in(at(22, 10, 0))
        with self.assertRaises(PermissionError):
            shift.delete()
        self.assertTrue(Shift.objects.filter(pk=shift.pk).exists())


class ScheduleIntegrityTests(ClockTestCase):
    def test_a_shift_must_end_after_it_starts(self):
        with transaction.atomic(), self.assertRaises(IntegrityError):
            ShiftTemplate.objects.create(
                position=self.dosa,
                period=Period.EVENING,
                weekday=Weekday.SUNDAY,
                starts_at=time(22, 0),
                ends_at=time(17, 0),
            )

    def test_a_position_has_one_every_day_morning(self):
        with transaction.atomic(), self.assertRaises(IntegrityError):
            ShiftTemplate.objects.create(
                position=self.dosa, period=Period.MORNING, starts_at=time(11, 0), ends_at=time(15, 0)
            )


class AdminTests(ClockTestCase):
    # Covers: FR-113.
    def test_the_admin_shows_shifts_but_cannot_add_change_or_delete_them(self):
        admin_user = User.objects.create_superuser("admin", password="pw")
        self.client.force_login(admin_user)
        shift = self.clock_in(at(22, 10, 0))

        self.assertEqual(self.client.get("/admin/labour/shift/").status_code, 200)
        self.assertEqual(self.client.get("/admin/labour/shift/add/").status_code, 403)
        self.assertEqual(self.client.post(f"/admin/labour/shift/{shift.pk}/delete/").status_code, 403)
        self.assertTrue(Shift.objects.filter(pk=shift.pk).exists())
