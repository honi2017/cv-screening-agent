import dataclasses
from pathlib import Path

import pytest

from screen.config import load_role
from screen.rank import apply_gates, assess, compute_penalties, rank_and_cut, tier2_count

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


# --- LinkedIn liveness: the penalty was reversed ----------------------------
#
# This used to add a `linkedin_dead` penalty whenever `liveness == "dead"`.
# That verdict was retired (see screen.linkedin_check's module docstring):
# LinkedIn's HTTP 999 -- the signal "dead" was based on -- is byte-identical
# for a fabricated slug and for a real profile that simply isn't public. Run
# against the real 69-candidate pool, treating 999 as "dead" flagged 26 of 54
# profiles (48%), including the top-ranked candidate, purely for having
# ordinary privacy settings. compute_penalties must never deduct anything for
# ANY liveness value -- "live", "unknown", or even a stray legacy "dead"
# string a precheck cached before this fix shipped. "live" is instead
# surfaced as positive-only evidence in the report's evidence panel (see
# screen.report and tests/test_report.py), never as a deduction.


@pytest.mark.parametrize("liveness", ["live", "unknown", "dead", None])
def test_linkedin_liveness_never_produces_a_penalty(liveness):
    linkedin = {"present": True, "source": "cv", "url": "u", "name_matches": True}
    if liveness is not None:
        linkedin["liveness"] = liveness
    pc = precheck(linkedin=linkedin)
    kinds = {p["kind"] for p in compute_penalties(pc, verdict(), CFG)}
    assert "linkedin_dead" not in kinds


def test_linkedin_liveness_never_produces_a_flag_chip():
    for liveness in ("live", "unknown", "dead"):
        pc = precheck(
            linkedin={"present": True, "source": "cv", "url": "u", "name_matches": True, "liveness": liveness}
        )
        a = assess(pc, verdict(), CFG)
        assert "LinkedIn dead" not in a.flags


def test_linkedin_dead_liveness_still_stacks_with_name_mismatch_penalty():
    # A liveness value never suppresses -- or is suppressed by --
    # linkedin_name_mismatch; that penalty is orthogonal and keyed on
    # name_matches alone.
    pc = precheck(
        linkedin={"present": True, "source": "cv", "url": "u", "name_matches": False, "liveness": "dead"}
    )
    kinds = {p["kind"] for p in compute_penalties(pc, verdict(), CFG)}
    assert kinds == {"linkedin_name_mismatch"}


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


# --- Fix 2 / Change 3 / Change 4: "broad claims, uncorroborated" ------------
#
# See the comment in screen.rank.assess for the full history: this was kept
# as a flag with NO score effect for a while, because the same signal, tried
# earlier as a penalty on ordinary tailored CVs, eliminated two-thirds of a
# real sample. It replaced an earlier "claims all/6-of-7 criteria" flag that
# measurement showed was near-tautological with the final score (85% of the
# top 13 by score vs. 11% of the rest). The discriminating pattern is
# breadth paired with nothing independent corroborating it -- every cited
# metric being suspiciously round, or a Tier-2 signal -- not breadth alone.
#
# Change 3: the hiring team reviewed real output and asked for this signal
# to carry weight, so it is now ALSO `penalties.broad_claims` -- applied by
# `compute_penalties` from the exact same condition that produces the flag
# (`screen.rank._broad_claims_uncorroborated`), so the two can never
# disagree about who they apply to. It is a penalty, never a gate: it must
# lower a rank, not eliminate anyone.
#
# Change 4: the original "uncorroborated" test used `not linkedin_present`
# instead of the round-metrics test above. That double-charged a fact
# `no_linkedin` already penalises (see the long comment in
# screen.rank._broad_claims_uncorroborated for the measurement that killed
# it) and is now replaced by `_all_metrics_round`. The Tier-2 branch is
# unchanged, including its deliberate double-count with `tier2_signal`.


def test_broad_claims_does_not_fire_from_missing_linkedin_alone_regression():
    # REGRESSION TEST for the double-charge bug: a broad candidate with no
    # LinkedIn, no Tier-2 flag, and non-round metrics must NOT also pay
    # `broad_claims` -- that would double-charge the single missing-LinkedIn
    # fact that `no_linkedin` already prices (see the removed
    # `not linkedin_present` branch documented in
    # screen.rank._broad_claims_uncorroborated). The `no_linkedin` penalty
    # still applies on its own; only the extra, redundant penalty is gone.
    scores = {k: CFG.criterion(k).max for k in CFG.criterion_keys()}  # all seven at 100% of max
    pc = precheck(
        linkedin={"present": False, "source": "none", "url": None, "name_matches": None},
        round_metric_ratio=0.2,  # some metrics, not all round
        metric_count=5,
    )
    a = assess(pc, verdict(scores=scores), CFG)
    assert "broad claims, uncorroborated" not in a.flags
    assert not any(p["kind"] == "broad_claims" for p in a.penalties)
    # The no-LinkedIn penalty still fires on its own -- this is the "red
    # flag but not a deal breaker" the hiring team asked for, not zero cost.
    assert any(p["kind"] == "no_linkedin" for p in a.penalties)
    assert a.gate is None
    assert a.final == round(a.fit + a.bonus - a.penalty_total, 2)
    assert a.penalty_total == float(CFG.penalties["no_linkedin"])


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
    flags = [{"tier": 2, "kind": "generic_bullet", "quote": "q", "explanation": "e"}]
    pens = compute_penalties(precheck(), verdict(flags=flags, scores=scores), CFG)
    broad = [p for p in pens if p["kind"] == "broad_claims"]
    assert len(broad) == 1
    assert broad[0]["points"] == CFG.penalties["broad_claims"]


def test_broad_claims_still_fires_via_tier2_branch_and_double_counts_deliberately():
    # The Tier-2 branch is UNCHANGED by this fix, including its deliberate
    # double-count with `tier2_signal` (see the "known, accepted overlap"
    # comment in screen.rank._broad_claims_uncorroborated) -- the hiring
    # team chose to keep this one, unlike the LinkedIn double-charge, which
    # was removed.
    scores = {k: CFG.criterion(k).max for k in CFG.criterion_keys()}  # all seven at max
    flags = [{"tier": 2, "kind": "generic_bullet", "quote": "q", "explanation": "e"}]
    a = assess(precheck(), verdict(flags=flags, scores=scores), CFG)
    assert "broad claims, uncorroborated" in a.flags
    assert any(p["kind"] == "broad_claims" for p in a.penalties)
    assert any(p["kind"] == "tier2_signal" for p in a.penalties)
    assert a.penalty_total == float(CFG.penalties["broad_claims"] + CFG.penalties["tier2_signal"])


# --- Change 4: all-round-metrics branch (replaces the removed LinkedIn one) --
#
# See screen.rank._all_metrics_round for the measurement backing this: among
# candidates with at least three metrics, 12 had every metric round and 9 of
# those 12 already carried an independent AI-slop or broad-claims flag (75%
# concordance). The minimum-metric guard is essential: without it, a single
# round number would trip this rule meaninglessly.


def test_broad_claims_fires_when_all_metrics_round_and_minimum_met():
    scores = {k: CFG.criterion(k).max for k in CFG.criterion_keys()}  # all seven at max
    pc = precheck(
        linkedin={"present": True, "source": "trakstar", "url": "u", "name_matches": True},
        round_metric_ratio=1.0,
        metric_count=3,  # exactly the configured minimum
    )
    a = assess(pc, verdict(scores=scores), CFG)
    assert "broad claims, uncorroborated" in a.flags
    assert any(p["kind"] == "broad_claims" for p in a.penalties)
    # No LinkedIn penalty and no Tier-2 signal here -- this candidate is
    # clean on both of those; the round-metrics branch alone triggers it.
    assert not any(p["kind"] == "no_linkedin" for p in a.penalties)
    assert not any(p["kind"] == "tier2_signal" for p in a.penalties)


def test_broad_claims_does_not_fire_when_ratio_is_one_but_metric_count_below_minimum():
    # A CV with a single round-percentage bullet scores ratio 1.0 -- the
    # minimum-metric guard must stop that from tripping the rule.
    scores = {k: CFG.criterion(k).max for k in CFG.criterion_keys()}
    pc = precheck(
        linkedin={"present": True, "source": "trakstar", "url": "u", "name_matches": True},
        round_metric_ratio=1.0,
        metric_count=2,  # one below the configured minimum of 3
    )
    a = assess(pc, verdict(scores=scores), CFG)
    assert "broad claims, uncorroborated" not in a.flags
    assert not any(p["kind"] == "broad_claims" for p in a.penalties)


def test_broad_claims_null_round_metric_ratio_does_not_fire_round_branch():
    # An older precheck predating this field (or one where the value is
    # explicitly null) must be treated as "no metric data", never as if the
    # ratio were 0.0 or 1.0.
    scores = {k: CFG.criterion(k).max for k in CFG.criterion_keys()}
    pc = precheck(
        linkedin={"present": True, "source": "trakstar", "url": "u", "name_matches": True},
        round_metric_ratio=None,
        metric_count=10,
    )
    a = assess(pc, verdict(scores=scores), CFG)
    assert "broad claims, uncorroborated" not in a.flags
    assert not any(p["kind"] == "broad_claims" for p in a.penalties)


def test_min_metrics_for_round_ratio_config_key_changes_the_minimum():
    scores = {k: CFG.criterion(k).max for k in CFG.criterion_keys()}
    pc = precheck(
        linkedin={"present": True, "source": "trakstar", "url": "u", "name_matches": True},
        round_metric_ratio=1.0,
        metric_count=2,
    )
    # At the default minimum of 3, two metrics is not enough.
    assert not any(p["kind"] == "broad_claims" for p in compute_penalties(pc, verdict(scores=scores), CFG))

    # Lowering the config key to 2 makes the same two metrics enough.
    lowered = dataclasses.replace(CFG, gates={**CFG.gates, "min_metrics_for_round_ratio": 2})
    assert any(
        p["kind"] == "broad_claims" for p in compute_penalties(pc, verdict(scores=scores), lowered)
    )


def test_broad_claims_flag_and_penalty_agree_for_the_round_metrics_branch():
    # compute_penalties (the score) and assess()'s flag (the report) must
    # never disagree about who the round-metrics branch applies to -- both
    # are computed from the one function, _broad_claims_uncorroborated.
    scores = {k: CFG.criterion(k).max for k in CFG.criterion_keys()}
    pc = precheck(
        linkedin={"present": True, "source": "trakstar", "url": "u", "name_matches": True},
        round_metric_ratio=1.0,
        metric_count=4,
    )
    v = verdict(scores=scores)
    penalty_fired = any(p["kind"] == "broad_claims" for p in compute_penalties(pc, v, CFG))
    a = assess(pc, v, CFG)
    flag_fired = "broad claims, uncorroborated" in a.flags
    assert penalty_fired and flag_fired


def test_all_metrics_round_with_narrow_coverage_produces_no_flag_or_penalty():
    # `_broad_claims_uncorroborated` is a conjunction -- breadth AND something
    # uncorroborated -- and the breadth half is unguarded for this branch.
    # `test_five_of_seven_with_no_linkedin_produces_no_flag_or_penalty` above
    # used to pin it by making the uncorroborated half true via a missing
    # LinkedIn, but that branch no longer feeds this rule, so it now passes
    # with BOTH halves false and would not notice `broad and` being dropped
    # from the return. This pins breadth for the round-metrics branch
    # specifically: all-round metrics on a candidate who scores on only five
    # of seven criteria must stay silent.
    keys = CFG.criterion_keys()
    scores = {k: CFG.criterion(k).max for k in keys}
    scores[keys[-1]] = 0
    scores[keys[-2]] = 0
    pc = precheck(round_metric_ratio=1.0, metric_count=5)
    a = assess(pc, verdict(scores=scores), CFG)
    assert "broad claims, uncorroborated" not in a.flags
    assert not any(p["kind"] == "broad_claims" for p in a.penalties)


# --- Reference flags: no score effect, ever -------------------------------
#
# See screen.rank._reference_flags for the full rationale. A reference flag
# is shown on the report for a human to eyeball and must NEVER affect a
# score: not produced by compute_penalties, never able to gate/eliminate/
# reorder anyone. The hiring manager's own words: "Just have a flagging, no
# points should be affected. Just for reference."

_OFFSHORE_SENTENCE = (
    "Collaborated with offshore engineering teams in Vietnam to design and "
    "deploy integration APIs."
)


def test_offshore_claim_reference_flag_fires_with_verbatim_sentence_and_places():
    pc = precheck(offshore_claims=[{"sentence": _OFFSHORE_SENTENCE, "places": ["vietnam"]}])
    a = assess(pc, verdict(), CFG)
    rf = next((f for f in a.reference_flags if f["kind"] == "offshore_claim"), None)
    assert rf is not None
    assert _OFFSHORE_SENTENCE in rf["sentences"]
    assert "vietnam" in rf["places"]


def test_offshore_claim_reference_flag_absent_without_any_claim():
    a = assess(precheck(), verdict(), CFG)
    assert not any(f["kind"] == "offshore_claim" for f in a.reference_flags)


def test_full_criteria_coverage_fires_on_broad_and_clean():
    # All seven criteria at max, verifiable LinkedIn present, no Tier-2
    # signal -- the exact case that currently escapes notice: broad_claims
    # requires no-LinkedIn or a Tier-2 signal, neither of which applies here.
    scores = {k: CFG.criterion(k).max for k in CFG.criterion_keys()}
    a = assess(precheck(), verdict(scores=scores), CFG)
    assert not any(p["kind"] == "broad_claims" for p in a.penalties)
    rf = next((f for f in a.reference_flags if f["kind"] == "full_criteria_coverage"), None)
    assert rf is not None
    total = len(CFG.criterion_keys())
    assert rf["label"] == f"claims {total}/{total} criteria"


def test_full_criteria_coverage_does_not_fire_when_broad_claims_penalty_fires():
    # Same breadth, but a Tier-2 signal fired -- broad_claims fires, so this
    # situation is already visible and scored; the reference flag must not
    # also fire (it exists for the case the penalty does NOT catch).
    scores = {k: CFG.criterion(k).max for k in CFG.criterion_keys()}
    flags = [{"tier": 2, "kind": "generic_bullet", "quote": "q", "explanation": "e"}]
    a = assess(precheck(), verdict(flags=flags, scores=scores), CFG)
    assert any(p["kind"] == "broad_claims" for p in a.penalties)
    assert not any(f["kind"] == "full_criteria_coverage" for f in a.reference_flags)


def test_full_criteria_coverage_does_not_fire_on_narrow_candidate():
    keys = CFG.criterion_keys()
    scores = {k: CFG.criterion(k).max for k in keys}
    scores[keys[-1]] = 0
    scores[keys[-2]] = 0
    a = assess(precheck(), verdict(scores=scores), CFG)
    assert not any(f["kind"] == "full_criteria_coverage" for f in a.reference_flags)


def test_reference_flag_kinds_never_appear_in_compute_penalties():
    # Fire both underlying conditions at once and confirm compute_penalties
    # -- the ONLY function whose output can cost a candidate a point -- never
    # produces either kind, under any circumstance.
    scores = {k: CFG.criterion(k).max for k in CFG.criterion_keys()}
    pc = precheck(offshore_claims=[{"sentence": _OFFSHORE_SENTENCE, "places": ["vietnam"]}])
    pens = compute_penalties(pc, verdict(scores=scores), CFG)
    kinds = {p["kind"] for p in pens}
    assert "offshore_claim" not in kinds
    assert "full_criteria_coverage" not in kinds


def test_offshore_claim_config_key_disables_the_flag():
    cfg = dataclasses.replace(
        CFG, reference_flags={**CFG.reference_flags, "offshore_claim": False}
    )
    pc = precheck(offshore_claims=[{"sentence": _OFFSHORE_SENTENCE, "places": ["vietnam"]}])
    a = assess(pc, verdict(), cfg)
    assert not any(f["kind"] == "offshore_claim" for f in a.reference_flags)


def test_full_criteria_coverage_config_key_disables_the_flag():
    cfg = dataclasses.replace(
        CFG, reference_flags={**CFG.reference_flags, "full_criteria_coverage": False}
    )
    scores = {k: CFG.criterion(k).max for k in CFG.criterion_keys()}
    a = assess(precheck(), verdict(scores=scores), cfg)
    assert not any(f["kind"] == "full_criteria_coverage" for f in a.reference_flags)


def test_reference_flags_never_change_final_score_or_status():
    """The hard constraint: build a candidate that fires BOTH reference
    flags, and confirm `final` and the ledger `status` it ends up with are
    byte-identical to an otherwise identical candidate with both flags
    suppressed via config. If this test ever fails, a reference flag has
    stopped being reference-only.
    """
    scores = {k: CFG.criterion(k).max for k in CFG.criterion_keys()}
    pc = precheck(offshore_claims=[{"sentence": _OFFSHORE_SENTENCE, "places": ["vietnam"]}])
    v = verdict(scores=scores)

    cfg_on = CFG  # roles/fde/role.json ships both reference_flags keys true
    cfg_off = dataclasses.replace(
        CFG, reference_flags={"offshore_claim": False, "full_criteria_coverage": False}
    )

    with_flags = assess(pc, v, cfg_on)
    without_flags = assess(pc, v, cfg_off)

    # Sanity check that this test actually exercises both flags -- otherwise
    # the equality assertions below would be vacuous.
    assert {f["kind"] for f in with_flags.reference_flags} == {
        "offshore_claim",
        "full_criteria_coverage",
    }
    assert without_flags.reference_flags == []

    assert with_flags.final == without_flags.final
    assert with_flags.penalty_total == without_flags.penalty_total
    assert with_flags.gate == without_flags.gate
    assert with_flags.penalties == without_flags.penalties

    # And the status a full ranking run assigns is identical too -- not just
    # the raw score in isolation. Four low-scoring filler candidates (same
    # in both runs) push this candidate to a clean top-of-pool "accepted"
    # in both scenarios, so the comparison isn't a vacuous "both waitlisted".
    fillers_on = {i: assess(precheck(candidate_id=i), verdict(), cfg_on) for i in range(2, 6)}
    fillers_off = {i: assess(precheck(candidate_id=i), verdict(), cfg_off) for i in range(2, 6)}

    result_with = rank_and_cut({1: with_flags, **fillers_on}, {}, CFG, "run-ref-on", {}, set())
    result_without = rank_and_cut(
        {1: without_flags, **fillers_off}, {}, CFG, "run-ref-off", {}, set()
    )

    def status_of(cid: int, result) -> str:
        if cid in result.accepted:
            return "accepted"
        if cid in result.waitlist:
            return "waitlist"
        if cid in result.gated:
            return "gated"
        return "unknown"

    assert status_of(1, result_with) == status_of(1, result_without) == "accepted"


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
