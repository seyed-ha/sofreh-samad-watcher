"""Read-only helpers for the JWTs Samad hands out.

We never verify these tokens — only the dining server can do that. We decode
the payload purely to show the user when a token is about to expire, so the
watcher can warn before a poll starts failing with 401.
"""

import base64
import binascii
import json
from datetime import datetime, timezone


def decode_jwt_payload(token):
    """Return the payload of a JWT as a dict, or None if it is unreadable."""
    if not token:
        return None
    parts = token.strip().split(".")
    if len(parts) != 3:
        return None
    payload = parts[1]
    # JWTs use base64url without padding; add it back before decoding.
    payload += "=" * (-len(payload) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload)
    except (binascii.Error, ValueError):
        return None
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def jwt_expiry(token):
    """Return the token's expiry as an aware datetime, or None if unknown."""
    payload = decode_jwt_payload(token) or {}
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(exp, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def mask_token(token, keep=6):
    """Render a token safely for logs and admin pages."""
    if not token:
        return ""
    token = token.strip()
    if len(token) <= keep * 2:
        return "…"
    return f"{token[:keep]}…{token[-keep:]} ({len(token)} chars)"
