from django.contrib import admin
from django.utils.html import format_html

from apps.stock.models import (
    GoodsReceipt, GoodsReceiptLine, StockBalance, StockCount, StockCountLine,
    StockMovement, Transfer, TransferLine, TransferTemplate, TransferTemplateLine, WasteEvent,
)


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    """
    DELIBERATELY READ-ONLY.

    The ledger is append-only. If it can be edited through the admin then it
    is not append-only, and every guarantee built on top of it -- the audit
    trail, variance analysis, "what did we hold on the 3rd" -- quietly stops
    being true. Movements are created by services.post_movement and corrected
    by services.reverse_movement, which writes an opposing row. There is no
    third way in, including for a superuser.
    """

    list_display = ("occurred_at", "item", "location", "movement_type", "quantity", "batch", "created_by")
    list_filter = ("movement_type", "location", "occurred_at")
    search_fields = ("item__name", "item__code", "note")
    date_hierarchy = "occurred_at"
    list_select_related = ("item", "location", "batch", "created_by")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StockBalance)
class StockBalanceAdmin(admin.ModelAdmin):
    """Also read-only: this is a cache of the ledger, not a place to type."""

    list_display = ("item", "location", "quantity", "updated_at")
    list_filter = ("location",)
    search_fields = ("item__name", "item__code")
    list_select_related = ("item", "location")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class GoodsReceiptLineInline(admin.TabularInline):
    model = GoodsReceiptLine
    extra = 1
    autocomplete_fields = ("item", "purchase_unit")


@admin.register(GoodsReceipt)
class GoodsReceiptAdmin(admin.ModelAdmin):
    list_display = ("id", "supplier", "location", "received_at", "status")
    list_filter = ("status", "supplier", "location")
    date_hierarchy = "received_at"
    inlines = [GoodsReceiptLineInline]


class TransferLineInline(admin.TabularInline):
    model = TransferLine
    extra = 1
    autocomplete_fields = ("item",)


@admin.register(Transfer)
class TransferAdmin(admin.ModelAdmin):
    list_display = ("id", "from_location", "to_location", "occurred_at", "status")
    list_filter = ("status", "from_location", "to_location")
    date_hierarchy = "occurred_at"
    inlines = [TransferLineInline]


class TransferTemplateLineInline(admin.TabularInline):
    model = TransferTemplateLine
    extra = 3
    autocomplete_fields = ("item",)


@admin.register(TransferTemplate)
class TransferTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "from_location", "to_location")
    inlines = [TransferTemplateLineInline]


class StockCountLineInline(admin.TabularInline):
    model = StockCountLine
    extra = 0
    autocomplete_fields = ("item",)
    readonly_fields = ("expected_quantity",)


@admin.register(StockCount)
class StockCountAdmin(admin.ModelAdmin):
    list_display = ("id", "location", "area", "cadence", "counted_at", "status")
    list_filter = ("cadence", "status", "location")
    date_hierarchy = "counted_at"
    inlines = [StockCountLineInline]


@admin.register(WasteEvent)
class WasteEventAdmin(admin.ModelAdmin):
    list_display = ("occurred_at", "item", "location", "quantity", "reason", "created_by")
    list_filter = ("reason", "location")
    search_fields = ("item__name", "note")
    date_hierarchy = "occurred_at"
    autocomplete_fields = ("item",)
