"""The client's reading of the programs endpoint, including the 401 dance."""

from datetime import date

import requests
from django.test import TestCase

from sofreh.models import Outcome, SamadAccount
from sofreh.services.samad import SamadClient

from .fixtures import (
    PROGRAMS_NOT_OPEN,
    PROGRAMS_OPEN,
    PROGRAMS_UNKNOWN_ERROR,
    TOKEN_REFRESH_OK,
)
from .support import FakeResponse, FakeSession, make_jwt

FAR_FUTURE = 4883264000  # year 2124
LONG_PAST = 1000000000  # year 2001

WEEK = date(2026, 10, 3)


class SamadClientTests(TestCase):
    def setUp(self):
        self.account = SamadAccount.objects.create(
            label="test",
            access_token=make_jwt(exp=FAR_FUTURE, user_name="123456789"),
            refresh_token=make_jwt(exp=FAR_FUTURE),
        )

    def client_with(self, **kwargs):
        session = FakeSession(**kwargs)
        return SamadClient(self.account, session=session), session

    def test_open_week_is_read_as_open(self):
        client, session = self.client_with(
            get_responses=[FakeResponse(200, PROGRAMS_OPEN)]
        )

        result = client.fetch_programs(WEEK, self_id="1")

        self.assertEqual(result.outcome, Outcome.OPEN)
        self.assertEqual(result.api_type, "INFO")
        self.assertEqual(result.program_count, 4)
        # Only the Tuesday entries have reserveRuleViolated=False.
        self.assertEqual(result.reservable_count, 2)
        self.assertEqual([day.day for day in result.days], ["Saturday", "Tuesday"])
        self.assertIn("زرشک‌پلو با مرغ", result.days[0].foods)

    def test_week_without_program_is_not_open_rather_than_an_error(self):
        client, _ = self.client_with(
            get_responses=[FakeResponse(200, PROGRAMS_NOT_OPEN)]
        )

        result = client.fetch_programs(WEEK)

        self.assertEqual(result.outcome, Outcome.NOT_OPEN)
        self.assertEqual(result.api_code, 10108005)
        self.assertFalse(result.is_problem)

    def test_not_open_is_recognised_even_though_it_arrives_as_http_400(self):
        # The live endpoint answers an unpublished week with 400 plus the
        # normal envelope. Reading the status code alone would call that a
        # failure and alert the user every few minutes.
        client, _ = self.client_with(
            get_responses=[FakeResponse(400, PROGRAMS_NOT_OPEN)]
        )

        result = client.fetch_programs(WEEK)

        self.assertEqual(result.outcome, Outcome.NOT_OPEN)
        self.assertEqual(result.http_status, 400)
        self.assertFalse(result.is_problem)

    def test_http_error_without_an_envelope_is_still_an_error(self):
        client, _ = self.client_with(
            get_responses=[FakeResponse(502, text="<html>bad gateway</html>")]
        )

        result = client.fetch_programs(WEEK)

        self.assertEqual(result.outcome, Outcome.API_ERROR)
        self.assertEqual(result.http_status, 502)

    def test_any_error_type_means_not_open(self):
        # ERROR means not open, full stop -- even a code we have not seen.
        # The unfamiliar code is still recorded so it shows up in the log.
        client, _ = self.client_with(
            get_responses=[FakeResponse(200, PROGRAMS_UNKNOWN_ERROR)]
        )

        result = client.fetch_programs(WEEK)

        self.assertEqual(result.outcome, Outcome.NOT_OPEN)
        self.assertFalse(result.is_problem)
        self.assertEqual(result.api_code, 10100001)
        self.assertIn("Unfamiliar ERROR code", result.detail)

    def test_a_response_that_is_neither_info_nor_error_is_a_problem(self):
        client, _ = self.client_with(
            get_responses=[FakeResponse(200, {"type": "WAT", "code": 1})]
        )

        result = client.fetch_programs(WEEK)

        self.assertEqual(result.outcome, Outcome.API_ERROR)
        self.assertTrue(result.is_problem)

    def test_request_shape_matches_the_documented_endpoint(self):
        client, session = self.client_with(
            get_responses=[FakeResponse(200, PROGRAMS_OPEN)]
        )

        client.fetch_programs(WEEK, self_id="1")

        call = session.get_calls[0]
        self.assertEqual(
            call["url"],
            "https://setad.dining.sharif.edu/rest/reservations/programs/v2",
        )
        self.assertEqual(call["params"]["weekStartDate"], "2026-10-03 00:00:00")
        self.assertEqual(call["params"]["selfId"], "1")
        self.assertEqual(
            call["headers"]["Authorization"], f"Bearer {self.account.access_token}"
        )

    def test_blank_self_id_omits_the_parameter(self):
        client, session = self.client_with(
            get_responses=[FakeResponse(200, PROGRAMS_OPEN)]
        )

        client.fetch_programs(WEEK, self_id="")

        self.assertNotIn("selfId", session.get_calls[0]["params"])

    def test_401_triggers_one_refresh_and_a_retry(self):
        client, session = self.client_with(
            get_responses=[
                FakeResponse(401, text="Unauthorized"),
                FakeResponse(200, PROGRAMS_OPEN),
            ],
            post_responses=[FakeResponse(200, TOKEN_REFRESH_OK)],
        )

        result = client.fetch_programs(WEEK)

        self.assertEqual(result.outcome, Outcome.OPEN)
        self.assertTrue(result.refreshed)
        self.assertEqual(len(session.post_calls), 1)

        refresh_body = session.post_calls[0]["data"]
        self.assertEqual(refresh_body["grant_type"], "refresh_token")
        self.assertEqual(refresh_body["client_id"], "samad-mobile")

        self.account.refresh_from_db()
        self.assertEqual(self.account.access_token, TOKEN_REFRESH_OK["access_token"])
        self.assertEqual(self.account.refresh_token, TOKEN_REFRESH_OK["refresh_token"])
        self.assertIsNotNone(self.account.last_refreshed_at)
        # The retry must use the new token, not the stale one.
        self.assertEqual(
            session.get_calls[1]["headers"]["Authorization"],
            f"Bearer {TOKEN_REFRESH_OK['access_token']}",
        )

    def test_401_with_a_dead_refresh_token_reports_auth_failure(self):
        client, session = self.client_with(
            get_responses=[FakeResponse(401, text="Unauthorized")],
            post_responses=[FakeResponse(400, text="invalid_grant")],
        )

        result = client.fetch_programs(WEEK)

        self.assertEqual(result.outcome, Outcome.AUTH_FAILED)
        self.assertIn("invalid_grant", result.detail)
        # One GET, one refresh attempt, and no endless retrying.
        self.assertEqual(len(session.get_calls), 1)
        self.assertEqual(len(session.post_calls), 1)

    def test_expired_access_token_is_refreshed_before_spending_a_request(self):
        self.account.access_token = make_jwt(exp=LONG_PAST)
        self.account.save()
        client, session = self.client_with(
            get_responses=[FakeResponse(200, PROGRAMS_OPEN)],
            post_responses=[FakeResponse(200, TOKEN_REFRESH_OK)],
        )

        result = client.fetch_programs(WEEK)

        self.assertEqual(result.outcome, Outcome.OPEN)
        self.assertTrue(result.refreshed)
        # Refreshed first, so the endpoint was only called once.
        self.assertEqual(len(session.get_calls), 1)

    def test_expired_token_with_no_refresh_token_fails_fast(self):
        self.account.access_token = make_jwt(exp=LONG_PAST)
        self.account.refresh_token = ""
        self.account.save()
        client, session = self.client_with()

        result = client.fetch_programs(WEEK)

        self.assertEqual(result.outcome, Outcome.AUTH_FAILED)
        self.assertEqual(session.get_calls, [])

    def test_network_failure_is_its_own_outcome(self):
        client, _ = self.client_with(
            get_responses=[requests.ConnectionError("dns is down")]
        )

        result = client.fetch_programs(WEEK)

        self.assertEqual(result.outcome, Outcome.NETWORK_ERROR)
        self.assertIn("dns is down", result.detail)
