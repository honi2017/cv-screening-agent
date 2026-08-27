from pathlib import Path

import pytest

from screen.config import load_role
from screen.verdict import (
    VerdictError,
    fit_total,
    validate_verdict,
    verdict_key,
    write_verdict,
    load_verdict,
)

CFG = load_role(Path(__file__).resolve().parents[1] / "roles" / "fde")

MD = """## Experience
### Staff Engineer, Northwind Data (2019-03 - Present)
- Owned the client integration platform end to end: 40 enterprise tenants.
- Led discovery workshops with law firms to scope data migrations.
- Rolled out Okta and Azure AD SAML SSO for 18 clients.
- Built internal LLM tooling that drafts integration mappings.
"""


def _raw(**overrides):
    scores = {
        "production_ownership": {"score": 20, "quote": "Owned the client integration platform end to end", "rationale": "r"},
        "integration_breadth": {"score": 15, "quote": "Rolled out Okta and Azure AD SAML SSO for 18 clients", "rationale": "r"},
        "client_solutioning": {"score": 16, "quote": "Led discovery workshops with law firms", "rationale": "r"},
        "communication_product": {"score": 10, "quote": "scope data migrations", "rationale": "r"},
        "domain": {"score": 5, "quote": "law firms", "rationale": "r"},
        "ai_tooling": {"score": 4, "quote": "Built internal LLM tooling", "rationale": "r"},
        "distributed_collab": {"score": 0, "quote": None, "rationale": "no evidence"},
    }
    raw = {
        "candidate_id": 1,
        "redflag": {"flags": [], "years_experience_estimate": {"value": 7, "confidence": "high"}},
        "fit": {"scores": scores, "bonus": {"points": 0, "justification": None}, "summary": "s"},
    }
    raw.update(overrides)
    return raw


def test_valid_verdict_passes_and_totals():
    v = validate_verdict(_raw(), CFG, MD)
    assert fit_total(v) == 70
    assert v["quote_warnings"] == []


def test_missing_criterion_is_an_error():
    raw = _raw()
    del raw["fit"]["scores"]["domain"]
    with pytest.raises(VerdictError) as exc:
        validate_verdict(raw, CFG, MD)
    assert any("domain" in e for e in exc.value.errors)


def test_unknown_criterion_is_an_error():
    raw = _raw()
    raw["fit"]["scores"]["vibes"] = {"score": 5, "quote": "x", "rationale": "r"}
    with pytest.raises(VerdictError) as exc:
        validate_verdict(raw, CFG, MD)
    assert any("vibes" in e for e in exc.value.errors)


def test_score_above_max_is_an_error():
    raw = _raw()
    raw["fit"]["scores"]["domain"]["score"] = 99
    with pytest.raises(VerdictError) as exc:
        validate_verdict(raw, CFG, MD)
    assert any("domain" in e and "max" in e for e in exc.value.errors)


def test_negative_score_is_an_error():
    raw = _raw()
    raw["fit"]["scores"]["domain"]["score"] = -1
    with pytest.raises(VerdictError):
        validate_verdict(raw, CFG, MD)


def test_bonus_above_cap_is_an_error():
    raw = _raw()
    raw["fit"]["bonus"] = {"points": 50, "justification": "wow"}
    with pytest.raises(VerdictError) as exc:
        validate_verdict(raw, CFG, MD)
    assert any("bonus" in e for e in exc.value.errors)


def test_bonus_without_justification_is_an_error():
    raw = _raw()
    raw["fit"]["bonus"] = {"points": 3, "justification": ""}
    with pytest.raises(VerdictError) as exc:
        validate_verdict(raw, CFG, MD)
    assert any("justification" in e for e in exc.value.errors)


def test_fabricated_quote_zeroes_score_and_warns():
    raw = _raw()
    raw["fit"]["scores"]["domain"]["quote"] = "Directed the Mars colonisation programme"
    v = validate_verdict(raw, CFG, MD)
    assert v["fit"]["scores"]["domain"]["score"] == 0
    assert any("domain" in w for w in v["quote_warnings"])
    assert fit_total(v) == 65


def test_nonzero_score_without_quote_zeroes_and_warns():
    raw = _raw()
    raw["fit"]["scores"]["domain"] = {"score": 5, "quote": None, "rationale": "r"}
    v = validate_verdict(raw, CFG, MD)
    assert v["fit"]["scores"]["domain"]["score"] == 0
    assert any("domain" in w for w in v["quote_warnings"])


def test_zero_score_without_quote_is_fine():
    v = validate_verdict(_raw(), CFG, MD)
    assert v["fit"]["scores"]["distributed_collab"]["score"] == 0
    assert not any("distributed_collab" in w for w in v["quote_warnings"])


def test_flag_without_quote_is_an_error():
    raw = _raw()
    raw["redflag"]["flags"] = [{"tier": 1, "kind": "placeholder", "explanation": "e"}]
    with pytest.raises(VerdictError) as exc:
        validate_verdict(raw, CFG, MD)
    assert any("quote" in e for e in exc.value.errors)


def test_flag_with_bad_tier_is_an_error():
    raw = _raw()
    raw["redflag"]["flags"] = [{"tier": 7, "kind": "x", "quote": "Owned the client", "explanation": "e"}]
    with pytest.raises(VerdictError) as exc:
        validate_verdict(raw, CFG, MD)
    assert any("tier" in e for e in exc.value.errors)


def test_flag_quote_not_in_cv_becomes_warning_not_error():
    raw = _raw()
    raw["redflag"]["flags"] = [
        {"tier": 2, "kind": "generic_bullets", "quote": "never appeared anywhere", "explanation": "e"}
    ]
    v = validate_verdict(raw, CFG, MD)
    assert v["redflag"]["flags"] == []
    assert any("flag" in w for w in v["quote_warnings"])


def test_missing_top_level_sections_is_an_error():
    with pytest.raises(VerdictError) as exc:
        validate_verdict({"candidate_id": 1}, CFG, MD)
    assert len(exc.value.errors) >= 2


def test_verdict_key_depends_on_all_three_inputs():
    pc = {"precheck_key": "abc"}
    base = verdict_key(MD, pc, 1)
    assert base == verdict_key(MD, pc, 1)
    assert base != verdict_key(MD + " changed", pc, 1)
    assert base != verdict_key(MD, {"precheck_key": "xyz"}, 1)
    assert base != verdict_key(MD, pc, 2)


def test_write_and_load_verdict_roundtrip(tmp_path):
    v = validate_verdict(_raw(), CFG, MD)
    path = tmp_path / "1.json"
    write_verdict(v, path)
    assert load_verdict(path)["candidate_id"] == 1
    assert load_verdict(tmp_path / "absent.json") is None


def test_load_verdict_returns_none_on_corrupt_file(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json")
    assert load_verdict(path) is None
