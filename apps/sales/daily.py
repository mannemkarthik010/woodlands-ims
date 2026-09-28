"""
A day's sales, from Shift4's CSV to stock.

    import_day   read the file for a business date: every line matched to a
                 menu button (new buttons are added, unmatched, for mapping)
    preview      what recording it would take out of stock, before it does
    post_day     take it out of stock -- only when every line is matched

Rules, all enforced here:

- The date is given by the person importing: the file does not carry one.
- The same file is never imported twice (its SHA-256 is kept).
- A second file for a date that already has one is refused unless the
  owner asks to replace it. Replacing marks the old import superseded and,
  if it had been recorded, reverses every movement it made -- so a day is
  never counted twice.
- Nothing is taken out of stock while a line is unmatched (is_safe_to_post).
- A dish with no recipe yet takes nothing out, and says so. Tubs sold by
  size (dosa batter, sambar, chutneys) are mapped straight to what they are,
  so those come out from the first day.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.models import Location, User
from apps.sales.models import PosItem, SalesImport, SalesImportLine, SalesImportStatus
from apps.sales.naming import normalise
from apps.sales.shift4 import Report, ReportError, number, read_sales_summary
from apps.stock.models import MovementType, StockMovement
from apps.stock.services import explode, post_movement, reverse_movement

SOURCE_TYPE = "sales.salesimport"


class SalesError(Exception):
    """Raised with a sentence that can be shown to the owner as it is."""


class DayAlreadyImported(SalesError):
    def __init__(self, existing: SalesImport):
        self.existing = existing
        super().__init__(f"{existing.business_date:%a %-d %b} already has a sales file. Replace it?")


def _fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _reverse_import(sales_import: SalesImport, *, user=None) -> int:
    moved = StockMovement.objects.filter(
        source_type=SOURCE_TYPE, source_id=sales_import.pk, reversed_by__isnull=True
    )
    n = 0
    for movement in moved:
        reverse_movement(
            movement, user=user, reason=f"Sales for {sales_import.business_date} replaced by a new file"
        )
        n += 1
    return n


# Implements: FR-610, FR-611.
@transaction.atomic
def import_day(
    data: bytes,
    *,
    filename: str,
    business_date: date,
    location: Location,
    user: User | None = None,
    replace: bool = False,
) -> SalesImport:
    if business_date > timezone.localdate():
        raise SalesError("That date hasn't happened yet.")
    try:
        report: Report = read_sales_summary(data)
    except ReportError as e:
        raise SalesError(str(e)) from None

    sha = _fingerprint(data)
    same_file = (
        SalesImport.objects.filter(source_sha256=sha)
        .exclude(status__in=[SalesImportStatus.SUPERSEDED, SalesImportStatus.FAILED])
        .first()
    )
    if same_file:
        raise SalesError(f"This exact file was already imported, for {same_file.business_date:%a %-d %b}.")

    existing = (
        SalesImport.objects.select_for_update()
        .filter(business_date=business_date, location=location)
        .exclude(status=SalesImportStatus.SUPERSEDED)
        .first()
    )
    if existing:
        if not replace:
            raise DayAlreadyImported(existing)
        _reverse_import(existing, user=user)
        existing.status = SalesImportStatus.SUPERSEDED
        existing.save(update_fields=["status", "updated_at"])

    sales_import = SalesImport.objects.create(
        business_date=business_date,
        location=location,
        source_filename=(filename or "")[:255],
        source_sha256=sha,
        status=SalesImportStatus.IMPORTED,
        imported_at=timezone.now(),
        created_by=user,
    )

    # One line per menu button; the report lists each once, but be safe.
    per_button: dict[str, list] = defaultdict(list)
    for row in report.rows:
        per_button[row.name].append(row)
    mapped = unmapped = 0
    for name, rows in per_button.items():
        first = rows[0]
        pos_item, created = PosItem.objects.get_or_create(
            pos_name=name,
            defaults={
                "pos_category": first.department,
                "revenue_class": first.revenue_class,
                "default_price": first.default_price,
                "group_key": normalise(name),
                "first_seen_on": business_date,
            },
        )
        if not created and (pos_item.last_seen_on is None or pos_item.last_seen_on < business_date):
            pos_item.last_seen_on = business_date
            pos_item.save(update_fields=["last_seen_on"])
        SalesImportLine.objects.create(
            sales_import=sales_import,
            pos_item=pos_item,
            quantity_sold=sum((r.quantity for r in rows), Decimal("0")),
            gross_amount=sum((r.gross or Decimal("0") for r in rows), Decimal("0")),
            raw_row=first.raw if len(rows) == 1 else {"rows": [r.raw for r in rows]},
        )
        if pos_item.needs_attention:
            unmapped += 1
        else:
            mapped += 1

    sales_import.rows_read = len(report.rows)
    sales_import.rows_mapped = mapped
    sales_import.rows_unmapped = unmapped
    sales_import.save(update_fields=["rows_read", "rows_mapped", "rows_unmapped"])
    return sales_import


def net_of(line: SalesImportLine) -> Decimal:
    """Net sales for a line, from the row as Shift4 wrote it."""
    rows = line.raw_row.get("rows", [line.raw_row])
    return sum((_money(r.get("Net Sales", "")) for r in rows), Decimal("0"))


def _money(value: str) -> Decimal:
    return number(value) or Decimal("0")


def refresh_matching(sales_import: SalesImport) -> SalesImport:
    """After menu buttons are mapped, count again what is still unmatched."""
    lines = sales_import.lines.select_related("pos_item")
    sales_import.rows_unmapped = sum(1 for line in lines if line.pos_item.needs_attention)
    sales_import.rows_mapped = len(lines) - sales_import.rows_unmapped
    sales_import.save(update_fields=["rows_mapped", "rows_unmapped"])
    return sales_import


@dataclass
class Preview:
    """What recording a day would do, worked out without doing it."""

    takes_out: dict = field(default_factory=dict)  # Item -> quantity in its base unit
    no_recipe: list = field(default_factory=list)  # (dish, how many sold) with nothing to take out
    unmatched: list = field(default_factory=list)  # SalesImportLine
    needs_portion: list = field(default_factory=list)  # SalesImportLine sold with no size per sale
    ignored: int = 0
    total_quantity: Decimal = Decimal("0")
    total_net: Decimal = Decimal("0")


# Implements: FR-602, FR-610.
def preview(sales_import: SalesImport) -> Preview:
    out = Preview()
    for line in sales_import.lines.select_related("pos_item", "pos_item__item", "pos_item__item__base_unit"):
        out.total_quantity += line.quantity_sold
        out.total_net += net_of(line)
        pos = line.pos_item
        if pos.ignore:
            out.ignored += 1
            continue
        if pos.item is None:
            out.unmatched.append(line)
            continue
        if pos.needs_portion:
            out.needs_portion.append(line)
            continue
        quantity = line.quantity_to_deplete * pos.quantity_per_sale
        components = [c for c in explode(pos.item, quantity) if c.item.is_stocked]
        if not components:
            out.no_recipe.append((pos.item, line.quantity_to_deplete))
        for c in components:
            out.takes_out[c.item] = out.takes_out.get(c.item, Decimal("0")) + c.quantity
    out.no_recipe.sort(key=lambda pair: -pair[1])
    out.takes_out = dict(sorted(out.takes_out.items(), key=lambda kv: kv[0].name.casefold()))
    return out


# Implements: FR-610, FR-611.
@transaction.atomic
def post_day(sales_import: SalesImport, *, user: User | None = None) -> Preview:
    sales_import = SalesImport.objects.select_for_update().get(pk=sales_import.pk)
    if sales_import.status == SalesImportStatus.POSTED:
        raise SalesError("This day's sales are already recorded.")
    refresh_matching(sales_import)
    if not sales_import.is_safe_to_post:
        raise SalesError(
            f"{sales_import.rows_unmapped} menu item(s) are not matched to a dish yet. Match them first — "
            "nothing is taken out of stock while a line is unaccounted for."
        )
    result = preview(sales_import)
    at = timezone.make_aware(datetime.combine(sales_import.business_date, time(23, 59)))
    for item, quantity in result.takes_out.items():
        if quantity:
            post_movement(
                item=item,
                location=sales_import.location,
                quantity=-quantity,
                movement_type=MovementType.SALE_DEPLETION,
                occurred_at=at,
                source=sales_import,
                user=user,
                note=f"Sales {sales_import.business_date}",
            )
    sales_import.status = SalesImportStatus.POSTED
    sales_import.posted_at = timezone.now()
    sales_import.save(update_fields=["status", "posted_at", "updated_at"])
    return result


def days(
    location: Location, *, last: int = 21, today: date | None = None
) -> list[tuple[date, SalesImport | None]]:
    """The last `last` days, newest first, each with its sales file or None -- a missing day shows."""
    from datetime import timedelta

    today = today or timezone.localdate()
    first = today - timedelta(days=last)
    live = {
        s.business_date: s
        for s in SalesImport.objects.filter(location=location, business_date__range=(first, today))
        .exclude(status=SalesImportStatus.SUPERSEDED)
        .annotate(items_sold=Sum("lines__quantity_sold"))
    }
    return [(today - timedelta(days=i), live.get(today - timedelta(days=i))) for i in range(1, last + 1)]
