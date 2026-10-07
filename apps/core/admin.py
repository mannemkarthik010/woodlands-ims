from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import UserChangeForm

from apps.core.models import Area, Location, Position, Role, Supplier, User
from apps.core.pins import PinError, set_pin, validate_pin
from apps.labour.admin import ShiftTemplateInline


class StaffChangeForm(UserChangeForm):
    """
    The owners set a person's tablet PIN here. It is typed once, checked
    against the same rules as everywhere else, hashed, and never shown again.
    """

    new_pin = forms.CharField(
        label="New tablet PIN",
        required=False,
        widget=forms.PasswordInput(render_value=False, attrs={"inputmode": "numeric", "autocomplete": "off"}),
        help_text="4 to 6 digits. Leave empty to keep the current PIN. Owners do not have one.",
    )

    def clean_new_pin(self):
        pin = self.cleaned_data.get("new_pin", "").strip()
        if pin:
            try:
                validate_pin(pin, role=self.cleaned_data.get("role", self.instance.role))
            except PinError as e:
                raise forms.ValidationError(str(e)) from None
        return pin


# Implements: FR-1201.
@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = StaffChangeForm
    list_display = ("username", "display_name", "role", "position", "is_active_staff", "preferred_language")
    list_filter = ("role", "position", "is_active_staff")
    search_fields = ("username", "display_name", "first_name", "last_name")
    readonly_fields = ("pin_status",)
    actions = ["password_link"]
    fieldsets = BaseUserAdmin.fieldsets + (
        (
            "Woodlands",
            {"fields": ("role", "position", "display_name", "preferred_language", "is_active_staff")},
        ),
        ("Tablet", {"fields": ("pin_status", "new_pin")}),
    )

    @admin.display(description="Tablet PIN")
    def pin_status(self, obj):
        if not obj.can_use_pin:
            return "Owners sign in with a password"
        return "Set" if obj.pin else "Not set — this person cannot record hours yet"

    @admin.action(description="Make a one-time “choose your password” link (owners)")
    def password_link(self, request, queryset):
        """
        A fresh link for an owner whose first one ran out, or who forgot their
        password. Made here, on the live site, because a link is signed with
        the site's secret key and that key is kept nowhere else. Only an owner
        may make one, and only for an owner; it is shown once, to be sent to
        that person directly -- never posted in a channel.
        """
        from apps.core.invites import set_password_link
        from apps.core.permissions import is_owner

        if not is_owner(request.user):
            self.message_user(request, "Only an owner can make these links.", messages.ERROR)
            return
        for user in queryset:
            if user.role != Role.OWNER:
                self.message_user(
                    request, f"{user}: staff sign in with a PIN, not a password.", messages.WARNING
                )
                continue
            self.message_user(
                request,
                f"{user.display_name or user.username}: {set_password_link(user)} — works once, for 3 days. "
                "Send it to them directly.",
                messages.SUCCESS,
            )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if form.cleaned_data.get("new_pin"):
            set_pin(obj, form.cleaned_data["new_pin"])


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
