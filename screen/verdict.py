"""Validate the JSON a judge subagent returns.

The judge is a language model, so its output is untrusted input. Two rules do
the heavy lifting: every score must be inside its criterion's range, and every
non-zero score must quote text that actually appears in the CV. A quote the
judge invented zeroes that criterion rather than failing the whole candidate —
losing one criterion is recoverable, discarding a real applicant is not.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from screen.config import RoleConfig
from screen.text import contains_quote

VALID_TIERS = (1, 2, 3)


class VerdictError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def verdict_key(redacted_md: str, precheck: dict[str, Any], rubric_version: int) -> str:
    material = f"{redacted_md}\n{precheck.get('precheck_key', '')}\n{rubric_version}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def validate_verdict(raw: dict[str, Any], cfg: RoleConfig, redacted_md: str) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(raw, dict):
        raise VerdictError(["verdict is not a JSON object"])

    if "redflag" not in raw or not isinstance(raw.get("redflag"), dict):
        errors.append("missing 'redflag' object")
    if "fit" not in raw or not isinstance(raw.get("fit"), dict):
        errors.append("missing 'fit' object")
    if errors:
        raise VerdictError(errors)

    fit = raw["fit"]
    redflag = raw["redflag"]
    scores_in = fit.get("scores")
    if not isinstance(scores_in, dict):
        raise VerdictError(["'fit.scores' must be an object keyed by criterion"])

    expected = cfg.criterion_keys()
    for key in expected:
        if key not in scores_in:
            errors.append(f"missing score for criterion '{key}'")
    for key in scores_in:
        if key not in expected:
            errors.append(f"unknown criterion '{key}'")

    clean_scores: dict[str, Any] = {}
    for key in expected:
        entry = scores_in.get(key)
        if not isinstance(entry, dict):
            continue
        try:
            score = float(entry.get("score"))
        except (TypeError, ValueError):
            errors.append(f"criterion '{key}' has a non-numeric score")
            continue

        maximum = cfg.criterion(key).max
        if score < 0:
            errors.append(f"criterion '{key}' score {score} is negative")
            continue
        if score > maximum:
            errors.append(f"criterion '{key}' score {score} exceeds max {maximum}")
            continue

        quote = entry.get("quote")
        if score > 0:
            if not quote or not str(quote).strip():
                warnings.append(f"criterion '{key}': score {score} had no quote — zeroed")
                score = 0.0
            elif not contains_quote(redacted_md, str(quote)):
                warnings.append(
                    f"criterion '{key}': quote not found in CV — zeroed (quote: {str(quote)[:60]!r})"
                )
                score = 0.0

        clean_scores[key] = {
            "score": score,
            "quote": entry.get("quote"),
            "rationale": str(entry.get("rationale") or "").strip(),
        }

    bonus_in = fit.get("bonus") or {"points": 0, "justification": None}
    try:
        bonus_points = float(bonus_in.get("points") or 0)
    except (TypeError, ValueError):
        errors.append("bonus.points is not numeric")
        bonus_points = 0.0
    justification = str(bonus_in.get("justification") or "").strip()
    if bonus_points < 0:
        errors.append("bonus.points is negative")
    if bonus_points > cfg.bonus_max:
        errors.append(f"bonus {bonus_points} exceeds bonus_max {cfg.bonus_max}")
    if bonus_points > 0 and not justification:
        errors.append("bonus awarded without justification")

    clean_flags: list[dict[str, Any]] = []
    for i, flag in enumerate(redflag.get("flags") or []):
        if not isinstance(flag, dict):
            errors.append(f"flag {i} is not an object")
            continue
        try:
            tier = int(flag.get("tier"))
        except (TypeError, ValueError):
            errors.append(f"flag {i} has a non-numeric tier")
            continue
        if tier not in VALID_TIERS:
            errors.append(f"flag {i} has invalid tier {tier} (expected 1, 2, or 3)")
            continue
        quote = str(flag.get("quote") or "").strip()
        if not quote:
            errors.append(f"flag {i} ('{flag.get('kind')}') has no quote")
            continue
        if not contains_quote(redacted_md, quote):
            warnings.append(
                f"flag '{flag.get('kind')}': quote not found in CV — dropped (quote: {quote[:60]!r})"
            )
            continue
        clean_flags.append(
            {
                "tier": tier,
                "kind": str(flag.get("kind") or "unspecified"),
                "quote": quote,
                "explanation": str(flag.get("explanation") or "").strip(),
            }
        )

    if errors:
        raise VerdictError(errors)

    estimate = redflag.get("years_experience_estimate") or {}
    try:
        est_value = float(estimate.get("value")) if estimate.get("value") is not None else None
    except (TypeError, ValueError):
        est_value = None

    return {
        "candidate_id": int(raw.get("candidate_id") or 0),
        "rubric_version": cfg.rubric_version,
        "redflag": {
            "flags": clean_flags,
            "years_experience_estimate": {
                "value": est_value,
                "confidence": str(estimate.get("confidence") or "low"),
            },
        },
        "fit": {
            "scores": clean_scores,
            "bonus": {"points": bonus_points, "justification": justification or None},
            "summary": str(fit.get("summary") or "").strip(),
        },
        "quote_warnings": warnings,
    }


def fit_total(verdict: dict[str, Any]) -> float:
    return round(
        sum(entry["score"] for entry in verdict["fit"]["scores"].values()), 2
    )


def write_verdict(verdict: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(verdict, indent=2))
    tmp.replace(path)


def load_verdict(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
