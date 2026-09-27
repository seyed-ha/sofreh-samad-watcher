"""Stored configuration and history for the Sofreh watcher.

Everything the user edits day to day lives here rather than in settings, so a
new access token or chat id is one admin save away — no redeploy, no restart.
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from .dates import week_start_for
from .jwt_tools import jwt_expiry, mask_token


class Outcome(models.TextChoices):
    """What a single poll of the programs endpoint told us."""

    OPEN = "open", "Reservation open"
    NOT_OPEN = "not_open", "Not open yet"
    AUTH_FAILED = "auth_failed", "Authentication failed"
    API_ERROR = "api_error", "API error"
    NETWORK_ERROR = "network_error", "Network error"

    @classmethod
    def problems(cls):
        return {cls.AUTH_FAILED, cls.API_ERROR, cls.NETWORK_ERROR}


class SamadAccount(models.Model):
    """The Samad tokens the watcher polls with.

    Samad issues a short-lived access token and a long-lived refresh token. We
    store both: when a poll comes back 401 we try the refresh token once, and
    only alert the user if that fails too.
    """

    label = models.CharField(max_length=100, default="default", unique=True)
    access_token = models.TextField(
        help_text="Bearer token from the Samad login response."
    )
    refresh_token = models.TextField(
        blank=True,
        help_text="Used to silently renew the access token when it expires.",
    )
    is_active = models.BooleanField(
        default=True, help_text="Only active accounts are polled."
    )

    last_refreshed_at = models.DateTimeField(null=True, blank=True)
    last_auth_error = models.TextField(blank=True)
    # Set when we warn about broken credentials; cleared on the next success so
    # the user gets one alert per outage instead of one per poll.
    auth_alert_sent_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Samad account"
        verbose_name_plural = "Samad accounts"

    def __str__(self):
        return f"{self.label} ({mask_token(self.access_token)})"

    @classmethod
    def get_active(cls):
        return cls.objects.filter(is_active=True).order_by("pk").first()

    @property
    def access_token_expires_at(self):
        return jwt_expiry(self.access_token)

    @property
    def refresh_token_expires_at(self):
        return jwt_expiry(self.refresh_token)

    def access_token_expired(self, now=None, leeway_seconds=60):
        """True when the access token is expired (or about to be).

        Unknown expiry counts as "not expired": we would rather make the call
        and let the server answer than refuse to poll over an unreadable JWT.
        """
        expires_at = self.access_token_expires_at
        if expires_at is None:
            return False
        now = now or timezone.now()
        return (expires_at - now).total_seconds() <= leeway_seconds

    def note_auth_failure(self, message):
        self.last_auth_error = message
        self.save(update_fields=["last_auth_error", "updated_at"])

    def clear_auth_failure(self):
        if self.last_auth_error or self.auth_alert_sent_at:
            self.last_auth_error = ""
            self.auth_alert_sent_at = None
            self.save(
                update_fields=["last_auth_error", "auth_alert_sent_at", "updated_at"]
            )


class TelegramBot(models.Model):
    """The bot that delivers notifications, plus its recipients."""

    label = models.CharField(max_length=100, default="default", unique=True)
    bot_token = models.CharField(
        max_length=255,
        help_text="From @BotFather, e.g. 123456789:AA…",
    )
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Telegram bot"
        verbose_name_plural = "Telegram bots"

    def __str__(self):
        return f"{self.label} ({mask_token(self.bot_token, keep=4)})"

    @classmethod
    def get_active(cls):
        return cls.objects.filter(is_active=True).order_by("pk").first()

    def active_chat_ids(self):
        return list(
            self.chats.filter(is_active=True).values_list("chat_id", flat=True)
        )


class TelegramChat(models.Model):
    """A person, group or channel that should hear about an opening."""

    bot = models.ForeignKey(
        TelegramBot, on_delete=models.CASCADE, related_name="chats"
    )
    chat_id = models.CharField(
        max_length=64,
        help_text="Numeric chat id, or @channelusername for a public channel.",
    )
    label = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Telegram chat"
        verbose_name_plural = "Telegram chats"
        constraints = [
            models.UniqueConstraint(
                fields=["bot", "chat_id"], name="unique_chat_per_bot"
            )
        ]

    def __str__(self):
        return self.label or self.chat_id


class Watch(models.Model):
    """One thing to keep an eye on: a self, a week, and where to shout."""

    name = models.CharField(max_length=100, default="Next week")
    account = models.ForeignKey(
        SamadAccount, on_delete=models.CASCADE, related_name="watches"
    )
    bot = models.ForeignKey(
        TelegramBot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="watches",
        help_text="Leave empty to use the active bot.",
    )

    self_id = models.CharField(
        max_length=16,
        default="1",
        blank=True,
        help_text="Dining hall id. Leave empty to omit the parameter entirely.",
    )
    weeks_ahead = models.PositiveSmallIntegerField(
        default=1,
        help_text="1 = the Saturday after the current week. 0 = this week (useful for testing).",
    )
    fixed_week_start = models.DateField(
        null=True,
        blank=True,
        help_text="Pin to one Saturday instead of rolling forward. For testing.",
    )

    is_active = models.BooleanField(default=True)

    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_outcome = models.CharField(
        max_length=20, choices=Outcome.choices, blank=True
    )
    # The week we have already announced, so a still-open week stays quiet.
    notified_week_start = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "watches"

    def __str__(self):
        return f"{self.name} → {self.target_week_start()}"

    def clean(self):
        if self.fixed_week_start and self.fixed_week_start.weekday() != 5:
            raise ValidationError(
                {"fixed_week_start": "The dining week starts on a Saturday."}
            )

    def target_week_start(self, today=None):
        """The Saturday this watch is asking about right now."""
        if self.fixed_week_start:
            return self.fixed_week_start
        return week_start_for(self.weeks_ahead, today=today)

    def already_notified_for(self, week_start):
        return self.notified_week_start == week_start


class CheckResult(models.Model):
    """One poll, kept so the user can see the watcher is alive and working."""

    watch = models.ForeignKey(
        Watch, on_delete=models.CASCADE, related_name="results"
    )
    checked_at = models.DateTimeField(default=timezone.now, db_index=True)
    week_start = models.DateField()
    outcome = models.CharField(max_length=20, choices=Outcome.choices)

    http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    api_type = models.CharField(max_length=20, blank=True)
    api_code = models.IntegerField(null=True, blank=True)
    api_message = models.CharField(max_length=255, blank=True)

    program_count = models.PositiveIntegerField(default=0)
    notified = models.BooleanField(default=False)
    detail = models.TextField(blank=True)

    class Meta:
        ordering = ["-checked_at"]

    def __str__(self):
        return f"{self.checked_at:%Y-%m-%d %H:%M} {self.week_start} {self.outcome}"

    @property
    def is_problem(self):
        return self.outcome in Outcome.problems()
