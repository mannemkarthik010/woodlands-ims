"""
Paying out, the way the owners run it: they choose where a pay cycle ends.

The example the owners gave: hours are recorded from the 1st to the 30th;
they pay the 1st to the 15th; the next payment starts from where they have
not paid. These tests are that example, and the ways it goes wrong in real
life -- a shift written in late, a button pressed twice, a shift nobody
finished, a payment made by mistake.

Today, for these tests, is Wednesday 30 September 2026.
"""

from datetime import date, datetime, time
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Location, Role, User
from apps.labour.models import PayRun, PayRunLine, Shift
from apps.labour.pay import PayError, last_paid_up_to, pay, undo_payment, unpaid
from apps.labour.services import ClockError, NotAllowed, clock_in, correct_shift, merge_person, record_shift

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 9, 30, 23, 0, tzinfo=LA)


def sep(day):
    return date(2026, 9, day)


class PayTestCase(TestCase):
    def setUp(self):
        patcher = mock.patch.object(timezone, "now", return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.restaurant = Location.objects.create(code="r", name="Restaurant", kind=Location.Kind.RESTAURANT)
        self.owner = User.objects.create_user("owner", display_name="Jaspinder", role=Role.OWNER)
        self.ravi = User.objects.create_user("ravi", display_name="Ravi Kumar", role=Role.KITCHEN)
        self.meera = User.objects.create_user("meera", display_name="Meera Nair", role=Role.KITCHEN)

    def work(self, person, day, start=time(10, 0), end=time(15, 0), entered_on=None):
        """A five-hour morning, entered that evening unless `entered_on` says otherwise."""
        entered = datetime(2026, 9, entered_on or day, 23, 0, tzinfo=LA)
        return record_shift(person, day=sep(day), start=start, end=end, location=self.restaurant, now=entered)

    def month(self, person):
        for day in range(1, 31):
            self.work(person, day)


class PayCycleTests(PayTestCase):
    # Covers: FR-106.
    def test_pay_the_first_half_and_the_next_payment_starts_on_the_sixteenth(self):
        self.month(self.ravi)
        self.month(self.meera)

        run = pay(sep(15), people=[self.ravi, self.meera], by=self.owner)

        self.assertEqual(Shift.objects.filter(pay_run=run).count(), 30)  # 15 days x 2 people
        line = run.lines.get(employee=self.ravi)
        self.assertEqual(
            (line.first_day, line.last_day, line.shift_count, line.minutes), (sep(1), sep(15), 15, 4500)
        )

        rest = unpaid(sep(30))
        self.assertEqual(rest.first_day, sep(16))
        self.assertEqual([r.total for r in rest.rows], [15 * 300, 15 * 300])
        self.assertEqual(last_paid_up_to(self.ravi), sep(15))

    def test_a_shift_written_in_late_for_a_paid_fortnight_is_paid_next_time_not_lost(self):
        for day in range(1, 16):
            if day != 10:
                self.work(self.ravi, day)
        pay(sep(15), people=[self.ravi], by=self.owner)

        # Ravi remembers the 10th on the 20th.
        self.work(self.ravi, 10, entered_on=20)
        self.work(self.ravi, 16)

        row = unpaid(sep(30)).rows[0]
        self.assertEqual((row.first_day, row.total, row.from_paid_period), (sep(10), 600, 1))

        second = pay(sep(30), people=[self.ravi], by=self.owner)
        self.assertEqual(second.lines.get().shift_count, 2)
        self.assertFalse(Shift.objects.filter(pay_run__isnull=True).exists())

    def test_pressing_the_button_twice_pays_nothing_the_second_time(self):
        self.month(self.ravi)
        pay(sep(15), people=[self.ravi], by=self.owner)
        with self.assertRaisesMessage(PayError, "nothing unpaid"):
            pay(sep(15), people=[self.ravi], by=self.owner)
        self.assertEqual(PayRun.objects.count(), 1)

    def test_only_the_people_chosen_are_paid(self):
        self.month(self.ravi)
        self.month(self.meera)
        pay(sep(15), people=[self.ravi], by=self.owner)
        self.assertEqual([r.person for r in unpaid(sep(15)).rows], [self.meera])

    # Covers: FR-107.
    def test_a_shift_with_no_end_is_never_paid_and_waits_until_it_is_fixed(self):
        self.work(self.ravi, 1)
        open_shift = clock_in(self.ravi, location=self.restaurant, at=datetime(2026, 9, 2, 10, 0, tzinfo=LA))

        preview = unpaid(sep(15))
        self.assertEqual((preview.total, preview.open_shifts), (300, 1))
        run = pay(sep(15), people=[self.ravi], by=self.owner)
        self.assertEqual(run.lines.get().minutes, 300)

        correct_shift(
            open_shift,
            by=self.owner,
            reason="Left at 3",
            clocked_out_at=datetime(2026, 9, 2, 15, 0, tzinfo=LA),
        )
        row = unpaid(sep(30)).rows[0]
        self.assertEqual((row.total, row.from_paid_period), (300, 1))

    def test_somebody_new_from_the_tablet_is_not_paid_until_an_owner_has_looked(self):
        new = User.objects.create_user(
            "ravi-2", display_name="Ravi Kumaar", role=Role.KITCHEN, needs_review=True
        )
        self.work(new, 3)
        self.work(self.ravi, 3)
        with self.assertRaisesMessage(PayError, "confirm or merge Ravi Kumaar first"):
            pay(sep(15), people=[self.ravi, new], by=self.owner)
        # Everybody else can still be paid while they wait.
        pay(sep(15), people=[self.ravi], by=self.owner)
        self.assertEqual([r.person for r in unpaid(sep(15)).rows], [new])

    def test_the_future_cannot_be_paid_and_only_owners_pay(self):
        self.work(self.ravi, 1)
        with self.assertRaisesMessage(PayError, "days that have happened"):
            pay(date(2026, 10, 1), people=[self.ravi], by=self.owner)
        with self.assertRaises(NotAllowed):
            pay(sep(15), people=[self.ravi], by=self.meera)


class PaidIsFinalTests(PayTestCase):
    def setUp(self):
        super().setUp()
        self.shift = self.work(self.ravi, 5)
        self.run = pay(sep(15), people=[self.ravi], by=self.owner)

    def test_a_paid_shift_cannot_be_corrected_until_the_payment_is_undone(self):
        with self.assertRaisesMessage(ClockError, "already paid"):
            correct_shift(self.shift, by=self.owner, reason="typo", period="EVENING")

        undo_payment(self.run, by=self.owner, reason="Marked the wrong fortnight")
        correct_shift(self.shift, by=self.owner, reason="typo", period="EVENING")

    def test_undoing_a_payment_keeps_its_record_and_makes_the_hours_unpaid_again(self):
        with self.assertRaisesMessage(PayError, "say why"):
            undo_payment(self.run, by=self.owner, reason=" ")
        undo_payment(self.run, by=self.owner, reason="Marked the wrong fortnight")

        self.run.refresh_from_db()
        self.assertTrue(self.run.is_void)
        self.assertEqual(
            (self.run.voided_by, self.run.void_reason), (self.owner, "Marked the wrong fortnight")
        )
        self.assertEqual(PayRunLine.objects.get().minutes, 300)  # what it said, kept
        self.assertEqual(unpaid(sep(15)).total, 300)
        self.assertIsNone(last_paid_up_to(self.ravi))
        with self.assertRaisesMessage(PayError, "already undone"):
            undo_payment(self.run, by=self.owner, reason="again")

    def test_a_payment_is_never_deleted(self):
        with self.assertRaises(PermissionError):
            self.run.delete()

    def test_a_duplicate_holding_paid_hours_is_not_merged_until_the_payment_is_undone(self):
        with self.assertRaisesMessage(ClockError, "already paid"):
            merge_person(self.ravi, into=self.meera, by=self.owner)


class PayScreenTests(PayTestCase):
    def setUp(self):
        super().setUp()
        for day in (1, 2, 16):
            self.work(self.ravi, day)
        self.work(self.meera, 3)
        self.client.force_login(self.owner)

    def test_workers_cannot_see_or_pay(self):
        self.client.force_login(self.ravi)
        self.assertEqual(self.client.get(reverse("hours_pay")).status_code, 403)
        self.assertEqual(self.client.get(reverse("hours_payments")).status_code, 403)

    def test_choose_the_day_check_the_summary_then_mark_as_paid(self):
        page = self.client.get(reverse("hours_pay"), {"up_to": "2026-09-15"})
        self.assertContains(page, "Unpaid hours from <strong>Tue 1 Sep</strong>")
        self.assertContains(
            page, "10\u00a0h\u00a000\u00a0m"
        )  # Ravi's 1st and 2nd; the 16th is after the day chosen

        form = {"up_to": "2026-09-15", "person": [self.ravi.pk, self.meera.pk]}
        check = self.client.post(reverse("hours_pay"), {**form, "step": "review"})
        self.assertContains(check, "Mark as paid?")
        self.assertContains(check, "15\u00a0h\u00a000\u00a0m")
        self.assertFalse(PayRun.objects.exists())  # nothing until confirmed

        done = self.client.post(reverse("hours_pay"), {**form, "step": "pay", "note": "Cash"})
        run = PayRun.objects.get()
        self.assertRedirects(done, reverse("hours_payment", args=[run.pk]))
        self.assertEqual((run.paid_up_to, run.note, run.created_by), (sep(15), "Cash", self.owner))

        # Next time, the screen starts from what is left.
        self.assertContains(
            self.client.get(reverse("hours_pay")), "Unpaid hours from <strong>Wed 16 Sep</strong>"
        )

    def test_the_statement_and_the_spreadsheet_for_a_payment(self):
        run = pay(sep(15), people=[self.ravi], by=self.owner)
        statement = self.client.get(reverse("hours_pay_statement", args=[run.pk, self.ravi.pk]))
        self.assertContains(statement, "Pay statement — Ravi Kumar")
        self.assertContains(statement, "for work 1 Sep – 2 Sep 2026")
        self.assertContains(statement, "10\u00a0h\u00a000\u00a0m")
        csv = self.client.get(reverse("hours_payment", args=[run.pk]), {"format": "csv"}).content.decode()
        self.assertEqual(
            csv.splitlines()[1], "Ravi Kumar,2026-09-01,2026-09-02,2,10.00,0.00,0.00,10.00,10:00"
        )

    def test_payments_are_listed_newest_first(self):
        first = pay(sep(1), people=[self.ravi], by=self.owner)
        second = pay(sep(15), people=[self.ravi, self.meera], by=self.owner)
        runs = list(self.client.get(reverse("hours_payments")).context["runs"])
        self.assertEqual(runs, [second, first])
        self.assertEqual((runs[0].people, runs[0].minutes), (2, 600))

    def test_undo_from_the_payment_page(self):
        run = pay(sep(15), people=[self.ravi], by=self.owner)
        self.client.post(reverse("hours_payment_undo", args=[run.pk]), {"reason": "Wrong day"})
        run.refresh_from_db()
        self.assertTrue(run.is_void)
        self.assertContains(self.client.get(reverse("hours_payments")), "undone")
