"""A single read-only status page.

The admin is where things get configured; this page answers the one question
worth answering at a glance: is next week open yet, and is the watcher alive?
No tokens are rendered here — see the admin for those.
"""

from django.conf import settings
from django.shortcuts import render
from django.utils import timezone

from .models import CheckResult, Outcome, SamadAccount, TelegramBot, Watch


def status(request):
    watches = []
    for watch in Watch.objects.select_related("account", "bot").order_by("name"):
        latest = watch.results.first()
        watches.append(
            {
                "watch": watch,
                "target_week": watch.target_week_start(),
                "latest": latest,
                "is_open": latest is not None and latest.outcome == Outcome.OPEN,
                "has_problem": latest is not None and latest.is_problem,
            }
        )

    account = SamadAccount.get_active()
    bot = TelegramBot.get_active()

    return render(
        request,
        "sofreh/status.html",
        {
            "watches": watches,
            "recent": CheckResult.objects.select_related("watch")[:25],
            "account": account,
            "access_expires_at": account.access_token_expires_at if account else None,
            "refresh_expires_at": account.refresh_token_expires_at if account else None,
            "refresh_days_left": account.refresh_token_days_left() if account else None,
            "expiry_threshold": settings.SOFREH["TOKEN_EXPIRY_WARNING_DAYS"],
            "auth_error": account.last_auth_error if account else "",
            "bot": bot,
            "chat_count": len(bot.active_chat_ids()) if bot else 0,
            "now": timezone.localtime(),
        },
    )
