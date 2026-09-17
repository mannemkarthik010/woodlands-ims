"""
How a recorded message actually reaches somebody.

Each channel does one thing: take a Notification that has been recorded and
deliver it, or say why it could not. None of them decides *whether* to send --
that was decided when the row was written -- and none of them is allowed to
raise into the caller. A stock count must not fail because a telephone network
was unreachable.

The default channel sends nothing at all. That is deliberate: text messages
cost the client money every month, and a system that starts texting the moment
it is deployed is a system that spent somebody else's money without asking.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.utils import timezone

from apps.notify.models import Channel, Notification, Status

log = logging.getLogger(__name__)


class Recorded:
    """
    Writes the message and stops. Used in development, in tests, and in
    production until the owners have agreed to pay for text messages.

    It is not a stub. The message is in the database, visible on screen, and
    nothing is lost by having been recorded rather than sent -- switching the
    channel on later does not need a single line of this system to change.
    """

    code = Channel.RECORDED

    def send(self, message: Notification) -> bool:
        log.info("Notification %s recorded, not sent: %s", message.pk, message.body)
        return True

    def address(self, user) -> str:
        return ""


class Sms:
    """
    Text messages through Twilio.

    Chosen because it reaches the kitchen staff who are not in Slack and do not
    read email during service. It costs roughly a cent a message plus a monthly
    fee for the number, which is the client's money, so nothing here runs
    unless NOTIFY_CHANNEL is explicitly set to SMS.
    """

    code = Channel.SMS

    def address(self, user) -> str:
        return (user.mobile or "").strip()

    def send(self, message: Notification) -> bool:
        if not all([settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN, settings.TWILIO_FROM_NUMBER]):
            message.error_detail = "Text messages are switched on but Twilio is not configured."
            return False

        try:
            from twilio.rest import Client  # imported here so the package is optional
        except ImportError:  # pragma: no cover - depends on the deployment
            message.error_detail = "The twilio package is not installed on this server."
            return False

        try:
            client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
            client.messages.create(to=message.sent_to, from_=settings.TWILIO_FROM_NUMBER, body=message.body)
            return True
        except Exception as problem:  # pragma: no cover - network
            # Never raised onwards. A count, a transfer or a production batch
            # must not fail because a telephone network was unreachable.
            message.error_detail = f"{type(problem).__name__}: {problem}"[:500]
            return False


CHANNELS = {Channel.RECORDED: Recorded(), Channel.SMS: Sms()}


def current() -> Recorded | Sms:
    return CHANNELS.get(settings.NOTIFY_CHANNEL, CHANNELS[Channel.RECORDED])


def deliver(message: Notification) -> Notification:
    """Try to send one recorded message, and record what happened either way."""
    channel = current()
    message.channel = channel.code
    message.attempts += 1
    message.sent_to = channel.address(message.recipient)

    if channel.code != Channel.RECORDED and not message.sent_to:
        message.status = Status.FAILED
        message.error_detail = f"No mobile number on record for {message.recipient}."
    elif channel.send(message):
        message.status = Status.SENT
        message.sent_at = timezone.now()
    else:
        message.status = Status.FAILED

    message.save(
        update_fields=[
            "channel",
            "status",
            "sent_at",
            "attempts",
            "error_detail",
            "sent_to",
            "updated_at",
        ]
    )
    return message
