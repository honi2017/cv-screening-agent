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

# Tier 1 is a hard gate: a hit silently and unappealably rejects a candidate, so
# this fires only on syntax that cannot plausibly occur in genuine CV prose.
#
# Deliberately absent, after five successive designs were each defeated by a
# realistic phrase:
#   - two-word bracketed field labels ("[Company Name]") — grammatically
#     identical to legitimate domain objects ("[Tracking Number]", "[IP Address]")
#   - ALL_CAPS angle-bracket tokens ("<COMPANY_NAME>") — identical in shape to the
#     env-var references an integration engineer cites ("<DB_NAME>", "<API_KEY>")
# Both classes now belong to the RedFlag judge pass, which can read the
# surrounding sentence. Every failed design keyed on "a field word appears inside
# the delimiter"; that approach cannot work, because engineers legitimately
# bracket things whose names contain those words. Do not reintroduce one.
_PLACEHOLDER_PATTERNS = (
    # Second person: nobody names a domain object "[Your Company]".
    re.compile(r"\[\s*your\b[^\]\n]{0,30}\]", re.I),
    # Imperative, and only inside an explicit delimiter — a bare "insert" is a
    # database verb ("batch insert operations").
    re.compile(r"[\[\{<]\s*insert\s+[^\]\}>\n]{2,40}[\]\}>]", re.I),
    re.compile(r"lorem\s+ipsum", re.I),
    re.compile(r"\b(?:x{1,3}|n{2,3})\s?%", re.I),
    re.compile(r"\{\{[^}\n]{1,40}\}\}"),
)


def find_placeholders(text: str) -> list[dict[str, Any]]:
    """Find template text the applicant never replaced."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pattern in _PLACEHOLDER_PATTERNS:
        for m in pattern.finditer(text):
            match = m.group(0).strip()
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
    return any(str(p).lower() in haystack for p in cfg.gates["metadata_template_producers"])


# --- Employment dates ------------------------------------------------------

from dataclasses import dataclass  # noqa: E402  (grouped with the date section)

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

_PRESENT = re.compile(r"\b(present|current|now|today|ongoing)\b", re.I)
_SEP = r"(?:\s*(?:-|–|—|to|until|through)\s*)"

# The two-digit branch (1[0-2]) MUST come before the single-digit branch
# (0?[1-9]) in this alternation. Python's re alternation takes the first
# matching branch, not the longest; "0?[1-9]" alone happily matches just the
# "1" in "12" and stops there. For the *start* date that wrong short match
# gets corrected by backtracking, because something must still match after
# it (_SEP). But the *end* date's month-num is the last construct in
# _RANGE_RE — nothing downstream ever fails to force a retry — so an
# end-month of Oct/Nov/Dec silently truncated to "1" (January) and was never
# corrected. Confirmed empirically: "(2019-01 - 2019-12)" parsed as ending
# 2019-01 with this order reversed. Trying the two-digit branch first fixes
# it without changing what the pattern matches for single-digit months.
_MY = r"(?:(?P<{p}mon>[A-Za-z]{{3,9}})\.?\s+)?(?P<{p}year>(?:19|20)\d{{2}})(?:\s*[-/]\s*(?P<{p}num>1[0-2]|0?[1-9]))?"

_RANGE_RE = re.compile(
    _MY.format(p="s") + _SEP + r"(?:" + _MY.format(p="e") + r"|(?P<present>present|current|now|today|ongoing))",
    re.I,
)


@dataclass(frozen=True)
class DateRange:
    start: tuple[int, int]
    end: tuple[int, int] | None  # None means "Present"
    precise: bool
    raw: str


def _month_from(mon_name: str | None, mon_num: str | None) -> tuple[int, bool]:
    if mon_num:
        return int(mon_num), True
    if mon_name and mon_name.lower() in _MONTHS:
        return _MONTHS[mon_name.lower()], True
    return 1, False


def extract_date_ranges(markdown: str) -> list[DateRange]:
    """Pull employment date ranges out of the experience section only."""
    body = find_section(
        markdown, ["experience", "employment", "work history", "professional"]
    )
    if body is None:
        body = markdown if find_section(markdown, ["education"]) is None else ""
    if not body:
        return []

    out: list[DateRange] = []
    for m in _RANGE_RE.finditer(body):
        s_year = int(m.group("syear"))
        s_month, s_precise = _month_from(m.group("smon"), m.group("snum"))

        if m.group("present"):
            end: tuple[int, int] | None = None
            e_precise = True
        else:
            if not m.group("eyear"):
                continue
            e_year = int(m.group("eyear"))
            e_month, e_precise = _month_from(m.group("emon"), m.group("enum"))
            end = (e_year, e_month)

        out.append(
            DateRange(
                start=(s_year, s_month),
                end=end,
                precise=s_precise and e_precise,
                raw=m.group(0).strip(),
            )
        )
    return out


def _to_months(ym: tuple[int, int]) -> int:
    return ym[0] * 12 + (ym[1] - 1)


def _merged_intervals(
    ranges: list[DateRange], today: tuple[int, int]
) -> list[tuple[int, int]]:
    intervals = []
    for r in ranges:
        start = _to_months(r.start)
        end = _to_months(r.end if r.end else today)
        if end >= start:
            intervals.append((start, end))
    if not intervals:
        return []
    intervals.sort()
    merged = [intervals[0]]
    for start, end in intervals[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + 1:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def compute_years(markdown: str, today: tuple[int, int]) -> dict[str, Any]:
    """Total professional experience in years from the union of date ranges.

    Confidence: high when 2+ ranges all carry months; medium when 2+ ranges but
    some are year-only; low with fewer than 2 ranges (the judge's estimate wins).
    """
    ranges = extract_date_ranges(markdown)
    merged = _merged_intervals(ranges, today)
    months = sum(end - start + 1 for start, end in merged)
    years = round(months / 12, 2)

    if len(ranges) < 2:
        confidence = "low"
    elif all(r.precise for r in ranges):
        confidence = "high"
    else:
        confidence = "medium"

    return {
        "computed": years if ranges else 0.0,
        "confidence": confidence,
        "ranges": [
            [f"{r.start[0]:04d}-{r.start[1]:02d}",
             "present" if r.end is None else f"{r.end[0]:04d}-{r.end[1]:02d}"]
            for r in ranges
        ],
    }


def short_stints(ranges: list[DateRange], today: tuple[int, int], years_back: int) -> int:
    """Completed roles shorter than 12 months that started within the window.

    The current role is excluded — it is short only because it is ongoing.
    """
    cutoff = _to_months(today) - years_back * 12
    count = 0
    for r in ranges:
        if r.end is None:
            continue
        start, end = _to_months(r.start), _to_months(r.end)
        if start >= cutoff and (end - start + 1) < 12:
            count += 1
    return count


def has_gap_over(
    ranges: list[DateRange], today: tuple[int, int], months: int, years_back: int
) -> bool:
    """True when consecutive roles inside the window are separated by a gap."""
    cutoff = _to_months(today) - years_back * 12
    merged = [iv for iv in _merged_intervals(ranges, today) if iv[1] >= cutoff]
    if len(merged) < 2:
        return False
    for (_, prev_end), (next_start, _) in zip(merged, merged[1:]):
        if next_start - prev_end - 1 >= months:
            return True
    return False
