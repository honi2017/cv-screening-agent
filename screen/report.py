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

# Per-candidate deep link, CONFIRMED against the live Trakstar UI.
#
# The Trakstar web app is a hash-routed single-page app and the API exposes no
# permalink on the candidate object (no `url`, no `link`), so this route cannot
# be derived from the API — it was read off the address bar. An earlier guessed
# form (`#candidates/{id}?opening={id}`) silently fell back to the opening's
# candidate list, which is why the shape here matters:
#
# the candidate is a `view:<id>` segment appended to the opening's LIST route,
# not a route of its own. The `orderBy`/`order` parameters are part of that list
# route; they only set the underlying list's sort and do not affect which
# candidate opens.
#
# Known-good example:
#   .../app/#candidates/list/selected_openings=704353&orderBy=date_created&order=desc/view:71588398/
#
# Keep the trailing slash. This template is the only place the route is
# encoded — correcting it later means editing this string alone.
TRAKSTAR_URL_TEMPLATE = (
    "https://anduin.hire.trakstar.com/app/#candidates/list"
    "/selected_openings={opening_id}&orderBy=date_created&order=desc"
    "/view:{candidate_id}/"
)


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
    return TRAKSTAR_URL_TEMPLATE.format(candidate_id=candidate_id, opening_id=opening_id)


def resume_url(candidate: dict[str, Any]) -> str | None:
    """The candidate's resume download link, straight from the ATS record.

    This is a tokenised URL issued by the applicant-tracking system: it grants
    access to the CV file with no authentication required, so treat it as
    sensitive in the same way as the CV contents it points to.
    """
    return (candidate.get("resume") or {}).get("file_url") or None


def _e(value: Any) -> str:
    return html_mod.escape(str(value if value is not None else ""), quote=True)


_CSS = """
:root {
  --bg:#fff; --fg:#16181d; --muted:#6b7280; --line:#e5e7eb; --accent:#1f6feb;
  --ok:#0f7b3f; --ok-bg:#eaf6ef;
  --warn:#9a6700; --warn-bg:#fbf0da;
  --bad:#b42318; --bad-bg:#fbeae8;
  --ref-fg:#3f4b5e; --ref-bg:#f1f4f8; --ref-line:#a9b6c6;
  --chip:#f3f4f6;
}
* { box-sizing: border-box; }
body { margin:0; padding:24px; background:var(--bg); color:var(--fg);
       font:14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
       /* This report gets printed; ask the browser to keep chip/section fills
          in a printed copy rather than dropping them to plain text by
          default. The non-colour cues (border style, shape, icon) below are
          still what carry the reference/penalty distinction if a given
          printer or print dialog ignores this anyway. */
       -webkit-print-color-adjust:exact; print-color-adjust:exact; }
h1 { font-size:22px; margin:0 0 4px; }
/* Section headers: a solid background band + left accent bar so it is
   unmistakable on a quick scroll where one section ends and the next
   begins -- applies uniformly to every section (Run/Delta/Shortlist/
   Waitlist/Gated/Needs review/Methodology) rather than singling any one
   out for colour, which keeps this a spacing/weight change, not a redesign. */
h2 { font-size:14px; margin:34px 0 10px; padding:9px 12px;
     background:var(--chip); border-left:4px solid var(--accent); border-radius:0 6px 6px 0;
     text-transform:uppercase; letter-spacing:.04em; font-weight:700; color:var(--fg); }
/* Per-gate sub-headings inside "Gated" only (see `_TABLE_COLUMNS` usage below) --
   reuses the same maroon as the elimination marker so a gate group reads as
   the same severity class as the inline "flagged ... after acceptance" note. */
h3 { font-size:12px; margin:18px 0 6px; padding:4px 10px; display:inline-block;
     background:var(--bad-bg); color:var(--bad); border-left:3px solid var(--bad);
     border-radius:0 4px 4px 0; font-weight:700; }
.sub { color:var(--muted); margin:0 0 20px; }
.stats { display:flex; flex-wrap:wrap; gap:16px; margin:16px 0 8px; }
.stat { background:var(--chip); border-radius:8px; padding:10px 14px; min-width:110px; }
.stat b { display:block; font-size:20px; } .stat span { color:var(--muted); font-size:12px; }
table { width:100%; border-collapse:collapse; margin:8px 0 4px; }
th, td { text-align:left; padding:7px 8px; border-bottom:1px solid var(--line);
         vertical-align:top; font-size:13px; }
th { color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.03em; }
tr.grey td { color:var(--muted); }
/* The score is the number a reader's eye should land on first in every row --
   sized and weighted well above the rest of the row's 13px text. */
.score { font-size:19px; font-weight:800; font-variant-numeric:tabular-nums;
         letter-spacing:-.01em; color:var(--fg); }
.bars { display:flex; gap:2px; align-items:flex-end; height:22px; }
.bar { width:9px; background:var(--accent); opacity:.85; border-radius:1px 1px 0 0; }
.bar.empty { background:var(--line); }

/* Three severity classes, restyled to read apart at a glance without
   leaning on colour alone (see the `.chip.ref` comment below for why that
   matters most there):
     .chip      -- a penalty-derived flag: amber pill, filled.
     .gate      -- a gate/elimination marker: maroon badge, filled.
     .chip.ref  -- a reference-only flag: slate tag, dashed border, squared
                   corners, its own icon glyph. Never a demerit -- see the
                   `reference_flags` field comment on `screen.rank.Assessment`. */
.chip { display:inline-block; background:var(--warn-bg); color:var(--warn);
        border:1px solid transparent; border-radius:999px; padding:1px 8px;
        margin:0 4px 4px 0; font-size:11px; font-weight:600; white-space:nowrap; }
.chip.bad { background:var(--bad-bg); color:var(--bad); }

/* Low-key metadata ("since <run-id>") -- deliberately NOT chip-styled, so it
   never competes with (or gets mistaken for) the severity classes above. */
.meta-tag { display:inline-block; color:var(--muted); font-size:11px; font-style:italic; }

.gate { display:inline-block; background:var(--bad-bg); color:var(--bad);
        font-weight:700; font-size:11px; padding:1px 8px; border-radius:3px; }

/* Reference flags used to carry a text label prefixing the whole group,
   since removed at the hiring manager's request (see `_reference_html`'s
   docstring) -- the distinction from penalty chips now has to survive on
   styling alone, including for a colour-blind reader and a printed,
   greyscale copy. So it is carried by THREE things at once, not just
   colour: a dashed border (penalty/gate chips have none), squared corners
   (they are full pills), and a leading icon glyph (`.ref-icon`) that has no
   equivalent on any other chip. Restyle this rule if the distinction ever
   needs to be stronger -- do not reach for a text label again. */
.ref-group { margin-top:4px; }
.chip.ref { background:var(--ref-bg); color:var(--ref-fg); border:1px dashed var(--ref-line);
            border-radius:4px; font-weight:500; }
.ref-icon { margin-right:4px; opacity:.75; }

details { margin:2px 0 0; }
/* The evidence toggle reads as a clickable control (bordered pill + a
   direction-indicating glyph that flips on open), not as a stray link. */
summary { cursor:pointer; display:inline-flex; align-items:center; gap:5px;
          color:var(--accent); font-size:11px; font-weight:600; padding:3px 10px;
          border:1px solid var(--accent); border-radius:999px;
          background:rgba(31,111,235,.06); list-style:none; }
summary::-webkit-details-marker { display:none; }
summary::before { content:"\\25b8"; font-size:9px; }
details[open] summary { background:rgba(31,111,235,.14); }
details[open] summary::before { content:"\\25be"; }
.detail-row td { padding-top:0; padding-bottom:0; border-bottom:none; }
/* A coloured top edge (echoing the toggle above it) plus squared top-left
   corner reads as "this panel hangs off the row above it", not a separate
   block that happens to sit underneath. */
.detail { background:#fafafa; border:1px solid var(--line); border-top:2px solid var(--accent);
          border-radius:0 6px 6px 6px; padding:10px 12px; margin:2px 0 10px; width:100%; }
.detail dl { display:grid; grid-template-columns:190px 1fr; gap:4px 12px; margin:0; }
.detail dt { color:var(--muted); } .detail dd { margin:0; }
blockquote { margin:2px 0 6px; padding:4px 10px; border-left:3px solid var(--line);
             color:#374151; font-style:italic; }
/* Shared styling for the "the cut is not what a naive reading would
   suggest" family of warnings -- the permanent inversion guard
   (`_inversion_html`) and the over-cap guard (`_over_cap_html`) both use
   this one class via `_cut_guard_note_html` so the two read as the same
   severity, deliberately pitched above the neutral `.chip.ref` reference
   chips (this is a real breach of a stated requirement) but below the
   maroon `.bad`/`.gate` styling (this is a hiring document, informative
   rather than alarming). */
.inversion-note { color:var(--warn); font-weight:600; margin:8px 0; }
footer { margin-top:36px; color:var(--muted); font-size:12px; }
footer table { max-width:760px; }
.empty-note { color:var(--muted); font-style:italic; }
/* Per-row link cluster: quieted to muted, divider-separated text so it reads
   as reference furniture rather than competing with the score/flags. */
.links { font-size:11px; }
.links a { color:var(--muted); text-decoration:none; white-space:nowrap;
           padding-right:8px; margin-right:8px; border-right:1px solid var(--line); }
.links a:last-child { border-right:none; margin-right:0; padding-right:0; }
.links a:hover { color:var(--accent); text-decoration:underline; }
"""


# Every outbound link opens in a new tab, so a reviewer working down the list
# never loses their place in the report. `noopener` denies the opened page a
# window.opener handle back to this one, and `noreferrer` stops this page's
# location leaking as a referrer — worth having when the report is a local file
# full of applicant PII and the resume link is an unauthenticated token URL.
_NEW_TAB = 'target="_blank" rel="noopener noreferrer"'


def _links_html(data: ReportInput, cid: int) -> str:
    links = [
        f'<a {_NEW_TAB} href="{_e(trakstar_url(data.opening_id, cid))}">Trakstar</a>'
    ]
    url = resume_url(data.candidates.get(cid, {}))
    if url:
        links.append(f'<a {_NEW_TAB} href="{_e(url)}">Resume</a>')
    linkedin = (data.prechecks.get(cid) or {}).get("linkedin") or {}
    linkedin_url = linkedin.get("url")
    # Rendered only when a profile is actually present: the "no LinkedIn"
    # flag chip already communicates absence, and a dead anchor is worse
    # than none -- see the -20 no-LinkedIn penalty, which exists precisely
    # so a human clicks through here to verify the person is real.
    if linkedin.get("present") and linkedin_url:
        links.append(f'<a {_NEW_TAB} href="{_e(linkedin_url)}">LinkedIn</a>')
    return f'<span class="links">{"".join(links)}</span>'


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


def _reference_html(assessment: Assessment) -> str:
    """Reference flags render in their own neutrally-styled group, separate
    from the penalty-derived chips in the Flags column -- see the
    `reference_flags` field comment on `screen.rank.Assessment`. They never
    affect `final`/status (compute_penalties never produces these kinds), and
    when a candidate has none this returns "" -- no group, no placeholder,
    nothing -- so a clean candidate's row carries no trace of it at all.

    This group used to be prefixed with a literal "for reference -- no score
    effect" label; the hiring manager asked for that text gone. The
    distinction from penalty chips is now carried entirely by the `.chip.ref`
    styling in `_CSS` (a dashed border, squared corners, and the `ref-icon`
    glyph below) -- deliberately not colour alone, so it still holds for a
    colour-blind reader or a greyscale printout. Do not reintroduce the old
    label text here; restyle `.chip.ref` instead if the distinction ever
    needs to be stronger.
    """
    if not assessment.reference_flags:
        return ""
    chips = "".join(
        f'<span class="chip ref" title="{_e(rf.get("detail"))}">'
        f'<span class="ref-icon" aria-hidden="true">◇</span>'
        f'{_e(rf.get("label", rf["kind"]))}</span>'
        for rf in assessment.reference_flags
    )
    return f'<div class="ref-group">{chips}</div>'


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

    # Reference flags: deliberately worded "For reference", never "Flag" or
    # "Penalty", and carry no points -- see the `reference_flags` field
    # comment on screen.rank.Assessment. `sentences`, when present (the
    # offshore_claim kind), is the whole point of that flag, so it is quoted
    # verbatim exactly like a judge's evidence quote above.
    for rf in assessment.reference_flags:
        quotes = "".join(f"<blockquote>{_e(s)}</blockquote>" for s in rf.get("sentences") or [])
        rows.append(
            f"<dt>For reference — {_e(rf['kind'])}</dt><dd>{quotes}{_e(rf.get('detail'))}</dd>"
        )

    years = precheck.get("years_experience") or {}
    linkedin = precheck.get("linkedin") or {}
    meta = precheck.get("pdf_meta") or {}
    if linkedin.get("present"):
        # The URL itself, not just the source, so the audit trail records
        # WHICH profile was checked -- this is the only place a reviewer can
        # click through to verify the person is real (the pipeline never
        # fetches LinkedIn itself). Escaped like any other candidate-supplied
        # text.
        linkedin_detail = (
            f"present via {_e(linkedin.get('source'))} — {_e(linkedin.get('url'))}"
            f" — name match: {_e(linkedin.get('name_matches'))}"
        )
        # "live" is the only liveness verdict ever surfaced here, and only as
        # positive corroboration -- a 200 means LinkedIn confirms a real,
        # publicly-visible profile behind this URL. "unknown" (every other
        # status, a network error, or a timeout -- see
        # screen.linkedin_check's module docstring) carries no meaning
        # either way and is deliberately shown as nothing at all: an
        # ordinary member's profile is private by default and looks
        # identical, over this check, to one that doesn't exist, so
        # surfacing "unknown" here would read as unearned doubt about a
        # real person.
        if linkedin.get("liveness") == "live":
            linkedin_detail += " — profile confirmed publicly visible"
    else:
        linkedin_detail = "not found"
    rows.extend(
        [
            f"<dt>Years computed</dt><dd>{_e(years.get('computed'))} "
            f"(confidence: {_e(years.get('confidence'))})</dd>",
            f"<dt>LinkedIn</dt><dd>{linkedin_detail}</dd>",
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


_TABLE_COLUMNS = ("#", "Name", "Score", "Criteria", "Penalties", "Flags", "Links")


def _candidate_table(data: ReportInput, ids: list[int], cfg: RoleConfig, grey: bool = False) -> str:
    if not ids:
        return '<p class="empty-note">None.</p>'

    head = "<tr>" + "".join(f"<th>{col}</th>" for col in _TABLE_COLUMNS) + "</tr>"
    body = []
    # This table numbers its rows, so the numbers are a rank claim and must
    # agree with the Score column. Sort here rather than trusting the caller:
    # the lists once arrived ordered by candidate id and the page rendered rank
    # 1 at 75 points above rank 2 at 85 — a silent lie no test caught, only a
    # reader did. Candidates with no assessment sort last; they are skipped below.
    ids = sorted(
        ids,
        key=lambda cid: (
            -data.assessments[cid].final if cid in data.assessments else 1,
            cid,
        ),
    )
    for rank, cid in enumerate(ids, start=1):
        assessment = data.assessments.get(cid)
        if assessment is None:
            continue
        entry = data.ledger.get(cid)
        chips = "".join(f'<span class="chip">{_e(f)}</span>' for f in assessment.flags)
        penalties = (
            f"−{assessment.penalty_total:g}" if assessment.penalty_total else "—"
        )
        # `.meta-tag`, not `.chip` -- this is bookkeeping furniture, not a
        # penalty/flag, and must never share styling with the severity chips
        # in the Flags column (see the `.meta-tag` comment in `_CSS`).
        since = (
            f'<br><span class="meta-tag">since {_e(entry.first_seen_run)}</span>'
            if entry and entry.status == "accepted"
            else ""
        )
        gate_note = (
            f'<br><span class="gate">flagged {_e(assessment.gate)} after acceptance</span>'
            if assessment.gate and entry and entry.status == "accepted"
            else ""
        )
        row_class = "grey" if grey else ""
        body.append(
            f'<tr class="{row_class}">'
            f"<td>{rank}</td>"
            f"<td>{_e(data.name(cid))}{since}{gate_note}</td>"
            f'<td class="score">{assessment.final:g}</td>'
            f"<td>{_bars_html(data.verdicts[cid], cfg)}</td>"
            f"<td>{penalties}</td>"
            f"<td>{chips or '—'}{_reference_html(assessment)}</td>"
            f"<td>{_links_html(data, cid)}</td>"
            "</tr>"
        )
        detail = _detail_html(data, cid, cfg)
        if detail:
            detail_class = "detail-row grey" if grey else "detail-row"
            body.append(
                f'<tr class="{detail_class}">'
                f'<td colspan="{len(_TABLE_COLUMNS)}">{detail}</td></tr>'
            )
    return f"<table>{head}{''.join(body)}</table>"


def _delta_html(data: ReportInput) -> str:
    delta = data.delta
    # `no_slot` is deliberately not rendered. It collects everyone who clears the
    # quality floor once the cap is full, so with no floor (a first run) or a low
    # one it is simply the entire waitlist — 46 people down to 57 points on the
    # real pool — restated under a heading claiming they would have qualified.
    # The Waitlist section already shows them, in rank order, with their flags.
    # The field is still computed and kept in the run record as an audit trail.
    labels = (
        ("new", "New applicants"),
        ("newly_accepted", "Newly shortlisted"),
        ("newly_gated", "Newly eliminated"),
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


def _inversion_count(data: ReportInput) -> int:
    """How many waitlisted candidates score strictly above the lowest-scoring
    accepted candidate.

    This is the permanent guard for the inversion the hiring manager once
    found invisible: a waitlisted candidate scoring 74 sitting above accepted
    candidates at 70, 65, 63, and 62 with nothing on the page explaining it.
    An earlier "would have qualified, no slot" section explained this and was
    removed at the hiring manager's request (see `_delta_html`'s comment on
    `no_slot`); removing it is exactly what made the inversion invisible, so
    this exists specifically so removing that section can never do that
    again.

    Ties, the calibration window (the main agent may reorder within it, see
    `rank_and_cut`), and the `cid in ledger` quality-floor exemption for an
    existing waitlisted candidate can all legitimately produce a small,
    explainable inversion even outside a re-baseline run. A nonzero count
    here is not asserting a bug -- it is making an ordinary, sometimes
    legitimate consequence of the cap visible instead of silent.
    """
    accepted_scores = [
        data.assessments[cid].final for cid in data.cut.accepted if cid in data.assessments
    ]
    if not accepted_scores:
        return 0
    floor = min(accepted_scores)
    return sum(
        1
        for cid in data.cut.waitlist
        if cid in data.assessments and data.assessments[cid].final > floor
    )


def _cut_guard_note_html(message_html: str) -> str:
    """Shared rendering for the "the cut is not what a naive reading would
    suggest" family of warnings -- see the `.inversion-note` comment in
    `_CSS`. Used by both `_inversion_html` and `_over_cap_html` so the two
    guards can never visually drift apart from each other; restyle
    `.inversion-note` if either ever needs to look different, rather than
    having one of the two call sites grow its own markup.

    `message_html` is trusted, pre-built markup, not raw candidate data --
    both call sites only ever interpolate integers, floats, and static
    strings into it.
    """
    return f'<p class="inversion-note">{message_html}</p>'


def _inversion_html(data: ReportInput) -> str:
    """Render nothing at all when there is no inversion -- see
    `_inversion_count`. Right after a re-baseline this is always empty by
    construction (the cut was just recomputed purely by score); the line
    earns its place again once stickiness re-freezes the cut on later runs.
    """
    count = _inversion_count(data)
    if not count:
        return ""
    noun = "candidate" if count == 1 else "candidates"
    verb = "scores" if count == 1 else "score"
    return _cut_guard_note_html(
        f"{count} waitlisted {noun} {verb} above the "
        "lowest-scoring accepted candidate — held out by the cap, not by score."
    )


def _over_cap_html(data: ReportInput) -> str:
    """H2 (docs/production-readiness-review.md): render the sticky-seating
    overage right beside the shortlist, prominently -- see
    `CutResult.over_cap`.

    `rank_and_cut` re-seats previously-accepted candidates before consulting
    any score (deliberate stickiness, not a bug -- see its docstring), so a
    pool that shrinks between runs can leave more people seated than the
    current cap allows with nothing else on the page saying so. Renders
    nothing at all when compliant (`over_cap == 0`); this function only
    ever reads `cut.over_cap`/`cut.accepted_share`, it never sets them or
    changes who is accepted -- see the comment on those properties in
    `screen.rank.CutResult`.
    """
    cut = data.cut
    if cut.over_cap <= 0:
        return ""
    accepted_now = len(cut.accepted)
    pct = f"{cut.accepted_share * 100:.1f}".rstrip("0").rstrip(".")
    return _cut_guard_note_html(
        f"{accepted_now} candidates are seated against a cap of {cut.cap} "
        f"({pct}% of the pool) — {cut.over_cap} over the ceiling. The excess is "
        "held by previously-accepted candidates who kept their slots under "
        "sticky acceptance, not by anyone newly added. "
        "<code>rank --rebaseline</code> recomputes the cut purely on current "
        "scores, if the team wants that."
    )


def render_html(data: ReportInput, cfg: RoleConfig) -> str:
    cut = data.cut
    pct = round(cfg.cap_fraction * 100)
    accepted_now = len(cut.accepted)
    open_slots = max(0, cut.cap - accepted_now)

    needs_rows = "".join(
        f"<tr><td>{_e(data.name(cid))}</td><td>{_e(reason)}</td>"
        f"<td>{_links_html(data, cid)}</td></tr>"
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
                f"<td>{_links_html(data, cid)}</td></tr>"
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
{_over_cap_html(data)}
{_inversion_html(data)}
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
<p>The Trakstar link opens the candidate's own record in the applicant tracking
system; the Resume link opens their CV file directly. Treat the Resume link as
sensitive — it grants access to the file without signing in.</p>
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

    # `no_slot` omitted here for the same reason as in the HTML delta — see the
    # comment in _delta_html.
    sections = (
        ("newly_accepted", "Newly shortlisted"),
        ("new", "New applicants"),
        ("newly_gated", "Newly eliminated"),
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
                if verdict and key == "newly_accepted":
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
        row["resume_url"] = resume_url(candidate) or ""
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
        "penalties", "flags", "linkedin_url", "trakstar_url", "resume_url",
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
                # H2 (docs/production-readiness-review.md): observational
                # only -- see the comment on these properties in
                # screen.rank.CutResult. Recorded here so the run record
                # answers "were we over the cap on this run" on its own,
                # the same way `rebaseline`/`unseated` below answer "why did
                # a status change" -- neither one changes `accepted`.
                "over_cap": data.cut.over_cap,
                "accepted_share": data.cut.accepted_share,
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
                # See screen.rank.rank_and_cut's `rebaseline` docstring: the
                # opt-in escape hatch from ledger stickiness. Recorded here
                # too (not just in the rank-stage `.cut.json`) so the
                # human-facing audit artifact answers "why did this status
                # change" on its own.
                "rebaseline": data.cut.rebaseline,
                "unseated": data.cut.unseated,
            },
            indent=2,
        )
    )
    out["run_record"] = run_record
    return out
