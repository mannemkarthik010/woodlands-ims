"""
The run-out watch: sambar, the batters, rice -- what the kitchen must never
run out of.

For each watched item (one with `lead_days` set) it works out, from what is
recorded and nothing else:

    on hand        the ledger: last count, plus what was made and delivered,
                   less what sales, batches and waste took out
    used per day   the average over the recent open days, from the same
                   movements -- including what counts found missing, which is
                   real use that nobody recorded
    days left      on hand / used per day
    lead           days it takes to have more: a batter is soaked, ground and
                   fermented (about a day); rice comes with a delivery

and turns that into the one thing a person needs to know:

    out        there is none
    now        make or order today -- what is left will not outlast the lead
               time, or it is already below its make-more level
    tomorrow   it will be "now" tomorrow
    ok         enough for a while
    unknown    not enough history yet to say how fast it goes; the owners'
               weekly plan is used if there is one, else the make-more level
               alone
    uncounted  never counted or delivered here, so nothing is known yet

It never invents a usage figure. With no history and no plan it says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.utils import timezone

from apps.catalog.models import Item, ItemKind, ParLevel, PlannedUse
from apps.core.models import Location
from apps.core.week import is_open
from apps.stock.models import MovementType, StockBalance, StockMovement

# Movements that are the kitchen using something up. Receipts, transfers in
# and what was made are supply, not use.
USE = [
    MovementType.SALE_DEPLETION,
    MovementType.PRODUCTION_CONSUME,
    MovementType.WASTE,
    MovementType.COUNT_ADJUSTMENT,
    MovementType.CORRECTION,
]
WINDOW_DAYS = 14
MIN_OPEN_DAYS = 1  # a rate from one day is rough; the screen says "based on 1 day"


@dataclass
class Watch:
    item: Item
    location: Location
    on_hand: Decimal
    per_day: Decimal | None
    par: Decimal | None
    open_days: int
    counted: bool = True  # anything at all recorded for it here yet
    planned: bool = False  # per_day is the owners' weekly plan, not yet measured

    @property
    def lead(self) -> int:
        return self.item.lead_days or 0

    @property
    def unit(self) -> str:
        return self.item.base_unit.code

    @property
    def in_containers(self) -> str:
        """46 lb of batter as the kitchen says it: "1.5 buckets". Empty when there is no bucket or bag."""
        measure = next(
            (
                m
                for m in self.item.measures.all()
                if m.is_active and m.name in ("bucket", "bag", "tub", "case")
            ),
            None,
        )
        if measure is None or not measure.quantity_in_base_units:
            return ""
        n = (max(self.on_hand, Decimal("0")) / measure.quantity_in_base_units).quantize(Decimal("0.1"))
        return f"{n.normalize():f} {measure.name}{'' if n == 1 else 's'}"

    @property
    def makes(self) -> bool:
        return self.item.kind == ItemKind.PREPARED

    @property
    def days_left(self) -> Decimal | None:
        if not self.per_day:
            return None
        return max(self.on_hand, Decimal("0")) / self.per_day

    @property
    def status(self) -> str:
        if not self.counted:
            return "uncounted"
        if self.on_hand <= 0:
            return "out"
        below_par = self.par is not None and self.on_hand <= self.par
        left = self.days_left
        if below_par or (left is not None and left <= self.lead):
            return "now"
        if left is not None and left <= self.lead + 1:
            return "tomorrow"
        if left is None:
            return "unknown"
        return "ok"

    @property
    def advice(self) -> str:
        verb = "Make" if self.makes else "Order"
        status = self.status
        if status == "out":
            return f"Out — {verb.lower()} more now"
        if status == "now":
            return f"{verb} today"
        if status == "tomorrow":
            return f"{verb} tomorrow"
        if status == "unknown":
            return "Not enough history yet — count it daily"
        if status == "uncounted":
            return "Not counted yet — count it tonight"
        days = int(self.days_left)
        return f"Enough for about {days} day{'' if days == 1 else 's'}"


def _open_days(end: date, days: int) -> int:
    return sum(1 for i in range(days) if is_open(end - timedelta(days=i)))


def _plans(today: date, items) -> dict[int, Decimal]:
    """Per open day, from the owners' plan for the week that includes today."""
    found = {}
    for plan in PlannedUse.objects.filter(
        item__in=items, week_starting__lte=today, week_starting__gt=today - timedelta(days=7)
    ).order_by("week_starting"):
        days = sum(1 for i in range(7) if is_open(plan.week_starting + timedelta(days=i)))
        if days and plan.quantity > 0:
            found[plan.item_id] = (plan.quantity / days).quantize(Decimal("0.01"))
    return found


def watched(today: date | None = None) -> list[Watch]:
    """Every watched item at the restaurant, the most urgent first."""
    today = today or timezone.localdate()
    location = Location.objects.filter(kind=Location.Kind.RESTAURANT, is_active=True).first()
    if location is None:
        return []
    items = list(
        Item.objects.filter(is_active=True, lead_days__isnull=False)
        .select_related("base_unit")
        .prefetch_related("measures")
        .order_by("name")
    )
    balances = {b.item_id: b.quantity for b in StockBalance.objects.filter(location=location, item__in=items)}
    pars = {p.item_id: p.quantity for p in ParLevel.objects.filter(location=location, item__in=items)}
    window_start = timezone.make_aware(datetime.combine(today - timedelta(days=WINDOW_DAYS), time.min))

    by_item: dict[int, list[StockMovement]] = {}
    for m in StockMovement.objects.filter(location=location, item__in=items).order_by("occurred_at", "pk"):
        by_item.setdefault(m.item_id, []).append(m)

    plans = _plans(today, items)

    out = []
    for item in items:
        moves = by_item.get(item.pk, [])
        per_day, days = None, 0
        if moves:
            # Tracking starts at the first thing recorded. If that was a
            # count, it set the opening stock -- it is not usage, and treating
            # "0 to 2 buckets" as negative use would hide the real rate.
            first = moves[0]
            opening_kinds = (MovementType.COUNT_ADJUSTMENT, MovementType.OPENING_BALANCE)
            opening = first.pk if first.movement_type in opening_kinds else None
            since = max(window_start, first.occurred_at)
            total_used = -sum(
                (
                    m.quantity
                    for m in moves
                    if m.pk != opening and m.movement_type in USE and m.occurred_at >= since
                ),
                Decimal("0"),
            )
            yesterday = today - timedelta(days=1)
            span = (yesterday - timezone.localtime(since).date()).days + 1
            days = _open_days(yesterday, span) if span > 0 else 0
            if days >= MIN_OPEN_DAYS and total_used > 0:
                per_day = (total_used / days).quantize(Decimal("0.01"))
        planned = per_day is None and item.pk in plans
        if planned:
            per_day = plans[item.pk]
        out.append(
            Watch(
                item=item,
                location=location,
                on_hand=balances.get(item.pk, Decimal("0")),
                per_day=per_day,
                par=pars.get(item.pk),
                open_days=days,
                # Before its first count or delivery the ledger's zero means
                # "not known", not "none left".
                counted=bool(moves),
                planned=planned,
            )
        )
    order = {"out": 0, "now": 1, "tomorrow": 2, "uncounted": 3, "unknown": 4, "ok": 5}
    return sorted(out, key=lambda w: (order[w.status], w.item.name))
