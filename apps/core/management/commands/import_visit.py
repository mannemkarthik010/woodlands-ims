"""
Load what the kitchen said on a visit into the system.

    python manage.py import_visit data/from-client/visit-2026-10-02.json --dry-run
    python manage.py import_visit data/from-client/visit-2026-10-02.json

The visit form was filled in by hand with the owners and the chefs, then
typed up, answer by answer, into the JSON file. Every figure in that file
carries the code of the box it came from (C1, K03, O22.1), so anything this
command writes can be traced back to a photo of the page.

WHAT IS LOADED

  vessels           what the scoop and the large ladle hold
  containers        what a bucket of each batter weighs
  count_every       which count list each kitchen-made item is on
  new_items         things the answers need that the system did not have yet
  make_more_below   par levels: below this, make more
  sized_buttons     the menu buttons sold by size with no size on them
  plates            what one plate of a dish uses -- a recipe for the dish,
                    so a day's sales take batter and chutney out of stock

WHAT IS NOT

Anything the answers do not settle. The batter recipes are written in scoops
and nobody has weighed a scoop of urad gota or of rice yet, so they stay in
the file as told and are not turned into weights here. Questions that came
back blank or contradictory are listed in the file under "still_to_ask" and
printed at the end, so the next visit starts from them.

Safe to run again: everything already as the file says is left alone, and a
dish whose plate has changed gets a new recipe version rather than an edit.
"""

import json
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.dateparse import parse_date

from apps.catalog.models import (
    Item,
    ItemKind,
    ItemMeasure,
    MeasureKind,
    ParLevel,
    Recipe,
    RecipeLine,
    Unit,
    Vessel,
)
from apps.catalog.services import add_item, find_by_name, move_to_list
from apps.core.models import Location, User
from apps.sales.models import PosItem
from apps.sales.naming import FLUID_OUNCE_ML
from apps.sales.services import quantity_for, set_portion


class DryRun(Exception):
    """Raised at the end of a dry run so nothing is kept."""


def ounces(item: Item, oz) -> Decimal:
    """Ounces as the kitchen said them, in the item's own base unit."""
    return quantity_for("", item, ounces=Decimal(str(oz)))


class Command(BaseCommand):
    help = "Load the answers from a restaurant visit (JSON) into the catalogue, counts and recipes."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--dry-run", action="store_true", help="Show what would change; keep nothing.")
        parser.add_argument("--by", default="", help="Username to record as having loaded it.")

    def handle(self, *args, **options):
        path = Path(options["path"])
        if not path.exists():
            raise CommandError(f"No such file: {path}")
        try:
            self.visit = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            raise CommandError(f"{path.name} is not valid JSON: {e}") from None

        self.user = None
        if options["by"]:
            self.user = User.objects.filter(username=options["by"]).first()
            if self.user is None:
                raise CommandError(f"No user called {options['by']}.")
        self.measured_on = parse_date(self.visit.get("visit", {}).get("date", ""))
        self.loud = options.get("verbosity", 1) > 0
        self.changes: list[str] = []
        self.skipped: list[str] = []

        try:
            with transaction.atomic():
                self.load()
                if options["dry_run"]:
                    raise DryRun
        except DryRun:
            pass
        self.report(dry=options["dry_run"])

    # -- the steps, in the order they depend on each other ---------------------

    def load(self):
        self.load_vessels()
        self.load_new_items()
        self.load_containers()
        self.load_count_every()
        self.load_make_more()
        self.load_sized_buttons()
        self.load_plates()

    def item(self, name: str, code: str) -> Item | None:
        found = find_by_name(name)
        if found is None or not found.is_active:
            self.skipped.append(f"{code}: no item called {name!r} -- skipped")
            return None
        return found

    def load_vessels(self):
        for row in self.visit.get("vessels", []):
            ml = (Decimal(str(row["fl_oz"])) * FLUID_OUNCE_ML).quantize(Decimal("0.01"))
            vessel = Vessel.objects.filter(name=row["name"]).first()
            if vessel and vessel.volume_ml == ml:
                continue
            Vessel.objects.update_or_create(
                name=row["name"],
                defaults={"volume_ml": ml, "measured_on": self.measured_on, "note": f"{row['code']}, visit"},
            )
            self.changes.append(f"{row['code']}: a {row['name']} holds {row['fl_oz']} fl oz")

    def load_new_items(self):
        for row in self.visit.get("new_items", []):
            if find_by_name(row["name"]):
                continue
            add_item(
                name=row["name"],
                what=row["what"],
                base_unit=Unit.objects.get(code=row["unit"]),
                count_every=row["every"],
                user=self.user,
            )
            self.changes.append(f"new item: {row['name']} ({row['why']})")

    def load_containers(self):
        for row in self.visit.get("containers", []):
            item = self.item(row["item"], row["code"])
            if item is None:
                continue
            quantity = ounces(item, Decimal(str(row["lb"])) * 16)
            measure = item.measures.filter(name=row["measure"], is_active=True).first()
            if measure and measure.quantity_in_base_units == quantity:
                continue
            if measure is None:
                measure = ItemMeasure(
                    item=item, name=row["measure"], kind=MeasureKind.KITCHEN, created_by=self.user
                )
            measure.quantity_in_base_units = quantity
            measure.measured_on = self.measured_on
            measure.save()
            self.changes.append(f"{row['code']}: a {row['measure']} of {item.name} is {row['lb']} lb")

    def load_count_every(self):
        for row in self.visit.get("count_every", []):
            item = self.item(row["item"], row["code"])
            if item is None or item.count_every == row["every"]:
                continue
            move_to_list(item, row["every"])
            self.changes.append(f"{row['code']}: {item.name} counted {row['every'].lower()}")

    def load_make_more(self):
        location = Location.objects.get(code="restaurant")
        for row in self.visit.get("make_more_below", []):
            item = self.item(row["item"], row["code"])
            if item is None:
                continue
            measure = item.measures.filter(name=row["measure"], is_active=True).first()
            if measure is None:
                self.skipped.append(f"{row['code']}: {item.name} has no {row['measure']} measure -- skipped")
                continue
            quantity = Decimal(str(row["quantity"])) * measure.quantity_in_base_units
            par = ParLevel.objects.filter(item=item, location=location).first()
            if par and par.quantity == quantity:
                continue
            ParLevel.objects.update_or_create(item=item, location=location, defaults={"quantity": quantity})
            self.changes.append(
                f"{row['code']}: make more {item.name} below {row['quantity']} {row['measure']}"
            )

    def load_sized_buttons(self):
        for row in self.visit.get("sized_buttons", []):
            pos = PosItem.objects.select_related("item__base_unit").filter(pos_name=row["pos_name"]).first()
            if pos is None or pos.item is None:
                self.skipped.append(
                    f"{row['code']}: menu button {row['pos_name']!r} not found or not matched"
                )
                continue
            target = ounces(pos.item, row["oz"])
            if pos.portion_confirmed and pos.quantity_per_sale == target:
                continue
            set_portion(pos, ounces=Decimal(str(row["oz"])), user=self.user)
            self.changes.append(f"{row['code']}: one {pos.pos_name} is {row['oz']} oz")

    def load_plates(self):
        plates = self.visit.get("plates", {})
        sides = {k: v for k, v in plates.get("sides", {}).items() if not k.startswith("_")}
        for row in plates.get("dishes", []):
            dish = self.item(row["dish"], row["codes"])
            if dish is None:
                continue
            if dish.kind != ItemKind.DISH:
                self.skipped.append(f"{row['codes']}: {dish.name} is not a dish -- skipped")
                continue
            told: dict[str, Decimal] = {}
            for name, oz in row["oz"].items():
                for part, part_oz in sides.items() if name == "sides" else [(name, oz)]:
                    told[part] = told.get(part, Decimal("0")) + Decimal(str(part_oz))
            components = {name: self.item(name, row["codes"]) for name in told}
            if None in components.values():
                continue
            # A plate is made of things held in stock. "Paneer" on the menu is
            # an add-on button, not the paneer in the fridge.
            dishes = [c.name for c in components.values() if c.kind == ItemKind.DISH]
            if dishes:
                self.skipped.append(
                    f"{row['codes']}: {', '.join(dishes)} is a menu dish, not stock -- skipped {dish.name}"
                )
                continue
            self.save_plate(dish, {components[name]: oz for name, oz in told.items()}, row["codes"])

    def save_plate(self, dish: Item, told: dict, codes: str):
        """`told` is ounces per component, as the chefs said them."""
        told = dict(sorted(told.items(), key=lambda kv: -kv[1]))
        wanted = {component: ounces(component, oz) for component, oz in told.items()}
        current = Recipe.objects.filter(item=dish, is_active=True).first()
        if current:
            have = {line.component: line.quantity for line in current.lines.select_related("component")}
            if have == wanted and current.yield_quantity == 1:
                return
            current.is_active = False
            current.save(update_fields=["is_active", "updated_at"])
        version = (
            Recipe.objects.filter(item=dish).order_by("-version").values_list("version", flat=True).first()
            or 0
        ) + 1
        recipe = Recipe.objects.create(
            item=dish,
            version=version,
            yield_quantity=Decimal("1"),
            method=f"One plate, as told by the chefs on the visit ({codes}).",
            created_by=self.user,
        )
        for order, (component, quantity) in enumerate(wanted.items()):
            RecipeLine.objects.create(
                recipe=recipe, component=component, quantity=quantity, sort_order=order, created_by=self.user
            )
        lines = ", ".join(f"{c.name} {oz:g} oz" for c, oz in told.items())
        self.changes.append(f"{codes}: one {dish.name} = {lines}")

    # -- what happened -----------------------------------------------------------

    def report(self, *, dry: bool):
        if not self.loud:
            return
        w = self.stdout.write
        for line in self.changes:
            w(f"  {line}")
        for line in self.skipped:
            w(self.style.WARNING(f"  {line}"))
        verb = "would change" if dry else "changed"
        w(self.style.SUCCESS(f"\n{len(self.changes)} {verb}, {len(self.skipped)} skipped."))
        if dry:
            w("Dry run: nothing was kept.")
        questions = self.visit.get("still_to_ask", [])
        if questions:
            w(f"\nStill to ask ({len(questions)}):")
            for q in questions:
                w(f"  - {q}")
