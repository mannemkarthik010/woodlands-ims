"""
Reading a typed-up recipe into the shape a cook expects.

The parser's job is to structure what is there. Its more important job is to
add nothing, and most of these tests are about that.
"""

from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

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


class ComponentTests(TestCase):
    """
    A component with its own recipe is named, not read out. Butter masala is
    built from kadai sauce and basic gravy; printing both inside it turns a
    one-page recipe into five, and a cook stops reading.
    """

    def setUp(self):
        from django.utils import timezone

        from apps.knowledge.models import Record

        Record.objects.create(
            title="Kadai Sauce",
            body="Chopped onion 15 onions · Oil 12 oz",
            approved_at=timezone.now(),
        )
        Record.objects.create(title="Basic Gravy", body="Onion 25 lb · Oil 12 oz", approved_at=timezone.now())

    def test_an_ingredient_that_has_its_own_record_is_marked(self):
        body = "Kadai sauce 1 large ladle · Basic gravy 3 large ladle · Heavy cream ½ gallon"
        marked = {i.name: i.component for i in recipe.parse(body)[0].ingredients}
        self.assertEqual(marked["Kadai sauce"], "Kadai Sauce")
        self.assertEqual(marked["Basic gravy"], "Basic Gravy")
        self.assertEqual(marked["Heavy cream"], "")

    def test_the_component_recipe_is_not_read_out_inside_the_one_asked_for(self):
        from apps.knowledge import engines
        from apps.knowledge.models import Record
        from apps.knowledge.retrieval import Hit

        butter = Record.objects.create(
            title="Butter Masala",
            body="Kadai sauce 1 large ladle · Basic gravy 3 large ladle",
            approved_at=timezone.now(),
        )
        from apps.knowledge.models import Passage

        passage = Passage.objects.create(record=butter, ordinal=0, text=butter.body)
        answer = engines.Passages().answer("butter masala", [Hit(passage=passage, score=5.0)])

        self.assertIn("Butter Masala", answer)
        self.assertIn("Kadai Sauce — has its own recipe", answer)
        self.assertNotIn("15 onions", answer)  # the kadai recipe itself stays where it is


class ScaleTests(TestCase):
    BUTTER = (
        "Kadai sauce 1 large ladle · Heavy cream ½ gallon\n\n"
        "**Yield:** 1 large soup chafer of concentrate\n\n"
        "**Per serving:** concentrate 1 large ladle · heavy cream 1 spoon · "
        "paneer 8–10 cubes · pinch each of sugar and methi"
    )

    def test_a_per_serving_figure_multiplies(self):
        sections = recipe.parse(self.BUTTER)
        text = recipe.as_scaled_text("Butter Masala", sections, 100)
        self.assertIn("For 100 servings", text)
        self.assertIn("100 large ladle", text)
        self.assertIn("800–1000 cubes", text)

    def test_something_that_is_not_a_number_is_not_multiplied(self):
        """A hundred times "a pinch" is not a number, and pretending it is would
        be worse than saying so."""
        section = recipe.per_serving(recipe.parse(self.BUTTER))
        scaled = recipe.scale([section], Decimal("100"))[0]
        pinch = [i for i in scaled.ingredients if "pinch" in i.name][0]
        self.assertEqual(pinch.quantity, "")

    def test_a_recipe_with_no_per_serving_figure_says_what_is_missing(self):
        """
        A base recipe cannot simply be multiplied: a hundred times two buckets
        is not a question anybody is asking.
        """
        sambar = "Toor dal 3 scoop · Salt 2 spoon\n\n**Yield:** 2 buckets"
        text = recipe.as_scaled_text("Sambar", recipe.parse(sambar), 200)
        self.assertIn("cannot be worked out", text)
        self.assertIn("2 buckets", text)
        self.assertNotIn("600 scoop", text)

    def test_the_batch_question_is_asked_even_when_the_serving_scales(self):
        text = recipe.as_scaled_text("Butter Masala", recipe.parse(self.BUTTER), 100)
        self.assertIn("how many servings that is has not been recorded", text)


class ServingsTests(TestCase):
    def test_a_number_of_people_is_read_out_of_the_question(self):
        from apps.knowledge.services import servings_wanted

        self.assertEqual(servings_wanted("butter masala for 100 people"), 100)
        self.assertEqual(servings_wanted("I need 40 portions of kurma"), 40)
        self.assertIsNone(servings_wanted("how do I make butter masala"))

    def test_an_absurd_number_is_ignored(self):
        from apps.knowledge.services import servings_wanted

        self.assertIsNone(servings_wanted("sambar for 99999 people"))
