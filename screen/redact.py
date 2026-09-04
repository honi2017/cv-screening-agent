"""Strip identity from CV markdown before any judgment happens.

Off-the-shelf LLMs score identical CVs differently when names, schools, or
locations differ (arXiv 2507.02087). Removing those fields is cheaper and more
reliable than instructing the model to ignore them — so we do both.

What survives on purpose: employers, job titles, dates, achievements, metrics,
technologies, degree level and field. Those are the scorable content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE_RE = re.compile(
    r"(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?)?\d{3}[\s.-]?\d{3,4}(?:[\s.-]?\d{2,4})?"
)
_URL_PROFILE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:linkedin\.com/(?:in|pub)/[\w\-%]+"
    r"|github\.com/[\w\-]+"
    r"|twitter\.com/[\w\-]+|x\.com/[\w\-]+)/?",
    re.I,
)
_CITY_STATE_RE = re.compile(
    r"\b([A-Z][a-zA-Z.\- ]{2,24}),\s*([A-Z]{2})\b(?:\s+\d{5}(?:-\d{4})?)?"
)
_STREET_RE = re.compile(
    r"\b\d{1,6}\s+[A-Z][A-Za-z.\-]*(?:\s+[A-Z][A-Za-z.\-]*)*\s+"
    r"(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Ln|Lane|Dr|Drive|Ct|Court|Way|Pl|Place)\b\.?",
    re.I,
)
_SCHOOL_RE = re.compile(
    r"\b(?:[A-Z][\w.\-]*\s+){0,4}"
    r"(?:University|Universität|Universidad|College|Institute of Technology|Polytechnic|"
    r"School of [A-Z]\w+)"
    r"(?:\s+of\s+[A-Z][\w.\-]*(?:\s+[A-Z][\w.\-]*)?)?",
)
_ZIP_RE = re.compile(r"\b\d{5}(?:-\d{4})?\b")

# Phone-like sequences that are actually dates or metrics must not be redacted.
_PHONE_GUARD_RE = re.compile(r"^(?:19|20)\d{2}(?:[-/]\d{1,2})?$")

# A bullet ("-", "*", "+"), a numbered list item, or a level-2+ heading marks
# the end of the CV's contact block.
_HEADER_END_RE = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s|#{2,}\s)")


def _header_region_end(lines: list[str]) -> int:
    """Index one past the CV's contact block, which ends at the first bullet or
    section heading. The candidate's name lives here and employer names do not,
    so the adjacent-capital guard is dropped inside this window."""
    seen = 0
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if _HEADER_END_RE.match(line):
            return i
        seen += 1
        if seen >= 5:
            return i + 1
    return len(lines)


@dataclass(frozen=True)
class RedactionResult:
    text: str
    tokens_replaced: int
    kinds: dict[str, int]


def _sub_counting(pattern: re.Pattern[str], token: str, text: str) -> tuple[str, int]:
    count = 0

    def repl(m: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return token

    return pattern.sub(repl, text), count


def _standalone_name_sub(text: str, value: str, token: str) -> tuple[str, int]:
    """Redact a lone first- or last-name token.

    Case-sensitive (so a surname that is also an ordinary English word, e.g.
    "Grant" or "Case", does not eat lowercase prose like "led a grant
    reporting project"), and skipped whenever the match sits directly next to
    another capitalized word (e.g. "Bell Labs", "Morgan Stanley") — that shape
    is far more likely to be an employer/institution name than a second,
    unaccompanied mention of the candidate's own surname.
    """
    pattern = re.compile(rf"\b{re.escape(value)}\b")
    count = 0
    out: list[str] = []
    last_end = 0
    for m in pattern.finditer(text):
        start, end = m.span()
        before, after = text[:start], text[end:]
        preceded_by_cap = bool(re.search(r"[A-Z][A-Za-z'\-]*\s$", before))
        followed_by_cap = bool(re.match(r"^\s+[A-Z][A-Za-z'\-]*", after))
        if preceded_by_cap or followed_by_cap:
            continue
        out.append(text[last_end:start])
        out.append(token)
        last_end = end
        count += 1
    out.append(text[last_end:])
    return "".join(out), count


def redact(markdown: str, candidate: dict[str, Any]) -> RedactionResult:
    text = markdown or ""
    kinds: dict[str, int] = {}

    def bump(kind: str, n: int) -> None:
        if n:
            kinds[kind] = kinds.get(kind, 0) + n

    # 1. Email and phone from the ATS record, then the generic email pattern —
    # all before name substitution. Name substitution is word-boundary based,
    # so if it ran first it would shred the local part of an address like
    # "alex.morgan@example.com" into "[NAME].[NAME]@example.com", breaking the
    # email out of a shape the email regex can still recognise.
    for field, token, kind in (
        ("email", "[EMAIL]", "email"),
        ("phone", "[PHONE]", "phone"),
    ):
        value = str(candidate.get(field) or "").strip()
        if len(value) > 4:
            text, n = _sub_counting(re.compile(re.escape(value), re.I), token, text)
            bump(kind, n)

    text, n = _sub_counting(_EMAIL_RE, "[EMAIL]", text)
    bump("email", n)

    # 2. Known name values from the ATS record.
    first = str(candidate.get("first_name") or "").strip()
    last = str(candidate.get("last_name") or "").strip()
    full = f"{first} {last}".strip()
    n_name = 0

    # 2a. First-through-last span, tolerant of a middle name/initial and of an
    # ALL-CAPS header. Requiring both the first and last name with only
    # capitalised tokens between them is a very strong signal — an employer
    # name essentially never matches it — so this stays case-insensitive.
    if len(first) > 2 and len(last) > 2:
        span = re.compile(
            rf"\b{re.escape(first)}(?:\s+[A-Z][A-Za-z.'\-]*){{0,2}}\s+{re.escape(last)}\b",
            re.I,
        )
        text, n = _sub_counting(span, "[NAME]", text)
        n_name += n

    # 2b. Unguarded name substitution inside the CV's header region only (see
    # _header_region_end). The contact block at the top is where the name
    # lives and where employer names essentially never appear, so the
    # adjacent-capital guard is counterproductive there. This also covers a
    # header that shows a different given name than the ATS record: the
    # surname alone still gets redacted even though it sits next to a
    # capitalised (but non-matching) given name that would otherwise trip the
    # guard in _standalone_name_sub.
    lines = text.split("\n")
    header_end = _header_region_end(lines)
    if header_end > 0:
        header_text = "\n".join(lines[:header_end])
        for value in (first, last):
            if len(value) > 2:
                header_text, n = _sub_counting(
                    re.compile(rf"\b{re.escape(value)}\b", re.I), "[NAME]", header_text
                )
                n_name += n
        if header_end < len(lines):
            text = header_text + "\n" + "\n".join(lines[header_end:])
        else:
            text = header_text

    # 2c. Full name elsewhere in the document (no middle name; a two-token
    # exact match is a strong, low-risk signal so it stays case-insensitive to
    # also catch an ALL-CAPS occurrence); first/last alone go through the
    # guarded, case-sensitive helper above.
    if len(full) > 2:
        text, n = _sub_counting(re.compile(rf"\b{re.escape(full)}\b", re.I), "[NAME]", text)
        n_name += n
    for value in (first, last):
        if len(value) > 2:
            text, n = _standalone_name_sub(text, value, "[NAME]")
            n_name += n
    bump("name", n_name)

    # 3. Pattern-based sweep for anything the record did not cover.
    text, n = _sub_counting(_URL_PROFILE_RE, "[PROFILE_URL]", text)
    bump("profile_url", n)

    text, n = _sub_counting(_STREET_RE, "[LOCATION]", text)
    bump("address", n)

    text, n = _sub_counting(_CITY_STATE_RE, "[LOCATION]", text)
    bump("city_state", n)

    text, n = _sub_counting(_ZIP_RE, "[LOCATION]", text)
    bump("zip", n)

    text, n = _sub_counting(_SCHOOL_RE, "[SCHOOL]", text)
    bump("school", n)

    # Phones last, and guarded, so date ranges like 2019-03 survive.
    def phone_repl(m: re.Match[str]) -> str:
        raw = m.group(0).strip()
        digits = re.sub(r"\D", "", raw)
        if len(digits) < 7 or _PHONE_GUARD_RE.match(raw):
            return m.group(0)
        return "[PHONE]"

    before = text
    text = _PHONE_RE.sub(phone_repl, text)
    bump("phone", text.count("[PHONE]") - before.count("[PHONE]"))

    return RedactionResult(
        text=text, tokens_replaced=sum(kinds.values()), kinds=kinds
    )


def assert_clean(text: str) -> list[str]:
    """Report residual PII. Empty list means the text is safe to hand to a judge."""
    leaks: list[str] = []
    if _EMAIL_RE.search(text):
        leaks.append(f"email: {_EMAIL_RE.search(text).group(0)}")
    for m in _PHONE_RE.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) >= 10:
            leaks.append(f"phone: {m.group(0).strip()}")
            break
    if _URL_PROFILE_RE.search(text):
        leaks.append(f"profile url: {_URL_PROFILE_RE.search(text).group(0)}")
    return leaks
