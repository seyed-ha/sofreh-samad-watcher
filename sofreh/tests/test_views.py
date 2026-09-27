"""The status page: useful at a glance, and free of secrets."""

from datetime import date

from django.test import TestCase
from django.urls import reverse

from sofreh.models import CheckResult, Outcome, SamadAccount, TelegramBot, TelegramChat, Watch

from .support import make_jwt

FAR_FUTURE = 4883264000
SECRET_ACCESS_TOKEN = make_jwt(exp=FAR_FUTURE, secret="do-not-render-me")


class StatusPageTests(TestCase):
    def test_empty_install_explains_what_to_do(self):
        response = self.client.get(reverse("sofreh:status"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No watch configured")
        self.assertContains(response, "sofreh_setup")

    def test_open_week_is_the_headline(self):
        watch = self._watch()
        CheckResult.objects.create(
            watch=watch,
            week_start=date(2026, 10, 3),
            outcome=Outcome.OPEN,
            program_count=36,
            notified=True,
        )

        response = self.client.get(reverse("sofreh:status"))

        self.assertContains(response, "Reservation is open")

    def test_closed_week_says_it_is_still_waiting(self):
        watch = self._watch()
        CheckResult.objects.create(
            watch=watch, week_start=date(2026, 10, 3), outcome=Outcome.NOT_OPEN
        )

        response = self.client.get(reverse("sofreh:status"))

        self.assertContains(response, "Not open yet")

    def test_auth_trouble_is_visible(self):
        watch = self._watch()
        watch.account.last_auth_error = "invalid_grant"
        watch.account.save()
        CheckResult.objects.create(
            watch=watch, week_start=date(2026, 10, 3), outcome=Outcome.AUTH_FAILED
        )

        response = self.client.get(reverse("sofreh:status"))

        self.assertContains(response, "needs new tokens")
        self.assertContains(response, "invalid_grant")

    def test_no_secret_reaches_the_page(self):
        self._watch()

        response = self.client.get(reverse("sofreh:status"))
        body = response.content.decode()

        self.assertNotIn(SECRET_ACCESS_TOKEN, body)
        self.assertNotIn("do-not-render-me", body)
        self.assertNotIn("123:AAsecret", body)

    def _watch(self):
        account = SamadAccount.objects.create(
            label="default",
            access_token=SECRET_ACCESS_TOKEN,
            refresh_token=make_jwt(exp=FAR_FUTURE),
        )
        bot = TelegramBot.objects.create(label="default", bot_token="123:AAsecret")
        TelegramChat.objects.create(bot=bot, chat_id="111")
        return Watch.objects.create(name="Next week", account=account, bot=bot)
