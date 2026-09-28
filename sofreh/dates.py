"""Week arithmetic for the Samad dining calendar.

The dining week starts on Saturday, so every programs query is keyed by the
Saturday that opens the week.

The target is the **upcoming Saturday, today included**. So on Saturday itself
we are still watching that same Saturday; the roll to the following week
happens on Sunday. That matches how the week is actually lived: Saturday is
part of the week that just started, not the one after it.
"""

from datetime import date, timedelta

from django.utils import timezone

# date.weekday(): Monday is 0, so Saturday is 5.
SATURDAY = 5


def upcoming_saturday(today=None):
    """The next Saturday, counting today when today is a Saturday."""
    today = today or timezone.localdate()
    return today + timedelta(days=(SATURDAY - today.weekday()) % 7)


def current_week_start(today=None):
    """The Saturday that opened the week `today` falls in (today if Saturday)."""
    today = today or timezone.localdate()
    return today - timedelta(days=(today.weekday() - SATURDAY) % 7)


def week_start_for(weeks_ahead=0, today=None):
    """The Saturday being watched: the upcoming one, plus any week offset.

    `weeks_ahead=0` is the normal case. Use 1 to look one week further out.
    """
    return upcoming_saturday(today) + timedelta(weeks=weeks_ahead)


def as_api_param(week_start):
    """Format a date the way the programs endpoint expects it."""
    if isinstance(week_start, date):
        week_start = week_start.isoformat()
    return f"{week_start} 00:00:00"
