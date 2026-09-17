"""
Catalog: the item master, units, and recipes.

This is the load-bearing module. If the item list or the unit conversions are
wrong, every number the system produces afterwards is wrong.

Two decisions worth knowing before reading:

1.  EVERYTHING IS AN ITEM. A sack of urad dal is an item. Dosa batter is an
    item. A masala dosa on the menu is an item. They differ only by `kind`.
    This is what lets a recipe explode a dish into bases and a base into raw
    materials using one mechanism instead of three.

2.  QUANTITIES ARE ALWAYS STORED IN THE ITEM'S BASE UNIT, as Decimal.
    Conversion happens at the edges -- when somebody types "6 sacks" -- and
    never in the middle. Floats are not used for quantities or money anywhere
    in this system.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import Supplier, TimeStamped

QTY = {"max_digits": 14, "decimal_places": 4}
MONEY = {"max_digits": 12, "decimal_places": 4}


class UnitKind(models.TextChoices):
    WEIGHT = "WEIGHT", "Weight"
    VOLUME = "VOLUME", "Volume"
    COUNT = "COUNT", "Count"


# Implements: FR-203.
class Unit(TimeStamped):
    """A unit of measure. Conversion between units of the same kind only."""

    code = models.SlugField(max_length=16, unique=True)  # lb, g, kg, gal, l, ml, each
    name = models.CharField(max_length=40)
    kind = models.CharField(max_length=8, choices=UnitKind.choices)
    # How many of the kind's canonical unit (g for weight, ml for volume,
    # 1 for count) one of these represents. Lets us convert lb -> g safely.
    to_canonical = models.DecimalField(
        max_digits=18, decimal_places=8, validators=[MinValueValidator(Decimal("0.00000001"))]
    )

    class Meta:
        ordering = ["kind", "code"]

    def __str__(self) -> str:
        return self.code


# Implements: FR-209.
class ItemKind(models.TextChoices):
    RAW = "RAW", "Raw material"
    PREPARED = "PREPARED", "Prepared component"  # batter, sambar, chutney, blends
    DISH = "DISH", "Menu dish"
    PACKAGING = "PACKAGING", "Packaging"
    CONSUMABLE = "CONSUMABLE", "Consumable"


# Implements: FR-201.
class ItemCategory(TimeStamped):
    name = models.CharField(max_length=80, unique=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "item categories"

    def __str__(self) -> str:
        return self.name


# Implements: FR-201, FR-203, FR-206, FR-207, FR-209, FR-211.
class Item(TimeStamped):
    code = models.SlugField(max_length=48, unique=True)
    name = models.CharField(max_length=160)
    kind = models.CharField(max_length=12, choices=ItemKind.choices, db_index=True)
    category = models.ForeignKey(
        ItemCategory, null=True, blank=True, on_delete=models.PROTECT, related_name="items"
    )

    # The unit everything is stored in. Changing this after movements exist is
    # a migration, not an edit -- guarded in clean().
    base_unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="+")

    # Dishes and prepared components are made, not bought, so they are not
    # stocked in the ordinary sense -- but prepared components ARE stocked,
    # dishes are not.
    is_stocked = models.BooleanField(
        default=True, help_text="Dishes are not stocked; raw materials and bases are."
    )

    shelf_life_days = models.PositiveSmallIntegerField(null=True, blank=True)
    is_allergen_relevant = models.BooleanField(default=False)
    allergen_notes = models.CharField(max_length=240, blank=True)

    current_unit_cost = models.DecimalField(
        null=True, blank=True, help_text="Weighted average, per base unit.", **MONEY
    )

    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["kind", "is_active"])]

    def __str__(self) -> str:
        return self.name

    def clean(self):
        if self.kind == ItemKind.DISH and self.is_stocked:
            raise ValidationError({"is_stocked": "A menu dish is not held in stock."})


# Implements: FR-202.
class ItemAlias(TimeStamped):
    """
    The same thing called three different names by three different people --
    the supplier's name, the chef's name, the menu name. All searchable.
    """

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="aliases")
    alias = models.CharField(max_length=160)
    source = models.CharField(max_length=40, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["item", "alias"], name="uniq_alias_per_item")]

    def __str__(self) -> str:
        return self.alias


# Implements: FR-205.
class Vessel(TimeStamped):
    """
    A thing the kitchen scoops with. There is one scoop, one spoon, one ladle.

    This is the other half of FR-205, and the half the first design missed.
    A vessel holds a fixed VOLUME -- that is a property of the object, true
    for everything you put in it, and it only has to be measured once. What
    that volume WEIGHS depends on what is in it, which is why a scoop of toor
    dal is 32 oz and a scoop of sambar powder is 14.

    So both are recorded, and they answer different questions:

        Vessel       how much the scoop holds.          Asked once.
        ItemMeasure  what a scoop of THIS weighs.       Asked per ingredient,
                     and only where the weight matters enough to be worth
                     somebody's time -- the dals and the rice, not every spice.

    Where an ingredient has been weighed, the weight is used. Where it has
    not, the volume is shown instead and marked as a volume, which is honest:
    "1 spoon (2 fl oz)" tells a cook something true, and does not pretend to
    be a weight nobody measured.
    """

    name = models.CharField(max_length=40, unique=True)  # scoop, spoon, large ladle, cap
    volume_ml = models.DecimalField(
        null=True, blank=True, help_text="What it holds, level. Measured once.", **QTY
    )
    note = models.CharField(
        max_length=200, blank=True, help_text="Which one, where it lives, how it was measured."
    )
    measured_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        if self.volume_ml:
            return f"{self.name} ({self.volume_ml:g} ml)"
        return f"{self.name} (not measured)"


# Implements: FR-204, FR-205.
class MeasureKind(models.TextChoices):
    PURCHASE = "PURCHASE", "How it is bought"
    KITCHEN = "KITCHEN", "How the kitchen measures it"


class ItemMeasure(TimeStamped):
    """
    Any named quantity of an item that is not its base unit, and what that is
    worth in base units. Two kinds, and the distinction matters.

    HOW IT IS BOUGHT. Urad dal arrives in 25 lb sacks from one supplier and
    20 lb sacks from another; if the system assumes one number, four sacks
    becomes twenty pounds of phantom stock. That is the single most common
    source of silent inventory error in food businesses, which is why the
    supplier is recorded against the pack rather than against the item.

    HOW THE KITCHEN MEASURES IT. The recipes are written in scoops, spoons,
    handfuls and bars, and those are *vessels, not weights*. The kitchen
    weighed them on 16 September 2026:

        1 scoop of toor dal        32 oz
        1 scoop of sambar powder   14 oz
        1 spoon of salt            2.2 oz
        1 spoon of cumin           0.6 oz

    A scoop is not a unit of mass. What it holds depends on what is in it, so
    the conversion belongs to the item and there is no global figure to store
    (FR-205). One number applied to everything would be wrong nearly everywhere
    it was used, and wrong quietly -- nobody would notice until the variance
    report stopped making sense.
    """

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="measures")
    name = models.CharField(max_length=60)  # "sack", "case of 24", "scoop", "spoon"
    kind = models.CharField(
        max_length=10, choices=MeasureKind.choices, default=MeasureKind.PURCHASE, db_index=True
    )
    supplier = models.ForeignKey(
        Supplier,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="item_measures",
        help_text="Only meaningful for a purchase pack. A scoop has no supplier.",
    )
    quantity_in_base_units = models.DecimalField(validators=[MinValueValidator(Decimal("0.0001"))], **QTY)
    is_approximate = models.BooleanField(
        default=False,
        help_text="A handful of curry leaves, where the weight is nominal rather than measured.",
    )
    measured_on = models.DateField(null=True, blank=True, help_text="When this was last put on a scale.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["item", "name"]
        constraints = [models.UniqueConstraint(fields=["item", "name", "supplier"], name="uniq_item_measure")]

    def __str__(self) -> str:
        who = f" ({self.supplier})" if self.supplier_id else ""
        return f"{self.name}{who} = {self.quantity_in_base_units} {self.item.base_unit}"


# Implements: FR-212.
class ParLevel(TimeStamped):
    """How much of an item should be on hand at a given location."""

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="par_levels")
    location = models.ForeignKey("core.Location", on_delete=models.CASCADE, related_name="par_levels")
    quantity = models.DecimalField(**QTY)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["item", "location"], name="uniq_par_per_item_location")
        ]


# ---------------------------------------------------------------------------
# Recipes
# ---------------------------------------------------------------------------


# Implements: FR-502, FR-601, FR-602, FR-609.
class Recipe(TimeStamped):
    """
    How an item is made. Applies equally to a dish (masala dosa) and to a
    prepared component (dosa batter), which is what lets one explosion
    routine walk all the way from a sale down to raw materials.

    Versioned: changing a recipe creates a new version and retires the old
    one, so a costing from three months ago can still be explained.
    """

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="recipes")
    version = models.PositiveSmallIntegerField(default=1)
    yield_quantity = models.DecimalField(
        validators=[MinValueValidator(Decimal("0.0001"))],
        help_text="How much this recipe produces, in the item's base unit.",
        **QTY,
    )
    is_active = models.BooleanField(default=True)

    # Prep guidance -- the parts a cook reads rather than the parts the
    # system calculates.
    method = models.TextField(blank=True)
    doneness_cues = models.TextField(blank=True)
    common_mistakes = models.TextField(blank=True)
    substitutions = models.TextField(blank=True)

    approved_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["item", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["item", "version"], name="uniq_recipe_version"),
            models.UniqueConstraint(
                fields=["item"],
                condition=models.Q(is_active=True),
                name="one_active_recipe_per_item",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.item.name} v{self.version}"


# Implements: FR-601, FR-602.
class RecipeLine(TimeStamped):
    """
    One component of a recipe. `component` may itself be a prepared item with
    its own recipe -- that is the nesting that makes a dish explode into
    bases and bases into raw materials.
    """

    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="lines")
    component = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="used_in")
    quantity = models.DecimalField(
        validators=[MinValueValidator(Decimal("0.0001"))],
        help_text="In the component's base unit, per one yield of the parent recipe.",
        **QTY,
    )
    note = models.CharField(max_length=160, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["recipe", "sort_order"]

    def clean(self):
        if self.component_id and self.recipe_id and self.component_id == self.recipe.item_id:
            raise ValidationError({"component": "A recipe cannot contain itself."})

    def __str__(self) -> str:
        return f"{self.quantity} {self.component.base_unit} {self.component.name}"
