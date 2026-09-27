"""Delivery of notifications through the Telegram Bot API."""

import logging
from dataclasses import dataclass

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

API_ROOT = "https://api.telegram.org"
# Telegram rejects anything longer; we trim rather than lose the message.
MAX_MESSAGE_LENGTH = 4096


@dataclass
class Delivery:
    chat_id: str
    ok: bool
    error: str = ""


class TelegramNotifier:
    def __init__(self, bot, session=None, config=None):
        self.bot = bot
        self.config = config or settings.SOFREH
        self.session = session or requests.Session()

    def send(self, text, chat_ids=None):
        """Send one message to every active chat. Returns a Delivery per chat."""
        chat_ids = chat_ids if chat_ids is not None else self.bot.active_chat_ids()
        if not chat_ids:
            logger.warning("Telegram bot %s has no active chats.", self.bot.label)
            return []

        if len(text) > MAX_MESSAGE_LENGTH:
            text = text[: MAX_MESSAGE_LENGTH - 1] + "…"

        return [self._send_one(chat_id, text) for chat_id in chat_ids]

    def _send_one(self, chat_id, text):
        url = f"{API_ROOT}/bot{self.bot.bot_token}/sendMessage"
        try:
            response = self.session.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=self.config["REQUEST_TIMEOUT"],
            )
        except requests.RequestException as exc:
            logger.error("Telegram send to %s failed: %s", chat_id, exc)
            return Delivery(chat_id, False, f"{type(exc).__name__}: {exc}")

        if response.status_code != 200:
            # Telegram explains refusals in the body; keep it for the log.
            detail = response.text[:300]
            logger.error(
                "Telegram refused the message to %s (HTTP %s): %s",
                chat_id,
                response.status_code,
                detail,
            )
            return Delivery(chat_id, False, f"HTTP {response.status_code}: {detail}")

        return Delivery(chat_id, True)

    def check_bot(self):
        """Call getMe, so `manage.py sofreh_test_telegram` can verify the token."""
        url = f"{API_ROOT}/bot{self.bot.bot_token}/getMe"
        try:
            response = self.session.get(url, timeout=self.config["REQUEST_TIMEOUT"])
        except requests.RequestException as exc:
            return False, f"{type(exc).__name__}: {exc}"
        if response.status_code != 200:
            return False, f"HTTP {response.status_code}: {response.text[:300]}"
        try:
            body = response.json()
        except ValueError:
            return False, "getMe did not return JSON."
        username = (body.get("result") or {}).get("username", "?")
        return True, f"@{username}"
