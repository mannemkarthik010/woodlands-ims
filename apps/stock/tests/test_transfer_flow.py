"""
The transfer flow, exercised the way a person actually uses it.

These are not unit tests of the view functions. They walk the screens in
order, because the thing that matters about this feature is that the whole
sequence works in a few taps -- a passing test on each step separately would
not tell us that.
"""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.catalog.models import Item, ItemAlias, ItemKind, Unit, UnitKind
from apps.core.models import Location, User
from apps.stock.models import DocumentStatus, MovementType, Transfer
from apps.stock.services import on_hand, post_movement


class TransferFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("karthik", password="pw")
        self.client.force_login(self.user)

        self.lb = Unit.objects.create(
            code="lb", name="Pound", kind=UnitKind.WEIGHT, to_canonical=Decimal("453.59237")
        )
        self.storage = Location.objects.create(
            code="devonshire", name="Devonshire Street unit", kind=Location.Kind.STORAGE
        )
        self.restaurant = Location.objects.create(
            code="restaurant", name="Restaurant", kind=Location.Kind.RESTAURANT
        )
        self.dal = Item.objects.create(code="raw-urad", name="Urad dal", kind=ItemKind.RAW, base_unit=self.lb)
        ItemAlias.objects.create(item=self.dal, alias="black gram")

        post_movement(
            item=self.dal,
            location=self.storage,
            quantity=Decimal("150"),
            movement_type=MovementType.RECEIPT,
        )

    def test_the_whole_run_from_start_to_recorded(self):
        # Tap "Storage run" -- a draft exists immediately, so nothing is lost
        # if the phone locks halfway through.
        response = self.client.get(reverse("transfer_new"))
        transfer = Transfer.objects.get()
        self.assertRedirects(response, reverse("transfer_edit", args=[transfer.pk]))

        # Defaults point the common way round without anyone choosing.
        self.assertEqual(transfer.from_location, self.storage)
        self.assertEqual(transfer.to_location, self.restaurant)

        # Type a couple of letters, get the item.
        results = self.client.get(reverse("item_search"), {"q": "ura", "transfer": transfer.pk})
        self.assertContains(results, "Urad dal")

        # Add it.
        self.client.post(
            reverse("transfer_add_line", args=[transfer.pk]),
            {"item": self.dal.pk, "quantity": "18"},
        )
        self.assertEqual(transfer.lines.count(), 1)

        # Nothing has moved yet -- a draft is not a transfer.
        self.assertEqual(on_hand(self.dal, self.storage), Decimal("150"))

        # Press record.
        self.client.post(reverse("transfer_post", args=[transfer.pk]))

        transfer.refresh_from_db()
        self.assertEqual(transfer.status, DocumentStatus.POSTED)
        self.assertEqual(on_hand(self.dal, self.storage), Decimal("132"))
        self.assertEqual(on_hand(self.dal, self.restaurant), Decimal("18"))
        self.assertEqual(on_hand(self.dal), Decimal("150"))

    def test_items_are_findable_by_the_name_someone_actually_uses(self):
        transfer = Transfer.objects.create(
            from_location=self.storage,
            to_location=self.restaurant,
            occurred_at="2026-09-16T15:00:00Z",
        )
        results = self.client.get(reverse("item_search"), {"q": "black gram", "transfer": transfer.pk})
        self.assertContains(results, "Urad dal")

    def test_the_same_item_twice_adds_up_instead_of_making_two_rows(self):
        transfer = Transfer.objects.create(
            from_location=self.storage,
            to_location=self.restaurant,
            occurred_at="2026-09-16T15:00:00Z",
        )
        url = reverse("transfer_add_line", args=[transfer.pk])
        self.client.post(url, {"item": self.dal.pk, "quantity": "10"})
        self.client.post(url, {"item": self.dal.pk, "quantity": "8"})

        self.assertEqual(transfer.lines.count(), 1)
        self.assertEqual(transfer.lines.get().quantity, Decimal("18"))

    def test_a_bad_quantity_is_refused_without_losing_the_run(self):
        transfer = Transfer.objects.create(
            from_location=self.storage,
            to_location=self.restaurant,
            occurred_at="2026-09-16T15:00:00Z",
        )
        response = self.client.post(
            reverse("transfer_add_line", args=[transfer.pk]),
            {"item": self.dal.pk, "quantity": "nonsense"},
        )
        self.assertContains(response, "greater than zero")
        self.assertEqual(transfer.lines.count(), 0)

    def test_an_empty_run_cannot_be_recorded(self):
        transfer = Transfer.objects.create(
            from_location=self.storage,
            to_location=self.restaurant,
            occurred_at="2026-09-16T15:00:00Z",
        )
        response = self.client.post(reverse("transfer_post", args=[transfer.pk]))
        self.assertContains(response, "Add at least one item")
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, DocumentStatus.DRAFT)

    def test_a_line_can_be_removed_before_recording(self):
        transfer = Transfer.objects.create(
            from_location=self.storage,
            to_location=self.restaurant,
            occurred_at="2026-09-16T15:00:00Z",
        )
        self.client.post(
            reverse("transfer_add_line", args=[transfer.pk]),
            {"item": self.dal.pk, "quantity": "18"},
        )
        line = transfer.lines.get()
        self.client.post(reverse("transfer_remove_line", args=[transfer.pk, line.pk]))
        self.assertEqual(transfer.lines.count(), 0)

    def test_signed_out_people_see_nothing(self):
        self.client.logout()
        response = self.client.get(reverse("transfer_new"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])
