"""The quiet-by-default rules: announce once, warn once, recover once."""

from datetime import date

import requests
from django.test import TestCase

from sofreh.models import CheckResult, Outcome, SamadAccount, TelegramBot, TelegramChat, Watch
from sofreh.services.checker import run_all, run_watch

from .fixtures import PROGRAMS_NOT_OPEN, PROGRAMS_OPEN
from .support import FakeResponse, FakeSession, make_jwt

FAR_FUTURE = 4883264000
WEEK = date(2026, 10, 3)


class CheckerTests(TestCase):
    def setUp(self):
        self.account = SamadAccount.objects.create(
            label="acc",
            access_token=make_jwt(exp=FAR_FUTURE),
            refresh_token=make_jwt(exp=FAR_FUTURE),
        )
        self.bot = TelegramBot.objects.create(label="bot", bot_token="123:AA")
        TelegramChat.objects.create(bot=self.bot, chat_id="111")
        self.watch = Watch.objects.create(
            name="Next week", account=self.account, bot=self.bot
        )

    def run_once(self, api_response, telegram_ok=True, **kwargs):
        samad = FakeSession(get_responses=[api_response])
        telegram = FakeSession(
            post_responses=[
                FakeResponse(200, {"ok": True})
                if telegram_ok
                else FakeResponse(400, text="chat not found")
            ]
        )
        report = run_watch(
            self.watch,
            week_start=WEEK,
            session=samad,
            telegram_session=telegram,
            **kwargs,
        )
        return report, telegram

    def sent_text(self, telegram):
        return telegram.post_calls[0]["json"]["text"]

    # -- the happy path --------------------------------------------------

    def test_open_week_is_announced_once(self):
        report, telegram = self.run_once(FakeResponse(200, PROGRAMS_OPEN))

        self.assertEqual(report.result.outcome, Outcome.OPEN)
        self.assertTrue(report.notified)
        self.assertIn("رزرو غذا باز شد", self.sent_text(telegram))

        self.watch.refresh_from_db()
        self.assertEqual(self.watch.notified_week_start, WEEK)
        self.assertEqual(self.watch.last_outcome, Outcome.OPEN)
        self.assertTrue(CheckResult.objects.get().notified)

    def test_second_poll_of_the_same_open_week_stays_quiet(self):
        self.run_once(FakeResponse(200, PROGRAMS_OPEN))

        report, telegram = self.run_once(FakeResponse(200, PROGRAMS_OPEN))

        self.assertFalse(report.notified)
        self.assertEqual(telegram.post_calls, [])
        self.assertEqual(CheckResult.objects.count(), 2)

    def test_force_notify_reannounces(self):
        self.run_once(FakeResponse(200, PROGRAMS_OPEN))

        report, telegram = self.run_once(
            FakeResponse(200, PROGRAMS_OPEN), force_notify=True
        )

        self.assertTrue(report.notified)
        self.assertEqual(len(telegram.post_calls), 1)

    def test_a_new_week_is_announced_again(self):
        self.run_once(FakeResponse(200, PROGRAMS_OPEN))
        self.watch.refresh_from_db()

        samad = FakeSession(get_responses=[FakeResponse(200, PROGRAMS_OPEN)])
        telegram = FakeSession(post_responses=[FakeResponse(200, {"ok": True})])
        report = run_watch(
            self.watch,
            week_start=date(2026, 10, 10),
            session=samad,
            telegram_session=telegram,
        )

        self.assertTrue(report.notified)

    def test_failed_delivery_does_not_count_as_announced(self):
        report, _ = self.run_once(
            FakeResponse(200, PROGRAMS_OPEN), telegram_ok=False
        )

        self.assertFalse(report.notified)
        self.watch.refresh_from_db()
        # Still unannounced, so the next poll tries again.
        self.assertIsNone(self.watch.notified_week_start)

    # -- waiting ----------------------------------------------------------

    def test_closed_week_says_nothing(self):
        report, telegram = self.run_once(FakeResponse(200, PROGRAMS_NOT_OPEN))

        self.assertEqual(report.result.outcome, Outcome.NOT_OPEN)
        self.assertFalse(report.notified)
        self.assertEqual(telegram.post_calls, [])
        self.assertEqual(CheckResult.objects.get().outcome, Outcome.NOT_OPEN)

    # -- trouble ----------------------------------------------------------

    def test_dead_tokens_warn_once(self):
        def poll():
            samad = FakeSession(
                get_responses=[FakeResponse(401, text="Unauthorized")],
                post_responses=[FakeResponse(400, text="invalid_grant")],
            )
            telegram = FakeSession(post_responses=[FakeResponse(200, {"ok": True})])
            report = run_watch(
                self.watch, week_start=WEEK, session=samad, telegram_session=telegram
            )
            return report, telegram

        first, telegram = poll()
        self.assertEqual(first.result.outcome, Outcome.AUTH_FAILED)
        self.assertTrue(first.notified)
        self.assertIn("توکن سماد کار نمی‌کند", self.sent_text(telegram))

        second, telegram = poll()
        self.assertFalse(second.notified)
        self.assertEqual(telegram.post_calls, [])

        self.account.refresh_from_db()
        self.assertIn("invalid_grant", self.account.last_auth_error)
        self.assertIsNotNone(self.account.auth_alert_sent_at)

    def test_recovery_after_an_auth_outage_is_announced_once(self):
        samad = FakeSession(
            get_responses=[FakeResponse(401, text="Unauthorized")],
            post_responses=[FakeResponse(400, text="invalid_grant")],
        )
        run_watch(
            self.watch,
            week_start=WEEK,
            session=samad,
            telegram_session=FakeSession(
                post_responses=[FakeResponse(200, {"ok": True})]
            ),
        )

        report, telegram = self.run_once(FakeResponse(200, PROGRAMS_NOT_OPEN))

        self.assertTrue(report.notified)
        self.assertIn("دوباره وصل شد", self.sent_text(telegram))

        self.account.refresh_from_db()
        self.assertIsNone(self.account.auth_alert_sent_at)
        self.assertEqual(self.account.last_auth_error, "")

        # And the recovery is not repeated on the next quiet poll.
        third, telegram = self.run_once(FakeResponse(200, PROGRAMS_NOT_OPEN))
        self.assertFalse(third.notified)
        self.assertEqual(telegram.post_calls, [])

    def test_repeated_network_failure_warns_once(self):
        def poll():
            samad = FakeSession(get_responses=[requests.ConnectionError("down")])
            telegram = FakeSession(post_responses=[FakeResponse(200, {"ok": True})])
            return (
                run_watch(
                    self.watch,
                    week_start=WEEK,
                    session=samad,
                    telegram_session=telegram,
                ),
                telegram,
            )

        first, telegram = poll()
        self.assertTrue(first.notified)
        self.assertIn("پاسخ غیرمنتظره", self.sent_text(telegram))

        second, telegram = poll()
        self.assertFalse(second.notified)
        self.assertEqual(telegram.post_calls, [])

    # -- operator conveniences -------------------------------------------

    def test_dry_run_sends_nothing_and_records_nothing(self):
        report, telegram = self.run_once(
            FakeResponse(200, PROGRAMS_OPEN), dry_run=True
        )

        self.assertTrue(report.would_notify)
        self.assertFalse(report.notified)
        self.assertEqual(telegram.post_calls, [])
        self.assertEqual(CheckResult.objects.count(), 0)
        self.watch.refresh_from_db()
        self.assertIsNone(self.watch.notified_week_start)

    def test_watch_without_a_bot_falls_back_to_the_active_one(self):
        self.watch.bot = None
        self.watch.save()

        report, telegram = self.run_once(FakeResponse(200, PROGRAMS_OPEN))

        self.assertTrue(report.notified)
        self.assertEqual(len(telegram.post_calls), 1)

    def test_missing_bot_is_reported_not_raised(self):
        self.watch.bot = None
        self.watch.save()
        TelegramBot.objects.all().delete()

        report, telegram = self.run_once(FakeResponse(200, PROGRAMS_OPEN))

        self.assertFalse(report.notified)
        self.assertIn("No active Telegram bot", " ".join(report.notes))
        self.assertEqual(CheckResult.objects.count(), 1)

    def test_run_all_skips_inactive_watches(self):
        Watch.objects.create(
            name="paused", account=self.account, bot=self.bot, is_active=False
        )
        samad = FakeSession(get_responses=[FakeResponse(200, PROGRAMS_NOT_OPEN)])

        reports = run_all(session=samad, week_start=WEEK)

        self.assertEqual([r.watch.name for r in reports], ["Next week"])
