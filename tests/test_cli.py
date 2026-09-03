import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

from screen.cli import main, run_id_now
from screen.paths import Paths

sys.path.insert(0, str(Path(__file__).parent))
from fixtures.make_fixtures import build_all  # noqa: E402

ROLE = str(Path(__file__).resolve().parents[1] / "roles" / "fde")


@pytest.fixture(scope="module")
def pdfs(tmp_path_factory):
    return build_all(tmp_path_factory.mktemp("pdfs"))


@pytest.fixture
def project(tmp_path, pdfs):
    """A folder-sourced project with five CVs, fetched and parsed."""
    src = tmp_path / "cvs"
    src.mkdir()
    for name, key in [
        ("Alex Morgan", "clean"),
        ("Pat Placeholder", "placeholder"),
        ("Jordan Blake", "template_a"),
        ("Riley Chen", "template_b"),
        ("Sam Rivera", "four_year"),
    ]:
        (src / f"{name}.pdf").write_bytes(pdfs[key].read_bytes())
    root = tmp_path / "proj"
    return root, src


def _args(root, *rest):
    return ["--root", str(root), "--opening", "704353", "--role", ROLE, *rest]


def test_run_id_format():
    assert run_id_now(datetime(2026, 8, 28, 8, 0)) == "2026-08-28T0800"


def test_fetch_folder_then_precheck(project, capsys):
    root, src = project
    assert main(_args(root, "fetch", "--source", "folder", "--path", str(src))) == 0
    assert main(_args(root, "parse")) == 0
    assert main(_args(root, "precheck", "--today", "2026-08")) == 0
    paths = Paths(root=root, opening_id="704353")
    assert len(list(paths.prechecks.glob("*.json"))) >= 4
    assert len(list(paths.redacted.glob("*.md"))) >= 4


def test_pending_lists_everyone_before_judging(project):
    root, src = project
    main(_args(root, "fetch", "--source", "folder", "--path", str(src)))
    main(_args(root, "parse"))
    main(_args(root, "precheck", "--today", "2026-08"))

    out_file = root / "pending.json"
    assert main(_args(root, "pending", "--out", str(out_file))) == 0
    payload = json.loads(out_file.read_text())
    assert len(payload["pending"]) >= 4
    assert payload["cached"] == []
    assert payload["rubric_change"] is False


def test_pending_is_empty_after_verdicts_written(project):
    root, src = project
    main(_args(root, "fetch", "--source", "folder", "--path", str(src)))
    main(_args(root, "parse"))
    main(_args(root, "precheck", "--today", "2026-08"))

    out_file = root / "pending.json"
    main(_args(root, "pending", "--out", str(out_file)))
    pending = json.loads(out_file.read_text())["pending"]

    _write_fake_verdicts(root, pending)

    main(_args(root, "pending", "--out", str(out_file)))
    payload = json.loads(out_file.read_text())
    assert payload["pending"] == []
    assert sorted(payload["cached"]) == sorted(pending)


def _write_fake_verdicts(root, ids, score=8.0):
    """Stand in for the judge subagents so the CLI can be tested end to end."""
    from screen.config import load_role
    from screen.verdict import validate_verdict, verdict_key, write_verdict

    cfg = load_role(Path(ROLE))
    paths = Paths(root=root, opening_id="704353")
    for cid in ids:
        precheck = json.loads((paths.prechecks / f"{cid}.json").read_text())
        redacted = (paths.redacted / f"{cid}.md").read_text()
        first_line = next(
            (line.strip("-* ").strip() for line in redacted.splitlines() if line.strip().startswith(("-", "*"))),
            "experience",
        )
        raw = {
            "candidate_id": cid,
            "redflag": {"flags": [], "years_experience_estimate": {"value": 7, "confidence": "high"}},
            "fit": {
                "scores": {
                    k: {"score": min(score, cfg.criterion(k).max), "quote": first_line, "rationale": "r"}
                    for k in cfg.criterion_keys()
                },
                "bonus": {"points": 0, "justification": None},
                "summary": f"Candidate {cid} summary.",
            },
        }
        verdict = validate_verdict(raw, cfg, redacted)
        verdict["verdict_key"] = verdict_key(redacted, precheck, cfg.rubric_version)
        write_verdict(verdict, paths.verdicts / f"{cid}.json")


def test_rank_prepare_emits_calibration_window(project):
    root, src = project
    main(_args(root, "fetch", "--source", "folder", "--path", str(src)))
    main(_args(root, "parse"))
    main(_args(root, "precheck", "--today", "2026-08"))
    out_file = root / "pending.json"
    main(_args(root, "pending", "--out", str(out_file)))
    _write_fake_verdicts(root, json.loads(out_file.read_text())["pending"])

    prep = root / "prepare.json"
    assert main(_args(root, "rank", "--prepare", "--out", str(prep))) == 0
    payload = json.loads(prep.read_text())
    assert "run_id" in payload
    assert "calibration_window" in payload
    assert isinstance(payload["candidates"], list)


def test_rank_finalize_then_report_writes_artifacts(project):
    root, src = project
    main(_args(root, "fetch", "--source", "folder", "--path", str(src)))
    main(_args(root, "parse"))
    main(_args(root, "precheck", "--today", "2026-08"))
    out_file = root / "pending.json"
    main(_args(root, "pending", "--out", str(out_file)))
    _write_fake_verdicts(root, json.loads(out_file.read_text())["pending"])

    prep = root / "prepare.json"
    main(_args(root, "rank", "--prepare", "--out", str(prep)))
    run_id = json.loads(prep.read_text())["run_id"]

    assert main(_args(root, "rank", "--finalize", "--run-id", run_id)) == 0
    assert main(_args(root, "report", "--run-id", run_id)) == 0

    paths = Paths(root=root, opening_id="704353")
    assert (paths.report / "latest.html").exists()
    assert (paths.report / "latest.md").exists()
    assert paths.ledger_json.exists()
    assert list(paths.runs.glob("*.json"))


def test_gated_candidates_appear_in_the_ledger(project):
    root, src = project
    main(_args(root, "fetch", "--source", "folder", "--path", str(src)))
    main(_args(root, "parse"))
    main(_args(root, "precheck", "--today", "2026-08"))
    out_file = root / "pending.json"
    main(_args(root, "pending", "--out", str(out_file)))
    _write_fake_verdicts(root, json.loads(out_file.read_text())["pending"])
    prep = root / "prepare.json"
    main(_args(root, "rank", "--prepare", "--out", str(prep)))
    run_id = json.loads(prep.read_text())["run_id"]
    main(_args(root, "rank", "--finalize", "--run-id", run_id))

    from screen.ledger import load_ledger

    ledger = load_ledger(Paths(root=root, opening_id="704353").ledger_json)
    statuses = {e.status for e in ledger.values()}
    # The placeholder CV and the shared-template pair must be eliminated.
    assert "gated" in statuses


def test_rubric_change_requires_full_flag(project):
    root, src = project
    main(_args(root, "fetch", "--source", "folder", "--path", str(src)))
    main(_args(root, "parse"))
    main(_args(root, "precheck", "--today", "2026-08"))
    out_file = root / "pending.json"
    main(_args(root, "pending", "--out", str(out_file)))
    ids = json.loads(out_file.read_text())["pending"]
    _write_fake_verdicts(root, ids)

    # Bump the rubric version in a copied role dir.
    role_copy = root / "role"
    role_copy.mkdir(parents=True, exist_ok=True)
    original = json.loads((Path(ROLE) / "role.json").read_text())
    original["rubric_version"] = 99
    (role_copy / "role.json").write_text(json.dumps(original))
    for name in ("redflag_prompt.md", "fit_prompt.md"):
        source = Path(ROLE) / name
        if source.exists():
            (role_copy / name).write_text(source.read_text())

    args = ["--root", str(root), "--opening", "704353", "--role", str(role_copy)]
    assert main([*args, "pending", "--out", str(out_file)]) == 3

    payload = json.loads(out_file.read_text())
    assert payload["rubric_change"] is True
    assert sorted(payload["pending"]) == sorted(ids)

    assert main([*args, "pending", "--full", "--out", str(out_file)]) == 0


def test_rank_finalize_rebaseline_flag_and_unseated_reach_the_run_record(project):
    """Plumbing test: `rank --finalize --rebaseline` must record `rebaseline:
    true` and an `unseated` list in the `.cut.json` run record, and a plain
    `rank --finalize` (the default) must record `rebaseline: false` with an
    empty `unseated` -- the audit trail for whichever mode actually ran.
    """
    root, src = project
    main(_args(root, "fetch", "--source", "folder", "--path", str(src)))
    main(_args(root, "parse"))
    main(_args(root, "precheck", "--today", "2026-08"))
    out_file = root / "pending.json"
    main(_args(root, "pending", "--out", str(out_file)))
    _write_fake_verdicts(root, json.loads(out_file.read_text())["pending"])

    prep = root / "prepare.json"
    main(_args(root, "rank", "--prepare", "--out", str(prep)))
    run_id = json.loads(prep.read_text())["run_id"]

    default_out = root / "default.json"
    assert main(_args(root, "rank", "--finalize", "--run-id", run_id, "--out", str(default_out))) == 0
    default_state = json.loads(default_out.read_text())
    assert default_state["cut"]["rebaseline"] is False
    assert default_state["cut"]["unseated"] == []

    rebaseline_out = root / "rebaseline.json"
    assert main(
        _args(root, "rank", "--finalize", "--rebaseline", "--run-id", run_id + "b", "--out", str(rebaseline_out))
    ) == 0
    rebaseline_state = json.loads(rebaseline_out.read_text())
    assert rebaseline_state["cut"]["rebaseline"] is True
    assert isinstance(rebaseline_state["cut"]["unseated"], list)


def test_bad_source_is_usage_error(project):
    root, src = project
    assert main(_args(root, "fetch", "--source", "folder")) == 2


def test_unknown_command_is_usage_error(project, capsys):
    root, _ = project
    with pytest.raises(SystemExit):
        main(_args(root, "frobnicate"))


def test_run_command_stops_before_judging(project):
    root, src = project
    assert main(_args(root, "run", "--source", "folder", "--path", str(src), "--today", "2026-08")) == 0
    paths = Paths(root=root, opening_id="704353")
    assert list(paths.prechecks.glob("*.json"))
    assert not list(paths.verdicts.glob("*.json"))
