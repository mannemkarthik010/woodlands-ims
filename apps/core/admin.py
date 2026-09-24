from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from apps.core.models import Area, Location, Position, Supplier, User
from apps.labour.admin import ShiftTemplateInline


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "display_name", "role", "position", "is_active_staff", "preferred_language")
    list_filter = ("role", "position", "is_active_staff")
    search_fields = ("username", "display_name", "first_name", "last_name")
    fieldsets = BaseUserAdmin.fieldsets + (
        (
            "Woodlands",
            {"fields": ("role", "position", "display_name", "preferred_language", "is_active_staff")},
        ),
    )


@admin.register(Position)
class PositionAdmin(admin.ModelAdmin):
    """A role's morning and evening hours are set here (ADR 0008)."""

    list_display = ("name", "is_active", "sort_order")
    list_editable = ("sort_order",)
    inlines = [ShiftTemplateInline]


class AreaInline(admin.TabularInline):
    model = Area
    extra = 0


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "kind", "is_active")
    list_filter = ("kind", "is_active")
    search_fields = ("name", "code")
    inlines = [AreaInline]


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("name", "contact_name", "phone", "delivery_days", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "contact_name")


# Locations are autocompleted from several places too.
LocationAdmin.search_fields = ("name", "code")
