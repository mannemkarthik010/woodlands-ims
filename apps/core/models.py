"""
Core: people, places, and the base classes everything else inherits.
"""

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


# Implements: NFR-18.
class TimeStamped(models.Model):
    """Every table records when a row was made and by whom. No exceptions."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    class Meta:
        abstract = True


class Role(models.TextChoices):
    """
    Four roles only. At Woodlands the owners are also the manager, the
    storekeeper and the bookkeeper, so inventing separate logins for those
    would add complication without adding control.
    """

    OWNER = "OWNER", "Owner"
    HEAD_CHEF = "HEAD_CHEF", "Head chef"
    KITCHEN = "KITCHEN", "Kitchen staff"
    FRONT_OF_HOUSE = "FOH", "Front of house"


class Position(TimeStamped):
    """
    A job in the kitchen or on the floor -- dosa station, prep, server,
    dishwasher. Distinct from `Role`, which is about what a person may see in
    the system; a position is about the hours they keep. Each position has
    its own morning and evening times (see `labour.ShiftTemplate`, ADR 0008).
    """

    name = models.CharField(max_length=60, unique=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self) -> str:
        return self.name


# Implements: FR-1201, FR-1202, FR-1208, NFR-09, NFR-11.
class User(AbstractUser):
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.KITCHEN)

    # Shared-tablet sign-in. Staff tap a PIN on the restaurant tablet; owners
    # never sign in that way -- an owner session requires a real password on
    # their own device. See `can_use_pin`.
    pin = models.CharField(
        max_length=128,
        blank=True,
        help_text="Hashed PIN for the shared tablet. Never stored in clear.",
    )
    display_name = models.CharField(max_length=80, blank=True)
    position = models.ForeignKey(
        Position,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="staff",
        help_text="The job whose shift times apply. Empty for the owners.",
    )

    # People can add themselves on the tablet, so that nobody is ever stuck
    # unable to record a shift. The owners then look each one over: a real new
    # starter is confirmed, a second spelling of somebody already on the list
    # is merged into them (see `apps.labour.services.merge_person`).
    needs_review = models.BooleanField(
        default=False, help_text="Added on the tablet and not yet confirmed by an owner."
    )
    possible_duplicate_of = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="The existing person they said they were not, when adding themselves.",
    )

    # A four-digit PIN has ten thousand possibilities, so it is only as good
    # as the limit on guessing it. See `apps.core.pins`.
    pin_failed_attempts = models.PositiveSmallIntegerField(default=0, editable=False)
    pin_locked_until = models.DateTimeField(null=True, blank=True, editable=False)

    # Personal data. Visible only to the owners and the administrator, kept
    # because a text message needs somewhere to go, and deleted with the
    # person's record when they ask (NFR-11, NFR-12).
    mobile = models.CharField(
        max_length=24, blank=True, help_text="For alerts. In full international form: +1818…"
    )
    receives_alerts = models.BooleanField(
        default=True, help_text="Unticking stops messages without removing the number."
    )
    preferred_language = models.CharField(max_length=8, default="en")
    is_active_staff = models.BooleanField(
        default=True,
        help_text="Unticking keeps all history intact but stops the person signing in.",
    )

    # Implements: FR-102, D-04.
    @property
    def can_use_pin(self) -> bool:
        """An owner must never be reachable from a tablet PIN."""
        return self.role != Role.OWNER

    # Implements: NFR-11.
    @property
    def may_see_money(self) -> bool:
        return self.role == Role.OWNER

    def __str__(self) -> str:
        return self.display_name or self.get_username()


# Implements: FR-401.
class Location(TimeStamped):
    """
    Somewhere stock can sit. Three kinds today: the restaurant, the
    Devonshire Street unit, and -- later -- a temporary event location.
    """

    class Kind(models.TextChoices):
        RESTAURANT = "RESTAURANT", "Restaurant"
        STORAGE = "STORAGE", "Storage unit"
        EVENT = "EVENT", "Event site"

    code = models.SlugField(max_length=24, unique=True)
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    address = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    # Counting cadence is set per location; the storage unit is counted
    # monthly, the restaurant weekly, with a short daily list on top.
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self) -> str:
        return self.name


# Implements: FR-402, FR-703.
class Area(TimeStamped):
    """
    Optional subdivision of a location -- dry store, walk-in, freezer, bar.
    Used to order count sheets so they follow the physical shelves.
    """

    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="areas")
    name = models.CharField(max_length=80)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["location", "sort_order", "name"]
        constraints = [models.UniqueConstraint(fields=["location", "name"], name="uniq_area_per_location")]

    def __str__(self) -> str:
        return f"{self.location.name} / {self.name}"


# Implements: FR-301.
class Supplier(TimeStamped):
    name = models.CharField(max_length=120, unique=True)
    contact_name = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    delivery_days = models.CharField(max_length=60, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name
