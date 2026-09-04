"""Check whether a LinkedIn profile URL confirms a real, publicly-visible profile.

Automated access to LinkedIn is contrary to their User Agreement. This module
is deliberately the smallest check that can still add one POSITIVE signal: one
GET request per candidate, against a URL the candidate themselves volunteered
(in the CV text or the Trakstar ATS field), reading nothing from the response
but its status code, and storing nothing but the resulting two-word verdict.
It exists at all only because it is gated behind an explicit config flag
(`gates.check_linkedin_liveness` in role.json) precisely so it can be
switched off without a code change.

WHAT THIS CHECK CAN AND CANNOT ESTABLISH
-----------------------------------------
A 200 response with a real page means the profile is real AND publicly
visible to an anonymous request — worth surfacing as a positive fact that
corroborates the candidate's identity. Any other response means NOTHING
about whether the profile exists. It is not weaker evidence of absence; it
is NO evidence of absence at all — see the finding below for why.

THE FINDING THAT REVERSED THIS CHECK'S ORIGINAL CONCLUSION
------------------------------------------------------------
This module originally also mapped LinkedIn's HTTP 999 to "dead" (verdict:
the profile does not exist), on the strength of four manually-picked URLs
(two real, two fabricated) checked both ways. Run against the real pool —
69 live candidates, 54 with a LinkedIn URL to check — that mapping reported
26 of 54 profiles (48%) as "dead". Three of those 26 were pulled and
re-examined by hand:

  - All three returned HTTP 999 with a BYTE-IDENTICAL 1530-byte response
    body — the exact same stub LinkedIn serves for a slug fabricated
    specifically for the original four-URL test.
  - One of the three was in fact an obviously fake value. Another belonged
    to the TOP-RANKED CANDIDATE IN THE POOL — a real person. The response
    does not distinguish them.
  - The four profiles that returned 200 in the original test were all
    public, heavily-indexed public figures. LinkedIn serves those pages to
    anonymous visitors. An ordinary member's profile is NOT public by
    default and gets the identical 999 bot-wall as a slug that resolves to
    nothing.

A 999 therefore measures PUBLIC VISIBILITY, not EXISTENCE — and those are
different populations of people. Treating 999 as "dead" would have docked
26 real candidates 10 points each purely for having ordinary, unremarkable
privacy settings. `"dead"` has consequently been removed from this module's
possible return values entirely: no caller can act on a conclusion the data
never supported. (If you are reading this while adding a new status-code
branch: a non-200 status is *never* evidence of "does not exist"; map it to
`"unknown"`, full stop.)

WHY THIS MODULE NOW SENDS A GET, NOT A HEAD
---------------------------------------------
The original measurement used HEAD, and found HEAD returned 405 for the
four public profiles and 999 for the four fabricated ones. That is the
identical flaw one layer down: HEAD's 405-vs-999 split is the same
public-visible-vs-not-public-visible split as GET's 200-vs-999, just
surfaced on a different status code — LinkedIn still runs its bot-wall
check first and only then declines the HEAD method. So a HEAD 405 carries
the same "visible, therefore real" information a GET 200 does, but a HEAD
999 is exactly as uninformative as a GET 999. Only a GET's 200 comes with
an actual rendered page behind it, which is the only version of "confirmed
live" this module now trusts — so it sends a GET, accepting the cost of
transferring (and immediately discarding) a response body per candidate.

MAPPING
-------
    200                                         -> "live"
    999, 405, every other status code, every    -> "unknown"
    network error, every timeout

`"unknown"` must never be penalised anywhere downstream —
`screen.rank.compute_penalties` adds no penalty for any liveness value at
all; only `"live"` is surfaced, as a positive corroborating fact in the
report's evidence panel, never as a deduction.

ZERO RETENTION
--------------
No header or body content is kept anywhere. The GET's response body is
fetched (per above, only because a real GET is the only trustworthy
positive signal) and discarded the instant its status code has been read —
nothing from it is inspected, logged, or returned. `client` is injectable
so tests never touch the real network; production call sites leave it
`None` and get a short-lived client built here with a browser User-Agent
and a ~15s timeout. The throttle below (a flat per-call delay) is
unchanged by this reversal.
"""

from __future__ import annotations

import time
from typing import Callable

import httpx

LIVE = "live"
UNKNOWN = "unknown"

TIMEOUT_SECONDS = 15.0

# A generic desktop-browser UA. LinkedIn's 200/999 distinction was measured
# with a browser-shaped request; an obviously non-browser User-Agent risks a
# different (untested) code path on their side.
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
    """GET `url` (following redirects) and return "live" or "unknown".

    `"dead"` is NOT a possible return value. See the module docstring: a
    non-200 response (including LinkedIn's 999 bot-wall) is indistinguishable
    between a fabricated slug and a real, merely-private profile, so it is
    never treated as evidence the profile does not exist.

    Reads and stores nothing from the response but its status code -- the
    body is transferred (a GET, unlike HEAD, is the only request shape whose
    200 is a trustworthy positive — see module docstring) but never read or
    kept anywhere. `client` is injectable so tests never touch the real
    network; production call sites leave it `None` and get a short-lived
    client built here with a browser User-Agent and a ~15s timeout.

    Mapping: 200 -> "live" (a real, publicly-visible profile). Every other
    status code (999, 405, or anything else), every network error, and every
    timeout -> "unknown". "unknown" carries no meaning either way -- it must
    never be penalised the way a stronger negative conclusion might be, and
    must never be surfaced as if it were doubt about the candidate.
    """
    owns_client = client is None
    if client is None:
        client = httpx.Client(follow_redirects=True)

    try:
        try:
            response = client.get(
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

        if response.status_code == 200:
            return LIVE
        return UNKNOWN
    finally:
        if owns_client:
            client.close()
