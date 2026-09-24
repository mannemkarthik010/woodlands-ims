"""Minutes shown the two ways the owners read hours: 41 h 35 m, and 41.58."""

from django import template

from apps.labour.reports import hours

register = template.Library()


@register.filter
def hm(minutes) -> str:
    """41 h 35 m. What a person checks against their own memory of the week."""
    if minutes is None:
        return "—"
    h, m = divmod(int(minutes), 60)
    return f"{h} h {m:02d} m"


@register.filter
def decimal_hours(minutes) -> str:
    """41.58. What gets multiplied by a rate."""
    if minutes is None:
        return "—"
    return f"{hours(int(minutes)):.2f}"
