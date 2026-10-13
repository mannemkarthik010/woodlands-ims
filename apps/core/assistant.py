"""
The kitchen assistant: tell it what happened, it fills in the records.

    "we made 4 buckets of dosa batter"           -> a batch, made today
    "2 bags of idly rice came from Restaurant Depot" -> a delivery
    "sambar 1.5 buckets, dosa batter 2 buckets"  -> a count of those two
    "how much sambar is left?"                   -> an answer from the ledger

It is an agent in the plain sense -- Claude, with tools, in a loop -- and it
is built so that it cannot do anything on its own:

- It can READ freely (what is on hand, the run-out watch) and use what it
  reads to decide.
- It can only PROPOSE a change. Every proposal is held, shown to the person
  as a card in their own words ("Made: Dosa Batter, 4 buckets"), and nothing
  touches stock until they tap Confirm. Confirming runs the same services
  the ordinary screens use, with the same checks.
- Item names in a proposal are constrained to the restaurant's own list
  (the tool schemas carry it as an enum, with strict validation), so it
  cannot invent an ingredient; units are checked again when confirmed.

AI is reached through apps.core.ai, so the same consent gate and provider
(Google Cloud or Anthropic) apply. Without AI the page says so and points at
the ordinary screens -- nothing in the kitchen depends on it.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from apps.catalog.models import Item, ItemKind
from apps.core import ai
from apps.core.models import Location, User

log = logging.getLogger(__name__)

MAX_TURNS = 6

SYSTEM = """You help the kitchen of Woodlands Indian Cuisine, a South Indian restaurant, keep its stock records.
Staff tell you, in their own words, what they made, what was delivered, or what they counted. Turn that into
proposals with the propose_* tools: one proposal per batch made, one per delivery, one per count (a count may
list several items). Use the stock tools when someone asks how much is left or what to make.

Rules:
- Use only the item names the tools allow. If what they said does not clearly match one, ask rather than guess.
- Keep their quantities and containers exactly ("4 buckets", "2 bags"). Never convert or estimate a number.
- Proposals are not saved until the person confirms, so after proposing, say briefly what you prepared.
- Reply in the language the person wrote in (English or Spanish), in one or two short sentences."""


class AssistantError(Exception):
    """Raised with a sentence that can be shown to the person as it is."""


@dataclass
class Proposal:
    kind: str  # made · delivery · count
    lines: list[dict]  # {"item": name, "quantity": "4", "measure": "bucket"}
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])

    @property
    def title(self) -> str:
        return {"made": "Made", "delivery": "Delivery received", "count": "Count"}[self.kind]

    def describe(self) -> str:
        return ", ".join(
            f"{line['item']} {line['quantity']} {line['measure']}".strip() for line in self.lines
        )


@dataclass
class Reply:
    text: str
    proposals: list[Proposal]


def _names(kinds) -> list[str]:
    return sorted(
        set(
            Item.objects.filter(is_active=True, is_stocked=True, kind__in=kinds)
            .exclude(code__startswith="DEMO-")
            .values_list("name", flat=True)
        )
    )


def _line_schema(names: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "item": {"type": "string", "enum": names},
            "quantity": {"type": "number"},
            "measure": {
                "type": "string",
                "description": "What it was counted in, as said: bucket, bag, case, scoop -- or lb, oz, each.",
            },
        },
        "required": ["item", "quantity", "measure"],
        "additionalProperties": False,
    }


def tools() -> list[dict]:
    made = _names([ItemKind.PREPARED])
    bought = _names([ItemKind.RAW, ItemKind.PACKAGING, ItemKind.CONSUMABLE])
    everything = sorted(set(made) | set(bought))

    def tool(name, description, properties, required):
        return {
            "name": name,
            "description": description,
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        }

    return [
        tool(
            "stock_on_hand",
            "How much of one item the records say is at the restaurant now.",
            {"item": {"type": "string", "enum": everything}},
            ["item"],
        ),
        tool(
            "runout_watch",
            "The run-out watch: sambar, the batters, rice -- on hand, use per day, and whether to make or order.",
            {},
            [],
        ),
        tool(
            "propose_made",
            "Propose recording one batch the kitchen made. Held until the person confirms.",
            {"line": _line_schema(made)},
            ["line"],
        ),
        tool(
            "propose_delivery",
            "Propose recording one delivery that arrived (one or more items). Held until the person confirms.",
            {"lines": {"type": "array", "items": _line_schema(bought)}},
            ["lines"],
        ),
        tool(
            "propose_count",
            "Propose recording a count of some items (how much is there now). Held until the person confirms.",
            {"lines": {"type": "array", "items": _line_schema(everything)}},
            ["lines"],
        ),
    ]


def _restaurant() -> Location:
    location = Location.objects.filter(kind=Location.Kind.RESTAURANT, is_active=True).first()
    if location is None:
        raise AssistantError("No restaurant is set up yet.")
    return location


def _read_tool(name: str, args: dict) -> str:
    from apps.stock.runout import watched
    from apps.stock.services import on_hand

    if name == "stock_on_hand":
        item = Item.objects.filter(name=args["item"], is_active=True).first()
        if item is None:
            return "No such item."
        qty = on_hand(item, _restaurant())
        return f"{item.name}: {qty.normalize():f} {item.base_unit.code} on hand at the restaurant."
    if name == "runout_watch":
        rows = [
            f"{w.item.name}: {w.on_hand.normalize():f} {w.unit}"
            f"{' (' + w.in_containers + ')' if w.in_containers else ''} - {w.advice}"
            for w in watched()
        ]
        return "\n".join(rows) or "Nothing is on the run-out watch yet."
    return "Unknown tool."


def _clean_line(line: dict) -> dict:
    return {
        "item": str(line.get("item", "")),
        "quantity": format(Decimal(str(line.get("quantity", 0))).normalize(), "f"),
        "measure": str(line.get("measure", "")).strip().lower(),
    }


def ask(text: str, *, history: list | None = None) -> Reply:
    """One message from a person; the reply and anything proposed. Raises AssistantError without AI."""
    if not ai.available():
        raise AssistantError("The assistant needs AI, which is not switched on yet. Use the screens below.")
    text = " ".join((text or "").split())[:1000]
    if not text:
        raise AssistantError("Tell me what happened, or ask what is left.")

    messages = list(history or []) + [{"role": "user", "content": text}]
    proposals: list[Proposal] = []
    toolset = tools()
    for _ in range(MAX_TURNS):
        response = ai.create(
            max_tokens=4000,
            output_config={"effort": "low"},
            system=SYSTEM,
            tools=toolset,
            messages=messages,
        )
        if response.stop_reason == "refusal":
            raise AssistantError("I couldn't help with that one. Please use the screens below.")
        if response.stop_reason != "tool_use":
            reply = "".join(b.text for b in response.content if b.type == "text").strip()
            return Reply(text=reply or "Done.", proposals=proposals)

        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in (b for b in response.content if b.type == "tool_use"):
            args = block.input if isinstance(block.input, dict) else json.loads(block.input or "{}")
            if block.name == "propose_made":
                proposals.append(Proposal("made", [_clean_line(args["line"])]))
                content = "Prepared for the person to confirm."
            elif block.name in ("propose_delivery", "propose_count"):
                kind = "delivery" if block.name == "propose_delivery" else "count"
                proposals.append(Proposal(kind, [_clean_line(line) for line in args["lines"]]))
                content = "Prepared for the person to confirm."
            else:
                content = _read_tool(block.name, args)
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": content})
        messages.append({"role": "user", "content": results})
    return Reply(text="I've prepared what I could — please check below.", proposals=proposals)


# --- Carrying out a confirmed proposal ---------------------------------------


def _resolve(line: dict) -> tuple[Item, Decimal, object]:
    item = Item.objects.filter(name=line["item"], is_active=True).first()
    if item is None:
        raise AssistantError(f"{line['item']} is not on the system.")
    try:
        quantity = Decimal(str(line["quantity"]))
    except InvalidOperation:
        raise AssistantError(f"{line['quantity']} is not a number.") from None
    if quantity <= 0:
        raise AssistantError(f"The amount for {item.name} must be more than zero.")
    said = (line.get("measure") or "").strip().lower().rstrip("s") if line.get("measure") else ""
    if said in ("", item.base_unit.code, item.base_unit.code.rstrip("s")):
        return item, quantity, None
    measure = next(
        (m for m in item.measures.filter(is_active=True) if m.name.lower().rstrip("s") == said), None
    )
    if measure is None:
        known = ", ".join(m.name for m in item.measures.filter(is_active=True)) or "none"
        raise AssistantError(
            f"{item.name} isn't recorded in {line['measure']} (known: {known}, or {item.base_unit.code})."
        )
    return item, quantity, measure


@transaction.atomic
def carry_out(proposal: Proposal, *, user: User) -> str:
    """Do what was proposed, through the ordinary services. All or nothing."""
    from apps.production.services import finish_batch, start_batch
    from apps.stock.models import GoodsReceipt, StockCount
    from apps.stock.services import (
        add_receipt_line,
        build_count_sheet,
        post_count,
        post_receipt,
        record_counted,
    )

    location = _restaurant()
    resolved = [_resolve(line) for line in proposal.lines]
    if proposal.kind == "made":
        item, quantity, measure = resolved[0]
        if item.kind != ItemKind.PREPARED:
            raise AssistantError(f"{item.name} is bought, not made in the kitchen.")
        batch = start_batch(item, location=location, by=user)
        finish_batch(batch, quantity=quantity, measure=measure, by=user, note="via the assistant")
        return f"Recorded: {item.name} made ({batch.batch_code})."
    if proposal.kind == "delivery":
        receipt = GoodsReceipt.objects.create(location=location, received_at=timezone.now(), created_by=user)
        for item, quantity, measure in resolved:
            add_receipt_line(receipt, item=item, quantity=quantity, measure=measure)
        post_receipt(receipt, user=user)
        return f"Recorded: delivery of {len(resolved)} item{'s' if len(resolved) != 1 else ''}."
    if proposal.kind == "count":
        # A sheet of just the items said -- not the full ad-hoc list a count
        # started from the screens would carry.
        count = StockCount.objects.create(
            location=location, cadence=StockCount.Cadence.ADHOC, counted_at=timezone.now(), created_by=user
        )
        build_count_sheet(count, items=[item for item, _, _ in resolved])
        by_item = {line.item_id: line for line in count.lines.all()}
        for item, quantity, measure in resolved:
            record_counted(by_item[item.pk], quantity=quantity, measure=measure)
        post_count(count, user=user)
        return f"Recorded: count of {len(resolved)} item{'s' if len(resolved) != 1 else ''}."
    raise AssistantError("I don't know how to do that.")


def to_session(proposals: list[Proposal]) -> list[dict]:
    return [asdict(p) for p in proposals]


def from_session(rows: list[dict]) -> list[Proposal]:
    return [Proposal(**row) for row in rows or []]
