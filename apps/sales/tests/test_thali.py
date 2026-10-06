"""
The thali of the day: what the owner says was in it, and what each plate takes out.
"""

import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse

from apps.catalog.models import Item, ItemKind
from apps.sales import daily, inbound, thali
from apps.sales.models import PosItem, ThaliDay
from apps.sales.services import set_portion
from apps.sales.tests.test_daily_sales import SalesTestCase, report

SUN = date(2026, 9, 27)


class ThaliTests(SalesTestCase):
    def setUp(self):
        super().setUp()
        each = self.dosa.base_unit
        self.thali_dish = Item.objects.create(
            code="thali",
            name="Thali",
            kind=ItemKind.DISH,
            base_unit=each,
            is_stocked=False,
            changes_daily=True,
        )
        self.dhal = Item.objects.create(
            code="dhal", name="Dhal Fry", kind=ItemKind.DISH, base_unit=each, is_stocked=False
        )
        self.kurma = Item.objects.create(
            code="kurma", name="Vegetable Kurma", kind=ItemKind.DISH, base_unit=each
        )
        self.rasam = Item.objects.create(
            code="rasam", name="Rasam", kind=ItemKind.PREPARED, base_unit=self.lb
        )
        PosItem.objects.create(pos_name="Thali", item=self.thali_dish, portion_confirmed=True)
        set_portion(self.batter_button, ounces=32)

    # --- what the owner types ----------------------------------------------

    def test_what_the_owner_types_is_split_into_dishes(self):
        self.assertEqual(
            thali.phrases("Dal fry, aloo gobi and veg kurma;\nrasam + payasam, rasam"),
            ["Dal fry", "aloo gobi", "veg kurma", "rasam", "payasam"],
        )

    def test_matching_by_name_handles_kitchen_spelling_and_says_when_it_cannot(self):
        found = thali.match_by_name(
            ["dal fry", "veg kurma", "rasam", "pumpkin halwa"], list(thali.candidates())
        )
        self.assertEqual(
            [s.item.name if s.item else None for s in found], ["Dhal Fry", "Vegetable Kurma", "Rasam", None]
        )

    def test_where_menu_and_kitchen_share_a_name_the_kitchens_is_offered(self):
        bowl = Item.objects.create(
            code="rasam-bowl",
            name="Rasam",
            kind=ItemKind.DISH,
            base_unit=self.dosa.base_unit,
            is_stocked=False,
        )
        offered = thali.candidates()
        self.assertIn(self.rasam, offered)
        self.assertNotIn(bowl, offered)

    def test_the_thali_itself_is_never_offered_as_something_in_the_thali(self):
        self.assertNotIn(self.thali_dish, thali.candidates())

    @override_settings(KNOWLEDGE_CONSENT=True, KNOWLEDGE_API_KEY="k", KNOWLEDGE_MODEL="claude-opus-5-5")
    def test_with_consent_claude_chooses_from_the_list_and_only_from_it(self):
        reply = {"matches": [{"said": "dal", "item": "Dhal Fry"}, {"said": "mystery", "item": ""}]}
        response = SimpleNamespace(
            stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(reply))]
        )
        with patch("anthropic.Anthropic") as client:
            client.return_value.beta.messages.create.return_value = response
            found, method = thali.suggest("dal, mystery")
        self.assertEqual(method, "claude")
        self.assertEqual([s.item for s in found], [self.dhal, None])
        schema = client.return_value.beta.messages.create.call_args.kwargs["output_config"]["format"][
            "schema"
        ]
        self.assertIn("Dhal Fry", schema["properties"]["matches"]["items"]["properties"]["item"]["enum"])

    @override_settings(KNOWLEDGE_CONSENT=True, KNOWLEDGE_API_KEY="k")
    def test_if_claude_fails_the_names_still_match(self):
        with patch("anthropic.Anthropic") as client:
            client.return_value.beta.messages.create.side_effect = ConnectionError("down")
            found, method = thali.suggest("rasam")
        self.assertEqual((method, found[0].item), ("names", self.rasam))

    @override_settings(KNOWLEDGE_CONSENT=False, KNOWLEDGE_API_KEY="k")
    def test_without_consent_nothing_is_sent(self):
        with patch("anthropic.Anthropic") as client:
            _, method = thali.suggest("rasam")
        self.assertEqual(method, "names")
        client.assert_not_called()

    # --- what a thali sold takes out ----------------------------------------

    def test_each_thali_sold_takes_out_that_days_plate(self):
        thali.save_day(
            SUN, [(self.sambar, Decimal("4")), (self.rasam, Decimal("8")), (self.dhal, Decimal("3"))]
        )
        s = self.load(report(("Thali", "Thali", 10, 150)))
        preview = daily.preview(s)
        self.assertEqual(preview.takes_out[self.sambar], Decimal("2.5"))  # 10 x 4 oz
        self.assertEqual(preview.takes_out[self.rasam], Decimal("5"))  # 10 x 8 oz
        self.assertNotIn(self.dhal, preview.takes_out)  # a dish with no recipe yet takes nothing
        self.assertEqual(preview.no_thali, [])

    def test_a_day_with_no_thali_entered_says_so_and_the_email_waits_for_it(self):
        s = self.load(report(("Thali", "Thali", 10, 150)))
        self.assertEqual(daily.preview(s).no_thali, [(self.thali_dish, Decimal("10"))])

        outcome = inbound.receive(
            report(("Thali", "Thali", 4, 60)),
            filename="x.csv",
            location=self.restaurant,
            business_date=date(2026, 9, 26),
        )
        self.assertEqual(outcome.status, "waiting")
        self.assertIn("thali", outcome.message + " ".join(n.body for n in self.owner.notifications.all()))

    def test_saving_again_replaces_the_days_thali(self):
        thali.save_day(SUN, [(self.sambar, Decimal("4"))])
        thali.save_day(SUN, [(self.rasam, Decimal("6"))], said="rasam")
        day = ThaliDay.objects.get(business_date=SUN)
        self.assertEqual([line.item for line in day.lines.all()], [self.rasam])
        with self.assertRaises(thali.ThaliError):
            thali.save_day(SUN, [(self.rasam, Decimal("0"))])

    # --- the screen ---------------------------------------------------------

    def test_the_owner_types_checks_and_saves(self):
        self.client.force_login(self.owner)
        url = f"{reverse('thali')}?date=2026-09-27"
        page = self.client.post(url, {"date": "2026-09-27", "action": "suggest", "said": "dal fry, rasam"})
        self.assertContains(page, "Dhal Fry")
        self.assertContains(page, "Matched by name")
        page = self.client.post(
            url,
            {
                "date": "2026-09-27",
                "action": "save",
                "said": "dal fry, rasam",
                "rows": "2",
                "item_0": self.dhal.pk,
                "oz_0": "3",
                "item_1": self.rasam.pk,
                "oz_1": "8",
            },
        )
        self.assertRedirects(page, url)
        self.assertEqual(ThaliDay.objects.get(business_date=SUN).lines.count(), 2)

    def test_only_owners_enter_the_thali(self):
        from apps.core.models import User

        self.client.force_login(User.objects.create_user("cook"))
        self.assertEqual(self.client.get(reverse("thali")).status_code, 403)
