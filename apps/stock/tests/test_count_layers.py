"""
Counting by layer, the way the kitchen asked for it:

    Layer 2  what the kitchen makes -- batters, sambar, chutneys   daily
    Layer 1  vegetables                                            weekly
    Layer 1  dry groceries and packaging                           monthly
    Layer 3  dishes -- cooked to order, never counted; they come from sales

and counting in the kitchen's own measures -- buckets, bags, cases -- with
the count staying blind: the measure is remembered, the number never is.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import (
    CountEvery,
    Item,
    ItemCategory,
    ItemKind,
    ItemMeasure,
    MeasureKind,
    Unit,
    UnitKind,
)
from apps.core.models import Location, User
from apps.stock.models import DocumentStatus, StockCount
from apps.stock.services import StockError, build_count_sheet, post_count, record_counted


class LayerTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("tablet", password="pw")
        self.client.force_login(self.user)
        self.lb = Unit.objects.create(
            code="lb", name="Pound", kind=UnitKind.WEIGHT, to_canonical=Decimal("453.59237")
        )
        self.each = Unit.objects.create(
            code="each", name="Each", kind=UnitKind.COUNT, to_canonical=Decimal("1")
        )
        self.restaurant = Location.objects.create(code="r", name="Restaurant", kind=Location.Kind.RESTAURANT)
        veg = ItemCategory.objects.create(name="Fresh vegetables")

        def item(code, name, kind, **kw):
            return Item.objects.create(code=code, name=name, kind=kind, base_unit=self.lb, **kw)

        self.sambar = item("sambar", "Sambar", ItemKind.PREPARED)
        self.batter = item("dosa-batter", "Dosa batter", ItemKind.PREPARED)
        self.onion = item("onion", "Onions", ItemKind.RAW, category=veg)
        self.dal = item("toor", "Toor dal", ItemKind.RAW)
        self.trays = Item.objects.create(
            code="tray", name="Aluminium tray", kind=ItemKind.PACKAGING, base_unit=self.each
        )
        self.dish = Item.objects.create(
            code="masala-dosa", name="Masala dosa", kind=ItemKind.DISH, base_unit=self.each, is_stocked=False
        )
        self.bucket = ItemMeasure.objects.create(
            item=self.sambar, name="bucket", kind=MeasureKind.KITCHEN, quantity_in_base_units=Decimal("32")
        )
        self.bag = ItemMeasure.objects.create(
            item=self.dal, name="8 lb bag", quantity_in_base_units=Decimal("8")
        )

    def sheet(self, cadence):
        count = StockCount.objects.create(
            location=self.restaurant, cadence=cadence, counted_at=timezone.now(), created_by=self.user
        )
        build_count_sheet(count)
        return count

    def names(self, count):
        return sorted(count.lines.values_list("item__name", flat=True))


class WhatIsOnEachCountTests(LayerTestCase):
    # Covers: FR-701, FR-703.
    def test_every_item_starts_on_the_count_the_kitchen_described(self):
        got = {i.name: (i.layer, i.count_every) for i in Item.objects.all()}
        self.assertEqual(got["Sambar"], (2, CountEvery.DAILY))
        self.assertEqual(got["Onions"], (1, CountEvery.WEEKLY))
        self.assertEqual(got["Toor dal"], (1, CountEvery.MONTHLY))
        self.assertEqual(got["Aluminium tray"], (1, CountEvery.MONTHLY))
        self.assertEqual(got["Masala dosa"], (3, CountEvery.NEVER))

    def test_each_count_holds_only_its_own_items(self):
        self.assertEqual(self.names(self.sheet(StockCount.Cadence.DAILY)), ["Dosa batter", "Sambar"])
        self.assertEqual(self.names(self.sheet(StockCount.Cadence.WEEKLY)), ["Onions"])
        self.assertEqual(self.names(self.sheet(StockCount.Cadence.MONTHLY)), ["Aluminium tray", "Toor dal"])
        # Everything that is counted at all; never a dish.
        self.assertEqual(
            self.names(self.sheet(StockCount.Cadence.ADHOC)),
            ["Aluminium tray", "Dosa batter", "Onions", "Sambar", "Toor dal"],
        )

    def test_an_owner_can_move_an_item_to_another_count(self):
        self.dal.count_every = CountEvery.WEEKLY
        self.dal.save()
        self.assertEqual(self.names(self.sheet(StockCount.Cadence.WEEKLY)), ["Onions", "Toor dal"])


class KitchenUnitsTests(LayerTestCase):
    # Covers: FR-702, FR-204.
    def test_three_buckets_of_sambar_is_ninety_six_pounds_and_both_are_kept(self):
        line = self.sheet(StockCount.Cadence.DAILY).lines.get(item=self.sambar)
        record_counted(line, quantity=Decimal("3"), measure=self.bucket)
        line.refresh_from_db()
        self.assertEqual(
            (line.entered_quantity, line.entered_measure, line.counted_quantity),
            (Decimal("3"), self.bucket, Decimal("96")),
        )

    def test_counting_in_the_base_unit_and_clearing_a_line(self):
        line = self.sheet(StockCount.Cadence.DAILY).lines.get(item=self.sambar)
        record_counted(line, quantity=Decimal("40.5"))
        self.assertEqual(line.counted_quantity, Decimal("40.5"))
        record_counted(line, quantity=None, measure=self.bucket)
        line.refresh_from_db()
        self.assertEqual((line.counted_quantity, line.entered_measure), (None, None))

    def test_a_negative_count_or_another_item_s_measure_is_refused(self):
        line = self.sheet(StockCount.Cadence.DAILY).lines.get(item=self.sambar)
        with self.assertRaises(StockError):
            record_counted(line, quantity=Decimal("-1"))
        with self.assertRaises(StockError):
            record_counted(line, quantity=Decimal("2"), measure=self.bag)

    def test_counting_on_the_screen_in_bags(self):
        count = self.sheet(StockCount.Cadence.MONTHLY)
        line = count.lines.get(item=self.dal)
        response = self.client.post(
            reverse("count_save_line", args=[count.pk, line.pk]), {"counted": "12", "measure": self.bag.pk}
        )
        self.assertContains(response, "= 96 lb")
        line.refresh_from_db()
        self.assertEqual(line.counted_quantity, Decimal("96"))

    def test_the_measure_is_remembered_but_the_number_never_is(self):
        first = self.sheet(StockCount.Cadence.DAILY)
        record_counted(first.lines.get(item=self.sambar), quantity=Decimal("3"), measure=self.bucket)
        post_count(first, user=self.user)

        second = self.sheet(StockCount.Cadence.DAILY)
        page = self.client.get(reverse("count_sheet", args=[second.pk]))
        self.assertContains(page, f'<option value="{self.bucket.pk}" selected>bucket</option>', html=True)
        line = next(li for li in page.context["lines"] if li.item_id == self.sambar.pk)
        self.assertIsNone(line.entered_quantity)
        self.assertNotContains(page, "96")


class CountHomeTests(LayerTestCase):
    def cards(self):
        return {c.label: c for c in self.client.get(reverse("count_home")).context["cards"]}

    def test_every_count_is_due_before_anything_has_been_counted(self):
        cards = self.cards()
        self.assertEqual(
            [(c.label, c.items, c.due) for c in cards.values()],
            [("Daily", 2, True), ("Weekly", 1, True), ("Monthly", 2, True)],
        )

    def test_a_count_done_today_is_done_and_one_left_open_continues(self):
        daily = self.sheet(StockCount.Cadence.DAILY)
        post_count(daily, user=self.user)
        weekly = self.sheet(StockCount.Cadence.WEEKLY)
        cards = self.cards()
        self.assertFalse(cards["Daily"].due)
        self.assertEqual(cards["Weekly"].draft, weekly)
        self.assertContains(self.client.get(reverse("count_home")), reverse("count_sheet", args=[weekly.pk]))

    def test_yesterday_s_daily_is_due_again_and_last_week_s_weekly_too(self):
        for cadence, days_ago in ((StockCount.Cadence.DAILY, 1), (StockCount.Cadence.WEEKLY, 7)):
            count = self.sheet(cadence)
            post_count(count, user=self.user)
            StockCount.objects.filter(pk=count.pk).update(
                counted_at=timezone.now() - timedelta(days=days_ago)
            )
        cards = self.cards()
        self.assertTrue(cards["Daily"].due)
        self.assertTrue(cards["Weekly"].due)

    def test_starting_from_a_card_makes_that_count(self):
        response = self.client.post(
            reverse("count_new"), {"cadence": StockCount.Cadence.DAILY, "location": self.restaurant.pk}
        )
        count = StockCount.objects.get()
        self.assertRedirects(response, reverse("count_sheet", args=[count.pk]))
        self.assertEqual((count.cadence, count.status), (StockCount.Cadence.DAILY, DocumentStatus.DRAFT))
        self.assertEqual(self.names(count), ["Dosa batter", "Sambar"])
