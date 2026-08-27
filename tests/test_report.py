import csv
from pathlib import Path

from screen.config import load_role
from screen.ledger import LedgerEntry
from screen.paths import Paths
from screen.rank import Assessment, CutResult
from screen.report import (
    ReportInput,
    render_html,
    render_markdown,
    rows_for_csv,
    write_all,
)

CFG = load_role(Path(__file__).resolve().parents[1] / "roles" / "fde")


def _assessment(cid, final, gate=None, flags=(), penalties=()):
    return Assessment(
        candidate_id=cid,
        gate=gate,
        gate_reasons=[f"{gate} reason for {cid}"] if gate else [],
        fit=final,
        bonus=0.0,
        penalties=list(penalties),
        penalty_total=float(sum(p["points"] for p in penalties)),
        final=final,
        flags=list(flags),
        tier2_count=0.0,
        timezone_hint="ET",
    )


def _verdict(cid):
    keys = CFG.criterion_keys()
    return {
        "candidate_id": cid,
        "redflag": {"flags": [], "years_experience_estimate": {"value": 7, "confidence": "high"}},
        "fit": {
            "scores": {
                k: {"score": 5.0, "quote": f"quote for {k}", "rationale": "because"} for k in keys
            },
            "bonus": {"points": 0, "justification": None},
            "summary": f"Summary for candidate {cid}.",
        },
        "quote_warnings": [],
    }


def _precheck(cid):
    return {
        "candidate_id": cid,
        "pages": 2,
        "pdf_meta": {"producer": "LaTeX", "created": "D:20260801094600Z", "minutes_before_submission": 14},
        "years_experience": {"computed": 7.5, "confidence": "high", "ranges": [["2019-03", "present"]]},
        "linkedin": {"present": True, "source": "trakstar", "url": "https://linkedin.com/in/x", "name_matches": True},
        "degree": {"present": True, "level": "BSc", "field": "Computer Science"},
        "location": {"us_evident": True, "non_us_explicit": False, "timezone_hint": "ET", "raw": "Boston, MA"},
        "skills_count": 12,
        "hidden_text": {"found": False, "spans": []},
        "placeholders": [],
        "pool_duplicate_bullets": [],
        "intra_cv_duplicate_bullets": [],
    }


def _data():
    ids = [1, 2, 3, 4, 5]
    assessments = {
        1: _assessment(1, 82.0),
        2: _assessment(2, 71.0, flags=["no LinkedIn"], penalties=[{"kind": "no_linkedin", "points": 8, "detail": "none found"}]),
        3: _assessment(3, 55.0),
        4: _assessment(4, 40.0, gate="G1"),
        5: _assessment(5, 30.0, gate="G2"),
    }
    cut = CutResult(
        cap=1,
        pool_size=6,
        accepted=[1],
        waitlist=[2, 3],
        gated=[4, 5],
        needs_review=[6],
        newly_accepted=[1],
        newly_gated=[4],
        no_slot=[2],
        calibration_window=[1, 2, 3],
        quality_floor=None,
    )
    return ReportInput(
        run_id="2026-08-28T0800",
        cut=cut,
        assessments=assessments,
        candidates={
            i: {
                "id": i,
                "first_name": f"Cand{i}",
                "last_name": "Test",
                "email": f"c{i}@example.com",
                "created_date": "2026-08-01T10:00:00Z",
            }
            for i in ids + [6]
        },
        prechecks={i: _precheck(i) for i in ids},
        verdicts={i: _verdict(i) for i in ids},
        ledger={
            i: LedgerEntry(
                candidate_id=i,
                status="accepted" if i == 1 else "waitlist" if i in (2, 3) else "gated",
                gate=assessments[i].gate,
                final=assessments[i].final,
                first_seen_run="2026-08-28T0800",
                status_changed_run="2026-08-28T0800",
                pdf_sha256="abc",
                trakstar_updated_date="u",
            )
            for i in ids
        },
        needs_review={6: "unparseable"},
        delta={"new": [1, 2, 3, 4, 5, 6], "newly_accepted": [1], "newly_gated": [4], "no_slot": [2]},
        calibration_note="Reviewed ranks 1-3; no reordering needed.",
        opening_id="704353",
    )


# --- HTML -------------------------------------------------------------------

def test_html_is_self_contained():
    html = render_html(_data(), CFG)
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert "<script src=" not in html
    assert "<link rel=\"stylesheet\"" not in html
    assert "http://" not in html.replace("http://www.w3.org", "")


def test_html_has_every_required_section():
    html = render_html(_data(), CFG)
    for heading in ("Run", "Delta", "Shortlist", "Waitlist", "Gated", "Needs manual review", "Methodology"):
        assert heading in html


def test_html_shows_cap_arithmetic():
    html = render_html(_data(), CFG)
    assert "20%" in html
    assert "of 6" in html


def test_html_lists_shortlist_names_and_scores():
    html = render_html(_data(), CFG)
    assert "Cand1 Test" in html
    assert "82" in html


def test_html_shows_gate_reason_quotes():
    html = render_html(_data(), CFG)
    assert "G1 reason for 4" in html


def test_html_shows_flag_chips():
    html = render_html(_data(), CFG)
    assert "no LinkedIn" in html


def test_html_shows_per_criterion_quotes_in_detail():
    html = render_html(_data(), CFG)
    assert "quote for production_ownership" in html


def test_html_shows_needs_review_reason():
    html = render_html(_data(), CFG)
    assert "unparseable" in html


def test_html_includes_calibration_note_and_rubric_version():
    html = render_html(_data(), CFG)
    assert "no reordering needed" in html
    assert f"rubric v{CFG.rubric_version}" in html.lower() or f"v{CFG.rubric_version}" in html


def test_html_escapes_candidate_supplied_text():
    data = _data()
    data.verdicts[1]["fit"]["summary"] = "<script>alert('xss')</script>"
    html = render_html(data, CFG)
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_html_never_contains_demographic_fields():
    html = render_html(_data(), CFG)
    for banned in ("gender", "nationality", "date of birth", "age:"):
        assert banned not in html.lower()


def test_html_avoids_hire_no_hire_language():
    html = render_html(_data(), CFG).lower()
    assert "no-hire" not in html
    assert "do not hire" not in html


def test_html_links_to_trakstar():
    html = render_html(_data(), CFG)
    assert "anduin.hire.trakstar.com" in html


# --- Markdown ---------------------------------------------------------------

def test_markdown_is_short_and_has_the_numbers():
    md = render_markdown(_data(), CFG)
    assert len(md.splitlines()) <= 45
    assert "Pool: 6" in md
    assert "Cap: 1" in md


def test_markdown_lists_delta_with_names_and_scores():
    md = render_markdown(_data(), CFG)
    assert "Cand1 Test" in md
    assert "82" in md


def test_markdown_names_newly_gated_with_gate():
    md = render_markdown(_data(), CFG)
    assert "Cand4 Test" in md
    assert "G1" in md


def test_markdown_reports_no_changes_when_delta_is_empty():
    data = _data()
    data.delta.update({"new": [], "newly_accepted": [], "newly_gated": [], "no_slot": []})
    md = render_markdown(data, CFG)
    assert "no changes" in md.lower()


# --- CSV --------------------------------------------------------------------

def test_csv_rows_have_all_columns():
    rows = rows_for_csv(_data(), CFG, statuses=("accepted", "waitlist", "gated"))
    assert rows
    row = rows[0]
    for column in ("id", "name", "email", "status", "gate", "final", "penalties", "flags", "trakstar_url"):
        assert column in row
    for key in CFG.criterion_keys():
        assert key in row


def test_csv_shortlist_filter_returns_only_accepted():
    rows = rows_for_csv(_data(), CFG, statuses=("accepted",))
    assert [r["id"] for r in rows] == [1]


def test_csv_has_no_demographic_columns():
    rows = rows_for_csv(_data(), CFG, statuses=("accepted", "waitlist", "gated"))
    for banned in ("gender", "nationality", "dob", "age", "location", "city"):
        assert banned not in rows[0]


# --- write_all --------------------------------------------------------------

def test_write_all_creates_every_artifact(tmp_path):
    paths = Paths(root=tmp_path, opening_id="704353")
    paths.ensure()
    written = write_all(_data(), CFG, paths)

    for key in ("html", "markdown", "shortlist_csv", "all_csv", "latest_html", "latest_markdown"):
        assert written[key].exists(), key
    assert written["html"].name.startswith("2026-08-28")
    assert written["latest_html"].name == "latest.html"


def test_write_all_latest_matches_run_file(tmp_path):
    paths = Paths(root=tmp_path, opening_id="704353")
    paths.ensure()
    written = write_all(_data(), CFG, paths)
    assert written["latest_html"].read_text() == written["html"].read_text()


def test_write_all_csv_is_parseable(tmp_path):
    paths = Paths(root=tmp_path, opening_id="704353")
    paths.ensure()
    written = write_all(_data(), CFG, paths)
    with written["shortlist_csv"].open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    assert rows[0]["name"] == "Cand1 Test"
