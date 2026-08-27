# CV Screening Agent

Screens applicants for one Trakstar Hire opening against a job description,
eliminates carelessly AI-generated CVs, and produces a ranked shortlist capped at
20 % of the pool. **Report only — it never writes to Trakstar.**

Design: `docs/superpowers/specs/2026-08-27-cv-screening-agent-design.md`

## Setup

```bash
uv sync
cp .env.example .env      # then fill in TRAKSTAR_API_KEY (needs Super Admin)
```

`.env` holds a real API key — it is gitignored and must never be committed,
pasted into a prompt, or pasted into a report or commit message.

## Run it

In Claude Code:

```
/screen-cvs
```

Or the deterministic stages alone:

```bash
uv run python -m screen run                              # fetch, parse, precheck, list pending
uv run python -m screen run --source folder --path ~/cvs  # no API key needed
uv run python -m screen report --run-id <run>              # re-render a report
```

Open `report/latest.html`.

## Daily schedule

```bash
scripts/install-schedule.sh 8 0     # 08:00 local
```

Runs are incremental: unchanged candidates are never re-parsed or re-judged.
Changing `rubric_version` in `roles/fde/role.json` forces a full re-judge, and a
scheduled run refuses to do that silently.

## How it decides

The pool is the **active cohort** for the opening — Trakstar candidates with
`state=in_process` only. A human has already decided on anyone rejected, so
they are never re-screened, and the fetch is scoped to opening `704353`
(Forward Deployed Engineer) alone: the account holds 186 openings, and an
unscoped query would pull in CVs for jobs this rubric was never written
against.

1. **Gates** eliminate: template placeholders, wrong company, duplicated bullets,
   bullets shared with another applicant, hidden text, under 4 years, explicit
   non-US with no authorisation.
2. **Penalties** subtract: no LinkedIn (−8), AI-slop signals (−5 each), shared
   template bullets (−10), 4–5 years (−10), no degree (−5), short stints (−5).
3. **Fit** scores 0–100 across seven criteria drawn from the JD, each needing a
   verbatim quote from the CV.
4. **Cap**: the shortlist is capped at 20 % of the *whole active pool* — i.e.
   `floor(0.20 × pool_size)` — not 20 % of whoever survives the gates.
   Decisions are sticky: an accepted candidate is never displaced by later
   applicants.

The judging model never sees names, emails, phones, addresses, or schools.

## Tests

```bash
uv run pytest
```

## Adding a role

Copy `roles/fde/` to `roles/<name>/`, edit `role.json` and the two prompts, then
pass `--role roles/<name>`.
