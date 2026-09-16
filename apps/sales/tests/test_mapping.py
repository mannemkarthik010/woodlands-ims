"""
The mapping queue.

What is being protected here is not the screen, it is the rule underneath it:
this system never guesses what a POS line means. Every test below is a way
that rule could quietly stop being true.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.catalog.models import Item, ItemKind, Unit, UnitKind
from apps.sales import services
from apps.sales.models import PosItem


def pos(name, **kwargs):
    return PosItem.objects.create(pos_name=name, group_key=services.normalise(name), **kwargs)


class GroupingTests(TestCase):
    """Grouping is automatic, because a wrong group is visible on the screen."""

    # Covers: FR-610.
    def test_one_dish_under_five_promotions_is_one_decision(self):
        for name in [
            "Masala Dosa",
            "$10 Masala Dosa",
            "DosaNights-Masala Dosa",
            "Weekday Lunch Masala Dosa",
            "NYPF - Masala Dosa",
        ]:
            pos(name)
        found = services.groups()
        self.assertEqual(len(found), 1)
        self.assertEqual(len(found[0].lines), 5)

    # Covers: FR-610.
    def test_the_biggest_groups_come_first(self):
        pos("Masala Dosa")
        pos("$10 Masala Dosa")
        pos("Filter Coffee")
        self.assertEqual(services.groups()[0].key, "masala dosa")

    # Covers: FR-610.
    def test_a_decided_group_leaves_the_queue(self):
        pos("Filter Coffee")
        self.assertEqual(len(services.groups()), 1)
        services.ignore_group("filter coffee")
        self.assertEqual(len(services.groups()), 0)
        self.assertEqual(len(services.groups(done=True)), 1)


class SizeTests(TestCase):
    """
    Three tubs of one chutney. Each sale counts as a quantity of 1 on the
    till, so without a quantity per sale the 16 oz and the 4 oz would deplete
    the same amount and the chutney figures would mean nothing.
    """

    def setUp(self):
        self.floz = Unit.objects.create(
            code="floz", name="Fluid ounce", kind=UnitKind.VOLUME, to_canonical=Decimal("29.5735")
        )
        self.each = Unit.objects.create(
            code="each", name="Each", kind=UnitKind.COUNT, to_canonical=Decimal("1")
        )
        self.chutney = Item.objects.create(
            code="prep-coconut-chutney",
            name="Coconut chutney",
            kind=ItemKind.PREPARED,
            base_unit=self.floz,
        )
        for name in ["Coconut Chutney 4 oz", "Coconut Chutney 8 oz", "Coconut Chutney 16 oz"]:
            pos(name)

    # Covers: FR-610.
    def test_each_tub_depletes_its_own_size(self):
        services.map_group("coconut chutney", item=self.chutney)
        sizes = {p.pos_name: p.quantity_per_sale for p in PosItem.objects.all()}
        self.assertEqual(sizes["Coconut Chutney 4 oz"], Decimal("4"))
        self.assertEqual(sizes["Coconut Chutney 16 oz"], Decimal("16"))

    # Covers: FR-203, FR-610.
    def test_a_size_is_converted_into_whatever_the_item_is_measured_in(self):
        """
        16 oz of chutney held in quarts is half a quart, not sixteen of
        anything. Carrying the number straight across would be a thirty-
        twofold error stated confidently, in a figure nobody re-checks.
        """
        quart = Unit.objects.create(
            code="qt", name="Quart", kind=UnitKind.VOLUME, to_canonical=Decimal("946.353")
        )
        in_quarts = Item.objects.create(
            code="prep-chutney-qt",
            name="Coconut chutney (quarts)",
            kind=ItemKind.PREPARED,
            base_unit=quart,
        )
        services.map_group("coconut chutney", item=in_quarts)
        sizes = {p.pos_name: p.quantity_per_sale for p in PosItem.objects.all()}
        self.assertEqual(sizes["Coconut Chutney 16 oz"], Decimal("0.5000"))
        self.assertEqual(sizes["Coconut Chutney 4 oz"], Decimal("0.1250"))

    # Covers: FR-610.
    def test_a_dish_is_one_dish_whatever_size_the_tub_is(self):
        """A bigger plate is a different dish, not more of one."""
        plate = Item.objects.create(
            code="dish-chutney-plate",
            name="Chutney plate",
            kind=ItemKind.DISH,
            base_unit=self.each,
            is_stocked=False,
        )
        services.map_group("coconut chutney", item=plate)
        for line in PosItem.objects.all():
            self.assertEqual(line.quantity_per_sale, Decimal("1"))

    # Covers: FR-610.
    def test_a_person_can_correct_a_size_and_the_correction_is_in_ounces(self):
        line = PosItem.objects.get(pos_name="Coconut Chutney 8 oz")
        services.map_group("coconut chutney", item=self.chutney, quantities={line.pk: Decimal("7.5")})
        line.refresh_from_db()
        self.assertEqual(line.quantity_per_sale, Decimal("7.5000"))


class DishCreationTests(TestCase):
    def setUp(self):
        self.each = Unit.objects.create(
            code="each", name="Each", kind=UnitKind.COUNT, to_canonical=Decimal("1")
        )

    # Covers: FR-201, FR-209, FR-601.
    def test_a_created_dish_is_a_dish_and_is_not_held_in_stock(self):
        dish = services.create_dish("Masala Dosa")
        self.assertEqual(dish.kind, ItemKind.DISH)
        self.assertFalse(dish.is_stocked)
        self.assertEqual(dish.base_unit, self.each)

    # Covers: FR-201.
    def test_two_dishes_with_the_same_name_do_not_collide(self):
        first = services.create_dish("Special")
        second = services.create_dish("Special")
        self.assertNotEqual(first.code, second.code)

    def test_a_dish_cannot_be_created_before_the_units_exist(self):
        Unit.objects.all().delete()
        with self.assertRaises(services.MappingError):
            services.create_dish("Masala Dosa")


class SuggestionTests(TestCase):
    """
    The menu spells the same food two ways -- "Chana Masala 16 oz" and
    "Channa Masala 4 oz" do not group together. Without a nudge, somebody
    creates two items for one food and every variance figure after that is
    quietly wrong.
    """

    def setUp(self):
        self.each = Unit.objects.create(
            code="each", name="Each", kind=UnitKind.COUNT, to_canonical=Decimal("1")
        )
        Item.objects.create(
            code="dish-chana-masala",
            name="Chana Masala",
            kind=ItemKind.DISH,
            base_unit=self.each,
            is_stocked=False,
        )

    # Covers: FR-610.
    def test_a_near_identical_name_is_offered(self):
        self.assertEqual([i.name for i in services.suggestions("channa masala")], ["Chana Masala"])

    # Covers: FR-610.
    def test_a_different_dish_is_not_offered(self):
        self.assertEqual(services.suggestions("masala dosa"), [])

    # Covers: FR-610.
    def test_a_suggestion_is_never_applied_on_its_own(self):
        """The whole point: suggesting is not deciding."""
        line = pos("Channa Masala 4 oz")
        services.suggestions("channa masala")
        line.refresh_from_db()
        self.assertIsNone(line.item_id)
        self.assertTrue(line.needs_attention)


class ScreenTests(TestCase):
    def setUp(self):
        self.each = Unit.objects.create(
            code="each", name="Each", kind=UnitKind.COUNT, to_canonical=Decimal("1")
        )
        self.user = get_user_model().objects.create_user("owner", password="pw")
        self.client.force_login(self.user)
        pos("Masala Dosa")
        pos("$10 Masala Dosa")

    def test_the_queue_lists_what_has_not_been_decided(self):
        page = self.client.get(reverse("mapping_queue"))
        self.assertContains(page, "Masala Dosa")
        self.assertContains(page, "2 POS lines")

    # Covers: FR-610.
    def test_creating_the_dish_settles_every_line_in_the_group(self):
        self.client.post(
            reverse("mapping_apply", args=["masala dosa"]),
            {"create": "1", "new_name": "Masala Dosa"},
        )
        dish = Item.objects.get(name="Masala Dosa")
        self.assertEqual(PosItem.objects.filter(item=dish).count(), 2)
        self.assertEqual(services.progress()["remaining"], 0)

    # Covers: FR-610.
    def test_choosing_an_existing_item_does_not_also_create_one(self):
        """
        Both the suggestion buttons and the create button live in one form.
        If the create flag were a hidden field, picking the suggestion would
        map to the existing dish AND create a duplicate of it.
        """
        existing = services.create_dish("Masala Dosa")
        self.client.post(
            reverse("mapping_apply", args=["masala dosa"]),
            {"item_id": existing.pk, "create": "1", "new_name": "Masala Dosa"},
        )
        self.assertEqual(Item.objects.filter(kind=ItemKind.DISH).count(), 1)
        self.assertEqual(PosItem.objects.filter(item=existing).count(), 2)

    # Covers: FR-610.
    def test_a_decision_records_who_made_it(self):
        self.client.post(
            reverse("mapping_apply", args=["masala dosa"]), {"create": "1", "new_name": "Masala Dosa"}
        )
        line = PosItem.objects.first()
        self.assertEqual(line.mapped_by, self.user)
        self.assertIsNotNone(line.mapped_at)

    # Covers: FR-610.
    def test_a_decision_can_be_undone(self):
        self.client.post(
            reverse("mapping_apply", args=["masala dosa"]), {"create": "1", "new_name": "Masala Dosa"}
        )
        self.client.post(reverse("mapping_reopen", args=["masala dosa"]))
        self.assertEqual(services.progress()["remaining"], 2)
        self.assertEqual(PosItem.objects.filter(item__isnull=False).count(), 0)

    # Covers: FR-610.
    def test_a_submission_with_no_choice_changes_nothing(self):
        response = self.client.post(reverse("mapping_apply", args=["masala dosa"]), {})
        self.assertContains(response, "Choose an item")
        self.assertEqual(PosItem.objects.filter(item__isnull=False).count(), 0)

    # Covers: FR-610.
    def test_a_nonsense_quantity_is_refused_and_nothing_is_mapped(self):
        line = PosItem.objects.first()
        response = self.client.post(
            reverse("mapping_apply", args=["masala dosa"]),
            {"create": "1", "new_name": "Masala Dosa", f"qty-{line.pk}": "twelve"},
        )
        self.assertContains(response, "not a quantity")
        self.assertEqual(Item.objects.filter(kind=ItemKind.DISH).count(), 0)

    # Covers: FR-1202, NFR-11.
    def test_signed_out_people_see_nothing(self):
        self.client.logout()
        for name, args in [
            ("mapping_queue", []),
            ("mapping_apply", ["masala dosa"]),
        ]:
            response = self.client.post(reverse(name, args=args))
            self.assertEqual(response.status_code, 302, name)
            self.assertIn("/login/", response["Location"])


class ReviewTests(TestCase):
    """
    The second look. What it must NOT say is as important as what it says: a
    review screen that cries wolf gets scrolled past, and then the real finding
    three cards down goes with it.
    """

    def setUp(self):
        self.each = Unit.objects.create(
            code="each", name="Each", kind=UnitKind.COUNT, to_canonical=Decimal("1")
        )
        self.user = get_user_model().objects.create_user("owner", password="pw")
        self.client.force_login(self.user)

    def _dish(self, code, name):
        return Item.objects.create(
            code=code, name=name, kind=ItemKind.DISH, base_unit=self.each, is_stocked=False
        )

    def kinds(self):
        return [f.kind for f in services.review()]

    # Covers: FR-203.
    def test_a_tub_counted_in_each_is_flagged(self):
        sambar = self._dish("dish-sambar", "Sambar")
        for name in ["Sambar 8oz", "Sambar 16 oz"]:
            pos(name, item=sambar)
        self.assertIn("sized", self.kinds())

    # Covers: FR-201.
    def test_two_spellings_of_one_food_are_flagged(self):
        self._dish("dish-raita", "Raita")
        self._dish("dish-raitha", "Raitha")
        self.assertIn("duplicate", self.kinds())

    def test_two_sizes_of_one_product_are_not_a_duplicate(self):
        """
        "Poori 2Pc" and "Poori 4Pc" are ninety per cent the same string and a
        hundred per cent different products. Where the numbers differ, the
        numbers are the point.
        """
        first = self._dish("dish-poori-2pc", "Poori 2Pc")
        second = self._dish("dish-poori-4pc", "Poori 4Pc")
        pos("Poori 2Pc", item=first)
        pos("Poori 4Pc", item=second)
        self.assertNotIn("duplicate", self.kinds())

    def test_demo_fixtures_are_not_flagged_against_the_real_catalogue(self):
        real = self._dish("dish-dosa-batter", "Dosa Batter")
        pos("Dosa Batter", item=real)
        Item.objects.create(
            code="DEMO-batter-dosa", name="Dosa batter", kind=ItemKind.PREPARED, base_unit=self.each
        )
        self.assertNotIn("duplicate", self.kinds())

    # Covers: FR-211.
    def test_an_item_nothing_points_at_is_flagged_as_left_over(self):
        self._dish("dish-abandoned", "Abandoned Thali")
        self.assertIn("orphan", self.kinds())

    # Covers: FR-202, FR-211.
    def test_merging_from_the_screen_keeps_the_old_name_and_moves_the_lines(self):
        keep = self._dish("dish-raita", "Raita")
        fold = self._dish("dish-raitha", "Raitha")
        line = pos("Raitha", item=fold)
        self.client.post(reverse("item_merge"), {"source": fold.pk, "target": keep.pk})
        line.refresh_from_db()
        fold.refresh_from_db()
        self.assertEqual(line.item, keep)
        self.assertFalse(fold.is_active)
        self.assertTrue(keep.aliases.filter(alias="Raitha").exists())

    # Covers: FR-203, FR-501.
    def test_converting_from_the_screen_makes_the_sizes_count(self):
        floz = Unit.objects.create(
            code="floz", name="Fluid ounce", kind=UnitKind.VOLUME, to_canonical=Decimal("29.5735")
        )
        sambar = self._dish("dish-sambar", "Sambar")
        pos("Sambar 16 oz", item=sambar)
        self.client.post(reverse("item_convert"), {"item": sambar.pk, "unit": floz.pk})
        sambar.refresh_from_db()
        self.assertEqual(sambar.kind, ItemKind.PREPARED)
        self.assertEqual(PosItem.objects.get().quantity_per_sale, Decimal("16"))
