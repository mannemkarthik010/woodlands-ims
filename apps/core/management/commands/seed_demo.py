"""
Sample items and opening stock, so the screens can be tried before the real
ingredients list arrives.

THIS IS MADE-UP DATA. Every item code starts with DEMO- so it is obvious in
any list and easy to remove:

    python manage.py seed_demo            add it
    python manage.py seed_demo --clear    take it away again

Delete all of it before the system holds anything real. The genuine item
master gets built on site, against the actual shelves, with the owners and
the chef -- a list assembled from a menu always misses the things nobody
thinks to mention.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.catalog.models import Item, ItemAlias, ItemCategory, ItemKind, Unit
from apps.core.models import Location
from apps.stock.models import MovementType, StockMovement
from apps.stock.services import post_movement

# code, name, unit, category, aliases, opening qty at storage
ITEMS = [
    ("DEMO-rice-idli", "Idli rice", "lb", "Rice and grains", ["parboiled rice"], 240),
    ("DEMO-rice-sona", "Sona masoori rice", "lb", "Rice and grains", [], 180),
    ("DEMO-dal-urad", "Urad dal (whole)", "lb", "Pulses and dals", ["black gram", "urad"], 150),
    ("DEMO-dal-toor", "Toor dal", "lb", "Pulses and dals", ["arhar", "pigeon pea"], 120),
    ("DEMO-dal-chana", "Chana dal", "lb", "Pulses and dals", [], 80),
    ("DEMO-fenugreek", "Fenugreek seed", "g", "Spices whole", ["methi"], 4000),
    ("DEMO-mustard", "Mustard seed", "g", "Spices whole", ["rai"], 3000),
    ("DEMO-cumin", "Cumin seed", "g", "Spices whole", ["jeera"], 2500),
    ("DEMO-turmeric", "Turmeric powder", "g", "Spices ground", ["haldi"], 2000),
    ("DEMO-chilli", "Red chilli powder", "g", "Spices ground", [], 2500),
    ("DEMO-sambar-pdr", "Sambar powder", "g", "Spice blends (in-house)", [], 1800),
    ("DEMO-ghee", "Ghee", "lb", "Oils and ghee", [], 40),
    ("DEMO-oil", "Sunflower oil", "l", "Oils and ghee", [], 60),
    ("DEMO-paneer", "Paneer", "lb", "Dairy and paneer", [], 25),
    ("DEMO-coconut", "Fresh coconut", "each", "Fresh produce", [], 30),
    ("DEMO-curryleaf", "Curry leaves", "g", "Fresh produce", ["kadi patta"], 800),
    ("DEMO-onion", "Onions", "lb", "Fresh produce", [], 100),
    ("DEMO-potato", "Potatoes", "lb", "Fresh produce", [], 120),
    ("DEMO-tomato", "Tomatoes", "lb", "Fresh produce", [], 60),
    ("DEMO-tamarind", "Tamarind block", "lb", "Tinned and jarred", ["imli"], 20),
]

BASES = [
    ("DEMO-batter-dosa", "Dosa batter", "gal", "Batters (in-house)"),
    ("DEMO-batter-idli", "Idli batter", "gal", "Batters (in-house)"),
    ("DEMO-sambar", "Sambar", "gal", "Bases and gravies (in-house)"),
    ("DEMO-chutney-coco", "Coconut chutney", "qt", "Chutneys (in-house)"),
]


class Command(BaseCommand):
    help = "Add (or remove) sample items and opening stock for trying the screens."

    def add_arguments(self, parser):
        parser.add_argument("--clear", action="store_true", help="Remove all DEMO- items instead.")

    @transaction.atomic
    def handle(self, *args, **options):
        if options["clear"]:
            demo = Item.objects.filter(code__startswith="DEMO-")
            moves = StockMovement.objects.filter(item__in=demo).count()
            StockMovement.objects.filter(item__in=demo).delete()
            count = demo.count()
            demo.delete()
            self.stdout.write(self.style.SUCCESS(f"Removed {count} demo items and {moves} movements."))
            return

        storage = Location.objects.filter(kind=Location.Kind.STORAGE).first()
        if storage is None:
            self.stdout.write(self.style.ERROR("Run `python manage.py seed` first."))
            return

        units = {u.code: u for u in Unit.objects.all()}
        cats = {c.name: c for c in ItemCategory.objects.all()}
        made = 0

        for code, name, unit, cat, aliases, opening in ITEMS:
            item, created = Item.objects.get_or_create(
                code=code,
                defaults={
                    "name": name,
                    "kind": ItemKind.RAW,
                    "base_unit": units[unit],
                    "category": cats.get(cat),
                },
            )
            if created:
                made += 1
                for alias in aliases:
                    ItemAlias.objects.get_or_create(item=item, alias=alias, defaults={"source": "demo"})
                post_movement(
                    item=item,
                    location=storage,
                    quantity=Decimal(opening),
                    movement_type=MovementType.OPENING_BALANCE,
                    occurred_at=timezone.now(),
                    note="Demo opening balance",
                )

        for code, name, unit, cat in BASES:
            _, created = Item.objects.get_or_create(
                code=code,
                defaults={
                    "name": name,
                    "kind": ItemKind.PREPARED,
                    "base_unit": units[unit],
                    "category": cats.get(cat),
                    "shelf_life_days": 3,
                },
            )
            made += created

        self.stdout.write(
            self.style.SUCCESS(f"Added {made} demo items with opening stock at {storage.name}.")
        )
        self.stdout.write(self.style.WARNING("Sample data. Remove with: python manage.py seed_demo --clear"))
