"""Bootstrap the watcher without clicking through the admin."""

import os

from django.core.management.base import BaseCommand, CommandError

from sofreh.jwt_tools import jwt_expiry, mask_token
from sofreh.models import SamadAccount, TelegramBot, TelegramChat, Watch


class Command(BaseCommand):
    help = (
        "Create or update the Samad account, Telegram bot and default watch. "
        "Values come from the command line, or from .env with --from-env."
    )

    def add_arguments(self, parser):
        parser.add_argument("--access-token")
        parser.add_argument("--refresh-token")
        parser.add_argument("--bot-token")
        parser.add_argument(
            "--chat-ids",
            help="Comma-separated Telegram chat ids to notify.",
        )
        parser.add_argument(
            "--self-id",
            default="1",
            help="Dining hall id (default 1). Pass an empty string to omit it.",
        )
        parser.add_argument(
            "--weeks-ahead",
            type=int,
            default=1,
            help="1 = watch the Saturday after this week (default).",
        )
        parser.add_argument(
            "--from-env",
            action="store_true",
            help="Read SAMAD_ACCESS_TOKEN, SAMAD_REFRESH_TOKEN, "
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_IDS from the environment/.env.",
        )

    def handle(self, *args, **options):
        if options["from_env"]:
            options = {
                **options,
                "access_token": options["access_token"]
                or os.environ.get("SAMAD_ACCESS_TOKEN"),
                "refresh_token": options["refresh_token"]
                or os.environ.get("SAMAD_REFRESH_TOKEN"),
                "bot_token": options["bot_token"]
                or os.environ.get("TELEGRAM_BOT_TOKEN"),
                "chat_ids": options["chat_ids"] or os.environ.get("TELEGRAM_CHAT_IDS"),
            }

        account = self._account(options)
        bot = self._bot(options)
        self._watch(account, bot, options)
        self._report(account, bot)

    # -- pieces ------------------------------------------------------------

    def _account(self, options):
        account = SamadAccount.objects.filter(label="default").first()
        access_token = (options.get("access_token") or "").strip()
        refresh_token = (options.get("refresh_token") or "").strip()

        if account is None:
            if not access_token:
                raise CommandError(
                    "No Samad account exists yet, so --access-token is required "
                    "(or set SAMAD_ACCESS_TOKEN and pass --from-env)."
                )
            account = SamadAccount.objects.create(
                label="default",
                access_token=access_token,
                refresh_token=refresh_token,
            )
            self.stdout.write(self.style.SUCCESS("Created the Samad account."))
            return account

        changed = []
        if access_token and access_token != account.access_token:
            account.access_token = access_token
            changed.append("access token")
        if refresh_token and refresh_token != account.refresh_token:
            account.refresh_token = refresh_token
            changed.append("refresh token")
        if changed:
            # New credentials mean any standing alert no longer applies.
            account.last_auth_error = ""
            account.auth_alert_sent_at = None
            account.save()
            self.stdout.write(
                self.style.SUCCESS(f"Updated the {' and '.join(changed)}.")
            )
        return account

    def _bot(self, options):
        bot_token = (options.get("bot_token") or "").strip()
        bot = TelegramBot.objects.filter(label="default").first()

        if bot is None and bot_token:
            bot = TelegramBot.objects.create(label="default", bot_token=bot_token)
            self.stdout.write(self.style.SUCCESS("Created the Telegram bot."))
        elif bot and bot_token and bot_token != bot.bot_token:
            bot.bot_token = bot_token
            bot.save()
            self.stdout.write(self.style.SUCCESS("Updated the Telegram bot token."))

        raw_ids = options.get("chat_ids") or ""
        chat_ids = [
            chat_id.strip()
            for chat_id in raw_ids.replace(",", " ").split()
            if chat_id.strip()
        ]
        if chat_ids and bot is None:
            raise CommandError("Chat ids were given but no bot token is configured.")
        for chat_id in chat_ids:
            _, created = TelegramChat.objects.get_or_create(bot=bot, chat_id=chat_id)
            if created:
                self.stdout.write(self.style.SUCCESS(f"Added chat {chat_id}."))
        return bot

    def _watch(self, account, bot, options):
        watch, created = Watch.objects.get_or_create(
            name="Next week",
            defaults={
                "account": account,
                "bot": bot,
                "self_id": options["self_id"],
                "weeks_ahead": options["weeks_ahead"],
            },
        )
        if created:
            self.stdout.write(
                self.style.SUCCESS(f"Created watch for {watch.target_week_start()}.")
            )
        elif bot and watch.bot is None:
            watch.bot = bot
            watch.save(update_fields=["bot", "updated_at"])
        return watch

    def _report(self, account, bot):
        self.stdout.write("")
        self.stdout.write("Samad account: " + mask_token(account.access_token))
        expiry = jwt_expiry(account.access_token)
        self.stdout.write(
            "  access token expires: "
            + (expiry.isoformat() if expiry else "unknown")
        )
        self.stdout.write(
            "  refresh token: "
            + (mask_token(account.refresh_token) or "MISSING (cannot auto-renew)")
        )
        if bot is None:
            self.stdout.write(
                self.style.WARNING(
                    "No Telegram bot yet — pass --bot-token and --chat-ids to add one."
                )
            )
        else:
            chats = bot.active_chat_ids()
            self.stdout.write(f"Telegram bot: {mask_token(bot.bot_token, keep=4)}")
            self.stdout.write(
                "  chats: " + (", ".join(chats) if chats else "none yet")
            )
        for watch in Watch.objects.filter(is_active=True):
            self.stdout.write(
                f"Watching: {watch.name} → {watch.target_week_start()} "
                f"(selfId={watch.self_id or 'omitted'})"
            )
