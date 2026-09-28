"""
Reading Shift4's "Sales Summary by Item" report, exported as CSV (ADR 0003).

What a real export looks like (28 September 2026):

    Item,Revenue Class,Department,Default Price,Qty,Total Cost,% of Total,
    Discounts,Avg. Sale Price,Gross Sales,Net Sales,Net Sales w/o Mods,% of Total
    Masala Dosa,Food,Dosa,11.75,27,0,0,0,11.85,320,320,,7.31
    ...
    Totals:,,,,423,0,100,20,10.35,4398.33,4378.33,,100

Three things learned from it, and handled here:

- The file carries no date. The business date is not in it -- the number in
  the file name is when it was exported -- so the date is always given by the
  person importing it, never guessed.
- Add-ons are folded into the dish they were added to: an average sale price
  above the default price is the only trace of them, and "Net Sales w/o Mods"
  is empty. An extra paneer cannot be counted from this report.
- The last line is the report's own total. The lines are checked against it,
  so a download cut off halfway is refused rather than half-imported.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

REQUIRED = ("Item", "Department", "Qty", "Net Sales")


class ReportError(Exception):
    """Raised with a sentence that can be shown to the owner as it is."""


@dataclass(frozen=True)
class Row:
    name: str
    department: str
    revenue_class: str
    quantity: Decimal
    default_price: Decimal | None
    gross: Decimal | None
    net: Decimal | None
    raw: dict


@dataclass(frozen=True)
class Report:
    rows: list[Row]
    total_quantity: Decimal
    total_net: Decimal | None


def number(value: str) -> Decimal | None:
    value = (value or "").strip().replace("$", "").replace(",", "")
    if value == "":
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ReportError("The file could not be read as text. Please export it again as CSV.")


# Implements: FR-610.
def read_sales_summary(data: bytes) -> Report:
    if not data or not data.strip():
        raise ReportError("The file is empty.")
    reader = csv.DictReader(io.StringIO(_text(data)))
    headers = [h.strip() for h in (reader.fieldnames or [])]
    missing = [h for h in REQUIRED if h not in headers]
    if missing:
        raise ReportError(
            "This does not look like Shift4's “Sales Summary by Item” report "
            f"(no {', '.join(missing)} column). Please export that report as CSV."
        )

    rows: list[Row] = []
    stated_total = stated_net = None
    for line_no, raw in enumerate(reader, start=2):
        raw = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
        name = raw.get("Item", "")
        if not name:
            continue
        if name.rstrip(":").lower() == "totals":
            stated_total, stated_net = number(raw.get("Qty", "")), number(raw.get("Net Sales", ""))
            continue
        quantity = number(raw.get("Qty", ""))
        if quantity is None:
            raise ReportError(f"Line {line_no} ({name}) has no quantity sold.")
        rows.append(
            Row(
                name=name,
                department=raw.get("Department", ""),
                revenue_class=raw.get("Revenue Class", ""),
                quantity=quantity,
                default_price=number(raw.get("Default Price", "")),
                gross=number(raw.get("Gross Sales", "")),
                net=number(raw.get("Net Sales", "")),
                raw=raw,
            )
        )

    if not rows:
        raise ReportError("The report has no items in it. Was anything sold that day?")
    total = sum((r.quantity for r in rows), Decimal("0"))
    if stated_total is not None and stated_total != total:
        raise ReportError(
            f"The report's own total is {stated_total.normalize():f} items, but its lines add up to "
            f"{total.normalize():f}. The file may have been cut off — please export it again."
        )
    net = sum((r.net for r in rows if r.net is not None), Decimal("0"))
    return Report(rows=rows, total_quantity=total, total_net=stated_net if stated_net is not None else net)
