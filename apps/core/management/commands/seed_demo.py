"""
Sample items and opening stock, so the screens can be tried before the real
ingredients list arrives.

THIS IS MADE-UP DATA. Every item code starts with DEMO-, every sample person's
username with demo-, and every sample position with "DEMO", so it is obvious
in any list and easy to remove:

    python manage.py seed_demo            add it
    python manage.py seed_demo --clear    take it away again

Delete all of it before the system holds anything real. The genuine item
master gets built on site, against the actual shelves, with the owners and
the chef -- a list assembled from a menu always misses the things nobody
thinks to mention.
"""

from datetime import time
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.catalog.models import Item, ItemAlias, ItemCategory, ItemKind, Unit
from apps.core.models import Location, Position, Role, User
from apps.core.pins import set_pin
from apps.labour.models import Period, ShiftTemplate
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

# Made-up positions and hours -- the real ones are the owners' to give.
POSITIONS = [
    ("DEMO Dosa station", (time(10, 0), time(15, 0)), (time(17, 0), time(22, 0))),
    ("DEMO Server", (time(11, 0), time(15, 0)), (time(17, 0), time(21, 30))),
]
# username, name shown on the tablet, position
STAFF = [
    ("demo-ravi", "Ravi (demo)", "DEMO Dosa station"),
    ("demo-meera", "Meera (demo)", "DEMO Dosa station"),
    ("demo-arjun", "Arjun (demo)", "DEMO Server"),
]
DEMO_PIN = "2580"
TABLET = ("demo-tablet", "demo-tablet")  # username, password -- a development convenience only

BASES = [
    ("DEMO-batter-dosa", "Dosa batter", "gal", "Batters (in-house)"),
    ("DEMO-batter-idli", "Idli batter", "gal", "Batters (in-house)"),
    ("DEMO-sambar", "Sambar", "gal", "Bases and gravies (in-house)"),
    ("DEMO-chutney-coco", "Coconut chutney", "qt", "Chutneys (in-house)"),
]


class Command(BaseCommand):
    help = "Add (or remove) sample items and opening stock for trying the screens."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear", action="store_true", help="Retire all DEMO- items instead of adding them."
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options["clear"]:
            # Retired, not deleted.
            #
            # The first version deleted, and it worked right up until somebody
            # used the demo data the way it was meant to be used: recording a
            # transfer and a count against it. After that the ledger refers to
            # these items, and the foreign keys refuse -- correctly. An item
            # the record mentions cannot be made to have never existed.
            #
            # So the demo items stop appearing and their history stays
            # readable, which is what happens to every other item in this
            # system when it goes out of use.
            people = User.objects.filter(username__startswith="demo-").update(
                is_active=False, is_active_staff=False
            )
            Position.objects.filter(name__startswith="DEMO ").update(is_active=False)
            self.stdout.write(f"Deactivated {people} demo people; their shifts stay on record.")

            demo = Item.objects.filter(code__startswith="DEMO-")
            count = demo.update(is_active=False)
            moves = StockMovement.objects.filter(item__in=demo).count()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Retired {count} demo items. {moves} movement(s) against them stay in the "
                    f"ledger, because that is what the ledger is for."
                )
            )
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

        for name, morning, evening in POSITIONS:
            position, _ = Position.objects.get_or_create(name=name)
            for period, (start, end) in ((Period.MORNING, morning), (Period.EVENING, evening)):
                ShiftTemplate.objects.get_or_create(
                    position=position,
                    period=period,
                    weekday=None,
                    defaults={"starts_at": start, "ends_at": end},
                )
        for username, display, position in STAFF:
            person, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "display_name": display,
                    "role": Role.KITCHEN,
                    "position": Position.objects.get(name=position),
                },
            )
            if created:
                person.set_unusable_password()
                person.save()
                set_pin(person, DEMO_PIN)
        tablet, created = User.objects.get_or_create(
            username=TABLET[0], defaults={"display_name": "Kitchen tablet (demo)", "role": Role.KITCHEN}
        )
        if created:
            tablet.set_password(TABLET[1])
            tablet.save()

        self.stdout.write(
            self.style.SUCCESS(f"Added {made} demo items with opening stock at {storage.name}.")
        )
        self.stdout.write(
            f"Demo staff: {', '.join(d for _, d, _ in STAFF)} -- PIN {DEMO_PIN}. "
            f"Tablet sign-in: {TABLET[0]} / {TABLET[1]}."
        )
        self.stdout.write(self.style.WARNING("Sample data. Remove with: python manage.py seed_demo --clear"))
