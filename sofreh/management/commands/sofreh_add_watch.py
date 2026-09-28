"""Add or update a watch.

Several watches run side by side and notify independently: the university self
serves lunch, the dormitory self serves dinner, and each publishes its week on
its own schedule. Each watch remembers separately which week it has already
announced, so one opening cannot suppress the other's message.
"""

from django.core.management.base import BaseCommand, CommandError

from sofreh.models import SamadAccount, TelegramBot, Watch


class Command(BaseCommand):
    help = "Create or update a watch for one dining hall (self)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--name",
            required=True,
            help="What to call it. This is the first line of the notification, "
            "so make it something you will recognise on your phone.",
        )
        parser.add_argument(
            "--self-id",
            required=True,
            help="Dining hall id: 1 = central (lunch), 11 = dormitory (dinner). "
            "Pass an empty string to omit the parameter.",
        )
        parser.add_argument(
            "--weeks-ahead",
            type=int,
            default=1,
            help="1 = the Saturday after this week (default).",
        )
        parser.add_argument(
            "--account", help="Samad account label. Defaults to the active one."
        )
        parser.add_argument(
            "--bot", help="Telegram bot label. Defaults to the active one."
        )
        parser.add_argument(
            "--disable", action="store_true", help="Pause this watch instead."
        )

    def handle(self, *args, **options):
        account = self._account(options.get("account"))
        bot = self._bot(options.get("bot"))

        watch, created = Watch.objects.get_or_create(
            name=options["name"],
            defaults={
                "account": account,
                "bot": bot,
                "self_id": options["self_id"],
                "weeks_ahead": options["weeks_ahead"],
            },
        )
        if created:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Created watch {watch.name!r} "
                    f"(selfId={watch.self_id or 'omitted'}) → {watch.target_week_start()}"
                )
            )
        else:
            watch.self_id = options["self_id"]
            watch.weeks_ahead = options["weeks_ahead"]
            watch.account = account
            watch.bot = bot
            watch.is_active = not options["disable"]
            watch.save()
            self.stdout.write(
                self.style.SUCCESS(f"Updated watch {watch.name!r}.")
            )

        if options["disable"] and watch.is_active:
            watch.is_active = False
            watch.save(update_fields=["is_active", "updated_at"])
            self.stdout.write(self.style.WARNING(f"Paused {watch.name!r}."))

        self._report()

    def _account(self, label):
        account = (
            SamadAccount.objects.filter(label=label).first()
            if label
            else SamadAccount.get_active()
        )
        if account is None:
            raise CommandError(
                "No Samad account found. Run `manage.py sofreh_setup` first."
            )
        return account

    def _bot(self, label):
        if label:
            bot = TelegramBot.objects.filter(label=label).first()
            if bot is None:
                raise CommandError(f"No Telegram bot labelled {label!r}.")
            return bot
        return TelegramBot.get_active()

    def _report(self):
        self.stdout.write("")
        self.stdout.write("Watches:")
        for watch in Watch.objects.order_by("name"):
            state = "" if watch.is_active else "  (paused)"
            self.stdout.write(
                f"  • {watch.name} — selfId={watch.self_id or 'omitted'}, "
                f"week {watch.target_week_start()}{state}"
            )
