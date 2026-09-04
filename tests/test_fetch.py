"""Tests for screen.fetch.

Deliberately rewritten from task-10-brief.md's test code to match the real
API shape measured in REAL-DATA-ADDENDUM.md, which supersedes the brief:
  - meta.total, not meta.total_count
  - the list endpoint returns no resume/profile_data; a detail call does
  - created_date/updated_date are UNIX integers, not ISO strings
  - opening_id scoping and state=in_process are hard constraints, tested
    directly rather than assumed

Every test uses httpx.MockTransport. No test reads .env or makes a real call.
"""

from __future__ import annotations

import base64
import inspect
import json

import httpx
import pytest

from screen.fetch import Throttle, TrakstarClient, fetch_folder, fetch_trakstar
from screen.paths import Paths

OPENING = "704353"
OTHER_OPENING = "999999"


# --- fixtures ----------------------------------------------------------------


def _list_row(cid: int, updated: int = 1787819217, opening_id: str = OPENING) -> dict:
    """A /v2/candidates list-endpoint row: summary fields only, no resume."""
    return {
        "id": cid,
        "opening_id": opening_id,
        "stage_id": 1,
        "email": f"c{cid}@example.com",
        "phone": "",
        "first_name": f"Cand{cid}",
        "last_name": "Test",
        "description": "",
        "created_date": 1787810000,
        "updated_date": updated,
        "source": "Careers page",
        "source_type": "career_site",
        "created_by": None,
        "assigned_to": None,
    }


def _detail(cid: int, opening_id: str = OPENING, ext: str = "pdf") -> dict:
    """A /v2/candidates/{id} detail-endpoint payload: resume, profile_data, state."""
    return {
        "id": cid,
        "opening_id": opening_id,
        "resume": {
            "file_name": f"cv{cid}.{ext}",
            "file_url": f"https://files.test/cv{cid}.{ext}",
        },
        "profile_data": [],
        "state": "in_process",
        "stage_name": "Resume Review",
        "state_metadata": {},
        "labels": [],
    }


def _transport(list_rows, details, pdf_bytes=b"%PDF-1.4 fake", fail_urls=(), calls=None):
    if calls is None:
        calls = {"list": 0, "detail": 0, "download": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        url = str(request.url)
        if path.endswith("/candidates"):
            calls["list"] += 1
            offset = int(request.url.params.get("offset", 0))
            limit = int(request.url.params.get("limit", 250))
            page = list_rows[offset : offset + limit]
            return httpx.Response(
                200,
                json={
                    "meta": {"total": len(list_rows), "offset": offset, "limit": limit},
                    "objects": page,
                },
            )
        if "/candidates/" in path:
            calls["detail"] += 1
            cid = int(path.rsplit("/", 1)[-1])
            detail = details.get(cid)
            if detail is None:
                return httpx.Response(404)
            return httpx.Response(200, json=detail)
        calls["download"] += 1
        if url in fail_urls:
            return httpx.Response(404)
        return httpx.Response(200, content=pdf_bytes)

    return httpx.MockTransport(handler), calls


def _client(list_rows, details, **kw):
    transport, calls = _transport(list_rows, details, **kw)
    client = TrakstarClient(
        api_key="test-key",
        client=httpx.Client(transport=transport),
        throttle=Throttle(max_requests=90, window_seconds=300, sleep=lambda s: None),
    )
    return client, calls


# --- TrakstarClient: read-only enforcement (constraint 1) --------------------


def test_client_exposes_no_write_methods():
    for method in ("post", "put", "patch", "delete"):
        assert not hasattr(TrakstarClient, method)


def test_get_helper_takes_no_method_argument():
    sig = inspect.signature(TrakstarClient._get)
    assert "method" not in sig.parameters


def test_default_client_follows_redirects():
    c = TrakstarClient(api_key="k")
    assert c._client.follow_redirects is True


# --- TrakstarClient: auth, pagination, retry, 401 -----------------------------


def test_list_candidates_paginates():
    rows = [_list_row(i) for i in range(1, 601)]
    client, _ = _client(rows, {})
    got = client.list_candidates(OPENING)
    assert len(got) == 600
    assert got[0]["id"] == 1
    assert got[-1]["id"] == 600


def test_auth_uses_api_key_as_basic_username():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"meta": {"total": 0}, "objects": []})

    c = TrakstarClient(api_key="secret", client=httpx.Client(transport=httpx.MockTransport(handler)))
    c.list_candidates(OPENING)

    expected = "Basic " + base64.b64encode(b"secret:").decode()
    assert seen["auth"] == expected


def test_retries_on_429_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"meta": {"total": 0}, "objects": []})

    c = TrakstarClient(
        api_key="k",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        throttle=Throttle(90, 300, sleep=lambda s: None),
        sleep=lambda s: None,
    )
    assert c.list_candidates(OPENING) == []
    assert calls["n"] == 2


def test_raises_on_401():
    def handler(request):
        return httpx.Response(401, json={"detail": "invalid key"})

    c = TrakstarClient(api_key="bad", client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(PermissionError, match="TRAKSTAR_API_KEY"):
        c.list_candidates(OPENING)


# --- TrakstarClient: opening scoping and state (constraints 2 and 3) ---------


def test_list_candidates_requires_opening_id():
    c = TrakstarClient(
        api_key="k",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={"meta": {"total": 0}, "objects": []})
            )
        ),
    )
    with pytest.raises(ValueError):
        c.list_candidates("")
    with pytest.raises(ValueError):
        c.list_candidates(None)


def test_list_candidates_sends_state_in_process_by_default():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["state"] = request.url.params.get("state")
        seen["opening_id"] = request.url.params.get("opening_id")
        return httpx.Response(200, json={"meta": {"total": 0}, "objects": []})

    c = TrakstarClient(api_key="k", client=httpx.Client(transport=httpx.MockTransport(handler)))
    c.list_candidates(OPENING)
    assert seen["state"] == "in_process"
    assert seen["opening_id"] == OPENING


def test_list_candidates_discards_foreign_opening_candidates():
    rows = [_list_row(1, opening_id=OPENING), _list_row(2, opening_id=OTHER_OPENING)]
    client, _ = _client(rows, {})
    got = client.list_candidates(OPENING)
    assert [c["id"] for c in got] == [1]
    assert client.last_list_discarded == 1


# --- fetch_trakstar: new/updated/unchanged, two-step list+detail -------------


def test_fetch_trakstar_requires_opening_id(tmp_path):
    paths = Paths(root=tmp_path, opening_id=OPENING)
    paths.ensure()
    client, _ = _client([], {})
    with pytest.raises(ValueError):
        fetch_trakstar(paths, client, "")


def test_fetch_trakstar_downloads_new_candidates(tmp_path):
    paths = Paths(root=tmp_path, opening_id=OPENING)
    paths.ensure()
    rows = [_list_row(1), _list_row(2)]
    details = {1: _detail(1), 2: _detail(2)}
    client, calls = _client(rows, details)

    result = fetch_trakstar(paths, client, OPENING)

    assert sorted(result["new"]) == [1, 2]
    assert result["foreign_opening_discarded"] == 0
    assert (paths.resumes / "1.pdf").read_bytes().startswith(b"%PDF")
    saved = json.loads(paths.candidates_json.read_text())
    assert {c["id"] for c in saved} == {1, 2}
    # Every candidate had to go through the detail call since none were cached.
    assert calls["detail"] == 2


def test_fetch_trakstar_skips_unchanged_on_second_run(tmp_path):
    paths = Paths(root=tmp_path, opening_id=OPENING)
    paths.ensure()
    rows = [_list_row(1)]
    details = {1: _detail(1)}

    client1, _ = _client(rows, details)
    fetch_trakstar(paths, client1, OPENING)

    client2, calls2 = _client(rows, details)
    second = fetch_trakstar(paths, client2, OPENING)

    assert second["new"] == []
    assert second["unchanged"] == [1]
    # updated_date matches the cache, so the detail call must be skipped —
    # that is what keeps incremental runs cheap.
    assert calls2["detail"] == 0


def test_fetch_trakstar_redownloads_when_updated_date_changes(tmp_path):
    paths = Paths(root=tmp_path, opening_id=OPENING)
    paths.ensure()

    client1, _ = _client([_list_row(1, updated=1787819217)], {1: _detail(1)})
    fetch_trakstar(paths, client1, OPENING)

    client2, calls2 = _client([_list_row(1, updated=1787900000)], {1: _detail(1)})
    second = fetch_trakstar(paths, client2, OPENING)

    assert second["updated"] == [1]
    assert calls2["detail"] == 1


def test_fetch_trakstar_records_download_failure(tmp_path):
    paths = Paths(root=tmp_path, opening_id=OPENING)
    paths.ensure()
    client, _ = _client(
        [_list_row(1)], {1: _detail(1)}, fail_urls=("https://files.test/cv1.pdf",)
    )
    result = fetch_trakstar(paths, client, OPENING)
    assert 1 in result["download_failed"]
    assert not (paths.resumes / "1.pdf").exists()


def test_fetch_trakstar_handles_missing_resume_url(tmp_path):
    paths = Paths(root=tmp_path, opening_id=OPENING)
    paths.ensure()
    detail = _detail(1)
    detail["resume"] = {}
    client, _ = _client([_list_row(1)], {1: detail})
    result = fetch_trakstar(paths, client, OPENING)
    assert result["download_failed"][1] == "no_resume_url"


def test_fetch_trakstar_saves_docx_extension(tmp_path):
    paths = Paths(root=tmp_path, opening_id=OPENING)
    paths.ensure()
    client, _ = _client(
        [_list_row(1)], {1: _detail(1, ext="docx")}, pdf_bytes=b"PK\x03\x04 fake docx"
    )
    fetch_trakstar(paths, client, OPENING)
    assert (paths.resumes / "1.docx").exists()
    assert not (paths.resumes / "1.pdf").exists()


def test_fetch_trakstar_preserves_int_dates(tmp_path):
    paths = Paths(root=tmp_path, opening_id=OPENING)
    paths.ensure()
    client, _ = _client([_list_row(1, updated=1787819217)], {1: _detail(1)})
    fetch_trakstar(paths, client, OPENING)
    saved = json.loads(paths.candidates_json.read_text())
    assert saved[0]["updated_date"] == 1787819217
    assert isinstance(saved[0]["updated_date"], int)


def test_fetch_trakstar_discards_candidate_whose_detail_opening_id_mismatches(tmp_path):
    paths = Paths(root=tmp_path, opening_id=OPENING)
    paths.ensure()
    # The list row passes the list-level filter (it claims OPENING), but its
    # detail response disagrees — must still be dropped before caching.
    client, _ = _client(
        [_list_row(1, opening_id=OPENING)], {1: _detail(1, opening_id=OTHER_OPENING)}
    )
    result = fetch_trakstar(paths, client, OPENING)
    assert result["new"] == []
    assert result["foreign_opening_discarded"] == 1
    saved = json.loads(paths.candidates_json.read_text())
    assert saved == []
    assert not (paths.resumes / "1.pdf").exists()


# --- fetch_folder --------------------------------------------------------------


def test_fetch_folder_builds_synthetic_candidates(tmp_path):
    src = tmp_path / "cvs"
    src.mkdir()
    (src / "Alex Morgan.pdf").write_bytes(b"%PDF-1.4 a")
    (src / "Riley Chen.pdf").write_bytes(b"%PDF-1.4 b")

    paths = Paths(root=tmp_path / "proj", opening_id=OPENING)
    paths.ensure()
    result = fetch_folder(paths, src)

    assert len(result["new"]) == 2
    assert result["foreign_opening_discarded"] == 0
    saved = json.loads(paths.candidates_json.read_text())
    names = sorted(f"{c['first_name']} {c['last_name']}" for c in saved)
    assert names == ["Alex Morgan", "Riley Chen"]
    for c in saved:
        assert (paths.resumes / f"{c['id']}.pdf").exists()


def test_fetch_folder_supports_docx_files(tmp_path):
    src = tmp_path / "cvs"
    src.mkdir()
    (src / "Jamie Lee.docx").write_bytes(b"PK\x03\x04 fake docx")

    paths = Paths(root=tmp_path / "proj", opening_id=OPENING)
    paths.ensure()
    result = fetch_folder(paths, src)

    assert len(result["new"]) == 1
    saved = json.loads(paths.candidates_json.read_text())
    cid = saved[0]["id"]
    assert (paths.resumes / f"{cid}.docx").exists()


def test_fetch_folder_ids_are_stable_across_runs(tmp_path):
    src = tmp_path / "cvs"
    src.mkdir()
    (src / "Alex Morgan.pdf").write_bytes(b"%PDF-1.4 a")

    paths = Paths(root=tmp_path / "proj", opening_id=OPENING)
    paths.ensure()
    first = json.loads(fetch_folder(paths, src) and paths.candidates_json.read_text())
    second_result = fetch_folder(paths, src)
    second = json.loads(paths.candidates_json.read_text())
    assert first[0]["id"] == second[0]["id"]
    assert second_result["unchanged"] == [first[0]["id"]]


def test_fetch_folder_reads_linkedin_from_csv(tmp_path):
    src = tmp_path / "cvs"
    src.mkdir()
    (src / "Alex Morgan.pdf").write_bytes(b"%PDF-1.4 a")
    csv_path = tmp_path / "export.csv"
    csv_path.write_text(
        "Name,Email,LinkedIn,Location\n"
        "Alex Morgan,alex@example.com,https://linkedin.com/in/alexmorgan,\"Boston, MA\"\n"
    )

    paths = Paths(root=tmp_path / "proj", opening_id=OPENING)
    paths.ensure()
    fetch_folder(paths, src, csv_path=csv_path)
    saved = json.loads(paths.candidates_json.read_text())
    pd = {item["name"]: item["value"] for item in saved[0]["profile_data"]}
    assert pd["LinkedIn"] == "https://linkedin.com/in/alexmorgan"
    assert pd["Location"] == "Boston, MA"
    assert saved[0]["email"] == "alex@example.com"


# --- Throttle ------------------------------------------------------------------


def test_throttle_sleeps_once_the_window_is_full():
    slept = []
    clock = {"t": 0.0}
    t = Throttle(max_requests=2, window_seconds=10, sleep=slept.append, now=lambda: clock["t"])
    t.wait()
    t.wait()
    t.wait()
    assert slept and slept[0] > 0
