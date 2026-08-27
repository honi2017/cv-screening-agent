"""Pure text helpers shared by signal detection, pool comparison, and quote checks.

Imports nothing from the rest of the package so it stays dependency-free.
"""

from __future__ import annotations

import hashlib
import re

_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*\S)\s*$")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*\S)\s*$")
_PUNCT_RE = re.compile(r"[^a-z0-9\s]+")
_SPACE_RE = re.compile(r"\s+")
_MD_EMPHASIS_RE = re.compile(r"[*_`]{1,3}")


def extract_bullets(markdown: str) -> list[str]:
    """Return the text of every markdown list item, in document order."""
    bullets: list[str] = []
    for line in markdown.splitlines():
        m = _BULLET_RE.match(line)
        if not m:
            continue
        text = _MD_EMPHASIS_RE.sub("", m.group(1)).strip()
        if text:
            bullets.append(text)
    return bullets


def normalize(s: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    s = s.lower()
    s = _PUNCT_RE.sub(" ", s)
    return _SPACE_RE.sub(" ", s).strip()


def bullet_hash(s: str) -> str:
    return hashlib.sha1(normalize(s).encode("utf-8")).hexdigest()[:16]


def jaccard(a: str, b: str) -> float:
    """Token-set similarity of two strings after normalization.

    Purely numeric tokens are excluded from both sets before comparison.
    This isolates the sentence structure as the signal, so "led team of 5 engineers"
    and "led team of 50 engineers" will score as near-duplicates (1.0) because they
    follow the same lazy-template pattern; the magnitude difference (5 vs 50) is blind.
    Asymmetry: "4M" remains as "4m" after normalization and is NOT filtered (since
    "4m".isdigit() is False); only pure-digit tokens like "40" disappear.
    Callers must account for this when setting thresholds: this cannot distinguish
    rounding from order-of-magnitude differences.
    """
    ta = set(t for t in normalize(a).split() if not t.isdigit())
    tb = set(t for t in normalize(b).split() if not t.isdigit())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def find_section(markdown: str, names: list[str]) -> str | None:
    """Return the body under the first heading whose text matches one of `names`.

    Matching is case-insensitive and substring-based ("Work Experience" matches
    "experience"). The body ends at the next heading of the same or higher level.
    """
    wanted = [n.lower() for n in names]
    lines = markdown.splitlines()
    start: int | None = None
    start_level = 0

    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if not m:
            continue
        # Count only the LEADING hashes. Counting '#' anywhere would break on
        # headings that mention C#, which CVs do constantly.
        stripped = line.strip()
        level = len(stripped) - len(stripped.lstrip("#"))
        heading = m.group(1).lower()
        if start is None:
            if any(w in heading for w in wanted):
                start, start_level = i + 1, level
        elif level <= start_level:
            return "\n".join(lines[start:i]).strip() or None

    if start is None:
        return None
    return "\n".join(lines[start:]).strip() or None


def contains_quote(haystack: str, quote: str) -> bool:
    """Whitespace-insensitive substring check, used to verify judge quotes."""
    if not quote.strip():
        return False
    h = _SPACE_RE.sub(" ", haystack).strip().lower()
    q = _SPACE_RE.sub(" ", quote).strip().lower()
    return q in h
