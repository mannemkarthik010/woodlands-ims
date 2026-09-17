"""
Noticing that something is running out, before it runs out.

The owners' complaint is not that they lack a report. It is that they find out
a base has gone when service has started and there is nothing to be done about
it. So the system has to notice by itself, and say so where somebody will
actually see it.

WHAT COUNTS AS RUNNING OUT

A par level is how much should be on hand. Below it means order, or make more.
At or below zero means it has already happened. Both are worth saying and they
are not the same sentence, so they are not the same message.

WHAT IS DELIBERATELY NOT DONE HERE

No item without a par level is ever reported. It is tempting to invent one --
"less than a week's usage" -- and it would be wrong in both directions: noisy
for the spice that is bought once a quarter, silent for the dal that turns over
twice a week. A par level is a judgement about how this kitchen runs, and it
belongs to the people who run it (FR-212). An item with no par level is not a
problem the system can see, and it says so rather than guessing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from apps.catalog.models import ItemKind, ParLevel
from apps.notify.models import Kind
from apps.notify.services import notify, owners
from apps.stock.models import StockBalance


def tidy(quantity: Decimal) -> str:
    """
    12.0000 reads as 12, and 0.2500 reads as 0.25.

    Decimal's own "g" format keeps the trailing zeros the column width gave it,
    and "down to 12.0000 lb" in a text message reads like a machine wrote it,
    which makes the next one easier to ignore.
    """
    text = f"{quantity:f}".rstrip("0").rstrip(".")
    return text or "0"


@dataclass
class Shortfall:
    par: ParLevel
    on_hand: Decimal

    @property
    def is_out(self) -> bool:
        return self.on_hand <= 0

    @property
    def is_base(self) -> bool:
        """A base or batter, which somebody has to make rather than order."""
        return self.par.item.kind == ItemKind.PREPARED

    @property
    def short_by(self) -> Decimal:
        return self.par.quantity - self.on_hand

    def sentence(self) -> str:
        item, where = self.par.item, self.par.location
        unit = item.base_unit.code
        if self.is_out:
            lead = f"{item.name} has run out at {where.name}"
        else:
            lead = f"{item.name} is down to {tidy(self.on_hand)} {unit} at {where.name}"
        want = f"should be {tidy(self.par.quantity)} {unit}"
        action = "needs making" if self.is_base else "needs ordering"
        return f"{lead} — {want}, so it {action}."


# Implements: FR-1301, FR-1306.
def shortfalls() -> list[Shortfall]:
    """
    Everything below its par level, worst first.

    Worst is measured as a proportion of par, not as a quantity: being 4 lb
    short of 5 lb of urad dal is a crisis, and being 4 lb short of 200 lb of
    rice is a Tuesday.
    """
    balances = {(b.item_id, b.location_id): b.quantity for b in StockBalance.objects.all()}
    found = []
    for par in ParLevel.objects.select_related("item", "item__base_unit", "location"):
        if par.quantity <= 0:
            continue
        on_hand = balances.get((par.item_id, par.location_id), Decimal("0"))
        if on_hand <= par.quantity:
            found.append(Shortfall(par=par, on_hand=on_hand))
    return sorted(found, key=lambda s: s.on_hand / s.par.quantity)


# Implements: FR-1301, FR-1302, FR-1303.
def raise_alerts(*, on: date | None = None) -> dict:
    """
    Tell the owners what is short, once per item per day.

    Once per day is the whole design. An item that is low on Tuesday is still
    low on Wednesday; a message every morning is a message that gets muted
    within a week, and a muted alert is worse than no alert because everybody
    believes they are covered.
    """
    on = on or date.today()
    told = skipped = 0

    for short in shortfalls():
        kind = Kind.BASE_LOW if short.is_base else Kind.LOW_STOCK
        body = short.sentence()
        for owner in owners():
            key = f"{kind}:{short.par.item_id}:{short.par.location_id}:{owner.pk}:{on:%Y-%m-%d}"
            if notify(recipient=owner, kind=kind, body=body, dedupe_key=key):
                told += 1
            else:
                skipped += 1

    return {"short": len(shortfalls()), "told": told, "already_said": skipped}
