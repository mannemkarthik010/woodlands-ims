"""
The thali of the day.

The thali's curries change every day, so no fixed recipe can say what a
plate uses. Each day an owner types what went in -- "dal fry, aloo gobi, veg
kurma, rasam, payasam" -- the way they would say it. The system matches each
phrase to something it knows, the owner confirms, and that day's thali sales
take those things out of stock.

Matching uses Claude when the owners have agreed to an outside service
(the same KNOWLEDGE_CONSENT and key as the recipe assistant), because the
kitchen's words for a dish rarely match the menu's spelling: "dal" for Dhal
Fry, "veg kurma" for Vegetable Kurma. Claude may only choose from the
system's own list, never invent an item. Without consent or a key, or if the
call fails, plain name matching does the same job less cleverly. Either way
it is a suggestion: nothing is saved until the owner has seen it.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from difflib import SequenceMatcher

from django.conf import settings
from django.db import transaction

from apps.catalog.models import Item, ItemKind
from apps.sales.models import ThaliDay, ThaliLine
from apps.sales.services import quantity_for

log = logging.getLogger(__name__)

# C13 on the visit form: "other curries, one plate -- 3 oz".
DEFAULT_OUNCES = Decimal("3")
CLOSE_ENOUGH = 0.6


class ThaliError(Exception):
    """Raised with a sentence that can be shown to the owner as it is."""


@dataclass
class Suggestion:
    said: str
    item: Item | None
    ounces: Decimal = DEFAULT_OUNCES


def candidates() -> list[Item]:
    """
    What a thali can hold: the kitchen's dishes and what it makes. Where the
    menu and the kitchen share a name -- "Rasam" the bowl, "Rasam" the pot --
    the kitchen's is kept, because that is the one held in stock.
    """
    found = (
        Item.objects.filter(is_active=True, kind__in=[ItemKind.DISH, ItemKind.PREPARED], changes_daily=False)
        .exclude(code__startswith="DEMO-")
        .order_by("name")
    )
    by_name: dict[str, Item] = {}
    for item in found:
        key = item.name.casefold()
        if key not in by_name or item.kind == ItemKind.PREPARED:
            by_name[key] = item
    return list(by_name.values())


def phrases(text: str) -> list[str]:
    """ "dal fry, aloo gobi and veg kurma" -> three phrases."""
    parts = re.split(r"[,\n;/+]|\band\b|&", text or "", flags=re.I)
    seen, out = set(), []
    for part in parts:
        phrase = " ".join(part.split()).strip(" .-")
        if phrase and phrase.casefold() not in seen:
            seen.add(phrase.casefold())
            out.append(phrase)
    return out


def _closeness(said: str, name: str) -> float:
    said, name = said.casefold(), name.casefold()
    if said == name:
        return 1.0
    ratio = SequenceMatcher(None, said, name).ratio()
    # "kurma" inside "Vegetable Kurma" is a strong sign on its own.
    words = set(said.split())
    if words and words <= set(name.split()):
        ratio = max(ratio, 0.75)
    return ratio


def match_by_name(said: list[str], items) -> list[Suggestion]:
    out = []
    for phrase in said:
        best = max(items, key=lambda i: _closeness(phrase, i.name), default=None)
        good = best is not None and _closeness(phrase, best.name) >= CLOSE_ENOUGH
        out.append(Suggestion(phrase, best if good else None))
    return out


def _can_ask_claude() -> bool:
    return bool(settings.KNOWLEDGE_CONSENT and settings.KNOWLEDGE_API_KEY)


def match_with_claude(said: list[str], items) -> list[Suggestion]:
    import anthropic

    names = [i.name for i in items]
    by_name = {i.name: i for i in items}
    schema = {
        "type": "object",
        "properties": {
            "matches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "said": {"type": "string"},
                        # "" when nothing on the list is that dish.
                        "item": {"type": "string", "enum": [*names, ""]},
                    },
                    "required": ["said", "item"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["matches"],
        "additionalProperties": False,
    }
    client = anthropic.Anthropic(api_key=settings.KNOWLEDGE_API_KEY, timeout=30.0)
    response = client.beta.messages.create(
        model=settings.KNOWLEDGE_MODEL,
        max_tokens=4000,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=(
            "You match what a South Indian restaurant's owner says was in today's thali to the "
            "restaurant's own list of dishes. Choose only from the list. Kitchen shorthand is "
            "normal: 'dal' is a dhal, 'veg' is vegetable. If nothing on the list is clearly that "
            "dish, answer with an empty item rather than the nearest guess. Return one match for "
            "each thing said, in the order given."
        ),
        messages=[
            {
                "role": "user",
                "content": "The list:\n"
                + "\n".join(names)
                + "\n\nToday's thali, as the owner said it:\n"
                + "\n".join(said),
            }
        ],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("The model declined.")
    text = next(b.text for b in response.content if b.type == "text")
    found = {m["said"].casefold(): m["item"] for m in json.loads(text)["matches"]}
    return [Suggestion(phrase, by_name.get(found.get(phrase.casefold(), ""))) for phrase in said]


def suggest(text: str) -> tuple[list[Suggestion], str]:
    """Suggestions for what the owner typed, and which method made them."""
    said = phrases(text)
    if not said:
        raise ThaliError("Type what is in today's thali, separated by commas.")
    items = candidates()
    if _can_ask_claude():
        try:
            return match_with_claude(said, items), "claude"
        except Exception:  # network, key, refusal: the owner still gets suggestions
            log.exception("Thali matching with Claude failed; matching by name instead.")
    return match_by_name(said, items), "names"


@transaction.atomic
def save_day(day: date, lines: list[tuple[Item, Decimal]], *, said: str = "", user=None) -> ThaliDay:
    """The day's thali, replacing whatever was entered for it before."""
    if not lines:
        raise ThaliError("Choose at least one thing that was in the thali.")
    if any(oz is None or oz <= 0 for _, oz in lines):
        raise ThaliError("Each amount per plate must be more than zero.")
    thali, _ = ThaliDay.objects.update_or_create(
        business_date=day, defaults={"said": said.strip()[:2000], "created_by": user}
    )
    thali.lines.all().delete()
    seen = set()
    for order, (item, oz) in enumerate(lines):
        if item.pk in seen:
            continue
        seen.add(item.pk)
        ThaliLine.objects.create(thali_day=thali, item=item, ounces=oz, sort_order=order)
    return thali


def plate(day: date) -> list[tuple[Item, Decimal]] | None:
    """What one thali used that day, in each item's own base unit; None if nobody said."""
    thali = ThaliDay.objects.filter(business_date=day).prefetch_related("lines__item__base_unit").first()
    if thali is None:
        return None
    return [(line.item, quantity_for("", line.item, ounces=line.ounces)) for line in thali.lines.all()]
