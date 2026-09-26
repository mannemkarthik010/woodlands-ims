"""
Build the "What we count, and how often" sheet for a visit to the restaurant.

The kitchen decides what is counted and how often -- not the system. This is
the sheet to take in and fill in with them, by hand: every item the system
knows, grouped by layer, with the questions that turn it into a count list,
and blank rows for everything it does not know yet (vegetables, packaging).

It is generated from the live item list, so it always matches the system.
What the system already knows -- how a grocery is bought -- is printed in, to
be confirmed rather than asked again.

Printed to PDF by the Chrome already on the machine, so the application does
not grow a PDF dependency for one form.

Run from the project root, with the virtual environment active:

    PYTHONPATH=. python tools/count_list_form.py "Claude outputs/Woodlands-what-we-count.pdf"
"""

import os
import subprocess
import sys
import tempfile
from datetime import date
from decimal import Decimal
from html import escape
from pathlib import Path

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from apps.catalog.models import Item, ItemKind, MeasureKind  # noqa: E402

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
BLANK_ROWS = {"prepared": 6, "groceries": 10, "vegetables": 22, "packaging": 20, "other": 10}


def number(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def plural(word: str) -> str:
    return word + ("es" if word.endswith(("x", "s", "ch", "sh")) else "s")


def pack(measure, unit: str) -> str:
    """ "4 lb bag", but "tray of 75" -- nobody says "75 each tray"."""
    qty = number(measure.quantity_in_base_units)
    return f"{measure.name} of {qty}" if unit == "each" else f"{qty} {unit} {measure.name}"


def comes_as(item: Item) -> str:
    """How it is bought, as far as the system knows: "4 lb bag · case of 10 bags"."""
    packs = sorted(
        (m for m in item.measures.all() if m.kind == MeasureKind.PURCHASE and m.is_active),
        key=lambda m: m.quantity_in_base_units,
    )
    if not packs:
        return ""
    smallest = packs[0]
    parts = [pack(smallest, item.base_unit.code)]
    for bigger in packs[1:]:
        ratio = bigger.quantity_in_base_units / smallest.quantity_in_base_units
        if ratio == ratio.to_integral_value():
            parts.append(f"{bigger.name} of {number(ratio)} {plural(smallest.name)}")
        else:
            parts.append(f"{bigger.name} = {number(bigger.quantity_in_base_units)} {item.base_unit.code}")
    return " · ".join(parts)


def kept_in(item: Item) -> str:
    """A container the kitchen already told us about, e.g. "bucket = 32 lb"."""
    for m in item.measures.all():
        if m.kind == MeasureKind.KITCHEN and m.name.lower() in {"bucket", "tub", "pan", "jar", "pot"}:
            return f"{m.name} = {number(m.quantity_in_base_units)} {item.base_unit.code}"
    return ""


BOX = '<span class="box"></span>'


def table(
    title: str, intro: str, columns: list, rows: list[list[str]], blank: int, blank_row: list[str]
) -> str:
    """
    One section. `columns` is a list of (group, label, width) -- a group
    heading spans the tick-box columns under it, one narrow column per
    choice, so a row is always a single line to tick along.
    """
    cols = "".join(f'<col style="width:{w}%">' for _, _, w in columns)
    groups, i = [], 0
    while i < len(columns):
        group = columns[i][0]
        span = 1
        while i + span < len(columns) and group and columns[i + span][0] == group:
            span += 1
        cls = ' class="grp"' if group else ""
        groups.append(f'<th colspan="{span}"{cls}>{escape(group)}</th>')
        i += span
    labels = "".join(f'<th class="{"tick" if g else ""}">{escape(label)}</th>' for g, label, _ in columns)
    body = "".join(
        "<tr>"
        + "".join(f'<td class="{"tick" if columns[j][0] else ""}">{cell}</td>' for j, cell in enumerate(r))
        + "</tr>"
        for r in rows + [blank_row] * blank
    )
    return (
        f'<section><h2>{escape(title)}</h2><p class="intro">{intro}</p>'
        f"<table><colgroup>{cols}</colgroup><thead><tr>{''.join(groups)}</tr><tr>{labels}</tr></thead>"
        f"<tbody>{body}</tbody></table></section>"
    )


def build() -> str:
    active = (
        Item.objects.filter(is_active=True)
        .exclude(code__startswith="DEMO-")
        .select_related("base_unit")
        .prefetch_related("measures")
        .order_by("name")
    )
    prepared = [i for i in active if i.kind == ItemKind.PREPARED]
    groceries = [i for i in active if i.kind in (ItemKind.RAW, ItemKind.CONSUMABLE)]
    packaging = [i for i in active if i.kind == ItemKind.PACKAGING]

    count4 = [
        ("Count it", "Daily", 6),
        ("Count it", "Weekly", 6),
        ("Count it", "Monthly", 6.5),
        ("Count it", "Not", 5),
    ]
    kept2 = [("Kept at", "Restaurant", 7.5), ("Kept at", "Storage", 6.5)]
    made = "How often is it made?"
    bought = "How often is it bought?"

    s1 = table(
        "1 · Made in the kitchen (Layer 2)",
        f"{len(prepared)} items the system knows. Add anything else the kitchen makes and keeps — "
        "gravies, bases, spice powders, dough.",
        [
            ("", "Item", 16),
            (made, "Daily", 5.5),
            (made, "2–3× wk", 6),
            (made, "Weekly", 6),
            (made, "Other", 8),
            ("", "One batch makes (e.g. 2 buckets)", 14),
            ("", "Kept in — one holds (e.g. bucket = 32 lb)", 21),
        ]
        + count4,
        [
            [f"<b>{escape(i.name)}</b>", BOX, BOX, BOX, "", "", escape(kept_in(i)), BOX, BOX, BOX, BOX]
            for i in prepared
        ],
        BLANK_ROWS["prepared"],
        ["", BOX, BOX, BOX, "", "", "", BOX, BOX, BOX, BOX],
    )
    s2 = table(
        "2 · Groceries (Layer 1)",
        f"{len(groceries)} items from the grocery sheet. Correct “comes as” where it is wrong or empty.",
        [("", "Item", 22), ("", "Comes as (bag / case / size) — correct if wrong", 27)]
        + kept2
        + count4
        + [("Still bought?", "Yes", 5), ("Still bought?", "No", 5)],
        [
            [f"<b>{escape(i.name)}</b>", escape(comes_as(i)), BOX, BOX, BOX, BOX, BOX, BOX, BOX, BOX]
            for i in groceries
        ],
        BLANK_ROWS["groceries"],
        ["", "", BOX, BOX, BOX, BOX, BOX, BOX, BOX, ""],
    )
    s3 = table(
        "3 · Vegetables and herbs (Layer 1)",
        "Not in the system yet. Every vegetable and herb bought — onions, tomatoes, green chillies, curry leaves, "
        "cilantro, ginger, garlic, potatoes, cabbage…",
        [
            ("", "Vegetable", 24),
            ("", "Comes as (case / bag / lb)", 24),
            (bought, "Daily", 7),
            (bought, "2× wk", 7),
            (bought, "Weekly", 7),
            (bought, "Other", 10),
            ("Count it", "Daily", 7),
            ("Count it", "Weekly", 7),
            ("Count it", "Not", 7),
        ],
        [],
        BLANK_ROWS["vegetables"],
        ["", "", BOX, BOX, BOX, "", BOX, BOX, BOX],
    )
    s4 = table(
        "4 · Packaging and disposables (Layer 1)",
        "Not in the system yet. Aluminium trays (each size), to-go boxes, lids, soup and sauce cups, bags, foil, "
        "cling film, gloves, napkins…",
        [("", "Item and size", 24), ("", "Comes as (e.g. case of 500)", 20), ("", "Supplier", 14.5)]
        + kept2
        + count4,
        [
            [f"<b>{escape(i.name)}</b>", escape(comes_as(i)), "", BOX, BOX, BOX, BOX, BOX, BOX]
            for i in packaging
        ],
        BLANK_ROWS["packaging"],
        ["", "", "", BOX, BOX, BOX, BOX, BOX, BOX],
    )
    s5 = table(
        "5 · Anything else we should count",
        "Drinks, dairy, frozen items, cleaning supplies — anything on today's paper sheets that is not above.",
        [("", "Item", 26), ("", "Comes as", 22), ("", "Kept at / notes", 28.5)] + count4,
        [],
        BLANK_ROWS["other"],
        ["", "", "", BOX, BOX, BOX, BOX],
    )

    today = date.today()
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Woodlands — what we count, and how often</title>
<style>
  @page {{ size: letter landscape; margin: 10mm 11mm; }}
  body {{ font: 9.5pt/1.3 -apple-system, "Helvetica Neue", Arial, sans-serif; color: #1F2933; margin: 0; }}
  .top {{ border-top: 3px solid #35363A; padding-top: 6px; display: flex; justify-content: space-between; align-items: baseline; }}
  h1 {{ font-size: 18pt; margin: 2px 0 0; }}
  .muted {{ color: #5B6770; font-size: 9pt; }}
  .who {{ font-size: 9pt; color: #5B6770; text-align: right; line-height: 1.9; }}
  .who span {{ display: inline-block; border-bottom: 1px solid #999; min-width: 160px; margin-left: 4px; }}
  .how {{ background: #F7F4F0; border: 1px solid #D8DEE4; border-radius: 6px; padding: 7px 10px; margin: 8px 0 4px; }}
  .how ol {{ margin: 3px 0 0 16px; padding: 0; }}
  .layers {{ display: flex; gap: 8px; margin: 6px 0 2px; }}
  .layers div {{ flex: 1; border: 1px solid #D8DEE4; border-radius: 6px; padding: 5px 8px; }}
  .layers b {{ color: #B7791F; }}
  section {{ break-before: page; }}
  section:first-of-type {{ break-before: auto; }}
  h2 {{ font-size: 13pt; margin: 8px 0 1px; }}
  h2::before {{ content: ""; display: inline-block; width: 4px; height: 13px; background: #B7791F; margin-right: 6px; vertical-align: -1px; }}
  .intro {{ color: #5B6770; margin: 0 0 4px; }}
  table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
  thead {{ display: table-header-group; }}
  th {{ text-align: left; font-size: 7.5pt; color: #5B6770; font-weight: 600; padding: 2px 4px; vertical-align: bottom; }}
  thead tr:last-child th {{ border-bottom: 1.5px solid #35363A; }}
  th.grp {{ text-align: center; color: #1F2933; border-bottom: 1px solid #D8DEE4; }}
  th.tick, td.tick {{ text-align: center; }}
  td {{ border-bottom: 1px solid #D8DEE4; padding: 0 4px; height: 21px; vertical-align: middle;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  td.tick {{ border-left: 1px solid #EEF1F4; }}
  tr {{ break-inside: avoid; }}
  .box {{ display: inline-block; width: 11px; height: 11px; border: 1.2px solid #1F2933; border-radius: 2px; vertical-align: middle; }}
  .sign {{ margin-top: 10px; }}
  .sign td {{ border: 0; padding: 8px 4px 0; white-space: normal; height: auto; }}
  .line {{ display: inline-block; border-bottom: 1px solid #999; width: 55%; }}
</style></head><body>

<div class="top">
  <div><div class="muted">Woodlands Indian Cuisine · Chatsworth · inventory system · printed {today:%-d %B %Y}</div>
    <h1>What we count, and how often</h1></div>
  <div class="who">Filled in with <span></span><br>Date <span></span></div>
</div>

<div class="how"><b>How to fill this in</b> — rough is fine. Tick one box per group; write sizes the way the kitchen says them ("1 bucket", "half pan").
  <ol>
    <li><b>Made in the kitchen:</b> how often each is made, what one batch makes, what it is kept in and what that holds.</li>
    <li><b>Groceries:</b> we printed how each comes where we know it — correct it if wrong, fill it in if empty. Where it is kept, how often to count it, and whether it is still bought.</li>
    <li><b>Vegetables and packaging:</b> the system has none of these yet — please list every one, with how it comes.</li>
    <li>Anything else on today's paper sheets that is not here goes in section 5.</li>
  </ol>
</div>

<div class="layers">
  <div><b>Layer 1</b> — bought in: groceries, vegetables, packaging. Counted weekly or monthly.</div>
  <div><b>Layer 2</b> — made in the kitchen: batters, sambar, chutneys, gravies. Usually counted daily.</div>
  <div><b>Layer 3</b> — dishes on the menu. <i>Not counted</i>: what they use comes from the day's sales.</div>
</div>

{s1}
{s2}
{s3}
{s4}
{s5}

<table class="sign"><tr>
  <td>Who counts the <b>daily</b> list, and at what time? <span class="line"></span></td>
  <td>Who counts <b>weekly / monthly</b>, and on which day? <span class="line"></span></td>
</tr></table>
</body></html>"""


def main(out: str) -> None:
    out_path = Path(out).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "count-list.html"
        page.write_text(build(), encoding="utf-8")
        subprocess.run(
            [
                CHROME,
                "--headless=new",
                "--disable-gpu",
                "--no-pdf-header-footer",
                f"--print-to-pdf={out_path}",
                page.as_uri(),
            ],
            check=True,
            capture_output=True,
        )
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "Woodlands-what-we-count.pdf")
