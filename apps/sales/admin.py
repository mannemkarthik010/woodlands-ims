from django.contrib import admin
from django.utils.html import format_html

from apps.sales.models import PosItem, SalesImport, SalesImportLine


class UnmappedFilter(admin.SimpleListFilter):
    """The work queue. Everything here stops sales depleting stock until dealt with."""

    title = "mapping"
    parameter_name = "mapping"

    def lookups(self, request, model_admin):
        return [("unmapped", "Needs mapping"), ("mapped", "Mapped"), ("ignored", "Ignored")]

    def queryset(self, request, queryset):
        if self.value() == "unmapped":
            return queryset.filter(item__isnull=True, ignore=False)
        if self.value() == "mapped":
            return queryset.filter(item__isnull=False)
        if self.value() == "ignored":
            return queryset.filter(ignore=True)
        return queryset


@admin.register(PosItem)
class PosItemAdmin(admin.ModelAdmin):
    list_display = ("pos_name", "pos_category", "item", "is_modifier", "ignore", "status_display", "last_seen_on")
    list_filter = (UnmappedFilter, "is_modifier", "ignore", "pos_category")
    search_fields = ("pos_name", "item__name")
    autocomplete_fields = ("item",)
    list_editable = ("item", "is_modifier", "ignore")
    list_per_page = 50

    @admin.display(description="Status")
    def status_display(self, obj):
        if obj.ignore:
            return format_html('<span style="color:#5B6770">ignored</span>')
        if obj.item_id:
            return format_html('<span style="color:#2F6B4F">mapped</span>')
        return format_html('<b style="color:#8A6100">needs mapping</b>')


class SalesImportLineInline(admin.TabularInline):
    model = SalesImportLine
    extra = 0
    readonly_fields = ("pos_item", "quantity_sold", "quantity_voided", "quantity_comped", "gross_amount")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(SalesImport)
class SalesImportAdmin(admin.ModelAdmin):
    list_display = (
        "business_date", "location", "status", "rows_read", "rows_mapped",
        "unmapped_display", "posted_at",
    )
    list_filter = ("status", "location")
    date_hierarchy = "business_date"
    readonly_fields = (
        "source_sha256", "rows_read", "rows_mapped", "rows_unmapped",
        "imported_at", "posted_at", "error_detail",
    )
    inlines = [SalesImportLineInline]

    @admin.display(description="Unmapped")
    def unmapped_display(self, obj):
        if obj.rows_unmapped:
            return format_html('<b style="color:#8A6100">{}</b>', obj.rows_unmapped)
        return "0"
