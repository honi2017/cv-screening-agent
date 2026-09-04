"""Pull candidates and CV files from Trakstar Hire, or from a local folder.

Trakstar allows 100 requests per 5 minutes per IP, so the client self-throttles
below that and backs off on 429. Nothing here judges anything; it only fills the
cache that later stages read.

READ-ONLY BY CONSTRUCTION. This is a live recruiting pipeline with real
applicants, and Phase 2's write-back is explicitly out of scope. TrakstarClient
exposes no write capability whatsoever -- no post/put/patch/delete method,
not even unused -- and its single private request helper hard-codes the HTTP
method to GET and asserts it, so a future edit that tries to introduce a
write fails loudly and immediately instead of silently mutating a real
candidate record. The only requests this module ever issues are:
  - GET /v2/candidates?opening_id=...&state=...   (list_candidates)
  - GET /v2/candidates/{id}                       (get_candidate_detail)
  - GET of a resume.file_url                      (download_resume)

SCOPED TO ONE OPENING, ONE STATE. The account holds 186 openings; an
unscoped query would pull real applications for 185 unrelated jobs.
list_candidates requires a non-empty opening_id (raises ValueError
otherwise) and always sends state=in_process alongside it -- 199 of 263
candidates for this opening were already rejected by a human, and
re-screening a rejected candidate risks resurfacing someone deliberately
declined. Any candidate whose opening_id does not match the requested one
is discarded -- both at the list stage and again on the detail response --
and counted rather than silently dropped, so a pagination or filter bug is
visible in the run summary instead of quietly mixing in another job's
applicants.

The list endpoint returns only summary fields -- no `resume`, no
`profile_data`. Those live on the detail endpoint only, so fetch_trakstar
does one list call per page, then one detail call per candidate that needs
processing (skipped whenever the list row's updated_date already matches
the cached one), then the resume download. created_date/updated_date are
UNIX integers, not ISO strings -- they are stored and compared as such.
"""

from __future__ import annotations

import csv
import hashlib
import json
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

import httpx

BASE_URL = "https://api.recruiterbox.com/v2/"
PAGE_LIMIT = 250
MAX_RETRIES = 5
DEFAULT_STATE = "in_process"
_RESUME_EXTENSIONS = (".pdf", ".docx")


class Throttle:
    """Allow at most `max_requests` in any `window_seconds` sliding window."""

    def __init__(
        self,
        max_requests: int = 90,
        window_seconds: float = 300.0,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._sleep = sleep
        self._now = now
        self._stamps: deque[float] = deque()

    def wait(self) -> None:
        now = self._now()
        while self._stamps and now - self._stamps[0] >= self.window_seconds:
            self._stamps.popleft()
        if len(self._stamps) >= self.max_requests:
            delay = self.window_seconds - (now - self._stamps[0]) + 0.1
            if delay > 0:
                self._sleep(delay)
        self._stamps.append(self._now())


class TrakstarClient:
    """Read-only client for the Trakstar Hire v2 API.

    No post/put/patch/delete method exists on this class, not even unused.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = BASE_URL,
        client: httpx.Client | None = None,
        throttle: Throttle | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise PermissionError(
                "TRAKSTAR_API_KEY is empty — set it in the project-root .env "
                "or export it in the environment"
            )
        self.base_url = base_url.rstrip("/") + "/"
        self._client = client or httpx.Client(timeout=60.0, follow_redirects=True)
        self._auth = (api_key, "")
        self._throttle = throttle or Throttle()
        self._sleep = sleep
        # Set fresh by every list_candidates() call; read immediately after
        # by fetch_trakstar so the discard count reaches the run summary.
        self.last_list_discarded = 0

    def _get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        """The one request path in this client. Hard-coded to GET.

        Takes no `method` argument by design: a future edit that tries to
        thread one through must first change this line, and the assert
        right below fails loudly if it lands on anything but GET.
        """
        method = "GET"
        assert method == "GET", "TrakstarClient is read-only — GET is the only allowed method"
        for attempt in range(MAX_RETRIES):
            self._throttle.wait()
            response = self._client.request(method, url, params=params, auth=self._auth)
            if response.status_code == 401:
                raise PermissionError(
                    "Trakstar returned 401 — check TRAKSTAR_API_KEY (needs Super Admin)"
                )
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == MAX_RETRIES - 1:
                    response.raise_for_status()
                retry_after = float(response.headers.get("Retry-After", 2**attempt))
                self._sleep(retry_after)
                continue
            response.raise_for_status()
            return response
        raise RuntimeError("unreachable")

    def list_candidates(
        self, opening_id: str, state: str = DEFAULT_STATE
    ) -> list[dict[str, Any]]:
        """GET /v2/candidates?opening_id=...&state=..., paginated.

        Requires a non-empty opening_id — the account holds 186 openings,
        and an unscoped query would pull real applications for 185
        unrelated jobs. Any row whose own opening_id doesn't match the one
        requested is dropped rather than kept, and the count is exposed via
        `self.last_list_discarded` so a pagination/filter bug is visible.
        """
        if not opening_id:
            raise ValueError("opening_id is required — refusing an unscoped candidate query")

        out: list[dict[str, Any]] = []
        discarded = 0
        offset = 0
        while True:
            payload = self._get(
                f"{self.base_url}candidates",
                params={
                    "opening_id": opening_id,
                    "state": state,
                    "limit": PAGE_LIMIT,
                    "offset": offset,
                },
            ).json()
            page = payload.get("objects") or payload.get("results") or []
            for candidate in page:
                if str(candidate.get("opening_id")) != str(opening_id):
                    discarded += 1
                    continue
                out.append(candidate)
            # meta.total, NOT meta.total_count — the latter does not exist
            # on the real API and pagination would silently stop at page 1.
            total = int(payload.get("meta", {}).get("total", offset + len(page)))
            offset += PAGE_LIMIT
            if offset >= total or not page:
                break

        self.last_list_discarded = discarded
        return out

    def get_candidate_detail(self, candidate_id: Any) -> dict[str, Any]:
        """GET /v2/candidates/{id} — carries resume, profile_data, state,
        stage_name; not present on the list endpoint."""
        return self._get(f"{self.base_url}candidates/{candidate_id}").json()

    def download_resume(self, url: str, dest: Path) -> None:
        """GET a resume.file_url and save it atomically.

        The URL needs no authentication (it is a tokenised redirect that
        returns the file body regardless), but sending the auth header
        anyway is harmless, so `_get` is reused unchanged. Redirects are
        followed because the default client is built with
        follow_redirects=True.
        """
        response = self._get(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_bytes(response.content)
        tmp.replace(dest)


def _load_cached(paths) -> dict[int, dict[str, Any]]:
    if not paths.candidates_json.exists():
        return {}
    try:
        return {int(c["id"]): c for c in json.loads(paths.candidates_json.read_text())}
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}


def _save_candidates(paths, candidates: list[dict[str, Any]]) -> None:
    tmp = paths.candidates_json.with_suffix(paths.candidates_json.suffix + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(candidates, indent=2))
    tmp.replace(paths.candidates_json)


def _resume_exists(paths, cid: int) -> bool:
    return any((paths.resumes / f"{cid}{ext}").exists() for ext in _RESUME_EXTENSIONS)


def _resume_ext(file_name: str) -> str:
    """Real extension of a downloaded resume, defaulting to .pdf.

    About 23% of real resumes are DOCX; saving under the true extension
    lets later stages (parse_pdf) resolve whichever exists.
    """
    ext = Path(file_name or "").suffix.lower()
    return ext if ext in _RESUME_EXTENSIONS else ".pdf"


def _drop_stale_resume(paths, cid: int, keep_ext: str) -> None:
    """Remove a resume saved under a different extension in an earlier run,
    so exactly one resume file exists per candidate for later stages to find."""
    for ext in _RESUME_EXTENSIONS:
        if ext == keep_ext:
            continue
        stale = paths.resumes / f"{cid}{ext}"
        if stale.exists():
            stale.unlink()


def fetch_trakstar(paths, client: TrakstarClient, opening_id: str) -> dict[str, Any]:
    """Sync the local cache with Trakstar, downloading only what changed.

    Two-step per the real API shape: a list call gets summary rows (no
    resume, no profile_data), then a detail call fills those in for any
    candidate that is new or whose updated_date changed. Unchanged
    candidates reuse their cached (already-detailed) record and skip the
    detail call entirely — that is what keeps incremental runs cheap.
    """
    if not opening_id:
        raise ValueError("opening_id is required — refusing an unscoped candidate query")

    remote_rows = client.list_candidates(opening_id)
    foreign_discarded = client.last_list_discarded
    cached = _load_cached(paths)

    merged: list[dict[str, Any]] = []
    new: list[int] = []
    updated: list[int] = []
    unchanged: list[int] = []
    download_failed: dict[int, str] = {}

    for row in remote_rows:
        cid = int(row["id"])
        previous = cached.get(cid)
        changed = (
            previous is None
            or previous.get("updated_date") != row.get("updated_date")
            or not _resume_exists(paths, cid)
        )

        if not changed:
            unchanged.append(cid)
            merged.append(previous)
            continue

        detail = client.get_candidate_detail(cid)
        # Verify opening_id again on the detail response, before writing
        # anything to the cache — a cheap defence against a pagination or
        # filter error mixing candidate pools together.
        if str(detail.get("opening_id")) != str(opening_id):
            foreign_discarded += 1
            continue

        candidate = {**row, **detail}
        resume_info = candidate.get("resume") or {}
        url = resume_info.get("file_url")

        if not url:
            download_failed[cid] = "no_resume_url"
        else:
            ext = _resume_ext(resume_info.get("file_name", ""))
            dest = paths.resumes / f"{cid}{ext}"
            try:
                client.download_resume(url, dest)
                _drop_stale_resume(paths, cid, ext)
            except (httpx.HTTPError, OSError) as exc:
                download_failed[cid] = f"{type(exc).__name__}: {exc}"

        merged.append(candidate)
        (new if previous is None else updated).append(cid)

    _save_candidates(paths, merged)
    return {
        "new": sorted(new),
        "updated": sorted(updated),
        "unchanged": sorted(unchanged),
        "download_failed": download_failed,
        "foreign_opening_discarded": foreign_discarded,
    }


def _stable_id(name: str) -> int:
    """Deterministic positive id for a folder-sourced CV, stable across runs."""
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    return int(digest, 16) % 900_000_000 + 1_000_000


def _read_csv_rows(csv_path: Path) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    with csv_path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            key = (row.get("Name") or row.get("name") or "").strip().lower()
            if key:
                rows[key] = row
    return rows


def fetch_folder(paths, folder: Path, csv_path: Path | None = None) -> dict[str, Any]:
    """Build the same cache from a folder of PDFs/DOCX plus an optional CSV export.

    Used for development before the API key lands, and for offline testing. File
    stem becomes the candidate name; ids are hashes of the stem so they survive
    re-runs.
    """
    cached = _load_cached(paths)
    csv_rows = _read_csv_rows(csv_path) if csv_path and csv_path.exists() else {}

    files = sorted(folder.glob("*.pdf")) + sorted(folder.glob("*.docx"))
    files.sort(key=lambda p: p.stem.lower())

    candidates: list[dict[str, Any]] = []
    new: list[int] = []
    unchanged: list[int] = []

    for src in files:
        ext = src.suffix.lower()
        name = src.stem.strip()
        cid = _stable_id(name)
        first, _, last = name.partition(" ")
        row = csv_rows.get(name.lower(), {})

        profile_data = [
            {"name": key, "value": row[key].strip()}
            for key in ("LinkedIn", "Location")
            if row.get(key, "").strip()
        ]

        dest = paths.resumes / f"{cid}{ext}"
        payload = src.read_bytes()
        is_new = cid not in cached or not dest.exists()
        if is_new or dest.read_bytes() != payload:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            tmp.write_bytes(payload)
            tmp.replace(dest)
            _drop_stale_resume(paths, cid, ext)

        digest = hashlib.sha256(payload).hexdigest()
        candidates.append(
            {
                "id": cid,
                "first_name": first or name,
                "last_name": last or "",
                "email": (row.get("Email") or row.get("email") or "").strip(),
                "phone": (row.get("Phone") or "").strip(),
                "created_date": (row.get("Applied") or "").strip(),
                "updated_date": digest,
                "stage_name": (row.get("Stage") or "Applied").strip(),
                "state": "in_process",
                "source": "folder",
                "profile_data": profile_data,
                "resume": {"file_name": src.name, "file_url": None},
            }
        )

        if cid in cached and cached[cid].get("updated_date") == digest:
            unchanged.append(cid)
        else:
            new.append(cid)

    _save_candidates(paths, candidates)
    return {
        "new": sorted(new),
        "updated": [],
        "unchanged": sorted(unchanged),
        "download_failed": {},
        "foreign_opening_discarded": 0,
    }
