"""
A worker writing in their own hours after the shift.

The owners want to start here: date, when you started, when you left. The
rules below exist because the person typing is tired, at the end of a
shift, on a tablet -- and because the numbers end up in somebody's pay.

"Now" in these tests is Thursday 24 September 2026, 10:30pm.
"""

from datetime import date, time
from decimal import Decimal

from apps.labour.models import Period, Shift, Source
from apps.labour.services import ClockError, clock_in, recent_days, record_shift
from apps.labour.tests.test_clock import ClockTestCase, at

NOW = at(24, 22, 30)


class RecordShiftTests(ClockTestCase):
    def record(self, day, start, end, **kw):
        kw.setdefault("now", NOW)
        return record_shift(self.ravi, day=day, start=start, end=end, location=self.restaurant, **kw)

    # Covers: FR-101.
    def test_a_shift_is_recorded_with_its_date_and_times_and_marked_as_entered(self):
        shift = self.record(date(2026, 9, 24), time(10, 0), time(15, 0))
        self.assertEqual((shift.clocked_in_at, shift.clocked_out_at), (at(24, 10, 0), at(24, 15, 0)))
        self.assertEqual(shift.hours_worked, Decimal("5.00"))
        self.assertEqual(shift.source, Source.ENTERED)
        self.assertEqual(shift.created_by, self.ravi)

    def test_morning_or_evening_is_still_worked_out_from_the_start_time(self):
        morning = self.record(date(2026, 9, 23), time(10, 0), time(15, 0))
        evening = self.record(date(2026, 9, 23), time(17, 0), time(22, 0))
        self.assertEqual((morning.period, evening.period), (Period.MORNING, Period.EVENING))
        self.assertEqual(evening.scheduled_start, time(17, 0))

    def test_a_forgotten_day_can_be_filled_in_later_and_it_shows(self):
        shift = self.record(date(2026, 9, 20), time(10, 0), time(15, 0))
        # `created_at` comes from the real clock, so pin it to the test's "now".
        Shift.objects.filter(pk=shift.pk).update(created_at=NOW)
        shift.refresh_from_db()
        self.assertEqual(shift.days_late_entered, 4)

    def test_an_end_time_before_the_start_means_past_midnight(self):
        shift = self.record(date(2026, 9, 22), time(17, 0), time(0, 30))
        self.assertEqual(shift.clocked_out_at, at(23, 0, 30))
        self.assertEqual(shift.hours_worked, Decimal("7.50"))
        self.assertEqual(str(shift.business_date), "2026-09-22")

    def test_an_impossible_length_is_refused_as_a_typo(self):
        # 5pm to 10am the next day: meant 10pm.
        with self.assertRaisesMessage(ClockError, "That is 17 hours"):
            self.record(date(2026, 9, 22), time(17, 0), time(10, 0))

    def test_the_future_is_refused(self):
        with self.assertRaisesMessage(ClockError, "hasn't happened yet"):
            self.record(date(2026, 9, 25), time(10, 0), time(15, 0))
        # Today, but the end time is still to come.
        with self.assertRaisesMessage(ClockError, "end time hasn't happened"):
            self.record(date(2026, 9, 24), time(17, 0), time(23, 30))

    def test_filling_in_as_you_walk_out_is_allowed_a_few_minutes_early(self):
        shift = self.record(date(2026, 9, 24), time(17, 0), time(22, 40))
        self.assertEqual(shift.clocked_out_at, at(24, 22, 40))

    def test_more_than_two_weeks_back_goes_to_an_owner(self):
        self.record(date(2026, 9, 11), time(10, 0), time(15, 0))  # 13 days back: fine
        with self.assertRaisesMessage(ClockError, "ask an owner"):
            self.record(date(2026, 9, 10), time(10, 0), time(15, 0))

    def test_a_shift_cannot_overlap_one_already_recorded(self):
        self.record(date(2026, 9, 23), time(10, 0), time(15, 0))
        with self.assertRaisesMessage(ClockError, "overlaps"):
            self.record(date(2026, 9, 23), time(14, 0), time(18, 0))
        # Back to back is not an overlap.
        self.record(date(2026, 9, 23), time(15, 0), time(16, 0))

    def test_an_entry_cannot_overlap_a_live_clock_in_either(self):
        clock_in(self.ravi, location=self.restaurant, at=at(23, 17, 0))
        with self.assertRaisesMessage(ClockError, "not finished"):
            self.record(date(2026, 9, 23), time(16, 0), time(22, 0))

    def test_checking_without_saving_writes_nothing(self):
        shift = self.record(date(2026, 9, 24), time(10, 0), time(15, 0), save=False)
        self.assertIsNone(shift.pk)
        self.assertEqual(shift.hours_worked, Decimal("5.00"))
        self.assertFalse(Shift.objects.exists())


class RecentDaysTests(ClockTestCase):
    # Covers: FR-109.
    def test_the_last_seven_days_include_the_empty_ones(self):
        record_shift(
            self.ravi,
            day=date(2026, 9, 24),
            start=time(10, 0),
            end=time(15, 0),
            location=self.restaurant,
            now=NOW,
        )
        record_shift(
            self.ravi,
            day=date(2026, 9, 24),
            start=time(17, 0),
            end=time(22, 0),
            location=self.restaurant,
            now=NOW,
        )
        days = recent_days(self.ravi, today=date(2026, 9, 24))

        self.assertEqual([d.day.day for d in days], [24, 23, 22, 21, 20, 19, 18])
        self.assertEqual(days[0].hours, Decimal("10.00"))
        self.assertEqual(len(days[0].shifts), 2)
        self.assertEqual(sum(d.is_empty for d in days), 6)

    def test_somebody_else_s_hours_are_not_shown(self):
        meera = type(self.ravi).objects.create_user("meera", role=self.ravi.role, position=self.dosa)
        record_shift(
            meera,
            day=date(2026, 9, 24),
            start=time(10, 0),
            end=time(15, 0),
            location=self.restaurant,
            now=NOW,
        )
        self.assertTrue(all(d.is_empty for d in recent_days(self.ravi, today=date(2026, 9, 24))))
