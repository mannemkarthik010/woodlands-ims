"""
Reading a typed-up recipe into the shape a cook expects.

The parser's job is to structure what is there. Its more important job is to
add nothing, and most of these tests are about that.
"""

from decimal import Decimal

from django.test import TestCase

from apps.catalog.models import Item, ItemKind, ItemMeasure, MeasureKind, Unit, UnitKind
from apps.knowledge import format as recipe

SAMBAR = (
    "Toor dal 3 scoop · Moong dal 1 scoop · Water ½ pot · Salt 2 spoon · "
    "Tamarind water 16 oz · Jaggery water 2 spoon\n\n"
    "**Tempering:** Oil 6 oz · Mustard seeds 1 spoon · Curry leaves handful\n\n"
    "**Yield:** 2 buckets"
)


class ParseTests(TestCase):
    def setUp(self):
        self.lb = Unit.objects.create(
            code="lb", name="Pound", kind=UnitKind.WEIGHT, to_canonical=Decimal("453.59237")
        )
        self.dal = Item.objects.create(code="raw-toor", name="Toor dal", kind=ItemKind.RAW, base_unit=self.lb)
        ItemMeasure.objects.create(
            item=self.dal,
            name="scoop",
            kind=MeasureKind.KITCHEN,
            quantity_in_base_units=Decimal("2"),
        )

    def test_an_ingredient_is_split_from_its_quantity(self):
        first = recipe.parse(SAMBAR)[0].ingredients[0]
        self.assertEqual(first.name, "Toor dal")
        self.assertEqual(first.quantity, "3 scoop")

    def test_a_bold_label_mid_paragraph_starts_a_section(self):
        """
        The document was typed as prose, so "**Tempering:**" sits in the middle
        of a line. Reading only line starts put the tempering oil into the main
        ingredient list, six lines from where it belongs.
        """
        headings = [s.heading for s in recipe.parse(SAMBAR)]
        self.assertIn("Tempering", headings)
        self.assertIn("Yield", headings)

    def test_the_tempering_ingredients_land_under_tempering(self):
        sections = {s.heading: s for s in recipe.parse(SAMBAR)}
        names = [i.name for i in sections["Tempering"].ingredients]
        self.assertEqual(names, ["Oil", "Mustard seeds", "Curry leaves"])

    # Covers: FR-205.
    def test_a_weighed_measure_is_shown_beside_the_chefs_own_words(self):
        """3 scoop stays 3 scoop. What a scoop weighs is added, not substituted."""
        first = recipe.parse(SAMBAR)[0].ingredients[0]
        self.assertEqual(first.quantity, "3 scoop")
        self.assertEqual(first.weighed, "6 lb")

    def test_an_unweighed_measure_shows_nothing(self):
        """
        Nobody has weighed a pot. Printing a number for it would be
        indistinguishable on the page from the ones that were measured.
        """
        water = [i for i in recipe.parse(SAMBAR)[0].ingredients if i.name == "Water"][0]
        self.assertEqual(water.quantity, "½ pot")
        self.assertEqual(water.weighed, "")

    def test_a_small_weight_is_shown_in_ounces(self):
        """0.08 lb of mustard seed is arithmetic; 1.3 oz is an instruction."""
        seed = Item.objects.create(
            code="raw-mustard", name="Mustard seeds", kind=ItemKind.RAW, base_unit=self.lb
        )
        ItemMeasure.objects.create(
            item=seed,
            name="spoon",
            kind=MeasureKind.KITCHEN,
            quantity_in_base_units=Decimal("0.0812"),
        )
        sections = {s.heading: s for s in recipe.parse(SAMBAR)}
        mustard = [i for i in sections["Tempering"].ingredients if i.name == "Mustard seeds"][0]
        self.assertEqual(mustard.weighed, "1.3 oz")

    def test_a_bracketed_aside_is_kept_and_not_read_as_a_quantity(self):
        parsed = recipe.parse("Methi 1 cap (approx 6–8 oz)")[0].ingredients[0]
        self.assertEqual(parsed.name, "Methi")
        self.assertEqual(parsed.quantity, "1 cap")
        self.assertEqual(parsed.aside, "approx 6–8 oz")


class MethodTests(TestCase):
    def test_a_recipe_with_no_steps_is_said_to_have_none(self):
        """
        Ramesh's document is quantities only. Every model ever trained could
        write a plausible method for sambar, and every one would be guessing at
        how THIS kitchen makes it. The gap is shown as a gap.
        """
        sections = recipe.parse(SAMBAR)
        self.assertFalse(recipe.has_method(sections))
        text = recipe.as_text("Sambar", sections)
        self.assertIn("Method", text)
        self.assertIn("Not recorded", text)

    def test_a_recipe_with_steps_is_not_told_it_has_none(self):
        body = (
            "Oil 8 oz · Mustard seeds 1 spoon\n\n"
            "Heat the oil until the mustard seeds crackle, then add the onions "
            "and cook until they are soft."
        )
        sections = recipe.parse(body)
        self.assertTrue(recipe.has_method(sections))
        self.assertNotIn("Not recorded", recipe.as_text("Something", sections))

    def test_the_text_rendering_keeps_the_sections(self):
        text = recipe.as_text("Sambar", recipe.parse(SAMBAR))
        self.assertIn("Ingredients", text)
        self.assertIn("Tempering", text)
        self.assertIn("Yield", text)
        self.assertIn("2 buckets", text)
