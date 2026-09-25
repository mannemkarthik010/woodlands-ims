"""
The owners correcting the record: setting the end of an unfinished shift,
fixing a typo, adding a shift somebody could not record, cancelling one that
should never have been written.

Everything an owner changes follows the same rules as a worker's entry, is
kept with a reason, and stays out of anything already paid.

Today, for these tests, is Wednesday 30 September 2026.
"""

from datetime import date, datetime, time
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Location, Role, User
from apps.labour.models import Shift, ShiftEdit, Source
from apps.labour.pay import pay, unpaid
from apps.labour.reports import hours_report
from apps.labour.services import (
    ClockError,
    NotAllowed,
    cancel_shift,
    clock_in,
    correct_shift,
    owner_add_shift,
    recent_days,
    record_shift,
    span,
)

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 9, 30, 23, 0, tzinfo=LA)


def sep(day):
    return date(2026, 9, day)


def at(day, hour, minute=0):
    return datetime(2026, 9, day, hour, minute, tzinfo=LA)


class OwnerFixTestCase(TestCase):
    def setUp(self):
        patcher = mock.patch.object(timezone, "now", return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.restaurant = Location.objects.create(code="r", name="Restaurant", kind=Location.Kind.RESTAURANT)
        self.owner = User.objects.create_user("owner", display_name="Jaspinder", role=Role.OWNER)
        self.ravi = User.objects.create_user("ravi", display_name="Ravi Kumar", role=Role.KITCHEN)

    def work(self, day, start=time(10, 0), end=time(15, 0)):
        return record_shift(
            self.ravi, day=sep(day), start=start, end=end, location=self.restaurant, now=at(day, 23, 30)
        )


class RulesTests(OwnerFixTestCase):
    # Covers: FR-105, FR-107.
    def test_setting_the_end_of_an_unfinished_shift_makes_it_count(self):
        open_shift = clock_in(self.ravi, location=self.restaurant, at=at(28, 10, 0))
        self.assertEqual(unpaid(sep(30)).open_shifts, 1)

        correct_shift(open_shift, by=self.owner, reason="Left at 3, per Suresh", clocked_out_at=at(28, 15, 0))

        preview = unpaid(sep(30))
        self.assertEqual((preview.open_shifts, preview.total), (0, 300))

    def test_a_correction_obeys_the_same_rules_as_any_shift(self):
        self.work(28)
        evening = self.work(28, time(17, 0), time(22, 0))
        start, end = span(sep(28), time(14, 0), time(22, 0))
        with self.assertRaisesMessage(ClockError, "overlaps"):
            correct_shift(evening, by=self.owner, reason="typo", clocked_in_at=start, clocked_out_at=end)
        start, end = span(sep(28), time(17, 0), time(10, 0))
        with self.assertRaisesMessage(ClockError, "17 hours"):
            correct_shift(evening, by=self.owner, reason="typo", clocked_in_at=start, clocked_out_at=end)
        # Tonight's shift, "corrected" to end after midnight -- which has not come yet.
        tonight = record_shift(
            self.ravi, day=sep(30), start=time(17, 0), end=time(22, 0), location=self.restaurant, now=NOW
        )
        with self.assertRaisesMessage(ClockError, "hasn't happened"):
            correct_shift(
                tonight, by=self.owner, reason="typo", clocked_out_at=datetime(2026, 10, 1, 0, 30, tzinfo=LA)
            )
        self.assertFalse(ShiftEdit.objects.exists())

    def test_a_shift_can_be_moved_without_clashing_with_itself(self):
        shift = self.work(28)
        start, end = span(sep(28), time(10, 30), time(15, 0))
        correct_shift(shift, by=self.owner, reason="Came in late", clocked_in_at=start, clocked_out_at=end)
        shift.refresh_from_db()
        self.assertEqual(shift.worked_minutes, 270)

    # Covers: FR-105.
    def test_an_owner_adds_a_shift_from_more_than_two_weeks_back(self):
        shift = owner_add_shift(
            self.ravi,
            day=sep(2),
            start=time(10, 0),
            end=time(15, 0),
            location=self.restaurant,
            by=self.owner,
            reason="Tablet was not set up yet",
        )
        self.assertEqual(
            (shift.source, shift.note, shift.created_by),
            (Source.OWNER, "Tablet was not set up yet", self.owner),
        )
        self.assertEqual(unpaid(sep(30)).total, 300)

    def test_adding_needs_a_reason_an_owner_and_the_same_rules(self):
        self.work(28)
        kw = {"day": sep(28), "start": time(14, 0), "end": time(18, 0), "location": self.restaurant}
        with self.assertRaisesMessage(ClockError, "say why"):
            owner_add_shift(self.ravi, by=self.owner, reason=" ", **kw)
        with self.assertRaises(NotAllowed):
            owner_add_shift(self.ravi, by=self.ravi, reason="me", **kw)
        with self.assertRaisesMessage(ClockError, "overlaps"):
            owner_add_shift(self.ravi, by=self.owner, reason="x", **kw)
        with self.assertRaisesMessage(ClockError, "hasn't happened"):
            owner_add_shift(
                self.ravi,
                by=self.owner,
                reason="x",
                day=date(2026, 10, 1),
                start=time(10),
                end=time(15),
                location=self.restaurant,
            )

    # Covers: FR-113.
    def test_a_cancelled_shift_stops_counting_everywhere_but_is_kept(self):
        mistake = self.work(28)
        self.work(29)
        cancel_shift(mistake, by=self.owner, reason="Entered for the wrong day")

        self.assertEqual(unpaid(sep(30)).total, 300)
        self.assertEqual(hours_report(sep(1), sep(30)).rows[0].total, 300)
        run = pay(sep(30), people=[self.ravi], by=self.owner)
        self.assertEqual(run.lines.get().shift_count, 1)

        kept = Shift.all_objects.get(pk=mistake.pk)
        self.assertEqual((kept.cancelled_by, kept.cancel_reason), (self.owner, "Entered for the wrong day"))
        self.assertEqual(kept.edits.get().field_name, "cancelled")
        # The same hours can now be written in correctly.
        owner_add_shift(
            self.ravi,
            day=sep(27),
            start=time(10),
            end=time(15),
            location=self.restaurant,
            by=self.owner,
            reason="The right day",
        )

    def test_a_cancelled_shift_no_longer_blocks_the_worker_writing_it_in(self):
        mistake = self.work(28)
        cancel_shift(mistake, by=self.owner, reason="Wrong times")
        self.work(28, time(10, 0), time(14, 0))

    def test_the_worker_sees_the_cancelled_shift_marked_and_not_counted(self):
        mistake = self.work(28)
        cancel_shift(mistake, by=self.owner, reason="Twice")
        day = next(d for d in recent_days(self.ravi, today=sep(30)) if d.day == sep(28))
        self.assertEqual((len(day.shifts), day.minutes, day.is_empty), (1, 0, True))

    def test_cancelling_needs_a_reason_and_cannot_touch_paid_or_cancelled_shifts(self):
        shift = self.work(28)
        with self.assertRaisesMessage(ClockError, "say why"):
            cancel_shift(shift, by=self.owner, reason="")
        with self.assertRaises(NotAllowed):
            cancel_shift(shift, by=self.ravi, reason="mine")
        pay(sep(30), people=[self.ravi], by=self.owner)
        with self.assertRaisesMessage(ClockError, "already paid"):
            cancel_shift(shift, by=self.owner, reason="x")

        other = self.work(29, time(17, 0), time(22, 0))
        Shift.objects.filter(pk=other.pk).update(pay_run=None)
        cancel_shift(other, by=self.owner, reason="x")
        with self.assertRaisesMessage(ClockError, "already cancelled"):
            cancel_shift(other, by=self.owner, reason="again")
        with self.assertRaisesMessage(ClockError, "cancelled"):
            correct_shift(other, by=self.owner, reason="x", period="MORNING")


class ScreenTests(OwnerFixTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def test_workers_cannot_open_the_owner_screens(self):
        shift = self.work(28)
        self.client.force_login(self.ravi)
        self.assertEqual(self.client.get(reverse("hours_shift", args=[shift.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("hours_shift_add", args=[self.ravi.pk])).status_code, 403)

    def test_to_pay_lists_unfinished_shifts_with_a_fix_button_and_the_fix_counts_them(self):
        open_shift = clock_in(self.ravi, location=self.restaurant, at=at(28, 10, 0))
        page = self.client.get(reverse("hours_pay"))
        self.assertContains(page, "Needs fixing")
        self.assertContains(page, reverse("hours_shift", args=[open_shift.pk]))

        back = f"{reverse('hours_pay')}?up_to=2026-09-30"
        response = self.client.post(
            reverse("hours_shift", args=[open_shift.pk]),
            {
                "day": "2026-09-28",
                "start": "10:00",
                "end": "15:00",
                "period": "MORNING",
                "reason": "Left at 3",
                "next": back,
            },
        )
        self.assertRedirects(response, back, fetch_redirect_response=False)
        self.assertEqual(unpaid(sep(30)).total, 300)
        self.assertContains(self.client.get(back), "corrected")

    def test_an_error_is_shown_on_the_form_and_nothing_changes(self):
        shift = self.work(28)
        page = self.client.post(
            reverse("hours_shift", args=[shift.pk]),
            {"day": "2026-09-28", "start": "17:00", "end": "10:00", "period": "EVENING", "reason": "typo"},
        )
        self.assertContains(page, "17 hours")
        shift.refresh_from_db()
        self.assertEqual(shift.worked_minutes, 300)

    def test_a_paid_shift_opens_read_only(self):
        shift = self.work(28)
        pay(sep(30), people=[self.ravi], by=self.owner)
        page = self.client.get(reverse("hours_shift", args=[shift.pk]))
        self.assertContains(page, "Paid hours are locked")
        self.assertNotContains(page, "Save correction")

    def test_add_a_shift_from_the_person_page(self):
        page = self.client.get(reverse("hours_person", args=[self.ravi.pk]), {"period": "this_month"})
        self.assertContains(page, reverse("hours_shift_add", args=[self.ravi.pk]))
        response = self.client.post(
            reverse("hours_shift_add", args=[self.ravi.pk]),
            {"day": "2026-09-03", "start": "10:00", "end": "15:30", "reason": "Paper timesheet"},
        )
        self.assertEqual(response.status_code, 302)
        shift = Shift.objects.get()
        self.assertEqual((shift.source, shift.hours_worked), (Source.OWNER, Decimal("5.50")))

    def test_cancel_from_the_shift_screen_and_it_shows_as_cancelled_on_the_person_page(self):
        shift = self.work(28)
        self.client.post(reverse("hours_shift_cancel", args=[shift.pk]), {"reason": "Entered twice"})
        page = self.client.get(reverse("hours_person", args=[self.ravi.pk]), {"period": "this_month"})
        self.assertContains(page, "Cancelled — not counted")
        self.assertContains(page, "Entered twice")
        self.assertContains(page, "No hours recorded in this period.")
