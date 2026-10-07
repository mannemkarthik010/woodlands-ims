"""
The owners' dashboard: the restaurant at a glance, and what needs doing.

Everything here is read from what the system already records -- sales files,
the stock ledger and par levels, the team plan, hours, counts, batches. It
adds no numbers of its own and estimates nothing: a figure that is not known
shows as not known, with the link that would make it known.

The "Needs attention" list is the heart of it. Each entry is something a
person can act on today, worded as the action, with the page that does it.
An empty list is the goal, and says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from apps.core.models import Location
from apps.core.week import OPENING_HOURS, is_open


@dataclass
class Todo:
    text: str
    url: str
    level: str = "warn"  # warn · bad


@dataclass
class DayBar:
    day: date
    net: Decimal | None  # None: no sales file
    closed: bool
    pct: int = 0  # bar height, as a share of the week's best day


@dataclass
class Dashboard:
    today: date
    hours: list
    sales_day: date
    sales: object | None = None  # SalesImport for the latest business day
    sales_preview: object | None = None
    top_sellers: list = field(default_factory=list)
    week: list[DayBar] = field(default_factory=list)
    week_total: Decimal = Decimal("0")
    team: object | None = None
    low: list = field(default_factory=list)
    unpaid_minutes: int = 0
    unpaid_people: int = 0
    made_today: list = field(default_factory=list)
    todos: list[Todo] = field(default_factory=list)

    @property
    def is_open(self) -> bool:
        return bool(self.hours)


def _restaurant():
    return Location.objects.filter(kind=Location.Kind.RESTAURANT, is_active=True).first()


def _last_open_day(before: date) -> date:
    day = before - timedelta(days=1)
    for _ in range(7):
        if is_open(day):
            return day
        day -= timedelta(days=1)
    return before - timedelta(days=1)


def _sales(board: Dashboard, location) -> None:
    from apps.sales import daily
    from apps.sales.models import SalesImport, SalesImportStatus

    live = SalesImport.objects.filter(location=location).exclude(status=SalesImportStatus.SUPERSEDED)
    board.sales = live.filter(business_date=board.sales_day).first()
    if board.sales:
        board.sales_preview = daily.preview(board.sales)
        lines = board.sales.lines.select_related("pos_item").order_by("-quantity_sold")[:5]
        board.top_sellers = [(line.pos_item.pos_name, line.quantity_sold) for line in lines]

    first = board.today - timedelta(days=7)
    by_day = {
        s.business_date: s for s in live.filter(business_date__gte=first, business_date__lt=board.today)
    }
    for i in range(7, 0, -1):
        day = board.today - timedelta(days=i)
        s = by_day.get(day)
        net = sum((daily.net_of(line) for line in s.lines.all()), Decimal("0")) if s else None
        board.week.append(DayBar(day=day, net=net, closed=not is_open(day)))
    board.week_total = sum((b.net or Decimal("0") for b in board.week), Decimal("0"))
    best = max((b.net or Decimal("0") for b in board.week), default=Decimal("0"))
    for bar in board.week:
        bar.pct = int((bar.net or 0) * 100 / best) if best else 0

    missing = [bar.day for bar in board.week if bar.net is None and not bar.closed]
    if len(missing) == 1:
        board.todos.append(Todo(f"No sales file for {missing[0]:%a %-d %b}", reverse("sales_home")))
    elif missing:
        board.todos.append(
            Todo(
                f"No sales file for {len(missing)} days ({', '.join(f'{d:%a %-d}' for d in missing)})",
                reverse("sales_home"),
            )
        )
    for s in live.filter(business_date__gte=first, status=SalesImportStatus.IMPORTED):
        board.todos.append(
            Todo(
                f"Sales for {s.business_date:%a %-d %b} read but not recorded",
                reverse("sales_day", args=[s.pk]),
            )
        )


def _stock(board: Dashboard) -> None:
    from apps.stock.alerts import shortfalls

    board.low = shortfalls()
    for s in board.low[:3]:
        board.todos.append(
            Todo(
                s.sentence(),
                reverse("made_today") if s.is_base else reverse("receipt_new"),
                "bad" if s.is_out else "warn",
            )
        )


def _counts(board: Dashboard, location) -> None:
    from apps.stock.models import DocumentStatus, StockCount

    for cadence, label in (
        (StockCount.Cadence.DAILY, "Daily"),
        (StockCount.Cadence.WEEKLY, "Weekly"),
        (StockCount.Cadence.MONTHLY, "Monthly"),
    ):
        last = (
            StockCount.objects.filter(cadence=cadence, location=location, status=DocumentStatus.POSTED)
            .order_by("-counted_at")
            .first()
        )
        if last is None:
            due = True
        else:
            done = timezone.localdate(last.counted_at)
            due = {
                StockCount.Cadence.DAILY: done < board.today,
                StockCount.Cadence.WEEKLY: (board.today - done).days >= 7,
            }.get(cadence, (done.year, done.month) != (board.today.year, board.today.month))
        if due and (cadence != StockCount.Cadence.DAILY or board.is_open):
            board.todos.append(Todo(f"{label} stock count is due", reverse("count_home")))


def _people(board: Dashboard) -> None:
    from apps.labour import team
    from apps.labour.pay import default_up_to, unpaid
    from apps.labour.reports import people_to_review
    from apps.labour.services import missed_clock_outs

    board.team = team.board(board.today)
    if board.is_open and not board.team.planned:
        board.todos.append(
            Todo("Nobody is planned for today yet", reverse("team_plan") + f"?date={board.today:%Y-%m-%d}")
        )
    owed = unpaid(default_up_to(board.today))
    board.unpaid_minutes = owed.total
    board.unpaid_people = len(owed.payable)
    missed = missed_clock_outs().count()
    if missed:
        board.todos.append(Todo(f"{missed} shift(s) with no finish time", reverse("hours_pay"), "bad"))
    review = people_to_review().count()
    if review:
        board.todos.append(
            Todo(f"{review} new name(s) added on the tablet to check", reverse("hours_report"))
        )


def _kitchen(board: Dashboard) -> None:
    from apps.catalog.models import Item
    from apps.production.models import ProductionBatch
    from apps.sales.models import ThaliDay

    board.made_today = list(
        ProductionBatch.objects.filter(started_at__date=board.today)
        .select_related("item")
        .order_by("-started_at")[:6]
    )
    thali_on_menu = Item.objects.filter(changes_daily=True, is_active=True).exists()
    if board.is_open and thali_on_menu and not ThaliDay.objects.filter(business_date=board.today).exists():
        board.todos.append(Todo("Today's thali curries are not entered", reverse("thali")))


def _menu(board: Dashboard) -> None:
    from apps.sales.models import PosItem

    unmatched = PosItem.objects.filter(item__isnull=True, ignore=False).count()
    if unmatched:
        board.todos.append(
            Todo(f"{unmatched} menu button(s) not matched to a dish", reverse("mapping_queue"))
        )


def build(today: date | None = None) -> Dashboard:
    today = today or timezone.localdate()
    board = Dashboard(today=today, hours=OPENING_HOURS[today.weekday()], sales_day=_last_open_day(today))
    location = _restaurant()
    if location is None:
        board.todos.append(Todo("No restaurant location set up yet", reverse("admin:index"), "bad"))
        return board
    _sales(board, location)
    _people(board)
    _kitchen(board)
    _stock(board)
    _counts(board, location)
    _menu(board)
    order = {"bad": 0, "warn": 1}
    board.todos.sort(key=lambda t: order.get(t.level, 2))
    return board
