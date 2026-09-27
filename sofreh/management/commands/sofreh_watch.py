"""Poll on a schedule until stopped.

This is the long-running process: `manage.py sofreh_watch`. It is deliberately
a loop rather than a Celery/cron setup — one student, one laptop, one screen
session. Cron works too: call `sofreh_check` on a schedule instead.
"""

import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from sofreh.services.checker import run_all

from .sofreh_check import parse_week, select_watches

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Poll Samad every few minutes and announce the moment a week opens."

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval",
            type=int,
            default=settings.SOFREH["POLL_INTERVAL_SECONDS"],
            help="Seconds between polls (default from SOFREH_POLL_INTERVAL).",
        )
        parser.add_argument("--watch", help="Limit to one watch, by id or name.")
        parser.add_argument("--week", help="Pin the target week (YYYY-MM-DD).")
        parser.add_argument(
            "--max-polls",
            type=int,
            default=0,
            help="Stop after this many rounds. 0 means run forever.",
        )

    def handle(self, *args, **options):
        interval = max(30, options["interval"])
        week = parse_week(options.get("week"))
        identifier = options.get("watch")
        max_polls = options["max_polls"]

        if not select_watches(identifier).exists():
            raise CommandError(
                "No active watch to run. Create one with `manage.py sofreh_setup`."
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Sofreh is watching, every {interval}s. Ctrl-C to stop."
            )
        )

        polls = 0
        try:
            while True:
                polls += 1
                self._poll(identifier, week)
                if max_polls and polls >= max_polls:
                    break
                time.sleep(interval)
        except KeyboardInterrupt:
            self.stdout.write("")
            self.stdout.write(self.style.NOTICE(f"Stopped after {polls} polls."))

    def _poll(self, identifier, week):
        stamp = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")
        try:
            reports = run_all(queryset=select_watches(identifier), week_start=week)
        except Exception:
            # A crash here would end the watch; log it and try again next tick.
            logger.exception("Poll failed; continuing.")
            self.stdout.write(self.style.ERROR(f"{stamp}  poll failed (see log)"))
            return

        for report in reports:
            style = self.style.SUCCESS if report.notified else self.style.NOTICE
            if report.result.is_problem:
                style = self.style.ERROR
            self.stdout.write(style(f"{stamp}  {report.summary}"))
