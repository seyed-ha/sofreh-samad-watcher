"""The command line: setup, one-shot check, and the loop's guard rails."""

from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from sofreh.models import Outcome, SamadAccount, TelegramBot, TelegramChat, Watch
from sofreh.services.samad import ProgramsResult

from .support import make_jwt

FAR_FUTURE = 4883264000


def fake_client(result):
    """Patch the client so command tests never touch the network."""
    client = mock.Mock()
    client.fetch_programs.return_value = result
    return mock.patch(
        "sofreh.services.checker.SamadClient", return_value=client
    )


class SetupCommandTests(TestCase):
    def test_creates_account_bot_chats_and_watch(self):
        out = StringIO()

        call_command(
            "sofreh_setup",
            "--access-token=abc",
            "--refresh-token=def",
            "--bot-token=123:AA",
            "--chat-ids=111,222",
            stdout=out,
        )

        account = SamadAccount.objects.get(label="default")
        self.assertEqual(account.access_token, "abc")
        self.assertEqual(account.refresh_token, "def")

        bot = TelegramBot.objects.get(label="default")
        self.assertEqual(sorted(bot.active_chat_ids()), ["111", "222"])

        watch = Watch.objects.get(name="Next week")
        self.assertEqual(watch.account, account)
        self.assertEqual(watch.bot, bot)
        self.assertEqual(watch.weeks_ahead, 0)

    def test_is_idempotent(self):
        args = [
            "--access-token=abc",
            "--bot-token=123:AA",
            "--chat-ids=111",
        ]
        call_command("sofreh_setup", *args, stdout=StringIO())
        call_command("sofreh_setup", *args, stdout=StringIO())

        self.assertEqual(SamadAccount.objects.count(), 1)
        self.assertEqual(TelegramBot.objects.count(), 1)
        self.assertEqual(TelegramChat.objects.count(), 1)
        self.assertEqual(Watch.objects.count(), 1)

    def test_new_tokens_clear_a_standing_auth_alert(self):
        SamadAccount.objects.create(
            label="default",
            access_token="old",
            last_auth_error="invalid_grant",
            auth_alert_sent_at=timezone.now(),
        )

        call_command("sofreh_setup", "--access-token=new", stdout=StringIO())

        account = SamadAccount.objects.get(label="default")
        self.assertEqual(account.access_token, "new")
        self.assertEqual(account.last_auth_error, "")
        self.assertIsNone(account.auth_alert_sent_at)

    def test_first_run_needs_an_access_token(self):
        with self.assertRaisesMessage(CommandError, "--access-token is required"):
            call_command("sofreh_setup", stdout=StringIO())

    def test_reads_tokens_from_the_environment(self):
        env = {
            "SAMAD_ACCESS_TOKEN": "env-access",
            "SAMAD_REFRESH_TOKEN": "env-refresh",
            "TELEGRAM_BOT_TOKEN": "123:env",
            "TELEGRAM_CHAT_IDS": "999",
        }
        with mock.patch.dict("os.environ", env, clear=False):
            call_command("sofreh_setup", "--from-env", stdout=StringIO())

        account = SamadAccount.objects.get(label="default")
        self.assertEqual(account.access_token, "env-access")
        self.assertEqual(
            TelegramBot.objects.get(label="default").active_chat_ids(), ["999"]
        )


class CheckCommandTests(TestCase):
    def setUp(self):
        self.account = SamadAccount.objects.create(
            label="default", access_token=make_jwt(exp=FAR_FUTURE)
        )
        self.bot = TelegramBot.objects.create(label="default", bot_token="123:AA")
        TelegramChat.objects.create(bot=self.bot, chat_id="111")
        self.watch = Watch.objects.create(
            name="Next week", account=self.account, bot=self.bot
        )

    def test_reports_a_closed_week_without_notifying(self):
        out = StringIO()
        result = ProgramsResult(outcome=Outcome.NOT_OPEN, api_type="ERROR")

        with fake_client(result):
            call_command("sofreh_check", stdout=out)

        self.assertIn("not_open", out.getvalue())
        self.watch.refresh_from_db()
        self.assertEqual(self.watch.last_outcome, Outcome.NOT_OPEN)

    def test_dry_run_changes_nothing(self):
        out = StringIO()
        result = ProgramsResult(
            outcome=Outcome.OPEN, api_type="INFO", program_count=6, reservable_count=6
        )

        with fake_client(result):
            call_command("sofreh_check", "--dry-run", stdout=out)

        self.assertIn("would notify", out.getvalue())
        self.watch.refresh_from_db()
        self.assertIsNone(self.watch.notified_week_start)
        self.assertEqual(self.watch.results.count(), 0)

    def test_week_override_must_be_a_saturday(self):
        with self.assertRaisesMessage(CommandError, "must be a Saturday"):
            call_command("sofreh_check", "--week=2026-10-04", stdout=StringIO())

    def test_week_override_is_passed_through(self):
        result = ProgramsResult(outcome=Outcome.NOT_OPEN)

        with fake_client(result):
            call_command("sofreh_check", "--week=2026-10-10", stdout=StringIO())

        self.assertEqual(str(self.watch.results.get().week_start), "2026-10-10")

    def test_no_active_watch_is_a_clear_error(self):
        Watch.objects.update(is_active=False)

        with self.assertRaisesMessage(CommandError, "No active watch"):
            call_command("sofreh_check", stdout=StringIO())

    def test_can_target_a_single_watch_by_name(self):
        Watch.objects.create(name="Other", account=self.account, bot=self.bot)
        result = ProgramsResult(outcome=Outcome.NOT_OPEN)

        with fake_client(result):
            call_command("sofreh_check", "--watch=Other", stdout=StringIO())

        self.assertEqual(self.watch.results.count(), 0)
        self.assertEqual(Watch.objects.get(name="Other").results.count(), 1)


class WatchCommandTests(TestCase):
    def setUp(self):
        account = SamadAccount.objects.create(
            label="default", access_token=make_jwt(exp=FAR_FUTURE)
        )
        self.watch = Watch.objects.create(name="Next week", account=account)

    def test_max_polls_stops_the_loop(self):
        result = ProgramsResult(outcome=Outcome.NOT_OPEN)

        with fake_client(result), mock.patch(
            "sofreh.management.commands.sofreh_watch.time.sleep"
        ) as sleep:
            call_command(
                "sofreh_watch", "--max-polls=2", "--interval=45", stdout=StringIO()
            )

        self.assertEqual(self.watch.results.count(), 2)
        # Slept between the two polls, but not after the last one.
        sleep.assert_called_once_with(45)

    def test_interval_has_a_floor_so_a_typo_cannot_hammer_the_server(self):
        result = ProgramsResult(outcome=Outcome.NOT_OPEN)

        with fake_client(result), mock.patch(
            "sofreh.management.commands.sofreh_watch.time.sleep"
        ) as sleep:
            call_command(
                "sofreh_watch", "--max-polls=2", "--interval=1", stdout=StringIO()
            )

        sleep.assert_called_once_with(30)

    def test_a_failing_poll_does_not_kill_the_loop(self):
        with mock.patch(
            "sofreh.management.commands.sofreh_watch.run_all",
            side_effect=RuntimeError("boom"),
        ):
            out = StringIO()
            call_command(
                "sofreh_watch", "--max-polls=1", "--interval=30", stdout=out
            )

        self.assertIn("poll failed", out.getvalue())


class DiscoverChatsCommandTests(TestCase):
    def setUp(self):
        SamadAccount.objects.create(label="default", access_token="abc")
        self.bot = TelegramBot.objects.create(label="default", bot_token="123:AA")

    def patched(self, chats, error=""):
        notifier = mock.Mock()
        notifier.discover_chats.return_value = (chats, error)
        return mock.patch(
            "sofreh.services.telegram.TelegramNotifier", return_value=notifier
        )

    def test_adds_whoever_has_messaged_the_bot(self):
        out = StringIO()
        found = [{"chat_id": "111", "type": "private", "name": "hamid"}]

        with self.patched(found):
            call_command("sofreh_setup", "--discover-chats", stdout=out)

        self.assertIn("Added chat 111", out.getvalue())
        self.assertEqual(self.bot.active_chat_ids(), ["111"])

    def test_running_twice_does_not_duplicate(self):
        found = [{"chat_id": "111", "type": "private", "name": "hamid"}]

        with self.patched(found):
            call_command("sofreh_setup", "--discover-chats", stdout=StringIO())
            out = StringIO()
            call_command("sofreh_setup", "--discover-chats", stdout=out)

        self.assertIn("Already had chat 111", out.getvalue())
        self.assertEqual(TelegramChat.objects.count(), 1)

    def test_explains_the_telegram_gotcha_when_nobody_has_written(self):
        with self.patched([]):
            with self.assertRaisesMessage(CommandError, "send the bot any message"):
                call_command("sofreh_setup", "--discover-chats", stdout=StringIO())

    def test_surfaces_a_telegram_error(self):
        with self.patched([], error="HTTP 401: Unauthorized"):
            with self.assertRaisesMessage(CommandError, "Unauthorized"):
                call_command("sofreh_setup", "--discover-chats", stdout=StringIO())
