"""Assemble the deterministic fact sheet for each candidate.

This stage runs every detector, writes prechecks/<id>.json, and writes the
redacted markdown the judge will read. It is the last point at which PII exists
in the pipeline's outputs.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from screen import linkedin_check, signals
from screen.config import RoleConfig
from screen.parse import ParsedPdf, UnsupportedFormatError, parse_pdf
from screen.paths import Paths
from screen.pool import pool_duplicates, write_pool_duplicates
from screen.redact import assert_clean, redact
from screen.text import extract_bullets

_PDF_DATE_RE = re.compile(r"D:(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})")

# Resumes are saved under their real extension -- .pdf or .docx (about 23% of
# the real pool is DOCX, per REAL-DATA-ADDENDUM section D) -- so this stage
# must resolve whichever one exists rather than assuming .pdf.
_RESUME_EXTENSIONS = (".pdf", ".docx")


def precheck_key(pdf_sha256: str, precheck_rules_version: int) -> str:
    return hashlib.sha256(
        f"{pdf_sha256}:{precheck_rules_version}".encode("utf-8")
    ).hexdigest()


def _parse_applied(applied: str | int | float | None) -> datetime | None:
    """Parse the application timestamp, which the real API returns as a UNIX
    integer (e.g. 1787819217), not an ISO string (REAL-DATA-ADDENDUM section
    C). Accept an int/float, a numeric string, or a genuine ISO-8601 string,
    so the signal doesn't silently disappear against real data.
    """
    if applied is None:
        return None
    if isinstance(applied, (int, float)):
        try:
            return datetime.fromtimestamp(float(applied), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(applied).strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return datetime.fromtimestamp(float(text), tz=timezone.utc)
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def minutes_before_submission(
    pdf_created: str, applied: str | int | float | None
) -> float | None:
    """Minutes between PDF creation and the application timestamp.

    Returns None when either timestamp is missing or unparseable — the caller
    treats None as "no signal", never as zero. `applied` accepts a UNIX epoch
    (int/float or numeric string) or an ISO-8601 string — see _parse_applied.
    """
    m = _PDF_DATE_RE.search(pdf_created or "")
    if not m:
        return None
    applied_dt = _parse_applied(applied)
    if applied_dt is None:
        return None
    try:
        created = datetime(*(int(g) for g in m.groups()), tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
    return (applied_dt - created).total_seconds() / 60


def _linkedin_liveness(
    linkedin: dict[str, Any],
    previous: dict[str, Any] | None,
    cfg: RoleConfig,
    client: httpx.Client | None,
    throttle: linkedin_check.Throttle | None,
) -> str | None:
    """The `linkedin.liveness` verdict to store, or `None` to add no field.

    `None` means the check was skipped entirely -- either the config flag
    `gates.check_linkedin_liveness` is off (no network, ever), or there is
    no usable URL to check. Otherwise this is the cache: when the stored
    precheck already carries a liveness verdict for this exact URL, that
    verdict is reused and no request is made; only a new or changed URL
    triggers a fresh HEAD request (see screen.linkedin_check).
    """
    if not cfg.gates.get("check_linkedin_liveness"):
        return None
    url = linkedin.get("url")
    if not url:
        return None

    if previous is not None:
        prev_linkedin = previous.get("linkedin") or {}
        if prev_linkedin.get("url") == url and prev_linkedin.get("liveness") is not None:
            return prev_linkedin["liveness"]

    if throttle is not None:
        throttle.wait()
    return linkedin_check.check_profile(url, client=client)


def build_precheck(
    candidate: dict[str, Any],
    parsed: ParsedPdf,
    cfg: RoleConfig,
    today: tuple[int, int],
    pool_dups: list[dict[str, Any]],
    previous: dict[str, Any] | None = None,
    linkedin_client: httpx.Client | None = None,
    linkedin_throttle: linkedin_check.Throttle | None = None,
) -> tuple[dict[str, Any], str]:
    """Build the precheck payload and the redacted markdown together.

    Returns both because the redacted text is a by-product of the same
    redaction pass whose statistics land in the payload — splitting them
    would redact twice.

    `previous` is the candidate's last-written precheck payload (or `None`
    for a first-ever run), used only to let the LinkedIn liveness check
    reuse a cached verdict instead of re-requesting an unchanged URL --
    see `_linkedin_liveness`. `linkedin_client`/`linkedin_throttle` are
    injection points for that same check: tests pass a fake client so
    nothing here ever touches the real network.
    """
    md = parsed.markdown
    bullets = extract_bullets(md)
    full_name = f"{candidate.get('first_name', '')} {candidate.get('last_name', '')}".strip()
    profile_data = candidate.get("profile_data") or []

    minutes = minutes_before_submission(
        parsed.meta.get("created", ""), candidate.get("created_date")
    )
    date_ranges = signals.extract_date_ranges(md)
    redaction = redact(md, candidate)

    linkedin = signals.find_linkedin(md, profile_data, full_name)
    liveness = _linkedin_liveness(linkedin, previous, cfg, linkedin_client, linkedin_throttle)
    if liveness is not None:
        linkedin = {**linkedin, "liveness": liveness}

    return {
        "candidate_id": int(candidate["id"]),
        "precheck_key": precheck_key(parsed.sha256, cfg.precheck_rules_version),
        "pdf_sha256": parsed.sha256,
        "precheck_rules_version": cfg.precheck_rules_version,
        "pages": parsed.pages,
        "text_chars": parsed.text_chars,
        "pdf_meta": {**parsed.meta, "minutes_before_submission": minutes},
        "hidden_text": parsed.hidden_text,
        "placeholders": signals.find_placeholders(md),
        "intra_cv_duplicate_bullets": signals.intra_cv_duplicates(
            bullets, threshold=float(cfg.gates["intra_dup_jaccard"])
        ),
        "pool_duplicate_bullets": pool_dups,
        "skills_count": signals.count_skills(md),
        "bullet_count": len(bullets),
        "power_verb_density": signals.power_verb_density(bullets),
        "round_metric_ratio": signals.round_metric_ratio(bullets),
        # Same bullet population the ratio above is computed over (see
        # signals.metric_count / signals._bullets_with_metrics) -- stored
        # alongside it so a consumer never has to guess how many metrics the
        # ratio was over, and the two can never disagree about what a
        # "metric" is.
        "metric_count": signals.metric_count(bullets),
        "template_metadata_signal": signals.template_metadata_signal(
            parsed.meta, minutes, cfg
        ),
        "years_experience": signals.compute_years(md, today),
        "short_stints_last_5y": signals.short_stints(date_ranges, today, years_back=5),
        "gap_over_12m": signals.has_gap_over(
            date_ranges, today, months=12, years_back=6
        ),
        "linkedin": linkedin,
        "degree": signals.find_degree(md),
        "location": signals.find_location(md, profile_data),
        # Reference-only (see screen.rank._reference_flags and the long
        # comment in screen.signals.find_offshore_claims) -- always computed,
        # since detection is pure and cheap; role.json's
        # reference_flags.offshore_claim key decides only whether assess()
        # surfaces it, never whether this is collected.
        "offshore_claims": signals.find_offshore_claims(md),
        "redaction": {
            "tokens_replaced": redaction.tokens_replaced,
            "kinds": redaction.kinds,
            "leaks": assert_clean(redaction.text),
        },
    }, redaction.text


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def _resolve_resume(resumes_dir: Path, cid: int) -> Path | None:
    """Resume files are saved under their real extension by the fetch stage
    (.pdf or .docx — about 23% of the real pool is DOCX). Resolve whichever
    one exists rather than assuming .pdf.
    """
    for ext in _RESUME_EXTENSIONS:
        candidate = resumes_dir / f"{cid}{ext}"
        if candidate.exists():
            return candidate
    return None


def run_stage(
    paths: Paths,
    cfg: RoleConfig,
    today: tuple[int, int],
    force: bool = False,
    linkedin_client: httpx.Client | None = None,
    linkedin_throttle: linkedin_check.Throttle | None = None,
) -> dict[str, Any]:
    """Precheck every candidate whose PDF or rules version changed.

    Pool duplicates are recomputed over the whole pool every run, because a new
    arrival can reveal that an already-processed CV used the same template.

    `linkedin_client`/`linkedin_throttle` are injection points for the
    LinkedIn liveness check (see screen.linkedin_check and
    `_linkedin_liveness`): production leaves both `None`, which gets a real
    client per check and a real ~1s delay between them; tests inject their
    own so nothing here ever touches the network. When the check is enabled
    (`gates.check_linkedin_liveness`) and no throttle was supplied, one is
    created for the whole run so consecutive checks are spaced out rather
    than firing back-to-back.
    """
    if cfg.gates.get("check_linkedin_liveness") and linkedin_throttle is None:
        linkedin_throttle = linkedin_check.Throttle()

    candidates = json.loads(paths.candidates_json.read_text())
    parsed_by_id: dict[int, ParsedPdf] = {}
    needs_review: dict[int, str] = {}
    min_chars = int(cfg.gates["min_text_chars"])

    for c in candidates:
        cid = int(c["id"])
        resume_path = _resolve_resume(paths.resumes, cid)
        if resume_path is None:
            needs_review[cid] = "missing_resume"
            continue
        try:
            parsed = parse_pdf(resume_path)
        except UnsupportedFormatError as exc:
            needs_review[cid] = exc.reason
            continue
        except Exception:
            needs_review[cid] = "unparseable"
            continue
        if parsed.text_chars < min_chars:
            needs_review[cid] = "unparseable"
            continue
        parsed_by_id[cid] = parsed

    # Pool-wide duplicate pass over everyone we could parse.
    dups = pool_duplicates(
        {cid: extract_bullets(p.markdown) for cid, p in parsed_by_id.items()}
    )
    write_pool_duplicates(dups, paths.pool_duplicates_json)

    processed: list[int] = []
    skipped: list[int] = []

    for c in candidates:
        cid = int(c["id"])
        if cid not in parsed_by_id:
            continue
        parsed = parsed_by_id[cid]
        out_path = paths.prechecks / f"{cid}.json"
        want_key = precheck_key(parsed.sha256, cfg.precheck_rules_version)

        # Loaded whenever a precheck already exists, force or not: even a
        # forced/rules-version rebuild should still let the LinkedIn liveness
        # check (below, via `previous`) reuse a cached verdict for an
        # unchanged URL rather than re-requesting it.
        existing: dict[str, Any] | None = None
        if out_path.exists():
            try:
                existing = json.loads(out_path.read_text())
            except json.JSONDecodeError:
                existing = None
            if not force and existing is not None:
                # Re-run when the PDF or the rules changed, or when the pool
                # pass discovered duplicates the stored file does not know
                # about.
                same_key = existing.get("precheck_key") == want_key
                same_dups = existing.get("pool_duplicate_bullets", []) == dups.get(cid, [])
                if same_key and same_dups:
                    skipped.append(cid)
                    continue

        payload, redacted_md = build_precheck(
            c, parsed, cfg, today, dups.get(cid, []),
            previous=existing,
            linkedin_client=linkedin_client,
            linkedin_throttle=linkedin_throttle,
        )
        _write_json(out_path, payload)
        _write_text(paths.redacted / f"{cid}.md", redacted_md)
        processed.append(cid)

    return {
        "processed": sorted(processed),
        "skipped": sorted(skipped),
        "needs_review": needs_review,
    }
