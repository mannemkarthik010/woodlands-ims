"""
Loading the answers from a visit.

What matters is that the kitchen's words arrive as they were said -- ounces
on a plate become the right fraction of a pound of batter -- and that a
second run, or a run against a menu "Paneer" button instead of the paneer
in the fridge, does no damage.
"""

import json
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.test import TestCase

from apps.catalog.models import CountEvery, Item, ItemKind, ParLevel, Recipe, Unit, UnitKind, Vessel
from apps.catalog.services import find_by_name
from apps.core.models import Location
from apps.sales.models import PosItem

SIDES = {"_code": "C6", "Sambar": 4, "Coconut Chutney": 4}


def item(name, kind=ItemKind.PREPARED, unit="lb", **extra):
    code = f"{kind.lower()}-{name.lower().replace(' ', '-')}"
    return Item.objects.create(
        code=code, name=name, kind=kind, base_unit=Unit.objects.get(code=unit), **extra
    )


class ImportVisitTests(TestCase):
    def setUp(self):
        Unit.objects.create(code="lb", name="Pound", kind=UnitKind.WEIGHT, to_canonical=Decimal("453.59237"))
        Unit.objects.create(code="each", name="Each", kind=UnitKind.COUNT, to_canonical=Decimal("1"))
        Location.objects.create(code="restaurant", name="Restaurant", kind=Location.Kind.RESTAURANT)
        self.batter = item("Dosa Batter")
        self.sambar = item("Sambar")
        self.chutney = item("Coconut Chutney")
        self.dosa = item("Plain Dosa", kind=ItemKind.DISH, unit="each")

    def run_visit(self, visit, **kwargs):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "visit.json"
            path.write_text(json.dumps({"visit": {"date": "2026-10-02"}, **visit}))
            call_command("import_visit", str(path), verbosity=0, **kwargs)

    def plate(self, oz):
        return {"plates": {"sides": SIDES, "dishes": [{"dish": "Plain Dosa", "codes": "C1 C6", "oz": oz}]}}

    def lines(self, dish):
        recipe = Recipe.objects.get(item=dish, is_active=True)
        return {line.component.name: line.quantity for line in recipe.lines.all()}

    # Covers: FR-601, FR-602.
    def test_a_plate_becomes_a_recipe_in_pounds_with_the_sides(self):
        self.run_visit(self.plate({"Dosa Batter": 5, "sides": 1}))
        self.assertEqual(
            self.lines(self.dosa),
            {"Dosa Batter": Decimal("0.3125"), "Sambar": Decimal("0.25"), "Coconut Chutney": Decimal("0.25")},
        )

    def test_running_it_twice_changes_nothing(self):
        self.run_visit(self.plate({"Dosa Batter": 5, "sides": 1}))
        self.run_visit(self.plate({"Dosa Batter": 5, "sides": 1}))
        self.assertEqual(Recipe.objects.filter(item=self.dosa).count(), 1)

    def test_a_changed_plate_is_a_new_version_and_the_old_one_is_kept(self):
        self.run_visit(self.plate({"Dosa Batter": 5, "sides": 1}))
        self.run_visit(self.plate({"Dosa Batter": 6, "sides": 1}))
        self.assertEqual(Recipe.objects.filter(item=self.dosa).count(), 2)
        self.assertEqual(Recipe.objects.get(item=self.dosa, is_active=True).version, 2)
        self.assertEqual(self.lines(self.dosa)["Dosa Batter"], Decimal("0.375"))

    def test_a_menu_button_is_never_used_as_an_ingredient(self):
        item("Paneer", kind=ItemKind.DISH, unit="each")
        self.run_visit(self.plate({"Dosa Batter": 5, "Paneer": 6}))
        self.assertFalse(Recipe.objects.filter(item=self.dosa).exists())

    def test_a_missing_ingredient_skips_the_dish_rather_than_half_making_it(self):
        self.run_visit(self.plate({"Dosa Batter": 5, "Mysore Chutney": 1}))
        self.assertFalse(Recipe.objects.filter(item=self.dosa).exists())

    # Covers: FR-212.
    def test_make_more_below_one_bucket_is_a_par_level_in_pounds(self):
        self.run_visit(
            {
                "containers": [{"code": "C26.12", "item": "Dosa Batter", "measure": "bucket", "lb": 32}],
                "make_more_below": [
                    {"code": "O22.1", "item": "Dosa Batter", "quantity": 1, "measure": "bucket"}
                ],
            }
        )
        self.assertEqual(ParLevel.objects.get(item=self.batter).quantity, Decimal("32"))

    def test_a_button_sold_by_size_gets_its_size(self):
        pos = PosItem.objects.create(pos_name="Dosa Batter", item=self.batter)
        self.assertTrue(pos.needs_portion)
        self.run_visit({"sized_buttons": [{"code": "C22", "pos_name": "Dosa Batter", "oz": 32}]})
        pos.refresh_from_db()
        self.assertTrue(pos.portion_confirmed)
        self.assertEqual(pos.quantity_per_sale, Decimal("2"))

    def test_count_lists_new_items_and_vessels(self):
        self.run_visit(
            {
                "vessels": [{"code": "C28.2", "name": "scoop", "fl_oz": 30}],
                "count_every": [{"code": "K02", "item": "Coconut Chutney", "every": "DAILY"}],
                "new_items": [
                    {"name": "Vada Batter", "what": "PREPARED", "unit": "lb", "every": "WEEKLY", "why": "C9"}
                ],
            }
        )
        self.assertEqual(Vessel.objects.get(name="scoop").volume_ml, Decimal("887.20"))
        self.chutney.refresh_from_db()
        self.assertEqual(self.chutney.count_every, CountEvery.DAILY)
        self.assertEqual(Item.objects.get(name="Vada Batter").kind, ItemKind.PREPARED)

    def test_a_dry_run_keeps_nothing(self):
        self.run_visit(self.plate({"Dosa Batter": 5, "sides": 1}), dry_run=True)
        self.assertFalse(Recipe.objects.exists())


class FindByNameTests(TestCase):
    def setUp(self):
        Unit.objects.create(code="lb", name="Pound", kind=UnitKind.WEIGHT, to_canonical=Decimal("453.59237"))

    def test_the_item_in_use_wins_over_a_retired_one_of_the_same_name(self):
        item("Chana Masala", kind=ItemKind.DISH, is_active=False)
        live = Item.objects.create(
            code="prep-chana",
            name="Chana Masala",
            kind=ItemKind.PREPARED,
            base_unit=Unit.objects.get(code="lb"),
        )
        self.assertEqual(find_by_name("chana masala"), live)
