"""One poll of one watch: ask Samad, decide whether to speak, write it down.

The decisions here are all about staying quiet. An open week is announced once,
not every five minutes; a dead token produces one warning, not one per poll;
and an unchanged error stays silent until it changes or clears.
"""

import logging
from dataclasses import dataclass, field

from django.utils import timezone

from ..models import CheckResult, Outcome, TelegramBot, Watch
from . import messages
from .samad import SamadClient
from .telegram import TelegramNotifier

logger = logging.getLogger(__name__)


@dataclass
class CheckReport:
    watch: Watch
    week_start: object
    result: object
    record: CheckResult = None
    notified: bool = False
    would_notify: bool = False
    deliveries: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def summary(self):
        state = self.result.outcome
        if self.notified:
            state += " (notified)"
        elif self.would_notify:
            state += " (would notify)"
        return f"{self.watch.name} [{self.week_start}] → {state}"


def run_watch(
    watch,
    *,
    force_notify=False,
    dry_run=False,
    week_start=None,
    session=None,
    telegram_session=None,
):
    """Poll one watch and act on the answer."""
    week_start = week_start or watch.target_week_start()
    previous = watch.results.first()

    client = SamadClient(watch.account, session=session)
    result = client.fetch_programs(week_start, self_id=watch.self_id)

    report = CheckReport(watch=watch, week_start=week_start, result=result)
    text = _message_for(watch, week_start, result, previous, force_notify, report)

    if text:
        if dry_run:
            report.would_notify = True
        else:
            report.deliveries = _deliver(watch, text, telegram_session, report)
            report.notified = any(delivery.ok for delivery in report.deliveries)

    if not dry_run:
        _persist(watch, week_start, result, report)

    logger.info(report.summary)
    return report


def run_all(queryset=None, **kwargs):
    watches = queryset if queryset is not None else Watch.objects.filter(is_active=True)
    return [run_watch(watch, **kwargs) for watch in watches]


# -- deciding ----------------------------------------------------------------


def _message_for(watch, week_start, result, previous, force_notify, report):
    """Return the text to send, or None to stay quiet."""
    account = watch.account

    if result.outcome == Outcome.OPEN:
        if watch.already_notified_for(week_start) and not force_notify:
            report.notes.append("Already announced this week; staying quiet.")
            return None
        return messages.reservation_open(watch, week_start, result)

    if result.outcome == Outcome.AUTH_FAILED:
        # One warning per outage. clear_auth_failure() re-arms it on recovery.
        if account.auth_alert_sent_at and not force_notify:
            report.notes.append("Auth alert already sent; staying quiet.")
            return None
        return messages.auth_problem(account, result.detail)

    if result.outcome in (Outcome.API_ERROR, Outcome.NETWORK_ERROR):
        repeated = previous is not None and previous.outcome == result.outcome
        if repeated and not force_notify:
            report.notes.append("Same problem as last time; staying quiet.")
            return None
        return messages.api_problem(watch, week_start, result)

    # NOT_OPEN: the normal answer while we wait. Speak only to say we are back
    # after an auth outage the user had to fix by hand.
    if account.auth_alert_sent_at:
        return messages.recovered(watch, week_start)

    return None


def _deliver(watch, text, telegram_session, report):
    bot = watch.bot or TelegramBot.get_active()
    if bot is None:
        report.notes.append("No active Telegram bot configured; nothing sent.")
        logger.warning("Watch %s has nothing to notify through.", watch.name)
        return []

    deliveries = TelegramNotifier(bot, session=telegram_session).send(text)
    if not deliveries:
        report.notes.append(f"Bot {bot.label} has no active chats.")
    for delivery in deliveries:
        if not delivery.ok:
            report.notes.append(f"Chat {delivery.chat_id}: {delivery.error}")
    return deliveries


# -- recording ---------------------------------------------------------------


def _persist(watch, week_start, result, report):
    account = watch.account

    if result.outcome == Outcome.AUTH_FAILED:
        account.note_auth_failure(result.detail)
        if report.notified and not account.auth_alert_sent_at:
            account.auth_alert_sent_at = timezone.now()
            account.save(update_fields=["auth_alert_sent_at", "updated_at"])
    elif result.outcome in (Outcome.OPEN, Outcome.NOT_OPEN):
        # A working poll means the credentials are fine again.
        account.clear_auth_failure()

    if result.outcome == Outcome.OPEN and report.notified:
        watch.notified_week_start = week_start

    watch.last_checked_at = timezone.now()
    watch.last_outcome = result.outcome
    watch.save(
        update_fields=[
            "last_checked_at",
            "last_outcome",
            "notified_week_start",
            "updated_at",
        ]
    )

    report.record = CheckResult.objects.create(
        watch=watch,
        week_start=week_start,
        outcome=result.outcome,
        http_status=result.http_status,
        api_type=result.api_type,
        api_code=result.api_code,
        api_message=(result.api_message or "")[:255],
        program_count=result.program_count,
        notified=report.notified,
        detail="\n".join([result.detail] + report.notes).strip(),
    )
    return report.record
