"""
Yesterday's sales, arriving by themselves.

Shift4 can email the "Sales Summary by Item" report every morning. The email
goes to an inbound address (Postmark, Mailgun, SendGrid, or a Cloudflare
email worker), which posts it here; this reads it as yesterday's sales and,
when every line is accounted for, records it -- the same `import_day` and
`post_day` an owner uses by hand, with the same checks.

What it will not do on its own:

- Replace a day that already has a file. If an owner uploaded one by hand,
  theirs stands; replacing undoes stock movements, and that is a decision.
- Record a day with a menu button nobody has matched yet, or a tub with no
  size. It reads the file, keeps it, and tells the owners what needs a
  decision; once they have made it, they record the day from its page.

Every outcome, good or not, leaves the owners a message, so a morning with no
email is noticed rather than assumed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.utils import timezone

from apps.core.models import Location
from apps.notify.models import Kind
from apps.notify.services import notify, owners
from apps.sales import daily
from apps.sales.models import SalesImport, SalesImportStatus


@dataclass
class Outcome:
    status: str  # recorded · waiting · already · duplicate · refused
    message: str
    sales_import: SalesImport | None = None


def _tell_owners(day, text: str, key: str) -> None:
    for owner in owners():
        notify(recipient=owner, kind=Kind.SALES, body=text, dedupe_key=f"sales:{key}:{day}:{owner.pk}"[:180])


def receive(
    data: bytes, *, filename: str, location: Location, received_at: datetime | None = None, business_date=None
) -> Outcome:
    """One emailed report. The day is the one before it arrived, unless given."""
    received_at = received_at or timezone.now()
    day = business_date or timezone.localtime(received_at).date() - timedelta(days=1)
    label = f"{day:%a %-d %b}"

    same = (
        SalesImport.objects.filter(source_sha256=daily._fingerprint(data))
        .exclude(status__in=[SalesImportStatus.SUPERSEDED, SalesImportStatus.FAILED])
        .first()
    )
    if same:
        # The provider retried, or the same email was forwarded twice.
        return Outcome("duplicate", f"Already have this file, for {same.business_date:%a %-d %b}.", same)

    try:
        sales_import = daily.import_day(data, filename=filename, business_date=day, location=location)
    except daily.DayAlreadyImported as e:
        _tell_owners(
            day, f"Sales email for {label} came in, but that day already has a file. Kept yours.", "already"
        )
        return Outcome("already", f"{label} already has a sales file; it was left as it is.", e.existing)
    except daily.SalesError as e:
        _tell_owners(
            day, f"Sales email for {label} could not be read: {e} Please upload it by hand.", "refused"
        )
        return Outcome("refused", str(e))

    preview = daily.preview(sales_import)
    if sales_import.is_safe_to_post and not preview.needs_portion:
        daily.post_day(sales_import)
        sales_import.refresh_from_db()
        _tell_owners(
            day,
            f"Sales for {label} recorded: {preview.total_quantity.normalize():f} items, "
            f"${preview.total_net:,.2f}. Stock has been updated.",
            "recorded",
        )
        return Outcome("recorded", f"{label} recorded.", sales_import)

    waiting = [line.pos_item.pos_name for line in preview.unmatched + preview.needs_portion]
    _tell_owners(
        day,
        f"Sales for {label} came in. {len(waiting)} menu item(s) need a decision before stock is "
        f"updated: {', '.join(waiting[:5])}{'…' if len(waiting) > 5 else ''}. Open Daily sales to finish.",
        "waiting",
    )
    return Outcome("waiting", f"{label} read; {len(waiting)} item(s) need a decision.", sales_import)
