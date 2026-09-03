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


def test_scored_lists_come_back_in_ranking_order_not_id_order():
    """Accepted/gated/delta lists must be ordered by score, never by candidate id.

    This shipped broken: the lists were returned as sorted(...) on the integer
    candidate id, and the report numbers rows 1..N in the order it receives
    them, so a reader saw rank 1 scoring 75 above rank 2 scoring 85. Nothing
    caught it because no test asserted the ordering -- only a human reading the
    rendered page did.

    Ids here are deliberately chosen so id order and score order disagree.
    """
    assessments = {
        100: a(100, 60.0),   # lowest id, lowest score
        300: a(300, 90.0),   # highest id, highest score
        200: a(200, 75.0),
        400: a(400, 50.0, gate="G1"),
        500: a(500, 70.0, gate="G2"),
    }
    result = rank_and_cut(assessments, {}, CFG, "run-1", {}, set())
    accepted_scores = [assessments[c].final for c in result.accepted]
    assert accepted_scores == sorted(accepted_scores, reverse=True), result.accepted
    gated_scores = [assessments[c].final for c in result.gated]
    assert gated_scores == sorted(gated_scores, reverse=True), result.gated
    # and the top-scoring candidate must be first, not the lowest id
    assert result.accepted[0] == 300


def test_needs_review_stays_in_id_order_having_no_score():
    """needs_review candidates have no verdict, so there is no score to rank by."""
    assessments = {300: a(300, 90.0), 100: a(100, 60.0)}
    result = rank_and_cut(
        assessments, {}, CFG, "run-1", needs_review={900: "unparseable", 800: "missing_resume"},
        withdrawn=set(),
    )
    assert result.needs_review == [800, 900]


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


# --- Candidates who vanished from the fetch entirely ------------------------


def test_ledger_entry_absent_from_known_ids_is_marked_withdrawn():
    # Candidate 99 is in the ledger from a prior run but the ATS no longer
    # returns them at all -- a human actioned them between runs.
    ledger = {99: entry(99, "waitlist", 40.0, run="run-0")}
    assessments = {i: a(i, 100 - i) for i in range(1, 6)}
    result = rank_and_cut(
        assessments, ledger, CFG, "run-2", {}, set(), known_ids={1, 2, 3, 4, 5}
    )
    assert ledger[99].status == "withdrawn"
    assert ledger[99].status_changed_run == "run-2"
    assert 99 not in result.accepted
    assert 99 not in result.waitlist


def test_accepted_entry_absent_from_known_ids_is_also_withdrawn():
    # This is the one case where an already-accepted status is allowed to
    # change: sticky-accept protects against the *algorithm* displacing
    # someone, not against the pipeline recording that a *human* rejected
    # that person in the ATS after the fact.
    ledger = {99: entry(99, "accepted", 90.0, run="run-0")}
    assessments = {i: a(i, 100 - i) for i in range(1, 6)}
    result = rank_and_cut(
        assessments, ledger, CFG, "run-2", {}, set(), known_ids={1, 2, 3, 4, 5}
    )
    assert ledger[99].status == "withdrawn"
    assert ledger[99].status_changed_run == "run-2"
    assert 99 not in result.accepted


def test_withdrawn_absent_entry_is_excluded_from_the_ledger_accepted_count():
    # The measured symptom: a stale "accepted" entry for a candidate who left
    # the pool keeps inflating a ledger-wide accepted count forever, which
    # shrinks "cap - accepted" for every future run even though this run's
    # own cut.accepted was never wrong. Marking it withdrawn frees that
    # phantom slot in the ledger itself, not just in this run's output.
    ledger = {
        99: entry(99, "accepted", 90.0, run="run-0"),
        1: entry(1, "accepted", 95.0, run="run-0"),
    }
    assessments = {1: a(1, 95), **{i: a(i, 50 - i) for i in range(2, 6)}}
    result = rank_and_cut(
        assessments, ledger, CFG, "run-2", {}, set(), known_ids={1, 2, 3, 4, 5}
    )
    ledger_accepted_ids = {cid for cid, e in ledger.items() if e.status == "accepted"}
    assert 99 not in ledger_accepted_ids
    assert ledger_accepted_ids == set(result.accepted)


def test_known_ids_none_marks_nothing_absent():
    # Backward-compatible default: existing callers that don't pass
    # known_ids see no behaviour change -- a stale ledger entry for a
    # candidate no longer in `assessments` keeps its old status untouched,
    # exactly as it did before `known_ids` existed.
    ledger = {99: entry(99, "accepted", 90.0, run="run-0")}
    assessments = {i: a(i, 100 - i) for i in range(1, 6)}
    rank_and_cut(assessments, ledger, CFG, "run-2", {}, set())
    assert ledger[99].status == "accepted"
    assert ledger[99].status_changed_run == "run-0"


def test_entries_still_present_in_assessments_are_untouched_by_known_ids():
    ledger = {1: entry(1, "waitlist", 50.0, run="run-0")}
    assessments = {i: a(i, 100 - i) for i in range(1, 6)}
    result = rank_and_cut(
        assessments, ledger, CFG, "run-2", {}, set(), known_ids={1, 2, 3, 4, 5}
    )
    assert ledger[1].status != "withdrawn"
