"""Run one poll of every active watch."""

from datetime import date

from django.core.management.base import BaseCommand, CommandError

from sofreh.models import Watch
from sofreh.services.checker import run_all


class Command(BaseCommand):
    help = "Poll Samad once for every active watch and notify if a week opened."

    def add_arguments(self, parser):
        parser.add_argument(
            "--watch",
            help="Limit to one watch, by id or exact name.",
        )
        parser.add_argument(
            "--week",
            help="Override the target week (YYYY-MM-DD, a Saturday). For testing.",
        )
        parser.add_argument(
            "--force-notify",
            action="store_true",
            help="Send even if this week was already announced.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Poll and print, but send nothing and record nothing.",
        )

    def handle(self, *args, **options):
        queryset = select_watches(options.get("watch"))
        if not queryset.exists():
            raise CommandError(
                "No active watch to run. Create one with `manage.py sofreh_setup`."
            )

        reports = run_all(
            queryset=queryset,
            week_start=parse_week(options.get("week")),
            force_notify=options["force_notify"],
            dry_run=options["dry_run"],
        )

        for report in reports:
            style = self.style.SUCCESS if report.notified else self.style.NOTICE
            if report.result.is_problem:
                style = self.style.ERROR
            self.stdout.write(style(report.summary))
            for note in report.notes:
                self.stdout.write(f"    {note}")
            if report.result.detail:
                self.stdout.write(f"    {report.result.detail}")
        return None


def select_watches(identifier):
    queryset = Watch.objects.filter(is_active=True)
    if not identifier:
        return queryset
    if identifier.isdigit():
        return queryset.filter(pk=int(identifier))
    return queryset.filter(name=identifier)


def parse_week(raw):
    if not raw:
        return None
    try:
        week = date.fromisoformat(raw)
    except ValueError as exc:
        raise CommandError(f"--week must look like 2026-10-03 ({exc}).") from exc
    if week.weekday() != 5:
        raise CommandError(
            f"--week must be a Saturday; {week} is a {week:%A}."
        )
    return week
