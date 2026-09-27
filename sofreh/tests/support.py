"""Test doubles: a fake requests.Session and a JWT builder."""

import base64
import json


def make_jwt(**claims):
    """Build an unsigned JWT whose payload decodes to `claims`."""

    def seg(data):
        raw = json.dumps(data).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{seg({'alg': 'HS256', 'typ': 'JWT'})}.{seg(claims)}.signature"


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text or (json.dumps(json_data) if json_data is not None else "")

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class FakeSession:
    """Returns queued responses and records every request for assertions."""

    def __init__(self, get_responses=None, post_responses=None):
        self.headers = {}
        self.get_responses = list(get_responses or [])
        self.post_responses = list(post_responses or [])
        self.get_calls = []
        self.post_calls = []

    def _pop(self, queue, kind):
        if not queue:
            raise AssertionError(f"unexpected extra {kind} request")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, url, **kwargs):
        self.get_calls.append({"url": url, **kwargs})
        return self._pop(self.get_responses, "GET")

    def post(self, url, **kwargs):
        self.post_calls.append({"url": url, **kwargs})
        return self._pop(self.post_responses, "POST")
