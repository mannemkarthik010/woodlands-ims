"""
Deciding who to tell, and making sure they are told once.
"""

from __future__ import annotations

from django.db import IntegrityError, transaction

from apps.core.models import Role, User
from apps.notify import channels
from apps.notify.models import Notification, Status


def owners():
    return User.objects.filter(role=Role.OWNER, is_active=True, is_active_staff=True)


@transaction.atomic
def notify(*, recipient: User, kind: str, body: str, dedupe_key: str) -> Notification | None:
    """
    Record one message, unless this exact thing has already been said.

    Returns None when it has. That is not a failure -- it is the whole point.
    An item that is low on Tuesday is still low on Wednesday, and a system that
    says so every morning gets muted within a week. A muted alert is worse than
    no alert, because everybody believes they are covered.
    """
    if not recipient.receives_alerts:
        Notification.objects.get_or_create(
            dedupe_key=dedupe_key,
            defaults={
                "recipient": recipient,
                "kind": kind,
                "body": body,
                "status": Status.SUPPRESSED,
            },
        )
        return None

    try:
        with transaction.atomic():
            return Notification.objects.create(
                recipient=recipient, kind=kind, body=body, dedupe_key=dedupe_key
            )
    except IntegrityError:
        return None  # already said


def send_pending(limit: int = 100) -> dict:
    """Carry everything that has been recorded and not yet sent."""
    sent = failed = 0
    for message in Notification.objects.filter(status=Status.PENDING).order_by("created_at")[:limit]:
        channels.deliver(message)
        sent += message.status == Status.SENT
        failed += message.status == Status.FAILED
    return {"sent": sent, "failed": failed}
