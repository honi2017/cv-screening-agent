"""Tests for screen.linkedin_check.check_profile.

Every test uses httpx.MockTransport (or the module's own `client` injection
point) -- none ever reaches the real network. See the module docstring for
why this check's mapping is deliberately narrow: 200 -> "live", and
EVERYTHING else -- 999, 405, any other status, any network error, any
timeout -- -> "unknown". "dead" is not a possible return value: a
non-200 response cannot tell a fabricated slug from a real profile that is
merely not public, so it is never treated as evidence of nonexistence.
"""

from __future__ import annotations

import httpx
import pytest

from screen.linkedin_check import check_profile

URL = "https://www.linkedin.com/in/somebody"


def _client(status_code: int | None = None, exc: Exception | None = None, capture: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture["method"] = request.method
            capture["headers"] = request.headers
        if exc is not None:
            raise exc
        return httpx.Response(status_code)

    return httpx.Client(transport=httpx.MockTransport(handler))


# --- The mapping --------------------------------------------------------


def test_200_is_live():
    assert check_profile(URL, client=_client(200)) == "live"


def test_999_is_unknown():
    assert check_profile(URL, client=_client(999)) == "unknown"


def test_405_is_unknown():
    # 405 used to map to "live" back when this module sent HEAD requests
    # (LinkedIn declines the HEAD method on a profile route). Now that the
    # module sends GET, a 405 has no special meaning and, like every other
    # non-200 status, tells us nothing about whether the profile exists.
    assert check_profile(URL, client=_client(405)) == "unknown"


def test_403_is_unknown():
    assert check_profile(URL, client=_client(403)) == "unknown"


def test_500_is_unknown():
    assert check_profile(URL, client=_client(500)) == "unknown"


def test_timeout_is_unknown():
    exc = httpx.TimeoutException("timed out", request=httpx.Request("GET", URL))
    assert check_profile(URL, client=_client(exc=exc)) == "unknown"


def test_connection_error_is_unknown():
    exc = httpx.ConnectError("connection refused", request=httpx.Request("GET", URL))
    assert check_profile(URL, client=_client(exc=exc)) == "unknown"


# --- "dead" is gone: assert it explicitly, across every input -------------


@pytest.mark.parametrize(
    "make_client",
    [
        lambda: _client(200),
        lambda: _client(999),
        lambda: _client(405),
        lambda: _client(403),
        lambda: _client(500),
        lambda: _client(exc=httpx.TimeoutException("timed out", request=httpx.Request("GET", URL))),
        lambda: _client(exc=httpx.ConnectError("connection refused", request=httpx.Request("GET", URL))),
    ],
    ids=["200", "999", "405", "403", "500", "timeout", "connect-error"],
)
def test_no_input_ever_produces_dead(make_client):
    assert check_profile(URL, client=make_client()) != "dead"


# --- Regression: a 999 bot-wall must never be read as "dead" --------------
#
# This module used to map LinkedIn's HTTP 999 to "dead" (verdict: the
# profile does not exist), based on four hand-picked URLs (two real, two
# fabricated) checked both ways. Run against the real applicant pool -- 69
# live candidates, 54 with a LinkedIn URL to check -- that mapping reported
# 26 of 54 profiles (48%) as "dead". Three of those 26 were pulled and
# re-examined by hand: all three returned HTTP 999 with a BYTE-IDENTICAL
# 1530-byte response body -- the exact same stub LinkedIn serves for a slug
# fabricated specifically for the original four-URL test. One of the three
# was in fact an obviously fake value; another belonged to the TOP-RANKED
# CANDIDATE IN THE POOL -- a real person the response does not distinguish
# from the fake one. Had "dead" stayed live, this would have cost 26 real
# candidates 10 points each purely for having ordinary, unremarkable
# LinkedIn privacy settings, not for anything about their candidacy.


def test_999_bot_wall_regression_must_be_unknown_not_dead():
    assert check_profile(URL, client=_client(999)) == "unknown"


# --- Request shape: GET, not HEAD; a browser User-Agent -------------------
#
# HEAD carried the identical flaw one layer down (405 for public profiles,
# 999 for everything else -- the same public-visibility split, not an
# existence split), so only a GET's 200 -- which comes with an actual
# rendered page behind it -- is trusted as a positive. See the module
# docstring for the full reasoning.


def test_sends_a_get_request_with_a_browser_user_agent():
    capture: dict = {}
    check_profile(URL, client=_client(200, capture=capture))
    assert capture["method"] == "GET"
    assert "mozilla" in capture["headers"]["user-agent"].lower()


# --- Nothing but the verdict is retained ---------------------------------


def test_returns_only_the_verdict_string():
    result = check_profile(URL, client=_client(200))
    assert result == "live"
    assert isinstance(result, str)
