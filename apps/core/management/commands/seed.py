"""
Reference data the system cannot function without.

Safe to run repeatedly -- everything here is get_or_create, so re-running
after adding a unit or a category will not duplicate anything.

    python manage.py seed
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.catalog.models import ItemCategory, Unit, UnitKind
from apps.core.models import Area, Location
from apps.labour.models import BreakPolicy

UNITS = [
    # code,   name,        kind,             how many canonical units (g / ml / 1)
    ("g",     "Gram",      UnitKind.WEIGHT,  "1"),
    ("kg",    "Kilogram",  UnitKind.WEIGHT,  "1000"),
    ("lb",    "Pound",     UnitKind.WEIGHT,  "453.59237"),
    ("oz",    "Ounce",     UnitKind.WEIGHT,  "28.349523"),
    ("ml",    "Millilitre", UnitKind.VOLUME, "1"),
    ("l",     "Litre",     UnitKind.VOLUME,  "1000"),
    ("gal",   "US gallon", UnitKind.VOLUME,  "3785.411784"),
    ("qt",    "US quart",  UnitKind.VOLUME,  "946.352946"),
    ("floz",  "US fluid ounce", UnitKind.VOLUME, "29.5735295"),
    ("each",  "Each",      UnitKind.COUNT,   "1"),
    ("dozen", "Dozen",     UnitKind.COUNT,   "12"),
]

# First pass at the item list. The real one gets built on site, against the
# actual shelves, with the owners and the chef -- an item list assembled from
# a menu always misses the things nobody thinks to mention.
CATEGORIES = [
    "Rice and grains", "Pulses and dals", "Flours", "Spices whole",
    "Spices ground", "Spice blends (in-house)", "Oils and ghee",
    "Dairy and paneer", "Fresh produce", "Frozen", "Tinned and jarred",
    "Batters (in-house)", "Bases and gravies (in-house)", "Chutneys (in-house)",
    "Sweets and desserts", "Beverages", "Beer and wine", "Packaging",
    "Cleaning and consumables",
]

LOCATIONS = [
    ("restaurant", "Restaurant", Location.Kind.RESTAURANT,
     "9840 Topanga Canyon Blvd, Unit A, Chatsworth, CA 91311", 1,
     ["Dry store", "Walk-in", "Freezer", "Prep kitchen", "Line", "Bar"]),
    ("devonshire", "Devonshire Street unit", Location.Kind.STORAGE,
     "Devonshire Street, Chatsworth, CA — approx. 0.5 miles from the restaurant", 2,
     ["Dry store", "Chilled", "Frozen"]),
]


class Command(BaseCommand):
    help = "Create reference data: units, categories, locations, break policy."

    @transaction.atomic
    def handle(self, *args, **options):
        made = {"units": 0, "categories": 0, "locations": 0, "areas": 0}

        for code, name, kind, canonical in UNITS:
            _, created = Unit.objects.get_or_create(
                code=code,
                defaults={"name": name, "kind": kind, "to_canonical": Decimal(canonical)},
            )
            made["units"] += created

        for i, name in enumerate(CATEGORIES, start=1):
            _, created = ItemCategory.objects.get_or_create(
                name=name, defaults={"sort_order": i}
            )
            made["categories"] += created

        for code, name, kind, address, order, areas in LOCATIONS:
            location, created = Location.objects.get_or_create(
                code=code,
                defaults={"name": name, "kind": kind, "address": address, "sort_order": order},
            )
            made["locations"] += created
            for j, area_name in enumerate(areas, start=1):
                _, a_created = Area.objects.get_or_create(
                    location=location, name=area_name, defaults={"sort_order": j}
                )
                made["areas"] += a_created

        # The restaurant closes 3–5pm and everyone breaks together, so the
        # closure is a standard deduction rather than four taps a day per
        # person. Monday is off because the restaurant is closed.
        # TO CONFIRM with the owners: does this hold on every trading day?
        _, policy_created = BreakPolicy.objects.get_or_create(
            name="Standard 3–5pm closure", defaults={"applies_monday": False}
        )

        self.stdout.write(self.style.SUCCESS(
            f"Seeded — units +{made['units']}, categories +{made['categories']}, "
            f"locations +{made['locations']}, areas +{made['areas']}, "
            f"break policy {'created' if policy_created else 'already present'}."
        ))
        self.stdout.write(
            "Totals now: "
            f"{Unit.objects.count()} units, {ItemCategory.objects.count()} categories, "
            f"{Location.objects.count()} locations, {Area.objects.count()} areas."
        )
