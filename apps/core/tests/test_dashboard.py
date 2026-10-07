"""
The owners' dashboard and the navigation every page carries.

Today, for these tests, is Wednesday 7 October 2026 (open for dinner).
"""

from datetime import date, datetime
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Item, ItemKind, ParLevel, Unit, UnitKind
from apps.core import dashboard
from apps.core.models import Location, Role, User
from apps.sales import daily
from apps.sales.models import PosItem
from apps.sales.tests.test_daily_sales import report

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=LA)
TODAY = date(2026, 10, 7)


class DashboardTests(TestCase):
    def setUp(self):
        patcher = mock.patch.object(timezone, "now", return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.restaurant = Location.objects.create(
            code="restaurant", name="Restaurant", kind=Location.Kind.RESTAURANT
        )
        self.owner = User.objects.create_user("jaspinder", display_name="Jaspinder", role=Role.OWNER)
        self.cook = User.objects.create_user("meera", display_name="Meera")
        lb = Unit.objects.create(
            code="lb", name="Pound", kind=UnitKind.WEIGHT, to_canonical=Decimal("453.59237")
        )
        each = Unit.objects.create(code="each", name="Each", kind=UnitKind.COUNT, to_canonical=Decimal("1"))
        self.batter = Item.objects.create(code="b", name="Dosa Batter", kind=ItemKind.PREPARED, base_unit=lb)
        dosa = Item.objects.create(
            code="d", name="Masala Dosa", kind=ItemKind.DISH, base_unit=each, is_stocked=False
        )
        PosItem.objects.create(pos_name="Masala Dosa", item=dosa, portion_confirmed=True)

    def test_an_empty_restaurant_still_shows_a_dashboard(self):
        board = dashboard.build(TODAY)
        self.assertTrue(board.is_open)
        self.assertEqual(board.sales_day, date(2026, 10, 6))  # yesterday, Tuesday
        self.assertIsNone(board.sales)
        self.assertEqual(len(board.week), 7)

    def test_yesterdays_sales_and_the_week_come_from_the_sales_files(self):
        daily.import_day(
            report(("Masala Dosa", "Dosa", 27, 320)),
            filename="x.csv",
            business_date=date(2026, 10, 6),
            location=self.restaurant,
        )
        board = dashboard.build(TODAY)
        self.assertEqual(board.sales_preview.total_quantity, Decimal("27"))
        self.assertEqual(board.top_sellers, [("Masala Dosa", Decimal("27"))])
        self.assertEqual(board.week[-1].net, Decimal("320"))
        self.assertEqual(board.week[-1].pct, 100)

    def test_missing_sales_days_are_one_line_and_monday_does_not_count(self):
        board = dashboard.build(TODAY)
        missing = [t.text for t in board.todos if t.text.startswith("No sales file")]
        self.assertEqual(len(missing), 1)
        self.assertIn("6 days", missing[0])  # Wed–Sun and Tue; Monday is closed
        self.assertNotIn("Mon", missing[0])

    def test_something_out_of_stock_comes_first(self):
        ParLevel.objects.create(item=self.batter, location=self.restaurant, quantity=Decimal("32"))
        board = dashboard.build(TODAY)
        self.assertEqual(len(board.low), 1)
        self.assertEqual(board.todos[0].level, "bad")
        self.assertIn("Dosa Batter", board.todos[0].text)

    def test_an_unmatched_menu_button_and_an_unplanned_day_are_flagged(self):
        PosItem.objects.create(pos_name="Pumpkin Soup")
        texts = " ".join(t.text for t in dashboard.build(TODAY).todos)
        self.assertIn("1 menu button(s) not matched", texts)
        self.assertIn("Nobody is planned for today", texts)

    # --- screens and navigation --------------------------------------------

    def test_owners_land_on_the_dashboard_and_staff_on_their_tiles(self):
        self.client.force_login(self.owner)
        page = self.client.get(reverse("home"))
        self.assertContains(page, "Needs attention")
        self.assertContains(page, "Hours &amp; pay")
        self.assertContains(page, "Sign out")
        self.client.force_login(self.cook)
        page = self.client.get(reverse("home"))
        self.assertContains(page, "What are you doing?")
        self.assertContains(page, "My hours")
        self.assertNotContains(page, "Hours &amp; pay")
        self.assertNotContains(page, "Needs attention")

    def test_the_current_section_is_marked_in_the_navigation(self):
        self.client.force_login(self.owner)
        page = self.client.get(reverse("team_today")).content.decode()
        self.assertIn('nav__link nav__link--on" href="/team/" aria-current="page">Team', page)

    def test_signing_out_works_from_any_page(self):
        self.client.force_login(self.owner)
        self.client.post(reverse("logout"))
        self.assertEqual(self.client.get(reverse("home")).status_code, 302)

    def test_the_login_page_has_no_navigation(self):
        self.assertNotContains(self.client.get(reverse("login")), "nav__link")
