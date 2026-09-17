"""
Stock: the ledger, and the documents that write to it.

THE ONE RULE THIS SYSTEM IS BUILT ON
------------------------------------
Stock is never stored as a number that gets edited. It is stored as a list of
movements that only ever gets appended to. The quantity on hand is the sum.

    on_hand(item, location) = SUM(quantity) FROM StockMovement

Nothing -- no receipt, no transfer, no count, no sale -- updates a balance
directly. Everything writes movements. A mistake is corrected by writing a
reversing movement, never by deleting or editing the original.

This costs slightly more code and buys four things that are otherwise very
hard to retrofit:

  * The audit trail is free. Every change already has a who, a when and a why.
  * Variance analysis is possible, because theoretical and actual depletion
    are both just movements of different types over the same period.
  * "What did we have on the 3rd?" is answerable, forever.
  * Nothing can silently drift. If a balance looks wrong, the movements say
    exactly how it got there.

StockBalance exists only as a cache for speed. It is derived, and can be
rebuilt from the ledger at any time. The ledger is the truth.
"""

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from apps.catalog.models import MONEY, QTY, Item, ItemMeasure
from apps.core.models import Area, Location, Supplier, TimeStamped


# Implements: FR-1203.
class MovementType(models.TextChoices):
    RECEIPT = "RECEIPT", "Goods received"
    TRANSFER_OUT = "TRANSFER_OUT", "Transferred out"
    TRANSFER_IN = "TRANSFER_IN", "Transferred in"
    PRODUCTION_CONSUME = "PROD_CONSUME", "Consumed in production"
    PRODUCTION_YIELD = "PROD_YIELD", "Produced"
    SALE_DEPLETION = "SALE", "Depleted by sales"
    WASTE = "WASTE", "Waste"
    COUNT_ADJUSTMENT = "COUNT_ADJ", "Count adjustment"
    CORRECTION = "CORRECTION", "Correction"
    OPENING_BALANCE = "OPENING", "Opening balance"


# Implements: FR-1203, FR-1204, NFR-18.
class StockMovement(TimeStamped):
    """
    One append-only row. Positive quantity increases stock at the location,
    negative decreases it. Always in the item's base unit.
    """

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="movements")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="movements")
    batch = models.ForeignKey(
        "production.ProductionBatch",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="movements",
        help_text="Set for prepared components, so a specific batch can be traced.",
    )

    movement_type = models.CharField(max_length=16, choices=MovementType.choices, db_index=True)
    quantity = models.DecimalField(help_text="Signed. In the item's base unit.", **QTY)
    unit_cost = models.DecimalField(null=True, blank=True, **MONEY)

    # When it physically happened, as against when somebody typed it in.
    # A storage run made at 3pm and entered at 6pm is two different times and
    # conflating them makes reconciliation impossible.
    occurred_at = models.DateTimeField(db_index=True)

    # What caused this movement. Generic rather than a dozen nullable FKs.
    source_type = models.CharField(max_length=32, blank=True, db_index=True)
    source_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)

    reverses = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reversed_by",
        help_text="Set when this movement exists to cancel an earlier one.",
    )
    note = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(fields=["item", "location", "occurred_at"]),
            models.Index(fields=["source_type", "source_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.quantity:+} {self.item.base_unit} {self.item.name} @ {self.location.code}"


# Implements: FR-401, FR-408.
class StockBalance(models.Model):
    """
    Derived cache of SUM(movements). Rebuildable; never authoritative.
    Kept in the same transaction as the movements that change it.
    """

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="balances")
    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name="balances")
    quantity = models.DecimalField(default=Decimal("0"), **QTY)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["item", "location"], name="uniq_balance")]

    def __str__(self) -> str:
        return f"{self.item.name} @ {self.location.code}: {self.quantity}"


# ---------------------------------------------------------------------------
# Documents. Each one produces movements; none of them touch balances.
# ---------------------------------------------------------------------------


class DocumentStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    POSTED = "POSTED", "Posted"
    VOIDED = "VOIDED", "Voided"


# Implements: FR-302, FR-303, FR-310.
class GoodsReceipt(TimeStamped):
    """What physically arrived -- not what was ordered, not what was invoiced."""

    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="receipts")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="receipts")
    received_at = models.DateTimeField()
    supplier_reference = models.CharField(max_length=80, blank=True)
    invoice_image = models.ImageField(upload_to="invoices/%Y/%m/", null=True, blank=True)
    status = models.CharField(max_length=8, choices=DocumentStatus.choices, default=DocumentStatus.DRAFT)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-received_at"]

    def __str__(self) -> str:
        return f"Receipt {self.pk} — {self.supplier}"


# Implements: FR-302, FR-306.
class GoodsReceiptLine(models.Model):
    receipt = models.ForeignKey(GoodsReceipt, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    # Entered as "6 sacks"; stored as base units so the sack size can differ
    # by supplier without corrupting the arithmetic.
    purchase_unit = models.ForeignKey(
        ItemMeasure, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    purchase_quantity = models.DecimalField(validators=[MinValueValidator(Decimal("0"))], **QTY)
    quantity_in_base_units = models.DecimalField(**QTY)
    unit_cost = models.DecimalField(null=True, blank=True, **MONEY)
    quantity_rejected = models.DecimalField(default=Decimal("0"), **QTY)
    note = models.CharField(max_length=160, blank=True)

    def __str__(self) -> str:
        return f"{self.quantity_in_base_units} {self.item.base_unit} {self.item.name}"


# Implements: FR-403, FR-404.
class Transfer(TimeStamped):
    """
    Stock moving between the Devonshire Street unit and the restaurant.

    The single most valuable record in the system, because it is the one that
    is not kept today -- and untracked transfer is where most of what looks
    like shrinkage in a two-site food business actually goes.

    Designed to be completed in under thirty seconds on a phone, standing at
    the unit door before loading. If it takes longer than that it will not be
    done, and a transfer log that is only sometimes completed is worse than
    none at all.
    """

    from_location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="transfers_out")
    to_location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="transfers_in")
    occurred_at = models.DateTimeField()
    status = models.CharField(max_length=8, choices=DocumentStatus.choices, default=DocumentStatus.DRAFT)
    note = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["-occurred_at"]

    def __str__(self) -> str:
        return f"{self.from_location.code} → {self.to_location.code} ({self.occurred_at:%d %b})"


# Implements: FR-403.
class TransferLine(models.Model):
    transfer = models.ForeignKey(Transfer, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(validators=[MinValueValidator(Decimal("0"))], **QTY)
    batch = models.ForeignKey(
        "production.ProductionBatch", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    def __str__(self) -> str:
        return f"{self.quantity} {self.item.base_unit} {self.item.name}"


# Implements: FR-406.
class TransferTemplate(TimeStamped):
    """A storage run that repeats. Load it, adjust the numbers, done."""

    name = models.CharField(max_length=80, unique=True)
    from_location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name="+")
    to_location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name="+")


class TransferTemplateLine(models.Model):
    template = models.ForeignKey(TransferTemplate, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="+")
    typical_quantity = models.DecimalField(**QTY)

    def __str__(self) -> str:
        return f"{self.typical_quantity} {self.item.base_unit} {self.item.name}"


# Implements: FR-701, FR-706.
class StockCount(TimeStamped):
    """
    A physical count. Counting everything daily is how inventory systems die,
    so counts are scoped: a short daily list, a weekly restaurant count, a
    monthly count including the storage unit.
    """

    class Cadence(models.TextChoices):
        DAILY = "DAILY", "Daily critical list"
        WEEKLY = "WEEKLY", "Weekly"
        MONTHLY = "MONTHLY", "Monthly full"
        ADHOC = "ADHOC", "Ad hoc"

    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="counts")
    area = models.ForeignKey(Area, null=True, blank=True, on_delete=models.PROTECT, related_name="counts")
    cadence = models.CharField(max_length=8, choices=Cadence.choices, default=Cadence.WEEKLY)
    counted_at = models.DateTimeField()
    status = models.CharField(max_length=8, choices=DocumentStatus.choices, default=DocumentStatus.DRAFT)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-counted_at"]


# Implements: FR-704.
class StockCountLine(models.Model):
    count = models.ForeignKey(StockCount, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    # Snapshotted when the sheet is generated, so the variance is against what
    # the system believed at the moment of counting, not at the moment of posting.
    expected_quantity = models.DecimalField(**QTY)
    counted_quantity = models.DecimalField(null=True, blank=True, **QTY)
    note = models.CharField(max_length=160, blank=True)

    def __str__(self) -> str:
        return f"{self.item.name}: counted {self.counted_quantity}"

    @property
    def variance(self):
        if self.counted_quantity is None:
            return None
        return self.counted_quantity - self.expected_quantity


# Implements: FR-802.
class WasteReason(models.TextChoices):
    SPOILED = "SPOILED", "Spoiled"
    EXPIRED = "EXPIRED", "Expired"
    OVER_PRODUCED = "OVER_PROD", "Over-produced"
    DAMAGED = "DAMAGED", "Dropped or damaged"
    COOKING_ERROR = "COOK_ERR", "Cooking error"
    CUSTOMER_RETURN = "RETURN", "Customer return"
    STAFF_MEAL = "STAFF_MEAL", "Staff meal"
    COMPED = "COMPED", "Comped dish"


# Implements: FR-801, FR-805.
class WasteEvent(TimeStamped):
    """
    Must take under fifteen seconds to record or it will not happen.

    Worth remembering at rollout: waste recording only works where staff are
    confident it will not be used against them. If the first month's report is
    used to reprimand somebody, the second month's report will show almost no
    waste, and it will be a lie.
    """

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="waste_events")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="waste_events")
    batch = models.ForeignKey(
        "production.ProductionBatch", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    quantity = models.DecimalField(validators=[MinValueValidator(Decimal("0"))], **QTY)
    reason = models.CharField(max_length=12, choices=WasteReason.choices)
    occurred_at = models.DateTimeField()
    note = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["-occurred_at"]
