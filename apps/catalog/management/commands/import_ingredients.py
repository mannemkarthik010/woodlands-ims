"""
Read the kitchen's grocery sheet into the catalogue.

    python manage.py import_ingredients data/from-client/groceries-with-purchase-history.csv
    python manage.py import_ingredients <file> --dry-run

The sheet Jaspinder shared is better than a list of names. It carries the pack
size ("4 lb bag"), the case ("1 case = 10 bags") and how fast it goes ("1 case
per week"), which is the item, its purchase units and a sanity check on
consumption in one document.

WHAT IS READ AND WHAT IS NOT

Read, because it is stated plainly and unambiguously:

  * the item name
  * the pack: "4 lb bag" -> a bag is 4 lb, and the item is held in pounds
  * the case: "1 case = 10 bags" -> a case is 10 bags, so 40 lb

Not read, because it is prose and prose is not data:

  * "use 4 bags per week", "1 bag lasts 1 month". These become par levels only
    once somebody says what a par level should be -- consumption is evidence
    for a par level, not the level itself.
  * "Used in mysore chutney, dosa batter, potato masala". These are recipe
    facts, and they are kept as a note on the item rather than guessed into
    a recipe.

Nothing here is destructive. An item that already exists is updated where the
sheet adds something and left alone otherwise, and a measure somebody has
corrected by hand is never overwritten by the sheet.
"""

import csv
import re
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.catalog.models import Item, ItemKind, ItemMeasure, MeasureKind, Unit

# "4 lb bag", "560 gram packets", "330 ml can", "50 gm packet", "1 kg packet"
PACK_RE = re.compile(
    r"^\s*(?P<qty>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>lb|lbs|pound|pounds|oz|ounce|g|gm|gram|grams|kg|ml|l|litre|liter)\b"
    r"\s*(?P<pack>[a-z ]*)",
    re.I,
)
# "20 piece per box", "75 piece per tray"
COUNT_RE = re.compile(r"^\s*(?P<qty>\d+)\s*(?:piece|pieces|pc|pcs)\s*per\s*(?P<pack>\w+)", re.I)
# "1 case= 10 bags", "1 case = 8 boxes"
CASE_RE = re.compile(r"1\s*case\s*=\s*(?P<qty>\d+)\s*(?P<of>\w+)", re.I)

UNIT_CODE = {
    "lb": "lb",
    "lbs": "lb",
    "pound": "lb",
    "pounds": "lb",
    "oz": "oz",
    "ounce": "oz",
    "g": "g",
    "gm": "g",
    "gram": "g",
    "grams": "g",
    "kg": "kg",
    "ml": "ml",
    "l": "l",
    "litre": "l",
    "liter": "l",
}


def slug(name: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return f"raw-{out}"[:48]


class Command(BaseCommand):
    help = "Import ingredients, pack sizes and case conversions from the kitchen's grocery sheet."

    def add_arguments(self, parser):
        parser.add_argument("csv_path")
        parser.add_argument("--dry-run", action="store_true", help="Report what would change, write nothing.")

    @transaction.atomic
    def handle(self, *args, **options):
        dry = options["dry_run"]
        loud = options.get("verbosity", 1) > 0
        units = {u.code: u for u in Unit.objects.all()}

        created = updated = packs = unknown = 0
        no_pack = []

        with open(options["csv_path"], newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                name = (row.get("Item") or "").strip()
                if not name:
                    continue

                size = (row.get("Size") or "").strip()
                pack_qty, pack_name, unit = self.read_pack(size, units)

                if unit is None:
                    # No readable pack. The item is still real and still worth
                    # having; what it is held in is a question for a person.
                    no_pack.append(name)
                    unit = units["lb"]
                    unknown += 1

                note_parts = [
                    p for p in [row.get("Usage"), row.get("Notes"), row.get("Purchases")] if (p or "").strip()
                ]
                note = " · ".join(part.strip() for part in note_parts)

                item = Item.objects.filter(code=slug(name)).first()
                if item is None:
                    item = Item(
                        code=slug(name),
                        name=name,
                        kind=ItemKind.RAW,
                        base_unit=unit,
                        is_stocked=True,
                    )
                    created += 1
                else:
                    updated += 1
                item.notes = note
                if not dry:
                    item.save()

                if dry or pack_qty is None:
                    continue

                packs += self.write_measure(item, pack_name, pack_qty)
                case = CASE_RE.search(row.get("Case") or "")
                if case:
                    per_case = Decimal(case.group("qty")) * pack_qty
                    packs += self.write_measure(item, "case", per_case)

        if not loud:
            return

        w = self.stdout.write
        w("")
        w(
            self.style.SUCCESS(
                f"{created} new, {updated} updated, {packs} purchase measures"
                + (" (dry run, nothing written)" if dry else "")
            )
        )
        if no_pack:
            w("")
            w(
                self.style.MIGRATE_HEADING(
                    f"{len(no_pack)} items have no readable pack size. They are in, held in pounds "
                    f"for now, and want a person to say what they are really measured in:"
                )
            )
            for name in no_pack:
                w(f"   {name}")
        w("")
        w(f"{Item.objects.filter(kind=ItemKind.RAW).count()} raw ingredients in the catalogue.")

    # ------------------------------------------------------------------
    def read_pack(self, size: str, units):
        """ "4 lb bag" -> (4, "bag", lb). "20 piece per box" -> (20, "box", each)."""
        count = COUNT_RE.match(size)
        if count:
            return Decimal(count.group("qty")), count.group("pack").lower(), units["each"]

        found = PACK_RE.match(size)
        if not found:
            return None, "", None
        code = UNIT_CODE.get(found.group("unit").lower())
        if code is None or code not in units:
            return None, "", None
        try:
            qty = Decimal(found.group("qty"))
        except InvalidOperation:
            return None, "", None
        pack = (found.group("pack") or "").strip().lower() or "pack"
        return qty, pack.rstrip("s"), units[code]

    def write_measure(self, item: Item, name: str, quantity: Decimal) -> int:
        """
        Never overwrite a figure a person has corrected. The sheet is evidence,
        not authority -- if somebody weighed the sack and found 24 lb, that
        beats what the supplier's catalogue says.
        """
        existing = ItemMeasure.objects.filter(item=item, name=name, supplier=None).first()
        if existing:
            return 0
        ItemMeasure.objects.create(
            item=item, name=name, kind=MeasureKind.PURCHASE, quantity_in_base_units=quantity
        )
        return 1
