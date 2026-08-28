from pathlib import Path

from screen.config import load_role
from screen.rank import apply_gates, assess, compute_penalties, tier2_count

CFG = load_role(Path(__file__).resolve().parents[1] / "roles" / "fde")


def precheck(**over):
    base = {
        "candidate_id": 1,
        "placeholders": [],
        "hidden_text": {"found": False, "spans": []},
        "intra_cv_duplicate_bullets": [],
        "pool_duplicate_bullets": [],
        "skills_count": 20,
        "template_metadata_signal": False,
        "years_experience": {"computed": 8.0, "confidence": "high", "ranges": []},
        "short_stints_last_5y": 0,
        "gap_over_12m": False,
        "linkedin": {"present": True, "source": "trakstar", "url": "u", "name_matches": True},
        "degree": {"present": True, "level": "BSc", "field": "Computer Science"},
        "location": {"us_evident": True, "non_us_explicit": False, "timezone_hint": "ET", "raw": None},
    }
    base.update(over)
    return base


def verdict(flags=(), scores=None, bonus=0.0, est=None):
    keys = CFG.criterion_keys()
    scores = scores or {k: 10 if CFG.criterion(k).max >= 10 else 2 for k in keys}
    return {
        "candidate_id": 1,
        "redflag": {
            "flags": list(flags),
            "years_experience_estimate": est or {"value": 8, "confidence": "high"},
        },
        "fit": {
            "scores": {k: {"score": float(scores[k]), "quote": "q", "rationale": "r"} for k in keys},
            "bonus": {"points": bonus, "justification": "j" if bonus else None},
            "summary": "s",
        },
        "quote_warnings": [],
    }


# --- Gates ------------------------------------------------------------------

def test_no_gate_for_clean_candidate():
    gate, reasons = apply_gates(precheck(), verdict(), CFG)
    assert gate is None
    assert reasons == []


def test_g1_precheck_placeholder():
    gate, reasons = apply_gates(
        precheck(placeholders=[{"kind": "placeholder", "match": "[Company Name]"}]), verdict(), CFG
    )
    assert gate == "G1"
    assert any("[Company Name]" in r for r in reasons)


def test_g1_judge_tier1_flag():
    flags = [{"tier": 1, "kind": "wrong_company", "quote": "join Palantir", "explanation": "e"}]
    gate, reasons = apply_gates(precheck(), verdict(flags=flags), CFG)
    assert gate == "G1"
    assert any("wrong_company" in r for r in reasons)


def test_g1_intra_cv_duplicates():
    dups = [{"a": "x", "b": "y", "similarity": 0.9}]
    gate, _ = apply_gates(precheck(intra_cv_duplicate_bullets=dups), verdict(), CFG)
    assert gate == "G1"


def test_g1_many_pool_duplicates():
    dups = [{"with_candidate": 2, "bullet": f"b{i}"} for i in range(4)]
    gate, _ = apply_gates(precheck(pool_duplicate_bullets=dups), verdict(), CFG)
    assert gate == "G1"


def test_few_pool_duplicates_do_not_gate():
    dups = [{"with_candidate": 2, "bullet": "b1"}]
    gate, _ = apply_gates(precheck(pool_duplicate_bullets=dups), verdict(), CFG)
    assert gate is None


def test_g2_at_threshold_tier2_flags():
    # Read the threshold from config rather than hardcoding it, so the
    # number lives in one place (role.json) and this test tracks
    # calibration changes.
    threshold = int(CFG.gates["tier2_gate_count"])
    flags = [
        {"tier": 2, "kind": f"k{i}", "quote": "q", "explanation": "e"} for i in range(threshold)
    ]
    gate, reasons = apply_gates(precheck(), verdict(flags=flags), CFG)
    assert gate == "G2"
    assert any(str(threshold) in r for r in reasons)


def test_g2_not_triggered_by_one_below_threshold():
    threshold = int(CFG.gates["tier2_gate_count"])
    flags = [
        {"tier": 2, "kind": f"k{i}", "quote": "q", "explanation": "e"}
        for i in range(threshold - 1)
    ]
    gate, _ = apply_gates(precheck(), verdict(flags=flags), CFG)
    assert gate is None


def test_metadata_signal_counts_half_and_cannot_gate_alone():
    # Two judge Tier-2 flags plus metadata = 2.5, still below the (>= 3)
    # threshold on its own.
    threshold = int(CFG.gates["tier2_gate_count"])
    flags = [{"tier": 2, "kind": f"k{i}", "quote": "q", "explanation": "e"} for i in range(2)]
    pc = precheck(template_metadata_signal=True)
    assert tier2_count(pc, verdict(flags=flags), CFG) == 2.5
    assert 2.5 < threshold
    gate, _ = apply_gates(pc, verdict(flags=flags), CFG)
    assert gate is None


def test_skills_count_contributes_a_tier2_signal():
    # Read the threshold from config rather than hardcoding it, so the number
    # lives in one place (role.json) and this test tracks calibration changes.
    threshold = int(CFG.gates["skills_count_threshold"])
    pc = precheck(skills_count=threshold)
    assert tier2_count(pc, verdict(), CFG) >= 1


def test_skills_count_below_threshold_does_not_contribute_signal():
    threshold = int(CFG.gates["skills_count_threshold"])
    pc = precheck(skills_count=threshold - 1)
    assert tier2_count(pc, verdict(), CFG) == 0


def test_tier2_gate_count_raised_to_four():
    # Fix 2: three common Tier-2 stylistic observations must no longer be
    # enough to eliminate a candidate on their own -- four independent
    # signals are required, so a genuine cluster of problems, not three
    # coincidental style notes.
    assert int(CFG.gates["tier2_gate_count"]) == 4

    three = [{"tier": 2, "kind": f"k{i}", "quote": "q", "explanation": "e"} for i in range(3)]
    gate, _ = apply_gates(precheck(), verdict(flags=three), CFG)
    assert gate is None

    four = [{"tier": 2, "kind": f"k{i}", "quote": "q", "explanation": "e"} for i in range(4)]
    gate, _ = apply_gates(precheck(), verdict(flags=four), CFG)
    assert gate == "G2"


def test_g3_hidden_text():
    pc = precheck(hidden_text={"found": True, "spans": [{"kind": "white_on_white", "text": "x"}]})
    gate, reasons = apply_gates(pc, verdict(), CFG)
    assert gate == "G3"
    assert any("white_on_white" in r for r in reasons)


def test_g4_years_below_minimum_with_high_confidence():
    pc = precheck(years_experience={"computed": 2.0, "confidence": "high", "ranges": []})
    gate, _ = apply_gates(pc, verdict(est={"value": 2, "confidence": "high"}), CFG)
    assert gate == "G4"


def test_g4_uses_judge_estimate_when_precheck_confidence_low():
    pc = precheck(years_experience={"computed": 0.0, "confidence": "low", "ranges": []})
    # Judge says 9 years, so no gate despite the precheck computing zero.
    gate, _ = apply_gates(pc, verdict(est={"value": 9, "confidence": "high"}), CFG)
    assert gate is None


def test_g4_gates_when_both_sources_are_low():
    pc = precheck(years_experience={"computed": 0.0, "confidence": "low", "ranges": []})
    gate, _ = apply_gates(pc, verdict(est={"value": 1, "confidence": "medium"}), CFG)
    assert gate == "G4"


def test_g5_non_us_explicit():
    pc = precheck(location={"us_evident": False, "non_us_explicit": True, "timezone_hint": "unknown", "raw": "Hanoi, Vietnam"})
    gate, _ = apply_gates(pc, verdict(), CFG)
    assert gate == "G5"


def test_unknown_location_does_not_gate():
    pc = precheck(location={"us_evident": False, "non_us_explicit": False, "timezone_hint": "unknown", "raw": None})
    gate, _ = apply_gates(pc, verdict(), CFG)
    assert gate is None


def test_missing_degree_does_not_gate_by_default():
    gate, _ = apply_gates(precheck(degree={"present": False, "level": None, "field": None}), verdict(), CFG)
    assert gate is None


def test_gate_precedence_g1_wins_over_g4():
    pc = precheck(
        placeholders=[{"kind": "placeholder", "match": "[Your Name]"}],
        years_experience={"computed": 1.0, "confidence": "high", "ranges": []},
    )
    gate, _ = apply_gates(pc, verdict(est={"value": 1, "confidence": "high"}), CFG)
    assert gate == "G1"


def test_g5_suppressed_when_sponsorship_answer_says_not_needed():
    # signals.find_location already folds the sponsorship "no" answer into
    # work_authorized -> non_us_explicit=False; confirm the gate honours it.
    pc = precheck(location={
        "us_evident": False,
        "non_us_explicit": False,
        "timezone_hint": "unknown",
        "raw": "Hanoi, Vietnam",
        "work_authorized": True,
        "needs_sponsorship": False,
    })
    gate, _ = apply_gates(pc, verdict(), CFG)
    assert gate is None


# --- Penalties --------------------------------------------------------------

def test_no_penalties_for_clean_candidate():
    assert compute_penalties(precheck(), verdict(), CFG) == []


def test_no_linkedin_penalty():
    pc = precheck(linkedin={"present": False, "source": "none", "url": None, "name_matches": None})
    pens = compute_penalties(pc, verdict(), CFG)
    assert [p["kind"] for p in pens] == ["no_linkedin"]
    assert pens[0]["points"] == 8


def test_linkedin_name_mismatch_penalty():
    pc = precheck(linkedin={"present": True, "source": "cv", "url": "u", "name_matches": False})
    pens = compute_penalties(pc, verdict(), CFG)
    assert [p["kind"] for p in pens] == ["linkedin_name_mismatch"]


def test_linkedin_unknown_match_is_not_penalised():
    pc = precheck(linkedin={"present": True, "source": "cv", "url": "u", "name_matches": None})
    assert compute_penalties(pc, verdict(), CFG) == []


def test_tier2_penalties_charged_per_signal():
    flags = [{"tier": 2, "kind": f"k{i}", "quote": "q", "explanation": "e"} for i in range(2)]
    pens = compute_penalties(precheck(), verdict(flags=flags), CFG)
    tier2 = [p for p in pens if p["kind"] == "tier2_signal"]
    assert sum(p["points"] for p in tier2) == 10


def test_pool_duplicate_penalty():
    dups = [{"with_candidate": 2, "bullet": "b1"}, {"with_candidate": 3, "bullet": "b2"}]
    pens = compute_penalties(precheck(pool_duplicate_bullets=dups), verdict(), CFG)
    assert any(p["kind"] == "pool_duplicate" and p["points"] == 10 for p in pens)


def test_years_4_to_5_penalty():
    pc = precheck(years_experience={"computed": 4.5, "confidence": "high", "ranges": []})
    pens = compute_penalties(pc, verdict(est={"value": 4.5, "confidence": "high"}), CFG)
    assert any(p["kind"] == "years_4_to_5" and p["points"] == 10 for p in pens)


def test_no_years_penalty_above_five():
    pc = precheck(years_experience={"computed": 6.0, "confidence": "high", "ranges": []})
    pens = compute_penalties(pc, verdict(), CFG)
    assert not any(p["kind"] == "years_4_to_5" for p in pens)


def test_no_degree_penalty():
    pens = compute_penalties(precheck(degree={"present": False, "level": None, "field": None}), verdict(), CFG)
    assert any(p["kind"] == "no_degree" and p["points"] == 5 for p in pens)


def test_short_stints_penalty_at_threshold():
    pens = compute_penalties(precheck(short_stints_last_5y=3), verdict(), CFG)
    assert any(p["kind"] == "short_stints" for p in pens)


def test_short_stints_below_threshold_not_penalised():
    pens = compute_penalties(precheck(short_stints_last_5y=2), verdict(), CFG)
    assert not any(p["kind"] == "short_stints" for p in pens)


# --- Assessment -------------------------------------------------------------

def test_assess_computes_final_score():
    # verdict() scores 10 on every criterion with max >= 10, 2 on the rest --
    # derive the expected fit from CFG rather than hardcode a number tied to
    # one particular criteria split (role.json's high/low mix is config, not
    # a fixed shape this test should assume). Penalties 8 (no linkedin); bonus 3.
    pc = precheck(linkedin={"present": False, "source": "none", "url": None, "name_matches": None})
    expected_fit = float(sum(10 if CFG.criterion(k).max >= 10 else 2 for k in CFG.criterion_keys()))
    a = assess(pc, verdict(bonus=3.0), CFG)
    assert a.fit == expected_fit
    assert a.bonus == 3.0
    assert a.penalty_total == 8.0
    assert a.final == round(expected_fit + 3.0 - 8.0, 2)
    assert a.gate is None
    assert "no LinkedIn" in a.flags


def test_assess_gated_candidate_keeps_scores_but_records_gate():
    pc = precheck(hidden_text={"found": True, "spans": [{"kind": "micro_font", "text": "x"}]})
    a = assess(pc, verdict(), CFG)
    assert a.gate == "G3"
    assert a.fit > 0


def test_assess_final_never_below_zero():
    scores = {k: 0 for k in CFG.criterion_keys()}
    pc = precheck(
        linkedin={"present": False, "source": "none", "url": None, "name_matches": None},
        degree={"present": False, "level": None, "field": None},
        short_stints_last_5y=3,
    )
    a = assess(pc, verdict(scores=scores), CFG)
    assert a.final == 0.0


def test_assess_records_flag_chips_for_report():
    pc = precheck(gap_over_12m=True, location={"us_evident": False, "non_us_explicit": False, "timezone_hint": "unknown", "raw": None})
    a = assess(pc, verdict(), CFG)
    assert "gap" in a.flags
    assert "location?" in a.flags
    assert a.timezone_hint == "unknown"


# --- Fix 2: "claims every criterion" flag (deterministic, report-only) -----
#
# See the comment in screen.rank.assess for why this is a flag rather than a
# penalty: the same signal, tried earlier as a penalty on ordinary tailored
# CVs, eliminated two-thirds of a real sample. This is purely a report
# annotation -- it must never touch `final`, `penalty_total`, or any gate.


def test_all_seven_criteria_high_produces_claims_all_flag():
    scores = {k: CFG.criterion(k).max for k in CFG.criterion_keys()}  # 100% of max, all seven
    a = assess(precheck(), verdict(scores=scores), CFG)
    assert "claims all criteria" in a.flags
    assert not any(f.startswith("claims ") for f in a.flags if f != "claims all criteria")


def test_six_of_seven_criteria_high_produces_claims_6_7_flag():
    keys = CFG.criterion_keys()
    scores = {k: CFG.criterion(k).max for k in keys}
    scores[keys[-1]] = 0  # one criterion scored zero -- below 60% of its max
    a = assess(precheck(), verdict(scores=scores), CFG)
    assert "claims 6/7 criteria" in a.flags
    assert "claims all criteria" not in a.flags


def test_five_of_seven_criteria_high_produces_no_claims_flag():
    keys = CFG.criterion_keys()
    scores = {k: CFG.criterion(k).max for k in keys}
    scores[keys[-1]] = 0
    scores[keys[-2]] = 0
    a = assess(precheck(), verdict(scores=scores), CFG)
    assert not any(f.startswith("claims") for f in a.flags)


def test_claims_all_criteria_flag_never_sets_gate_or_changes_penalty_total():
    scores = {k: CFG.criterion(k).max for k in CFG.criterion_keys()}
    a = assess(precheck(), verdict(scores=scores), CFG)
    assert a.gate is None
    assert a.penalty_total == 0.0
    assert a.final == round(a.fit + a.bonus - a.penalty_total, 2)


def test_claims_6_of_7_flag_never_sets_gate_or_changes_penalty_total():
    keys = CFG.criterion_keys()
    scores = {k: CFG.criterion(k).max for k in keys}
    scores[keys[-1]] = 0
    a = assess(precheck(), verdict(scores=scores), CFG)
    assert a.gate is None
    assert a.penalty_total == 0.0


def test_assess_needs_sponsorship_flag_no_score_effect_no_elimination():
    # An affirmative sponsorship answer must never gate or dock points --
    # it's information for the human, nothing more (REAL-DATA-ADDENDUM E4).
    pc = precheck(location={
        "us_evident": True,
        "non_us_explicit": False,
        "timezone_hint": "ET",
        "raw": "New York",
        "work_authorized": False,
        "needs_sponsorship": True,
    })
    clean = assess(precheck(), verdict(), CFG)
    a = assess(pc, verdict(), CFG)
    assert "needs sponsorship" in a.flags
    assert a.gate is None
    assert a.final == clean.final
