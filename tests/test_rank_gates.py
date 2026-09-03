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
    # read the value from config: it is a hiring-policy dial the team tunes,
    # so the number must live in role.json only, never duplicated here.
    assert pens[0]["points"] == CFG.penalties["no_linkedin"]


def test_linkedin_name_mismatch_penalty():
    pc = precheck(linkedin={"present": True, "source": "cv", "url": "u", "name_matches": False})
    pens = compute_penalties(pc, verdict(), CFG)
    assert [p["kind"] for p in pens] == ["linkedin_name_mismatch"]


def test_linkedin_unknown_match_is_not_penalised():
    pc = precheck(linkedin={"present": True, "source": "cv", "url": "u", "name_matches": None})
    assert compute_penalties(pc, verdict(), CFG) == []


# --- Change 1: LinkedIn liveness penalty ------------------------------------
#
# Separate from, and additional to, no_linkedin (which applies only when
# there is no usable URL at all -- present=False). A "dead" verdict never
# suppresses no_linkedin/linkedin_name_mismatch and is never suppressed by
# them; the two penalties simply stack when both conditions hold.


def test_linkedin_dead_is_penalised():
    pc = precheck(
        linkedin={"present": True, "source": "cv", "url": "u", "name_matches": True, "liveness": "dead"}
    )
    pens = compute_penalties(pc, verdict(), CFG)
    dead = [p for p in pens if p["kind"] == "linkedin_dead"]
    assert len(dead) == 1
    # read the value from config, per the same policy-dial reasoning as
    # no_linkedin above.
    assert dead[0]["points"] == CFG.penalties["linkedin_dead"]


def test_linkedin_live_is_not_penalised():
    pc = precheck(
        linkedin={"present": True, "source": "cv", "url": "u", "name_matches": True, "liveness": "live"}
    )
    assert not any(p["kind"] == "linkedin_dead" for p in compute_penalties(pc, verdict(), CFG))


def test_linkedin_unknown_liveness_is_not_penalised():
    # The critical safety property from screen.linkedin_check: "unknown" (a
    # network error, a timeout, or an ambiguous status code) must never be
    # treated as "probably dead".
    pc = precheck(
        linkedin={"present": True, "source": "cv", "url": "u", "name_matches": True, "liveness": "unknown"}
    )
    assert not any(p["kind"] == "linkedin_dead" for p in compute_penalties(pc, verdict(), CFG))


def test_linkedin_dead_penalty_stacks_with_name_mismatch():
    pc = precheck(
        linkedin={"present": True, "source": "cv", "url": "u", "name_matches": False, "liveness": "dead"}
    )
    kinds = {p["kind"] for p in compute_penalties(pc, verdict(), CFG)}
    assert {"linkedin_name_mismatch", "linkedin_dead"} <= kinds


def test_linkedin_dead_produces_flag_chip():
    pc = precheck(
        linkedin={"present": True, "source": "cv", "url": "u", "name_matches": True, "liveness": "dead"}
    )
    a = assess(pc, verdict(), CFG)
    assert "LinkedIn dead" in a.flags


def test_tier2_penalties_charged_per_signal():
    flags = [{"tier": 2, "kind": f"k{i}", "quote": "q", "explanation": "e"} for i in range(2)]
    pens = compute_penalties(precheck(), verdict(flags=flags), CFG)
    tier2 = [p for p in pens if p["kind"] == "tier2_signal"]
    # Read the per-signal value from config rather than hardcoding it: Change
    # 2 doubled tier2_signal from 5 to 10, and a literal here would silently
    # drift from role.json the next time the team retunes it.
    assert sum(p["points"] for p in tier2) == 2 * CFG.penalties["tier2_signal"]


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
    # Derive both the expected fit and the expected penalty from CFG rather
    # than hardcoding them: the criteria split and the penalty weights are both
    # config the team tunes, and a duplicated literal here would silently
    # contradict role.json the moment either is changed.
    pc = precheck(linkedin={"present": False, "source": "none", "url": None, "name_matches": None})
    expected_fit = float(sum(10 if CFG.criterion(k).max >= 10 else 2 for k in CFG.criterion_keys()))
    expected_pen = float(CFG.penalties["no_linkedin"])
    a = assess(pc, verdict(bonus=3.0), CFG)
    assert a.fit == expected_fit
    assert a.bonus == 3.0
    assert a.penalty_total == expected_pen
    assert a.final == round(expected_fit + 3.0 - expected_pen, 2)
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


# --- Fix 2 / Change 3: "broad claims, uncorroborated" -----------------------
#
# See the comment in screen.rank.assess for the full history: this was kept
# as a flag with NO score effect for a while, because the same signal, tried
# earlier as a penalty on ordinary tailored CVs, eliminated two-thirds of a
# real sample. It replaced an earlier "claims all/6-of-7 criteria" flag that
# measurement showed was near-tautological with the final score (85% of the
# top 13 by score vs. 11% of the rest). The discriminating pattern is
# breadth paired with nothing independent corroborating it -- absent
# LinkedIn or a Tier-2 signal -- not breadth alone.
#
# Change 3: the hiring team reviewed real output and asked for this signal
# to carry weight, so it is now ALSO `penalties.broad_claims` -- applied by
# `compute_penalties` from the exact same condition that produces the flag
# (`screen.rank._broad_claims_uncorroborated`), so the two can never
# disagree about who they apply to. It is a penalty, never a gate: it must
# lower a rank, not eliminate anyone.


def test_broad_claims_and_no_linkedin_produces_flag_and_penalty():
    scores = {k: CFG.criterion(k).max for k in CFG.criterion_keys()}  # all seven at 100% of max
    pc = precheck(linkedin={"present": False, "source": "none", "url": None, "name_matches": None})
    a = assess(pc, verdict(scores=scores), CFG)
    assert "broad claims, uncorroborated" in a.flags
    assert a.gate is None
    assert a.final == round(a.fit + a.bonus - a.penalty_total, 2)
    # Both the no-LinkedIn penalty AND the new broad_claims penalty apply --
    # read both values from config rather than hardcoding their sum.
    assert any(p["kind"] == "broad_claims" for p in a.penalties)
    assert a.penalty_total == float(CFG.penalties["no_linkedin"] + CFG.penalties["broad_claims"])


def test_six_of_seven_and_tier2_signal_produces_flag_and_penalty():
    keys = CFG.criterion_keys()
    scores = {k: CFG.criterion(k).max for k in keys}
    scores[keys[-1]] = 0  # one criterion scored zero -- below 60% of its max
    flags = [{"tier": 2, "kind": "generic_bullet", "quote": "q", "explanation": "e"}]
    a = assess(precheck(), verdict(flags=flags, scores=scores), CFG)
    assert "broad claims, uncorroborated" in a.flags
    assert any(p["kind"] == "broad_claims" for p in a.penalties)
    assert a.gate is None


def test_all_seven_with_linkedin_and_no_tier2_produces_no_flag_or_penalty():
    # The case the old design wrongly flagged: breadth alone, with a
    # verifiable LinkedIn profile and nothing suspicious from the judge.
    scores = {k: CFG.criterion(k).max for k in CFG.criterion_keys()}
    a = assess(precheck(), verdict(scores=scores), CFG)
    assert "broad claims, uncorroborated" not in a.flags
    assert not any(p["kind"] == "broad_claims" for p in a.penalties)


def test_five_of_seven_with_no_linkedin_produces_no_flag_or_penalty():
    # Breadth threshold not met, even though corroboration is also absent.
    keys = CFG.criterion_keys()
    scores = {k: CFG.criterion(k).max for k in keys}
    scores[keys[-1]] = 0
    scores[keys[-2]] = 0
    pc = precheck(linkedin={"present": False, "source": "none", "url": None, "name_matches": None})
    a = assess(pc, verdict(scores=scores), CFG)
    assert "broad claims, uncorroborated" not in a.flags
    assert not any(p["kind"] == "broad_claims" for p in a.penalties)


def test_broad_claims_penalty_value_read_from_config():
    scores = {k: CFG.criterion(k).max for k in CFG.criterion_keys()}
    pc = precheck(linkedin={"present": False, "source": "none", "url": None, "name_matches": None})
    pens = compute_penalties(pc, verdict(scores=scores), CFG)
    broad = [p for p in pens if p["kind"] == "broad_claims"]
    assert len(broad) == 1
    assert broad[0]["points"] == CFG.penalties["broad_claims"]


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
