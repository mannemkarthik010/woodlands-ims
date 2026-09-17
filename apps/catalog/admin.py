from django.contrib import admin

from apps.catalog.models import (
    Item,
    ItemAlias,
    ItemCategory,
    ItemMeasure,
    ParLevel,
    Recipe,
    RecipeLine,
    Unit,
)


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "kind", "to_canonical")
    list_filter = ("kind",)
    search_fields = ("code", "name")


@admin.register(ItemCategory)
class ItemCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "sort_order")
    search_fields = ("name",)


class ItemAliasInline(admin.TabularInline):
    model = ItemAlias
    extra = 1
    verbose_name_plural = "Other names this is called"


class ItemMeasureInline(admin.TabularInline):
    model = ItemMeasure
    extra = 1
    verbose_name_plural = "How it is bought (watch pack sizes per supplier)"


class ParLevelInline(admin.TabularInline):
    model = ParLevel
    extra = 0


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "kind", "base_unit", "category", "current_unit_cost", "is_active")
    list_filter = ("kind", "category", "is_active", "is_allergen_relevant")
    search_fields = ("name", "code", "aliases__alias")
    autocomplete_fields = ("category", "base_unit")
    inlines = [ItemAliasInline, ItemMeasureInline, ParLevelInline]
    fieldsets = (
        (None, {"fields": ("code", "name", "kind", "category", "base_unit", "is_stocked")}),
        ("Handling", {"fields": ("shelf_life_days", "is_allergen_relevant", "allergen_notes")}),
        ("Cost", {"fields": ("current_unit_cost",)}),
        ("Admin", {"fields": ("is_active", "notes")}),
    )


class RecipeLineInline(admin.TabularInline):
    model = RecipeLine
    fk_name = "recipe"
    extra = 2
    autocomplete_fields = ("component",)


@admin.register(Recipe)
class RecipeAdmin(admin.ModelAdmin):
    list_display = ("item", "version", "yield_quantity", "is_active", "approved_by", "approved_at")
    list_filter = ("is_active", "item__kind")
    search_fields = ("item__name",)
    autocomplete_fields = ("item", "approved_by")
    inlines = [RecipeLineInline]


@admin.register(ItemMeasure)
class ItemMeasureAdmin(admin.ModelAdmin):
    """
    Registered mainly so goods receipt can autocomplete against it -- and
    because pack sizes differing by supplier is worth being able to inspect
    directly when a count refuses to reconcile.
    """

    list_display = ("item", "name", "supplier", "quantity_in_base_units", "is_approximate", "is_active")
    list_filter = ("supplier", "is_approximate", "is_active")
    search_fields = ("item__name", "item__code", "name", "supplier__name")
    autocomplete_fields = ("item", "supplier")
