"""
The hours the owners pay out on: every shift a person recorded, however many
entries it took, added into one total per person.
"""

from datetime import date, time
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.core.models import Location, Role, User
from apps.labour.models import Shift
from apps.labour.reports import hours_report, period_dates, person_hours
from apps.labour.services import ClockError, NotAllowed, clock_in, confirm_person, merge_person, record_shift
from apps.labour.tests.test_clock import at

NOW = at(27, 23, 0)  # Sunday 27 September 2026, late


class ReportTestCase(TestCase):
    def setUp(self):
        self.restaurant = Location.objects.create(code="r", name="Restaurant", kind=Location.Kind.RESTAURANT)
        self.owner = User.objects.create_user("owner", display_name="Jaspinder", role=Role.OWNER)
        self.ravi = User.objects.create_user("ravi", display_name="Ravi Kumar", role=Role.KITCHEN)
        self.meera = User.objects.create_user("meera", display_name="Meera Nair", role=Role.KITCHEN)

    def shift(self, person, day, start, end, **kw):
        return record_shift(
            person, day=date(2026, 9, day), start=start, end=end, location=self.restaurant, now=NOW, **kw
        )


class ReportTotalsTests(ReportTestCase):
    # Covers: FR-106.
    def test_every_entry_adds_into_one_total_per_person(self):
        self.shift(self.ravi, 21, time(10, 0), time(15, 0))  # 5:00 morning
        self.shift(self.ravi, 21, time(17, 0), time(22, 20))  # 5:20 evening, a double
        self.shift(self.ravi, 23, time(10, 10), time(14, 55))  # 4:45
        self.shift(self.ravi, 25, time(8, 0), time(12, 0), is_catering_event=True)  # 4:00 catering
        self.shift(self.meera, 22, time(17, 0), time(0, 30))  # 7:30 past midnight

        report = hours_report(date(2026, 9, 21), date(2026, 9, 27))
        meera, ravi = report.rows  # alphabetical
        self.assertEqual(ravi.person, self.ravi)
        self.assertEqual(ravi.total, 5 * 60 + 5 * 60 + 20 + 4 * 60 + 45 + 4 * 60)
        self.assertEqual(ravi.total_hours, Decimal("19.08"))
        self.assertEqual(
            (ravi.morning, ravi.evening, ravi.catering), (5 * 60 + 4 * 60 + 45 + 4 * 60, 320, 240)
        )
        self.assertEqual(ravi.days, 3)
        self.assertEqual(meera.total, 450)
        self.assertEqual(report.total, ravi.total + meera.total)

    def test_totals_come_from_minutes_so_rounding_never_drifts(self):
        # Three shifts of 20 minutes are 0.33 h each when rounded, 0.99 h if
        # rounded first and added. The answer is exactly one hour.
        for start in (time(9, 0), time(10, 0), time(11, 0)):
            self.shift(self.ravi, 22, start, time(start.hour, 20))
        row = hours_report(date(2026, 9, 22), date(2026, 9, 22)).rows[0]
        self.assertEqual(row.total_hours, Decimal("1.00"))

    # Covers: FR-107.
    def test_an_unfinished_shift_is_flagged_and_not_counted(self):
        self.shift(self.ravi, 22, time(10, 0), time(15, 0))
        clock_in(self.ravi, location=self.restaurant, at=at(23, 10, 0))
        row = hours_report(date(2026, 9, 21), date(2026, 9, 27)).rows[0]
        self.assertEqual((row.total, row.open_shifts, row.days), (300, 1, 1))
        self.assertTrue(row.needs_attention)

    def test_a_shift_written_down_days_later_is_flagged(self):
        shift = self.shift(self.ravi, 21, time(10, 0), time(15, 0))
        Shift.objects.filter(pk=shift.pk).update(created_at=at(24, 9, 0))
        self.assertEqual(hours_report(date(2026, 9, 21), date(2026, 9, 27)).rows[0].late_entries, 1)

    def test_only_the_period_asked_for_is_counted(self):
        self.shift(self.ravi, 20, time(10, 0), time(15, 0))  # Sunday before
        self.shift(self.ravi, 21, time(10, 0), time(15, 0))
        self.assertEqual(hours_report(date(2026, 9, 21), date(2026, 9, 27)).rows[0].total, 300)

    def test_one_person_s_statement_matches_their_line_in_the_report(self):
        self.shift(self.ravi, 21, time(10, 0), time(15, 0))
        self.shift(self.ravi, 22, time(17, 0), time(23, 0))
        mine = person_hours(self.ravi, date(2026, 9, 21), date(2026, 9, 27))
        line = hours_report(date(2026, 9, 21), date(2026, 9, 27)).rows[0]
        self.assertEqual((mine.total, mine.morning, mine.evening), (line.total, line.morning, line.evening))


class PeriodTests(TestCase):
    def test_named_periods_with_the_week_starting_on_monday(self):
        thursday = date(2026, 9, 24)
        self.assertEqual(period_dates("this_week", thursday), (date(2026, 9, 21), thursday))
        self.assertEqual(period_dates("last_week", thursday), (date(2026, 9, 14), date(2026, 9, 20)))
        self.assertEqual(period_dates("last_2_weeks", thursday), (date(2026, 9, 7), date(2026, 9, 20)))
        self.assertEqual(period_dates("this_month", thursday), (date(2026, 9, 1), thursday))
        self.assertEqual(period_dates("last_month", thursday), (date(2026, 8, 1), date(2026, 8, 31)))
        monday = date(2026, 9, 21)
        self.assertEqual(period_dates("this_week", monday), (monday, monday))
        self.assertEqual(
            period_dates("last_month", date(2026, 1, 15)), (date(2025, 12, 1), date(2025, 12, 31))
        )


class MergeTests(ReportTestCase):
    def setUp(self):
        super().setUp()
        self.dup = User.objects.create_user(
            "ravi-2", display_name="Ravi Kumaar", role=Role.KITCHEN, needs_review=True
        )

    # Covers: FR-105.
    def test_merging_a_duplicate_moves_every_shift_and_the_total_is_whole_again(self):
        self.shift(self.ravi, 21, time(10, 0), time(15, 0))
        self.shift(self.dup, 22, time(10, 0), time(15, 0))
        self.shift(self.dup, 23, time(17, 0), time(22, 0))

        self.assertEqual(merge_person(self.dup, into=self.ravi, by=self.owner), 2)

        rows = hours_report(date(2026, 9, 21), date(2026, 9, 27)).rows
        self.assertEqual([(r.person, r.total) for r in rows], [(self.ravi, 900)])
        moved = Shift.objects.filter(employee=self.ravi, business_date=date(2026, 9, 22)).get()
        edit = moved.edits.get()
        self.assertEqual(
            (edit.field_name, edit.old_value, edit.new_value), ("employee", "Ravi Kumaar", "Ravi Kumar")
        )
        self.assertEqual(edit.created_by, self.owner)

        self.dup.refresh_from_db()
        self.assertFalse(self.dup.is_active_staff)
        self.assertEqual(self.dup.pin, "")
        self.assertTrue(User.objects.filter(pk=self.dup.pk).exists())  # switched off, not deleted

    def test_overlapping_hours_are_not_added_together(self):
        self.shift(self.ravi, 22, time(10, 0), time(15, 0))
        self.shift(self.dup, 22, time(10, 5), time(15, 0))  # the same shift, recorded twice
        with self.assertRaisesMessage(ClockError, "Correct one of them first"):
            merge_person(self.dup, into=self.ravi, by=self.owner)
        self.assertEqual(Shift.objects.filter(employee=self.dup).count(), 1)

    def test_only_an_owner_merges_or_confirms(self):
        with self.assertRaises(NotAllowed):
            merge_person(self.dup, into=self.ravi, by=self.meera)
        with self.assertRaises(NotAllowed):
            confirm_person(self.dup, by=self.meera)
        confirm_person(self.dup, by=self.owner)
        self.dup.refresh_from_db()
        self.assertFalse(self.dup.needs_review)


class ReportScreenTests(ReportTestCase):
    def setUp(self):
        super().setUp()
        self.shift(self.ravi, 21, time(10, 0), time(15, 30))

    def get(self, name, *args, user=None, **params):
        self.client.force_login(user or self.owner)
        return self.client.get(
            reverse(name, args=args), {"period": "custom", "from": "2026-09-21", "to": "2026-09-27", **params}
        )

    def test_only_owners_see_hours(self):
        self.assertEqual(self.get("hours_report", user=self.ravi).status_code, 403)
        self.assertEqual(self.get("hours_person", self.meera.pk, user=self.ravi).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("hours_report")).status_code, 302)

    # Covers: FR-112.
    def test_the_report_shows_the_total_and_downloads_as_a_spreadsheet(self):
        page = self.get("hours_report")
        self.assertContains(page, "Ravi Kumar")
        self.assertContains(page, "5 h 30 m")
        csv = self.get("hours_report", format="csv")
        self.assertEqual(csv["Content-Type"], "text/csv; charset=utf-8")
        lines = csv.content.decode().splitlines()
        self.assertEqual(lines[1], "Ravi Kumar,,1,5.50,0.00,0.00,5.50,5:30,0")

    def test_a_worker_s_statement_lists_each_shift(self):
        page = self.get("hours_person", self.ravi.pk)
        self.assertContains(page, "Hours — Ravi Kumar")
        self.assertContains(page, "10:00 AM")
        self.assertContains(page, "Print or save as PDF")
        csv = self.get("hours_person", self.ravi.pk, format="csv").content.decode().splitlines()
        self.assertEqual(csv[1], "2026-09-21,Morning,10:00,15:30,5.50,,Entered by the worker,")

    def test_a_new_person_is_shown_for_review_and_can_be_merged_from_the_report(self):
        dup = User.objects.create_user(
            "ravi-2",
            display_name="Ravi Kumaar",
            role=Role.KITCHEN,
            needs_review=True,
            possible_duplicate_of=self.ravi,
        )
        dup.pin = "x"
        dup.save()
        page = self.get("hours_report")
        self.assertContains(page, "New on the tablet")
        self.assertContains(page, "Said they are not <strong>Ravi Kumar</strong>")

        response = self.client.post(reverse("hours_merge", args=[dup.pk]), {"into": self.ravi.pk, "back": ""})
        self.assertRedirects(response, f"{reverse('hours_report')}?")
        dup.refresh_from_db()
        self.assertFalse(dup.is_active_staff)
