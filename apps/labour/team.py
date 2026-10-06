"""
Who is working on a day, and in what job.

The owner decides the team before the day -- the night before, or in the
morning -- and the board shows it against the hours people have actually
written in. Two things are deliberately not here:

- Start and finish times. They change all the time, so a plan with times in
  it would be wrong most days, and a board that shows "late" against a time
  nobody keeps is noise. The plan is the person, the job and morning or
  evening; the hours are what the worker records.
- Anything that changes hours or pay. The plan never creates, edits or
  checks a shift. It is read next to them, nothing more.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time, timedelta

from django.db import transaction
from django.utils import timezone

from apps.core.models import Position, User
from apps.core.week import OPENING_HOURS
from apps.labour.models import Assignment, Cover, Period, Shift


class PlanError(Exception):
    """Raised with a sentence that can be shown to the owner as it is."""


@dataclass
class Row:
    person: User
    position: Position | None
    cover: str | None  # None: not on the plan, but has hours recorded
    note: str = ""
    shifts: list[Shift] = field(default_factory=list)

    @property
    def working_now(self) -> Shift | None:
        return next((s for s in self.shifts if s.clocked_out_at is None), None)

    @property
    def minutes(self) -> int:
        return sum(s.worked_minutes or 0 for s in self.shifts)

    @property
    def status(self) -> str:
        """working · recorded · waiting (no hours yet)."""
        if self.working_now:
            return "working"
        return "recorded" if self.shifts else "waiting"


@dataclass
class Board:
    day: date
    hours: list[tuple[time, time]]
    morning: list[Row]
    evening: list[Row]
    unplanned: list[Row]

    @property
    def is_open(self) -> bool:
        return bool(self.hours)

    @property
    def planned(self) -> list[Row]:
        seen, out = set(), []
        for row in self.morning + self.evening:
            if row.person.pk not in seen:
                seen.add(row.person.pk)
                out.append(row)
        return out

    @property
    def working(self) -> int:
        return sum(1 for r in self.planned + self.unplanned if r.status == "working")

    @property
    def recorded(self) -> int:
        return sum(1 for r in self.planned if r.status != "waiting")

    @property
    def waiting(self) -> int:
        return sum(1 for r in self.planned if r.status == "waiting")


def _order(row: Row):
    position = row.position
    return (position is None, position.sort_order if position else 0, str(position or ""), str(row.person))


def board(day: date) -> Board:
    plan = {
        a.employee_id: a
        for a in Assignment.objects.filter(business_date=day).select_related("employee", "position")
    }
    shifts: dict[int, list[Shift]] = {}
    for shift in (
        Shift.objects.filter(business_date=day)
        .select_related("employee", "position")
        .order_by("clocked_in_at")
    ):
        shifts.setdefault(shift.employee_id, []).append(shift)

    morning, evening = [], []
    for a in plan.values():
        cover = Cover(a.cover)
        mine = shifts.get(a.employee_id, [])
        for period, column in ((Period.MORNING, morning), (Period.EVENING, evening)):
            if cover.includes(period):
                # Someone on both sees each half's hours in its own column.
                own = [s for s in mine if s.period == period] if cover == Cover.BOTH else mine
                column.append(Row(a.employee, a.position, a.cover, a.note, own))

    unplanned = [
        Row(found[0].employee, found[0].position or found[0].employee.position, None, shifts=found)
        for employee_id, found in shifts.items()
        if employee_id not in plan
    ]
    return Board(
        day=day,
        hours=OPENING_HOURS[day.weekday()],
        morning=sorted(morning, key=_order),
        evening=sorted(evening, key=_order),
        unplanned=sorted(unplanned, key=_order),
    )


def same_day_last_week(day: date) -> date:
    return day - timedelta(days=7)


@transaction.atomic
def save_plan(day: date, rows: list[dict], *, user: User | None = None) -> int:
    """
    The whole plan for a day, as the owner left the form: one dict per person
    with `person`, `cover` ("" for not working), `position` and `note`.
    Returns how many people are on it.
    """
    on = 0
    for row in rows:
        person, cover = row["person"], row.get("cover") or ""
        if not cover:
            Assignment.objects.filter(business_date=day, employee=person).delete()
            continue
        if cover not in Cover.values:
            raise PlanError("Choose morning, evening or both.")
        position = row.get("position") or person.position
        if position is None:
            raise PlanError(f"Choose a job for {person}.")
        Assignment.objects.update_or_create(
            business_date=day,
            employee=person,
            defaults={
                "position": position,
                "cover": cover,
                "note": " ".join((row.get("note") or "").split())[:120],
                "created_by": user,
            },
        )
        on += 1
    return on


@transaction.atomic
def copy_plan(source: date, target: date, *, user: User | None = None) -> int:
    """
    Last week's team for the same day, as a starting point. People already
    planned for the target day keep what the owner gave them; people who no
    longer work here are left out.
    """
    if source == target:
        raise PlanError("That is the same day.")
    taken = set(Assignment.objects.filter(business_date=target).values_list("employee_id", flat=True))
    copied = 0
    for a in Assignment.objects.filter(business_date=source, employee__is_active=True).exclude(
        employee_id__in=taken
    ):
        Assignment.objects.create(
            business_date=target,
            employee=a.employee,
            position=a.position,
            cover=a.cover,
            note=a.note,
            created_by=user,
        )
        copied += 1
    return copied


def default_plan_day(now=None) -> date:
    """Tomorrow, once the day is under way; before 10 am, today is still being planned."""
    now = timezone.localtime(now)
    return now.date() if now.hour < 10 else now.date() + timedelta(days=1)
