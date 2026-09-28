"""Warning about the refresh token before it dies, not after.

The access token renews itself, so its expiry is a non-event. The refresh
token is the one nothing can renew: when it goes, the watcher goes silent at
exactly the moment it looks healthy.
"""

from datetime import date, timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from sofreh.models import Outcome, SamadAccount, TelegramBot, TelegramChat, Watch
from sofreh.services.checker import run_watch

from .fixtures import PROGRAMS_NOT_OPEN, PROGRAMS_OPEN
from .support import FakeResponse, FakeSession, make_jwt

WEEK = date(2026, 10, 3)
FAR_FUTURE = 4883264000


def jwt_expiring_in(days):
    when = timezone.now() + timedelta(days=days)
    return make_jwt(exp=int(when.timestamp()))


class ExpiryWarningTests(TestCase):
    def setUp(self):
        self.account = SamadAccount.objects.create(
            label="default",
            access_token=make_jwt(exp=FAR_FUTURE),
            refresh_token=jwt_expiring_in(5),
        )
        self.bot = TelegramBot.objects.create(label="default", bot_token="123:AA")
        TelegramChat.objects.create(bot=self.bot, chat_id="111")
        self.watch = Watch.objects.create(
            name="Next week", account=self.account, bot=self.bot
        )

    def poll(self, api=PROGRAMS_NOT_OPEN, sends=2, **kwargs):
        samad = FakeSession(get_responses=[FakeResponse(200, api)])
        telegram = FakeSession(
            post_responses=[FakeResponse(200, {"ok": True})] * sends
        )
        report = run_watch(
            self.watch,
            week_start=WEEK,
            session=samad,
            telegram_session=telegram,
            **kwargs,
        )
        return report, telegram

    def texts(self, telegram):
        return [call["json"]["text"] for call in telegram.post_calls]

    def test_warns_while_there_is_still_time(self):
        report, telegram = self.poll()

        self.assertIn("expiry", report.delivered)
        sent = self.texts(telegram)
        self.assertEqual(len(sent), 1)
        self.assertIn("رفرش‌توکن سماد رو به اتمام است", sent[0])
        self.assertIn("5 روز", sent[0])

        self.account.refresh_from_db()
        self.assertIsNotNone(self.account.expiry_alert_sent_at)

    def test_it_is_a_nudge_not_a_nag(self):
        self.poll()

        report, telegram = self.poll()

        self.assertNotIn("expiry", report.delivered)
        self.assertEqual(telegram.post_calls, [])

    def test_a_healthy_refresh_token_says_nothing(self):
        self.account.refresh_token = jwt_expiring_in(200)
        self.account.save()

        report, telegram = self.poll()

        self.assertEqual(report.delivered, set())
        self.assertEqual(telegram.post_calls, [])

    def test_an_unreadable_refresh_token_is_not_guessed_about(self):
        self.account.refresh_token = "not-a-jwt"
        self.account.save()

        report, telegram = self.poll()

        self.assertEqual(report.delivered, set())
        self.assertEqual(telegram.post_calls, [])

    def test_hours_left_is_not_reported_as_already_expired(self):
        # Truncating days would turn "23 hours left" into "expired", which is
        # both wrong and needlessly alarming.
        self.account.refresh_token = jwt_expiring_in(0.96)
        self.account.save()

        _, telegram = self.poll()
        text = self.texts(telegram)[0]

        self.assertIn("فردا", text)
        self.assertNotIn("همین حالا منقضی شده", text)

    def test_an_already_expired_refresh_token_is_stated_plainly(self):
        self.account.refresh_token = jwt_expiring_in(-3)
        self.account.save()

        _, telegram = self.poll()

        self.assertIn("همین حالا منقضی شده", self.texts(telegram)[0])

    def test_the_warning_does_not_crowd_out_the_opening_announcement(self):
        # Both matter, and one failing must not silence the other.
        report, telegram = self.poll(api=PROGRAMS_OPEN)

        self.assertEqual(report.delivered, {"open", "expiry"})
        sent = self.texts(telegram)
        self.assertEqual(len(sent), 2)
        self.assertIn("رزرو غذا باز شد", sent[0])
        self.assertIn("رو به اتمام است", sent[1])

        self.watch.refresh_from_db()
        self.assertEqual(self.watch.notified_week_start, WEEK)

    def test_a_working_poll_does_not_cancel_the_expiry_warning(self):
        # clear_auth_failure() is about a different problem and must not
        # re-arm this warning every time a poll succeeds.
        self.poll()
        self.account.refresh_from_db()
        first_sent_at = self.account.expiry_alert_sent_at

        self.poll()

        self.account.refresh_from_db()
        self.assertEqual(self.account.expiry_alert_sent_at, first_sent_at)

    def test_pasting_new_tokens_re_arms_the_warning(self):
        self.poll()

        call_command(
            "sofreh_setup",
            f"--access-token={make_jwt(exp=FAR_FUTURE)}",
            f"--refresh-token={jwt_expiring_in(3)}",
            stdout=StringIO(),
        )

        self.account.refresh_from_db()
        self.assertIsNone(self.account.expiry_alert_sent_at)

        report, telegram = self.poll()
        self.assertIn("expiry", report.delivered)
        self.assertIn("3 روز", self.texts(telegram)[0])

    def test_dry_run_warns_without_recording(self):
        report, telegram = self.poll(dry_run=True)

        self.assertTrue(report.would_notify)
        self.assertEqual(telegram.post_calls, [])
        self.account.refresh_from_db()
        self.assertIsNone(self.account.expiry_alert_sent_at)

    def test_threshold_is_configurable(self):
        self.account.refresh_token = jwt_expiring_in(20)
        self.account.save()

        report, _ = self.poll()
        self.assertEqual(report.delivered, set())

        with self.settings(
            SOFREH={**self.settings_dict(), "TOKEN_EXPIRY_WARNING_DAYS": 30}
        ):
            report, telegram = self.poll()

        self.assertIn("expiry", report.delivered)

    def settings_dict(self):
        from django.conf import settings

        return dict(settings.SOFREH)
