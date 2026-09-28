"""The self-test command: does it actually prove the chain works?"""

from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from sofreh.models import Outcome, SamadAccount, TelegramBot, TelegramChat, Watch
from sofreh.services.samad import DaySummary, ProgramsResult
from sofreh.services.telegram import Delivery

from .support import make_jwt

FAR_FUTURE = 4883264000
MODULE = "sofreh.management.commands.sofreh_selftest"

OPEN_RESULT = ProgramsResult(
    outcome=Outcome.OPEN,
    api_type="INFO",
    program_count=30,
    reservable_count=12,
    days=[DaySummary(date="2026-10-03", day="Saturday", foods=["چلو جوجه‌کباب"])],
)
CLOSED_RESULT = ProgramsResult(
    outcome=Outcome.NOT_OPEN, api_type="ERROR", api_code=10108005
)


def fake_samad(*results):
    client = mock.Mock()
    client.fetch_programs.side_effect = list(results)
    return mock.patch(f"{MODULE}.SamadClient", return_value=client)


def fake_telegram(ok=True, sends=None):
    notifier = mock.Mock()
    notifier.check_bot.return_value = (ok, "@sofreh_bot" if ok else "Unauthorized")
    notifier.send.side_effect = sends or (lambda text, chat_ids=None: [
        Delivery(chat_id, True) for chat_id in (chat_ids or [])
    ])
    return mock.patch(f"{MODULE}.TelegramNotifier", return_value=notifier), notifier


class SelfTestCommandTests(TestCase):
    def setUp(self):
        self.account = SamadAccount.objects.create(
            label="default",
            access_token=make_jwt(exp=FAR_FUTURE),
            refresh_token=make_jwt(exp=FAR_FUTURE),
        )
        self.bot = TelegramBot.objects.create(label="default", bot_token="123:AA")
        TelegramChat.objects.create(bot=self.bot, chat_id="111")
        self.watch = Watch.objects.create(
            name="Next week", account=self.account, bot=self.bot
        )

    def test_reports_the_whole_chain_and_sends_two_messages(self):
        out = StringIO()
        patch_tg, notifier = fake_telegram()

        with fake_samad(CLOSED_RESULT, OPEN_RESULT), patch_tg:
            call_command("sofreh_selftest", stdout=out)

        text = out.getvalue()
        self.assertIn("Access token valid until", text)
        self.assertIn("renewal is automatic", text)
        self.assertIn("not open yet", text)
        self.assertIn("Bot token accepted", text)
        self.assertIn("Everything works", text)

        # One "this is a test" message, one preview of the real notification.
        self.assertEqual(notifier.send.call_count, 2)
        first, second = [call.args[0] for call in notifier.send.call_args_list]
        self.assertIn("self-test", first)
        self.assertIn("رزرو غذا باز شد", second)
        self.assertIn("چلو جوجه‌کباب", second)

    def test_preview_uses_sample_data_when_no_week_is_open(self):
        out = StringIO()
        patch_tg, notifier = fake_telegram()

        with fake_samad(CLOSED_RESULT, CLOSED_RESULT), patch_tg:
            call_command("sofreh_selftest", stdout=out)

        self.assertIn("made-up food names", out.getvalue())
        preview = notifier.send.call_args_list[1].args[0]
        self.assertIn("رزرو غذا باز شد", preview)

    def test_an_open_target_week_is_previewed_directly(self):
        out = StringIO()
        patch_tg, notifier = fake_telegram()

        with fake_samad(OPEN_RESULT), patch_tg:
            call_command("sofreh_selftest", stdout=out)

        self.assertIn("OPEN — 30 programs, 12 reservable", out.getvalue())
        self.assertEqual(notifier.send.call_count, 2)

    def test_a_rejected_bot_token_fails_the_command(self):
        patch_tg, _ = fake_telegram(ok=False)

        with fake_samad(CLOSED_RESULT, OPEN_RESULT), patch_tg:
            with self.assertRaisesMessage(CommandError, "1 check(s) failed"):
                call_command("sofreh_selftest", stdout=StringIO())

    def test_auth_failure_is_reported_and_fails(self):
        auth_failed = ProgramsResult(
            outcome=Outcome.AUTH_FAILED, detail="invalid_grant"
        )
        patch_tg, _ = fake_telegram()

        with fake_samad(auth_failed), patch_tg:
            with self.assertRaises(CommandError):
                call_command("sofreh_selftest", stdout=StringIO())

    def test_missing_chats_is_a_failure_with_the_telegram_gotcha_explained(self):
        self.bot.chats.all().delete()
        out = StringIO()
        patch_tg, _ = fake_telegram()

        with fake_samad(CLOSED_RESULT, OPEN_RESULT), patch_tg:
            with self.assertRaises(CommandError):
                call_command("sofreh_selftest", stdout=out)

        self.assertIn("blocks bots from writing to strangers", out.getvalue())

    def test_no_telegram_flag_skips_the_sending(self):
        out = StringIO()
        patch_tg, notifier = fake_telegram()

        with fake_samad(CLOSED_RESULT, OPEN_RESULT), patch_tg:
            call_command("sofreh_selftest", "--no-telegram", stdout=out)

        self.assertIn("Skipping Telegram", out.getvalue())
        notifier.send.assert_not_called()

    def test_week_override_must_be_a_saturday(self):
        with self.assertRaisesMessage(CommandError, "must be a Saturday"):
            call_command("sofreh_selftest", "--week=2026-10-05", stdout=StringIO())

    def test_missing_account_is_a_clear_error(self):
        Watch.objects.all().delete()
        SamadAccount.objects.all().delete()

        with self.assertRaisesMessage(CommandError, "No Samad account configured"):
            call_command("sofreh_selftest", stdout=StringIO())
