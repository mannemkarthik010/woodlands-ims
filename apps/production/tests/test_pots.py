"""
Cooked rice: the kitchen is told to cook the next pot before the last one runs out.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Item, ItemKind, ItemMeasure, MeasureKind, Unit, UnitKind
from apps.core.models import Location, Role, User
from apps.notify.models import Notification
from apps.production.models import BatchStatus, ProductionBatch
from apps.production.pots import pot_for

TZ = timezone.get_current_timezone()


def at(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=TZ)


# Wednesday 14 October 2026: dinner 5-10 pm. Saturday 17 October: lunch and dinner.
WED, SAT = (2026, 10, 14), (2026, 10, 17)


class PotTests(TestCase):
    def setUp(self):
        each = Unit.objects.create(code="each", name="Each", kind=UnitKind.COUNT, to_canonical=Decimal("1"))
        self.here = Location.objects.create(code="r", name="Restaurant", kind=Location.Kind.RESTAURANT)
        self.rice = Item.objects.create(
            code="rice",
            name="Cooked Rice",
            kind=ItemKind.PREPARED,
            base_unit=each,
            cook_minutes=30,
            pot_every_minutes=60,
        )
        ItemMeasure.objects.create(
            item=self.rice, name="pot", kind=MeasureKind.KITCHEN, quantity_in_base_units=Decimal("1")
        )
        self.n = 0

    def pot_at(self, moment):
        self.n += 1
        return ProductionBatch.objects.create(
            item=self.rice,
            batch_code=f"p{self.n}",
            location=self.here,
            started_at=moment,
            finished_at=moment,
            status=BatchStatus.AVAILABLE,
        )

    def test_the_first_pot_is_asked_for_before_opening(self):
        self.assertEqual(pot_for(self.rice, at(*WED, 16, 0)).status, "closed")
        first = pot_for(self.rice, at(*WED, 16, 35))
        self.assertEqual(first.status, "cook")
        self.assertIn("first pot", first.sentence())
        self.assertIn("5:00 pm", first.sentence())

    def test_it_asks_for_the_next_pot_a_cooking_time_before_the_last_runs_out(self):
        self.pot_at(at(*WED, 17, 0))
        # Lasts 60 min (the kitchen's guess), takes 30 to cook: start at 5:30.
        self.assertEqual(pot_for(self.rice, at(*WED, 17, 10)).status, "ok")
        self.assertEqual(pot_for(self.rice, at(*WED, 17, 22)).status, "soon")
        now = pot_for(self.rice, at(*WED, 17, 31))
        self.assertEqual(now.status, "cook")
        self.assertIn("runs out around 6:00 pm", now.sentence())
        self.assertIn("kitchen's estimate", now.basis())
        self.assertEqual(pot_for(self.rice, at(*WED, 18, 5)).status, "late")

    def test_a_pot_that_lasts_until_closing_needs_nothing_more(self):
        self.pot_at(at(*WED, 21, 30))
        self.assertEqual(pot_for(self.rice, at(*WED, 21, 45)).status, "enough")

    def test_it_learns_how_long_a_pot_lasts_from_the_pots_cooked(self):
        # Last week's Wednesdays: a pot every 40 minutes, not the guessed 60.
        for days_ago in (7, 14):
            day = at(*WED, 17, 0) - timedelta(days=days_ago)
            for k in range(3):
                self.pot_at(day + timedelta(minutes=40 * k))
        self.pot_at(at(*WED, 17, 0))
        pot = pot_for(self.rice, at(*WED, 17, 11))
        self.assertEqual(pot.lasts, timedelta(minutes=40))
        self.assertEqual(pot.status, "cook")  # 40 - 30 = start by 5:10
        self.assertIn("from the last 4 pots", pot.basis())

    def test_a_busy_saturday_is_compared_with_saturdays(self):
        for days_ago in (7, 14):  # Saturdays: every 30 min
            day = at(*SAT, 17, 0) - timedelta(days=days_ago)
            for k in range(3):
                self.pot_at(day + timedelta(minutes=30 * k))
        for days_ago in (3, 10):  # Wednesdays: every 90 min
            day = at(*SAT, 17, 0) - timedelta(days=days_ago)
            for k in range(3):
                self.pot_at(day + timedelta(minutes=90 * k))
        self.pot_at(at(*SAT, 17, 0))
        self.assertEqual(pot_for(self.rice, at(*SAT, 17, 1)).lasts, timedelta(minutes=30))

    def test_the_gap_between_lunch_and_dinner_is_not_a_pot_lasting(self):
        self.pot_at(at(*SAT, 14, 30))
        self.pot_at(at(*SAT, 17, 0))
        from apps.production.pots import learned_gaps

        self.assertEqual(learned_gaps(self.rice, at(*SAT, 18, 0)), [])

    def test_one_tap_records_the_pot_and_the_banner_tells_the_owners_once(self):
        owner = User.objects.create_user("pj", role=Role.OWNER)
        tablet = User.objects.create_user("kitchen", role=Role.KITCHEN, is_shared_login=True)
        self.client.force_login(tablet)
        with patch("apps.production.pots.timezone.now", return_value=at(*WED, 17, 40)):
            done = self.client.post(
                reverse("pot_cooked", args=[self.rice.pk]), {"next": "https://evil.example/"}
            )
        self.assertEqual(done["Location"], reverse("made_today"))  # never sent off the site
        self.assertEqual(ProductionBatch.objects.filter(item=self.rice).count(), 1)
        pot_time = ProductionBatch.objects.get().finished_at
        with patch("apps.production.pots.timezone.now", return_value=pot_time + timedelta(minutes=35)):
            page = self.client.get(reverse("pots_banner"))
            self.client.get(reverse("pots_banner"))
        self.assertContains(page, "Cook rice now")
        self.assertContains(page, "Cooked a fresh pot")
        self.assertEqual(Notification.objects.filter(recipient=owner).count(), 1)

    def test_the_banner_is_empty_when_nothing_needs_cooking(self):
        self.client.force_login(User.objects.create_user("kitchen", role=Role.KITCHEN))
        with patch("apps.production.pots.timezone.now", return_value=at(*WED, 12, 0)):
            page = self.client.get(reverse("pots_banner"))
        self.assertNotContains(page, "pot")
