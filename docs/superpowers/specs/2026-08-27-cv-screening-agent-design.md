# CV Screening Agent — Design Spec

**Date:** 2026-08-27
**Status:** Draft for review
**Owner:** Hung Huynh
**Scope:** Phase 1 — automated daily screening of Trakstar Hire applicants for the Forward Deployed Engineer opening (id 704353), producing a ranked report. Phase 2 (Slack review loop + Trakstar write-back) is out of scope but the design leaves hooks for it.

---

## 1. Goals and requirements

The agent screens every CV submitted to a Trakstar opening against the job description and produces a shortlist for human review.

Hard requirements (from the hiring team):

| # | Requirement | How it is met |
|---|---|---|
| R1 | Accept **≤ 20 %** of the total CV pool | Relative cut after ranking; sticky ledger keeps the cap stable as the pool grows (§6) |
| R2 | Eliminate CVs that are **AI-generated and carelessly unmodified** | Tiered signal set: Tier 1 → eliminate; ≥ 3 Tier 2 → eliminate; Tier 3 ignored (§5.1) |
| R3 | **No LinkedIn profile = red flag, not deal-breaker** | −8 penalty, surfaced as a flag chip (§5.3) |
| R4 | Must match expectations in `JD/` | Fit rubric derived from the JD, config-driven per role (§5.4) |
| R5 | Runs **daily on a schedule**, never re-processing CVs already handled | Idempotent, content-hashed stages (§7) |
| R6 | **Report only** — no writes to Trakstar in Phase 1 | Output is HTML + Markdown + CSV (§8) |
| R7 | Runtime is **Claude Code itself** — no external LLM API key | Judgment runs in Claude Code subagents; everything deterministic runs in Python |

Non-goals for Phase 1: Slack integration, Trakstar write-back, interview scheduling, multi-opening support in one run (one opening per invocation is fine; the layout supports several).

## 2. Data source: Trakstar Hire

Trakstar Hire (formerly Recruiterbox) exposes a REST API sufficient for read-only screening. No MCP server is needed.

- **Base URL:** `https://api.recruiterbox.com/v2/`
- **Auth:** HTTP Basic, API key as username, empty password (`curl -u $KEY: …`). Key generated at *Settings → Advanced Settings → API Key*; requires **Super Admin**. Key is pending and will be supplied via `.env`.
- **Rate limit:** 100 requests / 5 minutes per IP. The fetcher throttles to ≤ 90 per 5 min and backs off on HTTP 429.
- **Endpoints used:**
  - `GET /candidates?opening_id=704353&limit=250&offset=N` — pagination via `meta.total_count`.
  - Candidate object fields consumed: `id`, `first_name`, `last_name`, `email`, `phone`, `created_date`, `updated_date`, `stage_name`, `state`, `source`, `resume.file_url`, `resume.file_name`, `profile_data[]` (name/value custom fields — LinkedIn URL and location live here when the applicant filled them in).
  - `resume.file_url` — direct download of the CV file.
- **Fallback source:** a local folder of PDFs plus an optional CSV export from Trakstar (`--source folder --path …`). Same downstream pipeline; used if the API key is delayed or for offline testing.

Candidates whose `state` is `rejected`, `hired`, or `withdrawn` in Trakstar are fetched (so the pool count is honest) but marked `withdrawn` in the ledger and excluded from the pool denominator (§6.2).

## 3. Reference survey (what we borrowed)

| Source | Borrowed | Rejected |
|---|---|---|
| [interviewstreet/hiring-agent](https://github.com/interviewstreet/hiring-agent) | Rubric-as-config (`role.json` + prompt templates per role); mandatory verbatim evidence per score; deductions as first-class; fairness rule (judge blind to name, gender, school, GPA, location) | GitHub enrichment (senior FDE work is in private repos); external LLM backends |
| [haroon-sajid/resume-screening-app](https://github.com/haroon-sajid/resume-screening-app) | Separate **RedFlag** pass from **Fit** pass | Streamlit/LangGraph stack |
| [srbhr/Resume-Matcher](https://github.com/srbhr/resume-matcher) | — | Keyword/vector similarity scoring: rewards exactly the JD-stuffed CVs we want to catch |
| [Binoculars](https://github.com/ahans30/Binoculars), [Fast-DetectGPT](https://github.com/baoguangsheng/fast-detect-gpt) | — | Statistical "was AI used" detectors need two local 7B models and answer the wrong question; R2 is about *carelessness*, a content-quality judgment |
| [arXiv 2507.02087](https://arxiv.org/abs/2507.02087) | Off-the-shelf LLMs show demographic disparity → **redact PII before judgment** | — |
| Gem / Willo practitioner guides | Cross-pool duplicate detection; hidden-text check; PDF metadata as corroboration | "Perfect grammar" as a signal (penalises good writers) |

Not found anywhere: a relative-percentage cut with boundary calibration and a sticky ledger. That part is original to this design.

## 4. Architecture

### 4.1 Pipeline

```
Trakstar API ─► 1 FETCH ─► 2 PARSE ─► 3 PRECHECK ─► 4 JUDGE ─► 5 RANK & CUT ─► 6 REPORT
(or folder)     python      python      python       Claude      python + agent   python
                                                     subagents   (calibration)
```

| Stage | Runtime | Input | Output | Purpose |
|---|---|---|---|---|
| 1 Fetch | Python | Trakstar API or folder | `data/<opening>/candidates.json`, `resumes/<id>.pdf` | Pull candidates and CV files; cache; throttle |
| 2 Parse | Python (PyMuPDF4LLM) | PDF | `parsed/<id>.md`, `parsed/<id>.meta.json` | Layout-aware markdown; PDF metadata (Producer, Creator, CreationDate, page count); hidden-text spans (colour ≈ background, font size < 4 pt, render mode invisible) |
| 3 Precheck | Python | parsed MD + meta + candidate record | `prechecks/<id>.json`, `redacted/<id>.md`, `pool_duplicates.json` | Deterministic signals (§5.2) and PII redaction |
| 4 Judge | Claude Code subagent, one per pending candidate | `redacted/<id>.md`, `prechecks/<id>.json`, `roles/fde/*` | `verdicts/<id>.json` | Pass A RedFlag, Pass B Fit — strict JSON with verbatim quotes |
| 5 Rank & cut | Python, then main agent | all verdicts + prechecks + ledger | updated `state/ledger.json`, `state/runs/<run_id>.json` | Gates → penalties → sort → cap → sticky update; calibration window reviewed by the main agent |
| 6 Report | Python | ledger + run delta + verdicts | `report/<run_id>.{html,md}`, CSVs, `report/latest.*` | Human-readable output |

Division of labour: **Python owns everything that must be identical run to run** (fetching, parsing, regex/heuristic checks, arithmetic, rendering). **Claude owns only judgment** (is this bullet generic? does this experience count as client-facing?). The skill orchestrates.

### 4.2 The judge never sees identity

Stage 3 produces `redacted/<id>.md` with name, email, phone, street address, city/state, photo captions, and school names replaced by tokens (`[NAME]`, `[EMAIL]`, `[LOCATION]`, `[SCHOOL]`). Degree *level* and *field* survive. The LinkedIn URL is reduced to `linkedin.present` and `linkedin.name_matches` in prechecks; the URL itself never reaches the judge. The report re-attaches identity from `candidates.json`.

The judge prompt states explicitly: do not infer or weigh nationality, gender, age, or institution prestige.

### 4.3 Two judgment passes, one subagent

- **Pass A — RedFlag.** Prompted to find problems only: Tier 1/2 careless-AI signals, internal contradictions, seniority/language mismatch, timeline issues. Output: `flags[]`, each with `tier`, `kind`, `quote`, `explanation`.
- **Pass B — Fit.** Given the rubric, anchors, fairness rule, and Pass A's flags (so it does not re-derive them). Output: seven criterion scores, each with `quote` and `rationale`; optional `bonus` with justification; `summary` (2–3 sentences); `years_experience_estimate` with `confidence`.

Both passes run in the same subagent sequentially so the CV is read once. Output schema is enforced by `rank.py` on load; malformed JSON → one retry with the validation error → still bad → `judge_failed` status, surfaced in the report, never silently dropped.

### 4.4 Calibration window

Independent per-CV scoring drifts. After the arithmetic sort, the main agent reviews candidates ranked at `cut ± 5` side by side (their scores, quotes, flags) and may **reorder within that window**. It may not move anyone in from outside the window, and it may not increase the number of accepted candidates. Its reasoning is written to `state/runs/<run_id>.json` and shown in the report.

## 5. Rules

All numbers live in `roles/fde/role.json` (versioned). Changing them bumps `rubric_version`.

### 5.1 Careless-AI signal tiers (R2)

**Tier 1 — hard eliminate (gate G1). Any one suffices.**

| Kind | Detector |
|---|---|
| Template placeholder left in: `[Your Name]`, `[Company]`, `[Insert …]`, `Lorem ipsum`, `XX%`, `{{…}}` | precheck regex |
| Wrong company or role name in summary/cover text (e.g. "excited to join Palantir as a Solutions Architect") | RedFlag pass |
| Duplicate or near-duplicate bullets across different jobs inside the CV (normalised Jaccard ≥ 0.85) | precheck |
| Summary contradicts body (claimed years/domain not supported by experience section) | RedFlag pass |
| > 3 bullets shared verbatim with another applicant in the pool | precheck, cross-pool |

**Tier 2 — penalty −5 each; ≥ 3 → eliminate (gate G2).**

| Kind | Detector |
|---|---|
| Uniform bullet template: every bullet is `⟨power verb⟩ X, resulting in ⟨round %⟩` with no system/tool/client named | RedFlag pass, informed by precheck's power-verb density and round-number ratio |
| Generic summary that could apply to any role; JD keywords echoed in near-identical phrasing/order | RedFlag pass |
| Skills section ≥ 40 items with no evidence in experience | precheck count + RedFlag confirmation |
| No concrete nouns anywhere: no product names, versions, scale figures, named tools | RedFlag pass |
| 1–3 bullets shared verbatim with another applicant | precheck, cross-pool (−10 instead of −5) |
| PDF metadata corroboration only: Producer is a template service (Canva, Resume.io, Zety, Kickresume …) **and** created < 30 min before submission | precheck; counts as ½ a signal — never triggers G2 alone |

**Tier 3 — ignored.** Polished, LLM-assisted writing that is specific and consistent. The report does not mention it.

### 5.2 Deterministic prechecks (`prechecks/<id>.json`)

```json
{
  "candidate_id": 123,
  "pdf_sha256": "…",
  "pages": 2,
  "pdf_meta": {"producer": "…", "creator": "…", "created": "…", "minutes_before_submission": 14},
  "hidden_text": {"found": false, "spans": []},
  "placeholders": [],
  "intra_cv_duplicate_bullets": [],
  "pool_duplicate_bullets": [{"with_candidate": 456, "bullet": "…"}],
  "skills_count": 23,
  "power_verb_density": 0.62,
  "round_metric_ratio": 0.8,
  "years_experience": {"computed": 6.4, "confidence": "high|medium|low", "ranges": [["2018-03","2021-08"], …]},
  "short_stints_last_5y": 1,
  "gap_over_12m": false,
  "linkedin": {"present": true, "source": "trakstar|cv|none", "name_matches": true},
  "degree": {"present": true, "level": "BSc", "field": "Computer Science"},
  "location": {"us_evident": true, "non_us_explicit": false, "timezone_hint": "ET|CT|MT|PT|unknown"},
  "redaction": {"tokens_replaced": 9}
}
```

### 5.3 Gates and penalties

**Gates (eliminate; still counted in the pool denominator):**

| Gate | Rule | Source |
|---|---|---|
| G1 | Any Tier 1 signal | precheck + RedFlag |
| G2 | ≥ 3 Tier 2 signals (metadata counts ½) | RedFlag + precheck |
| G3 | Hidden / white-on-white / micro-font text | precheck |
| G4 | Years of software engineering experience < 4 with `confidence ≠ low`; if confidence is low the judge's estimate is used; still < 4 → gate | precheck + judge |
| G5 | CV explicitly states non-US residence and no US work authorisation / relocation statement | precheck + Trakstar `profile_data`. Unknown → flag only |

Deliberately **not** a gate: no Bachelor's degree evident (JD lists it as must-have). It is a −5 penalty plus flag so the human decides. Configurable.

**Penalties (subtracted from Fit):**

| Signal | Penalty | Flag chip |
|---|---|---|
| No LinkedIn (not in Trakstar and not in CV) | −8 | `no LinkedIn` |
| LinkedIn present, name does not match | −8 | `LinkedIn mismatch` |
| Each Tier 2 signal (1–2 of them) | −5 | `AI-slop: <kind>` |
| Cross-pool duplicate bullets (1–3) | −10 | `template dup` |
| Years experience 4 ≤ y < 5 | −10 | `4–5 yrs` |
| No degree evident | −5 | `no degree` |
| ≥ 3 roles < 12 months in last 5 years | −5 | `short stints` |
| Unexplained gap > 12 months in last 6 years | 0 | `gap` |
| Location unknown | 0 | `location?` |

### 5.4 Fit rubric (0–100)

Derived from `JD/FDE.md`. Each score requires a verbatim quote from the redacted CV; a score without a quote is invalid and is shown as a warning, not a number.

| Key | Criterion | Max | Evidence that earns points |
|---|---|---|---|
| `production_ownership` | Production engineering ownership | 25 | Shipped and owned production systems independently; named systems, scale, stack, incident ownership; fast decisions under pressure |
| `integration_breadth` | Integration breadth | 20 | Hands-on across several of: DBMS, SFTP, SMTP/email pipelines, enterprise SSO (SAML/OIDC), REST/webhook APIs, CRMs (Salesforce, HubSpot, DealCloud…), data-model design. Breadth over depth |
| `client_solutioning` | Client-facing solutioning | 20 | Led discovery/scoping with external clients; translated technical concepts; handled escalations; FDE/SE/implementation/consulting titles |
| `communication_product` | Communication & product sense | 15 | Trade-off reasoning in bullets; generalised one-off client work into platform features; the CV itself as a writing sample |
| `domain` | Domain | 10 | Fintech, private markets, enterprise SaaS, document digitisation, OCR, forms processing |
| `ai_tooling` | AI/LLM tooling | 5 | Built internal automation or agents with real outcomes; not "familiar with ChatGPT" |
| `distributed_collab` | Distributed / offshore collaboration | 5 | Worked across time zones, especially with Vietnam or similar offshore teams |

**Anchors** (fraction of max): 0 = no evidence · 0.25 = claimed, no specifics · 0.5 = one concrete example · 0.75 = several concrete examples with scale/outcome · 1.0 = sustained, owned, quantified, clearly senior.

**Bonus:** ≤ +5, judge must justify (e.g. built an integration platform reused across many clients; Palantir-style FDE background; early employee who scaled client delivery).

**Tiebreaker (deterministic, never the judge):** `timezone_hint ∈ {ET, CT}` from Trakstar location only — the JD lists East Coast/Midwest as a plus.

**Final:** `final = Σ criterion scores + bonus − Σ penalties`.

### 5.5 Verdict schema (`verdicts/<id>.json`)

```json
{
  "candidate_id": 123,
  "input_hash": "sha256(redacted.md + prechecks.json + rubric_version)",
  "rubric_version": 1,
  "redflag": {
    "flags": [{"tier": 1, "kind": "placeholder", "quote": "…", "explanation": "…"}],
    "years_experience_estimate": {"value": 6, "confidence": "medium"}
  },
  "fit": {
    "scores": {
      "production_ownership": {"score": 18, "quote": "…", "rationale": "…"},
      "integration_breadth": {"score": 12, "quote": "…", "rationale": "…"},
      "client_solutioning": {"score": 15, "quote": "…", "rationale": "…"},
      "communication_product": {"score": 9, "quote": "…", "rationale": "…"},
      "domain": {"score": 4, "quote": "…", "rationale": "…"},
      "ai_tooling": {"score": 2, "quote": "…", "rationale": "…"},
      "distributed_collab": {"score": 0, "quote": null, "rationale": "no evidence"}
    },
    "bonus": {"points": 0, "justification": null},
    "summary": "…"
  }
}
```

A `quote` may be `null` only when `score` is 0.

## 6. Ranking, the 20 % cap, and incremental runs

### 6.1 Sticky ledger

`state/ledger.json` holds one entry per candidate:

```json
{"candidate_id": 123, "status": "accepted|waitlist|gated|needs_review|withdrawn",
 "gate": "G1|G2|G3|G4|G5|null", "final": 71.0, "first_seen_run": "2026-08-28T08:00",
 "status_changed_run": "2026-08-30T08:00", "pdf_sha256": "…", "trakstar_updated_date": "…",
 "human_override": null}
```

### 6.2 Rules per run

1. **Pool** = all ledger entries not `withdrawn`. **Cap** = `floor(0.20 × pool)`.
2. **Accepted stays accepted.** Later runs never demote. `human_override` (Phase 2) can.
3. **Open slots** = cap − accepted. New arrivals and existing `waitlist` compete for them by `final`.
4. **Quality floor:** a new acceptance must score ≥ `min(final of accepted) − 5`. Unfilled slots roll forward.
5. **Gated stays gated** unless the PDF hash changes (re-upload) → full re-process of that candidate.
6. **Cross-pool duplicates** are recomputed over the whole pool every run (set intersection, no LLM). A hit on a `waitlist` candidate may gate them; a hit on an `accepted` candidate becomes a report flag, not a demotion.
7. `needs_review` (unparseable PDF, missing resume, `judge_failed`, unknown location with everything else fine) is excluded from ranking but included in the pool count.
8. Calibration (§4.4) runs on the window around the cut among non-gated, non-accepted candidates only.

### 6.3 Run record

`state/runs/<run_id>.json`: pool size, cap, open slots, lists of new / newly accepted / newly gated / would-have-qualified-but-no-slot, calibration reasoning, timing, API request count. This record is the Phase 2 Slack payload.

## 7. Idempotency — never re-scan what is done (R5)

| Stage | Skip when | Cost when skipped |
|---|---|---|
| 1 Fetch | candidate id in ledger **and** `updated_date` unchanged | 0 downloads (list call only: 1 request / 250 candidates) |
| 2 Parse | `pdf_sha256` unchanged | 0 |
| 3 Precheck | `pdf_sha256` and `precheck_rules_version` unchanged | 0 |
| 4 Judge | `input_hash` unchanged | **0 LLM calls** |
| 5 Rank | always runs | ms |
| 6 Report | always runs | ms |

Behaviours:

- **Rubric change** → `rubric_version` bump → all judge keys miss. Interactive run warns "re-judging N candidates"; **headless (scheduled) runs refuse unless `--full` is passed**, so a daily job never re-judges the whole pool by accident.
- **Interrupted run** → each stage writes `tmp` then renames; next run resumes at the first missing artifact.
- **Zero new applicants** → list call, report with "no changes" delta, exit.
- **`--full`** forces re-judge of everyone; **`--cap 0.20`** overrides the cap; **`--source folder --path …`** uses local files.

## 8. Report (R6)

Written to `report/<run_id>.*` and copied to `report/latest.*`.

### 8.1 `report/<run_id>.html`

Single self-contained file, no external assets. Sections:

1. **Run header** — opening, run time, pool size, cap, accepted, open slots, CVs newly processed.
2. **Delta since last run** — new arrivals · newly accepted · newly gated · would-have-qualified but no slot (with scores).
3. **Shortlist** — rank · name · final · seven mini-bars · penalties · flag chips · links (Trakstar candidate page, cached PDF) · run entered.
4. **Waitlist** — same table, greyed, by score.
5. **Gated** — grouped by gate, each row showing the verbatim triggering quote.
6. **Needs manual review** — unparseable, missing resume, judge failed, unknown location.
7. **Methodology** — rubric version, gate/penalty table, redaction note, calibration reasoning.

Row expansion → detail panel: per-criterion score + quote, every flag + quote, precheck facts, judge summary. **No score without a quote.**

### 8.2 `report/<run_id>.md`

~30-line summary: header numbers, delta lists with name, score, one-line reason. Phase 1: paste into Slack. Phase 2: posted automatically.

### 8.3 CSVs

`shortlist.csv`, `all_candidates.csv`: `id, name, email, status, gate, final, <7 criterion scores>, penalties, flags(;), linkedin_url, trakstar_url, first_seen_run, status_changed_run`.

### 8.4 What the report does not do

No hire/no-hire wording (only shortlist / waitlist / gated). No demographic fields anywhere. No emails sent.

## 9. Project layout

```
CVsScanningAgent/
├── .claude/skills/screen-cvs/SKILL.md     # /screen-cvs: runs stages, fans out judge subagents, calibrates
├── JD/FDE.md
├── roles/fde/
│   ├── role.json                          # criteria, weights, anchors, gates, penalties, cap, rubric_version
│   ├── redflag_prompt.md                  # Pass A
│   └── fit_prompt.md                      # Pass B
├── screen/                                # Python package (deterministic stages)
│   ├── fetch.py      # Trakstar client, folder source, cache, throttle/backoff
│   ├── parse.py      # PyMuPDF4LLM → md; metadata; hidden-text spans
│   ├── precheck.py   # placeholders, duplicates (intra + pool), LinkedIn, years, degree, location, redaction
│   ├── rank.py       # verdict validation, gates, penalties, cap, sticky ledger, run record
│   ├── report.py     # HTML + MD + CSV
│   └── cli.py        # python -m screen fetch|parse|precheck|pending|rank|report|run
├── tests/            # fixtures: clean CV, placeholder CV, template-dup pair, hidden-text CV, scanned PDF, 4-yr CV
├── data/<opening_id>/{candidates.json, resumes/, parsed/, redacted/, prechecks/, verdicts/, pool_duplicates.json}
├── state/{ledger.json, runs/}
├── report/
├── .env              # TRAKSTAR_API_KEY, OPENING_ID  (gitignored)
├── .gitignore        # data/, state/, report/, .env
└── scripts/install-schedule.sh   # writes ~/Library/LaunchAgents/com.anduin.screen-cvs.plist
```

### 9.1 Skill flow (`/screen-cvs`)

1. `python -m screen fetch` → `parse` → `precheck` → prints pending candidate ids (judge key misses).
2. If pending > 0 and `--full` not set and pending == whole pool while ledger is non-empty → abort with the rubric-change warning (headless) or ask (interactive).
3. Fan out one subagent per pending id (parallel, batches of ~8): input = `redacted/<id>.md`, `prechecks/<id>.json`, both prompts; output = `verdicts/<id>.json`. Validate; retry once; else mark `judge_failed`.
4. `python -m screen rank --prepare` → emits the calibration window. Main agent reviews and writes `state/runs/<run_id>.calibration.json` (ordering + reasoning). `python -m screen rank --finalize`.
5. `python -m screen report`.
6. Print the `.md` summary to the console.

### 9.2 Scheduling

`scripts/install-schedule.sh` installs a launchd agent running daily at 08:00 local:

```
claude -p "/screen-cvs" --permission-mode acceptEdits
```

with `WorkingDirectory` set to the project, stdout/stderr to `state/logs/`. Missed runs (Mac asleep) execute at next wake (launchd default). The job is incremental by default (§7).

## 10. Error handling

| Failure | Behaviour |
|---|---|
| Trakstar 401 | Abort with "check TRAKSTAR_API_KEY"; no partial report |
| Trakstar 429 / 5xx | Exponential backoff, max 5 tries, then abort the fetch stage; later stages still run on cached data and the report notes "fetch incomplete" |
| Resume download fails / missing `file_url` | `needs_review: missing_resume` |
| PDF unparseable or < 200 characters of text (scanned) | `needs_review: unparseable` |
| DOCX or other non-PDF | Phase 1: `needs_review: unsupported_format` (DOCX support is a small follow-up) |
| Subagent returns invalid JSON | retry once with validator message; then `needs_review: judge_failed` |
| Subagent quotes text not present in the redacted MD | score for that criterion invalidated → shown as warning; candidate keeps other scores |
| Ledger corrupt | Refuse to run; `state/ledger.json.bak` written after every successful run |

## 11. Testing

- **Unit (pytest):** `precheck.py` on fixture CVs — placeholder regexes, intra-CV and cross-pool duplicate detection thresholds, years computation on overlapping/open-ended ranges, LinkedIn name matching, redaction completeness (no email/phone regex hits remain). `rank.py` — cap arithmetic, sticky rules, quality floor, roll-forward, withdrawn handling, calibration window bounds.
- **Golden files:** `parse.py` output for each fixture PDF checked into `tests/golden/`.
- **Judge prompts:** a small labelled set (≈10 CVs: 3 clean, 3 Tier 1, 2 Tier 2-heavy, 2 borderline) run through the skill; expected gates and score bands recorded in `tests/judge_expectations.json`; the skill has a `--selftest` mode that reports agreement. Not asserted in CI (LLM), but run before any rubric version bump.
- **Fairness smoke test:** the same fixture CV with two different names/photos/schools must produce identical verdicts (redaction makes this deterministic by construction; the test guards against redaction regressions).
- **End-to-end:** `--source folder` over `tests/fixtures/` produces a report; asserted on counts and statuses, not on prose.

## 12. Phase 2 hooks (not built now)

- `state/runs/<run_id>.json` is already the Slack message payload.
- `ledger.human_override` is where Slack reactions / comments land.
- Trakstar write-back = `PUT /candidates/{id}` (stage move) + internal note, driven by `human_override` only — the agent never writes its own verdict to Trakstar.

## 13. Open items

| Item | Owner | Default until resolved |
|---|---|---|
| Trakstar API key (Super Admin) | Hung | Develop and test with `--source folder` |
| Confirm degree rule: penalty (proposed) vs hard gate | Hung | Penalty −5 + flag |
| Confirm run hour for launchd | Hung | 08:00 local |
| DOCX support | — | `needs_review` in Phase 1 |
