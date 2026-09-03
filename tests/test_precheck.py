import dataclasses
import json
import sys
from pathlib import Path

import httpx
import pytest

from screen.config import load_role
from screen.parse import parse_pdf, write_parsed
from screen.paths import Paths
from screen.precheck import (
    build_precheck,
    minutes_before_submission,
    precheck_key,
    run_stage,
)

sys.path.insert(0, str(Path(__file__).parent))
from fixtures.make_fixtures import build_all  # noqa: E402

CFG = load_role(Path(__file__).resolve().parents[1] / "roles" / "fde")
TODAY = (2026, 8)


@pytest.fixture(scope="module")
def pdfs(tmp_path_factory):
    return build_all(tmp_path_factory.mktemp("pdfs"))


CANDIDATE = {
    "id": 1,
    "first_name": "Alex",
    "last_name": "Morgan",
    "email": "alex.morgan@example.com",
    "phone": "+1 415 555 0134",
    "created_date": "2026-08-01T10:00:00Z",
    "profile_data": [{"name": "LinkedIn", "value": "https://linkedin.com/in/alexmorgan"}],
}


def test_build_precheck_on_clean_cv(pdfs):
    p, _redacted = build_precheck(CANDIDATE, parse_pdf(pdfs["clean"]), CFG, TODAY, [])
    assert p["candidate_id"] == 1
    assert p["placeholders"] == []
    assert p["hidden_text"]["found"] is False
    assert p["linkedin"]["present"] is True
    assert p["degree"]["present"] is True
    assert p["years_experience"]["computed"] > 9
    assert p["years_experience"]["confidence"] in {"high", "medium"}
    assert p["skills_count"] >= 8
    assert p["intra_cv_duplicate_bullets"] == []


def test_build_precheck_flags_placeholder_cv(pdfs):
    cand = {**CANDIDATE, "id": 2, "profile_data": []}
    p, _redacted = build_precheck(cand, parse_pdf(pdfs["placeholder"]), CFG, TODAY, [])
    assert len(p["placeholders"]) >= 2
    assert p["linkedin"]["present"] is False


def test_build_precheck_flags_hidden_text(pdfs):
    p, _redacted = build_precheck(CANDIDATE, parse_pdf(pdfs["hidden_text"]), CFG, TODAY, [])
    assert p["hidden_text"]["found"] is True


def test_build_precheck_four_year_cv_years_below_five(pdfs):
    p, _redacted = build_precheck({**CANDIDATE, "id": 3}, parse_pdf(pdfs["four_year"]), CFG, TODAY, [])
    assert 4.0 <= p["years_experience"]["computed"] < 6.5


def test_build_precheck_records_pool_duplicates(pdfs):
    dups = [{"with_candidate": 9, "bullet": "shared bullet text goes here for testing"}]
    p, _redacted = build_precheck(CANDIDATE, parse_pdf(pdfs["clean"]), CFG, TODAY, dups)
    assert p["pool_duplicate_bullets"] == dups


def test_build_precheck_includes_redaction_count_and_no_leaks(pdfs):
    p, redacted_md = build_precheck(CANDIDATE, parse_pdf(pdfs["clean"]), CFG, TODAY, [])
    assert p["redaction"]["tokens_replaced"] > 0
    assert "Alex Morgan" not in redacted_md
    assert p["redaction"]["leaks"] == []


def test_precheck_key_changes_with_rules_version():
    assert precheck_key("abc", 1) != precheck_key("abc", 2)
    assert precheck_key("abc", 1) == precheck_key("abc", 1)


def test_minutes_before_submission_computes_gap():
    # PDF D: format, applied 14 minutes later.
    assert minutes_before_submission("D:20260801094600Z", "2026-08-01T10:00:00Z") == pytest.approx(14, abs=1)


def test_minutes_before_submission_none_on_unparseable():
    assert minutes_before_submission("", "2026-08-01T10:00:00Z") is None
    assert minutes_before_submission("D:20260801094600Z", "") is None


# --- Addendum: created_date is a UNIX integer, not an ISO string ------------
#
# Section C of REAL-DATA-ADDENDUM: Trakstar's created_date/updated_date are
# UNIX integers (e.g. 1787819217), not ISO strings. minutes_before_submission
# must accept an int epoch or the real pipeline would silently lose this
# signal on every candidate.


def test_minutes_before_submission_accepts_unix_epoch_int():
    # 2026-08-01T09:46:00Z as a UNIX epoch int, applied 14 minutes later.
    from datetime import datetime, timezone

    epoch = int(datetime(2026, 8, 1, 9, 46, 0, tzinfo=timezone.utc).timestamp())
    applied_epoch = epoch + 14 * 60
    assert minutes_before_submission("D:20260801094600Z", applied_epoch) == pytest.approx(14, abs=1)


def test_minutes_before_submission_accepts_numeric_epoch_string():
    from datetime import datetime, timezone

    epoch = int(datetime(2026, 8, 1, 9, 46, 0, tzinfo=timezone.utc).timestamp())
    applied_epoch_str = str(epoch + 14 * 60)
    assert minutes_before_submission("D:20260801094600Z", applied_epoch_str) == pytest.approx(14, abs=1)


def test_minutes_before_submission_none_on_missing_epoch():
    assert minutes_before_submission("D:20260801094600Z", None) is None


def test_build_precheck_handles_int_created_date(pdfs):
    # A real candidate record's created_date is an int epoch, not a string.
    # The "clean" fixture carries no PDF creation-date metadata, so build a
    # ParsedPdf with one grafted on (parse_pdf itself is exercised by the
    # minutes_before_submission unit tests above).
    import dataclasses
    from datetime import datetime, timezone

    parsed = dataclasses.replace(
        parse_pdf(pdfs["clean"]), meta={**parse_pdf(pdfs["clean"]).meta, "created": "D:20260801094600Z"}
    )
    epoch = int(datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc).timestamp())
    cand = {**CANDIDATE, "id": 4, "created_date": epoch}
    p, _redacted = build_precheck(cand, parsed, CFG, TODAY, [])
    assert p["candidate_id"] == 4
    # minutes_before_submission must not silently fail (return None) just
    # because created_date is an int rather than a string.
    assert p["pdf_meta"]["minutes_before_submission"] == pytest.approx(14, abs=1)


def _seed(tmp_path, pdfs, names):
    """Lay out data/ as the fetch stage would, then return Paths."""
    paths = Paths(root=tmp_path, opening_id="704353")
    paths.ensure()
    candidates = []
    for idx, name in enumerate(names, start=1):
        pdf = paths.resumes / f"{idx}.pdf"
        pdf.write_bytes(pdfs[name].read_bytes())
        candidates.append(
            {
                "id": idx,
                "first_name": f"Cand{idx}",
                "last_name": "Test",
                "email": f"c{idx}@example.com",
                "phone": "",
                "created_date": "2026-08-01T10:00:00Z",
                "profile_data": [],
                "resume": {"file_name": f"{idx}.pdf"},
            }
        )
    paths.candidates_json.write_text(json.dumps(candidates, indent=2))
    for c in candidates:
        parsed = parse_pdf(paths.resumes / f"{c['id']}.pdf")
        write_parsed(parsed, paths.parsed / f"{c['id']}.md", paths.parsed / f"{c['id']}.meta.json")
    return paths


def test_run_stage_writes_prechecks_and_redacted(tmp_path, pdfs):
    paths = _seed(tmp_path, pdfs, ["clean", "placeholder"])
    result = run_stage(paths, CFG, TODAY)
    assert sorted(result["processed"]) == [1, 2]
    assert (paths.prechecks / "1.json").exists()
    assert (paths.redacted / "1.md").exists()
    assert "Cand1" not in (paths.redacted / "1.md").read_text()


def test_run_stage_detects_pool_duplicates_across_candidates(tmp_path, pdfs):
    paths = _seed(tmp_path, pdfs, ["template_a", "template_b"])
    run_stage(paths, CFG, TODAY)
    p1 = json.loads((paths.prechecks / "1.json").read_text())
    assert len(p1["pool_duplicate_bullets"]) >= 3
    assert p1["pool_duplicate_bullets"][0]["with_candidate"] == 2


def test_run_stage_skips_already_processed(tmp_path, pdfs):
    paths = _seed(tmp_path, pdfs, ["clean"])
    run_stage(paths, CFG, TODAY)
    second = run_stage(paths, CFG, TODAY)
    assert second["processed"] == []
    assert second["skipped"] == [1]


def test_run_stage_force_reprocesses(tmp_path, pdfs):
    paths = _seed(tmp_path, pdfs, ["clean"])
    run_stage(paths, CFG, TODAY)
    forced = run_stage(paths, CFG, TODAY, force=True)
    assert forced["processed"] == [1]


def test_run_stage_marks_scanned_pdf_needs_review(tmp_path, pdfs):
    paths = _seed(tmp_path, pdfs, ["scanned"])
    result = run_stage(paths, CFG, TODAY)
    assert result["needs_review"] == {1: "unparseable"}
    assert not (paths.prechecks / "1.json").exists()


# --- Addendum: resumes may be .pdf OR .docx, resolve whichever exists ------


def test_run_stage_resolves_docx_resume(tmp_path, pdfs):
    paths = Paths(root=tmp_path, opening_id="704353")
    paths.ensure()
    dest = paths.resumes / "1.docx"
    dest.write_bytes(pdfs["clean_docx"].read_bytes())
    candidates = [
        {
            "id": 1,
            "first_name": "Cand1",
            "last_name": "Test",
            "email": "c1@example.com",
            "phone": "",
            "created_date": "2026-08-01T10:00:00Z",
            "profile_data": [],
            "resume": {"file_name": "1.docx"},
        }
    ]
    paths.candidates_json.write_text(json.dumps(candidates, indent=2))
    result = run_stage(paths, CFG, TODAY)
    assert result["processed"] == [1]
    assert (paths.prechecks / "1.json").exists()
    payload = json.loads((paths.prechecks / "1.json").read_text())
    # DOCX has no equivalent of PDF hidden-text tricks -- G3 cannot fire.
    assert payload["hidden_text"] == {"found": False, "spans": []}


def test_run_stage_missing_resume_of_either_extension_needs_review(tmp_path, pdfs):
    paths = Paths(root=tmp_path, opening_id="704353")
    paths.ensure()
    candidates = [
        {
            "id": 1,
            "first_name": "Cand1",
            "last_name": "Test",
            "email": "c1@example.com",
            "phone": "",
            "created_date": "2026-08-01T10:00:00Z",
            "profile_data": [],
            "resume": {"file_name": "1.pdf"},
        }
    ]
    paths.candidates_json.write_text(json.dumps(candidates, indent=2))
    result = run_stage(paths, CFG, TODAY)
    assert result["needs_review"] == {1: "missing_resume"}


# --- Change 1: LinkedIn liveness check, wired into the precheck stage ------
#
# Every client here is httpx.MockTransport-backed; none makes a real request
# (the suite-wide conftest.py fixture would block one anyway).


def _counting_client(status_code: int):
    """A client that answers every HEAD with `status_code` and counts calls."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(status_code)

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def _seed_with_linkedin(tmp_path, pdfs, key: str, linkedin_url: str | None):
    """Lay out a single candidate whose LinkedIn URL is the explicit ATS
    field (not the CV text), so a test can change it between runs.
    """
    paths = Paths(root=tmp_path, opening_id="704353")
    paths.ensure()
    pdf = paths.resumes / "1.pdf"
    pdf.write_bytes(pdfs[key].read_bytes())
    profile_data = [{"name": "LinkedIn", "value": linkedin_url}] if linkedin_url else []
    candidates = [
        {
            "id": 1,
            "first_name": "Cand1",
            "last_name": "Test",
            "email": "c1@example.com",
            "phone": "",
            "created_date": "2026-08-01T10:00:00Z",
            "profile_data": profile_data,
            "resume": {"file_name": "1.pdf"},
        }
    ]
    paths.candidates_json.write_text(json.dumps(candidates, indent=2))
    parsed = parse_pdf(pdf)
    write_parsed(parsed, paths.parsed / "1.md", paths.parsed / "1.meta.json")
    return paths


def test_build_precheck_records_linkedin_liveness_verdict(pdfs):
    client, calls = _counting_client(200)  # 200 -> live
    p, _redacted = build_precheck(
        CANDIDATE, parse_pdf(pdfs["clean"]), CFG, TODAY, [], linkedin_client=client
    )
    assert p["linkedin"]["liveness"] == "live"
    assert calls["n"] == 1


def test_build_precheck_reuses_cached_liveness_for_unchanged_url(pdfs):
    # The cached verdict is "unknown" but a live client would answer 200
    # (live) -- if the cache were ignored, the result below would flip to
    # "live".
    client, calls = _counting_client(200)
    previous = {"linkedin": {"url": "https://linkedin.com/in/alexmorgan", "liveness": "unknown"}}
    p, _redacted = build_precheck(
        CANDIDATE, parse_pdf(pdfs["clean"]), CFG, TODAY, [],
        previous=previous, linkedin_client=client,
    )
    assert p["linkedin"]["liveness"] == "unknown"
    assert calls["n"] == 0


def test_build_precheck_rechecks_liveness_when_url_changed(pdfs):
    client, calls = _counting_client(200)
    previous = {"linkedin": {"url": "https://linkedin.com/in/someone-else", "liveness": "unknown"}}
    p, _redacted = build_precheck(
        CANDIDATE, parse_pdf(pdfs["clean"]), CFG, TODAY, [],
        previous=previous, linkedin_client=client,
    )
    assert p["linkedin"]["liveness"] == "live"
    assert calls["n"] == 1


def test_build_precheck_skips_liveness_check_entirely_when_flag_off(pdfs):
    cfg_off = dataclasses.replace(CFG, gates={**CFG.gates, "check_linkedin_liveness": False})

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not make any request when the config flag is off")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    p, _redacted = build_precheck(
        CANDIDATE, parse_pdf(pdfs["clean"]), cfg_off, TODAY, [], linkedin_client=client
    )
    assert "liveness" not in p["linkedin"]


def test_run_stage_does_not_recheck_linkedin_when_url_unchanged(tmp_path, pdfs):
    paths = _seed_with_linkedin(tmp_path, pdfs, "clean", "https://linkedin.com/in/samestable")

    client1, calls1 = _counting_client(200)
    run_stage(paths, CFG, TODAY, linkedin_client=client1)
    assert calls1["n"] == 1
    payload = json.loads((paths.prechecks / "1.json").read_text())
    assert payload["linkedin"]["liveness"] == "live"

    # A forced rebuild with the SAME URL must reuse the cached verdict, not
    # re-request -- proven by a client that would flip the answer to
    # "unknown" if it were called (999 no longer maps to "dead"; see
    # screen.linkedin_check's module docstring).
    client2, calls2 = _counting_client(999)
    run_stage(paths, CFG, TODAY, force=True, linkedin_client=client2)
    assert calls2["n"] == 0
    payload2 = json.loads((paths.prechecks / "1.json").read_text())
    assert payload2["linkedin"]["liveness"] == "live"


def test_run_stage_rechecks_linkedin_when_url_changed(tmp_path, pdfs):
    paths = _seed_with_linkedin(tmp_path, pdfs, "clean", "https://linkedin.com/in/first-slug")

    client1, calls1 = _counting_client(200)
    run_stage(paths, CFG, TODAY, linkedin_client=client1)
    assert calls1["n"] == 1

    candidates = json.loads(paths.candidates_json.read_text())
    candidates[0]["profile_data"] = [
        {"name": "LinkedIn", "value": "https://linkedin.com/in/second-slug"}
    ]
    paths.candidates_json.write_text(json.dumps(candidates, indent=2))

    client2, calls2 = _counting_client(999)
    run_stage(paths, CFG, TODAY, force=True, linkedin_client=client2)
    assert calls2["n"] == 1
    payload = json.loads((paths.prechecks / "1.json").read_text())
    # 999 maps to "unknown", never "dead" -- see screen.linkedin_check's
    # module docstring. What this test actually proves is that the fresh
    # check ran and its result (not the stale cached "live") was stored.
    assert payload["linkedin"]["liveness"] == "unknown"
