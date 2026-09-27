"""Prove the bot token and chat ids work before relying on them."""

from django.core.management.base import BaseCommand, CommandError

from sofreh.models import TelegramBot
from sofreh.services.telegram import TelegramNotifier


class Command(BaseCommand):
    help = "Check the Telegram bot token and send a test message to every chat."

    def add_arguments(self, parser):
        parser.add_argument(
            "--message",
            default="✅ Sofreh is wired up. You will hear from me when the "
            "reservation week opens.",
        )
        parser.add_argument(
            "--no-send",
            action="store_true",
            help="Only verify the token, do not message anyone.",
        )

    def handle(self, *args, **options):
        bot = TelegramBot.get_active()
        if bot is None:
            raise CommandError(
                "No active Telegram bot. Add one with "
                "`manage.py sofreh_setup --bot-token … --chat-ids …`."
            )

        notifier = TelegramNotifier(bot)
        ok, detail = notifier.check_bot()
        if not ok:
            raise CommandError(f"The bot token was rejected: {detail}")
        self.stdout.write(self.style.SUCCESS(f"Bot token is valid: {detail}"))

        chats = bot.active_chat_ids()
        if not chats:
            raise CommandError(
                "The bot has no active chats. Add one with "
                "`manage.py sofreh_setup --chat-ids <id>`, and remember to send "
                "the bot a message first — Telegram will not let it write to "
                "someone who has never talked to it."
            )

        if options["no_send"]:
            self.stdout.write(f"Would message: {', '.join(chats)}")
            return

        failures = 0
        for delivery in notifier.send(options["message"]):
            if delivery.ok:
                self.stdout.write(self.style.SUCCESS(f"Sent to {delivery.chat_id}"))
            else:
                failures += 1
                self.stdout.write(
                    self.style.ERROR(f"{delivery.chat_id}: {delivery.error}")
                )
        if failures:
            raise CommandError(f"{failures} chat(s) could not be reached.")
