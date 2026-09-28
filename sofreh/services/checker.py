"""One poll of one watch: ask Samad, decide whether to speak, write it down.

The decisions here are all about staying quiet. An open week is announced once,
not every five minutes; a dead token produces one warning, not one per poll;
and an unchanged error stays silent until it changes or clears.
"""

import logging
from dataclasses import dataclass, field

from django.conf import settings
from django.utils import timezone

from ..models import CheckResult, Outcome, TelegramBot, Watch
from . import messages
from .samad import ProgramsResult, SamadClient
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
    delivered: set = field(default_factory=set)
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

    skip = _skip_reason(watch, week_start, force_notify)
    if skip:
        # No request is made at all -- that is the whole point -- so nothing is
        # recorded either, and last_checked_at keeps meaning "last real poll".
        report = CheckReport(
            watch=watch,
            week_start=week_start,
            result=ProgramsResult(outcome=Outcome.SKIPPED, detail=skip),
        )
        report.notes.append(skip)
        logger.info(report.summary)
        return report

    client = SamadClient(watch.account, session=session)
    result = client.fetch_programs(week_start, self_id=watch.self_id)

    report = CheckReport(watch=watch, week_start=week_start, result=result)
    outgoing = _messages_for(watch, week_start, result, previous, force_notify, report)

    if outgoing:
        if dry_run:
            report.would_notify = True
        else:
            for kind, text in outgoing:
                deliveries = _deliver(watch, text, telegram_session, report)
                report.deliveries.extend(deliveries)
                if any(delivery.ok for delivery in deliveries):
                    # Track what actually landed: the open announcement and the
                    # expiry warning are marked done separately, so one failing
                    # cannot silence the other next time.
                    report.delivered.add(kind)
            report.notified = bool(report.delivered)

    if not dry_run:
        _persist(watch, week_start, result, report)

    logger.info(report.summary)
    return report


def run_all(queryset=None, **kwargs):
    watches = queryset if queryset is not None else Watch.objects.filter(is_active=True)
    return [run_watch(watch, **kwargs) for watch in watches]


# -- deciding ----------------------------------------------------------------


def _skip_reason(watch, week_start, force_notify):
    """Why this poll can be skipped, or None to go ahead and ask.

    Two savings, both safe:

    * A week we have already announced will not change its mind, so there is
      nothing left to learn until the target rolls to the next Saturday.
    * A watch that `follows` another has no reason to ask until the one it
      follows has opened -- with a periodic safety poll so a self that
      publishes first can never be missed entirely.
    """
    if force_notify:
        return None

    if watch.already_notified_for(week_start):
        return f"Already announced {week_start}; nothing left to ask."

    if watch.gate_is_open(week_start):
        return None

    if watch.last_checked_at is None:
        # Never polled: take one reading so the safety timer has a baseline.
        return None

    minutes = settings.SOFREH["GATED_RECHECK_MINUTES"]
    if minutes:
        since = (timezone.now() - watch.last_checked_at).total_seconds() / 60
        if since >= minutes:
            return None

    return f"Waiting for {watch.follows.name} to open first."


def _messages_for(watch, week_start, result, previous, force_notify, report):
    """Everything worth saying about this poll, as (kind, text) pairs."""
    outgoing = []
    account = watch.account

    if result.outcome == Outcome.OPEN:
        if watch.already_notified_for(week_start) and not force_notify:
            report.notes.append("Already announced this week; staying quiet.")
        else:
            outgoing.append(
                ("open", messages.reservation_open(watch, week_start, result))
            )

    elif result.outcome == Outcome.AUTH_FAILED:
        # One warning per outage. clear_auth_failure() re-arms it on recovery.
        if account.auth_alert_sent_at and not force_notify:
            report.notes.append("Auth alert already sent; staying quiet.")
        else:
            outgoing.append(("auth", messages.auth_problem(account, result.detail)))

    elif result.outcome in (Outcome.API_ERROR, Outcome.NETWORK_ERROR):
        repeated = previous is not None and previous.outcome == result.outcome
        if repeated and not force_notify:
            report.notes.append("Same problem as last time; staying quiet.")
        else:
            outgoing.append(
                ("problem", messages.api_problem(watch, week_start, result))
            )

    elif account.auth_alert_sent_at:
        # NOT_OPEN after an outage the user fixed by hand: say we are back.
        outgoing.append(("recovered", messages.recovered(watch, week_start)))

    expiring = _expiry_warning(account, force_notify, report)
    if expiring:
        outgoing.append(expiring)

    return outgoing


def _expiry_warning(account, force_notify, report):
    """Warn once, before the refresh token dies rather than after.

    The access token renews itself, so its expiry is a non-event. The refresh
    token is different: nothing can renew it, and when it goes the watcher goes
    silent exactly when it looks healthy.
    """
    if account.expiry_alert_sent_at and not force_notify:
        return None

    days_left = account.refresh_token_days_left()
    if days_left is None:
        return None

    threshold = settings.SOFREH["TOKEN_EXPIRY_WARNING_DAYS"]
    if days_left > threshold:
        return None

    report.notes.append(f"Refresh token expires in {days_left} days.")
    return (
        "expiry",
        messages.token_expiring(
            account, days_left, account.refresh_token_expires_at
        ),
    )


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
        if "auth" in report.delivered and not account.auth_alert_sent_at:
            account.auth_alert_sent_at = timezone.now()
            account.save(update_fields=["auth_alert_sent_at", "updated_at"])
    elif result.outcome in (Outcome.OPEN, Outcome.NOT_OPEN):
        # A working poll means the credentials are fine again. This does not
        # touch the expiry warning, which is about a different problem.
        account.clear_auth_failure()

    if "expiry" in report.delivered:
        account.expiry_alert_sent_at = timezone.now()
        account.save(update_fields=["expiry_alert_sent_at", "updated_at"])

    if result.outcome == Outcome.OPEN and "open" in report.delivered:
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
