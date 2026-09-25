"""
Stock counting.

The behaviour worth protecting here is what the system does with a blank --
it must mean "not counted", never "zero". Treating an unvisited shelf as
empty would write off stock that is sitting there, and it is the single most
destructive thing a counting system can get wrong.
"""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.catalog.models import Item, ItemKind, Unit, UnitKind
from apps.core.models import Location, User
from apps.stock.models import MovementType, StockCount
from apps.stock.services import build_count_sheet, on_hand, post_count, post_movement


class CountTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("karthik", password="pw")
        self.client.force_login(self.user)

        lb = Unit.objects.create(
            code="lb", name="Pound", kind=UnitKind.WEIGHT, to_canonical=Decimal("453.59237")
        )
        self.store = Location.objects.create(
            code="devonshire", name="Devonshire Street unit", kind=Location.Kind.STORAGE
        )
        self.dal = Item.objects.create(code="dal", name="Urad dal", kind=ItemKind.RAW, base_unit=lb)
        self.rice = Item.objects.create(code="rice", name="Idli rice", kind=ItemKind.RAW, base_unit=lb)

        for item, qty in ((self.dal, "150"), (self.rice, "240")):
            post_movement(
                item=item,
                location=self.store,
                quantity=Decimal(qty),
                movement_type=MovementType.OPENING_BALANCE,
            )

    def _sheet(self):
        # Dal and rice are dry groceries, so they are on the monthly count.
        count = StockCount.objects.create(
            location=self.store,
            cadence=StockCount.Cadence.MONTHLY,
            counted_at="2026-09-16T22:00:00Z",
            created_by=self.user,
        )
        build_count_sheet(count)
        return count

    # Covers: FR-702, FR-706.
    def test_a_blank_line_means_not_counted_not_zero(self):
        """The one that matters. An unvisited shelf must not be written off."""
        count = self._sheet()
        line = count.lines.get(item=self.dal)
        line.counted_quantity = Decimal("132")
        line.save()
        # rice deliberately left blank

        post_count(count, user=self.user)

        self.assertEqual(on_hand(self.dal, self.store), Decimal("132"))
        self.assertEqual(on_hand(self.rice, self.store), Decimal("240"))  # untouched

    # Covers: FR-706, FR-1203.
    def test_a_count_writes_the_difference_not_the_total(self):
        """The ledger must still explain how the balance got where it is."""
        count = self._sheet()
        line = count.lines.get(item=self.dal)
        line.counted_quantity = Decimal("132")
        line.save()

        movements = post_count(count, user=self.user)

        self.assertEqual(len(movements), 1)
        self.assertEqual(movements[0].quantity, Decimal("-18"))
        self.assertEqual(movements[0].movement_type, MovementType.COUNT_ADJUSTMENT)

    # Covers: FR-706.
    def test_an_exact_count_writes_nothing(self):
        count = self._sheet()
        line = count.lines.get(item=self.dal)
        line.counted_quantity = Decimal("150")
        line.save()
        self.assertEqual(post_count(count, user=self.user), [])

    # Covers: FR-701, FR-704.
    def test_expected_is_snapshotted_when_the_sheet_is_made(self):
        """
        Variance is measured against what was believed when somebody walked
        the shelves, not against what it drifted to afterwards.
        """
        count = self._sheet()
        post_movement(
            item=self.dal,
            location=self.store,
            quantity=Decimal("-50"),
            movement_type=MovementType.WASTE,
        )
        self.assertEqual(count.lines.get(item=self.dal).expected_quantity, Decimal("150"))

    # Covers: FR-706.
    def test_a_count_cannot_be_posted_twice(self):
        from apps.stock.services import StockError

        count = self._sheet()
        line = count.lines.get(item=self.dal)
        line.counted_quantity = Decimal("132")
        line.save()
        post_count(count, user=self.user)
        with self.assertRaises(StockError):
            post_count(count, user=self.user)

    # Covers: FR-702.
    def test_the_counting_screen_does_not_show_the_expected_figure(self):
        """
        Showing it turns counting into confirming. A sheet that agrees with
        itself tells you nothing about the shelf.
        """
        count = self._sheet()
        response = self.client.get(reverse("count_sheet", args=[count.pk]))
        self.assertContains(response, "Urad dal")
        self.assertNotContains(response, "150")

    # Covers: FR-704, FR-705.
    def test_variance_appears_on_review(self):
        count = self._sheet()
        line = count.lines.get(item=self.dal)
        line.counted_quantity = Decimal("132")
        line.save()
        response = self.client.get(reverse("count_review", args=[count.pk]))
        self.assertContains(response, "-18")

    # Covers: FR-702.
    def test_a_line_saves_as_it_is_typed(self):
        count = self._sheet()
        line = count.lines.get(item=self.dal)
        self.client.post(reverse("count_save_line", args=[count.pk, line.pk]), {"counted": "132"})
        line.refresh_from_db()
        self.assertEqual(line.counted_quantity, Decimal("132"))

    # Covers: FR-702.
    def test_clearing_a_line_puts_it_back_to_not_counted(self):
        count = self._sheet()
        line = count.lines.get(item=self.dal)
        line.counted_quantity = Decimal("132")
        line.save()
        self.client.post(reverse("count_save_line", args=[count.pk, line.pk]), {"counted": ""})
        line.refresh_from_db()
        self.assertIsNone(line.counted_quantity)

    # Covers: FR-702, NFR-05.
    def test_nonsense_does_not_overwrite_a_good_figure(self):
        count = self._sheet()
        line = count.lines.get(item=self.dal)
        line.counted_quantity = Decimal("132")
        line.save()
        self.client.post(reverse("count_save_line", args=[count.pk, line.pk]), {"counted": "abc"})
        line.refresh_from_db()
        self.assertEqual(line.counted_quantity, Decimal("132"))
