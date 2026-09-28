"""End-to-end check: can we read Samad, and can we reach Telegram?

Run this after setting things up, and any time you want reassurance that the
watcher would actually speak if next week opened. It sends a real Telegram
message containing a real notification preview, so "did the text arrive?" is
answered by looking at your phone.
"""

from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from sofreh.dates import current_week_start, week_start_for
from sofreh.jwt_tools import jwt_expiry
from sofreh.models import Outcome, SamadAccount, TelegramBot, Watch
from sofreh.services import messages
from sofreh.services.samad import SamadClient
from sofreh.services.telegram import TelegramNotifier

TICK = "✓"
CROSS = "✗"


class Command(BaseCommand):
    help = (
        "Verify the whole chain: tokens, the Samad API, the Telegram bot, and "
        "the chats. Sends a real test message unless --no-telegram is given."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--week", help="Probe this Saturday instead of the watch's target."
        )
        parser.add_argument(
            "--no-telegram",
            action="store_true",
            help="Only exercise the Samad side; send nothing.",
        )

    def handle(self, *args, **options):
        self.failures = 0

        watch = Watch.objects.filter(is_active=True).first()
        account = watch.account if watch else SamadAccount.get_active()
        if account is None:
            raise CommandError(
                "No Samad account configured. Run `manage.py sofreh_setup` first."
            )

        self._heading("1. Stored credentials")
        self._check_tokens(account)

        self._heading("2. Samad API")
        target, preview = self._check_api(watch, account, options.get("week"))

        if options["no_telegram"]:
            self.stdout.write("")
            self.stdout.write("Skipping Telegram (--no-telegram).")
            return self._verdict()

        self._heading("3. Telegram")
        self._check_telegram(watch, preview)

        return self._verdict()

    # -- steps ------------------------------------------------------------

    def _check_tokens(self, account):
        expiry = jwt_expiry(account.access_token)
        if expiry:
            self._ok(f"Access token valid until {expiry:%Y-%m-%d}")
        else:
            self._ok("Access token stored (expiry not readable)")

        if account.refresh_token:
            refresh_expiry = jwt_expiry(account.refresh_token)
            days_left = account.refresh_token_days_left()
            threshold = settings.SOFREH["TOKEN_EXPIRY_WARNING_DAYS"]
            when = f" until {refresh_expiry:%Y-%m-%d}" if refresh_expiry else ""
            if days_left is not None and days_left <= threshold:
                self._warn(
                    f"Refresh token expires in {days_left} day(s){when}. Nothing "
                    "can renew it — log in to Samad and repaste before then."
                )
            else:
                left = f" ({days_left} days left)" if days_left is not None else ""
                self._ok(
                    f"Refresh token stored{when}{left} — renewal is automatic"
                )
        else:
            self._warn(
                "No refresh token: you will have to repaste by hand when the "
                "access token expires."
            )

        if account.last_auth_error:
            self._fail(f"Last login attempt failed: {account.last_auth_error[:120]}")

    def _check_api(self, watch, account, week_override):
        client = SamadClient(account)
        self_id = watch.self_id if watch else "1"

        target = self._parse_week(week_override) or (
            watch.target_week_start() if watch else week_start_for(1)
        )
        result = client.fetch_programs(target, self_id=self_id)

        if result.outcome == Outcome.OPEN:
            self._ok(
                f"{target}: OPEN — {result.program_count} programs, "
                f"{result.reservable_count} reservable"
            )
            return target, (target, result)

        if result.outcome == Outcome.NOT_OPEN:
            self._ok(
                f"{target}: not open yet — the API answered "
                f"{result.api_type} ({result.api_message or result.api_code}). "
                "This is the normal waiting state."
            )
        elif result.outcome == Outcome.AUTH_FAILED:
            self._fail(f"Authentication failed: {result.detail[:200]}")
            return target, None
        else:
            self._fail(f"{result.outcome}: {result.detail[:200]}")
            return target, None

        # The target week is closed, so borrow the week we are living in --
        # which is published by definition -- to show what a real announcement
        # looks like.
        fallback = current_week_start()
        if fallback != target:
            sample = client.fetch_programs(fallback, self_id=self_id)
            if sample.outcome == Outcome.OPEN:
                self._ok(
                    f"{fallback}: OPEN — using this week to build a realistic "
                    "notification preview"
                )
                return target, (fallback, sample)
            self._warn(
                f"{fallback} is not open either, so the preview below uses "
                "made-up food names."
            )
        return target, None

    def _check_telegram(self, watch, preview):
        bot = (watch.bot if watch else None) or TelegramBot.get_active()
        if bot is None:
            self._fail(
                "No Telegram bot configured. Add one with "
                "`manage.py sofreh_setup --bot-token … --chat-ids …`."
            )
            return

        notifier = TelegramNotifier(bot)
        ok, detail = notifier.check_bot()
        if not ok:
            self._fail(f"Bot token rejected: {detail}")
            return
        self._ok(f"Bot token accepted: {detail}")

        chats = bot.active_chat_ids()
        if not chats:
            self._fail(
                "The bot has no active chats. Add one with "
                "`manage.py sofreh_setup --chat-ids <id>` — and message the bot "
                "first, since Telegram blocks bots from writing to strangers."
            )
            return
        self._ok(f"{len(chats)} chat(s) configured: {', '.join(chats)}")

        for text, label in self._test_messages(watch, preview):
            for delivery in notifier.send(text, chat_ids=chats):
                if delivery.ok:
                    self._ok(f"{label} delivered to {delivery.chat_id}")
                else:
                    self._fail(f"{label} to {delivery.chat_id}: {delivery.error}")

    def _test_messages(self, watch, preview):
        yield (
            "🧪 <b>Sofreh self-test</b>\n\n"
            "If you can read this, the bot token and this chat id both work. "
            "The next message shows what you will get when the week opens.",
            "Self-test message",
        )

        if preview:
            week, result = preview
            text = messages.reservation_open(watch, week, result)
            yield (
                "👇 <i>نمونه — پیام واقعی وقتی رزرو باز شود:</i>\n\n" + text,
                "Notification preview",
            )
        else:
            yield (
                "👇 <i>نمونه — پیام واقعی وقتی رزرو باز شود:</i>\n\n"
                + messages.reservation_open(watch, week_start_for(1), _sample()),
                "Notification preview (sample data)",
            )

    # -- output -----------------------------------------------------------

    def _heading(self, text):
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(text))

    def _ok(self, text):
        self.stdout.write(self.style.SUCCESS(f"  {TICK} {text}"))

    def _warn(self, text):
        self.stdout.write(self.style.WARNING(f"  ! {text}"))

    def _fail(self, text):
        self.failures += 1
        self.stdout.write(self.style.ERROR(f"  {CROSS} {text}"))

    def _verdict(self):
        self.stdout.write("")
        if self.failures:
            raise CommandError(
                f"{self.failures} check(s) failed — see above."
            )
        self.stdout.write(
            self.style.SUCCESS("Everything works. Start it with `manage.py sofreh_watch`.")
        )
        return None

    def _parse_week(self, raw):
        if not raw:
            return None
        try:
            week = date.fromisoformat(raw)
        except ValueError as exc:
            raise CommandError(f"--week must look like 2026-10-03 ({exc}).") from exc
        if week.weekday() != 5:
            raise CommandError(f"--week must be a Saturday; {week} is a {week:%A}.")
        return week


def _sample():
    """A stand-in result, for when no week is open to preview from."""
    from sofreh.services.samad import DaySummary, ProgramsResult

    return ProgramsResult(
        outcome=Outcome.OPEN,
        api_type="INFO",
        program_count=6,
        reservable_count=6,
        days=[
            DaySummary(
                date="2026-10-03",
                day="Saturday",
                foods=["چلو جوجه‌کباب", "زرشک‌پلو با مرغ"],
                reservable=2,
            ),
            DaySummary(
                date="2026-10-04",
                day="Sunday",
                foods=["قورمه‌سبزی", "لازانیا (کلین‌فود)"],
                reservable=2,
            ),
        ],
    )
