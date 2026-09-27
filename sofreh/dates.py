"""Week arithmetic for the Samad dining calendar.

The dining week starts on Saturday, so every programs query is keyed by the
Saturday that opens the week. `weeks_ahead=1` is the usual case: the week after
the one we are living in, which is the week that has not opened for reservation
yet.
"""

from datetime import date, timedelta

from django.utils import timezone

# date.weekday(): Monday is 0, so Saturday is 5.
SATURDAY = 5


def current_week_start(today=None):
    """The Saturday that opened the week `today` falls in (today if Saturday)."""
    today = today or timezone.localdate()
    return today - timedelta(days=(today.weekday() - SATURDAY) % 7)


def week_start_for(weeks_ahead=1, today=None):
    """The Saturday opening the week `weeks_ahead` weeks after the current one."""
    return current_week_start(today) + timedelta(weeks=weeks_ahead)


def as_api_param(week_start):
    """Format a date the way the programs endpoint expects it."""
    if isinstance(week_start, date):
        week_start = week_start.isoformat()
    return f"{week_start} 00:00:00"
