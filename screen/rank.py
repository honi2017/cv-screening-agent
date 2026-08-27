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

    return out


_FLAG_CHIPS = {
    "no_linkedin": "no LinkedIn",
    "linkedin_name_mismatch": "LinkedIn mismatch",
    "pool_duplicate": "template dup",
    "years_4_to_5": "4-5 yrs",
    "no_degree": "no degree",
    "short_stints": "short stints",
}


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
) -> CutResult:
    """Rank, apply the cap, and update the ledger in place.

    Pool counts everyone who applied except withdrawals — gated and
    needs-review candidates included — so the 20 % is honest.
    """
    pool_ids = (set(assessments) | set(needs_review)) - withdrawn
    pool_size = len(pool_ids)
    cap = math.floor(cfg.cap_fraction * pool_size)

    gated: list[int] = []
    rankable: list[Assessment] = []
    for cid, assessment in assessments.items():
        if cid in withdrawn or cid in needs_review:
            continue
        previously_accepted = (
            cid in ledger and ledger[cid].status == "accepted"
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

    already_accepted = [
        cid for cid in order if cid in ledger and ledger[cid].status == "accepted"
    ]
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
            if existing.status != "accepted":
                existing.final = assessment.final

        if existing.status == "accepted":
            continue  # sticky

        if existing.status != new_status:
            existing.status = new_status
            existing.status_changed_run = run_id
            if new_status == "gated":
                newly_gated.append(cid)

    return CutResult(
        cap=cap,
        pool_size=pool_size,
        accepted=sorted(accepted),
        waitlist=waitlist,
        gated=sorted(gated),
        needs_review=sorted(needs_review),
        newly_accepted=sorted(newly_accepted),
        newly_gated=sorted(newly_gated),
        no_slot=sorted(no_slot),
        calibration_window=window,
        quality_floor=floor,
    )
