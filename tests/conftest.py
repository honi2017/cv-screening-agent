"""Suite-wide safety net: no test may ever make a real network call.

This matters beyond the obvious (tests must be fast and offline): several
fixture CVs embed a `linkedin.com/in/<slug>` line (see
tests/fixtures/make_fixtures.py), and screen.linkedin_check.check_profile is
wired into the precheck stage behind a config flag that role.json ships as
`true`. Any test that exercises the real role config through build_precheck,
run_stage, or the CLI -- without explicitly injecting its own client -- would
otherwise fire a genuine HEAD request at linkedin.com the moment that flag is
on, purely as a side effect of testing something unrelated.

httpx.Client() defaults to httpx.HTTPTransport for real network I/O;
httpx.MockTransport is a wholly separate class (confirmed: it is not a
subclass of HTTPTransport), so patching only HTTPTransport.handle_request
blocks every real request while leaving every test that already injects a
MockTransport-backed client completely unaffected. The raised error is an
httpx.ConnectError -- exactly the exception screen.linkedin_check.check_profile
already treats as "unknown" -- so a test that hits this path degrades
safely (a candidate whose liveness silently comes back "unknown", never
penalised) instead of touching the network.
"""

from __future__ import annotations

import httpx
import pytest

from screen import linkedin_check


@pytest.fixture(autouse=True)
def _block_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "real network access is disabled in the test suite", request=request
        )

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _blocked)


@pytest.fixture(autouse=True)
def _no_linkedin_throttle_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralise screen.linkedin_check.Throttle's real ~1s sleep suite-wide.

    Several existing fixture CVs embed a `linkedin.com/in/<slug>` line, and
    role.json ships `gates.check_linkedin_liveness: true`, so any test that
    runs the real precheck stage (directly or through the CLI) without
    injecting its own throttle would otherwise pay a real sleep per profile
    URL for a feature that test has nothing to do with. The block above
    already makes the check itself resolve safely to "unknown" with no
    network I/O; this just removes the deliberate delay around it. Patched
    on the class (not a specific instance) so it applies no matter where a
    Throttle gets constructed -- production code is unaffected, since this
    patch only exists inside the test session.
    """
    monkeypatch.setattr(linkedin_check.Throttle, "wait", lambda self: None)
