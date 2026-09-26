"""
The owners decide what is on each count: they move items between the daily,
weekly and monthly lists, add what the system does not know yet, and stop
what is no longer bought -- all without the technical admin.
"""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import CountEvery, Item, ItemKind, ItemMeasure, MeasureKind, Unit, UnitKind
from apps.catalog.services import ItemError, add_item, move_to_list
from apps.core.models import Location, Role, User
from apps.stock.models import StockCount
from apps.stock.services import build_count_sheet


class ListsTestCase(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner", password="pw", role=Role.OWNER)
        self.cook = User.objects.create_user("tablet", password="pw", role=Role.KITCHEN)
        self.client.force_login(self.owner)
        self.lb = Unit.objects.create(
            code="lb", name="Pound", kind=UnitKind.WEIGHT, to_canonical=Decimal("453.59237")
        )
        self.each = Unit.objects.create(
            code="each", name="Each", kind=UnitKind.COUNT, to_canonical=Decimal("1")
        )
        self.restaurant = Location.objects.create(code="r", name="Restaurant", kind=Location.Kind.RESTAURANT)
        self.toor = Item.objects.create(code="toor", name="Toor Dal", kind=ItemKind.RAW, base_unit=self.lb)
        self.sambar = Item.objects.create(
            code="sambar", name="Sambar", kind=ItemKind.PREPARED, base_unit=self.lb
        )
        self.dosa = Item.objects.create(
            code="masala-dosa", name="Masala Dosa", kind=ItemKind.DISH, base_unit=self.each, is_stocked=False
        )

    def add(self, **kw):
        base = {"name": "Onions", "what": "VEGETABLE", "base_unit": self.lb, "count_every": CountEvery.WEEKLY}
        return add_item(**(base | kw), user=self.owner)

    def daily(self):
        count = StockCount.objects.create(
            location=self.restaurant, cadence=StockCount.Cadence.DAILY, counted_at=timezone.now()
        )
        build_count_sheet(count)
        return sorted(count.lines.values_list("item__name", flat=True))


class AddItemTests(ListsTestCase):
    # Covers: FR-201, FR-204.
    def test_a_grocery_that_comes_by_the_case_can_be_counted_in_cases_at_once(self):
        item = add_item(
            name="Urad Dal (whole)",
            what="RAW",
            base_unit=self.lb,
            count_every=CountEvery.MONTHLY,
            pack_name="case",
            pack_quantity=Decimal("40"),
            user=self.owner,
        )
        self.assertEqual(
            (item.kind, item.count_every, item.code), (ItemKind.RAW, CountEvery.MONTHLY, "raw-urad-dal-whole")
        )
        m = item.measures.get()
        self.assertEqual(
            (m.name, m.kind, m.quantity_in_base_units), ("case", MeasureKind.PURCHASE, Decimal("40"))
        )
        self.assertTrue(item.aliases.filter(alias="Urad Dal (whole)").exists())

    def test_a_vegetable_is_fresh_produce_and_something_made_here_is_kept_in_a_container(self):
        onions = self.add()
        self.assertEqual(
            (onions.kind, onions.category.name, onions.layer), (ItemKind.RAW, "Fresh produce", 1)
        )
        batter = add_item(
            name="Dosa batter",
            what="PREPARED",
            base_unit=self.lb,
            count_every=CountEvery.DAILY,
            pack_name="bucket",
            pack_quantity=Decimal("30"),
            user=self.owner,
        )
        self.assertEqual((batter.layer, batter.measures.get().kind), (2, MeasureKind.KITCHEN))
        self.assertIn("Dosa batter", self.daily())

    def test_the_same_name_in_any_form_is_never_added_twice(self):
        for name in ("toor dal", "TOOR  DAL", "Toor-Dal"):
            with self.subTest(name=name), self.assertRaises(ItemError) as caught:
                self.add(name=name, what="RAW")
            self.assertEqual(caught.exception.existing, self.toor)
        self.assertEqual(Item.objects.filter(name__icontains="toor").count(), 1)

    def test_a_stopped_item_is_offered_back_rather_than_added_again(self):
        self.toor.is_active = False
        self.toor.save()
        with self.assertRaisesMessage(ItemError, "bring it back"):
            self.add(name="Toor dal", what="RAW")

    def test_sample_data_never_blocks_the_real_item(self):
        Item.objects.create(
            code="DEMO-onion", name="Onions", kind=ItemKind.RAW, base_unit=self.lb, is_active=False
        )
        onions = self.add()
        self.assertEqual(onions.code, "veg-onions")

    def test_a_pack_needs_both_its_name_and_its_size(self):
        with self.assertRaisesMessage(ItemError, "both the name"):
            self.add(pack_name="case")
        with self.assertRaisesMessage(ItemError, "both the name"):
            self.add(pack_quantity=Decimal("10"))
        with self.assertRaises(ItemError):
            self.add(name="  ")

    def test_dishes_cannot_be_put_on_a_count(self):
        with self.assertRaisesMessage(ItemError, "Dishes are not counted"):
            move_to_list(self.dosa, CountEvery.DAILY)


class ScreenTests(ListsTestCase):
    def test_only_owners_manage_the_lists(self):
        self.client.force_login(self.cook)
        for name, args in (("count_lists", []), ("item_add", [])):
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 403)
        self.assertEqual(
            self.client.post(
                reverse("count_list_move", args=[self.toor.pk]), {"count_every": "DAILY"}
            ).status_code,
            403,
        )
        self.assertNotContains(self.client.get(reverse("count_home")), reverse("count_lists"))
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("count_home")), reverse("count_lists"))

    def test_the_lists_show_every_item_on_its_list_and_never_a_dish(self):
        page = self.client.get(reverse("count_lists"))
        sections = {s["label"]: [i.name for i in s["items"]] for s in page.context["sections"]}
        self.assertEqual(sections["Daily"], ["Sambar"])
        self.assertEqual(sections["Monthly"], ["Toor Dal"])
        self.assertNotContains(page, "Masala Dosa")

    def test_moving_an_item_changes_the_next_count(self):
        response = self.client.post(reverse("count_list_move", args=[self.toor.pk]), {"count_every": "DAILY"})
        self.assertContains(response, "Toor Dal is now on the daily list.")
        self.assertEqual(self.daily(), ["Sambar", "Toor Dal"])

    def test_add_from_the_form_and_add_another_like_it(self):
        response = self.client.post(
            reverse("item_add"),
            {
                "name": "Tomatoes",
                "what": "VEGETABLE",
                "base_unit": "lb",
                "count_every": "WEEKLY",
                "pack_name": "case",
                "pack_quantity": "25",
                "again": "1",
            },
        )
        self.assertRedirects(response, f"{reverse('item_add')}?what=VEGETABLE")
        self.assertEqual(Item.objects.get(name="Tomatoes").count_every, CountEvery.WEEKLY)
        # The next form starts as a weekly vegetable, ready for the next one.
        form = self.client.get(response.url).context["form"]
        self.assertEqual((form.initial["what"], form.initial["count_every"]), ("VEGETABLE", "WEEKLY"))

    def test_a_duplicate_on_the_form_is_explained(self):
        response = self.client.post(
            reverse("item_add"),
            {"name": "sambar", "what": "PREPARED", "base_unit": "lb", "count_every": "DAILY"},
        )
        self.assertContains(response, "Sambar is already on the system.")
        self.assertEqual(Item.objects.filter(name__iexact="sambar").count(), 1)

    def test_stop_using_and_bring_back(self):
        response = self.client.post(reverse("item_stop", args=[self.toor.pk]))
        self.assertContains(response, "Toor Dal is no longer used.")
        self.toor.refresh_from_db()
        self.assertFalse(self.toor.is_active)
        self.assertNotIn("Toor Dal", [i.name for s in response.context["sections"] for i in s["items"]])

        self.client.post(reverse("item_bring_back", args=[self.toor.pk]))
        self.toor.refresh_from_db()
        self.assertEqual((self.toor.is_active, self.toor.count_every), (True, CountEvery.MONTHLY))

    def test_a_measure_shows_beside_the_item(self):
        ItemMeasure.objects.create(item=self.toor, name="case", quantity_in_base_units=Decimal("40"))
        self.assertContains(self.client.get(reverse("count_lists")), "case = 40 lb")
