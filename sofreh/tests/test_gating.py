"""Asking less: rolling weeks, announced weeks, and followed watches."""

from datetime import date, timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from sofreh.dates import week_start_for
from sofreh.models import CheckResult, Outcome, SamadAccount, TelegramBot, TelegramChat, Watch
from sofreh.services.checker import run_watch

from .fixtures import PROGRAMS_NOT_OPEN, PROGRAMS_OPEN
from .support import FakeResponse, FakeSession, make_jwt

FAR_FUTURE = 4883264000
WEEK = date(2026, 10, 3)


class UpcomingSaturdayTests(TestCase):
    """Saturday belongs to the week it opens; the roll happens on Sunday."""

    def test_midweek_targets_the_saturday_ahead(self):
        self.assertEqual(week_start_for(0, date(2026, 9, 28)), date(2026, 10, 3))
        self.assertEqual(week_start_for(0, date(2026, 10, 2)), date(2026, 10, 3))

    def test_on_saturday_the_target_is_that_same_saturday(self):
        self.assertEqual(week_start_for(0, date(2026, 10, 3)), date(2026, 10, 3))

    def test_on_sunday_it_rolls_to_the_next_one(self):
        self.assertEqual(week_start_for(0, date(2026, 10, 4)), date(2026, 10, 10))

    def test_offset_looks_a_further_week_out(self):
        self.assertEqual(week_start_for(1, date(2026, 9, 28)), date(2026, 10, 10))


class SkipTests(TestCase):
    def setUp(self):
        self.account = SamadAccount.objects.create(
            label="default", access_token=make_jwt(exp=FAR_FUTURE)
        )
        self.bot = TelegramBot.objects.create(label="default", bot_token="123:AA")
        TelegramChat.objects.create(bot=self.bot, chat_id="111")
        self.lunch = Watch.objects.create(
            name="lunch", account=self.account, bot=self.bot, self_id="1"
        )
        self.dinner = Watch.objects.create(
            name="dinner",
            account=self.account,
            bot=self.bot,
            self_id="11",
            follows=self.lunch,
        )

    def poll(self, watch, responses=(), telegram=1, **kwargs):
        samad = FakeSession(get_responses=list(responses))
        tg = FakeSession(post_responses=[FakeResponse(200, {"ok": True})] * telegram)
        report = run_watch(
            watch, week_start=WEEK, session=samad, telegram_session=tg, **kwargs
        )
        return report, samad, tg

    # -- the follow gate ---------------------------------------------------

    def test_a_followed_watch_takes_one_baseline_reading(self):
        report, samad, _ = self.poll(
            self.dinner, [FakeResponse(200, PROGRAMS_NOT_OPEN)]
        )

        self.assertEqual(report.result.outcome, Outcome.NOT_OPEN)
        self.assertEqual(len(samad.get_calls), 1)

    def test_then_it_stops_asking_until_the_watch_it_follows_opens(self):
        self.poll(self.dinner, [FakeResponse(200, PROGRAMS_NOT_OPEN)])

        report, samad, _ = self.poll(self.dinner)

        self.assertEqual(report.result.outcome, Outcome.SKIPPED)
        self.assertEqual(samad.get_calls, [])
        self.assertIn("Waiting for lunch", " ".join(report.notes))

    def test_once_the_leader_opens_the_follower_is_polled_again(self):
        self.poll(self.dinner, [FakeResponse(200, PROGRAMS_NOT_OPEN)])
        # Lunch opens, which is recorded as a CheckResult.
        self.poll(self.lunch, [FakeResponse(200, PROGRAMS_OPEN)])

        report, samad, _ = self.poll(
            self.dinner, [FakeResponse(200, PROGRAMS_OPEN)]
        )

        self.assertEqual(report.result.outcome, Outcome.OPEN)
        self.assertEqual(len(samad.get_calls), 1)
        self.assertTrue(report.notified)

    def test_the_gate_reads_history_so_it_survives_a_restart(self):
        CheckResult.objects.create(
            watch=self.lunch, week_start=WEEK, outcome=Outcome.OPEN
        )

        self.assertTrue(self.dinner.gate_is_open(WEEK))

    def test_the_gate_is_per_week_not_forever(self):
        CheckResult.objects.create(
            watch=self.lunch, week_start=date(2026, 9, 26), outcome=Outcome.OPEN
        )

        self.assertFalse(self.dinner.gate_is_open(WEEK))

    def test_an_ungated_watch_is_never_skipped_for_that_reason(self):
        report, samad, _ = self.poll(
            self.lunch, [FakeResponse(200, PROGRAMS_NOT_OPEN)]
        )

        self.assertEqual(report.result.outcome, Outcome.NOT_OPEN)
        self.assertEqual(len(samad.get_calls), 1)

    def test_safety_poll_means_a_self_that_opens_first_is_not_missed(self):
        self.poll(self.dinner, [FakeResponse(200, PROGRAMS_NOT_OPEN)])
        self.dinner.refresh_from_db()

        # Pretend the last real poll was over an hour ago.
        self.dinner.last_checked_at = timezone.now() - timedelta(minutes=61)
        self.dinner.save(update_fields=["last_checked_at"])

        report, samad, _ = self.poll(
            self.dinner, [FakeResponse(200, PROGRAMS_OPEN)]
        )

        self.assertEqual(report.result.outcome, Outcome.OPEN)
        self.assertEqual(len(samad.get_calls), 1)
        self.assertTrue(report.notified)

    def test_the_safety_poll_can_be_turned_off(self):
        self.poll(self.dinner, [FakeResponse(200, PROGRAMS_NOT_OPEN)])
        self.dinner.refresh_from_db()
        self.dinner.last_checked_at = timezone.now() - timedelta(days=3)
        self.dinner.save(update_fields=["last_checked_at"])

        with self.settings(SOFREH={**self._conf(), "GATED_RECHECK_MINUTES": 0}):
            report, samad, _ = self.poll(self.dinner)

        self.assertEqual(report.result.outcome, Outcome.SKIPPED)
        self.assertEqual(samad.get_calls, [])

    # -- the announced-week skip -------------------------------------------

    def test_an_announced_week_is_not_asked_about_again(self):
        self.poll(self.lunch, [FakeResponse(200, PROGRAMS_OPEN)])

        report, samad, tg = self.poll(self.lunch)

        self.assertEqual(report.result.outcome, Outcome.SKIPPED)
        self.assertEqual(samad.get_calls, [])
        self.assertEqual(tg.post_calls, [])

    def test_a_new_target_week_resumes_polling(self):
        self.poll(self.lunch, [FakeResponse(200, PROGRAMS_OPEN)])

        samad = FakeSession(get_responses=[FakeResponse(200, PROGRAMS_NOT_OPEN)])
        report = run_watch(
            self.lunch,
            week_start=date(2026, 10, 10),
            session=samad,
            telegram_session=FakeSession(),
        )

        self.assertEqual(report.result.outcome, Outcome.NOT_OPEN)
        self.assertEqual(len(samad.get_calls), 1)

    def test_force_notify_overrides_every_skip(self):
        self.poll(self.lunch, [FakeResponse(200, PROGRAMS_OPEN)])

        report, samad, _ = self.poll(
            self.lunch, [FakeResponse(200, PROGRAMS_OPEN)], force_notify=True
        )

        self.assertEqual(report.result.outcome, Outcome.OPEN)
        self.assertEqual(len(samad.get_calls), 1)

    def test_skipping_is_not_mistaken_for_a_problem(self):
        self.poll(self.lunch, [FakeResponse(200, PROGRAMS_OPEN)])

        report, _, _ = self.poll(self.lunch)

        self.assertFalse(report.result.is_problem)

    def _conf(self):
        from django.conf import settings

        return dict(settings.SOFREH)


class WeeksAheadDefaultTests(TestCase):
    """The CLI default must match the model default.

    They disagreed once: --weeks-ahead still defaulted to 1 after the rebase
    onto the upcoming Saturday, so merely re-running the command to set an
    unrelated option pushed a watch a week into the future.
    """

    def setUp(self):
        SamadAccount.objects.create(label="default", access_token="abc")
        TelegramBot.objects.create(label="default", bot_token="123:AA")

    def add_dorm(self, *extra):
        call_command(
            "sofreh_add_watch",
            "--name=dorm",
            "--self-id=11",
            *extra,
            stdout=StringIO(),
        )
        return Watch.objects.get(name="dorm")

    def test_add_watch_defaults_to_the_upcoming_saturday(self):
        self.assertEqual(self.add_dorm().weeks_ahead, 0)

    def test_setup_defaults_to_the_upcoming_saturday(self):
        call_command(
            "sofreh_setup", "--access-token=abc", stdout=StringIO()
        )
        self.assertEqual(Watch.objects.get(name="Next week").weeks_ahead, 0)

    def test_updating_a_watch_does_not_push_it_a_week_out(self):
        before = self.add_dorm().target_week_start()

        after = self.add_dorm("--weeks-ahead=0").target_week_start()

        self.assertEqual(after, before)
