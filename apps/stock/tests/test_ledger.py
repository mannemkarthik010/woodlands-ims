"""
The foundation, tested. These are the rules the rest of the system assumes.
"""

from decimal import Decimal

from django.test import TestCase

from apps.catalog.models import Item, ItemKind, Recipe, RecipeLine, Unit, UnitKind
from apps.core.models import Location
from apps.stock.models import DocumentStatus, MovementType, StockMovement, Transfer, TransferLine
from apps.stock.services import (
    StockError,
    explode,
    on_hand,
    post_movement,
    post_transfer,
    rebuild_balances,
    reverse_movement,
)


class LedgerTests(TestCase):
    def setUp(self):
        self.lb = Unit.objects.create(
            code="lb", name="Pound", kind=UnitKind.WEIGHT, to_canonical=Decimal("453.59237")
        )
        self.gal = Unit.objects.create(
            code="gal", name="Gallon", kind=UnitKind.VOLUME, to_canonical=Decimal("3785.41")
        )
        self.storage = Location.objects.create(
            code="storage", name="Devonshire St", kind=Location.Kind.STORAGE
        )
        self.rest = Location.objects.create(code="rest", name="Restaurant", kind=Location.Kind.RESTAURANT)
        self.dal = Item.objects.create(code="raw-urad", name="Urad dal", kind=ItemKind.RAW, base_unit=self.lb)

    def test_balance_is_the_sum_of_movements(self):
        post_movement(
            item=self.dal, location=self.storage, quantity=Decimal("150"), movement_type=MovementType.RECEIPT
        )
        post_movement(
            item=self.dal,
            location=self.storage,
            quantity=Decimal("-18"),
            movement_type=MovementType.TRANSFER_OUT,
        )
        self.assertEqual(on_hand(self.dal, self.storage), Decimal("132"))

    def test_cache_can_always_be_rebuilt_from_the_ledger(self):
        post_movement(
            item=self.dal, location=self.storage, quantity=Decimal("100"), movement_type=MovementType.RECEIPT
        )
        from apps.stock.models import StockBalance

        StockBalance.objects.update(quantity=Decimal("999999"))  # corrupt it
        rebuild_balances()
        self.assertEqual(
            StockBalance.objects.get(item=self.dal, location=self.storage).quantity, Decimal("100")
        )

    def test_a_correction_is_a_new_row_not_an_edit(self):
        m = post_movement(
            item=self.dal, location=self.storage, quantity=Decimal("50"), movement_type=MovementType.RECEIPT
        )
        reverse_movement(m, reason="Counted wrong")
        self.assertEqual(on_hand(self.dal, self.storage), Decimal("0"))
        self.assertEqual(StockMovement.objects.count(), 2)  # original survives
        self.assertTrue(StockMovement.objects.filter(reverses=m).exists())

    def test_a_movement_cannot_be_reversed_twice(self):
        m = post_movement(
            item=self.dal, location=self.storage, quantity=Decimal("50"), movement_type=MovementType.RECEIPT
        )
        reverse_movement(m)
        with self.assertRaises(StockError):
            reverse_movement(m)

    def test_transfer_moves_stock_between_locations_atomically(self):
        post_movement(
            item=self.dal, location=self.storage, quantity=Decimal("150"), movement_type=MovementType.RECEIPT
        )
        t = Transfer.objects.create(
            from_location=self.storage, to_location=self.rest, occurred_at="2026-09-16T15:00:00Z"
        )
        TransferLine.objects.create(transfer=t, item=self.dal, quantity=Decimal("18"))
        post_transfer(t)

        self.assertEqual(on_hand(self.dal, self.storage), Decimal("132"))
        self.assertEqual(on_hand(self.dal, self.rest), Decimal("18"))
        self.assertEqual(on_hand(self.dal), Decimal("150"))  # nothing created or destroyed
        t.refresh_from_db()
        self.assertEqual(t.status, DocumentStatus.POSTED)

    def test_a_transfer_cannot_be_posted_twice(self):
        post_movement(
            item=self.dal, location=self.storage, quantity=Decimal("150"), movement_type=MovementType.RECEIPT
        )
        t = Transfer.objects.create(
            from_location=self.storage, to_location=self.rest, occurred_at="2026-09-16T15:00:00Z"
        )
        TransferLine.objects.create(transfer=t, item=self.dal, quantity=Decimal("18"))
        post_transfer(t)
        with self.assertRaises(StockError):
            post_transfer(t)

    def test_negative_stock_can_be_refused(self):
        with self.assertRaises(StockError):
            post_movement(
                item=self.dal,
                location=self.rest,
                quantity=Decimal("-5"),
                movement_type=MovementType.WASTE,
                allow_negative=False,
            )


class ExplosionTests(TestCase):
    """A sale has to reach the raw materials, not stop at the base."""

    def setUp(self):
        self.lb = Unit.objects.create(
            code="lb", name="Pound", kind=UnitKind.WEIGHT, to_canonical=Decimal("453.59237")
        )
        self.gal = Unit.objects.create(
            code="gal", name="Gallon", kind=UnitKind.VOLUME, to_canonical=Decimal("3785.41")
        )
        self.each = Unit.objects.create(
            code="each", name="Each", kind=UnitKind.COUNT, to_canonical=Decimal("1")
        )

        self.rice = Item.objects.create(
            code="raw-rice", name="Idli rice", kind=ItemKind.RAW, base_unit=self.lb
        )
        self.dal = Item.objects.create(code="raw-urad", name="Urad dal", kind=ItemKind.RAW, base_unit=self.lb)
        self.batter = Item.objects.create(
            code="prep-dosa-batter", name="Dosa batter", kind=ItemKind.PREPARED, base_unit=self.gal
        )
        self.dosa = Item.objects.create(
            code="dish-plain-dosa",
            name="Plain dosa",
            kind=ItemKind.DISH,
            base_unit=self.each,
            is_stocked=False,
        )

        # 20 lb rice + 5 lb dal -> 11 gal batter
        r = Recipe.objects.create(item=self.batter, yield_quantity=Decimal("11"))
        RecipeLine.objects.create(recipe=r, component=self.rice, quantity=Decimal("20"))
        RecipeLine.objects.create(recipe=r, component=self.dal, quantity=Decimal("5"))

        # 1 dosa -> 0.05 gal batter
        d = Recipe.objects.create(item=self.dosa, yield_quantity=Decimal("1"))
        RecipeLine.objects.create(recipe=d, component=self.batter, quantity=Decimal("0.05"))

    def test_a_dish_explodes_all_the_way_to_raw_materials(self):
        result = {c.item.code: c.quantity for c in explode(self.dosa, Decimal("100"))}
        # 100 dosas = 5 gal batter = 5/11 of a grind
        self.assertAlmostEqual(result["raw-rice"], Decimal("20") * (Decimal("5") / Decimal("11")), places=6)
        self.assertAlmostEqual(result["raw-urad"], Decimal("5") * (Decimal("5") / Decimal("11")), places=6)
        self.assertNotIn("prep-dosa-batter", result)  # intermediate, not a leaf

    def test_a_recipe_loop_is_refused_rather_than_hanging(self):
        loop = Recipe.objects.create(item=self.rice, yield_quantity=Decimal("1"))
        RecipeLine.objects.create(recipe=loop, component=self.batter, quantity=Decimal("1"))
        with self.assertRaises(StockError):
            explode(self.dosa, Decimal("1"))
