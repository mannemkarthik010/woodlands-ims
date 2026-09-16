from django.contrib import admin
from django.utils.html import format_html

from apps.production.models import ProductionBatch, ProductionInput


class ProductionInputInline(admin.TabularInline):
    model = ProductionInput
    fk_name = "batch"
    extra = 2
    autocomplete_fields = ("item",)
    verbose_name_plural = "What actually went in (not what the recipe says)"


@admin.register(ProductionBatch)
class ProductionBatchAdmin(admin.ModelAdmin):
    list_display = (
        "batch_code",
        "item",
        "status",
        "started_at",
        "matured_at",
        "expires_at",
        "expected_yield",
        "actual_yield",
        "variance_display",
    )
    list_filter = ("status", "item", "location")
    search_fields = ("batch_code", "item__name")
    date_hierarchy = "started_at"
    autocomplete_fields = ("item", "recipe", "produced_by")
    inlines = [ProductionInputInline]
    readonly_fields = ("variance_display",)
    fieldsets = (
        (None, {"fields": ("batch_code", "item", "location", "recipe", "status", "produced_by")}),
        ("Timing", {"fields": ("soak_started_at", "started_at", "finished_at")}),
        ("Yield", {"fields": ("expected_yield", "actual_yield", "variance_display")}),
        ("Fermentation", {"fields": ("matured_at", "expires_at", "ambient_temp_f")}),
        ("Notes", {"fields": ("note",)}),
    )

    @admin.display(description="Variance")
    def variance_display(self, obj):
        pct = obj.yield_variance_pct
        if pct is None:
            return "—"
        colour = "#7A2E1E" if pct < -2 else "#2F6B4F"
        return format_html('<b style="color:{}">{:+.1f}%</b>', colour, pct)
