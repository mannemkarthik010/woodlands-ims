"""
The run-out watch: on hand, used per day, days left, and what to do about it.

Today, for these tests, is Wednesday 14 October 2026.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.utils import timezone

from apps.catalog.models import Item, ItemKind, ParLevel, Unit, UnitKind
from apps.core.models import Location
from apps.stock import runout
from apps.stock.models import MovementType
from apps.stock.services import post_movement

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=LA)
TODAY = date(2026, 10, 14)


class RunoutTests(TestCase):
    def setUp(self):
        patcher = mock.patch.object(timezone, "now", return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.here = Location.objects.create(code="r", name="Restaurant", kind=Location.Kind.RESTAURANT)
        lb = Unit.objects.create(
            code="lb", name="Pound", kind=UnitKind.WEIGHT, to_canonical=Decimal("453.59237")
        )
        self.batter = Item.objects.create(
            code="b", name="Dosa Batter", kind=ItemKind.PREPARED, base_unit=lb, lead_days=1
        )
        self.rice = Item.objects.create(
            code="r", name="Basmati Rice", kind=ItemKind.RAW, base_unit=lb, lead_days=2
        )
        Item.objects.create(code="x", name="Not watched", kind=ItemKind.RAW, base_unit=lb)

    def move(self, item, qty, kind, days_ago=1):
        post_movement(
            item=item,
            location=self.here,
            quantity=Decimal(str(qty)),
            movement_type=kind,
            occurred_at=NOW - timedelta(days=days_ago),
        )

    def watch(self, item):
        return next(w for w in runout.watched(TODAY) if w.item == item)

    def test_only_items_with_a_lead_time_are_watched(self):
        self.assertEqual({w.item.name for w in runout.watched(TODAY)}, {"Dosa Batter", "Basmati Rice"})

    def test_usage_is_the_average_over_the_open_days_and_says_when_to_make_more(self):
        self.move(self.batter, 120, MovementType.PRODUCTION_YIELD, days_ago=13)
        # Tracked from 13 days ago to yesterday: 11 open days (two Mondays closed).
        self.move(self.batter, -55, MovementType.SALE_DEPLETION, days_ago=2)
        w = self.watch(self.batter)
        self.assertEqual(w.on_hand, Decimal("65"))
        self.assertEqual(w.per_day, Decimal("5.00"))
        self.assertEqual(w.open_days, 11)
        self.assertEqual(w.status, "ok")
        self.assertEqual(w.advice, "Enough for about 13 days")

    def test_what_a_count_found_missing_counts_as_use(self):
        self.move(self.batter, 40, MovementType.PRODUCTION_YIELD, days_ago=13)
        self.move(self.batter, -22, MovementType.SALE_DEPLETION, days_ago=3)
        self.move(self.batter, -5.5, MovementType.COUNT_ADJUSTMENT, days_ago=1)  # 5.5 lb nobody recorded
        w = self.watch(self.batter)
        self.assertEqual(w.per_day, Decimal("2.50"))  # 27.5 lb over 11 open days
        self.assertEqual(w.on_hand, Decimal("12.5"))
        self.assertEqual(w.status, "ok")

    def test_less_than_the_lead_time_left_means_make_today(self):
        self.move(self.batter, 30, MovementType.PRODUCTION_YIELD, days_ago=13)
        self.move(self.batter, -24, MovementType.SALE_DEPLETION, days_ago=2)  # 2 lb a day, 6 left
        self.move(self.batter, -5, MovementType.SALE_DEPLETION, days_ago=1)
        w = self.watch(self.batter)
        self.assertEqual(w.status, "now")
        self.assertEqual(w.advice, "Make today")

    def test_tomorrow_when_one_more_day_would_cross_the_lead_time(self):
        self.move(self.rice, 100, MovementType.RECEIPT, days_ago=13)
        self.move(self.rice, -80, MovementType.COUNT_ADJUSTMENT, days_ago=1)  # 7.27 lb a day, 20 left
        w = self.watch(self.rice)
        self.assertEqual(w.status, "tomorrow")  # 2.75 days left, lead 2
        self.assertEqual(w.advice, "Order tomorrow")

    def test_below_its_make_more_level_is_now_whatever_the_rate(self):
        ParLevel.objects.create(item=self.batter, location=self.here, quantity=Decimal("31"))
        self.move(self.batter, 30, MovementType.PRODUCTION_YIELD, days_ago=1)
        self.assertEqual(self.watch(self.batter).status, "now")

    def test_with_no_history_it_says_so_rather_than_guessing(self):
        self.move(self.rice, 40, MovementType.RECEIPT, days_ago=1)
        w = self.watch(self.rice)
        self.assertIsNone(w.per_day)
        self.assertEqual(w.status, "unknown")
        self.assertIn("Not enough history", w.advice)

    def test_what_is_left_is_also_said_in_buckets(self):
        from apps.catalog.models import ItemMeasure, MeasureKind

        ItemMeasure.objects.create(
            item=self.batter, name="bucket", kind=MeasureKind.KITCHEN, quantity_in_base_units=Decimal("30.86")
        )
        self.move(self.batter, 46.3, MovementType.PRODUCTION_YIELD, days_ago=2)
        self.assertEqual(self.watch(self.batter).in_containers, "1.5 buckets")

    def test_the_opening_count_is_not_usage(self):
        """Going from nothing recorded to 2 buckets on the first count sets the stock; it isn't negative use."""
        self.move(
            self.batter, 60, MovementType.COUNT_ADJUSTMENT, days_ago=3
        )  # Sunday: first count, opening stock
        self.move(self.batter, -20, MovementType.SALE_DEPLETION, days_ago=1)
        w = self.watch(self.batter)
        self.assertEqual(w.on_hand, Decimal("40"))
        self.assertEqual(w.open_days, 2)  # Sunday and Tuesday; Monday is closed
        self.assertEqual(w.per_day, Decimal("10.00"))
        self.assertEqual(w.status, "ok")  # 4 days left, lead 1

    def test_never_counted_is_not_out_it_is_unknown(self):
        w = self.watch(self.batter)
        self.assertEqual(w.status, "uncounted")
        self.assertEqual(w.advice, "Not counted yet — count it tonight")

    def test_none_left_is_out_and_comes_first(self):
        self.move(self.batter, 10, MovementType.PRODUCTION_YIELD, days_ago=5)
        self.move(self.batter, -10, MovementType.SALE_DEPLETION, days_ago=1)
        self.assertEqual(runout.watched(TODAY)[0].status, "out")
