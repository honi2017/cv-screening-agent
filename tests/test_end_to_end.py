"""End-to-end run over the fixture pool, with the judge stubbed.

This is the test that proves the pieces fit: seven CVs in, a report and ledger
out, with the placeholder CV, the hidden-text CV, and the shared-template pair
eliminated and the strongest candidate shortlisted.
"""

import csv
import json
import sys
from pathlib import Path

import pytest

from screen.cli import main
from screen.config import load_role
from screen.ledger import load_ledger
from screen.paths import Paths
from screen.verdict import validate_verdict, verdict_key, write_verdict

sys.path.insert(0, str(Path(__file__).parent))
from fixtures.make_fixtures import build_all  # noqa: E402

ROLE = Path(__file__).resolve().parents[1] / "roles" / "fde"
CFG = load_role(ROLE)
OPENING = "704353"

# Which fixture each applicant submitted, and how strong the stub judge finds them.
POOL = [
    ("Alex Morgan", "clean", 0.85),
    ("Sam Rivera", "four_year", 0.55),
    ("Jordan Blake", "template_a", 0.40),
    ("Riley Chen", "template_b", 0.40),
    ("Pat Placeholder", "placeholder", 0.30),
    ("Casey Hidden", "hidden_text", 0.80),
    ("Dana Scan", "scanned", 0.00),
]


@pytest.fixture
def project(tmp_path):
    pdfs = build_all(tmp_path / "pdfs")
    src = tmp_path / "cvs"
    src.mkdir()
    for name, key, _ in POOL:
        (src / f"{name}.pdf").write_bytes(pdfs[key].read_bytes())

    csv_path = tmp_path / "export.csv"
    rows = ["Name,Email,LinkedIn,Location"]
    for name, key, _ in POOL:
        # Everyone has a LinkedIn except Pat, so the -8 penalty is exercised.
        linkedin = "" if name.startswith("Pat") else f"https://linkedin.com/in/{name.split()[0].lower()}"
        rows.append(f"{name},{name.split()[0].lower()}@example.com,{linkedin},\"Boston, MA\"")
    csv_path.write_text("\n".join(rows) + "\n")

    return tmp_path / "proj", src, csv_path


def _args(root, *rest):
    return ["--root", str(root), "--opening", OPENING, "--role", str(ROLE), *rest]


def _stub_judge(root, ids):
    """Score each candidate proportionally to their POOL strength."""
    paths = Paths(root=root, opening_id=OPENING)
    candidates = {int(c["id"]): c for c in json.loads(paths.candidates_json.read_text())}
    strength_by_name = {name: strength for name, _, strength in POOL}

    for cid in ids:
        candidate = candidates[cid]
        name = f"{candidate['first_name']} {candidate['last_name']}".strip()
        strength = strength_by_name.get(name, 0.5)

        precheck = json.loads((paths.prechecks / f"{cid}.json").read_text())
        redacted = (paths.redacted / f"{cid}.md").read_text()
        quote = next(
            (l.strip("-* ").strip() for l in redacted.splitlines() if l.strip().startswith(("-", "*"))),
            None,
        )
        scores = {}
        for c in CFG.criteria:
            score = round(c.max * strength) if quote else 0
            scores[c.key] = {
                "score": score,
                "quote": quote if score > 0 else None,
                "rationale": "stub",
            }
        raw = {
            "candidate_id": cid,
            "redflag": {"flags": [], "years_experience_estimate": {"value": 8, "confidence": "high"}},
            "fit": {"scores": scores, "bonus": {"points": 0, "justification": None}, "summary": f"{name} stub summary."},
        }
        verdict = validate_verdict(raw, CFG, redacted)
        verdict["verdict_key"] = verdict_key(redacted, precheck, CFG.rubric_version)
        write_verdict(verdict, paths.verdicts / f"{cid}.json")


def _full_run(root, src, csv_path, run_id="2026-08-28T0800"):
    assert main(_args(root, "fetch", "--source", "folder", "--path", str(src), "--csv", str(csv_path))) == 0
    assert main(_args(root, "parse")) == 0
    assert main(_args(root, "precheck", "--today", "2026-08")) == 0

    pending_file = root / "pending.json"
    main(_args(root, "pending", "--out", str(pending_file)))
    pending = json.loads(pending_file.read_text())["pending"]
    _stub_judge(root, pending)

    assert main(_args(root, "rank", "--finalize", "--run-id", run_id)) == 0
    assert main(_args(root, "report", "--run-id", run_id)) == 0
    return Paths(root=root, opening_id=OPENING)


def _name_status(paths):
    candidates = {int(c["id"]): c for c in json.loads(paths.candidates_json.read_text())}
    ledger = load_ledger(paths.ledger_json)
    return {
        f"{candidates[cid]['first_name']} {candidates[cid]['last_name']}".strip(): entry.status
        for cid, entry in ledger.items()
    }


def test_end_to_end_produces_report_and_ledger(project):
    root, src, csv_path = project
    paths = _full_run(root, src, csv_path)

    assert (paths.report / "2026-08-28T0800.html").exists()
    assert (paths.report / "latest.html").exists()
    assert (paths.report / "latest.md").exists()
    assert paths.ledger_json.exists()
    assert list(paths.runs.glob("*.json"))


def test_placeholder_cv_is_eliminated(project):
    root, src, csv_path = project
    statuses = _name_status(_full_run(root, src, csv_path))
    assert statuses["Pat Placeholder"] == "gated"


def test_hidden_text_cv_is_eliminated_despite_strong_content(project):
    root, src, csv_path = project
    statuses = _name_status(_full_run(root, src, csv_path))
    # This CV has the strongest text in the pool but stuffs hidden keywords.
    assert statuses["Casey Hidden"] == "gated"


def test_shared_template_pair_is_eliminated(project):
    root, src, csv_path = project
    statuses = _name_status(_full_run(root, src, csv_path))
    assert statuses["Jordan Blake"] == "gated"
    assert statuses["Riley Chen"] == "gated"


def test_scanned_cv_needs_manual_review_not_rejection(project):
    root, src, csv_path = project
    statuses = _name_status(_full_run(root, src, csv_path))
    assert statuses["Dana Scan"] == "needs_review"


def test_cap_is_twenty_percent_of_the_whole_pool(project):
    root, src, csv_path = project
    paths = _full_run(root, src, csv_path)
    record = json.loads(next(paths.runs.glob("*.cut.json")).read_text())
    assert record["cut"]["pool_size"] == 7
    assert record["cut"]["cap"] == 1


def test_strongest_clean_candidate_is_shortlisted(project):
    root, src, csv_path = project
    statuses = _name_status(_full_run(root, src, csv_path))
    assert statuses["Alex Morgan"] == "accepted"


def test_report_html_never_contains_pii_of_gated_candidates_cv_text(project):
    root, src, csv_path = project
    paths = _full_run(root, src, csv_path)
    html = (paths.report / "latest.html").read_text()
    # Names appear (the report re-attaches identity) but CV emails must not.
    assert "Alex Morgan" in html
    assert "alex.morgan@example.com" not in html


def test_redacted_markdown_contains_no_candidate_email(project):
    root, src, csv_path = project
    paths = _full_run(root, src, csv_path)
    from screen.redact import assert_clean

    for md in paths.redacted.glob("*.md"):
        assert assert_clean(md.read_text()) == [], md.name


def test_second_run_judges_nobody_new(project):
    root, src, csv_path = project
    _full_run(root, src, csv_path)

    pending_file = root / "pending2.json"
    assert main(_args(root, "fetch", "--source", "folder", "--path", str(src), "--csv", str(csv_path))) == 0
    assert main(_args(root, "parse")) == 0
    assert main(_args(root, "precheck", "--today", "2026-08")) == 0
    assert main(_args(root, "pending", "--out", str(pending_file))) == 0
    assert json.loads(pending_file.read_text())["pending"] == []


def test_accepted_candidate_survives_a_stronger_second_wave(project, tmp_path):
    root, src, csv_path = project
    _full_run(root, src, csv_path)
    before = _name_status(Paths(root=root, opening_id=OPENING))
    assert before["Alex Morgan"] == "accepted"

    # Three strong newcomers arrive; the cap grows to 2 but Alex must stay in.
    pdfs = build_all(tmp_path / "pdfs2")
    for extra in ("Blake Strong", "Quinn Strong", "Reese Strong"):
        (src / f"{extra}.pdf").write_bytes(pdfs["clean"].read_bytes())
    rows = csv_path.read_text().rstrip().splitlines()
    for extra in ("Blake Strong", "Quinn Strong", "Reese Strong"):
        rows.append(f"{extra},{extra.split()[0].lower()}@example.com,https://linkedin.com/in/{extra.split()[0].lower()},\"Boston, MA\"")
    csv_path.write_text("\n".join(rows) + "\n")

    main(_args(root, "fetch", "--source", "folder", "--path", str(src), "--csv", str(csv_path)))
    main(_args(root, "parse"))
    main(_args(root, "precheck", "--today", "2026-08"))
    pending_file = root / "pending3.json"
    main(_args(root, "pending", "--out", str(pending_file)))
    _stub_judge(root, json.loads(pending_file.read_text())["pending"])
    main(_args(root, "rank", "--finalize", "--run-id", "2026-08-29T0800"))
    main(_args(root, "report", "--run-id", "2026-08-29T0800"))

    after = _name_status(Paths(root=root, opening_id=OPENING))
    assert after["Alex Morgan"] == "accepted"


def test_csv_shortlist_matches_the_ledger(project):
    root, src, csv_path = project
    paths = _full_run(root, src, csv_path)
    with (paths.report / "2026-08-28T0800-shortlist.csv").open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    accepted = [n for n, s in _name_status(paths).items() if s == "accepted"]
    assert sorted(r["name"] for r in rows) == sorted(accepted)


def test_needs_manual_review_section_names_the_candidate_not_by_id(project):
    """Guards a real regression: needs_review_reasons round-trips through
    run-state JSON on disk, whose keys are always strings. If the report
    stage forgets to coerce them back to int before looking candidates up,
    every needs-review row falls back to "Candidate <id>" -- the exact
    section a human must act on ends up naming nobody.
    """
    root, src, csv_path = project
    paths = _full_run(root, src, csv_path)
    html = (paths.report / "latest.html").read_text()

    assert "Dana Scan" in html
    assert "Candidate " not in html
