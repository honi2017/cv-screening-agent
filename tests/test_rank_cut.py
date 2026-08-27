from pathlib import Path

from screen.config import load_role
from screen.ledger import LedgerEntry
from screen.rank import Assessment, rank_and_cut

CFG = load_role(Path(__file__).resolve().parents[1] / "roles" / "fde")


def a(cid, final, gate=None, tz="unknown"):
    return Assessment(
        candidate_id=cid,
        gate=gate,
        gate_reasons=["reason"] if gate else [],
        fit=final,
        bonus=0.0,
        penalties=[],
        penalty_total=0.0,
        final=final,
        flags=[],
        tier2_count=0.0,
        timezone_hint=tz,
    )


def entry(cid, status, final=0.0, run="run-0"):
    return LedgerEntry(
        candidate_id=cid,
        status=status,
        gate=None,
        final=final,
        first_seen_run=run,
        status_changed_run=run,
        pdf_sha256="abc",
        trakstar_updated_date="u",
    )


def test_cap_is_twenty_percent_floored():
    assessments = {i: a(i, 100 - i) for i in range(1, 11)}
    result = rank_and_cut(assessments, {}, CFG, "run-1", {}, set())
    assert result.pool_size == 10
    assert result.cap == 2
    assert result.accepted == [1, 2]
    assert result.waitlist == list(range(3, 11))


def test_gated_candidates_still_count_in_the_denominator():
    assessments = {i: a(i, 100 - i, gate="G1" if i > 5 else None) for i in range(1, 11)}
    result = rank_and_cut(assessments, {}, CFG, "run-1", {}, set())
    assert result.pool_size == 10
    assert result.cap == 2
    assert result.gated == [6, 7, 8, 9, 10]
    assert result.accepted == [1, 2]


def test_gated_candidate_is_never_accepted_even_with_top_score():
    assessments = {1: a(1, 99, gate="G2"), 2: a(2, 50), 3: a(3, 40), 4: a(4, 30), 5: a(5, 20)}
    result = rank_and_cut(assessments, {}, CFG, "run-1", {}, set())
    assert 1 not in result.accepted
    assert result.accepted == [2]


def test_accepted_candidate_is_never_demoted_when_pool_grows():
    ledger = {1: entry(1, "accepted", 60.0)}
    # Five new stronger applicants arrive; cap for 6 candidates is 1.
    assessments = {1: a(1, 60), **{i: a(i, 90 + i) for i in range(2, 7)}}
    result = rank_and_cut(assessments, ledger, CFG, "run-2", {}, set())
    assert result.cap == 1
    assert 1 in result.accepted
    assert ledger[1].status == "accepted"


def test_open_slots_are_filled_from_waitlist_and_new_arrivals_together():
    ledger = {1: entry(1, "accepted", 90.0), 2: entry(2, "waitlist", 70.0)}
    # Pool of 10 gives cap 2, one already taken, so one slot opens.
    assessments = {1: a(1, 90), 2: a(2, 70), **{i: a(i, 60 - i) for i in range(3, 11)}}
    result = rank_and_cut(assessments, ledger, CFG, "run-2", {}, set())
    assert result.cap == 2
    assert result.accepted == [1, 2]
    assert result.newly_accepted == [2]


def test_quality_floor_blocks_a_weak_candidate_from_a_new_slot():
    ledger = {1: entry(1, "accepted", 90.0)}
    # Cap 2 with 10 candidates, so a slot is open, but the best remaining is far
    # below the accepted floor of 90 - 5 = 85.
    assessments = {1: a(1, 90), **{i: a(i, 40) for i in range(2, 11)}}
    result = rank_and_cut(assessments, ledger, CFG, "run-2", {}, set())
    assert result.cap == 2
    assert result.newly_accepted == []
    assert result.quality_floor == 85.0
    assert result.accepted == [1]


def test_quality_floor_allows_a_candidate_within_five_points():
    ledger = {1: entry(1, "accepted", 90.0)}
    assessments = {1: a(1, 90), 2: a(2, 86), **{i: a(i, 30) for i in range(3, 11)}}
    result = rank_and_cut(assessments, ledger, CFG, "run-2", {}, set())
    assert result.newly_accepted == [2]


def test_no_quality_floor_on_the_first_run():
    assessments = {i: a(i, 10) for i in range(1, 11)}
    result = rank_and_cut(assessments, {}, CFG, "run-1", {}, set())
    assert result.quality_floor is None
    assert len(result.accepted) == 2


def test_no_slot_list_names_candidates_who_would_have_qualified():
    ledger = {1: entry(1, "accepted", 90.0)}
    assessments = {1: a(1, 90), 2: a(2, 88), 3: a(3, 87), **{i: a(i, 20) for i in range(4, 11)}}
    result = rank_and_cut(assessments, ledger, CFG, "run-2", {}, set())
    # Cap 2, one slot, candidate 2 takes it; 3 cleared the floor but has no slot.
    assert result.newly_accepted == [2]
    assert 3 in result.no_slot


def test_withdrawn_candidates_leave_the_denominator():
    assessments = {i: a(i, 100 - i) for i in range(1, 11)}
    result = rank_and_cut(assessments, {}, CFG, "run-1", {}, withdrawn={9, 10})
    assert result.pool_size == 8
    assert result.cap == 1
    assert 9 not in result.waitlist


def test_needs_review_counts_in_pool_but_is_not_ranked():
    assessments = {i: a(i, 100 - i) for i in range(1, 9)}
    result = rank_and_cut(
        assessments, {}, CFG, "run-1", needs_review={9: "unparseable", 10: "missing_resume"}, withdrawn=set()
    )
    assert result.pool_size == 10
    assert result.cap == 2
    assert sorted(result.needs_review) == [9, 10]
    assert 9 not in result.accepted and 9 not in result.waitlist


def test_gated_stays_gated_across_runs():
    ledger = {1: entry(1, "gated", 0.0)}
    ledger[1].gate = "G1"
    assessments = {1: a(1, 95, gate="G1"), 2: a(2, 50), 3: a(3, 40), 4: a(4, 30), 5: a(5, 20)}
    result = rank_and_cut(assessments, ledger, CFG, "run-2", {}, set())
    assert result.gated == [1]
    assert result.newly_gated == []


def test_newly_gated_is_reported():
    ledger = {1: entry(1, "waitlist", 50.0)}
    assessments = {1: a(1, 50, gate="G3"), 2: a(2, 40), 3: a(3, 30), 4: a(4, 20), 5: a(5, 10)}
    result = rank_and_cut(assessments, ledger, CFG, "run-2", {}, set())
    assert result.newly_gated == [1]
    assert ledger[1].status == "gated"


def test_accepted_candidate_who_later_trips_a_gate_is_flagged_not_demoted():
    ledger = {1: entry(1, "accepted", 90.0)}
    assessments = {1: a(1, 90, gate="G1"), 2: a(2, 50), 3: a(3, 40), 4: a(4, 30), 5: a(5, 20)}
    result = rank_and_cut(assessments, ledger, CFG, "run-2", {}, set())
    assert 1 in result.accepted
    assert ledger[1].status == "accepted"
    assert ledger[1].gate == "G1"


def test_timezone_tiebreak_prefers_east_and_central():
    # Equal scores: ET wins over PT.
    assessments = {
        1: a(1, 70, tz="PT"),
        2: a(2, 70, tz="ET"),
        3: a(3, 10),
        4: a(4, 10),
        5: a(5, 10),
    }
    result = rank_and_cut(assessments, {}, CFG, "run-1", {}, set())
    assert result.accepted == [2]


def test_calibration_window_spans_the_cut():
    assessments = {i: a(i, 200 - i) for i in range(1, 51)}
    result = rank_and_cut(assessments, {}, CFG, "run-1", {}, set())
    assert result.cap == 10
    # Window is ranks 5..15 (cut 10, +/- 5).
    assert len(result.calibration_window) == 11
    assert result.calibration_window[0] == 5
    assert result.calibration_window[-1] == 15


def test_calibration_order_reorders_within_the_window_only():
    assessments = {i: a(i, 200 - i) for i in range(1, 21)}
    baseline = rank_and_cut(assessments, {}, CFG, "run-1", {}, set())
    assert baseline.cap == 4
    window = baseline.calibration_window
    reordered = list(reversed(window))
    result = rank_and_cut(
        assessments, {}, CFG, "run-1b", {}, set(), calibration_order=reordered
    )
    assert len(result.accepted) == 4
    # The candidate the agent promoted to the top of the window is now accepted.
    assert reordered[0] in result.accepted


def test_calibration_order_cannot_increase_accepted_count():
    assessments = {i: a(i, 200 - i) for i in range(1, 21)}
    result = rank_and_cut(
        assessments, {}, CFG, "run-1", {}, set(), calibration_order=list(range(1, 21))
    )
    assert len(result.accepted) == result.cap


def test_calibration_order_ignores_ids_outside_the_window():
    assessments = {i: a(i, 200 - i) for i in range(1, 21)}
    baseline = rank_and_cut(assessments, {}, CFG, "run-1", {}, set())
    # Candidate 1 is far above the window and must stay accepted.
    result = rank_and_cut(
        assessments, {}, CFG, "run-1b", {}, set(),
        calibration_order=baseline.calibration_window + [1],
    )
    assert 1 in result.accepted


def test_empty_pool_produces_zero_cap_and_no_crash():
    result = rank_and_cut({}, {}, CFG, "run-1", {}, set())
    assert result.pool_size == 0
    assert result.cap == 0
    assert result.accepted == []


def test_tiny_pool_yields_zero_slots():
    assessments = {i: a(i, 90) for i in range(1, 5)}
    result = rank_and_cut(assessments, {}, CFG, "run-1", {}, set())
    assert result.cap == 0
    assert result.accepted == []
    assert len(result.waitlist) == 4
