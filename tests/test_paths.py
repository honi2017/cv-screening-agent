from pathlib import Path

from screen.paths import Paths


def test_paths_are_namespaced_by_opening(tmp_path):
    p = Paths(root=tmp_path, opening_id="704353")
    assert p.data == tmp_path / "data" / "704353"
    assert p.resumes == tmp_path / "data" / "704353" / "resumes"
    assert p.candidates_json == tmp_path / "data" / "704353" / "candidates.json"
    assert p.ledger_json == tmp_path / "state" / "ledger.json"


def test_ensure_creates_all_directories(tmp_path):
    p = Paths(root=tmp_path, opening_id="704353")
    p.ensure()
    for d in (p.resumes, p.parsed, p.redacted, p.prechecks, p.verdicts, p.runs, p.logs, p.report):
        assert d.is_dir()
