"""Client for the Samad dining API.

Only one endpoint matters for the MVP: the weekly programs list. It answers
`type: "INFO"` once a week is open for reservation and `type: "ERROR"` with
SELF_HAS_NO_PROGRAM while it is not. Everything else here exists to keep that
one call working — chiefly renewing the access token when it expires.
"""

import logging
from dataclasses import dataclass, field

import requests
from django.conf import settings
from django.utils import timezone

from ..dates import as_api_param
from ..models import Outcome

logger = logging.getLogger(__name__)

# API `type` values. These are the whole protocol as far as Sofreh cares:
# INFO means the week is published and open, ERROR means it is not open yet.
TYPE_INFO = "INFO"
TYPE_ERROR = "ERROR"

# The error we expect while waiting. Others are still treated as "not open"
# (an ERROR is an ERROR), but they are recorded so an unexpected one is visible
# in the check log rather than silently indistinguishable from the normal wait.
EXPECTED_NOT_OPEN_RESOURCES = {"SELF_HAS_NO_PROGRAM"}
EXPECTED_NOT_OPEN_CODES = {10108005}


@dataclass
class DaySummary:
    date: str = ""
    day: str = ""
    foods: list = field(default_factory=list)
    reservable: int = 0


@dataclass
class ProgramsResult:
    """The outcome of a single poll, ready to be logged and announced."""

    outcome: str
    http_status: int = None
    api_type: str = ""
    api_code: int = None
    api_message: str = ""
    program_count: int = 0
    reservable_count: int = 0
    days: list = field(default_factory=list)
    detail: str = ""
    refreshed: bool = False

    @property
    def is_open(self):
        return self.outcome == Outcome.OPEN

    @property
    def is_problem(self):
        return self.outcome in Outcome.problems()


class SamadClient:
    """Polls the programs endpoint on behalf of one SamadAccount."""

    def __init__(self, account, session=None, config=None):
        self.account = account
        self.config = config or settings.SOFREH
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": self.config["USER_AGENT"],
            }
        )

    # -- URLs ---------------------------------------------------------------

    def _url(self, path_key):
        return self.config["API_BASE_URL"].rstrip("/") + self.config[path_key]

    # -- Token refresh ------------------------------------------------------

    def refresh_access_token(self):
        """Trade the refresh token for a fresh access token.

        Returns (ok, message). On success the account is updated in place.
        """
        if not self.account.refresh_token:
            return False, "No refresh token stored."

        data = {
            "grant_type": "refresh_token",
            "refresh_token": self.account.refresh_token,
            "client_id": self.config["OAUTH_CLIENT_ID"],
        }
        # Spring's token endpoint usually wants the client in a Basic header;
        # without a configured secret we can only send the id in the body.
        secret = self.config.get("OAUTH_CLIENT_SECRET") or ""
        auth = (self.config["OAUTH_CLIENT_ID"], secret) if secret else None

        try:
            response = self.session.post(
                self._url("TOKEN_PATH"),
                data=data,
                auth=auth,
                timeout=self.config["REQUEST_TIMEOUT"],
            )
        except requests.RequestException as exc:
            return False, f"Could not reach the token endpoint: {exc}"

        if response.status_code != 200:
            hint = ""
            if response.status_code in (400, 401) and not secret:
                hint = (
                    " The client may require a secret — set SAMAD_CLIENT_SECRET "
                    "in .env if you have one."
                )
            return False, (
                f"Refresh failed with HTTP {response.status_code}: "
                f"{response.text[:300]}{hint}"
            )

        try:
            body = response.json()
        except ValueError:
            return False, "Refresh endpoint did not return JSON."

        access_token = body.get("access_token")
        if not access_token:
            return False, "Refresh response contained no access_token."

        self.account.access_token = access_token
        # Samad may rotate the refresh token; keep the old one if it does not.
        if body.get("refresh_token"):
            self.account.refresh_token = body["refresh_token"]
        self.account.last_refreshed_at = timezone.now()
        self.account.save(
            update_fields=[
                "access_token",
                "refresh_token",
                "last_refreshed_at",
                "updated_at",
            ]
        )
        logger.info("Refreshed the Samad access token for %s", self.account.label)
        return True, "Access token refreshed."

    # -- Programs -----------------------------------------------------------

    def fetch_programs(self, week_start, self_id="1"):
        """Ask whether `week_start` is open, refreshing the token if needed."""
        # Renew proactively when the JWT says it has already expired, so the
        # common case costs one request instead of a guaranteed 401 plus retry.
        refreshed = False
        if self.account.access_token_expired():
            ok, message = self.refresh_access_token()
            refreshed = ok
            if not ok:
                return ProgramsResult(
                    outcome=Outcome.AUTH_FAILED,
                    detail=f"Access token expired and refresh failed. {message}",
                )

        result = self._request_programs(week_start, self_id)

        if result.http_status == 401 and self.account.refresh_token and not refreshed:
            logger.info("Got 401; trying the refresh token once.")
            ok, message = self.refresh_access_token()
            if not ok:
                return ProgramsResult(
                    outcome=Outcome.AUTH_FAILED,
                    http_status=401,
                    detail=f"Request returned 401 and refresh failed. {message}",
                )
            result = self._request_programs(week_start, self_id)
            result.refreshed = True
        else:
            result.refreshed = refreshed

        return result

    def _request_programs(self, week_start, self_id):
        params = {"weekStartDate": as_api_param(week_start)}
        if self_id:
            params["selfId"] = self_id

        try:
            response = self.session.get(
                self._url("PROGRAMS_PATH"),
                params=params,
                headers={"Authorization": f"Bearer {self.account.access_token}"},
                timeout=self.config["REQUEST_TIMEOUT"],
            )
        except requests.RequestException as exc:
            return ProgramsResult(
                outcome=Outcome.NETWORK_ERROR,
                detail=f"{type(exc).__name__}: {exc}",
            )

        if response.status_code == 401:
            return ProgramsResult(
                outcome=Outcome.AUTH_FAILED,
                http_status=401,
                detail=response.text[:300],
            )

        # A week that is not published yet comes back as HTTP 400 carrying the
        # ordinary envelope, so the body decides before the status code does.
        try:
            body = response.json()
        except ValueError:
            body = None

        if isinstance(body, dict) and body.get("type"):
            return self._interpret(body, response.status_code)

        if response.status_code >= 400:
            return ProgramsResult(
                outcome=Outcome.API_ERROR,
                http_status=response.status_code,
                detail=f"HTTP {response.status_code}: {response.text[:300]}",
            )

        return ProgramsResult(
            outcome=Outcome.API_ERROR,
            http_status=response.status_code,
            detail=f"Response was not the expected envelope: {response.text[:300]}",
        )

    def _interpret(self, body, http_status):
        api_type = str(body.get("type") or "")
        api_code = body.get("code")
        message = body.get("messageFa") or body.get("message") or ""
        resource = body.get("messageResource") or ""

        if api_type == TYPE_INFO:
            days, programs, reservable = summarise_payload(body.get("payload") or {})
            return ProgramsResult(
                outcome=Outcome.OPEN,
                http_status=http_status,
                api_type=api_type,
                api_code=api_code,
                api_message=message,
                program_count=programs,
                reservable_count=reservable,
                days=days,
            )

        if api_type == TYPE_ERROR:
            expected = (
                resource in EXPECTED_NOT_OPEN_RESOURCES
                or api_code in EXPECTED_NOT_OPEN_CODES
            )
            return ProgramsResult(
                outcome=Outcome.NOT_OPEN,
                http_status=http_status,
                api_type=api_type,
                api_code=api_code,
                api_message=message,
                detail=(
                    ""
                    if expected
                    else f"Unfamiliar ERROR code ({resource or api_code}); "
                    "treating it as not open yet."
                ),
            )

        # Neither INFO nor ERROR: the endpoint is not answering the way it
        # should, which is worth a word rather than an assumption.
        return ProgramsResult(
            outcome=Outcome.API_ERROR,
            http_status=http_status,
            api_type=api_type,
            api_code=api_code,
            api_message=message,
            detail=f"Unexpected response type {api_type!r}.",
        )


def summarise_payload(payload):
    """Condense selfWeekPrograms into per-day summaries for the notification."""
    days = []
    total = 0
    reservable_total = 0

    for day_programs in payload.get("selfWeekPrograms") or []:
        if not day_programs:
            continue
        summary = DaySummary(
            date=day_programs[0].get("date", ""),
            day=day_programs[0].get("dayTranslated", ""),
        )
        for program in day_programs:
            total += 1
            name = program.get("foodName") or program.get("foodTypeTitle") or ""
            if name and name not in summary.foods:
                summary.foods.append(name)
            # The API flips this to False once the program can actually be booked.
            if program.get("reserveRuleViolated") is False:
                summary.reservable += 1
                reservable_total += 1
        days.append(summary)

    return days, total, reservable_total
