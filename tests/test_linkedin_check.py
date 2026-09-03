"""Tests for screen.linkedin_check.check_profile.

Every test uses httpx.MockTransport (or the module's own `client` injection
point) -- none ever reaches the real network. See the module docstring for
the empirical mapping this codifies: 405/200 -> live, 999 -> dead, and
everything else -- including any network error or timeout -- -> unknown,
because "unknown" must never be treated as "probably dead".
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


def test_405_is_live():
    assert check_profile(URL, client=_client(405)) == "live"


def test_200_is_live():
    assert check_profile(URL, client=_client(200)) == "live"


def test_999_is_dead():
    assert check_profile(URL, client=_client(999)) == "dead"


def test_403_is_unknown():
    assert check_profile(URL, client=_client(403)) == "unknown"


def test_500_is_unknown():
    assert check_profile(URL, client=_client(500)) == "unknown"


def test_timeout_is_unknown():
    exc = httpx.TimeoutException("timed out", request=httpx.Request("HEAD", URL))
    assert check_profile(URL, client=_client(exc=exc)) == "unknown"


def test_connection_error_is_unknown():
    exc = httpx.ConnectError("connection refused", request=httpx.Request("HEAD", URL))
    assert check_profile(URL, client=_client(exc=exc)) == "unknown"


# --- Request shape: HEAD, not GET; a browser User-Agent ------------------


def test_sends_a_head_request_with_a_browser_user_agent():
    capture: dict = {}
    check_profile(URL, client=_client(200, capture=capture))
    assert capture["method"] == "HEAD"
    assert "mozilla" in capture["headers"]["user-agent"].lower()


# --- Nothing but the verdict is retained ---------------------------------


def test_returns_only_the_verdict_string():
    result = check_profile(URL, client=_client(200))
    assert result == "live"
    assert isinstance(result, str)
