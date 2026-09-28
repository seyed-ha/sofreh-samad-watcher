"""Delivery to Telegram and the message bodies we deliver."""

from datetime import date

import requests
from django.test import TestCase

from sofreh.models import SamadAccount, TelegramBot, TelegramChat, Watch
from sofreh.services import messages
from sofreh.services.samad import DaySummary, ProgramsResult
from sofreh.models import Outcome
from sofreh.services.telegram import MAX_MESSAGE_LENGTH, TelegramNotifier

from .support import FakeResponse, FakeSession

WEEK = date(2026, 10, 3)


class TelegramNotifierTests(TestCase):
    def setUp(self):
        self.bot = TelegramBot.objects.create(label="test", bot_token="123:AAtoken")
        TelegramChat.objects.create(bot=self.bot, chat_id="111", label="me")
        TelegramChat.objects.create(bot=self.bot, chat_id="222", label="roommate")
        TelegramChat.objects.create(bot=self.bot, chat_id="333", is_active=False)

    def test_sends_to_every_active_chat(self):
        session = FakeSession(
            post_responses=[FakeResponse(200, {"ok": True})] * 2
        )
        notifier = TelegramNotifier(self.bot, session=session)

        deliveries = notifier.send("hello")

        self.assertEqual([d.chat_id for d in deliveries], ["111", "222"])
        self.assertTrue(all(d.ok for d in deliveries))
        call = session.post_calls[0]
        self.assertEqual(
            call["url"], "https://api.telegram.org/bot123:AAtoken/sendMessage"
        )
        self.assertEqual(call["json"]["chat_id"], "111")
        self.assertEqual(call["json"]["text"], "hello")
        self.assertEqual(call["json"]["parse_mode"], "HTML")

    def test_one_bad_chat_does_not_stop_the_others(self):
        session = FakeSession(
            post_responses=[
                FakeResponse(400, text='{"description":"chat not found"}'),
                FakeResponse(200, {"ok": True}),
            ]
        )
        notifier = TelegramNotifier(self.bot, session=session)

        deliveries = notifier.send("hello")

        self.assertFalse(deliveries[0].ok)
        self.assertIn("chat not found", deliveries[0].error)
        self.assertTrue(deliveries[1].ok)

    def test_network_error_is_captured_per_chat(self):
        session = FakeSession(
            post_responses=[
                requests.ConnectionError("no route"),
                FakeResponse(200, {"ok": True}),
            ]
        )
        notifier = TelegramNotifier(self.bot, session=session)

        deliveries = notifier.send("hello")

        self.assertFalse(deliveries[0].ok)
        self.assertIn("no route", deliveries[0].error)

    def test_no_active_chats_sends_nothing(self):
        self.bot.chats.update(is_active=False)
        session = FakeSession()

        deliveries = TelegramNotifier(self.bot, session=session).send("hello")

        self.assertEqual(deliveries, [])
        self.assertEqual(session.post_calls, [])

    def test_overlong_message_is_trimmed_to_telegrams_limit(self):
        session = FakeSession(post_responses=[FakeResponse(200, {"ok": True})] * 2)
        notifier = TelegramNotifier(self.bot, session=session)

        notifier.send("x" * (MAX_MESSAGE_LENGTH + 500))

        self.assertEqual(
            len(session.post_calls[0]["json"]["text"]), MAX_MESSAGE_LENGTH
        )


class MessageTests(TestCase):
    def setUp(self):
        account = SamadAccount.objects.create(label="acc", access_token="t")
        self.watch = Watch.objects.create(name="Next week", account=account)

    def test_open_message_names_the_week_and_the_food(self):
        result = ProgramsResult(
            outcome=Outcome.OPEN,
            program_count=36,
            reservable_count=12,
            days=[
                DaySummary(
                    date="2026-10-03",
                    day="Saturday",
                    foods=["چلو جوجه‌کباب", "لوبیا پلو"],
                    reservable=2,
                )
            ],
        )

        text = messages.reservation_open(self.watch, WEEK, result)

        self.assertIn("رزرو غذا باز شد", text)
        self.assertIn("2026-10-03", text)
        self.assertIn("12 مورد قابل رزرو از 36", text)
        self.assertIn("شنبه", text)
        self.assertIn("چلو جوجه‌کباب", text)

    def test_published_but_unbookable_week_says_so(self):
        result = ProgramsResult(
            outcome=Outcome.OPEN, program_count=36, reservable_count=0
        )

        text = messages.reservation_open(self.watch, WEEK, result)

        self.assertIn("هنوز موردی قابل رزرو نیست", text)

    def test_food_names_are_html_escaped(self):
        result = ProgramsResult(
            outcome=Outcome.OPEN,
            program_count=1,
            reservable_count=1,
            days=[DaySummary(date="2026-10-03", day="Saturday", foods=["<b>x</b>"])],
        )

        text = messages.reservation_open(self.watch, WEEK, result)

        self.assertIn("&lt;b&gt;x&lt;/b&gt;", text)
        self.assertNotIn("<b>x</b>", text)

    def test_auth_problem_tells_the_user_what_to_do(self):
        account = self.watch.account

        text = messages.auth_problem(account, "invalid_grant")

        self.assertIn("401", text)
        self.assertIn("invalid_grant", text)
        self.assertIn("پنل ادمین", text)


class DiscoverChatsTests(TestCase):
    """Finding who we are allowed to message, without the user hunting for ids."""

    def setUp(self):
        self.bot = TelegramBot.objects.create(label="test", bot_token="123:AA")

    def notifier(self, response):
        return TelegramNotifier(self.bot, session=FakeSession(get_responses=[response]))

    def test_reads_chat_ids_out_of_getupdates(self):
        payload = {
            "ok": True,
            "result": [
                {
                    "message": {
                        "chat": {
                            "id": 111,
                            "type": "private",
                            "username": "hamid",
                            "first_name": "Hamid",
                        }
                    }
                },
                {"channel_post": {"chat": {"id": -100, "type": "channel", "title": "Sofreh"}}},
            ],
        }

        chats, error = self.notifier(FakeResponse(200, payload)).discover_chats()

        self.assertEqual(error, "")
        self.assertEqual(
            sorted(c["chat_id"] for c in chats), sorted(["111", "-100"])
        )
        self.assertEqual(chats[0]["name"], "hamid")

    def test_repeated_messages_from_one_person_collapse_to_one_chat(self):
        payload = {
            "ok": True,
            "result": [
                {"message": {"chat": {"id": 111, "type": "private", "username": "a"}}},
                {"message": {"chat": {"id": 111, "type": "private", "username": "a"}}},
            ],
        }

        chats, _ = self.notifier(FakeResponse(200, payload)).discover_chats()

        self.assertEqual(len(chats), 1)

    def test_nobody_has_written_yet(self):
        chats, error = self.notifier(
            FakeResponse(200, {"ok": True, "result": []})
        ).discover_chats()

        self.assertEqual(chats, [])
        self.assertEqual(error, "")

    def test_a_bad_token_is_reported(self):
        chats, error = self.notifier(
            FakeResponse(401, {"ok": False, "description": "Unauthorized"})
        ).discover_chats()

        self.assertEqual(chats, [])
        self.assertIn("401", error)
