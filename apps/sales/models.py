"""
Sales: reading the Shift4 daily report, and turning it into depletion.

The feed is Shift4 Dine's own "Sales Summary by Item" report, subscribed to
arrive as a CSV each morning covering the previous day. No API, no partner
application, no live connection into the till -- which also means that if this
system is ever down, the restaurant's ability to take money is completely
unaffected.

Two things this module is careful about:

1.  IDEMPOTENCY. The same report can arrive twice, or be re-imported by hand
    after a fix. An import is keyed on the business date and the file's hash,
    and re-importing a date reverses the previous import's movements before
    writing new ones. Sales data that double-counts is worse than none.

2.  NEVER GUESSING A MAPPING. Every POS item name has to be pointed at a dish
    in the catalog before it can depelete anything. Unrecognised names go into
    a queue for a human, they do not get silently ignored and they do not get
    matched on a hopeful string similarity. An unmapped item is a visible
    problem; a wrongly mapped one is an invisible wrong answer for months.
"""
from decimal import Decimal

from django.db import models

from apps.catalog.models import MONEY, QTY, Item
from apps.core.models import Location, TimeStamped


class PosItem(TimeStamped):
    """
    A line as Shift4 names it. Kept distinct from our Item on purpose: the
    menu drifts, names get re-spelled, and items get retired. This table is
    the join between their world and ours.
    """

    pos_name = models.CharField(max_length=200, unique=True)
    pos_category = models.CharField(max_length=120, blank=True)

    item = models.ForeignKey(
        Item,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="pos_items",
        help_text="The dish this sells. Null means unmapped -- it will not deplete anything.",
    )
    is_modifier = models.BooleanField(
        default=False,
        help_text="Add-ons such as extra paneer. These consume stock in their own right.",
    )
    ignore = models.BooleanField(
        default=False, help_text="Deliberately not stock-bearing, e.g. a service charge line.",
    )

    first_seen_on = models.DateField(null=True, blank=True)
    last_seen_on = models.DateField(null=True, blank=True)
    mapped_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    mapped_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["pos_name"]
        indexes = [models.Index(fields=["item"])]

    def __str__(self) -> str:
        return self.pos_name

    @property
    def needs_attention(self) -> bool:
        return self.item_id is None and not self.ignore


class SalesImportStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    IMPORTED = "IMPORTED", "Imported"
    POSTED = "POSTED", "Posted to stock"
    FAILED = "FAILED", "Failed"
    SUPERSEDED = "SUPERSEDED", "Superseded by a later import"


class SalesImport(TimeStamped):
    """One CSV, one business date."""

    business_date = models.DateField(db_index=True)
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="sales_imports")
    source_filename = models.CharField(max_length=255, blank=True)
    source_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    status = models.CharField(
        max_length=12, choices=SalesImportStatus.choices, default=SalesImportStatus.PENDING
    )

    rows_read = models.PositiveIntegerField(default=0)
    rows_mapped = models.PositiveIntegerField(default=0)
    rows_unmapped = models.PositiveIntegerField(default=0)

    error_detail = models.TextField(blank=True)
    imported_at = models.DateTimeField(null=True, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-business_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["business_date", "location"],
                condition=~models.Q(status="SUPERSEDED"),
                name="one_live_import_per_date",
            )
        ]

    def __str__(self) -> str:
        return f"Sales {self.business_date}"

    @property
    def is_safe_to_post(self) -> bool:
        """Refuse to post while anything is unmapped -- silence is not an answer."""
        return self.status == SalesImportStatus.IMPORTED and self.rows_unmapped == 0


class SalesImportLine(models.Model):
    sales_import = models.ForeignKey(SalesImport, on_delete=models.CASCADE, related_name="lines")
    pos_item = models.ForeignKey(PosItem, on_delete=models.PROTECT, related_name="sale_lines")

    quantity_sold = models.DecimalField(**QTY)
    quantity_voided = models.DecimalField(default=Decimal("0"), **QTY)
    quantity_comped = models.DecimalField(default=Decimal("0"), **QTY)
    gross_amount = models.DecimalField(null=True, blank=True, **MONEY)

    raw_row = models.JSONField(
        default=dict, help_text="The original CSV row, kept so an import can always be explained."
    )

    @property
    def quantity_to_deplete(self):
        """
        A voided order was never made, so it depletes nothing.
        A comped dish WAS made and given away, so it depletes in full.
        """
        return self.quantity_sold + self.quantity_comped
