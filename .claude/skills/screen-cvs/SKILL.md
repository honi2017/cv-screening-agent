---
name: screen-cvs
description: Screen Trakstar Hire applicants for an opening against the JD — fetch new CVs, run deterministic checks, judge each CV with a subagent, rank, apply the 20% cap, and write an HTML/Markdown/CSV report. Use when asked to screen CVs, review applicants, refresh the shortlist, or run the daily CV screening.
---

# Screen CVs

Runs the screening pipeline for one opening. Python does every deterministic
step; you do only the judging and the calibration.

**Never** invent scores, skip the quote requirement, or write to Trakstar.
This pipeline is **report-only** — it never calls a write endpoint, and it
never changes a candidate's stage, state, notes, or labels in Trakstar. The
fetch is scoped to opening **704353** (Forward Deployed Engineer) and to
**active** candidates only (`state=in_process`) — never an unfiltered query,
and never a rejected candidate a human has already decided on.

## 0. Setup

Work from the repository root — every path below is relative to it. Read
`roles/fde/role.json` only if you need a number; the CLI already applies it.

The scheduled job sets its working directory to the repo, and `.env` is loaded
from the working directory, so do not `cd` to an absolute path: that would break
this skill for every checkout other than the one it was written on.

Default source is Trakstar. `TRAKSTAR_API_KEY` comes from the repo-root `.env`
(loaded automatically) or the environment. If it is unset, fall back to
`--source folder --path <dir>` and say so in your summary.

## 1. Fetch, parse, precheck

```bash
uv run python -m screen run --today "$(date +%Y-%m)" --out /tmp/screen-run.json
```

This fetches only changed candidates, parses only changed PDFs, prechecks only
what moved, and prints the pending list. Read `/tmp/screen-run.json`.

- **Exit code 3** means the rubric version changed and this would re-judge the
  whole pool. In an interactive session, tell the user how many candidates are
  affected and ask before re-running with `--full`. In a scheduled run, stop and
  report — do not pass `--full` on your own.
- **Exit code 4** means the Trakstar key is missing or rejected. Report it and
  stop.

## 2. Judge each pending candidate

For every id in `pending`, dispatch **one subagent**. Batch them ~8 at a time.
Give each subagent exactly this task:

> Read these three files:
> - `roles/fde/redflag_prompt.md` — your Pass A instructions
> - `roles/fde/fit_prompt.md` — your Pass B instructions
> - `data/<opening>/redacted/<id>.md` — the redacted CV
> - `data/<opening>/prechecks/<id>.json` — the precomputed facts
>
> Run Pass A: substitute `{{REDACTED_CV}}` and `{{PRECHECKS}}` and produce the
> RedFlag JSON. Then run Pass B: substitute `{{REDACTED_CV}}`, `{{PRECHECKS}}`,
> and `{{REDFLAG_RESULT}}` (your Pass A output) and produce the Fit JSON.
>
> Write the combined verdict to `data/<opening>/verdicts/<id>.json`:
> `{"candidate_id": <id>, "redflag": <Pass A output>, "fit": <Pass B output>}`
>
> Then validate and stamp it:
> ```bash
> uv run python -c "
> import json,sys
> from pathlib import Path
> from screen.config import load_role
> from screen.verdict import validate_verdict, verdict_key, write_verdict
> cid=<id>; op='<opening>'
> cfg=load_role(Path('roles/fde'))
> red=Path(f'data/{op}/redacted/{cid}.md').read_text()
> pc=json.loads(Path(f'data/{op}/prechecks/{cid}.json').read_text())
> raw=json.loads(Path(f'data/{op}/verdicts/{cid}.json').read_text())
> v=validate_verdict(raw,cfg,red)
> v['verdict_key']=verdict_key(red,pc,cfg.rubric_version)
> write_verdict(v,Path(f'data/{op}/verdicts/{cid}.json'))
> print('ok', v['quote_warnings'])
> "
> ```
> If validation raises, read the error, fix your JSON, and retry **once**. If it
> fails again, delete the verdict file and report the candidate id as failed.
> Report back: the candidate id, the fit total, any quote warnings, and whether
> you flagged anything Tier 1.

Requirements you must enforce when reviewing subagent results:

- A candidate with no verdict file is `judge_failed` — it lands in "needs manual
  review", never in rejected. Do not fabricate a verdict to fill the gap.
- Quote warnings are expected occasionally; many warnings across many candidates
  means the prompt or the redaction is off. Say so in your summary.

## 3. Calibrate the cut

```bash
uv run python -m screen rank --prepare --out /tmp/screen-prepare.json
```

Read the window. These are the candidates straddling the accept line, with their
scores, penalties, flags, and per-criterion quotes. Independent scoring drifts,
so compare them **against each other** on the evidence and decide whether the
order is right.

If you would reorder them, write `/tmp/calibration.json`:

```json
{"order": [<candidate ids, best first, from the window only>],
 "note": "One or two sentences on what you changed and why."}
```

You may only reorder within the window. You cannot add anyone from outside it and
cannot widen the cut — the CLI enforces both. If the order looks right, write
just the note with the original order.

## 4. Finalize and report

```bash
RUN_ID=$(python3 -c "import json;print(json.load(open('/tmp/screen-prepare.json'))['run_id'])")
uv run python -m screen rank --finalize --run-id "$RUN_ID" --calibration /tmp/calibration.json
uv run python -m screen report --run-id "$RUN_ID"
```

## 5. Report back to the user

Print the Markdown summary the report command emits, then add:

- The path to `report/latest.html`.
- How many candidates were newly judged this run, and how many were cached.
- Anything in "needs manual review", by name and reason — these need a human.
- Any calibration change you made, and why.
- Any concern about the rules themselves (a gate that fired on someone who looks
  strong, a pattern of quote warnings). The rules are config; say so if one looks
  wrong rather than working around it.

Never describe a gated candidate as unqualified. They were eliminated by a rule;
name the rule.
