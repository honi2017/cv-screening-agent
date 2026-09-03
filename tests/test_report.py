import csv
import re
from pathlib import Path

from screen.config import load_role
from screen.ledger import LedgerEntry
from screen.paths import Paths
from screen.rank import Assessment, CutResult
from screen.report import (
    ReportInput,
    _detail_html,
    render_html,
    render_markdown,
    resume_url,
    rows_for_csv,
    trakstar_url,
    write_all,
)
import screen.report as report_mod

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
                **(
                    {"resume": {"file_name": "cv.pdf", "file_url": f"https://example.invalid/resume/{i}"}}
                    if i == 1
                    else {}
                ),
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


def test_shortlist_rank_numbers_ascend_as_scores_descend():
    """The rank column must agree with the score column.

    The report numbers rows in the order CutResult hands them over, so if that
    order is not by score the rank column silently lies -- which is exactly what
    shipped, showing rank 1 at 75 points above rank 2 at 85.
    """
    data = _data()
    # three accepted candidates whose id order disagrees with their score order
    data.cut.accepted[:] = [2, 1, 3]
    html = render_html(data, CFG)
    shortlist = html.split(">Shortlist<", 1)[1].split("<h2", 1)[0]
    seen = re.findall(r"<td>(\d+)</td><td>[^<]*Cand(\d+) Test", shortlist)
    ranks = [int(r) for r, _ in seen]
    scores = [data.assessments[int(c)].final for _, c in seen]
    assert ranks == sorted(ranks), f"rank column not ascending: {ranks}"
    assert scores == sorted(scores, reverse=True), (
        f"ranks {ranks} do not follow descending scores {scores}"
    )


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


def test_every_link_opens_in_a_new_tab_with_noopener():
    """Links must open in a new tab and must not hand the opener window over.

    A reviewer works down a long list; a same-tab navigation loses their place.
    `noopener` denies the opened page a window.opener reference back to the
    report, and `noreferrer` keeps this page's location out of the referrer --
    the report is a local file containing applicant PII and the resume link is
    an unauthenticated token URL, so neither should leak onward.
    """
    html = render_html(_data(), CFG)
    anchors = re.findall(r"<a\s[^>]*>(?:Trakstar|Resume)</a>", html)
    assert anchors, "expected Trakstar/Resume links in the report"
    for a in anchors:
        assert 'target="_blank"' in a, a
        assert "noopener" in a, a
        assert "noreferrer" in a, a


def test_trakstar_url_matches_the_confirmed_live_route_exactly():
    """Pin the deep-link route against a URL read off the live Trakstar UI.

    An earlier guessed route (`#candidates/{id}?opening={id}`) silently resolved
    to the opening's candidate list instead of the individual record, so the
    report shipped 70 links that all went to the wrong page. The candidate is a
    `view:<id>` segment appended to the opening's LIST route -- not a route of
    its own -- and the trailing slash is part of it. This asserts the whole
    string so any drift fails loudly rather than degrading to a list view.
    """
    assert trakstar_url("704353", 71588398) == (
        "https://anduin.hire.trakstar.com/app/#candidates/list"
        "/selected_openings=704353&orderBy=date_created&order=desc"
        "/view:71588398/"
    )


def test_trakstar_url_contains_candidate_and_opening_id():
    url = trakstar_url("704353", 42)
    assert "42" in url
    assert "704353" in url


def test_resume_url_returns_file_url_when_present():
    assert resume_url({"resume": {"file_url": "https://example.invalid/r/1"}}) == (
        "https://example.invalid/r/1"
    )


def test_resume_url_is_none_without_resume():
    assert resume_url({}) is None
    assert resume_url({"resume": {}}) is None
    assert resume_url({"resume": {"file_name": "cv.pdf"}}) is None


def test_candidate_with_resume_renders_both_links():
    html = render_html(_data(), CFG)
    shortlist_html = html.split("<h2>Shortlist</h2>", 1)[1].split("<h2>Waitlist</h2>", 1)[0]
    assert ">Resume<" in shortlist_html
    assert ">Trakstar<" in shortlist_html


def test_candidate_without_resume_renders_only_trakstar_link():
    html = render_html(_data(), CFG)
    waitlist_html = html.split("<h2>Waitlist</h2>", 1)[1].split("<h2>Gated</h2>", 1)[0]
    rows = _rows(waitlist_html)
    cand2_row = next(r for r in rows if "Cand2 Test" in r)
    assert ">Trakstar<" in cand2_row
    assert ">Resume<" not in cand2_row
    assert '<a href=""' not in cand2_row


def test_candidate_with_linkedin_renders_anchor():
    """The -20 no-LinkedIn penalty exists so a human clicks through to verify
    the profile is real; the report must actually offer that click.
    """
    html = render_html(_data(), CFG)
    shortlist_html = html.split("<h2>Shortlist</h2>", 1)[1].split("<h2>Waitlist</h2>", 1)[0]
    m = re.search(r'<a\s[^>]*href="([^"]+)"[^>]*>LinkedIn</a>', shortlist_html)
    assert m, "expected a LinkedIn anchor in the shortlist"
    assert m.group(1).startswith("https://")


def test_candidate_without_linkedin_renders_no_anchor():
    """No profile means no anchor -- the `no LinkedIn` flag chip already
    communicates the absence, and a dead anchor is worse than none.
    """
    data = _data()
    data.prechecks[1]["linkedin"] = {
        "present": False, "source": "none", "url": None, "name_matches": None,
    }
    html = render_html(data, CFG)
    shortlist_html = html.split("<h2>Shortlist</h2>", 1)[1].split("<h2>Waitlist</h2>", 1)[0]
    # The evidence panel's `<dt>LinkedIn</dt>` label is expected to remain
    # (it says "not found"); only the clickable anchor must be absent.
    assert not re.search(r"<a\s[^>]*>LinkedIn</a>", shortlist_html)


def test_linkedin_anchor_has_new_tab_and_noopener_attributes():
    html = render_html(_data(), CFG)
    anchors = re.findall(r"<a\s[^>]*>LinkedIn</a>", html)
    assert anchors, "expected at least one LinkedIn anchor"
    for a in anchors:
        assert 'target="_blank"' in a, a
        assert "noopener" in a, a
        assert "noreferrer" in a, a


def test_evidence_panel_shows_linkedin_url():
    """The audit trail must record WHICH profile was checked, not just that
    one was found via a given source.
    """
    html = render_html(_data(), CFG)
    assert "https://linkedin.com/in/x" in html


# --- LinkedIn liveness in the evidence panel: positive-only ----------------
#
# "live" (an HTTP 200 -- LinkedIn confirms a real, publicly-visible profile)
# is genuine corroborating evidence and must be shown. "unknown" (999, 405,
# any other status, a network error, or a timeout) means LinkedIn's bot-wall
# answered, which is indistinguishable between a fabricated slug and a real
# profile that simply isn't public -- see screen.linkedin_check's module
# docstring. It carries no meaning either way, so it must render as nothing
# at all: showing anything for "unknown" would read as unearned doubt about
# a real person.


def test_evidence_panel_shows_confirmation_when_linkedin_live():
    data = _data()
    data.prechecks[1]["linkedin"]["liveness"] = "live"
    detail = _detail_html(data, 1, CFG)
    assert "profile confirmed publicly visible" in detail


def test_evidence_panel_shows_nothing_extra_when_linkedin_unknown():
    data = _data()
    data.prechecks[1]["linkedin"]["liveness"] = "unknown"
    detail = _detail_html(data, 1, CFG)
    assert "profile confirmed publicly visible" not in detail
    # The base LinkedIn line (source, URL, name match) must still render --
    # "unknown" suppresses only the extra confirmation clause, not the line.
    assert "<dt>LinkedIn</dt>" in detail


def test_evidence_panel_shows_nothing_extra_when_liveness_absent():
    # No liveness field at all (e.g. the config flag was off for this run)
    # must behave exactly like "unknown": no confirmation clause.
    detail = _detail_html(_data(), 1, CFG)
    assert "profile confirmed publicly visible" not in detail


def test_footer_mentions_link_behaviour():
    """The footer must explain what each link opens, and warn about the CV link.

    The resume URL is a tokenised link that serves the file without any
    authentication, so anyone holding it can read the CV. A reader who forwards
    this report needs to know that from the page itself.
    """
    html = render_html(_data(), CFG)
    footer = html.split("<footer>", 1)[1]
    assert "candidate's own record" in footer
    assert "Resume" in footer
    assert "without signing in" in footer


def _rows(html_fragment):
    """Split an HTML fragment into its top-level <tr>...</tr> chunks."""
    return re.findall(r"<tr[^>]*>.*?</tr>", html_fragment, re.S)


def test_evidence_panel_is_its_own_full_width_row():
    html = render_html(_data(), CFG)
    shortlist_html = html.split("<h2>Shortlist</h2>", 1)[1].split("<h2>Waitlist</h2>", 1)[0]

    header_count = shortlist_html.count("<th>")
    assert header_count > 0

    colspans = re.findall(r'<td colspan="(\d+)"', shortlist_html)
    assert colspans, "expected the evidence panel to live in a colspan cell"
    assert all(int(c) == header_count for c in colspans), (
        "colspan must equal the number of header columns so they can't drift apart"
    )


def test_name_cell_no_longer_embeds_detail_panel():
    html = render_html(_data(), CFG)
    rows = _rows(html)
    summary_idx = next(i for i, r in enumerate(rows) if "Cand1 Test" in r)
    summary_row = rows[summary_idx]
    detail_row = rows[summary_idx + 1]

    assert "<details>" not in summary_row
    assert "evidence" not in summary_row
    assert "<details>" in detail_row
    assert "detail-row" in detail_row


def test_detail_html_empty_when_no_verdict():
    data = _data()
    data.verdicts.pop(3)
    assert _detail_html(data, 3, CFG) == ""


def test_candidate_table_skips_empty_detail_row(monkeypatch):
    monkeypatch.setattr(report_mod, "_detail_html", lambda *a, **k: "")
    html = report_mod.render_html(_data(), CFG)
    assert not re.search(r'<tr class="[^"]*detail-row', html)
    # summary rows still render normally
    assert "Cand1 Test" in html


def test_waitlist_detail_rows_are_grey():
    html = render_html(_data(), CFG)
    waitlist_html = html.split("<h2>Waitlist</h2>", 1)[1].split("<h2>Gated</h2>", 1)[0]
    detail_rows = [r for r in _rows(waitlist_html) if "detail-row" in r]
    assert detail_rows, "expected at least one evidence row in the waitlist table"
    for row in detail_rows:
        assert re.match(r'<tr class="[^"]*\bgrey\b[^"]*"', row)


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


def test_delta_omits_the_no_slot_section():
    """`no_slot` must not be rendered in either output.

    It collects everyone clearing the quality floor once the cap is full, so
    with no floor or a low one it is the whole waitlist restated under a
    heading claiming they would have qualified -- on the real pool that was 46
    people scoring down to 57. The Waitlist section already lists them in rank
    order with their flags.
    """
    data = _data()
    data.delta["no_slot"] = [2, 3]
    html = render_html(data, CFG)
    md = render_markdown(data, CFG)
    assert "no slot" not in html.lower()
    assert "would have qualified" not in html.lower()
    assert "no slot" not in md.lower()
    assert "would have qualified" not in md.lower()


def test_delta_says_no_changes_when_only_no_slot_is_populated():
    """A run whose only "delta" is no_slot has nothing to report."""
    data = _data()
    data.delta.update(
        {"new": [], "newly_accepted": [], "newly_gated": [], "no_slot": [2, 3]}
    )
    assert "no changes" in render_html(data, CFG).lower()
    assert "no changes" in render_markdown(data, CFG).lower()


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
    for column in (
        "id", "name", "email", "status", "gate", "final", "penalties", "flags",
        "trakstar_url", "resume_url",
    ):
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


def test_write_all_csvs_carry_resume_url_column(tmp_path):
    paths = Paths(root=tmp_path, opening_id="704353")
    paths.ensure()
    written = write_all(_data(), CFG, paths)
    for key in ("shortlist_csv", "all_csv"):
        with written[key].open(newline="") as fh:
            reader = csv.DictReader(fh)
            assert "resume_url" in reader.fieldnames
