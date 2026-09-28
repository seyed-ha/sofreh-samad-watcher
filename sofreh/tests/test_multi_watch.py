"""Two selfs, two watches, two independent notifications.

selfId 1 is the university self (lunch); selfId 11 is the dormitory (dinner).
They publish their weeks on their own schedules, so neither may speak for the
other and neither may silence the other.
"""

from datetime import date
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from sofreh.models import Outcome, SamadAccount, TelegramBot, TelegramChat, Watch
from sofreh.services import messages
from sofreh.services.checker import run_all
from sofreh.services.samad import DaySummary, ProgramsResult

from .fixtures import PROGRAMS_NOT_OPEN, PROGRAMS_OPEN
from .support import FakeResponse, FakeSession, make_jwt

FAR_FUTURE = 4883264000
WEEK = date(2026, 10, 3)


class MultiWatchTests(TestCase):
    def setUp(self):
        self.account = SamadAccount.objects.create(
            label="default", access_token=make_jwt(exp=FAR_FUTURE)
        )
        self.bot = TelegramBot.objects.create(label="default", bot_token="123:AA")
        TelegramChat.objects.create(bot=self.bot, chat_id="111")
        self.lunch = Watch.objects.create(
            name="سلف مرکزی — ناهار",
            account=self.account,
            bot=self.bot,
            self_id="1",
        )
        self.dinner = Watch.objects.create(
            name="خوابگاه — شام",
            account=self.account,
            bot=self.bot,
            self_id="11",
        )

    def run_both(self, lunch_api, dinner_api, sends=4):
        samad = FakeSession(
            get_responses=[FakeResponse(200, lunch_api), FakeResponse(200, dinner_api)]
        )
        telegram = FakeSession(
            post_responses=[FakeResponse(200, {"ok": True})] * sends
        )
        reports = run_all(
            queryset=Watch.objects.filter(is_active=True).order_by("pk"),
            week_start=WEEK,
            session=samad,
            telegram_session=telegram,
        )
        return reports, samad, telegram

    def test_each_watch_queries_its_own_self_id(self):
        _, samad, _ = self.run_both(PROGRAMS_NOT_OPEN, PROGRAMS_NOT_OPEN)

        self.assertEqual(samad.get_calls[0]["params"]["selfId"], "1")
        self.assertEqual(samad.get_calls[1]["params"]["selfId"], "11")

    def test_one_self_opening_does_not_speak_for_the_other(self):
        reports, _, telegram = self.run_both(PROGRAMS_OPEN, PROGRAMS_NOT_OPEN)

        self.assertTrue(reports[0].notified)
        self.assertFalse(reports[1].notified)
        self.assertEqual(len(telegram.post_calls), 1)
        self.assertIn("سلف مرکزی", telegram.post_calls[0]["json"]["text"])

    def test_both_opening_sends_two_separate_messages(self):
        reports, _, telegram = self.run_both(PROGRAMS_OPEN, PROGRAMS_OPEN)

        self.assertTrue(all(r.notified for r in reports))
        texts = [call["json"]["text"] for call in telegram.post_calls]
        self.assertEqual(len(texts), 2)
        self.assertIn("سلف مرکزی — ناهار", texts[0])
        self.assertIn("خوابگاه — شام", texts[1])

    def test_announcing_one_does_not_mark_the_other_as_announced(self):
        self.run_both(PROGRAMS_OPEN, PROGRAMS_NOT_OPEN)

        self.lunch.refresh_from_db()
        self.dinner.refresh_from_db()
        self.assertEqual(self.lunch.notified_week_start, WEEK)
        self.assertIsNone(self.dinner.notified_week_start)

        # The dorm week opens a day later; it still gets its own message.
        _, _, telegram = self.run_both(PROGRAMS_OPEN, PROGRAMS_OPEN)
        texts = [call["json"]["text"] for call in telegram.post_calls]
        self.assertEqual(len(texts), 1)
        self.assertIn("خوابگاه — شام", texts[0])

    def test_the_message_says_which_self_it_is_about_on_the_first_lines(self):
        result = ProgramsResult(
            outcome=Outcome.OPEN,
            program_count=17,
            reservable_count=17,
            meal_names=["شام"],
            days=[DaySummary(date="2026-10-03", day="Saturday", foods=["لوبیا پلو"])],
        )

        text = messages.reservation_open(self.dinner, WEEK, result)
        head = text.splitlines()[:2]

        self.assertIn("رزرو غذا باز شد", head[0])
        self.assertIn("خوابگاه — شام", head[1])
        self.assertIn("شام", text)

    def test_meal_names_come_from_the_api_not_from_the_self_id(self):
        # The dorm self turns out to serve lunch as well as dinner; we report
        # what the response says rather than inferring from the id.
        result = ProgramsResult(
            outcome=Outcome.OPEN, program_count=17, meal_names=["شام", "ناهار"]
        )

        text = messages.reservation_open(self.dinner, WEEK, result)

        self.assertIn("شام، ناهار", text)


class AddWatchCommandTests(TestCase):
    def setUp(self):
        self.account = SamadAccount.objects.create(
            label="default", access_token=make_jwt(exp=FAR_FUTURE)
        )
        self.bot = TelegramBot.objects.create(label="default", bot_token="123:AA")

    def test_creates_a_watch_for_another_self(self):
        out = StringIO()

        call_command(
            "sofreh_add_watch",
            "--name=خوابگاه — شام",
            "--self-id=11",
            stdout=out,
        )

        watch = Watch.objects.get(name="خوابگاه — شام")
        self.assertEqual(watch.self_id, "11")
        self.assertEqual(watch.account, self.account)
        self.assertEqual(watch.bot, self.bot)
        self.assertIn("Created watch", out.getvalue())

    def test_rerunning_updates_instead_of_duplicating(self):
        call_command(
            "sofreh_add_watch", "--name=dorm", "--self-id=11", stdout=StringIO()
        )
        call_command(
            "sofreh_add_watch", "--name=dorm", "--self-id=12", stdout=StringIO()
        )

        self.assertEqual(Watch.objects.filter(name="dorm").count(), 1)
        self.assertEqual(Watch.objects.get(name="dorm").self_id, "12")

    def test_can_pause_a_watch(self):
        call_command(
            "sofreh_add_watch", "--name=dorm", "--self-id=11", stdout=StringIO()
        )
        call_command(
            "sofreh_add_watch",
            "--name=dorm",
            "--self-id=11",
            "--disable",
            stdout=StringIO(),
        )

        self.assertFalse(Watch.objects.get(name="dorm").is_active)
