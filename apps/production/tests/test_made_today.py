"""
"Made today": the kitchen records what it made, in its own containers, and
what went in. The made item goes up; what went in comes down; nothing is
written until it is saved, and a batch started by mistake leaves no trace.
"""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.catalog.models import Item, ItemKind, ItemMeasure, MeasureKind, Unit, UnitKind
from apps.core.models import Location, User
from apps.production.models import BatchStatus, ProductionBatch
from apps.production.services import BatchError, add_input, discard_batch, finish_batch, start_batch
from apps.stock.models import MovementType, StockMovement
from apps.stock.services import on_hand


class MadeTodayTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("tablet", password="pw", display_name="Ramesh")
        self.client.force_login(self.user)
        lb = Unit.objects.create(
            code="lb", name="Pound", kind=UnitKind.WEIGHT, to_canonical=Decimal("453.59237")
        )
        self.restaurant = Location.objects.create(code="r", name="Restaurant", kind=Location.Kind.RESTAURANT)
        self.sambar = Item.objects.create(code="sambar", name="Sambar", kind=ItemKind.PREPARED, base_unit=lb)
        self.bucket = ItemMeasure.objects.create(
            item=self.sambar, name="bucket", kind=MeasureKind.KITCHEN, quantity_in_base_units=Decimal("32")
        )
        self.toor = Item.objects.create(code="toor", name="Toor dal", kind=ItemKind.RAW, base_unit=lb)
        self.scoop = ItemMeasure.objects.create(
            item=self.toor, name="scoop", kind=MeasureKind.KITCHEN, quantity_in_base_units=Decimal("2")
        )
        self.powder = Item.objects.create(
            code="podi", name="Sambar powder", kind=ItemKind.PREPARED, base_unit=lb
        )

    def start(self):
        return start_batch(self.sambar, location=self.restaurant, by=self.user)


class ServiceTests(MadeTodayTestCase):
    # Covers: FR-501, FR-502, FR-503.
    def test_two_buckets_of_sambar_made_from_three_scoops_of_toor_dal(self):
        batch = self.start()
        add_input(batch, item=self.toor, quantity=Decimal("3"), measure=self.scoop)
        add_input(batch, item=self.powder, quantity=Decimal("0.9"))
        finish_batch(batch, quantity=Decimal("2"), measure=self.bucket, by=self.user)

        self.assertEqual(on_hand(self.sambar, self.restaurant), Decimal("64"))
        self.assertEqual(
            on_hand(self.toor, self.restaurant), Decimal("-6")
        )  # no opening stock yet; the ledger says so
        self.assertEqual(on_hand(self.powder, self.restaurant), Decimal("-0.9"))
        batch.refresh_from_db()
        self.assertEqual((batch.status, batch.actual_yield), (BatchStatus.AVAILABLE, Decimal("64")))
        self.assertEqual(
            (batch.yield_entered_quantity, batch.yield_entered_measure), (Decimal("2"), self.bucket)
        )
        yielded = StockMovement.objects.get(movement_type=MovementType.PRODUCTION_YIELD)
        self.assertEqual(yielded.batch, batch)
        self.assertEqual(
            StockMovement.objects.filter(movement_type=MovementType.PRODUCTION_CONSUME).count(), 2
        )

    def test_what_went_in_is_optional(self):
        batch = self.start()
        finish_batch(batch, quantity=Decimal("1"), measure=self.bucket)
        self.assertEqual(on_hand(self.sambar, self.restaurant), Decimal("32"))
        self.assertFalse(StockMovement.objects.filter(movement_type=MovementType.PRODUCTION_CONSUME).exists())

    def test_nothing_is_written_to_stock_until_it_is_saved(self):
        batch = self.start()
        add_input(batch, item=self.toor, quantity=Decimal("3"), measure=self.scoop)
        self.assertFalse(StockMovement.objects.exists())
        discard_batch(batch)
        self.assertFalse(ProductionBatch.objects.exists())

    def test_the_rules(self):
        with self.assertRaisesMessage(BatchError, "not something the kitchen makes"):
            start_batch(self.toor, location=self.restaurant)
        batch = self.start()
        with self.assertRaisesMessage(BatchError, "cannot go into"):
            add_input(batch, item=self.sambar, quantity=Decimal("1"))
        with self.assertRaises(BatchError):
            add_input(batch, item=self.toor, quantity=Decimal("0"))
        with self.assertRaisesMessage(BatchError, "How much was made"):
            finish_batch(batch, quantity=None)
        with self.assertRaisesMessage(BatchError, "another item"):
            finish_batch(batch, quantity=Decimal("1"), measure=self.scoop)
        finish_batch(batch, quantity=Decimal("1"), measure=self.bucket)
        with self.assertRaisesMessage(BatchError, "already"):
            finish_batch(batch, quantity=Decimal("1"))
        with self.assertRaisesMessage(BatchError, "already"):
            discard_batch(batch)

    def test_the_same_ingredient_again_adds_to_its_line_and_codes_are_unique(self):
        batch = self.start()
        add_input(batch, item=self.toor, quantity=Decimal("2"), measure=self.scoop)
        line = add_input(batch, item=self.toor, quantity=Decimal("1"), measure=self.scoop)
        self.assertEqual((line.entered_quantity, line.quantity), (Decimal("3"), Decimal("6")))
        self.assertNotEqual(batch.batch_code, self.start().batch_code)


class ScreenTests(MadeTodayTestCase):
    def test_tap_sambar_say_two_buckets_add_what_went_in_save(self):
        page = self.client.get(reverse("made_today"))
        self.assertContains(page, "Sambar")
        self.assertNotContains(page, "Toor dal")  # groceries are not made

        response = self.client.post(reverse("batch_start"), {"item": self.sambar.pk})
        batch = ProductionBatch.objects.get()
        self.assertRedirects(response, reverse("batch_edit", args=[batch.pk]))
        self.assertContains(
            self.client.get(response.url), f'<option value="{self.bucket.pk}">bucket</option>', html=True
        )

        found = self.client.get(reverse("item_search"), {"q": "toor", "for": "batch", "id": batch.pk})
        self.assertContains(found, "Toor dal")
        added = self.client.post(
            reverse("batch_add_input", args=[batch.pk]),
            {"item": self.toor.pk, "quantity": "3", "measure": self.scoop.pk},
        )
        self.assertContains(added, "= 6 lb")

        done = self.client.post(
            reverse("batch_finish", args=[batch.pk]), {"made": "2", "measure": self.bucket.pk}
        )
        self.assertRedirects(done, reverse("made_today"))
        today = self.client.get(reverse("made_today"))
        self.assertContains(today, "Made today")
        self.assertContains(today, batch.batch_code)
        self.assertEqual(on_hand(self.sambar, self.restaurant), Decimal("64"))

    def test_saving_without_saying_how_much_is_explained(self):
        batch = self.start()
        response = self.client.post(reverse("batch_finish", args=[batch.pk]), {"made": ""})
        self.assertContains(response, "How much was made")
        self.assertFalse(StockMovement.objects.exists())

    def test_an_unfinished_batch_is_offered_to_finish(self):
        batch = self.start()
        self.assertContains(self.client.get(reverse("made_today")), reverse("batch_edit", args=[batch.pk]))
