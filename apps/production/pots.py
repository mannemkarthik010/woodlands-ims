"""
Cooked rice: telling the kitchen to cook the next pot before the last one runs out.

The owners' ask, 13 October 2026: when the cooked rice is about to finish,
the kitchen should be told to cook more. Sales arrive once a day, so nothing
in the system knows how many plates have gone out in the last half hour. What
it does know is when each pot was cooked -- the "Cooked a fresh pot" button --
and from those times it learns how long a pot lasts at Woodlands:

    the gap between one pot and the next, in the same service, is how long
    a pot lasted. The middle of the recent gaps (the median, so one slow
    Tuesday does not move it) is how long the next one will last.

Lunch and dinner, and the busy weekend against a quiet weeknight, run at
different speeds, so a service is compared with services like it when there
are enough of them. Until there are three gaps to learn from, the kitchen's
own guess (Item.pot_every_minutes) is used, and the screen says so.

    next pot needed by  = last pot + how long a pot lasts
    start cooking at    = needed by - minutes to cook (Item.cook_minutes)

Nothing is said outside opening hours, and nothing once the next pot would
only be needed after the service has ended.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from statistics import median

from django.db import transaction
from django.utils import timezone

from apps.catalog.models import Item, ItemMeasure
from apps.core.models import Location
from apps.core.week import LUNCH, OPENING_HOURS
from apps.production.models import BatchStatus, ProductionBatch

EARLY = timedelta(minutes=120)  # a pot cooked before opening belongs to that service
SOON = timedelta(minutes=10)  # "cook in 10 minutes" before "cook now"
LONGEST_GAP = timedelta(hours=4)  # longer than this is a break in service, not a pot lasting
MIN_GAPS = 3
LEARN_DAYS = 28
RECENT_GAPS = 12


@dataclass
class Service:
    starts: datetime
    ends: datetime

    @property
    def is_lunch(self) -> bool:
        return self.starts.time() == LUNCH[0]

    @property
    def is_weekend(self) -> bool:
        return self.starts.weekday() >= 4  # Friday to Sunday: lunch and dinner

    def like(self, other: Service) -> bool:
        return (self.is_lunch, self.is_weekend) == (other.is_lunch, other.is_weekend)


def services_on(day: date) -> list[Service]:
    tz = timezone.get_current_timezone()
    return [
        Service(datetime.combine(day, a, tzinfo=tz), datetime.combine(day, b, tzinfo=tz))
        for a, b in OPENING_HOURS[day.weekday()]
    ]


def current_service(now: datetime, cook: timedelta) -> Service | None:
    """The service under way, or the next one if its first pot should be on by now."""
    for service in services_on(now.date()):
        if service.starts - cook - SOON <= now < service.ends:
            return service
    return None


def _pots(item: Item, start: datetime, end: datetime) -> list[datetime]:
    return list(
        ProductionBatch.objects.filter(item=item, finished_at__gte=start, finished_at__lt=end)
        .exclude(status__in=[BatchStatus.IN_PROGRESS, BatchStatus.WRITTEN_OFF])
        .order_by("finished_at")
        .values_list("finished_at", flat=True)
    )


def learned_gaps(item: Item, now: datetime, like: Service | None = None) -> list[timedelta]:
    """How long recent pots lasted, newest first; only services like `like` when given."""
    gaps: list[timedelta] = []
    for back in range(LEARN_DAYS + 1):
        for service in services_on(now.date() - timedelta(days=back)):
            if like is not None and not service.like(like):
                continue
            times = _pots(item, service.starts - EARLY, min(service.ends, now))
            pairs = [b - a for a, b in zip(times, times[1:], strict=False)]
            gaps.extend(reversed([g for g in pairs if timedelta(0) < g <= LONGEST_GAP]))
        if len(gaps) >= RECENT_GAPS:
            break
    return gaps[:RECENT_GAPS]


@dataclass
class Pot:
    item: Item
    service: Service | None
    last: datetime | None  # when the last pot of this service was ready
    lasts: timedelta | None
    learned_from: int  # 0 = the kitchen's own guess
    now: datetime

    @property
    def cook(self) -> timedelta:
        return timedelta(minutes=self.item.cook_minutes or 0)

    @property
    def needed_by(self) -> datetime | None:
        if self.service is None:
            return None
        if self.last is None:
            return self.service.starts
        if self.lasts is None:
            return None
        return self.last + self.lasts

    @property
    def start_by(self) -> datetime | None:
        return self.needed_by - self.cook if self.needed_by else None

    @property
    def status(self) -> str:
        """closed, unknown, enough, ok, soon, cook, late."""
        if self.service is None:
            return "closed"
        if self.needed_by is None:
            return "unknown"
        if self.last is not None and self.needed_by >= self.service.ends:
            return "enough"  # this pot sees the service out
        if self.now >= self.needed_by and self.last is not None:
            return "late"
        if self.now >= self.start_by:
            return "cook"
        if self.now >= self.start_by - SOON:
            return "soon"
        return "ok"

    @property
    def alarm(self) -> bool:
        return self.status in ("cook", "late")

    def sentence(self) -> str:
        name = (
            self.item.name.lower().removeprefix("cooked ").strip()
        )  # "Cook rice now", not "cook cooked rice"
        at = _clock
        if self.status == "late":
            return f"Cook {name} now — the last pot ({at(self.last)}) is due to run out."
        if self.status == "cook" and self.last is None:
            return f"Cook the first pot of {name} now — service starts at {at(self.service.starts)}."
        if self.status == "cook":
            return f"Cook {name} now — the pot from {at(self.last)} runs out around {at(self.needed_by)}."
        if self.status == "soon":
            return f"Cook {name} at {at(self.start_by)} — the pot runs out around {at(self.needed_by)}."
        if self.status == "enough":
            return f"The {name} pot from {at(self.last)} should last until closing."
        if self.status == "ok":
            return f"Next pot of {name}: start at {at(self.start_by)}."
        if self.status == "unknown":
            return f"Cooked {name} at {at(self.last)}. Say how long a pot lasts to get reminders."
        return ""

    def basis(self) -> str:
        if self.learned_from:
            return f"a pot lasts about {_minutes(self.lasts)}, from the last {self.learned_from} pots"
        if self.lasts:
            return f"a pot lasts about {_minutes(self.lasts)} (the kitchen's estimate)"
        return ""


def _clock(moment: datetime) -> str:
    return timezone.localtime(moment).strftime("%-I:%M %p").lower()


def _minutes(gap: timedelta) -> str:
    m = round(gap.total_seconds() / 60)
    return f"{m} min" if m < 90 else f"{m // 60} h {m % 60} min"


def pot_for(item: Item, now: datetime | None = None) -> Pot:
    now = timezone.localtime(now or timezone.now())
    cook = timedelta(minutes=item.cook_minutes or 0)
    service = current_service(now, cook)
    last = None
    if service is not None:
        times = _pots(item, service.starts - EARLY, now + timedelta(seconds=1))
        last = times[-1] if times else None

    lasts, learned = None, 0
    gaps = learned_gaps(item, now, like=service) if service else []
    if len(gaps) < MIN_GAPS:
        gaps = learned_gaps(item, now)
    if len(gaps) >= MIN_GAPS:
        lasts, learned = median(gaps), len(gaps)
    elif item.pot_every_minutes:
        lasts = timedelta(minutes=item.pot_every_minutes)
    return Pot(item=item, service=service, last=last, lasts=lasts, learned_from=learned, now=now)


def cooked_items():
    return Item.objects.filter(is_active=True, cook_minutes__isnull=False).order_by("name")


def all_pots(now: datetime | None = None) -> list[Pot]:
    return [pot_for(item, now) for item in cooked_items()]


@transaction.atomic
def record_pot(item: Item, *, by=None) -> ProductionBatch:
    """One fresh pot, ready now: the same record "Made today" makes, in one tap."""
    from apps.production.services import finish_batch, start_batch

    location = Location.objects.filter(kind=Location.Kind.RESTAURANT, is_active=True).first()
    measure = ItemMeasure.objects.filter(item=item, name__iexact="pot").first()
    batch = start_batch(item, location=location, by=by)
    return finish_batch(batch, quantity=Decimal("1"), measure=measure, by=by, note="Cooked a fresh pot")


def tell_owners(pots: list[Pot]) -> int:
    """Each reminder recorded for the owners once -- per pot, not per look at the screen."""
    from apps.notify.models import Kind
    from apps.notify.services import notify, owners

    told = 0
    for pot in pots:
        if not pot.alarm:
            continue
        about = (
            timezone.localtime(pot.last).isoformat()
            if pot.last
            else f"first:{pot.service.starts.isoformat()}"
        )
        for owner in owners():
            key = f"POT:{pot.item.pk}:{about}:{pot.status}:{owner.pk}"
            told += bool(
                notify(recipient=owner, kind=Kind.BASE_LOW, body=pot.sentence(), dedupe_key=key[:180])
            )
    return told
