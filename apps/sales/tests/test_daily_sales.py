"""
A day's sales, from Shift4's "Sales Summary by Item" CSV to stock.

The file here is made up, in the exact format of a real export (checked
against one on 28 September 2026) -- the restaurant's real sales stay out of
the repository.
"""

from datetime import date
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Item, ItemKind, Recipe, RecipeLine, Unit, UnitKind
from apps.core.models import Location, Role, User
from apps.sales import daily
from apps.sales.models import PosItem, SalesImport, SalesImportStatus
from apps.sales.services import set_portion
from apps.sales.shift4 import ReportError, read_sales_summary
from apps.stock.models import MovementType, StockMovement
from apps.stock.services import on_hand

HEADER = (
    "Item,Revenue Class,Department,Default Price,Qty,Total Cost,% of Total,Discounts,"
    "Avg. Sale Price,Gross Sales,Net Sales,Net Sales w/o Mods,% of Total\n"
)


def report(*lines, total=None) -> bytes:
    """A Shift4 export: lines of (name, department, qty, net)."""
    body = "".join(f"{n},Food,{d},10,{q},0,0,0,10,{net},{net},,1\n" for n, d, q, net in lines)
    qty = total if total is not None else sum(q for _, _, q, _ in lines)
    return (HEADER + body + f"Totals:,,,,{qty},0,100,0,10,1,1,,100\n").encode()


DAY = report(
    ("Masala Dosa", "Dosa", 27, 320),
    ("Sambar 16 oz", "Side Orders", 3, 30),
    ("Dosa Batter", "Side Orders", 2, 20),
    ("Mango Lassi", "Beverages", 26, 136.5),
)


class SalesTestCase(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner", password="pw", role=Role.OWNER)
        self.lb = Unit.objects.create(
            code="lb", name="Pound", kind=UnitKind.WEIGHT, to_canonical=Decimal("453.59237")
        )
        each = Unit.objects.create(code="each", name="Each", kind=UnitKind.COUNT, to_canonical=Decimal("1"))
        self.restaurant = Location.objects.create(code="r", name="Restaurant", kind=Location.Kind.RESTAURANT)

        def item(code, name, kind, unit=None, **kw):
            return Item.objects.create(code=code, name=name, kind=kind, base_unit=unit or self.lb, **kw)

        self.dosa = item("masala-dosa", "Masala Dosa", ItemKind.DISH, each, is_stocked=False)
        self.lassi = item("mango-lassi", "Mango Lassi", ItemKind.DISH, each, is_stocked=False)
        self.sambar = item("sambar", "Sambar", ItemKind.PREPARED)
        self.batter = item("dosa-batter", "Dosa Batter", ItemKind.PREPARED)
        # As mapped on the menu mapping screen.
        PosItem.objects.create(pos_name="Masala Dosa", item=self.dosa, portion_confirmed=True)
        PosItem.objects.create(pos_name="Mango Lassi", item=self.lassi, portion_confirmed=True)
        PosItem.objects.create(
            pos_name="Sambar 16 oz", item=self.sambar, quantity_per_sale=Decimal("1"), portion_confirmed=True
        )
        self.batter_button = PosItem.objects.create(pos_name="Dosa Batter", item=self.batter)  # no size on it

    def load(self, data=DAY, day=date(2026, 9, 27), **kw):
        return daily.import_day(
            data, filename="sales.csv", business_date=day, location=self.restaurant, user=self.owner, **kw
        )


class ReadingTests(TestCase):
    # Covers: FR-610.
    def test_a_real_shaped_export_is_read_and_its_total_checked(self):
        r = read_sales_summary(DAY)
        self.assertEqual((len(r.rows), r.total_quantity), (4, Decimal("58")))
        self.assertEqual(
            (r.rows[0].name, r.rows[0].department, r.rows[0].quantity), ("Masala Dosa", "Dosa", Decimal("27"))
        )

    def test_a_cut_off_file_is_refused(self):
        with self.assertRaisesMessage(ReportError, "may have been cut off"):
            read_sales_summary(report(("Masala Dosa", "Dosa", 27, 320), total=58))

    def test_the_wrong_report_or_an_empty_file_is_refused(self):
        with self.assertRaisesMessage(ReportError, "Sales Summary by Item"):
            read_sales_summary(b"Department,Item Name,Item Price\nDosa,Masala Dosa,11.75\n")
        with self.assertRaisesMessage(ReportError, "empty"):
            read_sales_summary(b"  ")
        with self.assertRaisesMessage(ReportError, "no items"):
            read_sales_summary(HEADER.encode())

    def test_excel_s_byte_order_mark_does_not_matter(self):
        self.assertEqual(len(read_sales_summary(b"\xef\xbb\xbf" + DAY).rows), 4)


class ImportTests(SalesTestCase):
    # Covers: FR-610, FR-611.
    def test_a_day_is_read_every_line_matched_and_the_file_fingerprinted(self):
        s = self.load()
        self.assertEqual((s.status, s.rows_read, s.rows_unmapped), (SalesImportStatus.IMPORTED, 4, 0))
        self.assertEqual(len(s.source_sha256), 64)
        self.assertEqual(s.lines.get(pos_item__pos_name="Masala Dosa").quantity_sold, Decimal("27"))

    def test_the_same_file_is_never_imported_twice(self):
        self.load()
        with self.assertRaisesMessage(daily.SalesError, "already imported, for Sun 27 Sep"):
            self.load(day=date(2026, 9, 26))

    def test_a_second_file_for_a_day_asks_before_replacing_and_replacing_undoes_the_first(self):
        first = self.load()
        set_portion(self.batter_button, ounces=Decimal("32"))
        daily.post_day(first)
        self.assertEqual(on_hand(self.sambar, self.restaurant), Decimal("-3"))

        corrected = report(("Masala Dosa", "Dosa", 30, 350), ("Sambar 16 oz", "Side Orders", 1, 10))
        with self.assertRaises(daily.DayAlreadyImported):
            self.load(corrected)
        second = self.load(corrected, replace=True)

        first.refresh_from_db()
        self.assertEqual(first.status, SalesImportStatus.SUPERSEDED)
        self.assertEqual(
            on_hand(self.sambar, self.restaurant), Decimal("0")
        )  # the first day's taking-out undone
        self.assertEqual(on_hand(self.batter, self.restaurant), Decimal("0"))
        daily.post_day(second)
        self.assertEqual(on_hand(self.sambar, self.restaurant), Decimal("-1"))

    def test_a_new_button_is_added_unmatched_and_blocks_recording(self):
        s = self.load(report(("Masala Dosa", "Dosa", 1, 11), ("Mushroom Dosa", "Dosa", 2, 26)))
        self.assertEqual(s.rows_unmapped, 1)
        self.assertTrue(PosItem.objects.get(pos_name="Mushroom Dosa").needs_attention)
        with self.assertRaisesMessage(daily.SalesError, "not matched to a dish"):
            daily.post_day(s)
        self.assertFalse(StockMovement.objects.exists())

    def test_the_future_is_refused(self):
        with self.assertRaisesMessage(daily.SalesError, "hasn't happened"):
            self.load(day=date(2099, 1, 1))


class RecordingTests(SalesTestCase):
    # Covers: FR-602, FR-610.
    def test_tubs_come_out_dishes_without_a_recipe_are_listed_and_an_unsized_tub_waits(self):
        p = daily.preview(self.load())
        self.assertEqual(p.takes_out, {self.sambar: Decimal("3")})
        self.assertEqual(
            [(d.name, q) for d, q in p.no_recipe],
            [("Masala Dosa", Decimal("27")), ("Mango Lassi", Decimal("26"))],
        )
        self.assertEqual([line.pos_item.pos_name for line in p.needs_portion], ["Dosa Batter"])
        self.assertEqual(p.total_quantity, Decimal("58"))
        self.assertEqual(p.total_net, Decimal("506.5"))

    def test_once_its_portion_is_known_the_tub_comes_out_too(self):
        set_portion(self.batter_button, ounces=Decimal("32"))
        self.assertEqual(daily.preview(self.load()).takes_out[self.batter], Decimal("4"))  # 2 tubs x 2 lb

    def test_a_dish_with_a_recipe_takes_out_what_it_is_made_of(self):
        recipe = Recipe.objects.create(item=self.dosa, yield_quantity=Decimal("1"))
        RecipeLine.objects.create(recipe=recipe, component=self.batter, quantity=Decimal("0.5"))
        self.assertEqual(daily.preview(self.load()).takes_out[self.batter], Decimal("13.5"))  # 27 x 0.5 lb

    def test_recording_is_once_dated_the_business_day_and_traceable(self):
        s = self.load()
        daily.post_day(s, user=self.owner)
        m = StockMovement.objects.get(item=self.sambar)
        self.assertEqual(
            (m.movement_type, m.quantity, m.source_type, m.source_id),
            (MovementType.SALE_DEPLETION, Decimal("-3"), "sales.salesimport", s.pk),
        )
        self.assertEqual(timezone.localtime(m.occurred_at).date(), date(2026, 9, 27))
        self.assertEqual(m.note, "Sales 2026-09-27")
        with self.assertRaisesMessage(daily.SalesError, "already recorded"):
            daily.post_day(s)

    def test_a_missing_day_shows(self):
        self.load(day=date(2026, 9, 26))
        days = dict(daily.days(self.restaurant, last=3, today=date(2026, 9, 28)))
        self.assertIsNone(days[date(2026, 9, 27)])
        self.assertEqual(days[date(2026, 9, 26)].items_sold, Decimal("58"))  # items, not the 4 menu lines


class ScreenTests(SalesTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def upload(self, data=DAY, day="2026-09-27", **extra):
        return self.client.post(
            reverse("sales_home"),
            {"business_date": day, "file": SimpleUploadedFile("sales.csv", data), **extra},
        )

    def test_only_owners_see_sales(self):
        self.client.force_login(User.objects.create_user("tablet", role=Role.KITCHEN))
        self.assertEqual(self.client.get(reverse("sales_home")).status_code, 403)

    def test_upload_check_give_the_portion_record(self):
        response = self.upload()
        s = SalesImport.objects.get()
        self.assertRedirects(response, reverse("sales_day", args=[s.pk]))
        page = self.client.get(response.url)
        self.assertContains(page, "How much is one sale?")
        self.assertContains(page, "2 dishes sold have no recipe yet")

        self.client.post(reverse("sales_portion", args=[s.pk, self.batter_button.pk]), {"ounces": "32"})
        self.batter_button.refresh_from_db()
        self.assertEqual(
            (self.batter_button.quantity_per_sale, self.batter_button.portion_confirmed), (Decimal("2"), True)
        )

        self.client.post(reverse("sales_day", args=[s.pk]))
        s.refresh_from_db()
        self.assertEqual(s.status, SalesImportStatus.POSTED)
        self.assertEqual(on_hand(self.batter, self.restaurant), Decimal("-4"))

    def test_a_second_file_for_the_day_offers_to_replace(self):
        self.upload()
        again = self.upload(report(("Masala Dosa", "Dosa", 1, 11)))
        self.assertContains(again, "already has a sales file")
        self.assertContains(again, 'name="replace"')
        self.assertEqual(SalesImport.objects.count(), 1)
        self.upload(report(("Masala Dosa", "Dosa", 1, 11)), replace="1")
        self.assertEqual(SalesImport.objects.exclude(status=SalesImportStatus.SUPERSEDED).get().rows_read, 1)

    def test_a_bad_file_is_explained(self):
        response = self.upload(b"Department,Item Name\n")
        self.assertContains(response, "Sales Summary by Item")
        self.assertFalse(SalesImport.objects.exists())
