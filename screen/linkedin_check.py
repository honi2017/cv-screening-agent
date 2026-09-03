"""Check whether a LinkedIn profile URL actually resolves.

Automated access to LinkedIn is contrary to their User Agreement. This module
is deliberately the smallest check that can still tell a live profile from a
dead one: one HEAD request per candidate, against a URL the candidate
themselves volunteered (in the CV text or the Trakstar ATS field), reading
nothing from the response body and storing nothing but the resulting
three-word verdict. It exists at all only because it is gated behind an
explicit config flag (`gates.check_linkedin_liveness` in role.json)
precisely so it can be switched off without a code change.

Empirically (four real profiles checked both ways, plus six rapid
consecutive GETs to real profiles that all came back 200 -- so 999 is not a
rate-limit artifact at this volume), LinkedIn's responses distinguish a live
profile from one that does not exist:

    Request            Live profile              Non-existent profile
    HEAD /in/<slug>    405 Method Not Allowed     999
    GET  /in/<slug>    200, a real <title>        999, a 1530-byte stub

A HEAD request gets the same signal as a GET for zero transferred body
bytes, so that is what this module sends.

THE CRITICAL SAFETY PROPERTY -- read this before touching the mapping below:
`"unknown"` must never be penalised anywhere downstream. 999 is LinkedIn's
generic "blocked" response, and this module infers "the profile does not
exist" from it -- but that inference is only as good as the assumption that
a given 999 means "this specific slug is fake", not "LinkedIn is currently
blocking whoever is asking, for an unrelated reason". If LinkedIn ever starts
returning 999 broadly -- an IP block, a policy change, a rate limit tripped
at a request volume this project hasn't measured -- every candidate's
profile would suddenly look dead. If `"unknown"` carried the same penalty as
`"dead"` in that situation, the whole pool would be wrongly docked at once,
on the strength of a single ambiguous status code. Mapping anything that is
NOT a confirmed 405/200 (live) or a confirmed 999 (dead) down to `"unknown"`
means a broad failure of this kind silently stops the check from
contributing anything at all, rather than punishing every candidate for it.
`screen.rank` must add a `"dead"`-only penalty and never branch on
`"unknown"` for scoring; do not "fix" that by scoring `"unknown"` too.
"""

from __future__ import annotations

import time
from typing import Callable

import httpx

LIVE = "live"
DEAD = "dead"
UNKNOWN = "unknown"

TIMEOUT_SECONDS = 15.0

# A generic desktop-browser UA. LinkedIn's 405/999/200 distinction was
# measured with a browser-shaped request; an obviously non-browser
# User-Agent risks a different (untested) code path on their side.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class Throttle:
    """A flat per-call delay, so consecutive checks don't fire back-to-back.

    Shaped like screen.fetch.Throttle (a `.wait()` you call before each
    request) but simplified to a fixed sleep rather than a sliding
    request-rate window: this module makes at most one request per
    candidate in a single precheck run, so there is no window to track --
    only "leave about a second between requests" to keep the footprint
    small, per the module docstring above.
    """

    def __init__(
        self, seconds: float = 1.0, sleep: Callable[[float], None] = time.sleep
    ) -> None:
        self._seconds = seconds
        self._sleep = sleep

    def wait(self) -> None:
        if self._seconds > 0:
            self._sleep(self._seconds)


def check_profile(url: str, client: httpx.Client | None = None) -> str:
    """HEAD `url` (following redirects) and return "live", "dead", or "unknown".

    Reads and stores nothing from the response but its status code -- no
    body is fetched (HEAD transfers none) and no header or body content is
    kept anywhere. `client` is injectable so tests never touch the real
    network; production call sites leave it `None` and get a short-lived
    client built here with a browser User-Agent and a ~15s timeout.

    Mapping: 405 or 200 -> "live" (LinkedIn answers a real profile's HEAD
    with 405 since HEAD isn't an allowed method on that route, and answers
    GET with 200); 999 -> "dead" (LinkedIn's stock response for a slug that
    doesn't resolve). Everything else -- any other status code, a timeout,
    a connection failure, or any other network error -- maps to "unknown".
    See the module docstring for why "unknown" is not "probably dead" and
    must never be penalised the way "dead" is.
    """
    owns_client = client is None
    if client is None:
        client = httpx.Client(follow_redirects=True)

    try:
        try:
            response = client.head(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=TIMEOUT_SECONDS,
                follow_redirects=True,
            )
        except (httpx.HTTPError, httpx.InvalidURL):
            # httpx.InvalidURL sits outside the HTTPError hierarchy, but a
            # malformed URL is exactly the same "can't tell, so don't
            # penalise" situation as a network error -- see the module
            # docstring. In practice screen.signals.find_linkedin already
            # validates and normalises the URL before it ever reaches here,
            # so this is a defensive backstop, not an expected path.
            return UNKNOWN

        if response.status_code in (200, 405):
            return LIVE
        if response.status_code == 999:
            return DEAD
        return UNKNOWN
    finally:
        if owns_client:
            client.close()
