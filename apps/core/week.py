"""
When the restaurant is open, as the owners gave it on 6 October 2026.

    Monday              closed
    Tuesday - Thursday  dinner   5:00 - 10:00 pm
    Friday - Sunday     lunch   11:00 am - 3:00 pm, dinner 5:00 - 10:00 pm

Kept here, in one place, so a change of hours is one edit. Used where a day
with nothing in it means something different on a closed day: no sales file
for a Monday is not a missing file.
"""

from __future__ import annotations

from datetime import date, time

LUNCH = (time(11, 0), time(15, 0))
DINNER = (time(17, 0), time(22, 0))

# date.weekday(): Monday is 0.
OPENING_HOURS: dict[int, list[tuple[time, time]]] = {
    0: [],
    1: [DINNER],
    2: [DINNER],
    3: [DINNER],
    4: [LUNCH, DINNER],
    5: [LUNCH, DINNER],
    6: [LUNCH, DINNER],
}


def is_open(day: date) -> bool:
    return bool(OPENING_HOURS[day.weekday()])
