from pathlib import Path

import pytest

from screen.config import load_role

ROLE_DIR = Path(__file__).resolve().parents[1] / "roles" / "fde"


def test_load_role_reads_criteria_in_order():
    cfg = load_role(ROLE_DIR)
    assert [c.key for c in cfg.criteria] == [
        "production_ownership",
        "integration_breadth",
        "client_solutioning",
        "communication_product",
        "domain",
        "ai_tooling",
        "distributed_collab",
    ]


def test_max_fit_score_is_100():
    cfg = load_role(ROLE_DIR)
    assert cfg.max_fit_score() == 100


def test_scalars_load():
    cfg = load_role(ROLE_DIR)
    assert cfg.cap_fraction == 0.20
    assert cfg.quality_floor_delta == 5
    assert cfg.calibration_window == 5
    assert cfg.bonus_max == 5
    assert cfg.rubric_version == 3
    assert cfg.precheck_rules_version == 2
    assert cfg.gates["min_years"] == 4
    assert cfg.penalties["no_linkedin"] == 8


def test_criterion_lookup():
    cfg = load_role(ROLE_DIR)
    assert cfg.criterion("domain").max == 10
    with pytest.raises(KeyError):
        cfg.criterion("nope")


def test_rejects_criteria_not_summing_to_100(tmp_path):
    (tmp_path / "role.json").write_text(
        '{"position_title": "x", "rubric_version": 1, "precheck_rules_version": 1,'
        ' "cap_fraction": 0.2, "quality_floor_delta": 5, "calibration_window": 5,'
        ' "bonus_max": 5, "criteria": [{"key": "a", "label": "A", "max": 50}],'
        ' "anchors": {}, "gates": {}, "penalties": {}, "tiebreak_timezones": []}'
    )
    with pytest.raises(ValueError, match="must sum to 100"):
        load_role(tmp_path)
