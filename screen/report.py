"""Render the run into a self-contained HTML page, a Slack-shaped summary, and CSVs.

The HTML has no external assets so it opens from Finder and survives being
emailed. Everything a reader needs to challenge a decision — the quote behind
each score, the reason behind each gate — is on the page.
"""

from __future__ import annotations

import csv
import html as html_mod
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from screen.config import RoleConfig
from screen.ledger import LedgerEntry
from screen.paths import Paths
from screen.rank import Assessment, CutResult

TRAKSTAR_BASE = "https://anduin.hire.trakstar.com/app/#candidates"


@dataclass(frozen=True)
class ReportInput:
    run_id: str
    cut: CutResult
    assessments: dict[int, Assessment]
    candidates: dict[int, dict[str, Any]]
    prechecks: dict[int, dict[str, Any]]
    verdicts: dict[int, dict[str, Any]]
    ledger: dict[int, LedgerEntry]
    needs_review: dict[int, str]
    delta: dict[str, Any]
    calibration_note: str
    opening_id: str

    def name(self, cid: int) -> str:
        c = self.candidates.get(cid, {})
        return f"{c.get('first_name', '')} {c.get('last_name', '')}".strip() or f"Candidate {cid}"


def trakstar_url(opening_id: str, candidate_id: int) -> str:
    return f"{TRAKSTAR_BASE}/{candidate_id}?opening={opening_id}"


def _e(value: Any) -> str:
    return html_mod.escape(str(value if value is not None else ""), quote=True)


_CSS = """
:root { --bg:#fff; --fg:#16181d; --muted:#6b7280; --line:#e5e7eb; --accent:#1f6feb;
        --ok:#0f7b3f; --warn:#9a6700; --bad:#b42318; --chip:#f3f4f6; }
* { box-sizing: border-box; }
body { margin:0; padding:24px; background:var(--bg); color:var(--fg);
       font:14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }
h1 { font-size:22px; margin:0 0 4px; } h2 { font-size:17px; margin:32px 0 8px;
     padding-bottom:4px; border-bottom:1px solid var(--line); }
.sub { color:var(--muted); margin:0 0 20px; }
.stats { display:flex; flex-wrap:wrap; gap:16px; margin:16px 0 8px; }
.stat { background:var(--chip); border-radius:8px; padding:10px 14px; min-width:110px; }
.stat b { display:block; font-size:20px; } .stat span { color:var(--muted); font-size:12px; }
table { width:100%; border-collapse:collapse; margin:8px 0 4px; }
th, td { text-align:left; padding:7px 8px; border-bottom:1px solid var(--line);
         vertical-align:top; font-size:13px; }
th { color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.03em; }
tr.grey td { color:var(--muted); }
.score { font-weight:700; font-variant-numeric:tabular-nums; }
.bars { display:flex; gap:2px; align-items:flex-end; height:22px; }
.bar { width:9px; background:var(--accent); opacity:.85; border-radius:1px 1px 0 0; }
.bar.empty { background:var(--line); }
.chip { display:inline-block; background:var(--chip); border-radius:10px; padding:1px 7px;
        margin:0 3px 3px 0; font-size:11px; color:var(--warn); white-space:nowrap; }
.chip.bad { color:var(--bad); }
details { margin:4px 0; } summary { cursor:pointer; color:var(--accent); font-size:12px; }
.detail { background:#fafafa; border:1px solid var(--line); border-radius:6px;
          padding:10px 12px; margin:6px 0 10px; }
.detail dl { display:grid; grid-template-columns:190px 1fr; gap:4px 12px; margin:0; }
.detail dt { color:var(--muted); } .detail dd { margin:0; }
blockquote { margin:2px 0 6px; padding:4px 10px; border-left:3px solid var(--line);
             color:#374151; font-style:italic; }
.gate { color:var(--bad); font-weight:600; }
footer { margin-top:36px; color:var(--muted); font-size:12px; }
footer table { max-width:760px; }
.empty-note { color:var(--muted); font-style:italic; }
"""


def _bars_html(verdict: dict[str, Any], cfg: RoleConfig) -> str:
    parts = []
    for c in cfg.criteria:
        entry = verdict["fit"]["scores"].get(c.key, {})
        score = float(entry.get("score") or 0)
        pct = score / c.max if c.max else 0
        height = max(2, round(pct * 22))
        cls = "bar" if score > 0 else "bar empty"
        parts.append(
            f'<div class="{cls}" style="height:{height}px" '
            f'title="{_e(c.label)}: {score:g}/{c.max}"></div>'
        )
    return f'<div class="bars">{"".join(parts)}</div>'


def _detail_html(data: ReportInput, cid: int, cfg: RoleConfig) -> str:
    verdict = data.verdicts.get(cid, {})
    precheck = data.prechecks.get(cid, {})
    assessment = data.assessments.get(cid)
    if not verdict or assessment is None:
        return ""

    rows = []
    for c in cfg.criteria:
        entry = verdict["fit"]["scores"].get(c.key, {})
        quote = entry.get("quote")
        quote_html = f"<blockquote>{_e(quote)}</blockquote>" if quote else "<em>no evidence</em>"
        rows.append(
            f"<dt>{_e(c.label)} — {float(entry.get('score') or 0):g}/{c.max}</dt>"
            f"<dd>{quote_html}{_e(entry.get('rationale'))}</dd>"
        )

    flags = verdict.get("redflag", {}).get("flags") or []
    if flags:
        for f in flags:
            rows.append(
                f"<dt>Flag (Tier {_e(f['tier'])}) — {_e(f['kind'])}</dt>"
                f"<dd><blockquote>{_e(f['quote'])}</blockquote>{_e(f.get('explanation'))}</dd>"
            )

    for pen in assessment.penalties:
        rows.append(
            f"<dt>Penalty −{_e(pen['points'])} — {_e(pen['kind'])}</dt><dd>{_e(pen['detail'])}</dd>"
        )

    years = precheck.get("years_experience") or {}
    linkedin = precheck.get("linkedin") or {}
    meta = precheck.get("pdf_meta") or {}
    rows.extend(
        [
            f"<dt>Years computed</dt><dd>{_e(years.get('computed'))} "
            f"(confidence: {_e(years.get('confidence'))})</dd>",
            f"<dt>LinkedIn</dt><dd>{'present via ' + _e(linkedin.get('source')) if linkedin.get('present') else 'not found'}"
            f"{' — name match: ' + _e(linkedin.get('name_matches')) if linkedin.get('present') else ''}</dd>",
            f"<dt>PDF</dt><dd>{_e(precheck.get('pages'))} page(s); producer "
            f"{_e(meta.get('producer') or 'unknown')}; created "
            f"{_e(meta.get('minutes_before_submission'))} min before applying</dd>",
            f"<dt>Judge summary</dt><dd>{_e(verdict['fit'].get('summary'))}</dd>",
        ]
    )

    if assessment.quote_warnings:
        warns = "".join(f"<li>{_e(w)}</li>" for w in assessment.quote_warnings)
        rows.append(f"<dt>Quote warnings</dt><dd><ul>{warns}</ul></dd>")

    return (
        "<details><summary>evidence</summary>"
        f'<div class="detail"><dl>{"".join(rows)}</dl></div></details>'
    )


def _candidate_table(data: ReportInput, ids: list[int], cfg: RoleConfig, grey: bool = False) -> str:
    if not ids:
        return '<p class="empty-note">None.</p>'

    head = (
        "<tr><th>#</th><th>Name</th><th>Score</th><th>Criteria</th>"
        "<th>Penalties</th><th>Flags</th><th>Links</th></tr>"
    )
    body = []
    for rank, cid in enumerate(ids, start=1):
        assessment = data.assessments.get(cid)
        if assessment is None:
            continue
        entry = data.ledger.get(cid)
        chips = "".join(f'<span class="chip">{_e(f)}</span>' for f in assessment.flags)
        penalties = (
            f"−{assessment.penalty_total:g}" if assessment.penalty_total else "—"
        )
        since = (
            f'<br><span class="chip">since {_e(entry.first_seen_run)}</span>'
            if entry and entry.status == "accepted"
            else ""
        )
        gate_note = (
            f'<br><span class="gate">flagged {_e(assessment.gate)} after acceptance</span>'
            if assessment.gate and entry and entry.status == "accepted"
            else ""
        )
        body.append(
            f'<tr class="{"grey" if grey else ""}">'
            f"<td>{rank}</td>"
            f"<td>{_e(data.name(cid))}{since}{gate_note}{_detail_html(data, cid, cfg)}</td>"
            f'<td class="score">{assessment.final:g}</td>'
            f"<td>{_bars_html(data.verdicts[cid], cfg)}</td>"
            f"<td>{penalties}</td>"
            f"<td>{chips or '—'}</td>"
            f'<td><a href="{_e(trakstar_url(data.opening_id, cid))}">Trakstar</a></td>'
            "</tr>"
        )
    return f"<table>{head}{''.join(body)}</table>"


def _delta_html(data: ReportInput) -> str:
    delta = data.delta
    labels = (
        ("new", "New applicants"),
        ("newly_accepted", "Newly shortlisted"),
        ("newly_gated", "Newly eliminated"),
        ("no_slot", "Would have qualified, no slot"),
    )
    if not any(delta.get(key) for key, _ in labels):
        return '<p class="empty-note">No changes since the last run.</p>'

    blocks = []
    for key, label in labels:
        ids = delta.get(key) or []
        if not ids:
            continue
        items = []
        for cid in ids:
            assessment = data.assessments.get(cid)
            score = f" — {assessment.final:g}" if assessment else ""
            gate = f" ({assessment.gate})" if assessment and assessment.gate else ""
            items.append(f"<li>{_e(data.name(cid))}{score}{gate}</li>")
        blocks.append(f"<p><strong>{label}</strong> ({len(ids)})</p><ul>{''.join(items)}</ul>")
    return "".join(blocks)


def render_html(data: ReportInput, cfg: RoleConfig) -> str:
    cut = data.cut
    pct = round(cfg.cap_fraction * 100)
    accepted_now = len(cut.accepted)
    open_slots = max(0, cut.cap - accepted_now)

    needs_rows = "".join(
        f"<tr><td>{_e(data.name(cid))}</td><td>{_e(reason)}</td>"
        f'<td><a href="{_e(trakstar_url(data.opening_id, cid))}">Trakstar</a></td></tr>'
        for cid, reason in sorted(data.needs_review.items())
    )

    gated_blocks = []
    by_gate: dict[str, list[int]] = {}
    for cid in cut.gated:
        assessment = data.assessments.get(cid)
        if assessment and assessment.gate:
            by_gate.setdefault(assessment.gate, []).append(cid)
    for gate in sorted(by_gate):
        items = []
        for cid in by_gate[gate]:
            reasons = "".join(
                f"<blockquote>{_e(r)}</blockquote>" for r in data.assessments[cid].gate_reasons
            )
            items.append(
                f"<tr><td>{_e(data.name(cid))}</td><td>{reasons}</td>"
                f'<td><a href="{_e(trakstar_url(data.opening_id, cid))}">Trakstar</a></td></tr>'
            )
        gated_blocks.append(
            f"<h3>{_e(gate)} — {len(by_gate[gate])} candidate(s)</h3>"
            f"<table><tr><th>Name</th><th>Evidence</th><th>Links</th></tr>{''.join(items)}</table>"
        )

    gate_rows = "".join(
        f"<tr><td>{k}</td><td>{_e(v)}</td></tr>" for k, v in sorted(cfg.gates.items())
    )
    penalty_rows = "".join(
        f"<tr><td>{k}</td><td>−{_e(v)}</td></tr>" for k, v in sorted(cfg.penalties.items())
    )
    criteria_rows = "".join(
        f"<tr><td>{_e(c.label)}</td><td>{c.max}</td></tr>" for c in cfg.criteria
    )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CV screening — {_e(cfg.position_title)} — {_e(data.run_id)}</title>
<style>{_CSS}</style></head><body>
<h1>{_e(cfg.position_title)}</h1>
<p class="sub">Screening run {_e(data.run_id)} · opening {_e(data.opening_id)} ·
rubric v{cfg.rubric_version}</p>

<h2>Run</h2>
<div class="stats">
  <div class="stat"><b>{cut.pool_size}</b><span>candidates in pool</span></div>
  <div class="stat"><b>{cut.cap}</b><span>cap ({pct}% of {cut.pool_size})</span></div>
  <div class="stat"><b>{accepted_now}</b><span>shortlisted</span></div>
  <div class="stat"><b>{open_slots}</b><span>open slots</span></div>
  <div class="stat"><b>{len(cut.gated)}</b><span>eliminated</span></div>
  <div class="stat"><b>{len(data.needs_review)}</b><span>need review</span></div>
</div>

<h2>Delta since last run</h2>
{_delta_html(data)}

<h2>Shortlist</h2>
{_candidate_table(data, cut.accepted, cfg)}

<h2>Waitlist</h2>
{_candidate_table(data, cut.waitlist, cfg, grey=True)}

<h2>Gated</h2>
{''.join(gated_blocks) or '<p class="empty-note">None.</p>'}

<h2>Needs manual review</h2>
{f'<table><tr><th>Name</th><th>Reason</th><th>Links</th></tr>{needs_rows}</table>'
 if needs_rows else '<p class="empty-note">None.</p>'}

<footer>
<h2>Methodology</h2>
<p>Candidates are ranked on a 100-point rubric, then the top {pct}% of the whole
pool is shortlisted. Eliminated and unreviewable CVs still count toward that
denominator. Shortlist decisions are sticky: once shortlisted, a candidate is
not displaced by later applicants.</p>
<p>The judging model never sees names, emails, phone numbers, addresses, or
school names — those are redacted before evaluation and re-attached only in this
report. Every score and every elimination quotes the CV text it rests on; a
score whose quote could not be found in the CV is zeroed and marked.</p>
<p><strong>Calibration:</strong> {_e(data.calibration_note) or 'not run'}</p>
<table><tr><th>Criterion</th><th>Max</th></tr>{criteria_rows}</table>
<table><tr><th>Gate rule</th><th>Value</th></tr>{gate_rows}</table>
<table><tr><th>Penalty</th><th>Points</th></tr>{penalty_rows}</table>
<p>This report ranks and eliminates; it does not decide. Hiring decisions
remain with the team.</p>
</footer>
</body></html>"""


def render_markdown(data: ReportInput, cfg: RoleConfig) -> str:
    cut = data.cut
    pct = round(cfg.cap_fraction * 100)
    lines = [
        f"*{cfg.position_title}* — screening run {data.run_id}",
        f"Pool: {cut.pool_size} · Cap: {cut.cap} ({pct}%) · "
        f"Shortlisted: {len(cut.accepted)} · Open slots: {max(0, cut.cap - len(cut.accepted))} · "
        f"Eliminated: {len(cut.gated)} · Need review: {len(data.needs_review)}",
        "",
    ]

    sections = (
        ("newly_accepted", "Newly shortlisted"),
        ("new", "New applicants"),
        ("newly_gated", "Newly eliminated"),
        ("no_slot", "Would have qualified, no slot"),
    )
    if not any(data.delta.get(key) for key, _ in sections):
        lines.append("No changes since the last run.")
    else:
        for key, label in sections:
            ids = data.delta.get(key) or []
            if not ids:
                continue
            lines.append(f"*{label}* ({len(ids)})")
            for cid in ids[:12]:
                assessment = data.assessments.get(cid)
                score = f" — {assessment.final:g}" if assessment else ""
                gate = f" [{assessment.gate}]" if assessment and assessment.gate else ""
                summary = ""
                verdict = data.verdicts.get(cid)
                if verdict and key in {"newly_accepted", "no_slot"}:
                    summary = " — " + (verdict["fit"].get("summary") or "").split(".")[0]
                lines.append(f"• {data.name(cid)}{score}{gate}{summary}")
            if len(ids) > 12:
                lines.append(f"• …and {len(ids) - 12} more")
            lines.append("")

    lines.append(f"Full report: report/{data.run_id}.html")
    return "\n".join(lines)


def rows_for_csv(
    data: ReportInput, cfg: RoleConfig, statuses: tuple[str, ...]
) -> list[dict[str, Any]]:
    wanted = set(statuses)
    rows: list[dict[str, Any]] = []
    ordered = cut_order(data)

    for cid in ordered:
        entry = data.ledger.get(cid)
        assessment = data.assessments.get(cid)
        if entry is None or assessment is None or entry.status not in wanted:
            continue
        verdict = data.verdicts.get(cid, {})
        precheck = data.prechecks.get(cid, {})
        candidate = data.candidates.get(cid, {})
        row: dict[str, Any] = {
            "id": cid,
            "name": data.name(cid),
            "email": candidate.get("email", ""),
            "status": entry.status,
            "gate": assessment.gate or "",
            "final": assessment.final,
            "fit": assessment.fit,
            "bonus": assessment.bonus,
        }
        for c in cfg.criteria:
            row[c.key] = float(
                (verdict.get("fit", {}).get("scores", {}).get(c.key, {}) or {}).get("score") or 0
            )
        row["penalties"] = ";".join(
            f"{p['kind']}(-{p['points']})" for p in assessment.penalties
        )
        row["flags"] = ";".join(assessment.flags)
        row["linkedin_url"] = (precheck.get("linkedin") or {}).get("url") or ""
        row["trakstar_url"] = trakstar_url(data.opening_id, cid)
        row["first_seen_run"] = entry.first_seen_run
        row["status_changed_run"] = entry.status_changed_run
        rows.append(row)
    return rows


def cut_order(data: ReportInput) -> list[int]:
    """Accepted, then waitlist, then gated, then needs-review — report order."""
    return (
        list(data.cut.accepted)
        + list(data.cut.waitlist)
        + list(data.cut.gated)
        + list(data.cut.needs_review)
    )


def _write_csv(path: Path, rows: list[dict[str, Any]], cfg: RoleConfig) -> None:
    columns = [
        "id", "name", "email", "status", "gate", "final", "fit", "bonus",
        *cfg.criterion_keys(),
        "penalties", "flags", "linkedin_url", "trakstar_url",
        "first_seen_run", "status_changed_run",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def write_all(data: ReportInput, cfg: RoleConfig, paths: Paths) -> dict[str, Path]:
    safe_run = data.run_id.replace(":", "").replace(" ", "_")
    html = render_html(data, cfg)
    markdown = render_markdown(data, cfg)

    out: dict[str, Path] = {
        "html": paths.report / f"{safe_run}.html",
        "markdown": paths.report / f"{safe_run}.md",
        "shortlist_csv": paths.report / f"{safe_run}-shortlist.csv",
        "all_csv": paths.report / f"{safe_run}-all.csv",
        "latest_html": paths.report / "latest.html",
        "latest_markdown": paths.report / "latest.md",
    }

    for key in ("html", "latest_html"):
        out[key].parent.mkdir(parents=True, exist_ok=True)
        out[key].write_text(html)
    for key in ("markdown", "latest_markdown"):
        out[key].write_text(markdown)

    _write_csv(out["shortlist_csv"], rows_for_csv(data, cfg, ("accepted",)), cfg)
    _write_csv(
        out["all_csv"],
        rows_for_csv(data, cfg, ("accepted", "waitlist", "gated", "needs_review")),
        cfg,
    )

    run_record = paths.runs / f"{safe_run}.json"
    run_record.parent.mkdir(parents=True, exist_ok=True)
    run_record.write_text(
        json.dumps(
            {
                "run_id": data.run_id,
                "opening_id": data.opening_id,
                "rubric_version": cfg.rubric_version,
                "pool_size": data.cut.pool_size,
                "cap": data.cut.cap,
                "accepted": data.cut.accepted,
                "waitlist": data.cut.waitlist,
                "gated": data.cut.gated,
                "needs_review": data.needs_review,
                "newly_accepted": data.cut.newly_accepted,
                "newly_gated": data.cut.newly_gated,
                "no_slot": data.cut.no_slot,
                "quality_floor": data.cut.quality_floor,
                "calibration_window": data.cut.calibration_window,
                "calibration_note": data.calibration_note,
            },
            indent=2,
        )
    )
    out["run_record"] = run_record
    return out
