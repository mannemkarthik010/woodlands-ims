"""
Reading the kitchen's own paperwork.

The risk in both of these commands is the same: reading a little too much into
what is written. A sheet that says "1 bag lasts 1 month" has not told you a par
level, and a scale that says a scoop of toor dal is 32 oz has not told you what
a scoop of anything else is.
"""

from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.test import TestCase

from apps.catalog.models import Item, ItemKind, ItemMeasure, MeasureKind, Unit, UnitKind


def units():
    Unit.objects.create(code="lb", name="Pound", kind=UnitKind.WEIGHT, to_canonical=Decimal("453.59237"))
    Unit.objects.create(code="g", name="Gram", kind=UnitKind.WEIGHT, to_canonical=Decimal("1"))
    Unit.objects.create(code="oz", name="Ounce", kind=UnitKind.WEIGHT, to_canonical=Decimal("28.349523125"))
    Unit.objects.create(code="kg", name="Kilogram", kind=UnitKind.WEIGHT, to_canonical=Decimal("1000"))
    Unit.objects.create(code="ml", name="Millilitre", kind=UnitKind.VOLUME, to_canonical=Decimal("1"))
    Unit.objects.create(code="l", name="Litre", kind=UnitKind.VOLUME, to_canonical=Decimal("1000"))
    Unit.objects.create(code="each", name="Each", kind=UnitKind.COUNT, to_canonical=Decimal("1"))


def write(folder, name, text):
    path = Path(folder) / name
    path.write_text(text)
    return str(path)


class IngredientImportTests(TestCase):
    def setUp(self):
        units()

    def run_sheet(self, body, **kwargs):
        with TemporaryDirectory() as folder:
            path = write(folder, "sheet.csv", "Item,Size,Case,Usage,Notes,Purchases\n" + body)
            call_command("import_ingredients", path, verbosity=0, **kwargs)

    # Covers: FR-201, FR-204.
    def test_a_pack_and_a_case_both_become_measures(self):
        self.run_sheet("Toor Dal,8 lb bag,1 case = 5 bags,,,\n")
        item = Item.objects.get(name="Toor Dal")
        self.assertEqual(item.kind, ItemKind.RAW)
        self.assertEqual(item.base_unit.code, "lb")
        measures = {m.name: m.quantity_in_base_units for m in item.measures.all()}
        self.assertEqual(measures["bag"], Decimal("8"))
        self.assertEqual(measures["case"], Decimal("40"))  # five bags, not five pounds

    # Covers: FR-204.
    def test_a_count_pack_is_read_as_a_count(self):
        self.run_sheet("Vegetable Samosa,25 piece per box,1 case = 6 boxes,,,\n")
        item = Item.objects.get(name="Vegetable Samosa")
        self.assertEqual(item.base_unit.code, "each")
        measures = {m.name: m.quantity_in_base_units for m in item.measures.all()}
        self.assertEqual(measures["box"], Decimal("25"))
        self.assertEqual(measures["case"], Decimal("150"))

    # Covers: FR-201.
    def test_an_unreadable_pack_still_gets_the_ingredient_in(self):
        """
        "Jaggery" with no size is still a real thing the kitchen buys. Leaving
        it out to keep the import tidy would lose it.
        """
        self.run_sheet("Jaggery,,,,next time don't buy huge piece,\n")
        self.assertTrue(Item.objects.filter(name="Jaggery").exists())

    def test_usage_prose_does_not_become_a_par_level(self):
        """
        "1 bag lasts 1 month" is evidence for a par level, not a par level.
        It is kept as a note for the person who will set one.
        """
        self.run_sheet("Idly Rice,20 lb bag,,1 bag lasts 1 month,,\n")
        item = Item.objects.get(name="Idly Rice")
        self.assertEqual(item.par_levels.count(), 0)
        self.assertIn("1 bag lasts 1 month", item.notes)

    # Covers: FR-204.
    def test_a_figure_somebody_corrected_is_not_overwritten(self):
        """
        The sheet is evidence, not authority. If somebody weighed the sack and
        found 24 lb, that beats what the supplier's catalogue says.
        """
        self.run_sheet("Chana Dal,4 lb bag,,,,\n")
        measure = ItemMeasure.objects.get(item__name="Chana Dal", name="bag")
        measure.quantity_in_base_units = Decimal("3.75")
        measure.save()
        self.run_sheet("Chana Dal,4 lb bag,,,,\n")
        measure.refresh_from_db()
        self.assertEqual(measure.quantity_in_base_units, Decimal("3.7500"))


class KitchenMeasureTests(TestCase):
    def setUp(self):
        units()
        self.lb = Unit.objects.get(code="lb")

    def run_measures(self, body):
        with TemporaryDirectory() as folder:
            path = write(
                folder,
                "m.csv",
                "item,measure,ounces,approximate,measured_on,source\n" + body,
            )
            call_command("import_measures", path, verbosity=0)

    # Covers: FR-203, FR-205.
    def test_a_scoop_is_recorded_in_the_items_own_unit(self):
        Item.objects.create(code="raw-toor", name="Toor Dal", kind=ItemKind.RAW, base_unit=self.lb)
        self.run_measures("Toor Dal,scoop,32,no,2026-09-16,note\n")
        measure = ItemMeasure.objects.get(item__name="Toor Dal", name="scoop")
        self.assertEqual(measure.quantity_in_base_units, Decimal("2.0000"))  # 32 oz, in pounds
        self.assertEqual(measure.kind, MeasureKind.KITCHEN)

    # Covers: FR-205.
    def test_a_scoop_of_one_thing_says_nothing_about_a_scoop_of_another(self):
        """
        The whole finding. A scoop of toor dal is 32 oz and a scoop of sambar
        powder is 14 oz, because a scoop is a vessel and not a weight.
        """
        Item.objects.create(code="raw-toor", name="Toor Dal", kind=ItemKind.RAW, base_unit=self.lb)
        Item.objects.create(code="prep-podi", name="Sambar Powder", kind=ItemKind.PREPARED, base_unit=self.lb)
        self.run_measures("Toor Dal,scoop,32,no,2026-09-16,note\nSambar Powder,scoop,14,no,2026-09-16,note\n")
        held = {m.item.name: m.quantity_in_base_units for m in ItemMeasure.objects.filter(name="scoop")}
        self.assertEqual(held["Toor Dal"], Decimal("2.0000"))
        self.assertEqual(held["Sambar Powder"], Decimal("0.8750"))

    def test_a_weight_is_refused_against_an_item_held_by_volume(self):
        """
        Nobody has measured the density of sambar, so ounces of it cannot be
        turned into litres. Refused rather than approximated.
        """
        litre = Unit.objects.get(code="l")
        Item.objects.create(code="prep-sambar", name="Sambar", kind=ItemKind.PREPARED, base_unit=litre)
        self.run_measures("Sambar,scoop,32,no,2026-09-16,note\n")
        self.assertFalse(ItemMeasure.objects.filter(item__name="Sambar").exists())

    def test_an_ingredient_that_is_not_in_the_catalogue_is_skipped_not_invented(self):
        self.run_measures("Tomato Magic,can,106,no,2026-09-16,note\n")
        self.assertFalse(Item.objects.filter(name="Tomato Magic").exists())

    # Covers: FR-202.
    def test_a_measure_can_be_recorded_against_a_name_the_kitchen_uses(self):
        item = Item.objects.create(code="raw-urad", name="Urad Gota", kind=ItemKind.RAW, base_unit=self.lb)
        item.aliases.create(alias="Urid Gota", source="Kitchen note")
        self.run_measures("Urid Gota,spoon,1.95,no,2026-09-16,note\n")
        self.assertTrue(ItemMeasure.objects.filter(item=item, name="spoon").exists())
