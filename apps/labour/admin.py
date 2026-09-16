from django.contrib import admin

from apps.labour.models import BreakPolicy, Shift, ShiftEdit


@admin.register(BreakPolicy)
class BreakPolicyAdmin(admin.ModelAdmin):
    list_display = ("name", "start_time", "end_time", "is_active")


class ShiftEditInline(admin.TabularInline):
    model = ShiftEdit
    extra = 0
    readonly_fields = ("field_name", "old_value", "new_value", "reason", "created_by", "created_at")
    can_delete = False
    verbose_name_plural = "Corrections (kept forever)"

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = (
        "employee", "location", "clocked_in_at", "clocked_out_at",
        "hours_display", "worked_through_break", "is_catering_event",
    )
    list_filter = ("location", "worked_through_break", "is_catering_event")
    search_fields = ("employee__username", "employee__display_name")
    date_hierarchy = "clocked_in_at"
    autocomplete_fields = ("employee",)
    inlines = [ShiftEditInline]

    @admin.display(description="Hours")
    def hours_display(self, obj):
        h = obj.hours_worked
        return "—" if h is None else f"{h:.2f}"
