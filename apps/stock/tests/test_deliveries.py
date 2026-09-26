"""
A delivery, recorded the way it comes off the truck: in cases and bags,
converted to stock, with what was typed kept beside it.
"""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.catalog.models import Item, ItemKind, ItemMeasure, Unit, UnitKind
from apps.core.models import Location, User
from apps.stock.models import DocumentStatus, GoodsReceipt, MovementType, StockMovement
from apps.stock.services import StockError, add_receipt_line, on_hand, post_receipt


class DeliveryTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("tablet", password="pw")
        self.client.force_login(self.user)
        lb = Unit.objects.create(
            code="lb", name="Pound", kind=UnitKind.WEIGHT, to_canonical=Decimal("453.59237")
        )
        each = Unit.objects.create(code="each", name="Each", kind=UnitKind.COUNT, to_canonical=Decimal("1"))
        self.restaurant = Location.objects.create(code="r", name="Restaurant", kind=Location.Kind.RESTAURANT)
        self.storage = Location.objects.create(
            code="s", name="Devonshire Street unit", kind=Location.Kind.STORAGE
        )
        self.toor = Item.objects.create(code="toor", name="Toor dal", kind=ItemKind.RAW, base_unit=lb)
        self.bag = ItemMeasure.objects.create(item=self.toor, name="bag", quantity_in_base_units=Decimal("8"))
        self.case = ItemMeasure.objects.create(
            item=self.toor, name="case", quantity_in_base_units=Decimal("40")
        )
        self.trays = Item.objects.create(
            code="tray", name="Half tray", kind=ItemKind.PACKAGING, base_unit=each
        )
        self.sambar = Item.objects.create(code="sambar", name="Sambar", kind=ItemKind.PREPARED, base_unit=lb)

    def receipt(self):
        return GoodsReceipt.objects.create(location=self.restaurant, received_at="2026-09-26T17:00:00Z")


class ServiceTests(DeliveryTestCase):
    # Covers: FR-302, FR-306.
    def test_three_cases_of_toor_dal_is_a_hundred_and_twenty_pounds_in_stock(self):
        r = self.receipt()
        add_receipt_line(r, item=self.toor, quantity=Decimal("3"), measure=self.case)
        add_receipt_line(r, item=self.toor, quantity=Decimal("2"), measure=self.bag)
        add_receipt_line(r, item=self.trays, quantity=Decimal("500"))
        post_receipt(r, user=self.user)

        self.assertEqual(on_hand(self.toor, self.restaurant), Decimal("136"))
        self.assertEqual(on_hand(self.trays, self.restaurant), Decimal("500"))
        self.assertEqual(r.lines.get(purchase_unit=self.case).purchase_quantity, Decimal("3"))
        self.assertEqual(
            StockMovement.objects.filter(movement_type=MovementType.RECEIPT, item=self.toor).count(), 2
        )

    def test_the_same_item_and_pack_again_adds_to_its_line(self):
        r = self.receipt()
        add_receipt_line(r, item=self.toor, quantity=Decimal("1"), measure=self.case)
        add_receipt_line(r, item=self.toor, quantity=Decimal("2"), measure=self.case)
        line = r.lines.get()
        self.assertEqual(
            (line.purchase_quantity, line.quantity_in_base_units), (Decimal("3"), Decimal("120"))
        )

    def test_what_the_kitchen_makes_is_not_delivered_and_nonsense_is_refused(self):
        r = self.receipt()
        with self.assertRaisesMessage(StockError, "made in the kitchen"):
            add_receipt_line(r, item=self.sambar, quantity=Decimal("1"))
        with self.assertRaises(StockError):
            add_receipt_line(r, item=self.toor, quantity=Decimal("0"))
        other = ItemMeasure.objects.create(
            item=self.trays, name="sleeve", quantity_in_base_units=Decimal("50")
        )
        with self.assertRaisesMessage(StockError, "another item"):
            add_receipt_line(r, item=self.toor, quantity=Decimal("1"), measure=other)

    def test_a_delivery_is_recorded_once_and_never_empty(self):
        r = self.receipt()
        with self.assertRaisesMessage(StockError, "at least one"):
            post_receipt(r)
        add_receipt_line(r, item=self.toor, quantity=Decimal("1"), measure=self.bag)
        post_receipt(r)
        with self.assertRaisesMessage(StockError, "already"):
            post_receipt(r)
        with self.assertRaisesMessage(StockError, "already"):
            add_receipt_line(r, item=self.toor, quantity=Decimal("1"))
        self.assertEqual(on_hand(self.toor, self.restaurant), Decimal("8"))


class ScreenTests(DeliveryTestCase):
    def test_the_whole_delivery_from_start_to_recorded(self):
        response = self.client.get(reverse("receipt_new"))
        r = GoodsReceipt.objects.get()
        self.assertRedirects(response, reverse("receipt_edit", args=[r.pk]))

        # It arrived at the storage unit, from an unknown supplier.
        self.client.post(reverse("receipt_edit", args=[r.pk]), {"location": self.storage.pk, "supplier": ""})
        r.refresh_from_db()
        self.assertEqual((r.location, r.supplier), (self.storage, None))

        found = self.client.get(reverse("item_search"), {"q": "too", "for": "receipt", "id": r.pk})
        self.assertContains(found, "Toor dal")
        self.assertContains(found, f'<option value="{self.case.pk}">case</option>', html=True)
        self.assertNotContains(
            self.client.get(reverse("item_search"), {"q": "samb", "for": "receipt", "id": r.pk}), "Sambar"
        )

        lines = self.client.post(
            reverse("receipt_add_line", args=[r.pk]),
            {"item": self.toor.pk, "quantity": "2", "measure": self.case.pk},
        )
        self.assertContains(lines, "= 80 lb")
        done = self.client.post(reverse("receipt_post", args=[r.pk]))
        self.assertRedirects(done, reverse("receipt_done", args=[r.pk]))
        self.assertEqual(on_hand(self.toor, self.storage), Decimal("80"))
        self.assertEqual(GoodsReceipt.objects.get().status, DocumentStatus.POSTED)

    def test_a_bad_quantity_is_explained(self):
        r = self.receipt()
        response = self.client.post(
            reverse("receipt_add_line", args=[r.pk]), {"item": self.toor.pk, "quantity": "abc"}
        )
        self.assertContains(response, "greater than zero")
        self.assertFalse(r.lines.exists())

    def test_an_unknown_search_is_refused(self):
        self.assertEqual(
            self.client.get(reverse("item_search"), {"q": "too", "for": "nothing", "id": 1}).status_code, 400
        )
