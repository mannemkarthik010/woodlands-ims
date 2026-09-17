"""
Record what the kitchen's own measures weigh.

    python manage.py import_measures data/from-client/kitchen-measures.csv

Every recipe in this kitchen is written in scoops, spoons, handfuls and bars.
On 16 September 2026 somebody put them on a scale, and the result settles the
question the whole recipe half of this system was waiting on.

It also settles it in a particular way:

    1 scoop of toor dal        32 oz
    1 scoop of moong dal       31 oz
    1 scoop of sambar powder   14 oz
    1 spoon of salt            2.2 oz
    1 spoon of cumin seeds     0.6 oz

A scoop is a vessel, not a weight. What it holds depends on what is in it, so
the conversion belongs to the ITEM and there is no single global number to
store (FR-205). Storing one would be wrong nearly everywhere it was used, and
quietly -- nobody re-checks a conversion.

The CSV is in ounces because that is how the kitchen weighed them. It is
converted into each item's own base unit here, so a scoop of toor dal is
recorded as 2 lb rather than 32 of something.
"""

import csv
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.catalog.models import Item, ItemMeasure, MeasureKind, UnitKind

OUNCE_G = Decimal("28.349523125")


class Command(BaseCommand):
    help = "Record weighed kitchen measures (scoop, spoon, handful) against items."

    def add_arguments(self, parser):
        parser.add_argument("csv_path")
        parser.add_argument("--dry-run", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        dry = options["dry_run"]
        loud = options.get("verbosity", 1) > 0
        written, missing, unusable = 0, [], []

        with open(options["csv_path"], newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                name = (row["item"] or "").strip()
                item = (
                    Item.objects.filter(name__iexact=name).first()
                    or Item.objects.filter(aliases__alias__iexact=name).first()
                )
                if item is None:
                    missing.append(name)
                    continue

                unit = item.base_unit
                if unit.kind != UnitKind.WEIGHT or not unit.to_canonical:
                    # A weight cannot be written into an item held by volume or
                    # by the each without knowing its density, which nobody has
                    # measured. Refused rather than approximated.
                    unusable.append(f"{name} is held in {unit.code}")
                    continue

                ounces = Decimal(row["ounces"])
                quantity = (ounces * OUNCE_G / unit.to_canonical).quantize(Decimal("0.0001"))

                if not dry:
                    ItemMeasure.objects.update_or_create(
                        item=item,
                        name=row["measure"].strip(),
                        supplier=None,
                        defaults={
                            "kind": MeasureKind.KITCHEN,
                            "quantity_in_base_units": quantity,
                            "is_approximate": row.get("approximate", "").strip().lower() == "yes",
                            "measured_on": date.fromisoformat(row["measured_on"])
                            if row.get("measured_on")
                            else None,
                        },
                    )
                written += 1
                if loud:
                    self.stdout.write(
                        f"   1 {row['measure']} of {item.name} = {quantity:g} {unit.code}  ({ounces} oz)"
                    )

        if not loud:
            return
        w = self.stdout.write
        w("")
        w(self.style.SUCCESS(f"{written} kitchen measures recorded" + (" (dry run)" if dry else "")))
        if missing:
            w("")
            w(
                self.style.MIGRATE_HEADING(
                    "No such item yet — these were weighed but the ingredient is not in the catalogue:"
                )
            )
            for name in missing:
                w(f"   {name}")
        if unusable:
            w("")
            w(self.style.MIGRATE_HEADING("Refused — a weight cannot be applied to these:"))
            for line in unusable:
                w(f"   {line}")
