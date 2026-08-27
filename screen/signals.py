"""Deterministic signal detectors.

Every function here is pure: markdown or parsed values in, facts out. No I/O, no
LLM. Anything requiring judgment ("is this bullet generic?") belongs in the
judge prompts, not here.
"""

from __future__ import annotations

import re
from typing import Any

from screen.config import RoleConfig
from screen.text import find_section, jaccard, normalize

# --- Placeholders -----------------------------------------------------------

# Each entry is (pattern, requires_field_word). Placeholders are a Tier 1 hard
# gate, so these are tuned to prefer false negatives: a missed placeholder still
# reaches the judge, whereas a false hit silently discards a real applicant.
_PLACEHOLDER_PATTERNS = (
    # Bracketed label containing an unambiguous template word.
    (re.compile(r"\[[^\]\n]*\b(?:your|company|position|employer|candidate)\b[^\]\n]*\]", re.I), False),
    # Bare bracketed field label: Title Case or ALL CAPS only, and it must name a
    # known form field. "[Job Title]" and "[FULL NAME]" match; "[job queue]",
    # "[role-based access control]" and "[title]" do not.
    (re.compile(r"\[\s*(?:[A-Z][a-z]+|[A-Z]{2,})(?:[\s_-]+(?:[A-Z][a-z]+|[A-Z]{2,}))*\s*\]"), True),
    # "insert ..." only inside an explicit delimiter. Without this, every CV that
    # mentions a database insert was being eliminated.
    (re.compile(r"[\[\{<]\s*insert\s+[^\]\}>\n]{2,40}[\]\}>]", re.I), False),
    (re.compile(r"lorem\s+ipsum", re.I), False),
    # Unfilled metric placeholders: XX%, X%, NN%.
    (re.compile(r"\b(?:x{1,3}|n{2,3})\s?%", re.I), False),
    # Template engine syntax left behind.
    (re.compile(r"\{\{[^}\n]{1,40}\}\}"), False),
    (re.compile(r"<[A-Z_]{3,30}>"), False),
)

# "school", "university", "state", and "city" are deliberately absent: they
# are components of real institution and place names ("[Ohio State
# University]", "[Penn State]"), not unambiguous form-field labels. The
# genuine placeholders that use them ("[School Name]", "[University Name]")
# are still caught via "name".
_TEMPLATE_FIELD_WORDS = re.compile(
    r"\b(?:name|title|email|phone|address|date|degree)\b", re.I
)


def find_placeholders(text: str) -> list[dict[str, Any]]:
    """Find template text the applicant never replaced."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pattern, requires_field_word in _PLACEHOLDER_PATTERNS:
        for m in pattern.finditer(text):
            match = m.group(0).strip()
            if requires_field_word and not _TEMPLATE_FIELD_WORDS.search(match):
                continue
            key = match.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append({"kind": "placeholder", "match": match})
    return out


# --- Duplicate bullets within one CV ---------------------------------------


def intra_cv_duplicates(
    bullets: list[str], threshold: float, min_words: int = 6
) -> list[dict[str, Any]]:
    """Pairs of bullets in the same CV that are near-identical.

    Bullets with fewer than `min_words` words (after normalization) are
    skipped before comparison. This guards against jaccard()'s numeric
    blindness: jaccard excludes purely-numeric tokens so it can catch the
    lazy-template pattern "...resulting in 40% gains" / "...resulting in 45%
    gains", but the same blindness makes "led team of 5 engineers" and "led
    team of 50 engineers" score as identical (1.0) even though 5 and 50 are
    an order of magnitude apart -- a genuine career-progression pair, not a
    copy-pasted bullet. Since this function feeds a hard-elimination gate, a
    false positive here wrongly eliminates a real applicant. Short bullets
    are exactly where that risk concentrates, so the floor drops them from
    scope rather than trying to teach jaccard about magnitude.
    """
    candidates = [b for b in bullets if len(normalize(b).split()) >= min_words]
    out: list[dict[str, Any]] = []
    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            sim = jaccard(candidates[i], candidates[j])
            if sim >= threshold:
                out.append({"a": candidates[i], "b": candidates[j], "similarity": round(sim, 3)})
    return out


# --- Tier-2 heuristics ------------------------------------------------------

_POWER_VERBS = frozenset(
    """spearheaded orchestrated leveraged facilitated championed pioneered
    revolutionised revolutionized transformed streamlined optimised optimized
    synergised synergized empowered harnessed cultivated fostered drove
    catalysed catalyzed elevated amplified maximised maximized
    """.split()
)

_ROUND_PCT_RE = re.compile(r"\b(\d{1,3})\s?%")
_ANY_METRIC_RE = re.compile(r"\b\d+(?:\.\d+)?\s?%|\b\d[\d,]*\b")


def power_verb_density(bullets: list[str]) -> float:
    """Fraction of bullets whose first word is a generic corporate power verb."""
    if not bullets:
        return 0.0
    hits = 0
    for b in bullets:
        words = normalize(b).split()
        if words and words[0] in _POWER_VERBS:
            hits += 1
    return round(hits / len(bullets), 3)


def round_metric_ratio(bullets: list[str]) -> float:
    """Of the bullets citing a percentage, the fraction using suspiciously round
    multiples of 5. Real measurements are rarely all round numbers.
    """
    with_pct = [b for b in bullets if _ROUND_PCT_RE.search(b)]
    if not with_pct:
        return 0.0
    round_hits = 0
    for b in with_pct:
        values = [int(m.group(1)) for m in _ROUND_PCT_RE.finditer(b)]
        # A decimal percentage (4.1%) is not round; the integer regex skips it
        # only if a decimal point precedes, so check the raw text too.
        if any(f"{v}." in b or f".{v}" in b for v in values):
            continue
        if values and all(v % 5 == 0 for v in values):
            round_hits += 1
    return round(round_hits / len(with_pct), 3)


_SKILL_SPLIT_RE = re.compile(r"[,;|/•\n]+")


def count_skills(markdown: str) -> int:
    """Number of distinct items listed in the skills section."""
    body = find_section(markdown, ["skills", "technical skills", "technologies"])
    if not body:
        return 0
    items = {
        normalize(part)
        for part in _SKILL_SPLIT_RE.split(body)
        if normalize(part) and len(normalize(part)) > 1
    }
    return len(items)


def template_metadata_signal(
    meta: dict[str, Any], minutes_before_submission: float | None, cfg: RoleConfig
) -> bool:
    """True when the PDF came from a template service AND was created just before
    submission. Corroboration only — worth half a Tier-2 signal, never a gate.
    """
    if minutes_before_submission is None:
        return False
    if minutes_before_submission > float(cfg.gates["metadata_minutes_threshold"]):
        return False
    haystack = f"{meta.get('producer', '')} {meta.get('creator', '')}".lower()
    return any(p in haystack for p in cfg.gates["metadata_template_producers"])
