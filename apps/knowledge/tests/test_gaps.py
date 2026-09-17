"""
What the kitchen has not written down.

The value of this report is entirely in what it does NOT ask for. A sheet that
asks the chef to weigh an ounce, or to say how much "to taste" is, gets put
down and not picked up again.
"""

from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.catalog.models import Item, ItemKind, ItemMeasure, MeasureKind, Unit, UnitKind
from apps.knowledge import gaps
from apps.knowledge.models import Record


class MeasureTests(TestCase):
    def test_a_kitchen_vessel_is_asked_about(self):
        self.assertEqual(gaps.kitchen_measure("3 scoop", "Toor dal"), "scoop")
        self.assertEqual(gaps.kitchen_measure("1 large ladle", "Kadai sauce"), "ladle")

    def test_a_real_unit_is_not(self):
        """Nobody needs to weigh an ounce."""
        self.assertEqual(gaps.kitchen_measure("16 oz", "Tamarind water"), "")
        self.assertEqual(gaps.kitchen_measure("½ gallon", "Heavy cream"), "")

    def test_a_judgement_is_not_a_measure(self):
        """
        Asking the chef to weigh a pinch is the sort of question that makes
        somebody stop answering the rest of the sheet.
        """
        self.assertEqual(gaps.kitchen_measure("to taste", "Salt"), "")
        self.assertEqual(gaps.kitchen_measure("a pinch", "Turmeric"), "")
        self.assertEqual(gaps.kitchen_measure("handful", "Curry leaves"), "")

    def test_counting_the_ingredient_is_not_measuring_it(self):
        """ "Chopped onion 15–20 onions" counts onions. There is no vessel."""
        self.assertEqual(gaps.kitchen_measure("15–20 onions", "Chopped onion"), "")


class SurveyTests(TestCase):
    def setUp(self):
        self.lb = Unit.objects.create(
            code="lb", name="Pound", kind=UnitKind.WEIGHT, to_canonical=Decimal("453.59237")
        )
        dal = Item.objects.create(code="raw-toor", name="Toor dal", kind=ItemKind.RAW, base_unit=self.lb)
        ItemMeasure.objects.create(
            item=dal,
            name="scoop",
            kind=MeasureKind.KITCHEN,
            quantity_in_base_units=Decimal("2"),
        )
        Record.objects.create(
            title="Sambar",
            body="Toor dal 3 scoop · Hing 1 spoon · Water ½ pot\n\n**Yield:** 2 buckets",
            approved_at=timezone.now(),
        )

    def test_a_recipe_with_no_servings_figure_is_listed(self):
        found = gaps.survey()[0]
        self.assertTrue(found.needs_servings)

    def test_recording_the_servings_figure_clears_it(self):
        Record.objects.update(servings_per_batch=40)
        self.assertFalse(gaps.survey()[0].needs_servings)

    def test_only_the_unweighed_measures_are_asked_about(self):
        """
        A scoop of toor dal has been weighed. A spoon of hing has not, and a
        pot has not been weighed at all.
        """
        wanted = gaps.survey()[0].unweighed
        self.assertIn("spoon of hing", wanted)
        self.assertIn("pot of water", wanted)
        self.assertFalse([w for w in wanted if "toor dal" in w])

    def test_a_document_of_quantities_is_reported_as_having_no_method(self):
        self.assertTrue(gaps.survey()[0].needs_method)


class VesselTests(TestCase):
    """
    There is one scoop and one spoon in that kitchen. The vessel holds a fixed
    volume; what that volume weighs depends on what is in it.
    """

    def setUp(self):
        from apps.catalog.models import Vessel
        from apps.knowledge import format as recipe

        self.recipe = recipe
        self.lb = Unit.objects.create(
            code="lb", name="Pound", kind=UnitKind.WEIGHT, to_canonical=Decimal("453.59237")
        )
        dal = Item.objects.create(code="raw-toor", name="Toor dal", kind=ItemKind.RAW, base_unit=self.lb)
        ItemMeasure.objects.create(
            item=dal,
            name="scoop",
            kind=MeasureKind.KITCHEN,
            quantity_in_base_units=Decimal("2"),
        )
        Item.objects.create(code="raw-hing", name="Hing", kind=ItemKind.RAW, base_unit=self.lb)
        Vessel.objects.create(name="spoon", volume_ml=Decimal("60"))

    # Covers: FR-205.
    def test_a_weighed_ingredient_shows_its_weight(self):
        self.assertEqual(self.recipe.measured("Toor dal", "1 scoop"), "2 lb")

    # Covers: FR-205.
    def test_an_unweighed_one_falls_back_to_what_the_vessel_holds(self):
        """
        And says so. "1 spoon (2.03 fl oz)" is true of every spoon of
        everything; "1 spoon (2 oz)" would be a weight nobody measured, and a
        cook could not tell the two apart.
        """
        self.assertEqual(self.recipe.measured("Hing", "1 spoon"), "2.03 fl oz")

    def test_an_unmeasured_vessel_shows_nothing_at_all(self):
        from apps.catalog.models import Vessel

        Vessel.objects.all().delete()
        self.assertEqual(self.recipe.measured("Hing", "1 spoon"), "")

    def test_a_measured_vessel_stops_being_asked_about(self):
        from apps.catalog.models import Vessel

        Record.objects.create(
            title="Sambar", body="Hing 1 spoon · Toor dal 3 scoop", approved_at=timezone.now()
        )
        wanted = [name for name, _ in gaps.vessels_wanted(gaps.survey())]
        self.assertNotIn("spoon", wanted)
        Vessel.objects.filter(name="spoon").update(volume_ml=None)
        wanted = [name for name, _ in gaps.vessels_wanted(gaps.survey())]
        self.assertIn("spoon", wanted)

    def test_only_bulk_measures_are_asked_to_be_weighed_separately(self):
        """A scoop of chana dal decides pounds. A spoon of fennel decides nothing."""
        Record.objects.create(
            title="Something",
            body="Chana dal 1 scoop · Fennel 1 spoon",
            approved_at=timezone.now(),
        )
        wanted = gaps.weights_wanted(gaps.survey())
        self.assertIn("scoop of chana dal", wanted)
        self.assertFalse([w for w in wanted if "fennel" in w])
