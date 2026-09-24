from django.contrib import admin

from apps.labour.models import Shift, ShiftEdit, ShiftTemplate


@admin.register(ShiftTemplate)
class ShiftTemplateAdmin(admin.ModelAdmin):
    list_display = ("position", "period", "weekday", "starts_at", "ends_at")
    list_filter = ("position", "period", "weekday")


class ShiftTemplateInline(admin.TabularInline):
    """Shown on each position, so a role's hours are set in one place."""

    model = ShiftTemplate
    extra = 0
    fields = ("period", "weekday", "starts_at", "ends_at")


class ShiftEditInline(admin.TabularInline):
    model = ShiftEdit
    extra = 0
    readonly_fields = ("field_name", "old_value", "new_value", "reason", "created_by", "created_at")
    can_delete = False
    verbose_name_plural = "Corrections (kept forever)"

    def has_add_permission(self, request, obj=None):
        return False


# Implements: FR-105, FR-113.
@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    """
    Read-only on purpose. A correction made by editing a field here would
    leave no trace, so corrections go through `services.correct_shift` --
    from the owners' timesheet screen, which records who, when and why.
    """

    list_display = (
        "employee",
        "business_date",
        "period",
        "clocked_in_at",
        "clocked_out_at",
        "scheduled_start",
        "scheduled_end",
        "hours_display",
        "is_catering_event",
    )
    list_filter = ("period", "location", "is_catering_event")
    search_fields = ("employee__username", "employee__display_name")
    date_hierarchy = "business_date"
    inlines = [ShiftEditInline]

    @admin.display(description="Hours")
    def hours_display(self, obj):
        h = obj.hours_worked
        return "open" if h is None else f"{h:.2f}"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
