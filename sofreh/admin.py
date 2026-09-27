"""Admin screens — the day-to-day UI for pasting tokens and adding chats."""

from django.contrib import admin
from django.utils import timezone

from .jwt_tools import mask_token
from .models import CheckResult, SamadAccount, TelegramBot, TelegramChat, Watch


def _humanise_expiry(expires_at):
    if expires_at is None:
        return "unknown (token not readable as a JWT)"
    local = timezone.localtime(expires_at)
    delta = expires_at - timezone.now()
    if delta.total_seconds() <= 0:
        return f"EXPIRED on {local:%Y-%m-%d %H:%M}"
    return f"{local:%Y-%m-%d %H:%M} (in {delta.days} days)"


@admin.register(SamadAccount)
class SamadAccountAdmin(admin.ModelAdmin):
    list_display = ("label", "is_active", "token_preview", "access_expiry", "health")
    readonly_fields = (
        "access_expiry",
        "refresh_expiry",
        "last_refreshed_at",
        "last_auth_error",
        "auth_alert_sent_at",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (None, {"fields": ("label", "is_active")}),
        (
            "Tokens",
            {
                "fields": ("access_token", "access_expiry", "refresh_token", "refresh_expiry"),
                "description": (
                    "Paste the access_token and refresh_token from the Samad login "
                    "response. The watcher renews the access token by itself and "
                    "only alerts you when the refresh token stops working too."
                ),
            },
        ),
        (
            "Status",
            {
                "fields": (
                    "last_refreshed_at",
                    "last_auth_error",
                    "auth_alert_sent_at",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    @admin.display(description="access token")
    def token_preview(self, obj):
        return mask_token(obj.access_token)

    @admin.display(description="access token expires")
    def access_expiry(self, obj):
        return _humanise_expiry(obj.access_token_expires_at)

    @admin.display(description="refresh token expires")
    def refresh_expiry(self, obj):
        return _humanise_expiry(obj.refresh_token_expires_at)

    @admin.display(description="health")
    def health(self, obj):
        if obj.last_auth_error:
            return "needs new tokens"
        return "ok"


class TelegramChatInline(admin.TabularInline):
    model = TelegramChat
    extra = 1
    fields = ("chat_id", "label", "is_active")


@admin.register(TelegramBot)
class TelegramBotAdmin(admin.ModelAdmin):
    list_display = ("label", "is_active", "token_preview", "chat_count")
    inlines = [TelegramChatInline]

    @admin.display(description="bot token")
    def token_preview(self, obj):
        return mask_token(obj.bot_token, keep=4)

    @admin.display(description="active chats")
    def chat_count(self, obj):
        return obj.chats.filter(is_active=True).count()


@admin.register(Watch)
class WatchAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "is_active",
        "self_id",
        "target_week",
        "last_outcome",
        "last_checked_at",
        "notified_week_start",
    )
    list_filter = ("is_active", "last_outcome")
    readonly_fields = ("last_checked_at", "last_outcome", "created_at", "updated_at")

    @admin.display(description="target week (Saturday)")
    def target_week(self, obj):
        return obj.target_week_start()


@admin.register(CheckResult)
class CheckResultAdmin(admin.ModelAdmin):
    list_display = (
        "checked_at",
        "watch",
        "week_start",
        "outcome",
        "api_type",
        "api_message",
        "program_count",
        "notified",
    )
    list_filter = ("outcome", "notified", "watch")
    date_hierarchy = "checked_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
