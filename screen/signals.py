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


def _bullets_with_metrics(bullets: list[str]) -> list[str]:
    """Bullets citing a percentage -- the one definition of "a metric" shared
    by `round_metric_ratio` and `metric_count`. Factored out so the ratio's
    denominator and the count can never disagree about what counts as a
    metric; do not give either of them its own regex.
    """
    return [b for b in bullets if _ROUND_PCT_RE.search(b)]


def metric_count(bullets: list[str]) -> int:
    """Number of bullets citing a percentage metric -- the same population
    `round_metric_ratio` computes its ratio over. Consumed by
    `screen.rank._all_metrics_round` as the minimum-metrics guard: a ratio of
    1.0 over a single metric means nothing.
    """
    return len(_bullets_with_metrics(bullets))


def round_metric_ratio(bullets: list[str]) -> float:
    """Of the bullets citing a percentage, the fraction using suspiciously round
    multiples of 5. Real measurements are rarely all round numbers.
    """
    with_pct = _bullets_with_metrics(bullets)
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
    some are year-only; low with fewer than 2 ranges, or when any range is
    malformed (the judge's estimate wins in all of those cases).
    """
    ranges = extract_date_ranges(markdown)
    merged = _merged_intervals(ranges, today)
    months = sum(end - start + 1 for start, end in merged)
    years = round(months / 12, 2)

    # A range whose end precedes its start is a typo (a transposed "2021-01 -
    # 2019-01"). _merged_intervals already drops it from the total, but dropping
    # it silently while still reporting "high" confidence would hide an
    # undercount from gate G4 -- and "high" is precisely when the gate stops
    # consulting the judge's own estimate. An unparseable range means the
    # deterministic answer is untrustworthy, so hand the decision to the judge
    # instead.
    malformed = [
        r for r in ranges
        if r.end is not None and _to_months(r.end) < _to_months(r.start)
    ]

    if malformed or len(ranges) < 2:
        confidence = "low"
    elif all(r.precise for r in ranges):
        confidence = "high"
    else:
        confidence = "medium"

    return {
        "computed": years if ranges else 0.0,
        "confidence": confidence,
        "malformed_ranges": len(malformed),
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


# --- LinkedIn, degree, location --------------------------------------------

_LINKEDIN_RE = re.compile(
    r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/(?:in|pub)/([A-Za-z0-9\-_%]+)", re.I
)

_DEGREE_PATTERNS = (
    (re.compile(r"\b(?:ph\.?d|doctorate)\b", re.I), "PhD"),
    # m\.?s\.? (not m\.?s\.): the sole other bare-letter alternative, BA below,
    # already makes its trailing period optional (b\.?a\.?). Leaving this one's
    # period mandatory meant a bare "MS" -- at least as common on real resumes
    # as "MSc" -- silently failed to register as a degree at all, undercounting
    # a real degree holder. See BSc below for the identical fix.
    (re.compile(r"\b(?:m\.?sc|msc|m\.?s\.?|master(?:'s|s)?(?:\s+of\s+\w+)?)\b", re.I), "MSc"),
    (re.compile(r"\bm\.?eng\b", re.I), "MEng"),
    (re.compile(r"\bmba\b", re.I), "MBA"),
    # b\.?s\.? (not b\.?s\.): same fix as MSc above -- bare "BS" (no trailing
    # period) is at least as common as "BSc" on US resumes and was previously
    # invisible to this detector, wrongly zeroing out `present` (and the -5pt
    # penalty is *not* an elimination, so the safer failure mode is to err
    # toward crediting a plausible bare "BS"/"MS" than to keep missing it).
    (re.compile(r"\b(?:b\.?sc|bsc|b\.?s\.?|bachelor(?:'s|s)?(?:\s+of\s+\w+)?)\b", re.I), "BSc"),
    (re.compile(r"\bb\.?eng\b", re.I), "BEng"),
    (re.compile(r"\bb\.?tech\b", re.I), "BTech"),
    (re.compile(r"\bb\.?a\.?\b", re.I), "BA"),
)

_FIELD_RE = re.compile(
    r"\b(?:in|of)\s+([A-Z][A-Za-z&\s]{2,40}?)(?:,|\.|\n|$)|"
    r"(computer science|software engineering|information systems|computer engineering|"
    r"electrical engineering|mathematics|physics|information technology|data science)",
    re.I,
)

_ET_STATES = frozenset("ME NH VT MA RI CT NY NJ PA DE MD DC VA WV NC SC GA FL OH MI IN".split())
_CT_STATES = frozenset("IL WI MN IA MO AR LA MS AL TN KY KS NE SD ND OK TX".split())
_MT_STATES = frozenset("MT WY CO NM UT ID AZ".split())
_PT_STATES = frozenset("WA OR CA NV AK HI".split())

# Merged code->timezone lookup, reused by the per-field state-of-residence
# matcher below so each two-letter code's timezone is defined in exactly one
# place.
_STATE_CODE_TZ = {
    code: tz
    for tz, codes in (
        ("ET", _ET_STATES), ("CT", _CT_STATES), ("MT", _MT_STATES), ("PT", _PT_STATES),
    )
    for code in codes
}

_US_HINT_RE = re.compile(
    r",\s*([A-Z]{2})\b(?:\s+\d{5})?"
    r"|\b(United States|USA|U\.S\.A\.?|U\.S\.?|US)(?!\w)"
)

# Spelled-out US state names, mapped to timezone. _US_HINT_RE only recognises
# two-letter codes and "United States"/"USA"/"US", so "Boston, Massachusetts"
# previously yielded unknown location. This also resolves the class of US places
# whose names end in a country: "Santa Fe, New Mexico" was being eliminated by
# the endswith country check, as were the real US towns "Mexico, Missouri",
# "Denmark, South Carolina", "China, Maine", "Norway, Maine" and "Italy, Texas".
#
# "georgia" is deliberately ABSENT: it is the one US state name that is also a
# sovereign country, so "Batumi, Georgia" is irreducibly ambiguous -- no
# anchoring rule can tell US Georgia from the country. The two-letter code GA
# still resolves US Georgia via _US_HINT_RE, so "Atlanta, GA" keeps working;
# the deliberate trade is that a spelled-out "Atlanta, Georgia" now yields
# unknown location -- a flag, not an elimination, which is the safe failure.
_US_STATE_TIMEZONES = {
    "alabama": "CT", "alaska": "PT", "arizona": "MT", "arkansas": "CT",
    "california": "PT", "colorado": "MT", "connecticut": "ET", "delaware": "ET",
    "florida": "ET", "hawaii": "PT", "idaho": "MT",
    "illinois": "CT", "indiana": "ET", "iowa": "CT", "kansas": "CT",
    "kentucky": "ET", "louisiana": "CT", "maine": "ET", "maryland": "ET",
    "massachusetts": "ET", "michigan": "ET", "minnesota": "CT",
    "mississippi": "CT", "missouri": "CT", "montana": "MT", "nebraska": "CT",
    "nevada": "PT", "new hampshire": "ET", "new jersey": "ET",
    "new mexico": "MT", "new york": "ET", "north carolina": "ET",
    "north dakota": "CT", "ohio": "ET", "oklahoma": "CT", "oregon": "PT",
    "pennsylvania": "ET", "rhode island": "ET", "south carolina": "ET",
    "south dakota": "CT", "tennessee": "CT", "texas": "CT", "utah": "MT",
    "vermont": "ET", "virginia": "ET", "washington": "PT",
    "west virginia": "ET", "wisconsin": "CT", "wyoming": "MT",
    "district of columbia": "ET",
}
# A state name must occupy the "City, State" slot, exactly as the two-letter code
# does. Without the comma anchor this fired on a foreign place sharing a state
# name ("Washington, United Kingdom" — a real English village) and cancelled a
# correct non-US detection, and it fired on a candidate's own first name
# ("Georgia Martinez"), fabricating a timezone from no location at all.
# Longest first so "new mexico" wins over nothing and "west virginia" over "virginia".
_US_STATE_NAME_RE = re.compile(
    r",\s*(" + "|".join(sorted(_US_STATE_TIMEZONES, key=len, reverse=True)) + r")\b",
    re.I,
)

# --- Per-field state matching for the ATS "state of residence" answer -----
#
# The rule above is deliberately comma-anchored for CV prose (see its comment).
# This one is NOT, and that is deliberate too, and it is scoped to exactly one
# field: the real ATS's required "Please specify your current state of
# residence in the US" answer (REAL-DATA-ADDENDUM section E) is never "City,
# State" prose -- its value IS the state, verbatim examples include "Texas",
# "FL", "Friendswood texas" -- so the comma-anchored regex above never matches
# it and this authoritative field would otherwise be silently ignored.
#
# Do NOT reuse this matcher on free CV text or any other ATS field: an
# unanchored state name there is exactly the bug _US_STATE_NAME_RE's comma
# anchor exists to prevent (see its comment above) -- it cancelled genuine
# non-US detections and fabricated timezones from candidates' own first
# names. This function is per-field and must stay that way.
_US_STATE_NAME_UNANCHORED_RE = re.compile(
    r"\b(" + "|".join(sorted(_US_STATE_TIMEZONES, key=len, reverse=True)) + r")\b",
    re.I,
)


def _match_state_of_residence(value: str | None) -> str | None:
    """Timezone for a bare state-of-residence answer, matched unanchored and
    case-insensitively -- see the comment above for why this is safe only for
    that one ATS field.
    """
    if not value:
        return None
    stripped = value.strip()
    tokens = re.findall(r"[A-Za-z]+", stripped)
    # A single bare two-letter token ("FL", "ny") is unambiguously a state
    # code here -- the whole answer is nothing else. Inside a longer answer
    # ("Friendswood texas") only an UPPERCASE two-letter token counts as a
    # code, so an incidental lowercase connector word can never be misread as
    # one; the full-name regex below still resolves those answers via the
    # state's spelled-out name.
    if len(tokens) == 1 and len(tokens[0]) == 2:
        tz = _STATE_CODE_TZ.get(tokens[0].upper())
        if tz:
            return tz
    else:
        for token in tokens:
            if len(token) == 2 and token.isupper():
                tz = _STATE_CODE_TZ.get(token)
                if tz:
                    return tz
    m = _US_STATE_NAME_UNANCHORED_RE.search(stripped)
    if m:
        return _US_STATE_TIMEZONES[m.group(1).lower()]
    return None


def _state_residence_value(profile_data: list[dict[str, Any]]) -> str | None:
    for item in profile_data or []:
        if _normalize_label(item.get("name", "")) == _STATE_RESIDENCE_LABEL:
            value = str(item.get("value", "")).strip()
            if value:
                return value
    return None


# "viet nam" is ordered before "vietnam" and "united kingdom" stays ahead of
# any of its own substrings: `_is_residence_evidence` below matches a value
# against this tuple with `endswith`, which is order-independent for `any()`
# as things stand today, but if this ever grows an entry that IS a genuine
# substring of another (e.g. a short alias), checking the longer/more
# specific spelling first is what keeps a future rewrite (e.g. reporting
# WHICH country matched, not just whether one did) from picking a truncated
# form. Costs nothing to order defensively now.
_NON_US_COUNTRIES = (
    "viet nam", "vietnam", "india", "united kingdom", "canada", "england", "germany",
    "france", "singapore", "australia", "brazil", "mexico", "philippines", "poland",
    "ukraine", "nigeria", "pakistan", "bangladesh", "china", "japan", "korea",
    "netherlands", "spain", "italy", "ireland", "sweden", "norway", "denmark",
)

_WORK_AUTH_RE = re.compile(
    r"\b(?:us|u\.s\.)\s*(?:work\s*)?(?:authoriz|authoris|citizen|permanent resident|green card)"
    r"|\bauthorized to work in the (?:us|united states)\b"
    r"|\brelocat(?:e|ing|ion)\b",
    re.I,
)


# The candidate's residence drives gate G5, a hard elimination, so the location
# field is identified by an EXACT label match — never by needle matching. Both
# substring and whole-word matching were tried and both let unrelated fields
# masquerade as the location: "Ethnicity" contains "city", "Relocation" contains
# "location", and "Interview Location", "Office Location", "Previous Address" and
# "Email Address" all contain a location word for a non-residence reason. Each
# shadowed the real Location field and eliminated the candidate.
#
# An unrecognised label is the SAFE failure: it yields None, which falls back to
# the CV contact header and then to unknown location — and unknown location
# flags rather than eliminates. Widening this back into a needle match would
# reverse that, so don't.
# The real ATS's required residence question (REAL-DATA-ADDENDUM section E).
# Its value is authoritative -- unlike the labels below, whose values are
# free-form CV-style addresses, this field's value IS the state, so it gets
# its own unanchored matching path (_match_state_of_residence, above) rather
# than the comma-anchored "City, State" regexes used for the rest.
_STATE_RESIDENCE_LABEL = "please specify your current state of residence in the us"

_LOCATION_LABELS = frozenset({
    "location", "current location", "candidate location", "location city state",
    "city", "current city", "city town", "town",
    "address", "home address", "mailing address", "street address", "current address",
    "based in", "country", "current country", "state province",
    _STATE_RESIDENCE_LABEL,
})
_LABEL_NORM_RE = re.compile(r"[^a-z0-9]+")
# A bare domain is not a place, whatever the field label says.
_NOT_A_PLACE_RE = re.compile(
    r"@|://|\bhttps?\b|\b[a-z0-9-]+\.(?:com|net|org|io|co|dev|ai|vn|uk|de)\b", re.I
)


def _normalize_label(name: Any) -> str:
    """Fold an ATS field label to a comparable form: lowercase, punctuation
    and whitespace collapsed to single spaces, leading/trailing space
    stripped. The real API's labels are inconsistently capitalised and at
    least one carries a stray leading space -- always compare through this,
    never the raw label.
    """
    return _LABEL_NORM_RE.sub(" ", str(name).lower()).strip()


def _profile_value(profile_data: list[dict[str, Any]]) -> str | None:
    for item in profile_data or []:
        label = _normalize_label(item.get("name", ""))
        if label in _LOCATION_LABELS:
            value = str(item.get("value", "")).strip()
            if value and not _NOT_A_PLACE_RE.search(value):
                return value
    return None


# Words signalling intent or preference rather than current residence.
_INTENT_RE = re.compile(
    r"\b(?:open|willing|interested|available|relocat\w*|prefer\w*|seeking|looking|remote|office)\b",
    re.I,
)


def _is_residence_evidence(value: str) -> bool:
    """True when `value` reads like an address rather than prose about a place.

    `non_us_explicit` drives gate G5, a hard elimination, and was previously set
    by a bare substring scan for a country name. That eliminated candidates whose
    location field held free text ("Interested in opportunities across Singapore
    and Vietnam") or even an explicit relocation offer ("Willing to relocate to
    our Singapore office"), and read an email domain as a country of residence.
    A real address ends with the place, in a short trailing component: "Hanoi,
    Vietnam", "Ho Chi Minh City, Vietnam", "London, United Kingdom", "Vietnam".
    Prose does not.
    """
    if not value or _NOT_A_PLACE_RE.search(value) or _INTENT_RE.search(value):
        return False
    normalised = value.strip().rstrip(".").lower()
    tail = normalised.split(",")[-1].strip()
    if len(tail.split()) > 3:
        return False
    return any(normalised.endswith(country) for country in _NON_US_COUNTRIES)


# --- ATS structured answers: sponsorship and region (REAL-DATA-ADDENDUM E) -
#
# The real ATS asks every applicant two more required questions beyond
# location and LinkedIn. Both are handled here, next to the location logic
# they feed, rather than as a separate module -- find_location is where
# their evidence is combined with everything else into one `non_us_explicit`
# / `timezone_hint` decision.
_SPONSORSHIP_LABEL = _normalize_label(
    "Will you now or in the future require sponsorship for employment visa "
    "status (e.g., H-1B visa status)?"
)

# Verbatim-normalised values meaning "sponsorship is NOT needed", i.e.
# affirmative evidence of US work authorisation. Deliberately NOT stripped of
# punctuation the way labels are -- "no." and "n/a" are compared as written,
# just lowercased and trimmed -- because the addendum's own measured example
# values are this exact, short list.
_SPONSORSHIP_NOT_NEEDED = frozenset({"no", "no.", "n/a", "none", "not required"})


def _sponsorship_value(profile_data: list[dict[str, Any]]) -> str | None:
    for item in profile_data or []:
        if _normalize_label(item.get("name", "")) == _SPONSORSHIP_LABEL:
            value = str(item.get("value", "")).strip()
            if value:
                return value
    return None


def _sponsorship_evidence(value: str | None) -> tuple[bool, bool]:
    """(work_authorized, needs_sponsorship) from the raw sponsorship answer.

    "No" (in its several verbatim spellings) is affirmative evidence of US
    work authorisation and suppresses gate G5 entirely -- exactly like a
    CV-prose work-auth phrase already does, below. "Yes" is NEVER used to
    eliminate: needing a visa is not evidence of non-US residence, and
    screening on it is legally sensitive. It only raises a flag
    (`needs_sponsorship`) for a human to weigh -- it must never set
    `non_us_explicit`.
    """
    if not value:
        return False, False
    norm = value.strip().lower()
    if norm in _SPONSORSHIP_NOT_NEEDED:
        return True, False
    if norm.startswith("yes"):
        return False, True
    return False, False


# The region question's answers are measured to be too messy to trust on
# their own ("PST", "YES" were both observed) -- see REAL-DATA-ADDENDUM
# section E. It is corroboration only: it NEVER sets us_evident or
# non_us_explicit, and only fills in timezone_hint (used by the tiebreak in
# screen.rank) as a last resort, after the authoritative state-of-residence
# field and the CV-text paths have both had a chance to resolve it.
_REGION_LABEL = _normalize_label(
    "Which region of the US are you based in? (e.g., Northeast, Midwest, "
    "East Coast, South, West)"
)

_REGION_TIMEZONE_HINTS = (
    # Longest/most specific phrase first so "east coast" wins over a bare
    # "east" appearing inside it, etc.
    ("northeast", "ET"), ("east coast", "ET"), ("mid atlantic", "ET"),
    ("midwest", "CT"), ("south", "CT"), ("mountain", "MT"),
    ("west coast", "PT"), ("west", "PT"),
    ("est", "ET"), ("edt", "ET"), ("pst", "PT"), ("pdt", "PT"),
    ("cst", "CT"), ("cdt", "CT"), ("mst", "MT"), ("mdt", "MT"),
)


def _region_value(profile_data: list[dict[str, Any]]) -> str | None:
    for item in profile_data or []:
        if _normalize_label(item.get("name", "")) == _REGION_LABEL:
            value = str(item.get("value", "")).strip()
            if value:
                return value
    return None


def _region_timezone_hint(value: str | None) -> str | None:
    if not value:
        return None
    low = value.lower()
    for phrase, tz in _REGION_TIMEZONE_HINTS:
        if phrase in low:
            return tz
    return None


# LinkedIn gets the same exact-label treatment as location, for the same
# reason (a needle like "linkedin" is safe here, but staying consistent
# costs nothing) -- though a wrong match here only costs an -8pt penalty,
# never an elimination, so it doesn't need the value guard above.
_LINKEDIN_LABELS = frozenset({"linkedin", "linkedin profile", "linkedin url"})


def _linkedin_profile_value(profile_data: list[dict[str, Any]]) -> str | None:
    for item in profile_data or []:
        label = _normalize_label(item.get("name", ""))
        if label in _LINKEDIN_LABELS:
            value = str(item.get("value", "")).strip()
            if value:
                return value
    return None


def _slug_matches_name(slug: str, full_name: str) -> bool | None:
    """True/False when the slug carries name-like tokens, None when opaque."""
    slug_lower = slug.lower()
    slug_tokens = {t for t in re.split(r"[-_%\d]+", slug_lower) if len(t) > 2}
    if not slug_tokens:
        return None
    name_token_list = [t for t in normalize(full_name).split() if len(t) > 2]
    name_tokens = set(name_token_list)
    if not name_tokens:
        return None
    if slug_tokens & name_tokens:
        return True
    # Vanity slugs very commonly concatenate the name with no separator at all
    # ("alexmorgan", "morganalex" for "Alex Morgan") -- the split above only
    # breaks on "-", "_", "%", and digits, so a bare concatenation never
    # tokenizes and would otherwise fall through to the mismatch branch below
    # and penalise a legitimate profile. Require an EXACT match of the whole
    # (digit-stripped) slug against the full name concatenated forwards or
    # backwards, rather than a substring check, so this can't be tricked into
    # matching an unrelated slug that merely contains a name-like fragment.
    slug_clean = re.sub(r"[-_%\d]+", "", slug_lower)
    if slug_clean and slug_clean in ("".join(name_token_list), "".join(reversed(name_token_list))):
        return True
    # Slug has real words but none of them are the candidate's name.
    if any(len(t) > 3 for t in slug_tokens):
        return False
    return None


# A slug that _LINKEDIN_RE technically captures but that is obviously a
# template placeholder rather than anyone's real vanity handle -- "linkedin.com
# /in/your-name" is not a profile any more than "no" is. Kept short and
# generic on purpose: this is a backstop against unfilled-template slugs, not
# an attempt to guess every bad slug a human might type.
_PLACEHOLDER_SLUGS = frozenset({
    "yourname", "your-name", "yourprofile", "your-profile", "username",
    "profile", "linkedin", "url", "link", "na", "n-a", "none", "tbd",
    "placeholder", "example", "firstname-lastname",
})


def _extract_linkedin_profile(value: str | None) -> tuple[str, str] | None:
    """(matched substring, slug) for the first real profile path inside
    `value`, or None when it contains no such path.

    Uses `re.search`, deliberately not a whole-string parse: a genuine
    profile identifier is sometimes embedded inside an unrelated wrapper --
    e.g. a mangled paste of a confirmation-page redirect,
    "https://example.com/confirmation//www.linkedin.com/in/<slug>" -- where
    the profile path is real even though the surrounding string is not
    itself a valid URL. Searching, rather than requiring the whole value to
    parse as a URL, extracts that embedded identifier instead of discarding
    the whole string.

    A match whose slug is empty, shorter than LinkedIn's own 3-character
    minimum for a public-profile slug, or one of the known placeholder
    tokens (see `_PLACEHOLDER_SLUGS`) does not count -- that is template
    text (or, measured in the real data, someone typing "n/a" into the
    LinkedIn field: "https://www.linkedin.com/in/n/a" captures a slug of
    just "n", since "/" ends the slug character class), not a profile
    identifier.
    """
    if not value:
        return None
    m = _LINKEDIN_RE.search(value)
    if not m:
        return None
    slug = m.group(1)
    if not slug or len(slug) < 3 or slug.lower().strip("-_") in _PLACEHOLDER_SLUGS:
        return None
    return m.group(0), slug


def _normalize_linkedin_url(url: str) -> str:
    """Make a validated profile URL absolute and clickable.

    A schemeless value (`linkedin.com/in/<slug>`) is a *relative* path when
    dropped into an `href` or a spreadsheet cell, so it resolves against the
    report's own location and opens nothing -- measured on the real pool, 8
    of 56 stored profile URLs had no scheme for exactly this reason. This
    prepends `https://` when no scheme is present, and upgrades a plain
    `http://` to `https://` (3 more of the 56): LinkedIn redirects the
    cleartext scheme itself, but some clients block it outright before the
    redirect ever happens.

    Everything after the scheme -- `www.` or not, host, path, slug, any
    query string -- is left exactly as the candidate supplied it. This is
    normalisation for clickability only, called after the value has already
    passed the profile-shape check in `_extract_linkedin_profile`; it does
    NOT re-validate the value and must never be used to decide `present`.
    """
    if url.lower().startswith("https://"):
        return url
    if url.lower().startswith("http://"):
        return "https://" + url[len("http://") :]
    return "https://" + url


def find_linkedin(
    markdown: str, profile_data: list[dict[str, Any]], full_name: str
) -> dict[str, Any]:
    """Whether the candidate supplied something shaped like a LinkedIn
    profile URL, in the Trakstar ATS field or the CV text.

    What this validates -- and what it deliberately does NOT: this checks
    the SHAPE of the value only, i.e. does it contain a real profile path,
    `linkedin.com/in/<slug>` or `linkedin.com/pub/<slug>`, with a non-empty
    slug that isn't itself an unfilled placeholder ("your-name", etc.)? It
    does NOT verify that the profile actually EXISTS, and it never will:
    this pipeline never fetches LinkedIn, because LinkedIn blocks automated
    access, so confirming existence would require scraping, which this
    project will not do. A syntactically well-formed but entirely invented
    URL -- a fabricated slug that merely looks plausible -- passes this
    check and comes back `present: True`. Nothing downstream should read
    `present: True` as "the profile was confirmed to exist"; it means only
    "the value supplied looks like a profile URL."

    Measured against the real applicant pool: a plain non-empty ATS field
    value ("N/A", "n/a", "NA", "no", "ok", the bare site "www.linkedin.com")
    is NOT a profile and must come back absent -- `present: False`,
    `source: "none"`, `url: None`, `name_matches: None` -- so the -8 "no
    LinkedIn" penalty and the `no LinkedIn` flag apply. Only a value that
    contains a real profile path counts as present; the CV-text fallback is
    validated through the exact same check, via `_extract_linkedin_profile`,
    so the two paths cannot disagree on what counts as a profile.

    The stored `url` is normalised to an absolute `https://` link (see
    `_normalize_linkedin_url`) purely so a reviewer's click actually goes
    somewhere -- normalisation never runs on a value that failed the shape
    check above, so it can't resurrect a rejected value, and it still does
    NOT verify the profile exists.
    """
    raw = _linkedin_profile_value(profile_data or [])
    extracted = _extract_linkedin_profile(raw)
    source = "trakstar" if extracted else "none"

    if not extracted:
        extracted = _extract_linkedin_profile(markdown or "")
        if extracted:
            source = "cv"

    if not extracted:
        return {"present": False, "source": "none", "url": None, "name_matches": None}

    url, slug = extracted
    return {
        "present": True,
        "source": source,
        "url": _normalize_linkedin_url(url),
        "name_matches": _slug_matches_name(slug, full_name),
    }


def find_degree(markdown: str) -> dict[str, Any]:
    body = find_section(markdown, ["education", "academic"]) or markdown or ""
    level = None
    for pattern, label in _DEGREE_PATTERNS:
        if pattern.search(body):
            level = label
            break
    if level is None:
        return {"present": False, "level": None, "field": None}

    field = None
    fm = _FIELD_RE.search(body)
    if fm:
        field = (fm.group(1) or fm.group(2) or "").strip() or None
    return {"present": True, "level": level, "field": field}


def find_location(markdown: str, profile_data: list[dict[str, Any]]) -> dict[str, Any]:
    raw = _profile_value(profile_data or [])
    lines = [l for l in (markdown or "").splitlines() if l.strip()]
    # Residence evidence only: the ATS location field, else the contact header.
    # A country named in the summary or body describes WORK, not residence — and
    # this role's JD explicitly rewards offshore collaboration, so scanning prose
    # eliminated exactly the candidates the JD most wants. Verified: a US-based
    # candidate whose summary reads "clients across Vietnam and Singapore" was
    # being marked non_us_explicit and eliminated by gate G5.
    wide_scope = raw if raw else "\n".join(lines[:12])

    if raw:
        # The ATS field is free text, not necessarily an address. A bare
        # substring scan over it eliminated candidates whose location field
        # held prose ("Interested in opportunities across Singapore and
        # Vietnam"), an explicit relocation OFFER ("Willing to relocate to
        # our Singapore office"), or even an email domain -- see
        # _is_residence_evidence for the shape check that replaced it.
        non_us = _is_residence_evidence(raw)
    else:
        # A CV contact-header line is an address by convention, so this path
        # stays looser than the ATS-field path in other respects (no
        # intent-language check, no address-shape requirement -- a header
        # line already reads like "City, ST/Country"). But a domain or email
        # is never a place on either path: without stripping it out first, a
        # header containing "jane.doe@vietnamsoftware.com" on one line and a
        # genuine "Boston, MA" on another would set non_us_explicit purely
        # from the email's domain. Strip matched domain/email substrings
        # (rather than bailing out on the whole scope) so a real address on
        # a different header line still counts.
        header_scope = _NOT_A_PLACE_RE.sub(" ", "\n".join(lines[:3]))
        non_us = any(c in header_scope.lower() for c in _NON_US_COUNTRIES)

    # Check both the CV body AND the ATS value itself: a location field can
    # state its own US authorisation ("US citizen, currently in Vietnam"),
    # and that must cancel the gate exactly like a work-auth line in the CV.
    if non_us and (_WORK_AUTH_RE.search(markdown or "") or _WORK_AUTH_RE.search(raw or "")):
        # Says they are abroad but also authorised or relocating: not a gate.
        non_us = False

    timezone_hint = "unknown"
    us_evident = False
    for m in _US_HINT_RE.finditer(wide_scope):
        code = (m.group(1) or "").upper()
        if code in _ET_STATES:
            timezone_hint, us_evident = "ET", True
            break
        if code in _CT_STATES:
            timezone_hint, us_evident = "CT", True
            break
        if code in _MT_STATES:
            timezone_hint, us_evident = "MT", True
            break
        if code in _PT_STATES:
            timezone_hint, us_evident = "PT", True
            break
        if m.group(2):
            us_evident = True

    # A two-letter code or "United States"/"USA"/"US" match above takes
    # priority when both are present -- deterministic, and it's the more
    # specific signal. Otherwise, fall back to a spelled-out state name: this
    # is also what rescues a place like "Santa Fe, New Mexico" from the
    # country-name endswith check above, since "New Mexico" ends in "Mexico"
    # and nothing else in this function would otherwise recognise it as US.
    if not us_evident:
        sm = _US_STATE_NAME_RE.search(wide_scope)
        if sm:
            timezone_hint = _US_STATE_TIMEZONES[sm.group(1).lower()]
            us_evident = True

    # Authoritative per-field override: the ATS "state of residence" answer
    # (REAL-DATA-ADDENDUM section E) is looked up independently of `raw` --
    # not through wide_scope/_US_HINT_RE/_US_STATE_NAME_RE above, which are
    # comma-anchored for CV prose and never match a bare "Texas" or "FL" --
    # via the dedicated unanchored, per-field matcher. A hit here always wins
    # regardless of what the comma-anchored paths found.
    if not us_evident:
        state_tz = _match_state_of_residence(_state_residence_value(profile_data or []))
        if state_tz:
            timezone_hint, us_evident = state_tz, True

    # The sponsorship answer's "no" (e.g. "No", "no.", "N/A") is affirmative
    # evidence of US work authorisation and cancels gate G5 entirely, exactly
    # like the CV-prose work-auth check above -- regardless of what the
    # residence scan concluded. "Yes" never sets non_us_explicit; it can only
    # raise the `needs_sponsorship` flag below.
    work_authorized, needs_sponsorship = _sponsorship_evidence(
        _sponsorship_value(profile_data or [])
    )
    if work_authorized:
        non_us = False

    if us_evident:
        non_us = False

    # Region is corroboration only (see its comment above): it never touches
    # us_evident/non_us_explicit, and only fills in the tiebreak-facing
    # timezone_hint when nothing authoritative already resolved it.
    region_raw = _region_value(profile_data or [])
    if timezone_hint == "unknown":
        region_tz = _region_timezone_hint(region_raw)
        if region_tz:
            timezone_hint = region_tz

    return {
        "us_evident": us_evident,
        "non_us_explicit": non_us,
        "timezone_hint": timezone_hint,
        "raw": raw,
        "work_authorized": work_authorized,
        "needs_sponsorship": needs_sponsorship,
        "region_raw": region_raw,
    }


# --- Reference flag: offshore/nearshore collaboration claim -----------------
#
# This detector feeds a REFERENCE flag only (screen.rank._reference_flags):
# a chip a human reads and weighs, never a score input. It exists because the
# hiring manager wants to sanity-check CV claims like "Collaborated with
# offshore engineering teams in Vietnam to design and deploy integration
# APIs" against whether the named employer actually has staff in that
# country -- and that check cannot be automated. LinkedIn's company "People"
# tab requires an authenticated session; anonymous requests are bot-walled,
# and scripting a logged-in session breaches LinkedIn's User Agreement. It
# also wouldn't prove much even if it could be run: "offshore" ordinarily
# names a *vendor* relationship, so the employer legitimately shows zero
# staff in that country while the claim is entirely true. So this flag never
# judges the claim -- it only puts the sentence in front of a human.
#
# Two deliberate omissions, both load-bearing:
#
# 1. This makes NO attempt to attribute a claim to an employer. The parsed
#    markdown's employment headings mix title, dates, company, and location
#    on one messy line with no reliable separator between them, and guessing
#    wrong -- pinning an offshore claim on the wrong employer -- would be
#    worse than surfacing the claim with no employer at all. The report
#    already links the candidate's own resume; a human reads the real
#    employer straight off it.
# 2. This never fabricates a verification link (e.g. a canned LinkedIn
#    search URL). That would imply a check happened when none did. The only
#    thing surfaced is the claim text itself.
#
# Detection is deliberately loose: a keyword/proximity rule with good recall,
# not a precise one with clever exceptions. That is safe ONLY because this
# output can never cost a candidate a point or a rank -- a false positive
# here costs one extra line on a page a human is already reading. Contrast
# `_PLACEHOLDER_PATTERNS` above, which feeds a hard gate and therefore had to
# be narrowed until it could not misfire on genuine prose. If anyone ever
# attaches points (a penalty, a gate) to this flag, they must re-derive the
# detection from scratch with that bar in mind -- it was never built to
# carry that weight.

_OFFSHORE_TERM_RE = re.compile(r"\b(?:offshore|offshored|nearshore)\b", re.I)

_COLLAB_WORD_RE = re.compile(
    r"\bteams?\b|\bengineers?\b|\bdevelopers?\b|\bcollaborat\w*\b|\bpartner\w*\b|\bcoordinat\w*\b",
    re.I,
)

# Reuses the country list already assembled for residence detection
# (`_NON_US_COUNTRIES`, above) rather than keeping a second list in sync with
# it, plus a handful of region words that list has no reason to carry (it is
# scoped to "does the value read like a US vs. non-US address", not to broad
# geography). Order does not matter here: this scans with `\b...\b`
# word-boundary alternation, not the ordered `endswith` chain that tuple's
# own comment cares about.
_OFFSHORE_PLACE_NAMES = _NON_US_COUNTRIES + (
    "asia", "apac", "emea", "eastern europe", "latin america", "southeast asia",
)
_OFFSHORE_PLACE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(p) for p in _OFFSHORE_PLACE_NAMES) + r")\b", re.I
)

_LINE_BULLET_STRIP_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_LINE_EMPHASIS_STRIP_RE = re.compile(r"[*_`]{1,3}")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _candidate_sentences(markdown: str) -> list[str]:
    """Crude sentence/bullet units for the offshore-claim scan: one per
    markdown list item, further split on sentence punctuation for ordinary
    prose lines that pack more than one clause. Precision doesn't matter
    here -- see the module comment above for why a loose scan is safe for
    a reference-only flag.
    """
    out: list[str] = []
    for line in (markdown or "").splitlines():
        if not line.strip():
            continue
        text = _LINE_BULLET_STRIP_RE.sub("", line)
        text = _LINE_EMPHASIS_STRIP_RE.sub("", text)
        for clause in _SENTENCE_SPLIT_RE.split(text):
            clause = clause.strip()
            if clause:
                out.append(clause)
    return out


def find_offshore_claims(markdown: str) -> list[dict[str, Any]]:
    """Sentences/bullets that assert collaboration with a geographically
    separated team -- see the module comment above for what this is for and
    why the detection rule is deliberately loose. Fires on a sentence that
    either names offshore/nearshore/offshored outright, or names a country
    or region alongside a collaboration word (team, engineer, developer,
    collaborat*, partner*, coordinat*). The matching sentence is stored
    verbatim -- exactly as written, not normalized -- because for this flag
    the sentence IS the evidence.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sentence in _candidate_sentences(markdown):
        has_offshore_term = bool(_OFFSHORE_TERM_RE.search(sentence))
        # Lowercased for storage -- these match against the (lowercase)
        # `_OFFSHORE_PLACE_NAMES` canonical spellings and get deduplicated
        # across sentences, so "Vietnam" in one bullet and "vietnam" in
        # another must collapse to one entry, not two.
        places = sorted({m.group(0).lower() for m in _OFFSHORE_PLACE_RE.finditer(sentence)})
        has_collab_word = bool(_COLLAB_WORD_RE.search(sentence))
        if not (has_offshore_term or (places and has_collab_word)):
            continue
        key = normalize(sentence)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({"sentence": sentence, "places": places})
    return out
