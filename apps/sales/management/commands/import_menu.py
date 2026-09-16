"""
Import Shift4's "Menu Item Detail" export into PosItem rows.

    python manage.py import_menu path/to/menu-item-detail.csv

Every POS name has to point at something before its sales can deplete
anything, and there are over three hundred of them. Mapping that by hand is
an afternoon nobody has, so this does the parts that can be done safely and
leaves the rest visible.

WHAT IT DECIDES BY ITSELF

Only things that are certain from the row alone:

  * Lines that are not food at all -- corkage, per-head rates, table
    charges. Marked `ignore`; they will never deplete stock.
  * Prix-fixe parent lines. A "Prix Fixe - Adult" at $17.25 is a wrapper
    whose courses appear as their own $0.00 lines. Depleting both would
    count every meal twice, so the parent is ignored and the courses are
    what count.
  * Inactive menu items, recorded but flagged.

WHAT IT REFUSES TO DECIDE

Which dish a name refers to. It groups names that look like the same food --
"Masala Dosa", "$10 Masala Dosa", "DosaNights-Masala Dosa", "Weekday Lunch
Masala Dosa" and "NYPF - Masala Dosa" are five POS lines for one plate of
food -- and prints them together so a person can confirm the group in one
decision instead of five. It does not create the link.

A wrong mapping is invisible for months and quietly poisons every variance
figure that follows. An unmapped item is a line on a screen that says it
needs attention. The second failure is much cheaper than the first.
"""

import csv
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.sales.models import PosItem

# Not food. These never touch stock.
NEVER_STOCK = [
    r"^corkage",
    r"rate$",
    r"per person meal rate",
    r"drink rate",
]

# Wrapper lines whose courses are separately itemised at $0.00.
PRIX_FIXE_PARENT = [
    r"prix fixe",
]

# Prefixes that mark the same food sold under a promotion, a fixed-price
# night, or a seasonal menu. Stripping them reveals the underlying dish.
PROMO_PREFIXES = [
    r"^\$10\s+",
    r"^dosanights\s*-\s*",
    r"^dosanights-\s*",
    r"^dosanights\s+",
    r"^nypf\s*-\s*",
    r"^new year's pf add on\s*-\s*",
    r"^weekday lunch\s+",
    r"^father's day\s+",
    r"^\d+\s*piece\s+",
]

PROMO_SUFFIXES = [
    r"\s*-?\s*diwali special.*$",
    r"\s*\(online\)$",
    r"\s*-\s*\d+\s*piece$",
    r"\s*\(dine-?in\)$",
    r"\s*\(take ?out\)$",
]

# Sized portions of one base: "Coconut Chutney 16 oz" -> chutney, 16 fl oz.
SIZE_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*oz\b", re.I)


def normalise(name: str) -> str:
    """Reduce a POS name to the food it is, for grouping only."""
    out = name.lower().strip()
    for pattern in PROMO_PREFIXES:
        out = re.sub(pattern, "", out)
    for pattern in PROMO_SUFFIXES:
        out = re.sub(pattern, "", out)
    out = SIZE_RE.sub("", out)
    out = re.sub(r"\bgrab and go\b|\bgrab-and-go\b", "", out)
    out = re.sub(r"[^a-z0-9 ]+", " ", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def matches(name: str, patterns) -> bool:
    low = name.lower()
    return any(re.search(p, low) for p in patterns)


def to_decimal(raw):
    try:
        return Decimal(str(raw).strip() or "0")
    except (InvalidOperation, ValueError):
        return None


class Command(BaseCommand):
    help = "Import a Shift4 Menu Item Detail CSV into PosItem rows."

    def add_arguments(self, parser):
        parser.add_argument("csv_path")
        parser.add_argument("--dry-run", action="store_true", help="Report what would change, write nothing.")

    @transaction.atomic
    def handle(self, *args, **options):
        path = options["csv_path"]
        dry = options["dry_run"]

        created = updated = 0
        auto_ignored = []
        groups = defaultdict(list)
        sized = []

        with open(path, newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                name = (row.get("Item Name") or "").strip()
                if not name:
                    continue

                department = (row.get("Department") or "").strip()
                revenue_class = (row.get("Revenue Class") or "").strip()
                price = to_decimal(row.get("Item Price"))
                active = (row.get("Status") or "").strip().lower() == "active"

                ignore = matches(name, NEVER_STOCK) or matches(name, PRIX_FIXE_PARENT)
                if ignore:
                    auto_ignored.append(name)

                defaults = {
                    "pos_category": department,
                    "revenue_class": revenue_class,
                    "default_price": price,
                    "active_on_pos": active,
                }
                # Never un-ignore something a person has already decided about.
                existing = PosItem.objects.filter(pos_name=name).first()
                if existing is None:
                    defaults["ignore"] = ignore

                if not dry:
                    _, was_created = PosItem.objects.update_or_create(pos_name=name, defaults=defaults)
                    created += was_created
                    updated += not was_created
                else:
                    created += existing is None
                    updated += existing is not None

                if not ignore and active:
                    groups[normalise(name)].append(name)
                    size = SIZE_RE.search(name)
                    if size:
                        sized.append((name, Decimal(size.group(1))))

        multi = {k: v for k, v in groups.items() if len(v) > 1}

        if options.get("verbosity", 1) == 0:
            return

        w = self.stdout.write
        w("")
        w(self.style.SUCCESS(f"{created} new, {updated} updated" + (" (dry run)" if dry else "")))
        w("")
        w(self.style.MIGRATE_HEADING("Ignored automatically — not food:"))
        for n in sorted(auto_ignored):
            w(f"   {n}")
        w("")
        w(self.style.MIGRATE_HEADING(f"{len(multi)} groups of POS names that look like the same food."))
        w("Confirm each group once, rather than mapping every line separately:")
        w("")
        for key in sorted(multi, key=lambda k: -len(multi[k]))[:25]:
            w(f"   {self.style.WARNING(key)}  ({len(multi[key])} lines)")
            for n in sorted(multi[key]):
                w(f"       {n}")
        if len(multi) > 25:
            w(f"   … and {len(multi) - 25} more groups")
        w("")
        w(self.style.MIGRATE_HEADING(f"{len(sized)} sized portions — these need quantity_per_sale:"))
        for n, oz in sorted(sized)[:20]:
            w(f"   {n:<44} {oz} oz")
        if len(sized) > 20:
            w(f"   … and {len(sized) - 20} more")
        w("")
        total = PosItem.objects.count()
        unmapped = PosItem.objects.filter(item__isnull=True, ignore=False).count()
        w(f"{total} POS items, {unmapped} still need mapping.")
