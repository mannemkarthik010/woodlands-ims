"""
Production: the batter room, and everything else the kitchen makes.

This is the module that distinguishes Woodlands from a generic case, and the
one worth the most design attention.

Ordinary inventory software assumes ingredients go in and dishes come out.
Here there is a whole layer in between -- the batters, sambar, rasam, the
chutneys, ground blends, curry bases, potato masala -- and that layer has
properties a shelf does not:

  * Quantity is NOT conserved. A pound of dry material plus water yields a
    volume of batter at a ratio that has to be measured, not assumed.
  * Stock can exist and still be unusable. Batter is not available until
    fermentation completes. "In the tub" and "ready" are different states.
  * Shelf life starts at maturity, not at production, and the ambient
    temperature during fermentation affects it -- which is why the same
    recipe behaves differently in February and August.
  * One grind may be split into several products.

The single most valuable field in this file is `actual_yield`. Nobody at the
restaurant currently knows what a batch yields. A recorded expected-versus-
actual turns a vague sense that "we seem to be buying more rice lately" into
a number somebody can act on.
"""
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from apps.catalog.models import QTY, Item, Recipe
from apps.core.models import Location, TimeStamped


class BatchStatus(models.TextChoices):
    IN_PROGRESS = "IN_PROGRESS", "In progress"
    MATURING = "MATURING", "Maturing"          # exists, not yet usable
    AVAILABLE = "AVAILABLE", "Available"
    EXPIRED = "EXPIRED", "Expired"
    WRITTEN_OFF = "WRITTEN_OFF", "Written off"


class ProductionBatch(TimeStamped):
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="batches")
    batch_code = models.CharField(max_length=40, unique=True)
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="batches")

    # The formula this batch was meant to follow. Kept for comparison only --
    # what was ACTUALLY used is recorded in ProductionInput, because the chef
    # may well adjust by eye and the system must record what happened rather
    # than what the recipe said should happen.
    recipe = models.ForeignKey(
        Recipe, null=True, blank=True, on_delete=models.PROTECT, related_name="batches"
    )

    status = models.CharField(
        max_length=12, choices=BatchStatus.choices, default=BatchStatus.IN_PROGRESS, db_index=True
    )

    soak_started_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)

    expected_yield = models.DecimalField(null=True, blank=True, **QTY)
    actual_yield = models.DecimalField(
        null=True, blank=True, help_text="Measured, not assumed. The number nobody has today.", **QTY
    )

    # Fermentation. matured_at is when the batch becomes usable stock; until
    # then it is visible but not available.
    matured_at = models.DateTimeField(null=True, blank=True, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    ambient_temp_f = models.DecimalField(
        null=True, blank=True, max_digits=5, decimal_places=1,
        help_text="Recorded so fermentation time can be tuned across the seasons.",
    )

    produced_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.PROTECT, related_name="batches_made"
    )
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]
        verbose_name_plural = "production batches"
        indexes = [models.Index(fields=["item", "status", "expires_at"])]

    def __str__(self) -> str:
        return self.batch_code

    @property
    def yield_variance(self):
        """Signed difference, in base units. Negative means short."""
        if self.actual_yield is None or not self.expected_yield:
            return None
        return self.actual_yield - self.expected_yield

    @property
    def yield_variance_pct(self):
        if self.actual_yield is None or not self.expected_yield:
            return None
        return (self.actual_yield - self.expected_yield) / self.expected_yield * Decimal("100")

    @property
    def is_usable(self) -> bool:
        return self.status == BatchStatus.AVAILABLE


class ProductionInput(models.Model):
    """
    What actually went into the batch.

    Deliberately separate from the recipe. If the formula is judged rather
    than measured -- and for batter it very often is -- the system records
    reality and compares it to the formula afterwards, instead of assuming
    the formula and producing confident, wrong numbers.
    """

    batch = models.ForeignKey(ProductionBatch, on_delete=models.CASCADE, related_name="inputs")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(validators=[MinValueValidator(Decimal("0"))], **QTY)
    from_batch = models.ForeignKey(
        ProductionBatch, null=True, blank=True, on_delete=models.PROTECT, related_name="consumed_by"
    )
    note = models.CharField(max_length=160, blank=True)


class BatchSplit(models.Model):
    """
    One grind divided into more than one product -- a single batter split
    between dosa and uthappam, for instance.
    """

    parent = models.ForeignKey(ProductionBatch, on_delete=models.CASCADE, related_name="splits")
    child = models.OneToOneField(ProductionBatch, on_delete=models.CASCADE, related_name="split_from")
    quantity = models.DecimalField(**QTY)
