"""
The kitchen assistant: it proposes, a person confirms, the ordinary services
do the work. The model is scripted here -- no test reaches the network.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.catalog.models import Item, ItemKind, ItemMeasure, MeasureKind, Unit, UnitKind
from apps.core import assistant
from apps.core.models import Location, User
from apps.stock.models import MovementType, StockMovement
from apps.stock.services import on_hand

AI_ON = {"KNOWLEDGE_CONSENT": True, "AI_PROVIDER": "anthropic", "KNOWLEDGE_API_KEY": "k"}


def tool_call(name, args, id="t1"):
    return SimpleNamespace(
        stop_reason="tool_use", content=[SimpleNamespace(type="tool_use", id=id, name=name, input=args)]
    )


def said(text):
    return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])


class AssistantTests(TestCase):
    def setUp(self):
        lb = Unit.objects.create(
            code="lb", name="Pound", kind=UnitKind.WEIGHT, to_canonical=Decimal("453.59237")
        )
        self.here = Location.objects.create(code="r", name="Restaurant", kind=Location.Kind.RESTAURANT)
        self.batter = Item.objects.create(code="b", name="Dosa Batter", kind=ItemKind.PREPARED, base_unit=lb)
        ItemMeasure.objects.create(
            item=self.batter, name="bucket", kind=MeasureKind.KITCHEN, quantity_in_base_units=Decimal("30.86")
        )
        self.rice = Item.objects.create(code="r", name="Idly Rice", kind=ItemKind.RAW, base_unit=lb)
        ItemMeasure.objects.create(
            item=self.rice, name="bag", kind=MeasureKind.PURCHASE, quantity_in_base_units=Decimal("20")
        )
        self.cook = User.objects.create_user("tablet", display_name="Kitchen")

    def run_ask(self, *responses, text="we made 4 buckets of dosa batter"):
        with patch("apps.core.ai.create", side_effect=list(responses)) as create:
            reply = assistant.ask(text)
        return reply, create

    @override_settings(**AI_ON)
    def test_it_proposes_and_nothing_is_saved_until_confirmed(self):
        reply, create = self.run_ask(
            tool_call("propose_made", {"line": {"item": "Dosa Batter", "quantity": 4, "measure": "buckets"}}),
            said("I've prepared 4 buckets of dosa batter for you to confirm."),
        )
        self.assertEqual(reply.text, "I've prepared 4 buckets of dosa batter for you to confirm.")
        self.assertEqual(
            [(p.kind, p.describe()) for p in reply.proposals], [("made", "Dosa Batter 4 buckets")]
        )
        self.assertFalse(StockMovement.objects.exists())

        message = assistant.carry_out(reply.proposals[0], user=self.cook)
        self.assertIn("Dosa Batter made", message)
        self.assertEqual(on_hand(self.batter, self.here), Decimal("123.44"))

        # The tool list it was given limits items to the restaurant's own, strictly.
        tools = {t["name"]: t for t in create.call_args_list[0].kwargs["tools"]}
        made = tools["propose_made"]
        self.assertTrue(made["strict"])
        self.assertEqual(
            made["input_schema"]["properties"]["line"]["properties"]["item"]["enum"], ["Dosa Batter"]
        )

    @override_settings(**AI_ON)
    def test_a_delivery_and_a_count(self):
        reply, _ = self.run_ask(
            tool_call(
                "propose_delivery", {"lines": [{"item": "Idly Rice", "quantity": 2, "measure": "bag"}]}, "a"
            ),
            tool_call(
                "propose_count", {"lines": [{"item": "Idly Rice", "quantity": 50, "measure": "lb"}]}, "b"
            ),
            said("Done."),
            text="2 bags of idly rice came, and now we have 50 lb",
        )
        delivery, count = reply.proposals
        assistant.carry_out(delivery, user=self.cook)
        self.assertEqual(on_hand(self.rice, self.here), Decimal("40"))
        assistant.carry_out(count, user=self.cook)
        self.assertEqual(on_hand(self.rice, self.here), Decimal("50"))
        self.assertTrue(StockMovement.objects.filter(movement_type=MovementType.COUNT_ADJUSTMENT).exists())

    @override_settings(**AI_ON)
    def test_it_can_read_stock_before_answering(self):
        reply, create = self.run_ask(
            tool_call("stock_on_hand", {"item": "Idly Rice"}),
            said("There is no idly rice recorded."),
            text="how much idly rice is left?",
        )
        self.assertEqual(reply.proposals, [])
        tool_result = create.call_args_list[1].kwargs["messages"][-1]["content"][0]
        self.assertIn("Idly Rice: 0 lb on hand", tool_result["content"])

    def test_a_unit_the_item_is_not_kept_in_is_refused_when_confirmed(self):
        bad = assistant.Proposal("made", [{"item": "Dosa Batter", "quantity": "4", "measure": "trays"}])
        with self.assertRaisesMessage(assistant.AssistantError, "isn't recorded in trays"):
            assistant.carry_out(bad, user=self.cook)
        self.assertFalse(StockMovement.objects.exists())

    def test_something_bought_cannot_be_recorded_as_made(self):
        bad = assistant.Proposal("made", [{"item": "Idly Rice", "quantity": "1", "measure": "bag"}])
        with self.assertRaisesMessage(assistant.AssistantError, "bought, not made"):
            assistant.carry_out(bad, user=self.cook)

    @override_settings(KNOWLEDGE_CONSENT=False)
    def test_without_ai_it_says_so(self):
        with self.assertRaisesMessage(assistant.AssistantError, "not switched on"):
            assistant.ask("we made sambar")

    # --- the screen ---------------------------------------------------------

    @override_settings(**AI_ON)
    def test_ask_then_confirm_on_the_screen(self):
        self.client.force_login(self.cook)
        with patch(
            "apps.core.ai.create",
            side_effect=[
                tool_call(
                    "propose_made", {"line": {"item": "Dosa Batter", "quantity": 2, "measure": "bucket"}}
                ),
                said("Ready to confirm."),
            ],
        ):
            page = self.client.post(reverse("assistant"), {"action": "ask", "said": "2 buckets dosa batter"})
        self.assertContains(page, "Ready to confirm.")
        self.assertContains(page, "Dosa Batter")
        proposal_id = self.client.session[assistant_key()][0]["id"]
        self.assertFalse(StockMovement.objects.exists())

        page = self.client.post(reverse("assistant"), {"action": "confirm", "id": proposal_id}, follow=True)
        self.assertContains(page, "Dosa Batter made")
        self.assertEqual(on_hand(self.batter, self.here), Decimal("61.72"))
        self.assertEqual(self.client.session[assistant_key()], [])

    @override_settings(**AI_ON)
    def test_a_proposal_can_be_removed_without_saving(self):
        self.client.force_login(self.cook)
        with patch(
            "apps.core.ai.create",
            side_effect=[
                tool_call(
                    "propose_made", {"line": {"item": "Dosa Batter", "quantity": 2, "measure": "bucket"}}
                ),
                said("ok"),
            ],
        ):
            self.client.post(reverse("assistant"), {"action": "ask", "said": "2 buckets dosa batter"})
        proposal_id = self.client.session[assistant_key()][0]["id"]
        self.client.post(reverse("assistant"), {"action": "discard", "id": proposal_id})
        self.assertEqual(self.client.session[assistant_key()], [])
        self.assertFalse(StockMovement.objects.exists())


def assistant_key():
    from apps.core.views import SESSION_KEY

    return SESSION_KEY
