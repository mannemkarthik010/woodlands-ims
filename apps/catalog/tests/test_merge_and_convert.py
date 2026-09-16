"""
Putting right what 250 decisions in one sitting gets wrong.

Both operations here change what an item IS, which is only safe while the item
has no past. The tests that matter most are the two that refuse.
"""

from decimal import Decimal

from django.test import TestCase

from apps.catalog.models import Item, ItemAlias, ItemKind, Unit, UnitKind
from apps.catalog.services import CatalogError, convert_to_component, merge_items, retire_item
from apps.core.models import Location
from apps.sales.models import PosItem
from apps.sales.services import normalise
from apps.stock.models import MovementType
from apps.stock.services import post_movement


class MergeTests(TestCase):
    def setUp(self):
        self.each = Unit.objects.create(
            code="each", name="Each", kind=UnitKind.COUNT, to_canonical=Decimal("1")
        )
        self.keep = Item.objects.create(
            code="dish-chana-masala",
            name="Chana Masala",
            kind=ItemKind.DISH,
            base_unit=self.each,
            is_stocked=False,
        )
        self.fold = Item.objects.create(
            code="dish-channa-masala",
            name="Channa Masala",
            kind=ItemKind.DISH,
            base_unit=self.each,
            is_stocked=False,
        )
        self.line = PosItem.objects.create(
            pos_name="Channa Masala 4 oz", group_key="channa masala", item=self.fold
        )

    # Covers: FR-202.
    def test_the_old_spelling_survives_as_a_searchable_name(self):
        """
        Somebody typed "Channa Masala" into the till for years and will type it
        into the search box too. Losing the spelling makes the item harder to
        find, which is not the same as cleaner.
        """
        merge_items(self.fold, self.keep)
        self.assertTrue(ItemAlias.objects.filter(item=self.keep, alias="Channa Masala").exists())

    # Covers: FR-211.
    def test_the_till_lines_follow_and_the_old_item_is_retired_not_deleted(self):
        merge_items(self.fold, self.keep)
        self.line.refresh_from_db()
        self.fold.refresh_from_db()
        self.assertEqual(self.line.item, self.keep)
        self.assertFalse(self.fold.is_active)
        self.assertTrue(Item.objects.filter(pk=self.fold.pk).exists())

    # Covers: FR-1203, FR-1204.
    def test_an_item_with_stock_movements_cannot_be_merged_away(self):
        """
        The ledger refers to this item. Folding it into another would change
        what the record says happened, which is the one thing this system
        does not do.
        """
        stocked = Item.objects.create(
            code="prep-sambar", name="Sambar", kind=ItemKind.PREPARED, base_unit=self.each
        )
        where = Location.objects.create(code="rest", name="Restaurant", kind=Location.Kind.RESTAURANT)
        post_movement(item=stocked, location=where, quantity=Decimal("5"), movement_type=MovementType.RECEIPT)
        with self.assertRaises(CatalogError):
            merge_items(stocked, self.keep)

    def test_an_item_cannot_be_merged_into_itself(self):
        with self.assertRaises(CatalogError):
            merge_items(self.keep, self.keep)


class ConvertTests(TestCase):
    """
    A tub of sambar sold over the counter is not a dish. It is the same pot
    the kitchen ladles from into Idly Sambar, and it is measured in ounces.
    """

    def setUp(self):
        self.each = Unit.objects.create(
            code="each", name="Each", kind=UnitKind.COUNT, to_canonical=Decimal("1")
        )
        self.floz = Unit.objects.create(
            code="floz", name="Fluid ounce", kind=UnitKind.VOLUME, to_canonical=Decimal("29.5735")
        )
        self.sambar = Item.objects.create(
            code="dish-sambar",
            name="Sambar",
            kind=ItemKind.DISH,
            base_unit=self.each,
            is_stocked=False,
        )
        for name in ["Sambar 8oz", "Sambar 16 oz"]:
            PosItem.objects.create(pos_name=name, group_key=normalise(name), item=self.sambar)

    # Covers: FR-203, FR-209, FR-501.
    def test_it_becomes_a_stocked_component_and_the_tub_sizes_start_counting(self):
        convert_to_component(self.sambar, self.floz)
        self.sambar.refresh_from_db()
        self.assertEqual(self.sambar.kind, ItemKind.PREPARED)
        self.assertTrue(self.sambar.is_stocked)
        sizes = {p.pos_name: p.quantity_per_sale for p in PosItem.objects.all()}
        self.assertEqual(sizes["Sambar 8oz"], Decimal("8"))
        self.assertEqual(sizes["Sambar 16 oz"], Decimal("16"))

    # Covers: FR-203.
    def test_the_unit_cannot_change_once_there_are_movements_in_the_old_one(self):
        """
        Every existing movement is a number in the old unit. Changing what the
        unit means would silently change all of them.
        """
        self.sambar.is_stocked = True
        self.sambar.kind = ItemKind.PREPARED
        self.sambar.save()
        where = Location.objects.create(code="rest", name="Restaurant", kind=Location.Kind.RESTAURANT)
        post_movement(
            item=self.sambar,
            location=where,
            quantity=Decimal("3"),
            movement_type=MovementType.PRODUCTION_YIELD,
        )
        with self.assertRaises(CatalogError):
            convert_to_component(self.sambar, self.floz)


class RetireTests(TestCase):
    def test_retiring_keeps_the_item_and_its_code(self):
        each = Unit.objects.create(code="each", name="Each", kind=UnitKind.COUNT, to_canonical=Decimal("1"))
        item = Item.objects.create(
            code="dish-leftover",
            name="Leftover",
            kind=ItemKind.DISH,
            base_unit=each,
            is_stocked=False,
        )
        retire_item(item, reason="Left over from a mapping decision that was changed.")
        item.refresh_from_db()
        self.assertFalse(item.is_active)
        self.assertIn("mapping decision", item.notes)
