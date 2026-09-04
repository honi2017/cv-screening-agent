# Production-readiness review — CV screening pipeline

Reviewed 2026-09-04 against commit `f763194`, branch `feat/cv-screening-agent`.
Scope: opening 704353 (Forward Deployed Engineer), pool 46, read-only pipeline.

## Verdict

**Not ready as an unattended daily job. Ready as a human-in-the-loop tool run on demand.**

Every blocker below is operational or compliance, not correctness. The decision logic is
the strongest part of the system: 95% statement coverage, 477 tests, no vacuous tests, and
the scoring path (`rank.py` 98%, `signals.py` 97%) is where the coverage concentrates. What
is missing is the scaffolding that lets software run unattended against real people's data —
retention, alerting, permission portability, transport resilience.

## Blockers

### B1. The scheduled job's permissions are untracked

`scripts/install-schedule.sh` installs a launchd job running
`claude -p /screen-cvs --permission-mode acceptEdits`. That headless session needs Bash and
Write permission, granted in `.claude/settings.local.json` — which is matched by
`~/.gitignore_global:3` and is not tracked (`git ls-files .claude/` returns only
`SKILL.md`).

On a fresh clone or a second machine the grants do not exist and the daily job cannot run
the pipeline. Separately, the grants are blanket `Bash`, `Edit`, `Write`, `NotebookEdit` —
broader than an unattended job handling applicant PII should hold.

Fix: commit a project `.claude/settings.json` with narrowly scoped grants, e.g.
`Bash(uv run python -m screen:*)`, and keep `settings.local.json` for personal overrides.

### B2. No retention policy for applicant personal data

`data/` holds 506 files / 13 MB: resumes, names, emails, phone numbers, and parsed CV text
for 263 applicants. Nothing purges it — the only matches for retention/purge/expire in
`screen/` are unrelated comments.

The pipeline redacts PII *before AI judgment*, which is a real bias-mitigation control, but
the unredacted originals persist indefinitely on a laptop. That is a data-minimisation
exposure under GDPR/CCPA and the highest-risk item in this review, because it grows with
every run and applies to people who were rejected months ago.

Fix: a `screen purge --older-than <days>` command, a documented retention window agreed with
whoever owns data protection, and preferential deletion of resumes for candidates a human
has already rejected.

### B3. A failed run is indistinguishable from a quiet one

launchd sends stdout and stderr to `state/logs/screen-cvs.{out,err}.log`. Nothing watches
the exit code and nothing alerts. A run that fails leaves the team believing screening is
current when it is not — the failure mode is silence, which is the worst kind for a
scheduled job.

Fix: wrap the invocation so a non-zero exit notifies a human. Phase 2 already plans a Slack
channel; the same webhook serves this.

### B4. Transport errors are not retried

`TrakstarClient._get` retries on HTTP 429 and 5xx, honouring `Retry-After` with exponential
backoff over `MAX_RETRIES = 5`. It does not catch `httpx.ConnectError` or `ReadTimeout`, so a
transient DNS or network blip propagates and aborts the run.

Atomic writes mean no corruption and incremental fetch means a re-run is cheap, so the
damage is bounded — but combined with B3 the run fails silently and nobody re-runs it.

Fix: catch `httpx.RequestError` inside the existing retry loop.

## High

### H1. The measured findings live in gitignored scratch

`.superpowers/` is gitignored and holds 45 markdown files, including
`REAL-DATA-ADDENDUM.md` — which opens "Where this document contradicts a task brief, this
document wins" — and `progress.md`, carrying every ruling and the evidence behind them.

A `git clean -fdx` or a fresh clone destroys all of it. The next maintainer would have no
record that LinkedIn's HTTP 999 cannot distinguish a private profile from a missing one, or
that `find_placeholders` went through five designs before landing on syntax impossible in
prose. They would repeat the mistakes.

Fix: move the addendum and the rulings into `docs/` (as this file is).

### H2. Sticky acceptance can silently breach the 20% ceiling

Accepting ≤20% is requirement #1. `rank_and_cut` re-seats previously accepted candidates
before consulting any score, and nothing compares the seated count against the current cap.

This run it held only by luck: the pool contracted 69 → 46, dropping the cap 13 → 9, and
exactly four of the thirteen accepted had been rejected in Trakstar — leaving nine for nine
slots. Had two been rejected instead, eleven would have been seated against a cap of nine:
24%, in breach, with nothing reporting it.

Fix: compute `accepted > cap` and surface it in both the report and the run record.
`rank --rebaseline` is the remedy once detected.

### H3. The README is a setup document, not a runbook

79 lines covering setup, invocation, the schedule, and the rubric. It never mentions the
judging step (the stage that requires Claude), `--rebaseline`, the PII redaction model, the
read-only guarantee, or what to do when a run fails.

## Medium

- **M1.** `round_metric_ratio` is computed at `precheck.py:178` and consumed by nothing.
  Measured, it is the best-validated slop signal available: among the 34 candidates with ≥3
  metrics, 12 have every metric round, and 9 of those 12 already carry an independent
  AI-slop or broad-claims flag — 75% concordance. Two of the three it would newly surface
  sat in the top 6.
- **M2.** `years_experience.computed` is 0.0 for 28 of 69 candidates (41%) because no date
  ranges parse; the report renders "Years computed: 0.0 (confidence: low)" beside CVs
  claiming a decade, which reads as a detected contradiction rather than "not determined".
- **M3.** `skills_count` is 0 for 41% of the pool (addendum H1) — a heading-matching gap.
  The direction is safe (a missing count never clears a threshold) but the signal is
  materially weaker than designed.
- **M4.** `verdict.py` is the lowest-covered module at 86%, and the uncovered lines are the
  malformed-output guards: non-numeric tier, non-object flag, non-numeric bonus. That is
  precisely the boundary between LLM output and scoring a real person.
- **M5.** `validate_verdict` accepts any `kind` a judge emits. This is partly deliberate —
  the prompt permits a novel category for a genuine defect — but nothing distinguishes a
  reviewed category from an invented one, and at `tier2_signal: 10` an invented one now
  costs an applicant 10 points on a rule no human approved.
- **M6.** Education dates survive redaction, so age is inferable. The mitigation is a prompt
  instruction, and it *is* locked by a test (`tests/test_prompts.py:127`), but the control is
  instructional rather than structural. There is a real tension — dates feed the
  years-of-experience criterion — so this should be a documented accepted risk, not an
  unexamined one.

## Low

- **L1.** The read-only tripwire is an `assert`, which `-O` strips. The guarantee itself is
  structural (`_get` takes no `method` parameter; `method = "GET"` is a literal), so nothing
  is actually at risk — but the tripwire for future edits vanishes under optimisation.
  Convert to an explicit `raise`.
- **L2.** The tie-break at the cut ignores flag cleanliness: at 66 each, the candidate with
  an AI-slop flag took the last slot over one with none.
- **L3.** The 60% criteria bar is hardcoded at `rank.py:251` rather than read from config,
  unlike every penalty value.
- **L4.** `precheck.py:104` still documents a "HEAD request" after the liveness check moved
  to GET.
- **L5.** No log rotation; `state/logs/` grows unbounded.

## What is genuinely strong

Verified during this review, not assumed:

- **95% statement coverage**, 477 tests, 456 test functions, and **zero tests without an
  assertion** (checked by walking the AST, not by reading names).
- **Read-only is structurally enforced.** `_get` accepts no method argument; the only
  outbound calls are the two documented GETs plus a resume download.
- **EEO fields are structurally excluded.** ATS answers are read through exact-match
  allowlists, so Ethnicity, Disability Status and Veteran Status are ignored by
  construction. This is the fix for a real earlier defect that eliminated a candidate on
  protected-class data.
- **Atomic writes** (`.tmp` + `replace`) across eight modules, so a crash cannot leave torn
  state.
- **Judging fails safe.** A candidate with no verdict becomes "needs manual review", never
  rejected, and the skill forbids fabricating a verdict to fill the gap.
- **Quote verification.** Every non-zero criterion score must quote the CV verbatim or the
  validator zeroes it.
- **Protected-class instructions are test-locked**, so the prompt cannot silently lose them.
- **Rate limiting** honours the 100-req/5-min budget and `Retry-After`.
- **Reference flags are provably inert** — re-running the full pipeline changed the
  `final` and `status` of 0 of 69 candidates.
- **No credential in any tracked file**, confirmed by searching tracked content for the live
  key.

## Recommended order

1. B2 (retention) — grows worse every run and is the one with legal exposure.
2. B1, B3 (permissions, alerting) — together they decide whether the schedule works at all.
3. B4, H2 — resilience and the 20% guarantee.
4. H1, H3 — make the knowledge and the runbook survive this session.
5. M1, M2 — the two with visible effect on decisions.
