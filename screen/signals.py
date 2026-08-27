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

_US_HINT_RE = re.compile(
    r",\s*([A-Z]{2})\b(?:\s+\d{5})?"
    r"|\b(United States|USA|U\.S\.A\.?|U\.S\.?|US)(?!\w)"
)

_NON_US_COUNTRIES = (
    "vietnam", "viet nam", "india", "canada", "united kingdom", "england", "germany",
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
_LOCATION_LABELS = frozenset({
    "location", "current location", "candidate location", "location city state",
    "city", "current city", "city town", "town",
    "address", "home address", "mailing address", "street address",
    "based in", "country", "current country",
})
_LABEL_NORM_RE = re.compile(r"[^a-z0-9]+")
# A value containing an email or a URL is not a place, whatever its label says.
_NOT_A_PLACE_RE = re.compile(r"@|://|\bhttps?\b")


def _profile_value(profile_data: list[dict[str, Any]]) -> str | None:
    for item in profile_data or []:
        label = _LABEL_NORM_RE.sub(" ", str(item.get("name", "")).lower()).strip()
        if label in _LOCATION_LABELS:
            value = str(item.get("value", "")).strip()
            if value and not _NOT_A_PLACE_RE.search(value):
                return value
    return None


# LinkedIn gets the same exact-label treatment as location, for the same
# reason (a needle like "linkedin" is safe here, but staying consistent
# costs nothing) -- though a wrong match here only costs an -8pt penalty,
# never an elimination, so it doesn't need the value guard above.
_LINKEDIN_LABELS = frozenset({"linkedin", "linkedin profile", "linkedin url"})


def _linkedin_profile_value(profile_data: list[dict[str, Any]]) -> str | None:
    for item in profile_data or []:
        label = _LABEL_NORM_RE.sub(" ", str(item.get("name", "")).lower()).strip()
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


def find_linkedin(
    markdown: str, profile_data: list[dict[str, Any]], full_name: str
) -> dict[str, Any]:
    url = _linkedin_profile_value(profile_data or [])
    source = "trakstar" if url else "none"

    if not url:
        m = _LINKEDIN_RE.search(markdown or "")
        if m:
            url, source = m.group(0), "cv"

    if not url:
        return {"present": False, "source": "none", "url": None, "name_matches": None}

    m = _LINKEDIN_RE.search(url)
    slug = m.group(1) if m else ""
    return {
        "present": True,
        "source": source,
        "url": url,
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
    residence_scope = raw if raw else "\n".join(lines[:3])
    wide_scope = raw if raw else "\n".join(lines[:12])

    lowered = residence_scope.lower()
    non_us = any(c in lowered for c in _NON_US_COUNTRIES)
    if non_us and _WORK_AUTH_RE.search(markdown or ""):
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

    if us_evident:
        non_us = False

    return {
        "us_evident": us_evident,
        "non_us_explicit": non_us,
        "timezone_hint": timezone_hint,
        "raw": raw,
    }
