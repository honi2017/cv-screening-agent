"""Gates, penalties, and the final score.

Gates eliminate a candidate but never remove them from the pool count — the 20 %
cap is a fraction of everyone who applied, not of everyone who survived.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from screen.config import RoleConfig
from screen.verdict import fit_total

GATE_ORDER = ("G1", "G2", "G3", "G4", "G5")


@dataclass(frozen=True)
class Assessment:
    candidate_id: int
    gate: str | None
    gate_reasons: list[str]
    fit: float
    bonus: float
    penalties: list[dict[str, Any]]
    penalty_total: float
    final: float
    flags: list[str]
    tier2_count: float
    timezone_hint: str
    quote_warnings: list[str] = field(default_factory=list)
    # Reference-only flags (screen.rank._reference_flags): shown on the
    # report for a human to eyeball, never read by compute_penalties and
    # never able to move `final` or `gate`/status -- see the module comment
    # above `_reference_flags` for why that separation is load-bearing.
    reference_flags: list[dict[str, Any]] = field(default_factory=list)


def _judge_flags(verdict: dict[str, Any], tier: int) -> list[dict[str, Any]]:
    return [f for f in verdict["redflag"]["flags"] if int(f["tier"]) == tier]


def tier2_count(precheck: dict[str, Any], verdict: dict[str, Any], cfg: RoleConfig) -> float:
    """Tier-2 signals from the judge plus the two we can count deterministically.

    PDF metadata counts as half a signal: it corroborates, it never convicts.
    """
    count = float(len(_judge_flags(verdict, 2)))

    if precheck.get("skills_count", 0) >= int(cfg.gates["skills_count_threshold"]):
        count += 1.0
    if precheck.get("template_metadata_signal"):
        count += 0.5

    return count


def _effective_years(precheck: dict[str, Any], verdict: dict[str, Any]) -> float | None:
    """Prefer the computed value; fall back to the judge when dates were unclear."""
    computed = precheck.get("years_experience") or {}
    if computed.get("confidence") in {"high", "medium"}:
        return float(computed.get("computed") or 0.0)

    estimate = (verdict.get("redflag") or {}).get("years_experience_estimate") or {}
    if estimate.get("value") is not None:
        return float(estimate["value"])
    if computed.get("computed"):
        return float(computed["computed"])
    return None


def apply_gates(
    precheck: dict[str, Any], verdict: dict[str, Any], cfg: RoleConfig
) -> tuple[str | None, list[str]]:
    """Return the first gate the candidate trips, in G1..G5 order, with reasons."""
    gates: dict[str, list[str]] = {g: [] for g in GATE_ORDER}

    # G1 — Tier 1: unambiguous evidence nobody edited this CV.
    for p in precheck.get("placeholders") or []:
        gates["G1"].append(f"template placeholder left in: {p['match']}")
    for d in precheck.get("intra_cv_duplicate_bullets") or []:
        gates["G1"].append(
            f"duplicate bullets in the same CV (similarity {d['similarity']}): {d['a'][:80]}"
        )
    pool_dups = precheck.get("pool_duplicate_bullets") or []
    if len(pool_dups) >= int(cfg.gates["pool_dup_tier1_count"]):
        others = sorted({d["with_candidate"] for d in pool_dups})
        gates["G1"].append(
            f"{len(pool_dups)} bullets shared verbatim with candidate(s) {others}"
        )
    for f in _judge_flags(verdict, 1):
        gates["G1"].append(f"{f['kind']}: {f['quote'][:100]}")

    # G2 — enough Tier 2 signals to stop being a coincidence.
    t2 = tier2_count(precheck, verdict, cfg)
    if t2 >= float(cfg.gates["tier2_gate_count"]):
        kinds = [f["kind"] for f in _judge_flags(verdict, 2)]
        gates["G2"].append(f"{t2} Tier-2 AI-slop signals ({', '.join(kinds) or 'heuristics'})")

    # G3 — hidden text is deliberate gaming, not carelessness.
    hidden = precheck.get("hidden_text") or {}
    if hidden.get("found"):
        kinds = sorted({s["kind"] for s in hidden.get("spans") or []})
        gates["G3"].append(f"hidden text detected ({', '.join(kinds)})")

    # G4 — below the experience floor.
    years = _effective_years(precheck, verdict)
    minimum = float(cfg.gates["min_years"])
    if years is not None and years < minimum:
        gates["G4"].append(f"{years:.1f} years of experience, below the {minimum:.0f}-year floor")

    # G5 — explicitly outside the US with no authorisation or relocation note.
    #
    # `non_us_explicit` already accounts for work authorisation: signals.find_location
    # (REAL-DATA-ADDENDUM section E) sets it False whenever the CV states US work
    # authorisation OR the ATS sponsorship answer says "no sponsorship needed" --
    # so this gate is suppressed for free by reading that one field. It never
    # eliminates on a "yes" sponsorship answer; that only raises the inert
    # `needs sponsorship` flag in assess() below.
    location = precheck.get("location") or {}
    if location.get("non_us_explicit"):
        raw = location.get("raw")
        detail = f" ({raw})" if raw else ""
        gates["G5"].append(
            "CV indicates non-US residence with no US authorisation or "
            f"relocation note{detail}"
        )

    for gate in GATE_ORDER:
        if gates[gate]:
            return gate, gates[gate]
    return None, []


def compute_penalties(
    precheck: dict[str, Any], verdict: dict[str, Any], cfg: RoleConfig
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    p = cfg.penalties

    linkedin = precheck.get("linkedin") or {}
    if not linkedin.get("present"):
        out.append(
            {
                "kind": "no_linkedin",
                "points": p["no_linkedin"],
                "detail": "no LinkedIn profile in Trakstar or the CV",
            }
        )
    elif linkedin.get("name_matches") is False:
        out.append(
            {
                "kind": "linkedin_name_mismatch",
                "points": p["linkedin_name_mismatch"],
                "detail": f"LinkedIn slug does not match the candidate name ({linkedin.get('url')})",
            }
        )

    # A `liveness` penalty deliberately does NOT exist here. This check used
    # to add a `linkedin_dead` penalty when `liveness == "dead"`; that verdict
    # itself has been retired (see screen.linkedin_check's module docstring)
    # because LinkedIn's HTTP 999 -- the signal "dead" was based on -- turned
    # out to be byte-identical for a fabricated slug and for a real profile
    # that simply isn't public. Run against the real pool it flagged 26 of 54
    # candidates (48%) as "dead", including the top-ranked candidate, purely
    # for having ordinary privacy settings. `"live"` is real, positive
    # evidence and is surfaced in the report's evidence panel (see
    # screen.report); `"unknown"` carries no meaning either way and must
    # never cost a candidate anything. Do not reintroduce a penalty keyed on
    # `liveness` without a measurement as solid as the one that killed this.
    for flag in _judge_flags(verdict, 2):
        out.append(
            {
                "kind": "tier2_signal",
                "points": p["tier2_signal"],
                "detail": f"{flag['kind']}: {flag['quote'][:80]}",
            }
        )

    pool_dups = precheck.get("pool_duplicate_bullets") or []
    if pool_dups:
        others = sorted({d["with_candidate"] for d in pool_dups})
        out.append(
            {
                "kind": "pool_duplicate",
                "points": p["pool_duplicate"],
                "detail": f"{len(pool_dups)} bullet(s) shared with candidate(s) {others}",
            }
        )

    years = _effective_years(precheck, verdict)
    if years is not None and float(cfg.gates["min_years"]) <= years < 5:
        out.append(
            {
                "kind": "years_4_to_5",
                "points": p["years_4_to_5"],
                "detail": f"{years:.1f} years, below the JD's 5-year expectation",
            }
        )

    degree = precheck.get("degree") or {}
    if not degree.get("present"):
        out.append(
            {"kind": "no_degree", "points": p["no_degree"], "detail": "no degree evident in the CV"}
        )

    if int(precheck.get("short_stints_last_5y") or 0) >= 3:
        out.append(
            {
                "kind": "short_stints",
                "points": p["short_stints"],
                "detail": f"{precheck['short_stints_last_5y']} roles under 12 months in the last 5 years",
            }
        )

    # See `_broad_claims_uncorroborated` and the long comment in `assess` --
    # this used to be a report-only flag with no score effect; the hiring
    # team asked for it to carry weight, so it is now a penalty (never a
    # gate) computed from the exact same condition the flag uses.
    if _broad_claims_uncorroborated(precheck, verdict, cfg):
        out.append(
            {
                "kind": "broad_claims",
                "points": p["broad_claims"],
                "detail": "claims strength on nearly every rubric criterion with nothing independently corroborating it",
            }
        )

    return out


_FLAG_CHIPS = {
    "no_linkedin": "no LinkedIn",
    "linkedin_name_mismatch": "LinkedIn mismatch",
    "pool_duplicate": "template dup",
    "years_4_to_5": "4-5 yrs",
    "no_degree": "no degree",
    "short_stints": "short stints",
}


def _high_scoring_criteria_count(verdict: dict[str, Any], cfg: RoleConfig) -> int:
    """How many rubric criteria the judge scored at or above 60% of their max."""
    scores = verdict["fit"]["scores"]
    count = 0
    for key in cfg.criterion_keys():
        entry = scores.get(key)
        if not entry:
            continue
        maximum = cfg.criterion(key).max
        if maximum > 0 and float(entry["score"]) >= 0.6 * maximum:
            count += 1
    return count


def _broad_coverage(verdict: dict[str, Any], cfg: RoleConfig) -> bool:
    """True when the judge scored at or above 60% of max on nearly every
    rubric criterion (all but at most one). Factored out of
    `_broad_claims_uncorroborated` so the `full_criteria_coverage` reference
    flag (see `_reference_flags`) reuses the exact same arithmetic instead of
    duplicating it -- the two must never be able to disagree about what
    counts as "broad".
    """
    total_criteria = len(cfg.criterion_keys())
    high_scoring = _high_scoring_criteria_count(verdict, cfg)
    return high_scoring >= total_criteria - 1


def _broad_claims_uncorroborated(
    precheck: dict[str, Any], verdict: dict[str, Any], cfg: RoleConfig
) -> bool:
    """True when the CV claims strength on nearly every rubric criterion while
    offering nothing independently checkable -- see the long comment in
    `assess` for the history of this signal and why this, not "claims
    all/6-of-7 criteria" alone, is what the deterministic layer safely
    commits to. Computed once here and used for both the `broad_claims`
    penalty (compute_penalties) and the matching report flag (assess) so the
    two can never disagree about which candidates it applies to.
    """
    broad = _broad_coverage(verdict, cfg)
    linkedin_present = bool((precheck.get("linkedin") or {}).get("present"))
    uncorroborated = (not linkedin_present) or tier2_count(precheck, verdict, cfg) >= 1
    return broad and uncorroborated


# --- Reference flags ---------------------------------------------------------
#
# A reference flag is shown on the report for a human to eyeball and NEVER
# affects a score: it is not produced by `compute_penalties`, it cannot gate,
# eliminate, or reorder anyone, and adding one leaves every candidate's
# `final` and status byte-identical (see
# tests/test_rank_gates.py::test_reference_flags_never_change_score_or_status
# for the test that locks this down directly). The hiring manager's own
# words: "Just have a flagging, no points should be affected. Just for
# reference." Each flag below is gated behind its own role.json
# `reference_flags.<kind>` key so either can be switched off with no code
# change (default enabled -- see RoleConfig.reference_flag_enabled).


def _offshore_claim_reference(precheck: dict[str, Any], cfg: RoleConfig) -> dict[str, Any] | None:
    if not cfg.reference_flag_enabled("offshore_claim"):
        return None
    claims = precheck.get("offshore_claims") or []
    if not claims:
        return None
    sentences = [c["sentence"] for c in claims]
    places = sorted({p for c in claims for p in (c.get("places") or [])})
    return {
        "kind": "offshore_claim",
        # Wording note (presentation-only change): this used to read
        # "offshore/nearshore claim (for reference)" -- the hiring manager
        # read "(for reference)" as "a reference was checked", the opposite
        # of the truth (nothing here is verified; see the `detail` string
        # below). "(unverified)" says plainly what this is.
        "label": "offshore claim (unverified)",
        "detail": (
            f"{len(sentences)} sentence(s) claim collaboration with a geographically "
            "separated team -- unverifiable (see code comment), shown for reference "
            "only; the resume link above names the employer"
        ),
        "sentences": sentences,
        "places": places,
    }


def _full_criteria_coverage_reference(
    precheck: dict[str, Any], verdict: dict[str, Any], cfg: RoleConfig
) -> dict[str, Any] | None:
    """Fires when the candidate scores broadly (see `_broad_coverage`) AND the
    `broad_claims` penalty did NOT already fire for them.

    Rationale: when the penalty fires, the situation is already both visible
    (the "broad claims, uncorroborated" chip) and scored -- a human reviewing
    the report has no reason to need this too. This flag exists specifically
    for the case that currently escapes notice entirely: broad coverage on an
    otherwise-clean candidate, which slips past `broad_claims` because that
    penalty additionally requires no verifiable LinkedIn or at least one
    Tier-2 signal. A candidate scoring on every criterion with a clean profile
    and zero flags is either a genuinely excellent match or a well-executed
    rewrite of the job description, and no amount of document analysis tells
    those apart -- that's an interview question, not a scoring question,
    which is exactly why this is reference-only.
    """
    if not cfg.reference_flag_enabled("full_criteria_coverage"):
        return None
    if not _broad_coverage(verdict, cfg):
        return None
    if _broad_claims_uncorroborated(precheck, verdict, cfg):
        return None
    total = len(cfg.criterion_keys())
    high = _high_scoring_criteria_count(verdict, cfg)
    return {
        "kind": "full_criteria_coverage",
        "label": f"claims {high}/{total} criteria",
        "detail": (
            f"scored at or above 60% of max on {high} of {total} rubric criteria "
            "with nothing else flagged -- an interview question, not a scoring one"
        ),
    }


def _reference_flags(
    precheck: dict[str, Any], verdict: dict[str, Any], cfg: RoleConfig
) -> list[dict[str, Any]]:
    flags = [
        _offshore_claim_reference(precheck, cfg),
        _full_criteria_coverage_reference(precheck, verdict, cfg),
    ]
    return [f for f in flags if f is not None]


def assess(precheck: dict[str, Any], verdict: dict[str, Any], cfg: RoleConfig) -> Assessment:
    gate, reasons = apply_gates(precheck, verdict, cfg)
    penalties = compute_penalties(precheck, verdict, cfg)
    penalty_total = float(sum(p["points"] for p in penalties))

    fit = fit_total(verdict)
    bonus = float(verdict["fit"]["bonus"]["points"] or 0)
    final = max(0.0, round(fit + bonus - penalty_total, 2))

    flags: list[str] = []
    for pen in penalties:
        chip = _FLAG_CHIPS.get(pen["kind"])
        if chip and chip not in flags:
            flags.append(chip)
        if pen["kind"] == "tier2_signal":
            chip = f"AI-slop: {pen['detail'].split(':')[0]}"
            if chip not in flags:
                flags.append(chip)
    if precheck.get("gap_over_12m"):
        flags.append("gap")
    location = precheck.get("location") or {}
    if not location.get("us_evident") and not location.get("non_us_explicit"):
        flags.append("location?")
    if location.get("needs_sponsorship"):
        # Information for the human only -- REAL-DATA-ADDENDUM section E4:
        # needing a visa is not evidence of non-US residence, and screening
        # on it is legally sensitive. No score effect, no elimination.
        flags.append("needs sponsorship")
    if verdict.get("quote_warnings"):
        flags.append("quote warning")

    # "Broad claims, uncorroborated" flag -- paired with the `broad_claims`
    # penalty above (both come from `_broad_claims_uncorroborated`, computed
    # once, so the flag and the deduction can never disagree about who they
    # apply to). This replaced an earlier flag that fired on "claims
    # all/6-of-7 criteria" alone, which measurement showed was
    # near-tautological with the final score: it fired on 85% of the top 13
    # candidates by score versus 11% of the rest, because scoring highly on
    # a seven-criterion rubric requires scoring well on most criteria. A
    # flag that just restates the ranking tells a human nothing.
    #
    # What actually discriminates is breadth paired with the *absence* of
    # anything independent corroborating it: a CV that ticks nearly every
    # box, including the rare and specific ones, while offering nothing a
    # reviewer can check independently (no verifiable LinkedIn) or while
    # the judge itself flagged something suspicious (a Tier-2 signal). That
    # combination is what a human reviewer actually caught by eye -- a
    # top-scoring CV that ticked every box and had no verifiable LinkedIn.
    #
    # This was KEPT as a flag with NO score effect and NO gate for a while,
    # for the same reason the old generic_summary/jd_language_mirroring
    # signals were retired: as PENALTIES they fired on 83% of a real sample
    # and eliminated two-thirds of it -- tailoring a CV to a posting is
    # normal and the spec protects it. "Is this breadth genuine, or written
    # to order?" is a judgment call a deterministic rule cannot make safely
    # at scale, and getting it wrong the same way again would dock or
    # eliminate genuinely broad, tailored candidates.
    #
    # The hiring team subsequently reviewed real output and read these CVs
    # as written to match the job description by AI rather than genuinely
    # broad -- a report flag with no score effect wasn't acting on that
    # judgment, so they asked for it to carry weight. It is now
    # `penalties.broad_claims` (Change 3), but deliberately still a
    # PENALTY, not a new gate: it lowers a candidate's rank rather than
    # eliminating them outright, which keeps exactly the safety margin the
    # paragraph above argues for -- a human reading the flagged CV next to
    # its per-criterion quotes (which the report already shows) can still
    # override a rank-lowering that turns out to be wrong; a gate would
    # have foreclosed that entirely.
    if _broad_claims_uncorroborated(precheck, verdict, cfg):
        flags.append("broad claims, uncorroborated")

    return Assessment(
        candidate_id=int(precheck["candidate_id"]),
        gate=gate,
        gate_reasons=reasons,
        fit=fit,
        bonus=bonus,
        penalties=penalties,
        penalty_total=penalty_total,
        final=final,
        flags=flags,
        tier2_count=tier2_count(precheck, verdict, cfg),
        timezone_hint=str(location.get("timezone_hint") or "unknown"),
        quote_warnings=list(verdict.get("quote_warnings") or []),
        reference_flags=_reference_flags(precheck, verdict, cfg),
    )

# --- Ranking and the cap ----------------------------------------------------

import math  # noqa: E402  (grouped with the ranking section)

from screen.ledger import LedgerEntry  # noqa: E402


@dataclass(frozen=True)
class CutResult:
    cap: int
    pool_size: int
    accepted: list[int]
    waitlist: list[int]
    gated: list[int]
    needs_review: list[int]
    newly_accepted: list[int]
    newly_gated: list[int]
    no_slot: list[int]
    calibration_window: list[int]
    quality_floor: float | None
    # Both default so every existing caller (and every `.cut.json` written
    # before this change existed) keeps working unchanged -- see the
    # `rebaseline` parameter on `rank_and_cut` below for what these mean.
    rebaseline: bool = False
    unseated: list[int] = field(default_factory=list)

    # --- H2: observational overage guard --------------------------------
    #
    # `rank_and_cut` re-seats every previously-accepted candidate (see the
    # `already_accepted` seeding above) before it ever consults a score --
    # that stickiness is deliberate: it is what stops the algorithm from
    # displacing someone the team may already have contacted. But nothing
    # in that seeding step compares the resulting seated count back against
    # `cap`, so a pool that shrinks between runs (dropping the cap) can
    # leave more people sticky-seated than the new cap allows, silently.
    #
    # `over_cap` and `accepted_share` below are PURE OBSERVATION: they
    # measure that gap for the report and the run record to surface, and
    # they do not feed back into `accepted`, `waitlist`, gating, or the
    # ledger anywhere in this function. They must never be used to trim or
    # reorder `accepted` -- that would silently convert a sticky-accept
    # into an unseat, which is exactly the behaviour `rebaseline=True`
    # exists to perform explicitly and audibly (see `unseated` above). If
    # a human decides the overage should be corrected, the remedy is an
    # explicit `rank --rebaseline` run -- never a change to these
    # properties.
    #
    # Computed as properties, not stored fields: both are fully derived
    # from `cap`/`accepted`/`pool_size`, which already round-trip through
    # every `.cut.json` (including ones written before this change), so a
    # report regenerated for an old run id computes these correctly with
    # no migration needed.
    @property
    def over_cap(self) -> int:
        """How many more candidates are currently seated than `cap` allows.

        0 when compliant; never negative.
        """
        return max(0, len(self.accepted) - self.cap)

    @property
    def accepted_share(self) -> float:
        """Accepted as a fraction of `pool_size`. 0.0 for an empty pool."""
        return len(self.accepted) / self.pool_size if self.pool_size else 0.0


def _sort_key(assessment: Assessment, cfg: RoleConfig) -> tuple:
    """Score first; the JD's East-Coast/Midwest preference breaks ties."""
    tz_rank = 0 if assessment.timezone_hint in cfg.tiebreak_timezones else 1
    return (-assessment.final, tz_rank, assessment.candidate_id)


def rank_and_cut(
    assessments: dict[int, Assessment],
    ledger: dict[int, LedgerEntry],
    cfg: RoleConfig,
    run_id: str,
    needs_review: dict[int, str],
    withdrawn: set[int],
    calibration_order: list[int] | None = None,
    known_ids: set[int] | None = None,
    rebaseline: bool = False,
) -> CutResult:
    """Rank, apply the cap, and update the ledger in place.

    Pool counts everyone who applied except withdrawals — gated and
    needs-review candidates included — so the 20 % is honest.

    `known_ids` is the full set of candidate ids in the current fetch (the
    pipeline only fetches active candidates, so this is the active pool).
    It lets us tell "still active but gated/needs-review/withdrawn" apart
    from "no longer in the fetch at all" -- the latter means a human
    actioned them in the ATS between runs and they never showed up in
    `withdrawn` because we can no longer see their state. Defaults to
    `None`, meaning "derive everything from the arguments as before and
    mark nothing absent" -- kept for backward compatibility with callers
    that don't have the full id set handy.

    `rebaseline` is the opt-in, explicit escape hatch from stickiness: when
    True, the cut is decided purely by current scores -- no candidate is
    exempt from re-ranking merely because a past run accepted them. It must
    never be inferred (from a rubric bump, a config change, anything) --
    only ever set because a human asked for it on this specific run. See the
    `already_accepted` seeding and the ledger-update loop below for the two
    places that check it, and `CutResult.unseated` for the audit trail it
    produces.
    """
    pool_ids = (set(assessments) | set(needs_review)) - withdrawn
    pool_size = len(pool_ids)
    cap = math.floor(cfg.cap_fraction * pool_size)

    # Snapshot of who the ledger says is accepted *before* anything below
    # mutates it -- used only to build `unseated` (see the ledger-update
    # loop). Cheap to compute unconditionally; only consulted when
    # `rebaseline` is set.
    previously_accepted_in_ledger = {
        cid for cid, entry in ledger.items() if entry.status == "accepted"
    }

    gated: list[int] = []
    rankable: list[Assessment] = []
    for cid, assessment in assessments.items():
        if cid in withdrawn or cid in needs_review:
            continue
        # Under `rebaseline`, the ledger's "accepted" status is being
        # deliberately ignored, so it must not exempt anyone from a gate
        # either -- otherwise "the cut is decided purely by current scores"
        # would be a lie for exactly the candidates this flag exists to
        # re-examine. Gates still apply; only stickiness is switched off.
        previously_accepted = (
            not rebaseline and cid in ledger and ledger[cid].status == "accepted"
        )
        if assessment.gate and not previously_accepted:
            gated.append(cid)
        else:
            rankable.append(assessment)

    rankable.sort(key=lambda a: _sort_key(a, cfg))
    order = [a.candidate_id for a in rankable]

    # The calibration window straddles the cut; the main agent may reorder
    # inside it but cannot pull anyone in from outside or widen the cut.
    # Window is the 2w+1 ranks centred on the cut: 1-indexed ranks
    # (cap - w) .. (cap + w), i.e. 0-indexed slice [cap-w-1 : cap+w].
    w = cfg.calibration_window
    lo, hi = max(0, cap - w - 1), min(len(order), cap + w)
    window = order[lo:hi]

    if calibration_order:
        allowed = set(window)
        proposed = [cid for cid in calibration_order if cid in allowed]
        remainder = [cid for cid in window if cid not in proposed]
        order = order[:lo] + proposed + remainder + order[hi:]

    # `rebaseline` seeds this empty: the whole point is to decide the cut
    # purely by current scores, with no run-order advantage for anyone.
    already_accepted = [] if rebaseline else [
        cid for cid in order if cid in ledger and ledger[cid].status == "accepted"
    ]
    # Consequence, documented deliberately (not an accident): an empty
    # `already_accepted` makes `floor` None below, so the quality floor is
    # OFF for a rebaseline run -- every candidate clears it. The floor
    # normally guards against a slot opened purely by pool growth being
    # filled by a weak brand-new arrival; that scenario doesn't exist when
    # the whole cut is being recomputed from scratch, so suppressing the
    # floor here is correct, not a gap.
    floor: float | None = None
    if already_accepted:
        floor = round(
            min(ledger[cid].final for cid in already_accepted) - cfg.quality_floor_delta, 2
        )

    accepted = list(already_accepted)
    newly_accepted: list[int] = []
    no_slot: list[int] = []

    for cid in order:
        if cid in accepted:
            continue
        assessment = assessments[cid]
        # The floor guards against a slot opened purely by pool growth being
        # filled by a weak brand-new arrival (design doc §6.2 rule 4). A
        # candidate the ledger already had on the waitlist from a prior run
        # was already ranked and vetted; the sticky-ledger promise extends
        # sensibly to not subjecting them to a stricter bar just because the
        # cap grew -- so an existing ledger entry (any non-accepted status;
        # already-accepted candidates are handled separately above) exempts
        # them from the floor.
        clears_floor = floor is None or assessment.final >= floor or cid in ledger
        if len(accepted) < cap and clears_floor:
            accepted.append(cid)
            newly_accepted.append(cid)
        elif clears_floor and len(accepted) >= cap:
            no_slot.append(cid)

    accepted_set = set(accepted)
    waitlist = [cid for cid in order if cid not in accepted_set]

    # Candidates who lost an accepted slot specifically because of this
    # rebaseline run -- the audit trail the run record needs so a future
    # reader sees *why* a status changed. Deliberately empty whenever
    # `rebaseline` is False: stickiness guarantees nobody is ever displaced
    # by the algorithm on an ordinary run, so this list would be a
    # meaningless restatement of unrelated ledger churn (e.g. a candidate
    # withdrawing in the ATS) rather than a rebaseline consequence.
    unseated: list[int] = []
    if rebaseline:
        unseated = sorted(previously_accepted_in_ledger - accepted_set)

    # --- Ledger update ------------------------------------------------------
    newly_gated: list[int] = []

    def status_for(cid: int) -> str:
        if cid in withdrawn:
            return "withdrawn"
        if cid in needs_review:
            return "needs_review"
        if cid in accepted_set:
            return "accepted"
        if cid in gated:
            return "gated"
        return "waitlist"

    for cid in sorted(pool_ids | withdrawn):
        assessment = assessments.get(cid)
        new_status = status_for(cid)
        existing = ledger.get(cid)

        if existing is None:
            ledger[cid] = LedgerEntry(
                candidate_id=cid,
                status=new_status,
                gate=assessment.gate if assessment else None,
                final=assessment.final if assessment else 0.0,
                first_seen_run=run_id,
                status_changed_run=run_id,
                pdf_sha256="",
                trakstar_updated_date="",
            )
            if new_status == "gated":
                newly_gated.append(cid)
            continue

        # Record the gate even on an accepted candidate: the report surfaces it
        # as a flag, and a human decides whether to override.
        if assessment is not None:
            existing.gate = assessment.gate
            if rebaseline or existing.status != "accepted":
                existing.final = assessment.final

        # Sticky-accept is exactly what `rebaseline` opts out of for this
        # run: with it set, a demoted candidate must get a normal status
        # transition below (and an updated `status_changed_run`), not this
        # early exit that would otherwise silently keep them "accepted"
        # forever with no record that anything changed.
        if not rebaseline and existing.status == "accepted":
            continue  # sticky

        if existing.status != new_status:
            existing.status = new_status
            existing.status_changed_run = run_id
            if new_status == "gated":
                newly_gated.append(cid)

    # --- Candidates who vanished from the fetch entirely -------------------
    #
    # The pipeline only fetches active candidates. A candidate who is
    # rejected/hired/withdrawn in the ATS between runs doesn't get relisted
    # with that state -- they just disappear from `candidates.json`, so
    # they're never in `assessments`, never in `needs_review`, and never in
    # `withdrawn` (which is built only from states we can still see). Their
    # ledger entry would otherwise keep its old status forever.
    #
    # Leaving the active pool entirely is a human decision made in the ATS,
    # so it gets the same `withdrawn` treatment as a visible rejection.
    #
    # This is the one case where an `accepted` status is allowed to change.
    # The sticky-accept rule (see `existing.status == "accepted": continue`
    # above) exists to stop the *algorithm* from displacing someone the team
    # may already have contacted -- it was never meant to stop the pipeline
    # from recording that a *human* has since rejected that same person in
    # the ATS. A future reader should not "fix" this by exempting accepted
    # entries here; that would silently keep declined candidates on the
    # shortlist forever.
    if known_ids is not None:
        present_ids = set(assessments) | set(needs_review) | known_ids
        for cid, existing in ledger.items():
            if cid in present_ids or existing.status == "withdrawn":
                continue
            existing.status = "withdrawn"
            existing.status_changed_run = run_id

    # Every scored list is returned in RANKING order — highest final score
    # first, using the same key the cut itself applied so the tiebreak agrees.
    # These previously came back as sorted(...) on the candidate id, which the
    # report then numbered 1..N: the rank column was ordering by database id and
    # a reader saw 75 above 85. `needs_review` stays in id order because those
    # candidates have no verdict and therefore no score to rank by.
    def by_rank(ids: list[int]) -> list[int]:
        scored = [cid for cid in ids if cid in assessments]
        unscored = [cid for cid in ids if cid not in assessments]
        scored.sort(key=lambda cid: _sort_key(assessments[cid], cfg))
        return scored + sorted(unscored)

    return CutResult(
        cap=cap,
        pool_size=pool_size,
        accepted=by_rank(accepted),
        waitlist=waitlist,
        gated=by_rank(gated),
        needs_review=sorted(needs_review),
        newly_accepted=by_rank(newly_accepted),
        newly_gated=by_rank(newly_gated),
        no_slot=by_rank(no_slot),
        calibration_window=window,
        quality_floor=floor,
        rebaseline=rebaseline,
        unseated=unseated,
    )
