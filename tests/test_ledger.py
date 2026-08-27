import json

import pytest

from screen.ledger import LedgerEntry, load_ledger, save_ledger


def entry(cid, status="waitlist", final=50.0):
    return LedgerEntry(
        candidate_id=cid,
        status=status,
        gate=None,
        final=final,
        first_seen_run="run-1",
        status_changed_run="run-1",
        pdf_sha256="abc",
        trakstar_updated_date="2026-08-01T10:00:00Z",
        human_override=None,
    )


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "ledger.json"
    save_ledger({1: entry(1, "accepted", 80.0)}, path)
    loaded = load_ledger(path)
    assert loaded[1].status == "accepted"
    assert loaded[1].final == 80.0
    assert loaded[1].candidate_id == 1


def test_load_missing_file_returns_empty(tmp_path):
    assert load_ledger(tmp_path / "absent.json") == {}


def test_save_writes_backup_of_previous_version(tmp_path):
    path = tmp_path / "ledger.json"
    save_ledger({1: entry(1, "waitlist")}, path)
    save_ledger({1: entry(1, "accepted")}, path)
    backup = json.loads((tmp_path / "ledger.json.bak").read_text())
    assert backup[0]["status"] == "waitlist"
    assert load_ledger(path)[1].status == "accepted"


def test_load_corrupt_ledger_raises_rather_than_losing_decisions(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text("{not json")
    with pytest.raises(ValueError, match="corrupt"):
        load_ledger(path)


def test_invalid_status_is_rejected(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps([{**entry(1).__dict__, "status": "hired-ish"}]))
    with pytest.raises(ValueError, match="status"):
        load_ledger(path)
