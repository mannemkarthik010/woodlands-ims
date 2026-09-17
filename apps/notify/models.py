"""
Telling somebody something, once.

The client's ask is simple to say and easy to get wrong: when stock is running
out, the owners should hear about it; when a batter needs making, the person
who will make it should hear about it. Two rules decide the design.

FIRST, THE MESSAGE IS RECORDED BEFORE IT IS SENT. A notification is a row in
this table. Sending is a separate step that can fail, be retried, or be
switched from one channel to another without the rest of the system knowing.
Nothing in the stock or production code calls a telephone network; it records
that somebody should be told, and that is the end of its responsibility.

SECOND, NOBODY IS TOLD THE SAME THING TWICE. An item that is low on Tuesday is
still low on Wednesday. A system that says so every morning is a system whose
messages get muted within a week, and a muted alert is worse than none --
everybody believes they are covered. Every notification carries a `dedupe_key`
that says what it is about and over what period, and the key is unique.
"""

from django.db import models

from apps.core.models import TimeStamped


class Channel(models.TextChoices):
    SMS = "SMS", "Text message"
    SLACK = "SLACK", "Slack"
    EMAIL = "EMAIL", "Email"
    RECORDED = "RECORDED", "Recorded only"  # development, and the safe default


class Status(models.TextChoices):
    PENDING = "PENDING", "Waiting to be sent"
    SENT = "SENT", "Sent"
    FAILED = "FAILED", "Failed"
    SUPPRESSED = "SUPPRESSED", "Not sent — the person has alerts turned off"


class Kind(models.TextChoices):
    LOW_STOCK = "LOW_STOCK", "An item is running out"
    BASE_LOW = "BASE_LOW", "A base or batter is running out"
    ASSIGNMENT = "ASSIGNMENT", "Somebody has been asked to make something"
    EXPIRING = "EXPIRING", "A batch is close to its use-by"


# Implements: FR-1302, FR-1303, FR-1308.
class Notification(TimeStamped):
    recipient = models.ForeignKey("core.User", on_delete=models.PROTECT, related_name="notifications")
    kind = models.CharField(max_length=12, choices=Kind.choices, db_index=True)
    body = models.TextField(help_text="What the person will actually read.")

    # What this message is about, and over what window. Two alerts about the
    # same item on the same day share a key, and the second one is never
    # created. See FR-1303.
    dedupe_key = models.CharField(max_length=180, unique=True)

    channel = models.CharField(max_length=10, choices=Channel.choices, default=Channel.RECORDED)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    error_detail = models.TextField(blank=True)

    # Where it was sent, as it was at the time. A staff member changing their
    # number later should not rewrite what happened on Tuesday.
    sent_to = models.CharField(max_length=40, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["recipient", "status"])]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} → {self.recipient}"
