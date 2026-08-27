# CV Screening Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a daily-scheduled agent that screens Trakstar Hire applicants for the Forward Deployed Engineer opening against the JD, eliminates carelessly AI-generated CVs, and emits a ranked report accepting at most 20 % of the pool.

**Architecture:** Six-stage pipeline (fetch → parse → precheck → judge → rank → report). Python owns every deterministic step (HTTP, PDF parsing, regex/heuristic signals, arithmetic, rendering) so runs are reproducible; Claude Code subagents own only judgment (Pass A RedFlag, Pass B Fit) and see PII-redacted CV text. A sticky ledger keeps the 20 % shortlist stable as new applicants arrive, and content hashes make every stage skip work already done.

**Tech Stack:** Python 3.13, `uv` for dependency management, `pymupdf4llm` + `pymupdf` (PDF → markdown, metadata, hidden-text detection), `httpx` (Trakstar REST), `pytest`, stdlib `csv`/`html`/`hashlib`/`json`. No external LLM API — judgment runs inside Claude Code via a skill.

**Spec:** `docs/superpowers/specs/2026-08-27-cv-screening-agent-design.md`

## Global Constraints

- Python **3.13** (`/opt/homebrew/bin/python3`); manage deps with **`uv`**, never bare `pip`. All commands run from the project root `/Users/hung/Coding/Odin/WorkSpace/CVsScanningAgent`.
- The repo is **not yet a git repo** — Task 1 runs `git init`. Every task ends with a commit.
- **Trakstar base URL:** `https://api.recruiterbox.com/v2/`. **Auth:** HTTP Basic, API key as username, empty password. **Rate limit:** 100 requests / 5 min per IP — the client throttles to ≤ 90 / 5 min and backs off on 429.
- **Opening id:** `704353`. Config lives in `.env` (`TRAKSTAR_API_KEY`, `OPENING_ID`), which is gitignored. The API key is **not yet available** — all development and testing uses `--source folder`.
- **Cap fraction: 0.20.** `cap = floor(0.20 × pool)`. **Quality floor delta: 5.** **Calibration window: ±5.** **Bonus max: +5.**
- **The judge never sees PII.** Name, email, phone, street address, city/state, school names are replaced with `[NAME]`, `[EMAIL]`, `[PHONE]`, `[LOCATION]`, `[SCHOOL]` before any subagent reads the text. Degree level and field survive.
- **No score without a verbatim quote.** A criterion score > 0 whose `quote` is absent from the redacted markdown is invalidated and reported as a warning.
- **Report only.** Nothing in this plan writes to Trakstar or sends email.
- Never commit `data/`, `state/`, `report/`, `.env`, or any real candidate CV.
- Rubric numbers are **config, not code**: all weights, gates, penalties live in `roles/fde/role.json`.

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | uv project, deps, pytest config |
| `.gitignore` | excludes `data/`, `state/`, `report/`, `.env`, caches |
| `.env.example` | documents `TRAKSTAR_API_KEY`, `OPENING_ID` |
| `roles/fde/role.json` | rubric: criteria + weights, anchors, gates, penalties, cap, versions |
| `roles/fde/redflag_prompt.md` | Pass A prompt (careless-AI tiers, contradictions) |
| `roles/fde/fit_prompt.md` | Pass B prompt (rubric, anchors, fairness rule) |
| `screen/config.py` | load + validate `role.json` into typed objects |
| `screen/paths.py` | every filesystem path derived from opening id; no path strings elsewhere |
| `screen/text.py` | markdown → bullets, normalization, hashing, Jaccard, section lookup |
| `screen/parse.py` | PDF → markdown + PDF metadata + hidden-text spans + sha256 |
| `screen/signals.py` | deterministic signal detectors (placeholders, dup bullets, Tier-2 heuristics, years, degree, LinkedIn, location) |
| `screen/redact.py` | PII redaction of markdown |
| `screen/pool.py` | cross-candidate duplicate-bullet detection |
| `screen/precheck.py` | assemble `prechecks/<id>.json` + `redacted/<id>.md` |
| `screen/fetch.py` | Trakstar client + local-folder source, caching, throttle/backoff |
| `screen/verdict.py` | validate subagent JSON against schema + quote verification |
| `screen/rank.py` | gates, penalties, final score, cap, calibration window |
| `screen/ledger.py` | load/save sticky ledger with backup; run records |
| `screen/report.py` | HTML + Markdown + CSV rendering |
| `screen/cli.py` | `python -m screen <stage>` subcommands |
| `.claude/skills/screen-cvs/SKILL.md` | orchestration: stages, subagent fan-out, calibration |
| `scripts/install-schedule.sh` | launchd daily job |
| `tests/fixtures/make_fixtures.py` | generates fixture PDFs programmatically (no binaries in git) |
| `tests/*` | unit + end-to-end tests |

Split rationale: `signals.py` holds pure detector functions with no I/O so they are trivially testable; `precheck.py` only orchestrates them. `text.py` is shared by `signals.py`, `pool.py`, and `verdict.py` (quote verification), so it must not import either.

---

### Task 1: Project scaffolding, config, and paths

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`, `roles/fde/role.json`, `screen/__init__.py`, `screen/config.py`, `screen/paths.py`
- Test: `tests/test_config.py`, `tests/test_paths.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `screen.config.Criterion(key: str, label: str, max: int)`
  - `screen.config.RoleConfig` with attributes `position_title: str`, `rubric_version: int`, `precheck_rules_version: int`, `cap_fraction: float`, `quality_floor_delta: int`, `calibration_window: int`, `bonus_max: int`, `criteria: list[Criterion]`, `anchors: dict[str, float]`, `gates: dict[str, Any]`, `penalties: dict[str, int]`, `tiebreak_timezones: list[str]`, and methods `criterion(key) -> Criterion`, `max_fit_score() -> int`
  - `screen.config.load_role(role_dir: Path) -> RoleConfig`
  - `screen.paths.Paths(root: Path, opening_id: str)` with attributes `data`, `resumes`, `parsed`, `redacted`, `prechecks`, `verdicts`, `candidates_json`, `pool_duplicates_json`, `state`, `ledger_json`, `runs`, `logs`, `report` and method `ensure() -> None`

- [ ] **Step 1: Initialise git and the uv project**

```bash
cd /Users/hung/Coding/Odin/WorkSpace/CVsScanningAgent
git init
git branch -M main
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "cv-screening-agent"
version = "0.1.0"
description = "Screens Trakstar Hire applicants against a JD and produces a ranked shortlist"
requires-python = ">=3.13"
dependencies = [
    "pymupdf4llm>=0.0.17",
    "pymupdf>=1.24",
    "httpx>=0.27",
]

[dependency-groups]
dev = ["pytest>=8.4"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["screen"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

- [ ] **Step 3: Write `.gitignore`**

```gitignore
# Candidate data — never commit
data/
state/
report/
.env

# Python
__pycache__/
*.pyc
.venv/
.pytest_cache/
.uv/
```

- [ ] **Step 4: Write `.env.example`**

```bash
# Trakstar Hire API key — Settings > Advanced Settings > API Key (requires Super Admin)
TRAKSTAR_API_KEY=
# Forward Deployed Engineer opening
OPENING_ID=704353
```

- [ ] **Step 5: Write `roles/fde/role.json`**

```json
{
  "position_title": "Forward Deployed Engineer, Engineering",
  "rubric_version": 1,
  "precheck_rules_version": 1,
  "cap_fraction": 0.20,
  "quality_floor_delta": 5,
  "calibration_window": 5,
  "bonus_max": 5,
  "criteria": [
    {"key": "production_ownership", "label": "Production engineering ownership", "max": 25},
    {"key": "integration_breadth", "label": "Integration breadth", "max": 20},
    {"key": "client_solutioning", "label": "Client-facing solutioning", "max": 20},
    {"key": "communication_product", "label": "Communication & product sense", "max": 15},
    {"key": "domain", "label": "Domain", "max": 10},
    {"key": "ai_tooling", "label": "AI/LLM tooling", "max": 5},
    {"key": "distributed_collab", "label": "Distributed / offshore collaboration", "max": 5}
  ],
  "anchors": {
    "none": 0.0,
    "claimed": 0.25,
    "one_example": 0.5,
    "several_examples": 0.75,
    "sustained_senior": 1.0
  },
  "gates": {
    "min_years": 4,
    "tier2_gate_count": 3,
    "degree_required": false,
    "intra_dup_jaccard": 0.85,
    "pool_dup_tier1_count": 4,
    "skills_count_threshold": 40,
    "metadata_template_producers": [
      "canva", "resume.io", "zety", "kickresume", "novoresume", "enhancv", "flowcv"
    ],
    "metadata_minutes_threshold": 30,
    "min_text_chars": 200
  },
  "penalties": {
    "no_linkedin": 8,
    "linkedin_name_mismatch": 8,
    "tier2_signal": 5,
    "pool_duplicate": 10,
    "years_4_to_5": 10,
    "no_degree": 5,
    "short_stints": 5
  },
  "tiebreak_timezones": ["ET", "CT"]
}
```

- [ ] **Step 6: Write the failing tests**

`tests/test_config.py`:

```python
from pathlib import Path

import pytest

from screen.config import load_role

ROLE_DIR = Path(__file__).resolve().parents[1] / "roles" / "fde"


def test_load_role_reads_criteria_in_order():
    cfg = load_role(ROLE_DIR)
    assert [c.key for c in cfg.criteria] == [
        "production_ownership",
        "integration_breadth",
        "client_solutioning",
        "communication_product",
        "domain",
        "ai_tooling",
        "distributed_collab",
    ]


def test_max_fit_score_is_100():
    cfg = load_role(ROLE_DIR)
    assert cfg.max_fit_score() == 100


def test_scalars_load():
    cfg = load_role(ROLE_DIR)
    assert cfg.cap_fraction == 0.20
    assert cfg.quality_floor_delta == 5
    assert cfg.calibration_window == 5
    assert cfg.bonus_max == 5
    assert cfg.rubric_version == 1
    assert cfg.precheck_rules_version == 1
    assert cfg.gates["min_years"] == 4
    assert cfg.penalties["no_linkedin"] == 8


def test_criterion_lookup():
    cfg = load_role(ROLE_DIR)
    assert cfg.criterion("domain").max == 10
    with pytest.raises(KeyError):
        cfg.criterion("nope")


def test_rejects_criteria_not_summing_to_100(tmp_path):
    (tmp_path / "role.json").write_text(
        '{"position_title": "x", "rubric_version": 1, "precheck_rules_version": 1,'
        ' "cap_fraction": 0.2, "quality_floor_delta": 5, "calibration_window": 5,'
        ' "bonus_max": 5, "criteria": [{"key": "a", "label": "A", "max": 50}],'
        ' "anchors": {}, "gates": {}, "penalties": {}, "tiebreak_timezones": []}'
    )
    with pytest.raises(ValueError, match="must sum to 100"):
        load_role(tmp_path)
```

`tests/test_paths.py`:

```python
from pathlib import Path

from screen.paths import Paths


def test_paths_are_namespaced_by_opening(tmp_path):
    p = Paths(root=tmp_path, opening_id="704353")
    assert p.data == tmp_path / "data" / "704353"
    assert p.resumes == tmp_path / "data" / "704353" / "resumes"
    assert p.candidates_json == tmp_path / "data" / "704353" / "candidates.json"
    assert p.ledger_json == tmp_path / "state" / "ledger.json"


def test_ensure_creates_all_directories(tmp_path):
    p = Paths(root=tmp_path, opening_id="704353")
    p.ensure()
    for d in (p.resumes, p.parsed, p.redacted, p.prechecks, p.verdicts, p.runs, p.logs, p.report):
        assert d.is_dir()
```

- [ ] **Step 7: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py tests/test_paths.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'screen.config'`

- [ ] **Step 8: Write `screen/__init__.py` and `screen/paths.py`**

`screen/__init__.py`: empty file.

`screen/paths.py`:

```python
"""Every filesystem path used by the pipeline, derived from the opening id.

No other module builds path strings; this keeps the cache layout in one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Paths:
    root: Path
    opening_id: str

    @property
    def data(self) -> Path:
        return self.root / "data" / self.opening_id

    @property
    def resumes(self) -> Path:
        return self.data / "resumes"

    @property
    def parsed(self) -> Path:
        return self.data / "parsed"

    @property
    def redacted(self) -> Path:
        return self.data / "redacted"

    @property
    def prechecks(self) -> Path:
        return self.data / "prechecks"

    @property
    def verdicts(self) -> Path:
        return self.data / "verdicts"

    @property
    def candidates_json(self) -> Path:
        return self.data / "candidates.json"

    @property
    def pool_duplicates_json(self) -> Path:
        return self.data / "pool_duplicates.json"

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def ledger_json(self) -> Path:
        return self.state / "ledger.json"

    @property
    def runs(self) -> Path:
        return self.state / "runs"

    @property
    def logs(self) -> Path:
        return self.state / "logs"

    @property
    def report(self) -> Path:
        return self.root / "report"

    def ensure(self) -> None:
        for d in (
            self.resumes,
            self.parsed,
            self.redacted,
            self.prechecks,
            self.verdicts,
            self.runs,
            self.logs,
            self.report,
        ):
            d.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 9: Write `screen/config.py`**

```python
"""Load and validate the role rubric from roles/<role>/role.json.

All weights, gates, and penalties are config so a new JD means a new folder,
never a code change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Criterion:
    key: str
    label: str
    max: int


@dataclass(frozen=True)
class RoleConfig:
    position_title: str
    rubric_version: int
    precheck_rules_version: int
    cap_fraction: float
    quality_floor_delta: int
    calibration_window: int
    bonus_max: int
    criteria: list[Criterion]
    anchors: dict[str, float]
    gates: dict[str, Any]
    penalties: dict[str, int]
    tiebreak_timezones: list[str]
    role_dir: Path = field(default_factory=Path)

    def criterion(self, key: str) -> Criterion:
        for c in self.criteria:
            if c.key == key:
                return c
        raise KeyError(key)

    def criterion_keys(self) -> list[str]:
        return [c.key for c in self.criteria]

    def max_fit_score(self) -> int:
        return sum(c.max for c in self.criteria)

    def redflag_prompt(self) -> str:
        return (self.role_dir / "redflag_prompt.md").read_text()

    def fit_prompt(self) -> str:
        return (self.role_dir / "fit_prompt.md").read_text()


REQUIRED_KEYS = (
    "position_title",
    "rubric_version",
    "precheck_rules_version",
    "cap_fraction",
    "quality_floor_delta",
    "calibration_window",
    "bonus_max",
    "criteria",
    "anchors",
    "gates",
    "penalties",
    "tiebreak_timezones",
)


def load_role(role_dir: Path) -> RoleConfig:
    raw = json.loads((role_dir / "role.json").read_text())

    missing = [k for k in REQUIRED_KEYS if k not in raw]
    if missing:
        raise ValueError(f"role.json missing keys: {', '.join(missing)}")

    criteria = [
        Criterion(key=c["key"], label=c["label"], max=int(c["max"]))
        for c in raw["criteria"]
    ]
    if not criteria:
        raise ValueError("role.json defines no criteria")

    keys = [c.key for c in criteria]
    if len(set(keys)) != len(keys):
        raise ValueError("role.json has duplicate criterion keys")

    total = sum(c.max for c in criteria)
    if total != 100:
        raise ValueError(f"criteria max values must sum to 100, got {total}")

    if not 0 < float(raw["cap_fraction"]) <= 1:
        raise ValueError("cap_fraction must be in (0, 1]")

    return RoleConfig(
        position_title=raw["position_title"],
        rubric_version=int(raw["rubric_version"]),
        precheck_rules_version=int(raw["precheck_rules_version"]),
        cap_fraction=float(raw["cap_fraction"]),
        quality_floor_delta=int(raw["quality_floor_delta"]),
        calibration_window=int(raw["calibration_window"]),
        bonus_max=int(raw["bonus_max"]),
        criteria=criteria,
        anchors={k: float(v) for k, v in raw["anchors"].items()},
        gates=dict(raw["gates"]),
        penalties={k: int(v) for k, v in raw["penalties"].items()},
        tiebreak_timezones=list(raw["tiebreak_timezones"]),
        role_dir=role_dir,
    )
```

- [ ] **Step 10: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py tests/test_paths.py -v`
Expected: PASS — 7 tests

- [ ] **Step 11: Commit**

```bash
git add pyproject.toml uv.lock .gitignore .env.example roles/ screen/ tests/
git commit -m "feat: project scaffolding, role config loader, path layout"
```

---

### Task 2: Text utilities — bullets, normalization, similarity

**Files:**
- Create: `screen/text.py`
- Test: `tests/test_text.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `screen.text.extract_bullets(markdown: str) -> list[str]`
  - `screen.text.normalize(s: str) -> str`
  - `screen.text.bullet_hash(s: str) -> str` (sha1 of normalized text, 16 hex chars)
  - `screen.text.jaccard(a: str, b: str) -> float` (token-set similarity of normalized text)
  - `screen.text.find_section(markdown: str, names: list[str]) -> str | None`
  - `screen.text.contains_quote(haystack: str, quote: str) -> bool` (whitespace-insensitive substring match)

- [ ] **Step 1: Write the failing tests**

`tests/test_text.py`:

```python
from screen.text import (
    bullet_hash,
    contains_quote,
    extract_bullets,
    find_section,
    jaccard,
    normalize,
)

MD = """
# Jane Doe

## Experience

### Senior Engineer, Acme (2019-2023)
- Built a payments pipeline handling 4M events/day
* Led SSO rollout across 12 enterprise clients

### Engineer, Beta Corp (2016-2019)
+ Maintained SFTP ingestion jobs

## Skills
Python, Go, PostgreSQL
"""


def test_extract_bullets_handles_all_markers():
    bullets = extract_bullets(MD)
    assert bullets == [
        "Built a payments pipeline handling 4M events/day",
        "Led SSO rollout across 12 enterprise clients",
        "Maintained SFTP ingestion jobs",
    ]


def test_extract_bullets_ignores_headings_and_prose():
    assert "Python, Go, PostgreSQL" not in extract_bullets(MD)


def test_normalize_lowercases_strips_punctuation_and_collapses_space():
    assert normalize("  Led   SSO rollout, across 12 clients!  ") == "led sso rollout across 12 clients"


def test_bullet_hash_is_stable_and_normalization_insensitive():
    assert bullet_hash("Led SSO rollout") == bullet_hash("  led   sso   rollout!  ")
    assert bullet_hash("Led SSO rollout") != bullet_hash("Led SAML rollout")
    assert len(bullet_hash("x")) == 16


def test_jaccard_identical_and_disjoint():
    assert jaccard("built a payments pipeline", "Built a payments pipeline!") == 1.0
    assert jaccard("alpha beta", "gamma delta") == 0.0


def test_jaccard_near_duplicate_above_threshold():
    a = "Spearheaded cross-functional initiatives resulting in 40% efficiency gains"
    b = "Spearheaded cross functional initiatives resulting in 45% efficiency gains"
    assert jaccard(a, b) >= 0.85


def test_jaccard_empty_inputs_are_zero():
    assert jaccard("", "anything") == 0.0
    assert jaccard("", "") == 0.0


def test_find_section_returns_body_until_next_heading():
    body = find_section(MD, ["skills"])
    assert body is not None
    assert "Python, Go, PostgreSQL" in body
    assert "Experience" not in body


def test_find_section_is_case_insensitive_and_returns_none_when_absent():
    assert find_section(MD, ["EXPERIENCE"]) is not None
    assert find_section(MD, ["publications"]) is None


def test_find_section_handles_hash_inside_heading_text():
    # "C#" in a heading must not inflate the computed heading level, or the
    # section boundary lands in the wrong place.
    md = "## Skills\n### C# and .NET\n- LINQ\n## Education\nBSc\n"
    body = find_section(md, ["skills"])
    assert body is not None
    assert "LINQ" in body
    assert "BSc" not in body


def test_contains_quote_ignores_whitespace_differences():
    assert contains_quote("Built a  payments\npipeline", "Built a payments pipeline")
    assert not contains_quote("Built a payments pipeline", "Built a billing pipeline")


def test_contains_quote_empty_quote_is_false():
    assert not contains_quote("anything", "")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_text.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'screen.text'`

- [ ] **Step 3: Write `screen/text.py`**

```python
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
    """Token-set similarity of two strings after normalization."""
    ta = set(normalize(a).split())
    tb = set(normalize(b).split())
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_text.py -v`
Expected: PASS — 12 tests

- [ ] **Step 5: Commit**

```bash
git add screen/text.py tests/test_text.py
git commit -m "feat: text utilities for bullets, normalization, similarity, quote checks"
```

---

### Task 3: PDF parsing — markdown, metadata, hidden text

**Files:**
- Create: `screen/parse.py`, `tests/fixtures/make_fixtures.py`
- Test: `tests/test_parse.py`

**Interfaces:**
- Consumes: `screen.paths.Paths`.
- Produces:
  - `screen.parse.ParsedPdf` — frozen dataclass with `markdown: str`, `meta: dict`, `hidden_text: dict`, `pages: int`, `sha256: str`, `text_chars: int`, and `to_meta_json() -> dict`
  - `screen.parse.parse_pdf(pdf_path: Path) -> ParsedPdf`
  - `screen.parse.sha256_file(path: Path) -> str`
  - `screen.parse.write_parsed(parsed: ParsedPdf, md_path: Path, meta_path: Path) -> None`
  - `tests/fixtures/make_fixtures.py::build_all(out_dir: Path) -> dict[str, Path]` — fixture PDFs keyed by name: `clean`, `placeholder`, `template_a`, `template_b`, `hidden_text`, `scanned`, `four_year`

- [ ] **Step 1: Add the PDF dependencies**

```bash
uv add pymupdf4llm pymupdf
```

- [ ] **Step 2: Write the fixture generator**

`tests/fixtures/make_fixtures.py`:

```python
"""Generate fixture CVs as real PDFs at test time.

Keeping generation in code (rather than committing binaries) means fixtures are
reviewable, and lets us build pathological cases — white-on-white text, 2pt
fonts — that are hard to author by hand.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

CLEAN = """Alex Morgan
alex.morgan@example.com | +1 415 555 0134 | Boston, MA
linkedin.com/in/alexmorgan

EXPERIENCE

Staff Forward Deployed Engineer, Northwind Data (2019-03 - Present)
- Owned the client integration platform end to end: 40 enterprise tenants, SFTP
  and REST ingestion, 12M records/day, on-call rotation for 4 years.
- Led discovery workshops with law firms and hedge funds to scope data migrations,
  then translated findings into integration designs the client signed off on.
- Rolled out Okta and Azure AD SAML SSO for 18 clients, cutting onboarding from
  6 weeks to 9 days.
- Generalised a one-off Salesforce sync built for Redwood Capital into a reusable
  CRM connector now used by 22 tenants.

Senior Engineer, Beacon Systems (2015-06 - 2019-02)
- Built the PostgreSQL to Snowflake replication service behind the reporting suite.
- Ran SMTP deliverability for transactional mail, moving bounce rate 4.1% to 0.6%.

EDUCATION
BSc Computer Science, State University

SKILLS
Python, Go, PostgreSQL, Snowflake, SFTP, SAML, OIDC, Salesforce, Terraform, AWS
"""

PLACEHOLDER = """[Your Name]
[Your Email] | [Your Phone]

SUMMARY
Results-driven engineer excited to join [Company Name] as a [Position Title].

EXPERIENCE

Software Engineer, Acme Corp (2018 - 2023)
- Spearheaded cross-functional initiatives resulting in XX% efficiency gains.
- Leveraged synergies to optimise outcomes across the organisation.

EDUCATION
BSc Computer Science, State University
"""

TEMPLATE_SHARED_BULLETS = """- Spearheaded cross-functional initiatives resulting in 40% efficiency gains
- Leveraged cutting-edge technologies to optimise operational outcomes by 35%
- Orchestrated stakeholder alignment resulting in 50% faster delivery cycles
- Facilitated seamless collaboration driving 25% improvement in team velocity
"""

TEMPLATE_A = f"""Jordan Blake
jordan.blake@example.com | Austin, TX

EXPERIENCE
Senior Software Engineer, Globex (2017 - 2024)
{TEMPLATE_SHARED_BULLETS}
EDUCATION
BSc Information Systems, State University
"""

TEMPLATE_B = f"""Riley Chen
riley.chen@example.com | Denver, CO

EXPERIENCE
Lead Engineer, Initech (2016 - 2023)
{TEMPLATE_SHARED_BULLETS}
EDUCATION
BSc Computer Engineering, State University
"""

FOUR_YEAR = """Sam Rivera
sam.rivera@example.com | Chicago, IL
linkedin.com/in/samrivera

EXPERIENCE

Software Engineer, Vertex Labs (2022-01 - Present)
- Built the billing reconciliation service in Python, processing 800k rows daily.
- Integrated the Stripe and NetSuite APIs behind a shared webhook gateway.

Junior Engineer, Vertex Labs (2021-01 - 2021-12)
- Maintained the internal SFTP drop used by 6 partner banks.

EDUCATION
BSc Computer Science, State University

SKILLS
Python, Stripe, NetSuite, SFTP
"""


def _write_text_pdf(path: Path, body: str) -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(
        pymupdf.Rect(50, 50, 545, 780), body, fontsize=9, fontname="helv"
    )
    doc.set_metadata({"producer": "TestSuite", "creator": "TestSuite"})
    doc.save(path)
    doc.close()
    return path


def _write_hidden_text_pdf(path: Path) -> Path:
    """A visually normal CV with white-on-white and 2pt keyword stuffing."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(
        pymupdf.Rect(50, 50, 545, 600), CLEAN, fontsize=9, fontname="helv"
    )
    # White text on the default white background.
    page.insert_text(
        (50, 700),
        "forward deployed engineer SAML SFTP private markets fintech",
        fontsize=9,
        fontname="helv",
        color=(1, 1, 1),
    )
    # Micro font.
    page.insert_text(
        (50, 720),
        "python go postgresql salesforce okta subscription documents",
        fontsize=2,
        fontname="helv",
        color=(0, 0, 0),
    )
    doc.set_metadata({"producer": "TestSuite", "creator": "TestSuite"})
    doc.save(path)
    doc.close()
    return path


def _write_scanned_pdf(path: Path) -> Path:
    """A page with no extractable text, standing in for a scanned CV."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(pymupdf.Rect(60, 60, 500, 700), color=(0.4, 0.4, 0.4), width=2)
    doc.set_metadata({"producer": "TestSuite", "creator": "TestSuite"})
    doc.save(path)
    doc.close()
    return path


def build_all(out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    return {
        "clean": _write_text_pdf(out_dir / "clean.pdf", CLEAN),
        "placeholder": _write_text_pdf(out_dir / "placeholder.pdf", PLACEHOLDER),
        "template_a": _write_text_pdf(out_dir / "template_a.pdf", TEMPLATE_A),
        "template_b": _write_text_pdf(out_dir / "template_b.pdf", TEMPLATE_B),
        "four_year": _write_text_pdf(out_dir / "four_year.pdf", FOUR_YEAR),
        "hidden_text": _write_hidden_text_pdf(out_dir / "hidden_text.pdf"),
        "scanned": _write_scanned_pdf(out_dir / "scanned.pdf"),
    }


if __name__ == "__main__":
    import sys

    built = build_all(Path(sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/pdfs"))
    for name, p in built.items():
        print(f"{name}: {p}")
```

- [ ] **Step 3: Write the failing tests**

`tests/test_parse.py`:

```python
from pathlib import Path

import pytest

from screen.parse import parse_pdf, sha256_file, write_parsed

import sys

sys.path.insert(0, str(Path(__file__).parent))
from fixtures.make_fixtures import build_all  # noqa: E402


@pytest.fixture(scope="module")
def pdfs(tmp_path_factory):
    return build_all(tmp_path_factory.mktemp("pdfs"))


def test_parse_clean_extracts_text_and_page_count(pdfs):
    p = parse_pdf(pdfs["clean"])
    assert p.pages == 1
    assert p.text_chars > 200
    assert "Northwind Data" in p.markdown
    assert "SAML SSO" in p.markdown or "SAML" in p.markdown


def test_sha256_is_stable_and_matches_helper(pdfs):
    p = parse_pdf(pdfs["clean"])
    assert p.sha256 == sha256_file(pdfs["clean"])
    assert len(p.sha256) == 64


def test_metadata_captures_producer_and_creation(pdfs):
    p = parse_pdf(pdfs["clean"])
    assert p.meta["producer"] == "TestSuite"
    assert "created" in p.meta


def test_hidden_text_detected_for_white_and_micro_font(pdfs):
    p = parse_pdf(pdfs["hidden_text"])
    assert p.hidden_text["found"] is True
    kinds = {s["kind"] for s in p.hidden_text["spans"]}
    assert "white_on_white" in kinds
    assert "micro_font" in kinds


def test_clean_cv_has_no_hidden_text(pdfs):
    p = parse_pdf(pdfs["clean"])
    assert p.hidden_text["found"] is False
    assert p.hidden_text["spans"] == []


def test_scanned_pdf_yields_almost_no_text(pdfs):
    p = parse_pdf(pdfs["scanned"])
    assert p.text_chars < 200


def test_write_parsed_writes_markdown_and_meta(tmp_path, pdfs):
    p = parse_pdf(pdfs["clean"])
    md, meta = tmp_path / "1.md", tmp_path / "1.meta.json"
    write_parsed(p, md, meta)
    assert md.read_text() == p.markdown
    import json

    loaded = json.loads(meta.read_text())
    assert loaded["sha256"] == p.sha256
    assert loaded["pages"] == 1
    assert loaded["hidden_text"]["found"] is False
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/test_parse.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'screen.parse'`

- [ ] **Step 5: Write `screen/parse.py`**

```python
"""PDF to markdown, plus the two forensic signals we can only get from the PDF:
document metadata and text that was never meant to be seen.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf
import pymupdf4llm

# sRGB integer for pure white, as returned by page.get_text("dict").
_WHITE = 0xFFFFFF
_LUMINANCE_HIDDEN = 0.92
_MICRO_FONT_PT = 4.0
_INVISIBLE_RENDER_MODE = 3


@dataclass(frozen=True)
class ParsedPdf:
    markdown: str
    meta: dict[str, Any]
    hidden_text: dict[str, Any]
    pages: int
    sha256: str
    text_chars: int

    def to_meta_json(self) -> dict[str, Any]:
        return {
            "sha256": self.sha256,
            "pages": self.pages,
            "text_chars": self.text_chars,
            "pdf_meta": self.meta,
            "hidden_text": self.hidden_text,
        }


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _luminance(color_int: int) -> float:
    r = ((color_int >> 16) & 0xFF) / 255
    g = ((color_int >> 8) & 0xFF) / 255
    b = (color_int & 0xFF) / 255
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _hidden_spans(doc: pymupdf.Document) -> list[dict[str, Any]]:
    """Find text a human reader would never see.

    Three kinds: white (or near-white) on the default white page, fonts below
    4pt, and spans drawn with the invisible text render mode.
    """
    spans: list[dict[str, Any]] = []

    for pno, page in enumerate(doc, start=1):
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = (span.get("text") or "").strip()
                    if not text:
                        continue
                    color = int(span.get("color", 0))
                    size = float(span.get("size", 12.0))
                    if color == _WHITE or _luminance(color) >= _LUMINANCE_HIDDEN:
                        spans.append(
                            {"page": pno, "kind": "white_on_white", "text": text[:200], "size": size}
                        )
                    elif size < _MICRO_FONT_PT:
                        spans.append(
                            {"page": pno, "kind": "micro_font", "text": text[:200], "size": size}
                        )

        try:
            for trace in page.get_texttrace():
                if int(trace.get("type", 0)) != _INVISIBLE_RENDER_MODE:
                    continue
                chars = trace.get("chars") or []
                text = "".join(chr(c[0]) for c in chars if isinstance(c, (list, tuple)) and c).strip()
                if text:
                    spans.append(
                        {"page": pno, "kind": "invisible_render_mode", "text": text[:200], "size": None}
                    )
        except Exception:
            # get_texttrace is best-effort; the colour and size checks above are
            # the primary signal and must not be lost to a trace failure.
            pass

    return spans


def parse_pdf(pdf_path: Path) -> ParsedPdf:
    doc = pymupdf.open(pdf_path)
    try:
        markdown = pymupdf4llm.to_markdown(doc, show_progress=False)
        raw_meta = doc.metadata or {}
        meta = {
            "producer": (raw_meta.get("producer") or "").strip(),
            "creator": (raw_meta.get("creator") or "").strip(),
            "created": (raw_meta.get("creationDate") or "").strip(),
            "modified": (raw_meta.get("modDate") or "").strip(),
        }
        spans = _hidden_spans(doc)
        pages = doc.page_count
    finally:
        doc.close()

    return ParsedPdf(
        markdown=markdown,
        meta=meta,
        hidden_text={"found": bool(spans), "spans": spans},
        pages=pages,
        sha256=sha256_file(pdf_path),
        text_chars=len(markdown.strip()),
    )


def write_parsed(parsed: ParsedPdf, md_path: Path, meta_path: Path) -> None:
    """Write atomically: temp file then rename, so an interrupted run leaves no
    half-written artifact for the next run to trust.
    """
    for path, payload in (
        (md_path, parsed.markdown),
        (meta_path, json.dumps(parsed.to_meta_json(), indent=2)),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(payload)
        tmp.replace(path)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_parse.py -v`
Expected: PASS — 7 tests.

If `test_hidden_text_detected_for_white_and_micro_font` fails, print the spans first — `uv run python -c "from pathlib import Path; import sys; sys.path.insert(0,'tests'); from fixtures.make_fixtures import build_all; from screen.parse import parse_pdf; p=build_all(Path('/tmp/fx')); print(parse_pdf(p['hidden_text']).hidden_text)"` — and adjust the colour/size extraction to match what PyMuPDF actually reports. Do not relax the test to pass.

- [ ] **Step 7: Commit**

```bash
git add screen/parse.py tests/test_parse.py tests/fixtures/make_fixtures.py pyproject.toml uv.lock
git commit -m "feat: PDF parsing with metadata and hidden-text detection"
```

---

### Task 4: Signals A — placeholders, intra-CV duplicates, Tier-2 heuristics

**Files:**
- Create: `screen/signals.py`
- Test: `tests/test_signals_slop.py`

**Interfaces:**
- Consumes: `screen.text.extract_bullets`, `normalize`, `jaccard`, `find_section`; `screen.config.RoleConfig`.
- Produces:
  - `screen.signals.find_placeholders(text: str) -> list[dict]` — each `{"kind": "placeholder", "match": str}`
  - `screen.signals.intra_cv_duplicates(bullets: list[str], threshold: float) -> list[dict]` — each `{"a": str, "b": str, "similarity": float}`
  - `screen.signals.power_verb_density(bullets: list[str]) -> float`
  - `screen.signals.round_metric_ratio(bullets: list[str]) -> float`
  - `screen.signals.count_skills(markdown: str) -> int`
  - `screen.signals.template_metadata_signal(meta: dict, minutes_before_submission: float | None, cfg: RoleConfig) -> bool`

- [ ] **Step 1: Write the failing tests**

`tests/test_signals_slop.py`:

```python
from pathlib import Path

from screen.config import load_role
from screen.signals import (
    count_skills,
    find_placeholders,
    intra_cv_duplicates,
    power_verb_density,
    round_metric_ratio,
    template_metadata_signal,
)

CFG = load_role(Path(__file__).resolve().parents[1] / "roles" / "fde")


def test_find_placeholders_catches_bracketed_tokens():
    text = "Excited to join [Company Name] as a [Position Title]. Contact [Your Email]."
    kinds = [p["match"] for p in find_placeholders(text)]
    assert "[Company Name]" in kinds
    assert "[Position Title]" in kinds
    assert "[Your Email]" in kinds


def test_find_placeholders_catches_lorem_and_xx_metrics():
    found = find_placeholders("Lorem ipsum dolor. Improved throughput by XX%.")
    matches = " ".join(p["match"].lower() for p in found)
    assert "lorem ipsum" in matches
    assert "xx%" in matches


def test_find_placeholders_catches_mustache_and_insert():
    found = find_placeholders("Hello {{name}}, [Insert metric here].")
    matches = " ".join(p["match"].lower() for p in found)
    assert "{{name}}" in matches
    assert "insert metric here" in matches


def test_find_placeholders_ignores_normal_brackets():
    # Real CVs cite things like "[1]" or "(2019-2023)"; these must not fire.
    assert find_placeholders("Published in JMLR [1]. Worked 2019-2023 (Acme).") == []


def test_intra_cv_duplicates_flags_near_identical_bullets():
    bullets = [
        "Spearheaded cross-functional initiatives resulting in 40% efficiency gains",
        "Spearheaded cross functional initiatives resulting in 45% efficiency gains",
        "Built a PostgreSQL replication service for the reporting suite",
    ]
    dups = intra_cv_duplicates(bullets, threshold=0.85)
    assert len(dups) == 1
    assert dups[0]["similarity"] >= 0.85


def test_intra_cv_duplicates_ignores_distinct_bullets():
    bullets = [
        "Built a PostgreSQL replication service",
        "Led SAML SSO rollout for 18 clients",
        "Ran SMTP deliverability improvements",
    ]
    assert intra_cv_duplicates(bullets, threshold=0.85) == []


def test_power_verb_density_high_for_slop_and_low_for_specifics():
    slop = [
        "Spearheaded cross-functional initiatives",
        "Leveraged cutting-edge technologies",
        "Orchestrated stakeholder alignment",
        "Facilitated seamless collaboration",
    ]
    real = [
        "Built the PostgreSQL to Snowflake replication service",
        "Ran SMTP deliverability, bounce rate 4.1% to 0.6%",
        "Rolled out Okta SAML for 18 clients",
        "Owned on-call for the ingestion platform",
    ]
    assert power_verb_density(slop) >= 0.75
    assert power_verb_density(real) <= 0.25


def test_round_metric_ratio_counts_only_round_percentages():
    round_only = ["improved by 40%", "reduced by 25%", "grew 50%"]
    specific = ["improved by 4.1%", "reduced 12 hours to 37 minutes", "grew 18.6%"]
    assert round_metric_ratio(round_only) == 1.0
    assert round_metric_ratio(specific) == 0.0


def test_round_metric_ratio_zero_when_no_metrics():
    assert round_metric_ratio(["built a thing", "owned a service"]) == 0.0


def test_power_verb_density_and_round_ratio_handle_empty():
    assert power_verb_density([]) == 0.0
    assert round_metric_ratio([]) == 0.0


def test_count_skills_splits_on_commas_and_pipes():
    md = "## Skills\nPython, Go | PostgreSQL; Snowflake, SFTP\n\n## Education\nBSc\n"
    assert count_skills(md) == 5


def test_count_skills_zero_without_section():
    assert count_skills("## Experience\n- did things\n") == 0


def test_template_metadata_signal_true_for_known_producer_and_fresh_file():
    meta = {"producer": "Canva", "creator": "Canva"}
    assert template_metadata_signal(meta, minutes_before_submission=5, cfg=CFG) is True


def test_template_metadata_signal_false_when_old_or_unknown_producer():
    assert template_metadata_signal({"producer": "Canva"}, 120, CFG) is False
    assert template_metadata_signal({"producer": "LaTeX"}, 5, CFG) is False
    assert template_metadata_signal({"producer": "Canva"}, None, CFG) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_signals_slop.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'screen.signals'`

- [ ] **Step 3: Write the first half of `screen/signals.py`**

```python
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

_PLACEHOLDER_PATTERNS = (
    # Bracketed template fields: [Your Name], [Company Name], [Position Title].
    re.compile(r"\[[^\]\n]*\b(?:your|company|position|title|name|email|phone|employer|role|job)\b[^\]\n]*\]", re.I),
    # Explicit insert instructions, with or without brackets.
    re.compile(r"\[?\s*insert\s+[^\]\n.]{2,40}\s*\]?", re.I),
    re.compile(r"lorem\s+ipsum", re.I),
    # Unfilled metric placeholders: XX%, X%, NN%.
    re.compile(r"\b(?:x{1,3}|n{2,3})\s?%", re.I),
    # Template engine syntax left behind.
    re.compile(r"\{\{[^}\n]{1,40}\}\}"),
    re.compile(r"<[A-Z_]{3,30}>"),
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


def intra_cv_duplicates(bullets: list[str], threshold: float) -> list[dict[str, Any]]:
    """Pairs of bullets in the same CV that are near-identical."""
    out: list[dict[str, Any]] = []
    for i in range(len(bullets)):
        for j in range(i + 1, len(bullets)):
            sim = jaccard(bullets[i], bullets[j])
            if sim >= threshold:
                out.append({"a": bullets[i], "b": bullets[j], "similarity": round(sim, 3)})
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
_ANY_METRIC_RE = re.compile(r"\b\d+(?:\.\d+)?\s?%|\b\d[\d,]*\b")


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
    return any(p in haystack for p in cfg.gates["metadata_template_producers"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_signals_slop.py -v`
Expected: PASS — 14 tests.

- [ ] **Step 5: Commit**

```bash
git add screen/signals.py tests/test_signals_slop.py
git commit -m "feat: placeholder, duplicate-bullet, and AI-slop heuristic detectors"
```

---

### Task 5: Signals B — employment dates, years of experience, stints, gaps

**Files:**
- Modify: `screen/signals.py` (append the date section)
- Test: `tests/test_signals_dates.py`

**Interfaces:**
- Consumes: `screen.text.find_section`.
- Produces:
  - `screen.signals.DateRange` — frozen dataclass `start: tuple[int, int]`, `end: tuple[int, int] | None`, `precise: bool`, `raw: str`; `end is None` means "Present"
  - `screen.signals.extract_date_ranges(markdown: str) -> list[DateRange]`
  - `screen.signals.compute_years(markdown: str, today: tuple[int, int]) -> dict` — `{"computed": float, "confidence": "high"|"medium"|"low", "ranges": [[str, str], ...]}`
  - `screen.signals.short_stints(ranges: list[DateRange], today: tuple[int, int], years_back: int) -> int`
  - `screen.signals.has_gap_over(ranges: list[DateRange], today: tuple[int, int], months: int, years_back: int) -> bool`

Note: this computes **total professional experience** from the experience section's date ranges. It cannot tell software engineering from other work — the judge estimates that separately, and gate G4 prefers the judge's number when confidence is `low`.

- [ ] **Step 1: Write the failing tests**

`tests/test_signals_dates.py`:

```python
from screen.signals import (
    DateRange,
    compute_years,
    extract_date_ranges,
    has_gap_over,
    short_stints,
)

TODAY = (2026, 8)

MD = """
## Experience

### Staff Engineer, Northwind (2019-03 - Present)
- did things

### Senior Engineer, Beacon (2015-06 - 2019-02)
- did other things
"""

MD_YEARS_ONLY = """
## Experience
### Engineer, Acme (2018 - 2023)
- work
### Engineer, Globex (2014 - 2017)
- work
"""


def test_extract_date_ranges_month_year_and_present():
    ranges = extract_date_ranges(MD)
    assert len(ranges) == 2
    assert ranges[0].start == (2019, 3)
    assert ranges[0].end is None
    assert ranges[0].precise is True
    assert ranges[1].start == (2015, 6)
    assert ranges[1].end == (2019, 2)


def test_extract_date_ranges_named_months():
    md = "### Engineer, Acme (Mar 2019 - Feb 2021)\n- work\n"
    ranges = extract_date_ranges(md)
    assert ranges[0].start == (2019, 3)
    assert ranges[0].end == (2021, 2)
    assert ranges[0].precise is True


def test_extract_date_ranges_year_only_marks_imprecise():
    ranges = extract_date_ranges(MD_YEARS_ONLY)
    assert len(ranges) == 2
    assert all(r.precise is False for r in ranges)
    assert ranges[0].start == (2018, 1)


def test_extract_date_ranges_handles_en_dash_and_to():
    md = "### A (2019-01 – 2020-01)\n### B (2015-01 to 2016-01)\n"
    assert len(extract_date_ranges(md)) == 2


def test_extract_date_ranges_ignores_education_only_years():
    md = "## Education\nBSc Computer Science, State University, 2014\n"
    assert extract_date_ranges(md) == []


def test_compute_years_sums_non_overlapping_ranges():
    result = compute_years(MD, today=TODAY)
    # 2015-06..2019-02 = 45 months; 2019-03..2026-08 = 90 months; total 135 = 11.25y
    assert result["computed"] == 11.2 or abs(result["computed"] - 11.25) < 0.1
    assert result["confidence"] == "high"


def test_compute_years_merges_overlapping_ranges():
    md = """
## Experience
### A (2018-01 - 2022-01)
### B (2020-01 - 2024-01)
"""
    result = compute_years(md, today=TODAY)
    # Union is 2018-01..2024-01 = 72 months = 6.0y, not 4+4=8.
    assert abs(result["computed"] - 6.0) < 0.1


def test_compute_years_confidence_medium_for_year_only():
    result = compute_years(MD_YEARS_ONLY, today=TODAY)
    assert result["confidence"] == "medium"


def test_compute_years_confidence_low_with_single_or_no_range():
    assert compute_years("## Experience\n### A (2020-01 - 2021-01)\n", TODAY)["confidence"] == "low"
    assert compute_years("## Experience\nno dates here\n", TODAY)["confidence"] == "low"
    assert compute_years("## Experience\nno dates here\n", TODAY)["computed"] == 0.0


def test_short_stints_counts_sub_year_roles_in_window():
    ranges = [
        DateRange(start=(2025, 1), end=(2025, 6), precise=True, raw="a"),
        DateRange(start=(2024, 1), end=(2024, 8), precise=True, raw="b"),
        DateRange(start=(2023, 1), end=(2023, 5), precise=True, raw="c"),
        DateRange(start=(2015, 1), end=(2015, 4), precise=True, raw="old"),
    ]
    assert short_stints(ranges, today=TODAY, years_back=5) == 3


def test_short_stints_ignores_current_role_and_long_roles():
    ranges = [
        DateRange(start=(2026, 6), end=None, precise=True, raw="current"),
        DateRange(start=(2022, 1), end=(2025, 1), precise=True, raw="long"),
    ]
    assert short_stints(ranges, today=TODAY, years_back=5) == 0


def test_has_gap_over_detects_long_break():
    ranges = [
        DateRange(start=(2024, 6), end=None, precise=True, raw="a"),
        DateRange(start=(2018, 1), end=(2022, 1), precise=True, raw="b"),
    ]
    assert has_gap_over(ranges, today=TODAY, months=12, years_back=6) is True


def test_has_gap_over_false_for_continuous_history():
    ranges = [
        DateRange(start=(2022, 2), end=None, precise=True, raw="a"),
        DateRange(start=(2018, 1), end=(2022, 1), precise=True, raw="b"),
    ]
    assert has_gap_over(ranges, today=TODAY, months=12, years_back=6) is False


def test_has_gap_over_false_with_fewer_than_two_ranges():
    assert has_gap_over([], TODAY, 12, 6) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_signals_dates.py -v`
Expected: FAIL — `ImportError: cannot import name 'DateRange'`

- [ ] **Step 3: Append the date section to `screen/signals.py`**

```python
# --- Employment dates ------------------------------------------------------

from dataclasses import dataclass  # noqa: E402  (grouped with the date section)

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

_PRESENT = re.compile(r"\b(present|current|now|today|ongoing)\b", re.I)
_SEP = r"(?:\s*(?:-|–|—|to|until|through)\s*)"
_MY = r"(?:(?P<{p}mon>[A-Za-z]{{3,9}})\.?\s+)?(?P<{p}year>(?:19|20)\d{{2}})(?:\s*[-/]\s*(?P<{p}num>0?[1-9]|1[0-2]))?"

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
    some are year-only; low with fewer than 2 ranges (the judge's estimate wins).
    """
    ranges = extract_date_ranges(markdown)
    merged = _merged_intervals(ranges, today)
    months = sum(end - start + 1 for start, end in merged)
    years = round(months / 12, 2)

    if len(ranges) < 2:
        confidence = "low"
    elif all(r.precise for r in ranges):
        confidence = "high"
    else:
        confidence = "medium"

    return {
        "computed": years if ranges else 0.0,
        "confidence": confidence,
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_signals_dates.py -v`
Expected: PASS — 14 tests. The `_RANGE_RE` named-group formatting is the fiddly part; if group names collide, rename with the `{p}` prefix as written rather than simplifying the regex.

- [ ] **Step 5: Commit**

```bash
git add screen/signals.py tests/test_signals_dates.py
git commit -m "feat: employment date parsing, years of experience, stints and gaps"
```

---

### Task 6: Signals C — LinkedIn, degree, location

**Files:**
- Modify: `screen/signals.py` (append the profile section)
- Test: `tests/test_signals_profile.py`

**Interfaces:**
- Consumes: `screen.text.find_section`, `normalize`.
- Produces:
  - `screen.signals.find_linkedin(markdown: str, profile_data: list[dict], full_name: str) -> dict` — `{"present": bool, "source": "trakstar"|"cv"|"none", "url": str|None, "name_matches": bool|None}`
  - `screen.signals.find_degree(markdown: str) -> dict` — `{"present": bool, "level": str|None, "field": str|None}`
  - `screen.signals.find_location(markdown: str, profile_data: list[dict]) -> dict` — `{"us_evident": bool, "non_us_explicit": bool, "timezone_hint": str, "raw": str|None}`

- [ ] **Step 1: Write the failing tests**

`tests/test_signals_profile.py`:

```python
from screen.signals import find_degree, find_linkedin, find_location


def test_find_linkedin_prefers_trakstar_profile_data():
    pd = [{"name": "LinkedIn Profile", "value": "https://linkedin.com/in/alexmorgan"}]
    r = find_linkedin("no link in cv", pd, "Alex Morgan")
    assert r["present"] is True
    assert r["source"] == "trakstar"
    assert r["name_matches"] is True


def test_find_linkedin_falls_back_to_cv_text():
    r = find_linkedin("linkedin.com/in/alex-morgan", [], "Alex Morgan")
    assert r["present"] is True
    assert r["source"] == "cv"
    assert r["name_matches"] is True


def test_find_linkedin_absent_everywhere():
    r = find_linkedin("no profile here", [], "Alex Morgan")
    assert r == {"present": False, "source": "none", "url": None, "name_matches": None}


def test_find_linkedin_name_mismatch_flagged():
    pd = [{"name": "LinkedIn", "value": "https://linkedin.com/in/someoneelse123"}]
    r = find_linkedin("", pd, "Alex Morgan")
    assert r["present"] is True
    assert r["name_matches"] is False


def test_find_linkedin_name_unknown_for_opaque_slug():
    pd = [{"name": "LinkedIn", "value": "https://linkedin.com/in/ab12xy90"}]
    r = find_linkedin("", pd, "Alex Morgan")
    assert r["name_matches"] is None


def test_find_degree_detects_level_and_field():
    md = "## Education\nBSc Computer Science, State University\n"
    r = find_degree(md)
    assert r["present"] is True
    assert r["level"] == "BSc"
    assert "computer science" in r["field"].lower()


def test_find_degree_detects_spelled_out_bachelors_and_masters():
    assert find_degree("Bachelor of Science in Information Systems")["present"] is True
    assert find_degree("Master's degree in Computer Engineering")["level"] in {"MSc", "Master"}


def test_find_degree_absent():
    r = find_degree("## Experience\n- worked places\n")
    assert r == {"present": False, "level": None, "field": None}


def test_find_location_us_state_gives_timezone_hint():
    r = find_location("Boston, MA", [])
    assert r["us_evident"] is True
    assert r["non_us_explicit"] is False
    assert r["timezone_hint"] == "ET"


def test_find_location_central_state():
    assert find_location("Austin, TX", [])["timezone_hint"] == "CT"


def test_find_location_non_us_explicit():
    r = find_location("Hanoi, Vietnam", [])
    assert r["non_us_explicit"] is True
    assert r["us_evident"] is False


def test_find_location_unknown_when_silent():
    r = find_location("no address on this cv", [])
    assert r["us_evident"] is False
    assert r["non_us_explicit"] is False
    assert r["timezone_hint"] == "unknown"


def test_find_location_prefers_trakstar_field():
    pd = [{"name": "Location", "value": "Chicago, IL"}]
    r = find_location("no address", pd)
    assert r["us_evident"] is True
    assert r["timezone_hint"] == "CT"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_signals_profile.py -v`
Expected: FAIL — `ImportError: cannot import name 'find_linkedin'`

- [ ] **Step 3: Append the profile section to `screen/signals.py`**

```python
# --- LinkedIn, degree, location --------------------------------------------

_LINKEDIN_RE = re.compile(
    r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/(?:in|pub)/([A-Za-z0-9\-_%]+)", re.I
)

_DEGREE_PATTERNS = (
    (re.compile(r"\b(?:ph\.?d|doctorate)\b", re.I), "PhD"),
    (re.compile(r"\b(?:m\.?sc|msc|m\.?s\.|master(?:'s|s)?(?:\s+of\s+\w+)?)\b", re.I), "MSc"),
    (re.compile(r"\bm\.?eng\b", re.I), "MEng"),
    (re.compile(r"\bmba\b", re.I), "MBA"),
    (re.compile(r"\b(?:b\.?sc|bsc|b\.?s\.|bachelor(?:'s|s)?(?:\s+of\s+\w+)?)\b", re.I), "BSc"),
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
    r"\b([A-Z]{2})\b(?:\s+\d{5})?|\b(United States|USA|U\.S\.A?\.)\b"
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


def _profile_value(profile_data: list[dict[str, Any]], *needles: str) -> str | None:
    for item in profile_data or []:
        name = str(item.get("name", "")).lower()
        if any(n in name for n in needles):
            value = str(item.get("value", "")).strip()
            if value:
                return value
    return None


def _slug_matches_name(slug: str, full_name: str) -> bool | None:
    """True/False when the slug carries name-like tokens, None when opaque."""
    slug_tokens = {t for t in re.split(r"[-_%\d]+", slug.lower()) if len(t) > 2}
    if not slug_tokens:
        return None
    name_tokens = {t for t in normalize(full_name).split() if len(t) > 2}
    if not name_tokens:
        return None
    if slug_tokens & name_tokens:
        return True
    # Slug has real words but none of them are the candidate's name.
    if any(len(t) > 3 for t in slug_tokens):
        return False
    return None


def find_linkedin(
    markdown: str, profile_data: list[dict[str, Any]], full_name: str
) -> dict[str, Any]:
    url = _profile_value(profile_data or [], "linkedin")
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
    raw = _profile_value(profile_data or [], "location", "city", "address")
    haystack = raw or "\n".join((markdown or "").splitlines()[:12])

    lowered = haystack.lower()
    non_us = any(c in lowered for c in _NON_US_COUNTRIES)
    if non_us and _WORK_AUTH_RE.search(markdown or ""):
        # Says they are abroad but also authorised or relocating: not a gate.
        non_us = False

    timezone_hint = "unknown"
    us_evident = False
    for m in _US_HINT_RE.finditer(haystack):
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_signals_profile.py -v`
Expected: PASS — 13 tests.

- [ ] **Step 5: Run the whole suite to catch regressions**

Run: `uv run pytest -v`
Expected: PASS — all tests from Tasks 1–6.

- [ ] **Step 6: Commit**

```bash
git add screen/signals.py tests/test_signals_profile.py
git commit -m "feat: LinkedIn, degree, and location detection"
```

---

### Task 7: PII redaction

**Files:**
- Create: `screen/redact.py`
- Test: `tests/test_redact.py`

**Interfaces:**
- Consumes: `screen.text.find_section`.
- Produces:
  - `screen.redact.RedactionResult` — frozen dataclass `text: str`, `tokens_replaced: int`, `kinds: dict[str, int]`
  - `screen.redact.redact(markdown: str, candidate: dict) -> RedactionResult`
  - `screen.redact.assert_clean(text: str) -> list[str]` — returns the list of leak descriptions found (empty means clean); used by tests and by `precheck` as a safety net

The `candidate` dict is a Trakstar candidate record; `first_name`, `last_name`, `email`, `phone` are used to redact values the regexes might miss.

- [ ] **Step 1: Write the failing tests**

`tests/test_redact.py`:

```python
from screen.redact import assert_clean, redact

CANDIDATE = {
    "first_name": "Alex",
    "last_name": "Morgan",
    "email": "alex.morgan@example.com",
    "phone": "+1 415 555 0134",
}

MD = """# Alex Morgan
alex.morgan@example.com | +1 415 555 0134 | Boston, MA
linkedin.com/in/alexmorgan | github.com/alexmorgan

## Experience
### Staff Engineer, Northwind Data (2019-03 - Present)
- Led SSO rollout for 18 clients, cutting onboarding from 6 weeks to 9 days.

## Education
BSc Computer Science, State University
"""


def test_redacts_name_email_and_phone():
    r = redact(MD, CANDIDATE)
    assert "Alex Morgan" not in r.text
    assert "alex.morgan@example.com" not in r.text
    assert "555 0134" not in r.text
    assert "[NAME]" in r.text
    assert "[EMAIL]" in r.text
    assert "[PHONE]" in r.text


def test_redacts_linkedin_and_school_but_keeps_degree():
    r = redact(MD, CANDIDATE)
    assert "linkedin.com/in/alexmorgan" not in r.text
    assert "State University" not in r.text
    assert "BSc Computer Science" in r.text


def test_keeps_employer_names_and_achievements():
    # Employers and outcomes are exactly what the judge must score on.
    r = redact(MD, CANDIDATE)
    assert "Northwind Data" in r.text
    assert "cutting onboarding from 6 weeks to 9 days" in r.text
    assert "2019-03" in r.text


def test_redacts_us_city_state():
    r = redact(MD, CANDIDATE)
    assert "Boston, MA" not in r.text
    assert "[LOCATION]" in r.text


def test_tokens_replaced_counts_and_kinds():
    r = redact(MD, CANDIDATE)
    assert r.tokens_replaced >= 5
    assert r.kinds["name"] >= 1
    assert r.kinds["email"] >= 1


def test_assert_clean_finds_nothing_in_redacted_output():
    r = redact(MD, CANDIDATE)
    assert assert_clean(r.text) == []


def test_assert_clean_reports_leaks():
    leaks = assert_clean("Contact me at bob@example.com or +1 415 555 9999")
    assert any("email" in leak for leak in leaks)
    assert any("phone" in leak for leak in leaks)


def test_redact_handles_missing_candidate_fields():
    r = redact("Some CV text with no PII patterns.", {})
    assert r.text.strip() != ""
    assert r.tokens_replaced == 0


def test_redact_is_idempotent():
    once = redact(MD, CANDIDATE).text
    twice = redact(once, CANDIDATE).text
    assert once == twice
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_redact.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'screen.redact'`

- [ ] **Step 3: Write `screen/redact.py`**

```python
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


def redact(markdown: str, candidate: dict[str, Any]) -> RedactionResult:
    text = markdown or ""
    kinds: dict[str, int] = {}

    def bump(kind: str, n: int) -> None:
        if n:
            kinds[kind] = kinds.get(kind, 0) + n

    # 1. Known values from the ATS record first — most reliable.
    first = str(candidate.get("first_name") or "").strip()
    last = str(candidate.get("last_name") or "").strip()
    full = f"{first} {last}".strip()
    n_name = 0
    for value in [v for v in (full, first, last) if len(v) > 2]:
        text, n = _sub_counting(re.compile(rf"\b{re.escape(value)}\b", re.I), "[NAME]", text)
        n_name += n
    bump("name", n_name)

    for field, token, kind in (
        ("email", "[EMAIL]", "email"),
        ("phone", "[PHONE]", "phone"),
    ):
        value = str(candidate.get(field) or "").strip()
        if len(value) > 4:
            text, n = _sub_counting(re.compile(re.escape(value), re.I), token, text)
            bump(kind, n)

    # 2. Pattern-based sweep for anything the record did not cover.
    text, n = _sub_counting(_EMAIL_RE, "[EMAIL]", text)
    bump("email", n)

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_redact.py -v`
Expected: PASS — 9 tests.

Redaction is where over-eager regexes destroy scorable content. If `test_keeps_employer_names_and_achievements` fails, tighten the offending pattern — never loosen the test. In particular the `[NAME]` substitution must not eat employer names that share a token with the candidate's name.

- [ ] **Step 5: Commit**

```bash
git add screen/redact.py tests/test_redact.py
git commit -m "feat: PII redaction with leak assertion"
```

---

### Task 8: Cross-pool duplicate bullets

**Files:**
- Create: `screen/pool.py`
- Test: `tests/test_pool.py`

**Interfaces:**
- Consumes: `screen.text.bullet_hash`, `extract_bullets`.
- Produces:
  - `screen.pool.pool_duplicates(bullets_by_candidate: dict[int, list[str]], min_words: int = 6) -> dict[int, list[dict]]` — per candidate, `[{"with_candidate": int, "bullet": str}]`
  - `screen.pool.write_pool_duplicates(result: dict[int, list[dict]], path: Path) -> None`
  - `screen.pool.load_pool_duplicates(path: Path) -> dict[int, list[dict]]`

- [ ] **Step 1: Write the failing tests**

`tests/test_pool.py`:

```python
import json

from screen.pool import load_pool_duplicates, pool_duplicates, write_pool_duplicates

SHARED = "Spearheaded cross-functional initiatives resulting in 40% efficiency gains"


def test_finds_bullet_shared_between_two_candidates():
    result = pool_duplicates(
        {
            1: [SHARED, "Built a PostgreSQL replication service for reporting"],
            2: [SHARED, "Ran SMTP deliverability improvements across three regions"],
        }
    )
    assert len(result[1]) == 1
    assert result[1][0]["with_candidate"] == 2
    assert result[1][0]["bullet"] == SHARED
    assert result[2][0]["with_candidate"] == 1


def test_no_duplicates_when_all_bullets_unique():
    result = pool_duplicates(
        {
            1: ["Built a PostgreSQL replication service for reporting"],
            2: ["Ran SMTP deliverability improvements across three regions"],
        }
    )
    assert result == {}


def test_ignores_short_generic_bullets():
    # "Python and Go" appearing twice is not evidence of a shared template.
    result = pool_duplicates({1: ["Python and Go"], 2: ["Python and Go"]})
    assert result == {}


def test_normalization_means_punctuation_and_case_do_not_hide_a_match():
    result = pool_duplicates(
        {
            1: ["Spearheaded cross-functional initiatives resulting in 40% efficiency gains"],
            2: ["spearheaded cross functional initiatives, resulting in 40% efficiency gains!"],
        }
    )
    assert 1 in result and 2 in result


def test_three_way_share_lists_every_other_candidate():
    result = pool_duplicates({1: [SHARED], 2: [SHARED], 3: [SHARED]})
    assert {d["with_candidate"] for d in result[1]} == {2, 3}


def test_candidate_repeating_own_bullet_is_not_a_pool_duplicate():
    result = pool_duplicates({1: [SHARED, SHARED]})
    assert result == {}


def test_write_and_load_roundtrip(tmp_path):
    result = pool_duplicates({1: [SHARED], 2: [SHARED]})
    path = tmp_path / "pool_duplicates.json"
    write_pool_duplicates(result, path)
    assert json.loads(path.read_text())["1"][0]["with_candidate"] == 2
    assert load_pool_duplicates(path) == result


def test_load_missing_file_returns_empty(tmp_path):
    assert load_pool_duplicates(tmp_path / "absent.json") == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pool.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'screen.pool'`

- [ ] **Step 3: Write `screen/pool.py`**

```python
"""Detect bullets shared verbatim between different applicants.

Two candidates writing the same sentence means a shared template, which is the
strongest available evidence of a CV nobody bothered to personalise. This check
can only run over the whole pool, so it re-runs every time — it costs a set
intersection and no LLM calls.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from screen.text import bullet_hash, normalize


def pool_duplicates(
    bullets_by_candidate: dict[int, list[str]], min_words: int = 6
) -> dict[int, list[dict[str, Any]]]:
    """Map candidate id to the bullets they share with other candidates.

    Bullets shorter than `min_words` are skipped: short lines like "Python and
    Go" collide innocently and would produce noise.
    """
    owners: dict[str, dict[int, str]] = defaultdict(dict)

    for cid, bullets in bullets_by_candidate.items():
        for bullet in bullets:
            if len(normalize(bullet).split()) < min_words:
                continue
            owners[bullet_hash(bullet)][cid] = bullet

    result: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for by_candidate in owners.values():
        if len(by_candidate) < 2:
            continue
        for cid, bullet in by_candidate.items():
            for other in by_candidate:
                if other != cid:
                    result[cid].append({"with_candidate": other, "bullet": bullet})

    return {cid: sorted(v, key=lambda d: d["with_candidate"]) for cid, v in result.items()}


def write_pool_duplicates(result: dict[int, list[dict[str, Any]]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({str(k): v for k, v in result.items()}, indent=2))
    tmp.replace(path)


def load_pool_duplicates(path: Path) -> dict[int, list[dict[str, Any]]]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    return {int(k): v for k, v in raw.items()}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_pool.py -v`
Expected: PASS — 8 tests

- [ ] **Step 5: Commit**

```bash
git add screen/pool.py tests/test_pool.py
git commit -m "feat: cross-pool duplicate bullet detection"
```

---

### Task 9: Precheck assembly

**Files:**
- Create: `screen/precheck.py`
- Test: `tests/test_precheck.py`

**Interfaces:**
- Consumes: `screen.config.RoleConfig`, `screen.paths.Paths`, `screen.parse.ParsedPdf`, all of `screen.signals`, `screen.redact.redact`/`assert_clean`, `screen.pool.pool_duplicates`, `screen.text.extract_bullets`.
- Produces:
  - `screen.precheck.build_precheck(candidate: dict, parsed: ParsedPdf, cfg: RoleConfig, today: tuple[int, int], pool_dups: list[dict]) -> tuple[dict, str]` — `(prechecks/<id>.json payload, redacted markdown)`. It returns both because the redacted text is a by-product of the same redaction pass whose statistics land in the payload; splitting them would redact twice.
  - `screen.precheck.minutes_before_submission(pdf_created: str, applied_iso: str) -> float | None`
  - `screen.precheck.precheck_key(pdf_sha256: str, precheck_rules_version: int) -> str`
  - `screen.precheck.run_stage(paths: Paths, cfg: RoleConfig, today: tuple[int, int], force: bool = False) -> dict` — writes `prechecks/*.json`, `redacted/*.md`, `pool_duplicates.json`; returns `{"processed": [ids], "skipped": [ids], "needs_review": {id: reason}}`

- [ ] **Step 1: Write the failing tests**

`tests/test_precheck.py`:

```python
import json
import sys
from pathlib import Path

import pytest

from screen.config import load_role
from screen.parse import parse_pdf, write_parsed
from screen.paths import Paths
from screen.precheck import (
    build_precheck,
    minutes_before_submission,
    precheck_key,
    run_stage,
)

sys.path.insert(0, str(Path(__file__).parent))
from fixtures.make_fixtures import build_all  # noqa: E402

CFG = load_role(Path(__file__).resolve().parents[1] / "roles" / "fde")
TODAY = (2026, 8)


@pytest.fixture(scope="module")
def pdfs(tmp_path_factory):
    return build_all(tmp_path_factory.mktemp("pdfs"))


CANDIDATE = {
    "id": 1,
    "first_name": "Alex",
    "last_name": "Morgan",
    "email": "alex.morgan@example.com",
    "phone": "+1 415 555 0134",
    "created_date": "2026-08-01T10:00:00Z",
    "profile_data": [{"name": "LinkedIn", "value": "https://linkedin.com/in/alexmorgan"}],
}


def test_build_precheck_on_clean_cv(pdfs):
    p, _redacted = build_precheck(CANDIDATE, parse_pdf(pdfs["clean"]), CFG, TODAY, [])
    assert p["candidate_id"] == 1
    assert p["placeholders"] == []
    assert p["hidden_text"]["found"] is False
    assert p["linkedin"]["present"] is True
    assert p["degree"]["present"] is True
    assert p["years_experience"]["computed"] > 9
    assert p["years_experience"]["confidence"] in {"high", "medium"}
    assert p["skills_count"] >= 8
    assert p["intra_cv_duplicate_bullets"] == []


def test_build_precheck_flags_placeholder_cv(pdfs):
    cand = {**CANDIDATE, "id": 2, "profile_data": []}
    p, _redacted = build_precheck(cand, parse_pdf(pdfs["placeholder"]), CFG, TODAY, [])
    assert len(p["placeholders"]) >= 2
    assert p["linkedin"]["present"] is False


def test_build_precheck_flags_hidden_text(pdfs):
    p, _redacted = build_precheck(CANDIDATE, parse_pdf(pdfs["hidden_text"]), CFG, TODAY, [])
    assert p["hidden_text"]["found"] is True


def test_build_precheck_four_year_cv_years_below_five(pdfs):
    p, _redacted = build_precheck({**CANDIDATE, "id": 3}, parse_pdf(pdfs["four_year"]), CFG, TODAY, [])
    assert 4.0 <= p["years_experience"]["computed"] < 6.5


def test_build_precheck_records_pool_duplicates(pdfs):
    dups = [{"with_candidate": 9, "bullet": "shared bullet text goes here for testing"}]
    p, _redacted = build_precheck(CANDIDATE, parse_pdf(pdfs["clean"]), CFG, TODAY, dups)
    assert p["pool_duplicate_bullets"] == dups


def test_build_precheck_includes_redaction_count_and_no_leaks(pdfs):
    p, redacted_md = build_precheck(CANDIDATE, parse_pdf(pdfs["clean"]), CFG, TODAY, [])
    assert p["redaction"]["tokens_replaced"] > 0
    assert "Alex Morgan" not in redacted_md
    assert p["redaction"]["leaks"] == []


def test_precheck_key_changes_with_rules_version():
    assert precheck_key("abc", 1) != precheck_key("abc", 2)
    assert precheck_key("abc", 1) == precheck_key("abc", 1)


def test_minutes_before_submission_computes_gap():
    # PDF D: format, applied 14 minutes later.
    assert minutes_before_submission("D:20260801094600Z", "2026-08-01T10:00:00Z") == pytest.approx(14, abs=1)


def test_minutes_before_submission_none_on_unparseable():
    assert minutes_before_submission("", "2026-08-01T10:00:00Z") is None
    assert minutes_before_submission("D:20260801094600Z", "") is None


def _seed(tmp_path, pdfs, names):
    """Lay out data/ as the fetch stage would, then return Paths."""
    paths = Paths(root=tmp_path, opening_id="704353")
    paths.ensure()
    candidates = []
    for idx, name in enumerate(names, start=1):
        pdf = paths.resumes / f"{idx}.pdf"
        pdf.write_bytes(pdfs[name].read_bytes())
        candidates.append(
            {
                "id": idx,
                "first_name": f"Cand{idx}",
                "last_name": "Test",
                "email": f"c{idx}@example.com",
                "phone": "",
                "created_date": "2026-08-01T10:00:00Z",
                "profile_data": [],
                "resume": {"file_name": f"{idx}.pdf"},
            }
        )
    paths.candidates_json.write_text(json.dumps(candidates, indent=2))
    for c in candidates:
        parsed = parse_pdf(paths.resumes / f"{c['id']}.pdf")
        write_parsed(parsed, paths.parsed / f"{c['id']}.md", paths.parsed / f"{c['id']}.meta.json")
    return paths


def test_run_stage_writes_prechecks_and_redacted(tmp_path, pdfs):
    paths = _seed(tmp_path, pdfs, ["clean", "placeholder"])
    result = run_stage(paths, CFG, TODAY)
    assert sorted(result["processed"]) == [1, 2]
    assert (paths.prechecks / "1.json").exists()
    assert (paths.redacted / "1.md").exists()
    assert "Cand1" not in (paths.redacted / "1.md").read_text()


def test_run_stage_detects_pool_duplicates_across_candidates(tmp_path, pdfs):
    paths = _seed(tmp_path, pdfs, ["template_a", "template_b"])
    run_stage(paths, CFG, TODAY)
    p1 = json.loads((paths.prechecks / "1.json").read_text())
    assert len(p1["pool_duplicate_bullets"]) >= 3
    assert p1["pool_duplicate_bullets"][0]["with_candidate"] == 2


def test_run_stage_skips_already_processed(tmp_path, pdfs):
    paths = _seed(tmp_path, pdfs, ["clean"])
    run_stage(paths, CFG, TODAY)
    second = run_stage(paths, CFG, TODAY)
    assert second["processed"] == []
    assert second["skipped"] == [1]


def test_run_stage_force_reprocesses(tmp_path, pdfs):
    paths = _seed(tmp_path, pdfs, ["clean"])
    run_stage(paths, CFG, TODAY)
    forced = run_stage(paths, CFG, TODAY, force=True)
    assert forced["processed"] == [1]


def test_run_stage_marks_scanned_pdf_needs_review(tmp_path, pdfs):
    paths = _seed(tmp_path, pdfs, ["scanned"])
    result = run_stage(paths, CFG, TODAY)
    assert result["needs_review"] == {1: "unparseable"}
    assert not (paths.prechecks / "1.json").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_precheck.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'screen.precheck'`

- [ ] **Step 3: Write `screen/precheck.py`**

```python
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

from screen import signals
from screen.config import RoleConfig
from screen.parse import ParsedPdf, parse_pdf
from screen.paths import Paths
from screen.pool import pool_duplicates, write_pool_duplicates
from screen.redact import assert_clean, redact
from screen.text import extract_bullets

_PDF_DATE_RE = re.compile(r"D:(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})")


def precheck_key(pdf_sha256: str, precheck_rules_version: int) -> str:
    return hashlib.sha256(
        f"{pdf_sha256}:{precheck_rules_version}".encode("utf-8")
    ).hexdigest()


def minutes_before_submission(pdf_created: str, applied_iso: str) -> float | None:
    """Minutes between PDF creation and the application timestamp.

    Returns None when either timestamp is missing or unparseable — the caller
    treats None as "no signal", never as zero.
    """
    m = _PDF_DATE_RE.search(pdf_created or "")
    if not m or not applied_iso:
        return None
    try:
        created = datetime(*(int(g) for g in m.groups()), tzinfo=timezone.utc)
        applied = datetime.fromisoformat(applied_iso.replace("Z", "+00:00"))
        if applied.tzinfo is None:
            applied = applied.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
    return (applied - created).total_seconds() / 60


def build_precheck(
    candidate: dict[str, Any],
    parsed: ParsedPdf,
    cfg: RoleConfig,
    today: tuple[int, int],
    pool_dups: list[dict[str, Any]],
) -> dict[str, Any]:
    md = parsed.markdown
    bullets = extract_bullets(md)
    full_name = f"{candidate.get('first_name', '')} {candidate.get('last_name', '')}".strip()
    profile_data = candidate.get("profile_data") or []

    minutes = minutes_before_submission(
        parsed.meta.get("created", ""), str(candidate.get("created_date") or "")
    )
    date_ranges = signals.extract_date_ranges(md)
    redaction = redact(md, candidate)

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
        "template_metadata_signal": signals.template_metadata_signal(
            parsed.meta, minutes, cfg
        ),
        "years_experience": signals.compute_years(md, today),
        "short_stints_last_5y": signals.short_stints(date_ranges, today, years_back=5),
        "gap_over_12m": signals.has_gap_over(
            date_ranges, today, months=12, years_back=6
        ),
        "linkedin": signals.find_linkedin(md, profile_data, full_name),
        "degree": signals.find_degree(md),
        "location": signals.find_location(md, profile_data),
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


def run_stage(
    paths: Paths, cfg: RoleConfig, today: tuple[int, int], force: bool = False
) -> dict[str, Any]:
    """Precheck every candidate whose PDF or rules version changed.

    Pool duplicates are recomputed over the whole pool every run, because a new
    arrival can reveal that an already-processed CV used the same template.
    """
    candidates = json.loads(paths.candidates_json.read_text())
    parsed_by_id: dict[int, ParsedPdf] = {}
    needs_review: dict[int, str] = {}
    min_chars = int(cfg.gates["min_text_chars"])

    for c in candidates:
        cid = int(c["id"])
        pdf = paths.resumes / f"{cid}.pdf"
        if not pdf.exists():
            needs_review[cid] = "missing_resume"
            continue
        try:
            parsed = parse_pdf(pdf)
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

        if not force and out_path.exists():
            try:
                existing = json.loads(out_path.read_text())
            except json.JSONDecodeError:
                existing = {}
            # Re-run when the PDF or the rules changed, or when the pool pass
            # discovered duplicates the stored file does not know about.
            same_key = existing.get("precheck_key") == want_key
            same_dups = existing.get("pool_duplicate_bullets", []) == dups.get(cid, [])
            if same_key and same_dups:
                skipped.append(cid)
                continue

        payload, redacted_md = build_precheck(c, parsed, cfg, today, dups.get(cid, []))
        _write_json(out_path, payload)
        _write_text(paths.redacted / f"{cid}.md", redacted_md)
        processed.append(cid)

    return {
        "processed": sorted(processed),
        "skipped": sorted(skipped),
        "needs_review": needs_review,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_precheck.py -v`
Expected: PASS — 16 tests

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: PASS — everything from Tasks 1–9

- [ ] **Step 6: Commit**

```bash
git add screen/precheck.py tests/test_precheck.py
git commit -m "feat: precheck stage assembling deterministic signals and redacted text"
```

---

### Task 10: Fetch stage — Trakstar client and folder source

**Files:**
- Create: `screen/fetch.py`
- Test: `tests/test_fetch.py`

**Interfaces:**
- Consumes: `screen.paths.Paths`.
- Produces:
  - `screen.fetch.Throttle(max_requests: int, window_seconds: float, sleep=time.sleep)` with `wait() -> None`
  - `screen.fetch.TrakstarClient(api_key: str, base_url: str = "https://api.recruiterbox.com/v2/", client: httpx.Client | None = None, throttle: Throttle | None = None)` with `list_candidates(opening_id: str) -> list[dict]` and `download_resume(url: str, dest: Path) -> None`
  - `screen.fetch.fetch_trakstar(paths: Paths, client: TrakstarClient, opening_id: str) -> dict` — `{"new": [...], "updated": [...], "unchanged": [...], "download_failed": {...}}`
  - `screen.fetch.fetch_folder(paths: Paths, folder: Path, csv_path: Path | None = None) -> dict` — same shape
- Third-party: `httpx` (already a dependency). Tests use `httpx.MockTransport`, never real network.

- [ ] **Step 1: Write the failing tests**

`tests/test_fetch.py`:

```python
import json
from pathlib import Path

import httpx
import pytest

from screen.fetch import Throttle, TrakstarClient, fetch_folder, fetch_trakstar
from screen.paths import Paths

OPENING = "704353"


def _candidate(cid: int, updated: str = "2026-08-01T10:00:00Z") -> dict:
    return {
        "id": cid,
        "first_name": f"Cand{cid}",
        "last_name": "Test",
        "email": f"c{cid}@example.com",
        "phone": "",
        "created_date": "2026-08-01T09:00:00Z",
        "updated_date": updated,
        "stage_name": "Applied",
        "state": "in_process",
        "source": "Careers page",
        "profile_data": [],
        "resume": {"file_name": f"cv{cid}.pdf", "file_url": f"https://files.test/cv{cid}.pdf"},
    }


def _transport(candidates, pdf_bytes=b"%PDF-1.4 fake", fail_urls=()):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/candidates" in url:
            offset = int(request.url.params.get("offset", 0))
            limit = int(request.url.params.get("limit", 250))
            page = candidates[offset : offset + limit]
            return httpx.Response(
                200,
                json={
                    "meta": {"total_count": len(candidates), "offset": offset, "limit": limit},
                    "objects": page,
                },
            )
        if url in fail_urls:
            return httpx.Response(404)
        return httpx.Response(200, content=pdf_bytes)

    return httpx.MockTransport(handler)


def _client(candidates, **kw):
    transport = _transport(candidates, **kw)
    return TrakstarClient(
        api_key="test-key",
        client=httpx.Client(transport=transport),
        throttle=Throttle(max_requests=90, window_seconds=300, sleep=lambda s: None),
    )


def test_list_candidates_paginates():
    cands = [_candidate(i) for i in range(1, 601)]
    got = _client(cands).list_candidates(OPENING)
    assert len(got) == 600
    assert got[0]["id"] == 1
    assert got[-1]["id"] == 600


def test_auth_uses_api_key_as_basic_username():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"meta": {"total_count": 0}, "objects": []})

    c = TrakstarClient(api_key="secret", client=httpx.Client(transport=httpx.MockTransport(handler)))
    c.list_candidates(OPENING)

    import base64

    expected = "Basic " + base64.b64encode(b"secret:").decode()
    assert seen["auth"] == expected


def test_retries_on_429_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"meta": {"total_count": 0}, "objects": []})

    c = TrakstarClient(
        api_key="k",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        throttle=Throttle(90, 300, sleep=lambda s: None),
        sleep=lambda s: None,
    )
    assert c.list_candidates(OPENING) == []
    assert calls["n"] == 2


def test_raises_on_401():
    def handler(request):
        return httpx.Response(401, json={"detail": "invalid key"})

    c = TrakstarClient(api_key="bad", client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(PermissionError, match="TRAKSTAR_API_KEY"):
        c.list_candidates(OPENING)


def test_fetch_trakstar_downloads_new_candidates(tmp_path):
    paths = Paths(root=tmp_path, opening_id=OPENING)
    paths.ensure()
    result = fetch_trakstar(paths, _client([_candidate(1), _candidate(2)]), OPENING)
    assert sorted(result["new"]) == [1, 2]
    assert (paths.resumes / "1.pdf").read_bytes().startswith(b"%PDF")
    saved = json.loads(paths.candidates_json.read_text())
    assert {c["id"] for c in saved} == {1, 2}


def test_fetch_trakstar_skips_unchanged_on_second_run(tmp_path):
    paths = Paths(root=tmp_path, opening_id=OPENING)
    paths.ensure()
    cands = [_candidate(1)]
    fetch_trakstar(paths, _client(cands), OPENING)
    second = fetch_trakstar(paths, _client(cands), OPENING)
    assert second["new"] == []
    assert second["unchanged"] == [1]


def test_fetch_trakstar_redownloads_when_updated_date_changes(tmp_path):
    paths = Paths(root=tmp_path, opening_id=OPENING)
    paths.ensure()
    fetch_trakstar(paths, _client([_candidate(1, updated="2026-08-01T10:00:00Z")]), OPENING)
    second = fetch_trakstar(
        paths, _client([_candidate(1, updated="2026-08-05T11:00:00Z")]), OPENING
    )
    assert second["updated"] == [1]


def test_fetch_trakstar_records_download_failure(tmp_path):
    paths = Paths(root=tmp_path, opening_id=OPENING)
    paths.ensure()
    client = _client([_candidate(1)], fail_urls=("https://files.test/cv1.pdf",))
    result = fetch_trakstar(paths, client, OPENING)
    assert 1 in result["download_failed"]
    assert not (paths.resumes / "1.pdf").exists()


def test_fetch_trakstar_handles_missing_resume_url(tmp_path):
    paths = Paths(root=tmp_path, opening_id=OPENING)
    paths.ensure()
    cand = _candidate(1)
    cand["resume"] = {}
    result = fetch_trakstar(paths, _client([cand]), OPENING)
    assert result["download_failed"][1] == "no_resume_url"


def test_fetch_folder_builds_synthetic_candidates(tmp_path):
    src = tmp_path / "cvs"
    src.mkdir()
    (src / "Alex Morgan.pdf").write_bytes(b"%PDF-1.4 a")
    (src / "Riley Chen.pdf").write_bytes(b"%PDF-1.4 b")

    paths = Paths(root=tmp_path / "proj", opening_id=OPENING)
    paths.ensure()
    result = fetch_folder(paths, src)

    assert len(result["new"]) == 2
    saved = json.loads(paths.candidates_json.read_text())
    names = sorted(f"{c['first_name']} {c['last_name']}" for c in saved)
    assert names == ["Alex Morgan", "Riley Chen"]
    for c in saved:
        assert (paths.resumes / f"{c['id']}.pdf").exists()


def test_fetch_folder_ids_are_stable_across_runs(tmp_path):
    src = tmp_path / "cvs"
    src.mkdir()
    (src / "Alex Morgan.pdf").write_bytes(b"%PDF-1.4 a")

    paths = Paths(root=tmp_path / "proj", opening_id=OPENING)
    paths.ensure()
    first = json.loads(fetch_folder(paths, src) and paths.candidates_json.read_text())
    second_result = fetch_folder(paths, src)
    second = json.loads(paths.candidates_json.read_text())
    assert first[0]["id"] == second[0]["id"]
    assert second_result["unchanged"] == [first[0]["id"]]


def test_fetch_folder_reads_linkedin_from_csv(tmp_path):
    src = tmp_path / "cvs"
    src.mkdir()
    (src / "Alex Morgan.pdf").write_bytes(b"%PDF-1.4 a")
    csv_path = tmp_path / "export.csv"
    csv_path.write_text(
        "Name,Email,LinkedIn,Location\n"
        "Alex Morgan,alex@example.com,https://linkedin.com/in/alexmorgan,\"Boston, MA\"\n"
    )

    paths = Paths(root=tmp_path / "proj", opening_id=OPENING)
    paths.ensure()
    fetch_folder(paths, src, csv_path=csv_path)
    saved = json.loads(paths.candidates_json.read_text())
    pd = {item["name"]: item["value"] for item in saved[0]["profile_data"]}
    assert pd["LinkedIn"] == "https://linkedin.com/in/alexmorgan"
    assert pd["Location"] == "Boston, MA"
    assert saved[0]["email"] == "alex@example.com"


def test_throttle_sleeps_once_the_window_is_full():
    slept = []
    clock = {"t": 0.0}
    t = Throttle(max_requests=2, window_seconds=10, sleep=slept.append, now=lambda: clock["t"])
    t.wait()
    t.wait()
    t.wait()
    assert slept and slept[0] > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_fetch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'screen.fetch'`

- [ ] **Step 3: Write `screen/fetch.py`**

```python
"""Pull candidates and CV files from Trakstar Hire, or from a local folder.

Trakstar allows 100 requests per 5 minutes per IP, so the client self-throttles
below that and backs off on 429. Nothing here judges anything; it only fills the
cache that later stages read.
"""

from __future__ import annotations

import csv
import hashlib
import json
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

import httpx

BASE_URL = "https://api.recruiterbox.com/v2/"
PAGE_LIMIT = 250
MAX_RETRIES = 5


class Throttle:
    """Allow at most `max_requests` in any `window_seconds` sliding window."""

    def __init__(
        self,
        max_requests: int = 90,
        window_seconds: float = 300.0,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._sleep = sleep
        self._now = now
        self._stamps: deque[float] = deque()

    def wait(self) -> None:
        now = self._now()
        while self._stamps and now - self._stamps[0] >= self.window_seconds:
            self._stamps.popleft()
        if len(self._stamps) >= self.max_requests:
            delay = self.window_seconds - (now - self._stamps[0]) + 0.1
            if delay > 0:
                self._sleep(delay)
        self._stamps.append(self._now())


class TrakstarClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = BASE_URL,
        client: httpx.Client | None = None,
        throttle: Throttle | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise PermissionError("TRAKSTAR_API_KEY is empty — set it in .env")
        self.base_url = base_url.rstrip("/") + "/"
        self._client = client or httpx.Client(timeout=60.0, follow_redirects=True)
        self._auth = (api_key, "")
        self._throttle = throttle or Throttle()
        self._sleep = sleep

    def _get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        for attempt in range(MAX_RETRIES):
            self._throttle.wait()
            response = self._client.get(url, params=params, auth=self._auth)
            if response.status_code == 401:
                raise PermissionError(
                    "Trakstar returned 401 — check TRAKSTAR_API_KEY (needs Super Admin)"
                )
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == MAX_RETRIES - 1:
                    response.raise_for_status()
                retry_after = float(response.headers.get("Retry-After", 2**attempt))
                self._sleep(retry_after)
                continue
            response.raise_for_status()
            return response
        raise RuntimeError("unreachable")

    def list_candidates(self, opening_id: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            payload = self._get(
                f"{self.base_url}candidates",
                params={"opening_id": opening_id, "limit": PAGE_LIMIT, "offset": offset},
            ).json()
            page = payload.get("objects") or payload.get("results") or []
            out.extend(page)
            total = int(payload.get("meta", {}).get("total_count", len(out)))
            offset += PAGE_LIMIT
            if offset >= total or not page:
                break
        return out

    def download_resume(self, url: str, dest: Path) -> None:
        response = self._get(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_bytes(response.content)
        tmp.replace(dest)


def _load_cached(paths) -> dict[int, dict[str, Any]]:
    if not paths.candidates_json.exists():
        return {}
    try:
        return {int(c["id"]): c for c in json.loads(paths.candidates_json.read_text())}
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}


def _save_candidates(paths, candidates: list[dict[str, Any]]) -> None:
    tmp = paths.candidates_json.with_suffix(".json.tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(candidates, indent=2))
    tmp.replace(paths.candidates_json)


def fetch_trakstar(paths, client: TrakstarClient, opening_id: str) -> dict[str, Any]:
    """Sync the local cache with Trakstar, downloading only what changed."""
    remote = client.list_candidates(opening_id)
    cached = _load_cached(paths)

    new: list[int] = []
    updated: list[int] = []
    unchanged: list[int] = []
    download_failed: dict[int, str] = {}

    for candidate in remote:
        cid = int(candidate["id"])
        pdf = paths.resumes / f"{cid}.pdf"
        previous = cached.get(cid)
        changed = (
            previous is None
            or previous.get("updated_date") != candidate.get("updated_date")
            or not pdf.exists()
        )

        if not changed:
            unchanged.append(cid)
            continue

        url = (candidate.get("resume") or {}).get("file_url")
        if not url:
            download_failed[cid] = "no_resume_url"
        else:
            try:
                client.download_resume(url, pdf)
            except (httpx.HTTPError, OSError) as exc:
                download_failed[cid] = f"{type(exc).__name__}: {exc}"

        (new if previous is None else updated).append(cid)

    _save_candidates(paths, remote)
    return {
        "new": sorted(new),
        "updated": sorted(updated),
        "unchanged": sorted(unchanged),
        "download_failed": download_failed,
    }


def _stable_id(name: str) -> int:
    """Deterministic positive id for a folder-sourced CV, stable across runs."""
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    return int(digest, 16) % 900_000_000 + 1_000_000


def _read_csv_rows(csv_path: Path) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    with csv_path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            key = (row.get("Name") or row.get("name") or "").strip().lower()
            if key:
                rows[key] = row
    return rows


def fetch_folder(paths, folder: Path, csv_path: Path | None = None) -> dict[str, Any]:
    """Build the same cache from a folder of PDFs plus an optional CSV export.

    Used for development before the API key lands, and for offline testing. File
    stem becomes the candidate name; ids are hashes of the stem so they survive
    re-runs.
    """
    cached = _load_cached(paths)
    csv_rows = _read_csv_rows(csv_path) if csv_path and csv_path.exists() else {}

    candidates: list[dict[str, Any]] = []
    new: list[int] = []
    unchanged: list[int] = []

    for pdf in sorted(folder.glob("*.pdf")):
        name = pdf.stem.strip()
        cid = _stable_id(name)
        first, _, last = name.partition(" ")
        row = csv_rows.get(name.lower(), {})

        profile_data = [
            {"name": key, "value": row[key].strip()}
            for key in ("LinkedIn", "Location")
            if row.get(key, "").strip()
        ]

        dest = paths.resumes / f"{cid}.pdf"
        payload = pdf.read_bytes()
        is_new = cid not in cached or not dest.exists()
        if is_new or dest.read_bytes() != payload:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(".pdf.tmp")
            tmp.write_bytes(payload)
            tmp.replace(dest)

        digest = hashlib.sha256(payload).hexdigest()
        candidates.append(
            {
                "id": cid,
                "first_name": first or name,
                "last_name": last or "",
                "email": (row.get("Email") or row.get("email") or "").strip(),
                "phone": (row.get("Phone") or "").strip(),
                "created_date": (row.get("Applied") or "").strip(),
                "updated_date": digest,
                "stage_name": (row.get("Stage") or "Applied").strip(),
                "state": "in_process",
                "source": "folder",
                "profile_data": profile_data,
                "resume": {"file_name": pdf.name, "file_url": None},
            }
        )

        if cid in cached and cached[cid].get("updated_date") == digest:
            unchanged.append(cid)
        else:
            new.append(cid)

    _save_candidates(paths, candidates)
    return {
        "new": sorted(new),
        "updated": [],
        "unchanged": sorted(unchanged),
        "download_failed": {},
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_fetch.py -v`
Expected: PASS — 13 tests

- [ ] **Step 5: Commit**

```bash
git add screen/fetch.py tests/test_fetch.py
git commit -m "feat: Trakstar client and local-folder fetch with throttling and caching"
```

---

### Task 11: Verdict validation and quote verification

**Files:**
- Create: `screen/verdict.py`
- Test: `tests/test_verdict.py`

**Interfaces:**
- Consumes: `screen.config.RoleConfig`, `screen.text.contains_quote`.
- Produces:
  - `screen.verdict.VerdictError(Exception)` with `.errors: list[str]`
  - `screen.verdict.validate_verdict(raw: dict, cfg: RoleConfig, redacted_md: str) -> dict` — returns the normalized verdict; raises `VerdictError` listing every problem so the retry prompt can quote them
  - `screen.verdict.verdict_key(redacted_md: str, precheck: dict, rubric_version: int) -> str`
  - `screen.verdict.fit_total(verdict: dict) -> float`
  - `screen.verdict.load_verdict(path: Path) -> dict | None`
  - `screen.verdict.write_verdict(verdict: dict, path: Path) -> None`

`validate_verdict` adds a `quote_warnings: list[str]` field: criteria whose quote is not in the redacted markdown have their score zeroed and are listed there, per the spec's "no score without a quote" rule.

- [ ] **Step 1: Write the failing tests**

`tests/test_verdict.py`:

```python
from pathlib import Path

import pytest

from screen.config import load_role
from screen.verdict import (
    VerdictError,
    fit_total,
    validate_verdict,
    verdict_key,
    write_verdict,
    load_verdict,
)

CFG = load_role(Path(__file__).resolve().parents[1] / "roles" / "fde")

MD = """## Experience
### Staff Engineer, Northwind Data (2019-03 - Present)
- Owned the client integration platform end to end: 40 enterprise tenants.
- Led discovery workshops with law firms to scope data migrations.
- Rolled out Okta and Azure AD SAML SSO for 18 clients.
- Built internal LLM tooling that drafts integration mappings.
"""


def _raw(**overrides):
    scores = {
        "production_ownership": {"score": 20, "quote": "Owned the client integration platform end to end", "rationale": "r"},
        "integration_breadth": {"score": 15, "quote": "Rolled out Okta and Azure AD SAML SSO for 18 clients", "rationale": "r"},
        "client_solutioning": {"score": 16, "quote": "Led discovery workshops with law firms", "rationale": "r"},
        "communication_product": {"score": 10, "quote": "scope data migrations", "rationale": "r"},
        "domain": {"score": 5, "quote": "law firms", "rationale": "r"},
        "ai_tooling": {"score": 4, "quote": "Built internal LLM tooling", "rationale": "r"},
        "distributed_collab": {"score": 0, "quote": None, "rationale": "no evidence"},
    }
    raw = {
        "candidate_id": 1,
        "redflag": {"flags": [], "years_experience_estimate": {"value": 7, "confidence": "high"}},
        "fit": {"scores": scores, "bonus": {"points": 0, "justification": None}, "summary": "s"},
    }
    raw.update(overrides)
    return raw


def test_valid_verdict_passes_and_totals():
    v = validate_verdict(_raw(), CFG, MD)
    assert fit_total(v) == 70
    assert v["quote_warnings"] == []


def test_missing_criterion_is_an_error():
    raw = _raw()
    del raw["fit"]["scores"]["domain"]
    with pytest.raises(VerdictError) as exc:
        validate_verdict(raw, CFG, MD)
    assert any("domain" in e for e in exc.value.errors)


def test_unknown_criterion_is_an_error():
    raw = _raw()
    raw["fit"]["scores"]["vibes"] = {"score": 5, "quote": "x", "rationale": "r"}
    with pytest.raises(VerdictError) as exc:
        validate_verdict(raw, CFG, MD)
    assert any("vibes" in e for e in exc.value.errors)


def test_score_above_max_is_an_error():
    raw = _raw()
    raw["fit"]["scores"]["domain"]["score"] = 99
    with pytest.raises(VerdictError) as exc:
        validate_verdict(raw, CFG, MD)
    assert any("domain" in e and "max" in e for e in exc.value.errors)


def test_negative_score_is_an_error():
    raw = _raw()
    raw["fit"]["scores"]["domain"]["score"] = -1
    with pytest.raises(VerdictError):
        validate_verdict(raw, CFG, MD)


def test_bonus_above_cap_is_an_error():
    raw = _raw()
    raw["fit"]["bonus"] = {"points": 50, "justification": "wow"}
    with pytest.raises(VerdictError) as exc:
        validate_verdict(raw, CFG, MD)
    assert any("bonus" in e for e in exc.value.errors)


def test_bonus_without_justification_is_an_error():
    raw = _raw()
    raw["fit"]["bonus"] = {"points": 3, "justification": ""}
    with pytest.raises(VerdictError) as exc:
        validate_verdict(raw, CFG, MD)
    assert any("justification" in e for e in exc.value.errors)


def test_fabricated_quote_zeroes_score_and_warns():
    raw = _raw()
    raw["fit"]["scores"]["domain"]["quote"] = "Directed the Mars colonisation programme"
    v = validate_verdict(raw, CFG, MD)
    assert v["fit"]["scores"]["domain"]["score"] == 0
    assert any("domain" in w for w in v["quote_warnings"])
    assert fit_total(v) == 65


def test_nonzero_score_without_quote_zeroes_and_warns():
    raw = _raw()
    raw["fit"]["scores"]["domain"] = {"score": 5, "quote": None, "rationale": "r"}
    v = validate_verdict(raw, CFG, MD)
    assert v["fit"]["scores"]["domain"]["score"] == 0
    assert any("domain" in w for w in v["quote_warnings"])


def test_zero_score_without_quote_is_fine():
    v = validate_verdict(_raw(), CFG, MD)
    assert v["fit"]["scores"]["distributed_collab"]["score"] == 0
    assert not any("distributed_collab" in w for w in v["quote_warnings"])


def test_flag_without_quote_is_an_error():
    raw = _raw()
    raw["redflag"]["flags"] = [{"tier": 1, "kind": "placeholder", "explanation": "e"}]
    with pytest.raises(VerdictError) as exc:
        validate_verdict(raw, CFG, MD)
    assert any("quote" in e for e in exc.value.errors)


def test_flag_with_bad_tier_is_an_error():
    raw = _raw()
    raw["redflag"]["flags"] = [{"tier": 7, "kind": "x", "quote": "Owned the client", "explanation": "e"}]
    with pytest.raises(VerdictError) as exc:
        validate_verdict(raw, CFG, MD)
    assert any("tier" in e for e in exc.value.errors)


def test_flag_quote_not_in_cv_becomes_warning_not_error():
    raw = _raw()
    raw["redflag"]["flags"] = [
        {"tier": 2, "kind": "generic_bullets", "quote": "never appeared anywhere", "explanation": "e"}
    ]
    v = validate_verdict(raw, CFG, MD)
    assert v["redflag"]["flags"] == []
    assert any("flag" in w for w in v["quote_warnings"])


def test_missing_top_level_sections_is_an_error():
    with pytest.raises(VerdictError) as exc:
        validate_verdict({"candidate_id": 1}, CFG, MD)
    assert len(exc.value.errors) >= 2


def test_verdict_key_depends_on_all_three_inputs():
    pc = {"precheck_key": "abc"}
    base = verdict_key(MD, pc, 1)
    assert base == verdict_key(MD, pc, 1)
    assert base != verdict_key(MD + " changed", pc, 1)
    assert base != verdict_key(MD, {"precheck_key": "xyz"}, 1)
    assert base != verdict_key(MD, pc, 2)


def test_write_and_load_verdict_roundtrip(tmp_path):
    v = validate_verdict(_raw(), CFG, MD)
    path = tmp_path / "1.json"
    write_verdict(v, path)
    assert load_verdict(path)["candidate_id"] == 1
    assert load_verdict(tmp_path / "absent.json") is None


def test_load_verdict_returns_none_on_corrupt_file(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json")
    assert load_verdict(path) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_verdict.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'screen.verdict'`

- [ ] **Step 3: Write `screen/verdict.py`**

```python
"""Validate the JSON a judge subagent returns.

The judge is a language model, so its output is untrusted input. Two rules do
the heavy lifting: every score must be inside its criterion's range, and every
non-zero score must quote text that actually appears in the CV. A quote the
judge invented zeroes that criterion rather than failing the whole candidate —
losing one criterion is recoverable, discarding a real applicant is not.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from screen.config import RoleConfig
from screen.text import contains_quote

VALID_TIERS = (1, 2, 3)


class VerdictError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def verdict_key(redacted_md: str, precheck: dict[str, Any], rubric_version: int) -> str:
    material = f"{redacted_md}\n{precheck.get('precheck_key', '')}\n{rubric_version}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def validate_verdict(raw: dict[str, Any], cfg: RoleConfig, redacted_md: str) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(raw, dict):
        raise VerdictError(["verdict is not a JSON object"])

    if "redflag" not in raw or not isinstance(raw.get("redflag"), dict):
        errors.append("missing 'redflag' object")
    if "fit" not in raw or not isinstance(raw.get("fit"), dict):
        errors.append("missing 'fit' object")
    if errors:
        raise VerdictError(errors)

    fit = raw["fit"]
    redflag = raw["redflag"]
    scores_in = fit.get("scores")
    if not isinstance(scores_in, dict):
        raise VerdictError(["'fit.scores' must be an object keyed by criterion"])

    expected = cfg.criterion_keys()
    for key in expected:
        if key not in scores_in:
            errors.append(f"missing score for criterion '{key}'")
    for key in scores_in:
        if key not in expected:
            errors.append(f"unknown criterion '{key}'")

    clean_scores: dict[str, Any] = {}
    for key in expected:
        entry = scores_in.get(key)
        if not isinstance(entry, dict):
            continue
        try:
            score = float(entry.get("score"))
        except (TypeError, ValueError):
            errors.append(f"criterion '{key}' has a non-numeric score")
            continue

        maximum = cfg.criterion(key).max
        if score < 0:
            errors.append(f"criterion '{key}' score {score} is negative")
            continue
        if score > maximum:
            errors.append(f"criterion '{key}' score {score} exceeds max {maximum}")
            continue

        quote = entry.get("quote")
        if score > 0:
            if not quote or not str(quote).strip():
                warnings.append(f"criterion '{key}': score {score} had no quote — zeroed")
                score = 0.0
            elif not contains_quote(redacted_md, str(quote)):
                warnings.append(
                    f"criterion '{key}': quote not found in CV — zeroed (quote: {str(quote)[:60]!r})"
                )
                score = 0.0

        clean_scores[key] = {
            "score": score,
            "quote": entry.get("quote"),
            "rationale": str(entry.get("rationale") or "").strip(),
        }

    bonus_in = fit.get("bonus") or {"points": 0, "justification": None}
    try:
        bonus_points = float(bonus_in.get("points") or 0)
    except (TypeError, ValueError):
        errors.append("bonus.points is not numeric")
        bonus_points = 0.0
    justification = str(bonus_in.get("justification") or "").strip()
    if bonus_points < 0:
        errors.append("bonus.points is negative")
    if bonus_points > cfg.bonus_max:
        errors.append(f"bonus {bonus_points} exceeds bonus_max {cfg.bonus_max}")
    if bonus_points > 0 and not justification:
        errors.append("bonus awarded without justification")

    clean_flags: list[dict[str, Any]] = []
    for i, flag in enumerate(redflag.get("flags") or []):
        if not isinstance(flag, dict):
            errors.append(f"flag {i} is not an object")
            continue
        try:
            tier = int(flag.get("tier"))
        except (TypeError, ValueError):
            errors.append(f"flag {i} has a non-numeric tier")
            continue
        if tier not in VALID_TIERS:
            errors.append(f"flag {i} has invalid tier {tier} (expected 1, 2, or 3)")
            continue
        quote = str(flag.get("quote") or "").strip()
        if not quote:
            errors.append(f"flag {i} ('{flag.get('kind')}') has no quote")
            continue
        if not contains_quote(redacted_md, quote):
            warnings.append(
                f"flag '{flag.get('kind')}': quote not found in CV — dropped (quote: {quote[:60]!r})"
            )
            continue
        clean_flags.append(
            {
                "tier": tier,
                "kind": str(flag.get("kind") or "unspecified"),
                "quote": quote,
                "explanation": str(flag.get("explanation") or "").strip(),
            }
        )

    if errors:
        raise VerdictError(errors)

    estimate = redflag.get("years_experience_estimate") or {}
    try:
        est_value = float(estimate.get("value")) if estimate.get("value") is not None else None
    except (TypeError, ValueError):
        est_value = None

    return {
        "candidate_id": int(raw.get("candidate_id") or 0),
        "rubric_version": cfg.rubric_version,
        "redflag": {
            "flags": clean_flags,
            "years_experience_estimate": {
                "value": est_value,
                "confidence": str(estimate.get("confidence") or "low"),
            },
        },
        "fit": {
            "scores": clean_scores,
            "bonus": {"points": bonus_points, "justification": justification or None},
            "summary": str(fit.get("summary") or "").strip(),
        },
        "quote_warnings": warnings,
    }


def fit_total(verdict: dict[str, Any]) -> float:
    return round(
        sum(entry["score"] for entry in verdict["fit"]["scores"].values()), 2
    )


def write_verdict(verdict: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(verdict, indent=2))
    tmp.replace(path)


def load_verdict(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_verdict.py -v`
Expected: PASS — 17 tests

- [ ] **Step 5: Commit**

```bash
git add screen/verdict.py tests/test_verdict.py
git commit -m "feat: verdict schema validation with quote verification"
```

---

### Task 12: Gates, penalties, and the final score

**Files:**
- Create: `screen/rank.py` (gates and scoring only; the cap and ledger arrive in Task 13)
- Test: `tests/test_rank_gates.py`

**Interfaces:**
- Consumes: `screen.config.RoleConfig`, `screen.verdict.fit_total`.
- Produces:
  - `screen.rank.Assessment` — frozen dataclass `candidate_id: int`, `gate: str | None`, `gate_reasons: list[str]`, `fit: float`, `bonus: float`, `penalties: list[dict]`, `penalty_total: float`, `final: float`, `flags: list[str]`, `tier2_count: float`, `timezone_hint: str`
  - `screen.rank.tier2_count(precheck: dict, verdict: dict, cfg: RoleConfig) -> float`
  - `screen.rank.apply_gates(precheck: dict, verdict: dict, cfg: RoleConfig) -> tuple[str | None, list[str]]`
  - `screen.rank.compute_penalties(precheck: dict, verdict: dict, cfg: RoleConfig) -> list[dict]` — each `{"kind": str, "points": int, "detail": str}`
  - `screen.rank.assess(precheck: dict, verdict: dict, cfg: RoleConfig) -> Assessment`

- [ ] **Step 1: Write the failing tests**

`tests/test_rank_gates.py`:

```python
from pathlib import Path

from screen.config import load_role
from screen.rank import apply_gates, assess, compute_penalties, tier2_count

CFG = load_role(Path(__file__).resolve().parents[1] / "roles" / "fde")


def precheck(**over):
    base = {
        "candidate_id": 1,
        "placeholders": [],
        "hidden_text": {"found": False, "spans": []},
        "intra_cv_duplicate_bullets": [],
        "pool_duplicate_bullets": [],
        "skills_count": 20,
        "template_metadata_signal": False,
        "years_experience": {"computed": 8.0, "confidence": "high", "ranges": []},
        "short_stints_last_5y": 0,
        "gap_over_12m": False,
        "linkedin": {"present": True, "source": "trakstar", "url": "u", "name_matches": True},
        "degree": {"present": True, "level": "BSc", "field": "Computer Science"},
        "location": {"us_evident": True, "non_us_explicit": False, "timezone_hint": "ET", "raw": None},
    }
    base.update(over)
    return base


def verdict(flags=(), scores=None, bonus=0.0, est=None):
    keys = CFG.criterion_keys()
    scores = scores or {k: 10 if CFG.criterion(k).max >= 10 else 2 for k in keys}
    return {
        "candidate_id": 1,
        "redflag": {
            "flags": list(flags),
            "years_experience_estimate": est or {"value": 8, "confidence": "high"},
        },
        "fit": {
            "scores": {k: {"score": float(scores[k]), "quote": "q", "rationale": "r"} for k in keys},
            "bonus": {"points": bonus, "justification": "j" if bonus else None},
            "summary": "s",
        },
        "quote_warnings": [],
    }


# --- Gates ------------------------------------------------------------------

def test_no_gate_for_clean_candidate():
    gate, reasons = apply_gates(precheck(), verdict(), CFG)
    assert gate is None
    assert reasons == []


def test_g1_precheck_placeholder():
    gate, reasons = apply_gates(
        precheck(placeholders=[{"kind": "placeholder", "match": "[Company Name]"}]), verdict(), CFG
    )
    assert gate == "G1"
    assert any("[Company Name]" in r for r in reasons)


def test_g1_judge_tier1_flag():
    flags = [{"tier": 1, "kind": "wrong_company", "quote": "join Palantir", "explanation": "e"}]
    gate, reasons = apply_gates(precheck(), verdict(flags=flags), CFG)
    assert gate == "G1"
    assert any("wrong_company" in r for r in reasons)


def test_g1_intra_cv_duplicates():
    dups = [{"a": "x", "b": "y", "similarity": 0.9}]
    gate, _ = apply_gates(precheck(intra_cv_duplicate_bullets=dups), verdict(), CFG)
    assert gate == "G1"


def test_g1_many_pool_duplicates():
    dups = [{"with_candidate": 2, "bullet": f"b{i}"} for i in range(4)]
    gate, _ = apply_gates(precheck(pool_duplicate_bullets=dups), verdict(), CFG)
    assert gate == "G1"


def test_few_pool_duplicates_do_not_gate():
    dups = [{"with_candidate": 2, "bullet": "b1"}]
    gate, _ = apply_gates(precheck(pool_duplicate_bullets=dups), verdict(), CFG)
    assert gate is None


def test_g2_three_tier2_flags():
    flags = [
        {"tier": 2, "kind": f"k{i}", "quote": "q", "explanation": "e"} for i in range(3)
    ]
    gate, reasons = apply_gates(precheck(), verdict(flags=flags), CFG)
    assert gate == "G2"
    assert any("3" in r for r in reasons)


def test_g2_not_triggered_by_two_tier2_flags():
    flags = [{"tier": 2, "kind": f"k{i}", "quote": "q", "explanation": "e"} for i in range(2)]
    gate, _ = apply_gates(precheck(), verdict(flags=flags), CFG)
    assert gate is None


def test_metadata_signal_counts_half_and_cannot_gate_alone():
    # Two judge Tier-2 flags plus metadata = 2.5, still below 3.
    flags = [{"tier": 2, "kind": f"k{i}", "quote": "q", "explanation": "e"} for i in range(2)]
    pc = precheck(template_metadata_signal=True)
    assert tier2_count(pc, verdict(flags=flags), CFG) == 2.5
    gate, _ = apply_gates(pc, verdict(flags=flags), CFG)
    assert gate is None


def test_skills_count_contributes_a_tier2_signal():
    pc = precheck(skills_count=45)
    assert tier2_count(pc, verdict(), CFG) >= 1


def test_g3_hidden_text():
    pc = precheck(hidden_text={"found": True, "spans": [{"kind": "white_on_white", "text": "x"}]})
    gate, reasons = apply_gates(pc, verdict(), CFG)
    assert gate == "G3"
    assert any("white_on_white" in r for r in reasons)


def test_g4_years_below_minimum_with_high_confidence():
    pc = precheck(years_experience={"computed": 2.0, "confidence": "high", "ranges": []})
    gate, _ = apply_gates(pc, verdict(est={"value": 2, "confidence": "high"}), CFG)
    assert gate == "G4"


def test_g4_uses_judge_estimate_when_precheck_confidence_low():
    pc = precheck(years_experience={"computed": 0.0, "confidence": "low", "ranges": []})
    # Judge says 9 years, so no gate despite the precheck computing zero.
    gate, _ = apply_gates(pc, verdict(est={"value": 9, "confidence": "high"}), CFG)
    assert gate is None


def test_g4_gates_when_both_sources_are_low():
    pc = precheck(years_experience={"computed": 0.0, "confidence": "low", "ranges": []})
    gate, _ = apply_gates(pc, verdict(est={"value": 1, "confidence": "medium"}), CFG)
    assert gate == "G4"


def test_g5_non_us_explicit():
    pc = precheck(location={"us_evident": False, "non_us_explicit": True, "timezone_hint": "unknown", "raw": "Hanoi, Vietnam"})
    gate, _ = apply_gates(pc, verdict(), CFG)
    assert gate == "G5"


def test_unknown_location_does_not_gate():
    pc = precheck(location={"us_evident": False, "non_us_explicit": False, "timezone_hint": "unknown", "raw": None})
    gate, _ = apply_gates(pc, verdict(), CFG)
    assert gate is None


def test_missing_degree_does_not_gate_by_default():
    gate, _ = apply_gates(precheck(degree={"present": False, "level": None, "field": None}), verdict(), CFG)
    assert gate is None


def test_gate_precedence_g1_wins_over_g4():
    pc = precheck(
        placeholders=[{"kind": "placeholder", "match": "[Your Name]"}],
        years_experience={"computed": 1.0, "confidence": "high", "ranges": []},
    )
    gate, _ = apply_gates(pc, verdict(est={"value": 1, "confidence": "high"}), CFG)
    assert gate == "G1"


# --- Penalties --------------------------------------------------------------

def test_no_penalties_for_clean_candidate():
    assert compute_penalties(precheck(), verdict(), CFG) == []


def test_no_linkedin_penalty():
    pc = precheck(linkedin={"present": False, "source": "none", "url": None, "name_matches": None})
    pens = compute_penalties(pc, verdict(), CFG)
    assert [p["kind"] for p in pens] == ["no_linkedin"]
    assert pens[0]["points"] == 8


def test_linkedin_name_mismatch_penalty():
    pc = precheck(linkedin={"present": True, "source": "cv", "url": "u", "name_matches": False})
    pens = compute_penalties(pc, verdict(), CFG)
    assert [p["kind"] for p in pens] == ["linkedin_name_mismatch"]


def test_linkedin_unknown_match_is_not_penalised():
    pc = precheck(linkedin={"present": True, "source": "cv", "url": "u", "name_matches": None})
    assert compute_penalties(pc, verdict(), CFG) == []


def test_tier2_penalties_charged_per_signal():
    flags = [{"tier": 2, "kind": f"k{i}", "quote": "q", "explanation": "e"} for i in range(2)]
    pens = compute_penalties(precheck(), verdict(flags=flags), CFG)
    tier2 = [p for p in pens if p["kind"] == "tier2_signal"]
    assert sum(p["points"] for p in tier2) == 10


def test_pool_duplicate_penalty():
    dups = [{"with_candidate": 2, "bullet": "b1"}, {"with_candidate": 3, "bullet": "b2"}]
    pens = compute_penalties(precheck(pool_duplicate_bullets=dups), verdict(), CFG)
    assert any(p["kind"] == "pool_duplicate" and p["points"] == 10 for p in pens)


def test_years_4_to_5_penalty():
    pc = precheck(years_experience={"computed": 4.5, "confidence": "high", "ranges": []})
    pens = compute_penalties(pc, verdict(est={"value": 4.5, "confidence": "high"}), CFG)
    assert any(p["kind"] == "years_4_to_5" and p["points"] == 10 for p in pens)


def test_no_years_penalty_above_five():
    pc = precheck(years_experience={"computed": 6.0, "confidence": "high", "ranges": []})
    pens = compute_penalties(pc, verdict(), CFG)
    assert not any(p["kind"] == "years_4_to_5" for p in pens)


def test_no_degree_penalty():
    pens = compute_penalties(precheck(degree={"present": False, "level": None, "field": None}), verdict(), CFG)
    assert any(p["kind"] == "no_degree" and p["points"] == 5 for p in pens)


def test_short_stints_penalty_at_threshold():
    pens = compute_penalties(precheck(short_stints_last_5y=3), verdict(), CFG)
    assert any(p["kind"] == "short_stints" for p in pens)


def test_short_stints_below_threshold_not_penalised():
    pens = compute_penalties(precheck(short_stints_last_5y=2), verdict(), CFG)
    assert not any(p["kind"] == "short_stints" for p in pens)


# --- Assessment -------------------------------------------------------------

def test_assess_computes_final_score():
    # 4 criteria at 10 + 3 at 2 = 46; penalties 8 (no linkedin); bonus 3.
    pc = precheck(linkedin={"present": False, "source": "none", "url": None, "name_matches": None})
    a = assess(pc, verdict(bonus=3.0), CFG)
    assert a.fit == 46.0
    assert a.bonus == 3.0
    assert a.penalty_total == 8.0
    assert a.final == 41.0
    assert a.gate is None
    assert "no LinkedIn" in a.flags


def test_assess_gated_candidate_keeps_scores_but_records_gate():
    pc = precheck(hidden_text={"found": True, "spans": [{"kind": "micro_font", "text": "x"}]})
    a = assess(pc, verdict(), CFG)
    assert a.gate == "G3"
    assert a.fit > 0


def test_assess_final_never_below_zero():
    scores = {k: 0 for k in CFG.criterion_keys()}
    pc = precheck(
        linkedin={"present": False, "source": "none", "url": None, "name_matches": None},
        degree={"present": False, "level": None, "field": None},
        short_stints_last_5y=3,
    )
    a = assess(pc, verdict(scores=scores), CFG)
    assert a.final == 0.0


def test_assess_records_flag_chips_for_report():
    pc = precheck(gap_over_12m=True, location={"us_evident": False, "non_us_explicit": False, "timezone_hint": "unknown", "raw": None})
    a = assess(pc, verdict(), CFG)
    assert "gap" in a.flags
    assert "location?" in a.flags
    assert a.timezone_hint == "unknown"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rank_gates.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'screen.rank'`

- [ ] **Step 3: Write `screen/rank.py`**

```python
"""Gates, penalties, and the final score.

Gates eliminate a candidate but never remove them from the pool count — the 20 %
cap is a fraction of everyone who applied, not of everyone who survived.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from screen.config import RoleConfig
from screen.verdict import fit_total

GATE_ORDER = ("G1", "G2", "G3", "G4", "G5")


@dataclass(frozen=True)
class Assessment:
    candidate_id: int
    gate: str | None
    gate_reasons: list[str]
    fit: float
    bonus: float
    penalties: list[dict[str, Any]]
    penalty_total: float
    final: float
    flags: list[str]
    tier2_count: float
    timezone_hint: str
    quote_warnings: list[str] = field(default_factory=list)


def _judge_flags(verdict: dict[str, Any], tier: int) -> list[dict[str, Any]]:
    return [f for f in verdict["redflag"]["flags"] if int(f["tier"]) == tier]


def tier2_count(precheck: dict[str, Any], verdict: dict[str, Any], cfg: RoleConfig) -> float:
    """Tier-2 signals from the judge plus the two we can count deterministically.

    PDF metadata counts as half a signal: it corroborates, it never convicts.
    """
    count = float(len(_judge_flags(verdict, 2)))

    if precheck.get("skills_count", 0) >= int(cfg.gates["skills_count_threshold"]):
        count += 1.0
    if precheck.get("template_metadata_signal"):
        count += 0.5

    return count


def _effective_years(precheck: dict[str, Any], verdict: dict[str, Any]) -> float | None:
    """Prefer the computed value; fall back to the judge when dates were unclear."""
    computed = precheck.get("years_experience") or {}
    if computed.get("confidence") in {"high", "medium"}:
        return float(computed.get("computed") or 0.0)

    estimate = (verdict.get("redflag") or {}).get("years_experience_estimate") or {}
    if estimate.get("value") is not None:
        return float(estimate["value"])
    if computed.get("computed"):
        return float(computed["computed"])
    return None


def apply_gates(
    precheck: dict[str, Any], verdict: dict[str, Any], cfg: RoleConfig
) -> tuple[str | None, list[str]]:
    """Return the first gate the candidate trips, in G1..G5 order, with reasons."""
    gates: dict[str, list[str]] = {g: [] for g in GATE_ORDER}

    # G1 — Tier 1: unambiguous evidence nobody edited this CV.
    for p in precheck.get("placeholders") or []:
        gates["G1"].append(f"template placeholder left in: {p['match']}")
    for d in precheck.get("intra_cv_duplicate_bullets") or []:
        gates["G1"].append(
            f"duplicate bullets in the same CV (similarity {d['similarity']}): {d['a'][:80]}"
        )
    pool_dups = precheck.get("pool_duplicate_bullets") or []
    if len(pool_dups) >= int(cfg.gates["pool_dup_tier1_count"]):
        others = sorted({d["with_candidate"] for d in pool_dups})
        gates["G1"].append(
            f"{len(pool_dups)} bullets shared verbatim with candidate(s) {others}"
        )
    for f in _judge_flags(verdict, 1):
        gates["G1"].append(f"{f['kind']}: {f['quote'][:100]}")

    # G2 — enough Tier 2 signals to stop being a coincidence.
    t2 = tier2_count(precheck, verdict, cfg)
    if t2 >= float(cfg.gates["tier2_gate_count"]):
        kinds = [f["kind"] for f in _judge_flags(verdict, 2)]
        gates["G2"].append(f"{t2} Tier-2 AI-slop signals ({', '.join(kinds) or 'heuristics'})")

    # G3 — hidden text is deliberate gaming, not carelessness.
    hidden = precheck.get("hidden_text") or {}
    if hidden.get("found"):
        kinds = sorted({s["kind"] for s in hidden.get("spans") or []})
        gates["G3"].append(f"hidden text detected ({', '.join(kinds)})")

    # G4 — below the experience floor.
    years = _effective_years(precheck, verdict)
    minimum = float(cfg.gates["min_years"])
    if years is not None and years < minimum:
        gates["G4"].append(f"{years:.1f} years of experience, below the {minimum:.0f}-year floor")

    # G5 — explicitly outside the US with no authorisation or relocation note.
    location = precheck.get("location") or {}
    if location.get("non_us_explicit"):
        raw = location.get("raw")
        detail = f" ({raw})" if raw else ""
        gates["G5"].append(
            "CV indicates non-US residence with no US authorisation or "
            f"relocation note{detail}"
        )

    for gate in GATE_ORDER:
        if gates[gate]:
            return gate, gates[gate]
    return None, []


def compute_penalties(
    precheck: dict[str, Any], verdict: dict[str, Any], cfg: RoleConfig
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    p = cfg.penalties

    linkedin = precheck.get("linkedin") or {}
    if not linkedin.get("present"):
        out.append(
            {
                "kind": "no_linkedin",
                "points": p["no_linkedin"],
                "detail": "no LinkedIn profile in Trakstar or the CV",
            }
        )
    elif linkedin.get("name_matches") is False:
        out.append(
            {
                "kind": "linkedin_name_mismatch",
                "points": p["linkedin_name_mismatch"],
                "detail": f"LinkedIn slug does not match the candidate name ({linkedin.get('url')})",
            }
        )

    for flag in _judge_flags(verdict, 2):
        out.append(
            {
                "kind": "tier2_signal",
                "points": p["tier2_signal"],
                "detail": f"{flag['kind']}: {flag['quote'][:80]}",
            }
        )

    pool_dups = precheck.get("pool_duplicate_bullets") or []
    if pool_dups:
        others = sorted({d["with_candidate"] for d in pool_dups})
        out.append(
            {
                "kind": "pool_duplicate",
                "points": p["pool_duplicate"],
                "detail": f"{len(pool_dups)} bullet(s) shared with candidate(s) {others}",
            }
        )

    years = _effective_years(precheck, verdict)
    if years is not None and float(cfg.gates["min_years"]) <= years < 5:
        out.append(
            {
                "kind": "years_4_to_5",
                "points": p["years_4_to_5"],
                "detail": f"{years:.1f} years, below the JD's 5-year expectation",
            }
        )

    degree = precheck.get("degree") or {}
    if not degree.get("present"):
        out.append(
            {"kind": "no_degree", "points": p["no_degree"], "detail": "no degree evident in the CV"}
        )

    if int(precheck.get("short_stints_last_5y") or 0) >= 3:
        out.append(
            {
                "kind": "short_stints",
                "points": p["short_stints"],
                "detail": f"{precheck['short_stints_last_5y']} roles under 12 months in the last 5 years",
            }
        )

    return out


_FLAG_CHIPS = {
    "no_linkedin": "no LinkedIn",
    "linkedin_name_mismatch": "LinkedIn mismatch",
    "pool_duplicate": "template dup",
    "years_4_to_5": "4-5 yrs",
    "no_degree": "no degree",
    "short_stints": "short stints",
}


def assess(precheck: dict[str, Any], verdict: dict[str, Any], cfg: RoleConfig) -> Assessment:
    gate, reasons = apply_gates(precheck, verdict, cfg)
    penalties = compute_penalties(precheck, verdict, cfg)
    penalty_total = float(sum(p["points"] for p in penalties))

    fit = fit_total(verdict)
    bonus = float(verdict["fit"]["bonus"]["points"] or 0)
    final = max(0.0, round(fit + bonus - penalty_total, 2))

    flags: list[str] = []
    for pen in penalties:
        chip = _FLAG_CHIPS.get(pen["kind"])
        if chip and chip not in flags:
            flags.append(chip)
        if pen["kind"] == "tier2_signal":
            chip = f"AI-slop: {pen['detail'].split(':')[0]}"
            if chip not in flags:
                flags.append(chip)
    if precheck.get("gap_over_12m"):
        flags.append("gap")
    location = precheck.get("location") or {}
    if not location.get("us_evident") and not location.get("non_us_explicit"):
        flags.append("location?")
    if verdict.get("quote_warnings"):
        flags.append("quote warning")

    return Assessment(
        candidate_id=int(precheck["candidate_id"]),
        gate=gate,
        gate_reasons=reasons,
        fit=fit,
        bonus=bonus,
        penalties=penalties,
        penalty_total=penalty_total,
        final=final,
        flags=flags,
        tier2_count=tier2_count(precheck, verdict, cfg),
        timezone_hint=str(location.get("timezone_hint") or "unknown"),
        quote_warnings=list(verdict.get("quote_warnings") or []),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_rank_gates.py -v`
Expected: PASS — 33 tests

- [ ] **Step 5: Commit**

```bash
git add screen/rank.py tests/test_rank_gates.py
git commit -m "feat: gates, penalties, and final score computation"
```

---

### Task 13: Sticky ledger, the 20 % cap, and the calibration window

**Files:**
- Create: `screen/ledger.py`
- Modify: `screen/rank.py` (append the ranking section)
- Test: `tests/test_ledger.py`, `tests/test_rank_cut.py`

**Interfaces:**
- Consumes: `screen.rank.Assessment`, `screen.config.RoleConfig`, `screen.paths.Paths`.
- Produces:
  - `screen.ledger.LedgerEntry` — mutable dataclass `candidate_id: int`, `status: str`, `gate: str | None`, `final: float`, `first_seen_run: str`, `status_changed_run: str`, `pdf_sha256: str`, `trakstar_updated_date: str`, `human_override: str | None`
  - `screen.ledger.load_ledger(path: Path) -> dict[int, LedgerEntry]`
  - `screen.ledger.save_ledger(ledger: dict[int, LedgerEntry], path: Path) -> None` (writes `.bak` first)
  - `screen.ledger.STATUSES = ("accepted", "waitlist", "gated", "needs_review", "withdrawn")`
  - `screen.rank.CutResult` — frozen dataclass `cap: int`, `pool_size: int`, `accepted: list[int]`, `waitlist: list[int]`, `gated: list[int]`, `needs_review: list[int]`, `newly_accepted: list[int]`, `newly_gated: list[int]`, `no_slot: list[int]`, `calibration_window: list[int]`, `quality_floor: float | None`
  - `screen.rank.rank_and_cut(assessments: dict[int, Assessment], ledger: dict[int, LedgerEntry], cfg: RoleConfig, run_id: str, needs_review: dict[int, str], withdrawn: set[int], calibration_order: list[int] | None = None) -> CutResult`

`rank_and_cut` mutates `ledger` in place and returns the summary. Passing `calibration_order` (the main agent's reordering of the window) applies it before the cut; passing `None` uses score order.

- [ ] **Step 1: Write the failing ledger tests**

`tests/test_ledger.py`:

```python
import json

import pytest

from screen.ledger import LedgerEntry, load_ledger, save_ledger


def entry(cid, status="waitlist", final=50.0):
    return LedgerEntry(
        candidate_id=cid,
        status=status,
        gate=None,
        final=final,
        first_seen_run="run-1",
        status_changed_run="run-1",
        pdf_sha256="abc",
        trakstar_updated_date="2026-08-01T10:00:00Z",
        human_override=None,
    )


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "ledger.json"
    save_ledger({1: entry(1, "accepted", 80.0)}, path)
    loaded = load_ledger(path)
    assert loaded[1].status == "accepted"
    assert loaded[1].final == 80.0
    assert loaded[1].candidate_id == 1


def test_load_missing_file_returns_empty(tmp_path):
    assert load_ledger(tmp_path / "absent.json") == {}


def test_save_writes_backup_of_previous_version(tmp_path):
    path = tmp_path / "ledger.json"
    save_ledger({1: entry(1, "waitlist")}, path)
    save_ledger({1: entry(1, "accepted")}, path)
    backup = json.loads((tmp_path / "ledger.json.bak").read_text())
    assert backup[0]["status"] == "waitlist"
    assert load_ledger(path)[1].status == "accepted"


def test_load_corrupt_ledger_raises_rather_than_losing_decisions(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text("{not json")
    with pytest.raises(ValueError, match="corrupt"):
        load_ledger(path)


def test_invalid_status_is_rejected(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps([{**entry(1).__dict__, "status": "hired-ish"}]))
    with pytest.raises(ValueError, match="status"):
        load_ledger(path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ledger.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'screen.ledger'`

- [ ] **Step 3: Write `screen/ledger.py`**

```python
"""The sticky record of every screening decision.

Once a candidate is accepted they stay accepted: a later run that finds three
stronger applicants must not un-shortlist someone the team may already have
contacted. That promise lives here, and it is why the ledger refuses to load a
corrupt file rather than starting fresh.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

STATUSES = ("accepted", "waitlist", "gated", "needs_review", "withdrawn")


@dataclass
class LedgerEntry:
    candidate_id: int
    status: str
    gate: str | None
    final: float
    first_seen_run: str
    status_changed_run: str
    pdf_sha256: str
    trakstar_updated_date: str
    human_override: str | None = None


def load_ledger(path: Path) -> dict[int, LedgerEntry]:
    if not path.exists():
        return {}
    try:
        rows = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"ledger at {path} is corrupt ({exc}); restore {path}.bak before running"
        ) from exc

    out: dict[int, LedgerEntry] = {}
    for row in rows:
        status = row.get("status")
        if status not in STATUSES:
            raise ValueError(f"ledger has invalid status {status!r} for candidate {row.get('candidate_id')}")
        out[int(row["candidate_id"])] = LedgerEntry(
            candidate_id=int(row["candidate_id"]),
            status=status,
            gate=row.get("gate"),
            final=float(row.get("final") or 0.0),
            first_seen_run=str(row.get("first_seen_run") or ""),
            status_changed_run=str(row.get("status_changed_run") or ""),
            pdf_sha256=str(row.get("pdf_sha256") or ""),
            trakstar_updated_date=str(row.get("trakstar_updated_date") or ""),
            human_override=row.get("human_override"),
        )
    return out


def save_ledger(ledger: dict[int, LedgerEntry], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.with_suffix(path.suffix + ".bak").write_text(path.read_text())
    rows = [asdict(ledger[cid]) for cid in sorted(ledger)]
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(rows, indent=2))
    tmp.replace(path)
```

- [ ] **Step 4: Run the ledger tests**

Run: `uv run pytest tests/test_ledger.py -v`
Expected: PASS — 5 tests

- [ ] **Step 5: Write the failing cut tests**

`tests/test_rank_cut.py`:

```python
from pathlib import Path

from screen.config import load_role
from screen.ledger import LedgerEntry
from screen.rank import Assessment, rank_and_cut

CFG = load_role(Path(__file__).resolve().parents[1] / "roles" / "fde")


def a(cid, final, gate=None, tz="unknown"):
    return Assessment(
        candidate_id=cid,
        gate=gate,
        gate_reasons=["reason"] if gate else [],
        fit=final,
        bonus=0.0,
        penalties=[],
        penalty_total=0.0,
        final=final,
        flags=[],
        tier2_count=0.0,
        timezone_hint=tz,
    )


def entry(cid, status, final=0.0, run="run-0"):
    return LedgerEntry(
        candidate_id=cid,
        status=status,
        gate=None,
        final=final,
        first_seen_run=run,
        status_changed_run=run,
        pdf_sha256="abc",
        trakstar_updated_date="u",
    )


def test_cap_is_twenty_percent_floored():
    assessments = {i: a(i, 100 - i) for i in range(1, 11)}
    result = rank_and_cut(assessments, {}, CFG, "run-1", {}, set())
    assert result.pool_size == 10
    assert result.cap == 2
    assert result.accepted == [1, 2]
    assert result.waitlist == list(range(3, 11))


def test_gated_candidates_still_count_in_the_denominator():
    assessments = {i: a(i, 100 - i, gate="G1" if i > 5 else None) for i in range(1, 11)}
    result = rank_and_cut(assessments, {}, CFG, "run-1", {}, set())
    assert result.pool_size == 10
    assert result.cap == 2
    assert result.gated == [6, 7, 8, 9, 10]
    assert result.accepted == [1, 2]


def test_gated_candidate_is_never_accepted_even_with_top_score():
    assessments = {1: a(1, 99, gate="G2"), 2: a(2, 50), 3: a(3, 40), 4: a(4, 30), 5: a(5, 20)}
    result = rank_and_cut(assessments, {}, CFG, "run-1", {}, set())
    assert 1 not in result.accepted
    assert result.accepted == [2]


def test_accepted_candidate_is_never_demoted_when_pool_grows():
    ledger = {1: entry(1, "accepted", 60.0)}
    # Five new stronger applicants arrive; cap for 6 candidates is 1.
    assessments = {1: a(1, 60), **{i: a(i, 90 + i) for i in range(2, 7)}}
    result = rank_and_cut(assessments, ledger, CFG, "run-2", {}, set())
    assert result.cap == 1
    assert 1 in result.accepted
    assert ledger[1].status == "accepted"


def test_open_slots_are_filled_from_waitlist_and_new_arrivals_together():
    ledger = {1: entry(1, "accepted", 90.0), 2: entry(2, "waitlist", 70.0)}
    # Pool of 10 gives cap 2, one already taken, so one slot opens.
    assessments = {1: a(1, 90), 2: a(2, 70), **{i: a(i, 60 - i) for i in range(3, 11)}}
    result = rank_and_cut(assessments, ledger, CFG, "run-2", {}, set())
    assert result.cap == 2
    assert result.accepted == [1, 2]
    assert result.newly_accepted == [2]


def test_quality_floor_blocks_a_weak_candidate_from_a_new_slot():
    ledger = {1: entry(1, "accepted", 90.0)}
    # Cap 2 with 10 candidates, so a slot is open, but the best remaining is far
    # below the accepted floor of 90 - 5 = 85.
    assessments = {1: a(1, 90), **{i: a(i, 40) for i in range(2, 11)}}
    result = rank_and_cut(assessments, ledger, CFG, "run-2", {}, set())
    assert result.cap == 2
    assert result.newly_accepted == []
    assert result.quality_floor == 85.0
    assert result.accepted == [1]


def test_quality_floor_allows_a_candidate_within_five_points():
    ledger = {1: entry(1, "accepted", 90.0)}
    assessments = {1: a(1, 90), 2: a(2, 86), **{i: a(i, 30) for i in range(3, 11)}}
    result = rank_and_cut(assessments, ledger, CFG, "run-2", {}, set())
    assert result.newly_accepted == [2]


def test_no_quality_floor_on_the_first_run():
    assessments = {i: a(i, 10) for i in range(1, 11)}
    result = rank_and_cut(assessments, {}, CFG, "run-1", {}, set())
    assert result.quality_floor is None
    assert len(result.accepted) == 2


def test_no_slot_list_names_candidates_who_would_have_qualified():
    ledger = {1: entry(1, "accepted", 90.0)}
    assessments = {1: a(1, 90), 2: a(2, 88), 3: a(3, 87), **{i: a(i, 20) for i in range(4, 11)}}
    result = rank_and_cut(assessments, ledger, CFG, "run-2", {}, set())
    # Cap 2, one slot, candidate 2 takes it; 3 cleared the floor but has no slot.
    assert result.newly_accepted == [2]
    assert 3 in result.no_slot


def test_withdrawn_candidates_leave_the_denominator():
    assessments = {i: a(i, 100 - i) for i in range(1, 11)}
    result = rank_and_cut(assessments, {}, CFG, "run-1", {}, withdrawn={9, 10})
    assert result.pool_size == 8
    assert result.cap == 1
    assert 9 not in result.waitlist


def test_needs_review_counts_in_pool_but_is_not_ranked():
    assessments = {i: a(i, 100 - i) for i in range(1, 9)}
    result = rank_and_cut(
        assessments, {}, CFG, "run-1", needs_review={9: "unparseable", 10: "missing_resume"}, withdrawn=set()
    )
    assert result.pool_size == 10
    assert result.cap == 2
    assert sorted(result.needs_review) == [9, 10]
    assert 9 not in result.accepted and 9 not in result.waitlist


def test_gated_stays_gated_across_runs():
    ledger = {1: entry(1, "gated", 0.0)}
    ledger[1].gate = "G1"
    assessments = {1: a(1, 95, gate="G1"), 2: a(2, 50), 3: a(3, 40), 4: a(4, 30), 5: a(5, 20)}
    result = rank_and_cut(assessments, ledger, CFG, "run-2", {}, set())
    assert result.gated == [1]
    assert result.newly_gated == []


def test_newly_gated_is_reported():
    ledger = {1: entry(1, "waitlist", 50.0)}
    assessments = {1: a(1, 50, gate="G3"), 2: a(2, 40), 3: a(3, 30), 4: a(4, 20), 5: a(5, 10)}
    result = rank_and_cut(assessments, ledger, CFG, "run-2", {}, set())
    assert result.newly_gated == [1]
    assert ledger[1].status == "gated"


def test_accepted_candidate_who_later_trips_a_gate_is_flagged_not_demoted():
    ledger = {1: entry(1, "accepted", 90.0)}
    assessments = {1: a(1, 90, gate="G1"), 2: a(2, 50), 3: a(3, 40), 4: a(4, 30), 5: a(5, 20)}
    result = rank_and_cut(assessments, ledger, CFG, "run-2", {}, set())
    assert 1 in result.accepted
    assert ledger[1].status == "accepted"
    assert ledger[1].gate == "G1"


def test_timezone_tiebreak_prefers_east_and_central():
    # Equal scores: ET wins over PT.
    assessments = {
        1: a(1, 70, tz="PT"),
        2: a(2, 70, tz="ET"),
        3: a(3, 10),
        4: a(4, 10),
        5: a(5, 10),
    }
    result = rank_and_cut(assessments, {}, CFG, "run-1", {}, set())
    assert result.accepted == [2]


def test_calibration_window_spans_the_cut():
    assessments = {i: a(i, 200 - i) for i in range(1, 51)}
    result = rank_and_cut(assessments, {}, CFG, "run-1", {}, set())
    assert result.cap == 10
    # Window is ranks 5..15 (cut 10, +/- 5).
    assert len(result.calibration_window) == 11
    assert result.calibration_window[0] == 5
    assert result.calibration_window[-1] == 15


def test_calibration_order_reorders_within_the_window_only():
    assessments = {i: a(i, 200 - i) for i in range(1, 21)}
    baseline = rank_and_cut(assessments, {}, CFG, "run-1", {}, set())
    assert baseline.cap == 4
    window = baseline.calibration_window
    reordered = list(reversed(window))
    result = rank_and_cut(
        assessments, {}, CFG, "run-1b", {}, set(), calibration_order=reordered
    )
    assert len(result.accepted) == 4
    # The candidate the agent promoted to the top of the window is now accepted.
    assert reordered[0] in result.accepted


def test_calibration_order_cannot_increase_accepted_count():
    assessments = {i: a(i, 200 - i) for i in range(1, 21)}
    result = rank_and_cut(
        assessments, {}, CFG, "run-1", {}, set(), calibration_order=list(range(1, 21))
    )
    assert len(result.accepted) == result.cap


def test_calibration_order_ignores_ids_outside_the_window():
    assessments = {i: a(i, 200 - i) for i in range(1, 21)}
    baseline = rank_and_cut(assessments, {}, CFG, "run-1", {}, set())
    # Candidate 1 is far above the window and must stay accepted.
    result = rank_and_cut(
        assessments, {}, CFG, "run-1b", {}, set(),
        calibration_order=baseline.calibration_window + [1],
    )
    assert 1 in result.accepted


def test_empty_pool_produces_zero_cap_and_no_crash():
    result = rank_and_cut({}, {}, CFG, "run-1", {}, set())
    assert result.pool_size == 0
    assert result.cap == 0
    assert result.accepted == []


def test_tiny_pool_yields_zero_slots():
    assessments = {i: a(i, 90) for i in range(1, 5)}
    result = rank_and_cut(assessments, {}, CFG, "run-1", {}, set())
    assert result.cap == 0
    assert result.accepted == []
    assert len(result.waitlist) == 4
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `uv run pytest tests/test_rank_cut.py -v`
Expected: FAIL — `ImportError: cannot import name 'rank_and_cut'`

- [ ] **Step 7: Append the ranking section to `screen/rank.py`**

```python
# --- Ranking and the cap ----------------------------------------------------

import math  # noqa: E402  (grouped with the ranking section)

from screen.ledger import LedgerEntry  # noqa: E402


@dataclass(frozen=True)
class CutResult:
    cap: int
    pool_size: int
    accepted: list[int]
    waitlist: list[int]
    gated: list[int]
    needs_review: list[int]
    newly_accepted: list[int]
    newly_gated: list[int]
    no_slot: list[int]
    calibration_window: list[int]
    quality_floor: float | None


def _sort_key(assessment: Assessment, cfg: RoleConfig) -> tuple:
    """Score first; the JD's East-Coast/Midwest preference breaks ties."""
    tz_rank = 0 if assessment.timezone_hint in cfg.tiebreak_timezones else 1
    return (-assessment.final, tz_rank, assessment.candidate_id)


def rank_and_cut(
    assessments: dict[int, Assessment],
    ledger: dict[int, LedgerEntry],
    cfg: RoleConfig,
    run_id: str,
    needs_review: dict[int, str],
    withdrawn: set[int],
    calibration_order: list[int] | None = None,
) -> CutResult:
    """Rank, apply the cap, and update the ledger in place.

    Pool counts everyone who applied except withdrawals — gated and
    needs-review candidates included — so the 20 % is honest.
    """
    pool_ids = (set(assessments) | set(needs_review)) - withdrawn
    pool_size = len(pool_ids)
    cap = math.floor(cfg.cap_fraction * pool_size)

    gated: list[int] = []
    rankable: list[Assessment] = []
    for cid, assessment in assessments.items():
        if cid in withdrawn or cid in needs_review:
            continue
        previously_accepted = (
            cid in ledger and ledger[cid].status == "accepted"
        )
        if assessment.gate and not previously_accepted:
            gated.append(cid)
        else:
            rankable.append(assessment)

    rankable.sort(key=lambda a: _sort_key(a, cfg))
    order = [a.candidate_id for a in rankable]

    # The calibration window straddles the cut; the main agent may reorder
    # inside it but cannot pull anyone in from outside or widen the cut.
    w = cfg.calibration_window
    lo, hi = max(0, cap - w), min(len(order), cap + w + 1)
    window = order[lo:hi]

    if calibration_order:
        allowed = set(window)
        proposed = [cid for cid in calibration_order if cid in allowed]
        remainder = [cid for cid in window if cid not in proposed]
        order = order[:lo] + proposed + remainder + order[hi:]

    already_accepted = [
        cid for cid in order if cid in ledger and ledger[cid].status == "accepted"
    ]
    floor: float | None = None
    if already_accepted:
        floor = round(
            min(ledger[cid].final for cid in already_accepted) - cfg.quality_floor_delta, 2
        )

    accepted = list(already_accepted)
    newly_accepted: list[int] = []
    no_slot: list[int] = []

    for cid in order:
        if cid in accepted:
            continue
        assessment = assessments[cid]
        clears_floor = floor is None or assessment.final >= floor
        if len(accepted) < cap and clears_floor:
            accepted.append(cid)
            newly_accepted.append(cid)
        elif clears_floor and len(accepted) >= cap:
            no_slot.append(cid)

    accepted_set = set(accepted)
    waitlist = [cid for cid in order if cid not in accepted_set]

    # --- Ledger update ------------------------------------------------------
    newly_gated: list[int] = []

    def status_for(cid: int) -> str:
        if cid in withdrawn:
            return "withdrawn"
        if cid in needs_review:
            return "needs_review"
        if cid in accepted_set:
            return "accepted"
        if cid in gated:
            return "gated"
        return "waitlist"

    for cid in sorted(pool_ids | withdrawn):
        assessment = assessments.get(cid)
        new_status = status_for(cid)
        existing = ledger.get(cid)

        if existing is None:
            ledger[cid] = LedgerEntry(
                candidate_id=cid,
                status=new_status,
                gate=assessment.gate if assessment else None,
                final=assessment.final if assessment else 0.0,
                first_seen_run=run_id,
                status_changed_run=run_id,
                pdf_sha256="",
                trakstar_updated_date="",
            )
            if new_status == "gated":
                newly_gated.append(cid)
            continue

        # Record the gate even on an accepted candidate: the report surfaces it
        # as a flag, and a human decides whether to override.
        if assessment is not None:
            existing.gate = assessment.gate
            if existing.status != "accepted":
                existing.final = assessment.final

        if existing.status == "accepted":
            continue  # sticky

        if existing.status != new_status:
            existing.status = new_status
            existing.status_changed_run = run_id
            if new_status == "gated":
                newly_gated.append(cid)

    return CutResult(
        cap=cap,
        pool_size=pool_size,
        accepted=sorted(accepted),
        waitlist=waitlist,
        gated=sorted(gated),
        needs_review=sorted(needs_review),
        newly_accepted=sorted(newly_accepted),
        newly_gated=sorted(newly_gated),
        no_slot=sorted(no_slot),
        calibration_window=window,
        quality_floor=floor,
    )
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/test_rank_cut.py -v`
Expected: PASS — 21 tests

- [ ] **Step 9: Run the full suite**

Run: `uv run pytest`
Expected: PASS — everything from Tasks 1–13

- [ ] **Step 10: Commit**

```bash
git add screen/ledger.py screen/rank.py tests/test_ledger.py tests/test_rank_cut.py
git commit -m "feat: sticky ledger, 20% cap, quality floor, and calibration window"
```

---

### Task 14: Report rendering — HTML, Markdown, CSV

**Files:**
- Create: `screen/report.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `screen.config.RoleConfig`, `screen.rank.Assessment`/`CutResult`, `screen.ledger.LedgerEntry`, `screen.paths.Paths`.
- Produces:
  - `screen.report.ReportInput` — frozen dataclass bundling `run_id: str`, `cut: CutResult`, `assessments: dict[int, Assessment]`, `candidates: dict[int, dict]`, `prechecks: dict[int, dict]`, `verdicts: dict[int, dict]`, `ledger: dict[int, LedgerEntry]`, `needs_review: dict[int, str]`, `delta: dict`, `calibration_note: str`, `opening_id: str`
  - `screen.report.render_html(data: ReportInput, cfg: RoleConfig) -> str`
  - `screen.report.render_markdown(data: ReportInput, cfg: RoleConfig) -> str`
  - `screen.report.rows_for_csv(data: ReportInput, cfg: RoleConfig, statuses: tuple[str, ...]) -> list[dict]`
  - `screen.report.write_all(data: ReportInput, cfg: RoleConfig, paths: Paths) -> dict[str, Path]`
  - `screen.report.trakstar_url(opening_id: str, candidate_id: int) -> str`

- [ ] **Step 1: Write the failing tests**

`tests/test_report.py`:

```python
import csv
from pathlib import Path

from screen.config import load_role
from screen.ledger import LedgerEntry
from screen.paths import Paths
from screen.rank import Assessment, CutResult
from screen.report import (
    ReportInput,
    render_html,
    render_markdown,
    rows_for_csv,
    write_all,
)

CFG = load_role(Path(__file__).resolve().parents[1] / "roles" / "fde")


def _assessment(cid, final, gate=None, flags=(), penalties=()):
    return Assessment(
        candidate_id=cid,
        gate=gate,
        gate_reasons=[f"{gate} reason for {cid}"] if gate else [],
        fit=final,
        bonus=0.0,
        penalties=list(penalties),
        penalty_total=float(sum(p["points"] for p in penalties)),
        final=final,
        flags=list(flags),
        tier2_count=0.0,
        timezone_hint="ET",
    )


def _verdict(cid):
    keys = CFG.criterion_keys()
    return {
        "candidate_id": cid,
        "redflag": {"flags": [], "years_experience_estimate": {"value": 7, "confidence": "high"}},
        "fit": {
            "scores": {
                k: {"score": 5.0, "quote": f"quote for {k}", "rationale": "because"} for k in keys
            },
            "bonus": {"points": 0, "justification": None},
            "summary": f"Summary for candidate {cid}.",
        },
        "quote_warnings": [],
    }


def _precheck(cid):
    return {
        "candidate_id": cid,
        "pages": 2,
        "pdf_meta": {"producer": "LaTeX", "created": "D:20260801094600Z", "minutes_before_submission": 14},
        "years_experience": {"computed": 7.5, "confidence": "high", "ranges": [["2019-03", "present"]]},
        "linkedin": {"present": True, "source": "trakstar", "url": "https://linkedin.com/in/x", "name_matches": True},
        "degree": {"present": True, "level": "BSc", "field": "Computer Science"},
        "location": {"us_evident": True, "non_us_explicit": False, "timezone_hint": "ET", "raw": "Boston, MA"},
        "skills_count": 12,
        "hidden_text": {"found": False, "spans": []},
        "placeholders": [],
        "pool_duplicate_bullets": [],
        "intra_cv_duplicate_bullets": [],
    }


def _data():
    ids = [1, 2, 3, 4, 5]
    assessments = {
        1: _assessment(1, 82.0),
        2: _assessment(2, 71.0, flags=["no LinkedIn"], penalties=[{"kind": "no_linkedin", "points": 8, "detail": "none found"}]),
        3: _assessment(3, 55.0),
        4: _assessment(4, 40.0, gate="G1"),
        5: _assessment(5, 30.0, gate="G2"),
    }
    cut = CutResult(
        cap=1,
        pool_size=6,
        accepted=[1],
        waitlist=[2, 3],
        gated=[4, 5],
        needs_review=[6],
        newly_accepted=[1],
        newly_gated=[4],
        no_slot=[2],
        calibration_window=[1, 2, 3],
        quality_floor=None,
    )
    return ReportInput(
        run_id="2026-08-28T0800",
        cut=cut,
        assessments=assessments,
        candidates={
            i: {
                "id": i,
                "first_name": f"Cand{i}",
                "last_name": "Test",
                "email": f"c{i}@example.com",
                "created_date": "2026-08-01T10:00:00Z",
            }
            for i in ids + [6]
        },
        prechecks={i: _precheck(i) for i in ids},
        verdicts={i: _verdict(i) for i in ids},
        ledger={
            i: LedgerEntry(
                candidate_id=i,
                status="accepted" if i == 1 else "waitlist" if i in (2, 3) else "gated",
                gate=assessments[i].gate,
                final=assessments[i].final,
                first_seen_run="2026-08-28T0800",
                status_changed_run="2026-08-28T0800",
                pdf_sha256="abc",
                trakstar_updated_date="u",
            )
            for i in ids
        },
        needs_review={6: "unparseable"},
        delta={"new": [1, 2, 3, 4, 5, 6], "newly_accepted": [1], "newly_gated": [4], "no_slot": [2]},
        calibration_note="Reviewed ranks 1-3; no reordering needed.",
        opening_id="704353",
    )


# --- HTML -------------------------------------------------------------------

def test_html_is_self_contained():
    html = render_html(_data(), CFG)
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert "<script src=" not in html
    assert "<link rel=\"stylesheet\"" not in html
    assert "http://" not in html.replace("http://www.w3.org", "")


def test_html_has_every_required_section():
    html = render_html(_data(), CFG)
    for heading in ("Run", "Delta", "Shortlist", "Waitlist", "Gated", "Needs manual review", "Methodology"):
        assert heading in html


def test_html_shows_cap_arithmetic():
    html = render_html(_data(), CFG)
    assert "20%" in html
    assert "of 6" in html


def test_html_lists_shortlist_names_and_scores():
    html = render_html(_data(), CFG)
    assert "Cand1 Test" in html
    assert "82" in html


def test_html_shows_gate_reason_quotes():
    html = render_html(_data(), CFG)
    assert "G1 reason for 4" in html


def test_html_shows_flag_chips():
    html = render_html(_data(), CFG)
    assert "no LinkedIn" in html


def test_html_shows_per_criterion_quotes_in_detail():
    html = render_html(_data(), CFG)
    assert "quote for production_ownership" in html


def test_html_shows_needs_review_reason():
    html = render_html(_data(), CFG)
    assert "unparseable" in html


def test_html_includes_calibration_note_and_rubric_version():
    html = render_html(_data(), CFG)
    assert "no reordering needed" in html
    assert f"rubric v{CFG.rubric_version}" in html.lower() or f"v{CFG.rubric_version}" in html


def test_html_escapes_candidate_supplied_text():
    data = _data()
    data.verdicts[1]["fit"]["summary"] = "<script>alert('xss')</script>"
    html = render_html(data, CFG)
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_html_never_contains_demographic_fields():
    html = render_html(_data(), CFG)
    for banned in ("gender", "nationality", "date of birth", "age:"):
        assert banned not in html.lower()


def test_html_avoids_hire_no_hire_language():
    html = render_html(_data(), CFG).lower()
    assert "no-hire" not in html
    assert "do not hire" not in html


def test_html_links_to_trakstar():
    html = render_html(_data(), CFG)
    assert "anduin.hire.trakstar.com" in html


# --- Markdown ---------------------------------------------------------------

def test_markdown_is_short_and_has_the_numbers():
    md = render_markdown(_data(), CFG)
    assert len(md.splitlines()) <= 45
    assert "Pool: 6" in md
    assert "Cap: 1" in md


def test_markdown_lists_delta_with_names_and_scores():
    md = render_markdown(_data(), CFG)
    assert "Cand1 Test" in md
    assert "82" in md


def test_markdown_names_newly_gated_with_gate():
    md = render_markdown(_data(), CFG)
    assert "Cand4 Test" in md
    assert "G1" in md


def test_markdown_reports_no_changes_when_delta_is_empty():
    data = _data()
    data.delta.update({"new": [], "newly_accepted": [], "newly_gated": [], "no_slot": []})
    md = render_markdown(data, CFG)
    assert "no changes" in md.lower()


# --- CSV --------------------------------------------------------------------

def test_csv_rows_have_all_columns():
    rows = rows_for_csv(_data(), CFG, statuses=("accepted", "waitlist", "gated"))
    assert rows
    row = rows[0]
    for column in ("id", "name", "email", "status", "gate", "final", "penalties", "flags", "trakstar_url"):
        assert column in row
    for key in CFG.criterion_keys():
        assert key in row


def test_csv_shortlist_filter_returns_only_accepted():
    rows = rows_for_csv(_data(), CFG, statuses=("accepted",))
    assert [r["id"] for r in rows] == [1]


def test_csv_has_no_demographic_columns():
    rows = rows_for_csv(_data(), CFG, statuses=("accepted", "waitlist", "gated"))
    for banned in ("gender", "nationality", "dob", "age", "location", "city"):
        assert banned not in rows[0]


# --- write_all --------------------------------------------------------------

def test_write_all_creates_every_artifact(tmp_path):
    paths = Paths(root=tmp_path, opening_id="704353")
    paths.ensure()
    written = write_all(_data(), CFG, paths)

    for key in ("html", "markdown", "shortlist_csv", "all_csv", "latest_html", "latest_markdown"):
        assert written[key].exists(), key
    assert written["html"].name.startswith("2026-08-28")
    assert written["latest_html"].name == "latest.html"


def test_write_all_latest_matches_run_file(tmp_path):
    paths = Paths(root=tmp_path, opening_id="704353")
    paths.ensure()
    written = write_all(_data(), CFG, paths)
    assert written["latest_html"].read_text() == written["html"].read_text()


def test_write_all_csv_is_parseable(tmp_path):
    paths = Paths(root=tmp_path, opening_id="704353")
    paths.ensure()
    written = write_all(_data(), CFG, paths)
    with written["shortlist_csv"].open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    assert rows[0]["name"] == "Cand1 Test"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'screen.report'`

- [ ] **Step 3: Write `screen/report.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_report.py -v`
Expected: PASS — 24 tests

- [ ] **Step 5: Eyeball the HTML once**

```bash
uv run python - <<'PY'
import sys; sys.path.insert(0, "tests")
from pathlib import Path
from test_report import _data
from screen.config import load_role
from screen.report import render_html
Path("/tmp/screening-preview.html").write_text(
    render_html(_data(), load_role(Path("roles/fde")))
)
print("wrote /tmp/screening-preview.html")
PY
open /tmp/screening-preview.html
```

Confirm by eye: the six stat tiles read correctly, the criteria bars render, `evidence` expands to show quotes, and the gated section shows its reasons. Fix any layout problem before committing.

- [ ] **Step 6: Commit**

```bash
git add screen/report.py tests/test_report.py
git commit -m "feat: HTML, Markdown, and CSV report rendering"
```

---

### Task 15: CLI

**Files:**
- Create: `screen/cli.py`, `screen/__main__.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: every module built so far.
- Produces:
  - `python -m screen fetch [--source trakstar|folder] [--path DIR] [--csv FILE]`
  - `python -m screen parse`
  - `python -m screen precheck [--force]`
  - `python -m screen pending [--full]` — prints JSON `{"pending": [ids], "cached": [ids], "needs_review": {...}, "rubric_change": bool}`; the skill reads this to decide the fan-out
  - `python -m screen rank --prepare` — prints JSON `{"run_id": ..., "calibration_window": [...], "candidates": [{id, final, flags, summary, quotes}]}`
  - `python -m screen rank --finalize [--calibration FILE]`
  - `python -m screen report`
  - `python -m screen run [--full]` — fetch → parse → precheck → pending (does **not** judge; judging is the skill's job)
  - Shared flags: `--root DIR`, `--opening ID`, `--role DIR`, `--today YYYY-MM`
  - `screen.cli.main(argv: list[str] | None = None) -> int`
  - `screen.cli.run_id_now(now: datetime) -> str` — `"YYYY-MM-DDTHHMM"`

Exit codes: `0` success, `1` unexpected error, `2` bad usage, `3` rubric change needs `--full`, `4` Trakstar auth failure.

- [ ] **Step 1: Write the failing tests**

`tests/test_cli.py`:

```python
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

from screen.cli import main, run_id_now
from screen.paths import Paths

sys.path.insert(0, str(Path(__file__).parent))
from fixtures.make_fixtures import build_all  # noqa: E402

ROLE = str(Path(__file__).resolve().parents[1] / "roles" / "fde")


@pytest.fixture(scope="module")
def pdfs(tmp_path_factory):
    return build_all(tmp_path_factory.mktemp("pdfs"))


@pytest.fixture
def project(tmp_path, pdfs):
    """A folder-sourced project with five CVs, fetched and parsed."""
    src = tmp_path / "cvs"
    src.mkdir()
    for name, key in [
        ("Alex Morgan", "clean"),
        ("Pat Placeholder", "placeholder"),
        ("Jordan Blake", "template_a"),
        ("Riley Chen", "template_b"),
        ("Sam Rivera", "four_year"),
    ]:
        (src / f"{name}.pdf").write_bytes(pdfs[key].read_bytes())
    root = tmp_path / "proj"
    return root, src


def _args(root, *rest):
    return ["--root", str(root), "--opening", "704353", "--role", ROLE, *rest]


def test_run_id_format():
    assert run_id_now(datetime(2026, 8, 28, 8, 0)) == "2026-08-28T0800"


def test_fetch_folder_then_precheck(project, capsys):
    root, src = project
    assert main(_args(root, "fetch", "--source", "folder", "--path", str(src))) == 0
    assert main(_args(root, "parse")) == 0
    assert main(_args(root, "precheck", "--today", "2026-08")) == 0
    paths = Paths(root=root, opening_id="704353")
    assert len(list(paths.prechecks.glob("*.json"))) >= 4
    assert len(list(paths.redacted.glob("*.md"))) >= 4


def test_pending_lists_everyone_before_judging(project):
    root, src = project
    main(_args(root, "fetch", "--source", "folder", "--path", str(src)))
    main(_args(root, "parse"))
    main(_args(root, "precheck", "--today", "2026-08"))

    out_file = root / "pending.json"
    assert main(_args(root, "pending", "--out", str(out_file))) == 0
    payload = json.loads(out_file.read_text())
    assert len(payload["pending"]) >= 4
    assert payload["cached"] == []
    assert payload["rubric_change"] is False


def test_pending_is_empty_after_verdicts_written(project):
    root, src = project
    main(_args(root, "fetch", "--source", "folder", "--path", str(src)))
    main(_args(root, "parse"))
    main(_args(root, "precheck", "--today", "2026-08"))

    out_file = root / "pending.json"
    main(_args(root, "pending", "--out", str(out_file)))
    pending = json.loads(out_file.read_text())["pending"]

    _write_fake_verdicts(root, pending)

    main(_args(root, "pending", "--out", str(out_file)))
    payload = json.loads(out_file.read_text())
    assert payload["pending"] == []
    assert sorted(payload["cached"]) == sorted(pending)


def _write_fake_verdicts(root, ids, score=8.0):
    """Stand in for the judge subagents so the CLI can be tested end to end."""
    from screen.config import load_role
    from screen.verdict import validate_verdict, verdict_key, write_verdict

    cfg = load_role(Path(ROLE))
    paths = Paths(root=root, opening_id="704353")
    for cid in ids:
        precheck = json.loads((paths.prechecks / f"{cid}.json").read_text())
        redacted = (paths.redacted / f"{cid}.md").read_text()
        first_line = next(
            (line.strip("-* ").strip() for line in redacted.splitlines() if line.strip().startswith(("-", "*"))),
            "experience",
        )
        raw = {
            "candidate_id": cid,
            "redflag": {"flags": [], "years_experience_estimate": {"value": 7, "confidence": "high"}},
            "fit": {
                "scores": {
                    k: {"score": min(score, cfg.criterion(k).max), "quote": first_line, "rationale": "r"}
                    for k in cfg.criterion_keys()
                },
                "bonus": {"points": 0, "justification": None},
                "summary": f"Candidate {cid} summary.",
            },
        }
        verdict = validate_verdict(raw, cfg, redacted)
        verdict["verdict_key"] = verdict_key(redacted, precheck, cfg.rubric_version)
        write_verdict(verdict, paths.verdicts / f"{cid}.json")


def test_rank_prepare_emits_calibration_window(project):
    root, src = project
    main(_args(root, "fetch", "--source", "folder", "--path", str(src)))
    main(_args(root, "parse"))
    main(_args(root, "precheck", "--today", "2026-08"))
    out_file = root / "pending.json"
    main(_args(root, "pending", "--out", str(out_file)))
    _write_fake_verdicts(root, json.loads(out_file.read_text())["pending"])

    prep = root / "prepare.json"
    assert main(_args(root, "rank", "--prepare", "--out", str(prep))) == 0
    payload = json.loads(prep.read_text())
    assert "run_id" in payload
    assert "calibration_window" in payload
    assert isinstance(payload["candidates"], list)


def test_rank_finalize_then_report_writes_artifacts(project):
    root, src = project
    main(_args(root, "fetch", "--source", "folder", "--path", str(src)))
    main(_args(root, "parse"))
    main(_args(root, "precheck", "--today", "2026-08"))
    out_file = root / "pending.json"
    main(_args(root, "pending", "--out", str(out_file)))
    _write_fake_verdicts(root, json.loads(out_file.read_text())["pending"])

    prep = root / "prepare.json"
    main(_args(root, "rank", "--prepare", "--out", str(prep)))
    run_id = json.loads(prep.read_text())["run_id"]

    assert main(_args(root, "rank", "--finalize", "--run-id", run_id)) == 0
    assert main(_args(root, "report", "--run-id", run_id)) == 0

    paths = Paths(root=root, opening_id="704353")
    assert (paths.report / "latest.html").exists()
    assert (paths.report / "latest.md").exists()
    assert paths.ledger_json.exists()
    assert list(paths.runs.glob("*.json"))


def test_gated_candidates_appear_in_the_ledger(project):
    root, src = project
    main(_args(root, "fetch", "--source", "folder", "--path", str(src)))
    main(_args(root, "parse"))
    main(_args(root, "precheck", "--today", "2026-08"))
    out_file = root / "pending.json"
    main(_args(root, "pending", "--out", str(out_file)))
    _write_fake_verdicts(root, json.loads(out_file.read_text())["pending"])
    prep = root / "prepare.json"
    main(_args(root, "rank", "--prepare", "--out", str(prep)))
    run_id = json.loads(prep.read_text())["run_id"]
    main(_args(root, "rank", "--finalize", "--run-id", run_id))

    from screen.ledger import load_ledger

    ledger = load_ledger(Paths(root=root, opening_id="704353").ledger_json)
    statuses = {e.status for e in ledger.values()}
    # The placeholder CV and the shared-template pair must be eliminated.
    assert "gated" in statuses


def test_rubric_change_requires_full_flag(project):
    root, src = project
    main(_args(root, "fetch", "--source", "folder", "--path", str(src)))
    main(_args(root, "parse"))
    main(_args(root, "precheck", "--today", "2026-08"))
    out_file = root / "pending.json"
    main(_args(root, "pending", "--out", str(out_file)))
    ids = json.loads(out_file.read_text())["pending"]
    _write_fake_verdicts(root, ids)

    # Bump the rubric version in a copied role dir.
    role_copy = root / "role"
    role_copy.mkdir(parents=True, exist_ok=True)
    original = json.loads((Path(ROLE) / "role.json").read_text())
    original["rubric_version"] = 99
    (role_copy / "role.json").write_text(json.dumps(original))
    for name in ("redflag_prompt.md", "fit_prompt.md"):
        source = Path(ROLE) / name
        if source.exists():
            (role_copy / name).write_text(source.read_text())

    args = ["--root", str(root), "--opening", "704353", "--role", str(role_copy)]
    assert main([*args, "pending", "--out", str(out_file)]) == 3

    payload = json.loads(out_file.read_text())
    assert payload["rubric_change"] is True
    assert sorted(payload["pending"]) == sorted(ids)

    assert main([*args, "pending", "--full", "--out", str(out_file)]) == 0


def test_bad_source_is_usage_error(project):
    root, src = project
    assert main(_args(root, "fetch", "--source", "folder")) == 2


def test_unknown_command_is_usage_error(project, capsys):
    root, _ = project
    with pytest.raises(SystemExit):
        main(_args(root, "frobnicate"))


def test_run_command_stops_before_judging(project):
    root, src = project
    assert main(_args(root, "run", "--source", "folder", "--path", str(src), "--today", "2026-08")) == 0
    paths = Paths(root=root, opening_id="704353")
    assert list(paths.prechecks.glob("*.json"))
    assert not list(paths.verdicts.glob("*.json"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'screen.cli'`

- [ ] **Step 3: Write `screen/cli.py`**

```python
"""Command-line entry points for the deterministic stages.

Judgment is deliberately absent here: `pending` tells the Claude Code skill who
needs judging, and `rank --prepare` / `--finalize` bracket the calibration step
the main agent performs. That split keeps every LLM call inside the skill and
every reproducible computation inside Python.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from screen import fetch as fetch_mod
from screen import report as report_mod
from screen.config import RoleConfig, load_role
from screen.ledger import load_ledger, save_ledger
from screen.paths import Paths
from screen.precheck import run_stage as precheck_stage
from screen.rank import assess, rank_and_cut
from screen.verdict import load_verdict, verdict_key

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_RUBRIC_CHANGE, EXIT_AUTH = 0, 1, 2, 3, 4

WITHDRAWN_STATES = {"rejected", "hired", "withdrawn", "archived"}


def run_id_now(now: datetime) -> str:
    return now.strftime("%Y-%m-%dT%H%M")


def _emit(payload: dict[str, Any], out: str | None) -> None:
    text = json.dumps(payload, indent=2)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text)
    print(text)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text()) if path.exists() else None


def _today(arg: str | None) -> tuple[int, int]:
    if arg:
        year, month = arg.split("-")
        return int(year), int(month)
    now = datetime.now()
    return now.year, now.month


def _context(args: argparse.Namespace) -> tuple[Paths, RoleConfig]:
    root = Path(args.root).resolve()
    paths = Paths(root=root, opening_id=str(args.opening))
    paths.ensure()
    return paths, load_role(Path(args.role).resolve())


# --- stages -----------------------------------------------------------------


def cmd_fetch(args: argparse.Namespace) -> int:
    paths, _cfg = _context(args)

    if args.source == "folder":
        if not args.path:
            print("--path is required with --source folder", file=sys.stderr)
            return EXIT_USAGE
        folder = Path(args.path)
        if not folder.is_dir():
            print(f"{folder} is not a directory", file=sys.stderr)
            return EXIT_USAGE
        result = fetch_mod.fetch_folder(
            paths, folder, csv_path=Path(args.csv) if args.csv else None
        )
    else:
        api_key = os.environ.get("TRAKSTAR_API_KEY", "")
        try:
            client = fetch_mod.TrakstarClient(api_key=api_key)
            result = fetch_mod.fetch_trakstar(paths, client, str(args.opening))
        except PermissionError as exc:
            print(str(exc), file=sys.stderr)
            return EXIT_AUTH

    _emit(result, args.out)
    return EXIT_OK


def cmd_parse(args: argparse.Namespace) -> int:
    from screen.parse import parse_pdf, write_parsed

    paths, _cfg = _context(args)
    candidates = _load_json(paths.candidates_json) or []
    parsed, failed = [], {}

    for candidate in candidates:
        cid = int(candidate["id"])
        pdf = paths.resumes / f"{cid}.pdf"
        md_path = paths.parsed / f"{cid}.md"
        meta_path = paths.parsed / f"{cid}.meta.json"
        if not pdf.exists():
            failed[cid] = "missing_resume"
            continue

        existing = _load_json(meta_path)
        from screen.parse import sha256_file

        if existing and existing.get("sha256") == sha256_file(pdf) and md_path.exists():
            continue
        try:
            write_parsed(parse_pdf(pdf), md_path, meta_path)
            parsed.append(cid)
        except Exception as exc:  # a broken PDF must not stop the batch
            failed[cid] = f"{type(exc).__name__}: {exc}"

    _emit({"parsed": sorted(parsed), "failed": failed}, args.out)
    return EXIT_OK


def cmd_precheck(args: argparse.Namespace) -> int:
    paths, cfg = _context(args)
    result = precheck_stage(paths, cfg, _today(args.today), force=args.force)
    _emit(result, args.out)
    return EXIT_OK


def cmd_pending(args: argparse.Namespace) -> int:
    """Report who still needs judging, and refuse a silent full re-judge."""
    paths, cfg = _context(args)
    candidates = _load_json(paths.candidates_json) or []
    ledger = load_ledger(paths.ledger_json)

    pending: list[int] = []
    cached: list[int] = []
    needs_review: dict[int, str] = {}

    for candidate in candidates:
        cid = int(candidate["id"])
        precheck_path = paths.prechecks / f"{cid}.json"
        redacted_path = paths.redacted / f"{cid}.md"
        if not precheck_path.exists() or not redacted_path.exists():
            needs_review[cid] = "no_precheck"
            continue

        precheck = json.loads(precheck_path.read_text())
        redacted = redacted_path.read_text()
        want = verdict_key(redacted, precheck, cfg.rubric_version)
        existing = load_verdict(paths.verdicts / f"{cid}.json")

        if args.full or existing is None or existing.get("verdict_key") != want:
            pending.append(cid)
        else:
            cached.append(cid)

    # A pool that was fully judged before and is now fully pending means the
    # rubric moved. Interactive runs can proceed with --full; a scheduled run
    # must not silently re-judge everyone.
    rubric_change = bool(
        ledger and pending and not cached and not args.full and len(pending) > 1
    )

    payload = {
        "pending": sorted(pending),
        "cached": sorted(cached),
        "needs_review": needs_review,
        "rubric_change": rubric_change,
        "rubric_version": cfg.rubric_version,
        "paths": {
            "redacted": str(paths.redacted),
            "prechecks": str(paths.prechecks),
            "verdicts": str(paths.verdicts),
            "role": str(cfg.role_dir),
        },
    }
    _emit(payload, args.out)

    if rubric_change:
        print(
            f"rubric v{cfg.rubric_version} would re-judge all {len(pending)} candidates; "
            "re-run with --full to confirm",
            file=sys.stderr,
        )
        return EXIT_RUBRIC_CHANGE
    return EXIT_OK


def _gather(paths: Paths, cfg: RoleConfig):
    """Load candidates, prechecks, verdicts, and derive assessments."""
    candidates = {int(c["id"]): c for c in (_load_json(paths.candidates_json) or [])}
    prechecks: dict[int, dict] = {}
    verdicts: dict[int, dict] = {}
    assessments = {}
    needs_review: dict[int, str] = {}
    withdrawn: set[int] = set()

    for cid, candidate in candidates.items():
        if str(candidate.get("state", "")).lower() in WITHDRAWN_STATES:
            withdrawn.add(cid)
            continue

        precheck = _load_json(paths.prechecks / f"{cid}.json")
        verdict = load_verdict(paths.verdicts / f"{cid}.json")
        if precheck is None:
            needs_review[cid] = "unparseable"
            continue
        if verdict is None:
            needs_review[cid] = "judge_failed"
            continue

        prechecks[cid] = precheck
        verdicts[cid] = verdict
        assessments[cid] = assess(precheck, verdict, cfg)

    return candidates, prechecks, verdicts, assessments, needs_review, withdrawn


def cmd_rank(args: argparse.Namespace) -> int:
    paths, cfg = _context(args)
    candidates, prechecks, verdicts, assessments, needs_review, withdrawn = _gather(paths, cfg)
    ledger = load_ledger(paths.ledger_json)

    if args.prepare:
        run_id = args.run_id or run_id_now(datetime.now())
        dry = rank_and_cut(
            assessments, dict(ledger), cfg, run_id, needs_review, withdrawn
        )
        window = []
        for cid in dry.calibration_window:
            assessment = assessments[cid]
            verdict = verdicts[cid]
            window.append(
                {
                    "candidate_id": cid,
                    "final": assessment.final,
                    "fit": assessment.fit,
                    "penalties": assessment.penalties,
                    "flags": assessment.flags,
                    "summary": verdict["fit"].get("summary"),
                    "scores": {
                        k: {"score": v["score"], "quote": v["quote"]}
                        for k, v in verdict["fit"]["scores"].items()
                    },
                }
            )
        _emit(
            {
                "run_id": run_id,
                "cap": dry.cap,
                "pool_size": dry.pool_size,
                "quality_floor": dry.quality_floor,
                "calibration_window": dry.calibration_window,
                "candidates": window,
            },
            args.out,
        )
        return EXIT_OK

    if not args.finalize:
        print("rank requires --prepare or --finalize", file=sys.stderr)
        return EXIT_USAGE

    run_id = args.run_id or run_id_now(datetime.now())
    calibration_order = None
    calibration_note = ""
    if args.calibration:
        payload = _load_json(Path(args.calibration)) or {}
        calibration_order = payload.get("order")
        calibration_note = str(payload.get("note") or "")

    previous_status = {cid: entry.status for cid, entry in ledger.items()}
    cut = rank_and_cut(
        assessments, ledger, cfg, run_id, needs_review, withdrawn, calibration_order
    )
    save_ledger(ledger, paths.ledger_json)

    delta = {
        "new": sorted(cid for cid in assessments if cid not in previous_status),
        "newly_accepted": cut.newly_accepted,
        "newly_gated": cut.newly_gated,
        "no_slot": cut.no_slot,
    }
    state = {
        "run_id": run_id,
        "delta": delta,
        "calibration_note": calibration_note,
        "cut": {
            "cap": cut.cap,
            "pool_size": cut.pool_size,
            "accepted": cut.accepted,
            "waitlist": cut.waitlist,
            "gated": cut.gated,
            "needs_review": cut.needs_review,
            "newly_accepted": cut.newly_accepted,
            "newly_gated": cut.newly_gated,
            "no_slot": cut.no_slot,
            "calibration_window": cut.calibration_window,
            "quality_floor": cut.quality_floor,
        },
        "needs_review_reasons": needs_review,
    }
    (paths.runs / f"{run_id.replace(':', '')}.cut.json").write_text(json.dumps(state, indent=2))
    _emit(state, args.out)
    return EXIT_OK


def cmd_report(args: argparse.Namespace) -> int:
    paths, cfg = _context(args)
    candidates, prechecks, verdicts, assessments, needs_review, withdrawn = _gather(paths, cfg)
    ledger = load_ledger(paths.ledger_json)

    run_id = args.run_id or run_id_now(datetime.now())
    state = _load_json(paths.runs / f"{run_id.replace(':', '')}.cut.json")
    if state is None:
        print(
            f"no cut state for run {run_id}; run `rank --finalize` first", file=sys.stderr
        )
        return EXIT_USAGE

    from screen.rank import CutResult

    cut = CutResult(**state["cut"])
    data = report_mod.ReportInput(
        run_id=run_id,
        cut=cut,
        assessments=assessments,
        candidates=candidates,
        prechecks=prechecks,
        verdicts=verdicts,
        ledger=ledger,
        needs_review=state.get("needs_review_reasons") or needs_review,
        delta=state["delta"],
        calibration_note=state.get("calibration_note") or "",
        opening_id=str(args.opening),
    )
    written = report_mod.write_all(data, cfg, paths)
    print(written["markdown"].read_text())
    _emit({k: str(v) for k, v in written.items()}, args.out)
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    """Everything up to judging: fetch, parse, precheck, then list pending."""
    for step in (cmd_fetch, cmd_parse, cmd_precheck):
        code = step(args)
        if code != EXIT_OK:
            return code
    return cmd_pending(args)


# --- argument parsing -------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="screen", description="CV screening pipeline")
    parser.add_argument("--root", default=".", help="project root holding data/, state/, report/")
    parser.add_argument("--opening", default=os.environ.get("OPENING_ID", "704353"))
    parser.add_argument("--role", default="roles/fde")
    parser.add_argument("--out", help="also write this command's JSON output to a file")

    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser("fetch", help="pull candidates and CV files")
    p_fetch.add_argument("--source", choices=("trakstar", "folder"), default="trakstar")
    p_fetch.add_argument("--path", help="folder of PDFs (with --source folder)")
    p_fetch.add_argument("--csv", help="optional Trakstar CSV export for LinkedIn/location")
    p_fetch.set_defaults(func=cmd_fetch)

    sub.add_parser("parse", help="PDF to markdown").set_defaults(func=cmd_parse)

    p_pre = sub.add_parser("precheck", help="run deterministic signal detection")
    p_pre.add_argument("--force", action="store_true")
    p_pre.add_argument("--today", help="YYYY-MM, for reproducible date maths")
    p_pre.set_defaults(func=cmd_precheck)

    p_pending = sub.add_parser("pending", help="list candidates still needing judgment")
    p_pending.add_argument("--full", action="store_true", help="re-judge everyone")
    p_pending.set_defaults(func=cmd_pending)

    p_rank = sub.add_parser("rank", help="rank, cut, and update the ledger")
    p_rank.add_argument("--prepare", action="store_true")
    p_rank.add_argument("--finalize", action="store_true")
    p_rank.add_argument("--calibration", help="JSON file with {order: [...], note: str}")
    p_rank.add_argument("--run-id", dest="run_id")
    p_rank.set_defaults(func=cmd_rank)

    p_report = sub.add_parser("report", help="render HTML, Markdown, CSV")
    p_report.add_argument("--run-id", dest="run_id")
    p_report.set_defaults(func=cmd_report)

    p_run = sub.add_parser("run", help="fetch, parse, precheck, then list pending")
    p_run.add_argument("--source", choices=("trakstar", "folder"), default="trakstar")
    p_run.add_argument("--path")
    p_run.add_argument("--csv")
    p_run.add_argument("--force", action="store_true")
    p_run.add_argument("--full", action="store_true")
    p_run.add_argument("--today")
    p_run.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Defaults for flags a given subcommand does not define.
    for name, default in (
        ("today", None), ("force", False), ("full", False), ("run_id", None),
        ("calibration", None), ("prepare", False), ("finalize", False),
        ("source", "trakstar"), ("path", None), ("csv", None), ("out", None),
    ):
        if not hasattr(args, name):
            setattr(args, name, default)

    try:
        return int(args.func(args))
    except PermissionError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_AUTH
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
```

`screen/__main__.py`:

```python
from screen.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS — 11 tests

- [ ] **Step 5: Commit**

```bash
git add screen/cli.py screen/__main__.py tests/test_cli.py
git commit -m "feat: CLI for fetch, parse, precheck, pending, rank, report"
```

---

### Task 16: Judge prompts

**Files:**
- Create: `roles/fde/redflag_prompt.md`, `roles/fde/fit_prompt.md`
- Test: `tests/test_prompts.py`

**Interfaces:**
- Consumes: `screen.config.RoleConfig.redflag_prompt()`, `fit_prompt()`.
- Produces: two prompt files. Each contains `{{REDACTED_CV}}` and `{{PRECHECKS}}` placeholders the skill substitutes; the fit prompt also contains `{{REDFLAG_RESULT}}`.

- [ ] **Step 1: Write the failing tests**

`tests/test_prompts.py`:

```python
from pathlib import Path

from screen.config import load_role

CFG = load_role(Path(__file__).resolve().parents[1] / "roles" / "fde")


def test_redflag_prompt_has_placeholders():
    text = CFG.redflag_prompt()
    assert "{{REDACTED_CV}}" in text
    assert "{{PRECHECKS}}" in text


def test_redflag_prompt_defines_all_three_tiers():
    text = CFG.redflag_prompt().lower()
    for tier in ("tier 1", "tier 2", "tier 3"):
        assert tier in text


def test_redflag_prompt_requires_verbatim_quotes():
    text = CFG.redflag_prompt().lower()
    assert "verbatim" in text
    assert "quote" in text


def test_redflag_prompt_protects_polished_but_specific_writing():
    text = CFG.redflag_prompt().lower()
    assert "tier 3" in text
    assert "do not" in text


def test_fit_prompt_has_placeholders_including_redflag_result():
    text = CFG.fit_prompt()
    for token in ("{{REDACTED_CV}}", "{{PRECHECKS}}", "{{REDFLAG_RESULT}}"):
        assert token in text


def test_fit_prompt_lists_every_criterion_with_its_max():
    text = CFG.fit_prompt()
    for c in CFG.criteria:
        assert c.key in text
        assert str(c.max) in text


def test_fit_prompt_states_the_anchors():
    text = CFG.fit_prompt().lower()
    for phrase in ("no evidence", "one concrete", "sustained"):
        assert phrase in text


def test_fit_prompt_carries_the_fairness_rule():
    text = CFG.fit_prompt().lower()
    for banned in ("nationality", "gender", "age"):
        assert banned in text
    assert "prestige" in text or "institution" in text


def test_fit_prompt_caps_the_bonus():
    assert str(CFG.bonus_max) in CFG.fit_prompt()


def test_both_prompts_demand_json_only():
    for text in (CFG.redflag_prompt(), CFG.fit_prompt()):
        lowered = text.lower()
        assert "json" in lowered
        assert "only" in lowered
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_prompts.py -v`
Expected: FAIL — `FileNotFoundError: roles/fde/redflag_prompt.md`

- [ ] **Step 3: Write `roles/fde/redflag_prompt.md`**

```markdown
# Pass A — RedFlag

You are reviewing one CV for problems. Your only job in this pass is to find
what is wrong with it. Do not score it and do not judge whether the candidate is
qualified — a later pass does that.

The CV has been redacted: names, emails, phone numbers, addresses, and school
names appear as `[NAME]`, `[EMAIL]`, `[PHONE]`, `[LOCATION]`, `[SCHOOL]`. That
is expected. Never treat a redaction token as a problem with the CV.

## What to look for

### Tier 1 — evidence nobody edited this CV before sending it

- **Wrong company or role.** The CV or summary names a different employer or
  position than the one being applied for (Forward Deployed Engineer at Anduin).
  Example: "excited to join Palantir as a Solutions Architect".
- **Summary contradicts the body.** The summary claims years of experience, a
  domain, or a seniority the experience section does not support. A claim of
  "10 years in fintech" over three years of unrelated work is Tier 1.

Placeholder text and duplicated bullets are already detected mechanically — do
not re-report them; they appear in the prechecks below.

### Tier 2 — strong signals of generated text nobody personalised

- **Uniform bullet template.** Nearly every bullet follows the same shape —
  power verb, vague task, round percentage — with no system, tool, client, or
  scale named.
- **Generic summary.** A summary that would fit any engineering job, or one that
  echoes the job description's keywords in near-identical phrasing or order.
- **Skills without evidence.** A long skills list whose items appear nowhere in
  the experience section.
- **No concrete nouns.** No product names, technologies, versions, scale
  figures, client types, or named tools anywhere in the CV.

### Tier 3 — not a problem, do not report

Polished, well-edited, obviously AI-assisted writing that is nonetheless
**specific and internally consistent** is fine. Many strong candidates use AI to
edit. Perfect grammar is not a defect. If the content is concrete and the claims
hang together, report nothing.

## Other things worth flagging (Tier 2 unless clearly severe)

- Seniority mismatch: language and scope far above or below the stated years.
- Timeline problems the dates alone do not explain, such as overlapping
  full-time roles at different employers.

## Rules

1. **Every flag must quote the CV verbatim.** Copy the exact text, unmodified,
   from the CV below. A flag whose quote cannot be found in the CV is discarded.
   If you cannot quote it, do not flag it.
2. When uncertain whether something is Tier 1 or Tier 2, choose **Tier 2**.
3. When uncertain whether something is a problem at all, do not flag it. A false
   elimination costs the company a good engineer.
4. Judge the content, never the person. Ignore anything suggesting nationality,
   gender, age, or institution prestige.

## Also estimate

How many years of **software engineering** experience the CV supports, and your
confidence (`high`, `medium`, `low`). The mechanical date maths is in the
prechecks; you are the fallback when those dates were unclear, and you are the
one who can tell engineering work from adjacent work.

## Prechecks (already computed — do not re-derive)

```json
{{PRECHECKS}}
```

## The CV

```markdown
{{REDACTED_CV}}
```

## Output

Return **only** this JSON object, with no prose before or after it:

```json
{
  "flags": [
    {"tier": 1, "kind": "wrong_company", "quote": "exact text from the CV", "explanation": "one sentence"}
  ],
  "years_experience_estimate": {"value": 7, "confidence": "high"}
}
```

Use these `kind` values where they apply: `wrong_company`,
`summary_contradicts_body`, `uniform_bullet_template`, `generic_summary`,
`skills_without_evidence`, `no_concrete_nouns`, `seniority_mismatch`,
`timeline_problem`. Use a short snake_case string of your own if none fit.
```

- [ ] **Step 4: Write `roles/fde/fit_prompt.md`**

```markdown
# Pass B — Fit

Score this CV against the Forward Deployed Engineer role. The RedFlag pass has
already found the problems — they are given below, and you must not re-derive
them. Score the evidence that is present.

The CV is redacted: `[NAME]`, `[EMAIL]`, `[PHONE]`, `[LOCATION]`, `[SCHOOL]`.
That is expected.

## The role, in one paragraph

A technical solutioning role. The engineer works directly with investment firms,
law firms, and hedge funds to scope and build integrations between the client's
systems and Anduin's platform; explores client CRMs and data models; implements
subscription-form digitisation logic; and turns one-off client work into
platform capabilities. It demands 5+ years of production engineering, hands-on
breadth across integration domains (databases, SFTP, SMTP, enterprise SSO), real
client-facing experience, and the communication to hold both an engineering and
a client conversation. Fully remote, US-based, collaborating with teams in
Vietnam.

## Criteria

Score each independently. The maximum is in brackets.

- **`production_ownership` [25]** — Shipped and owned production systems
  independently. Named systems, scale figures, stack, on-call or incident
  ownership. Evidence of making sound decisions under pressure.
- **`integration_breadth` [20]** — Hands-on across *several* integration
  domains: database management systems, SFTP, SMTP or email pipelines,
  enterprise SSO (SAML, OIDC, Okta, Azure AD), REST and webhook APIs, CRMs
  (Salesforce, HubSpot, DealCloud), data-model design. Breadth matters more than
  depth in any one.
- **`client_solutioning` [20]** — Led discovery or scoping with external
  clients, translated technical concepts for non-engineers, handled escalations
  professionally. A prior FDE, solutions engineering, implementation, or
  consulting role is strong evidence.
- **`communication_product` [15]** — Trade-off reasoning visible in the writing.
  Turned client-specific work into reusable capability. The CV itself is a
  writing sample for a client-facing role: clarity counts, verbosity does not.
- **`domain` [10]** — Fintech, private markets, enterprise SaaS, document
  digitisation, OCR, or forms-processing experience.
- **`ai_tooling` [5]** — Built internal automation, agents, or LLM tooling with
  a real outcome. "Familiar with ChatGPT" earns nothing.
- **`distributed_collab` [5]** — Worked across time zones, especially with
  offshore teams in Vietnam or similar.

## Anchors

Apply these as a fraction of each criterion's maximum:

- **0.0** — no evidence at all
- **0.25** — claimed, with no specifics
- **0.5** — one concrete, specific example
- **0.75** — several concrete examples with scale or outcomes
- **1.0** — sustained, owned, quantified, unmistakably senior

Round to the nearest whole point. Do not inflate: 0.5 for one solid example is
the correct score, not a penalty.

## Bonus

Up to **{{BONUS_MAX}}** points for evidence that is exceptional *for this role
specifically* — an integration platform reused across many clients, a
Palantir-style forward-deployed background, an early employee who scaled client
delivery. Justify it in one sentence or award nothing.

## Rules

1. **Every score above 0 must quote the CV verbatim.** Copy the exact text.
   A score whose quote is not found in the CV is zeroed automatically, so quote
   carefully. Score 0 takes `"quote": null`.
2. Score only what the CV supports. Do not infer skills from job titles, and do
   not credit a technology merely because it appears in a skills list.
3. **Fairness:** ignore and never weigh nationality, gender, age, or the
   prestige of any institution or employer. Score the work, not the logo.
4. Do not re-litigate the RedFlag findings; penalties are applied outside this
   pass.

## RedFlag result

```json
{{REDFLAG_RESULT}}
```

## Prechecks

```json
{{PRECHECKS}}
```

## The CV

```markdown
{{REDACTED_CV}}
```

## Output

Return **only** this JSON object, no prose:

```json
{
  "scores": {
    "production_ownership": {"score": 18, "quote": "exact CV text", "rationale": "one sentence"},
    "integration_breadth":  {"score": 12, "quote": "exact CV text", "rationale": "one sentence"},
    "client_solutioning":   {"score": 15, "quote": "exact CV text", "rationale": "one sentence"},
    "communication_product":{"score": 9,  "quote": "exact CV text", "rationale": "one sentence"},
    "domain":               {"score": 4,  "quote": "exact CV text", "rationale": "one sentence"},
    "ai_tooling":           {"score": 2,  "quote": "exact CV text", "rationale": "one sentence"},
    "distributed_collab":   {"score": 0,  "quote": null, "rationale": "no evidence"}
  },
  "bonus": {"points": 0, "justification": null},
  "summary": "Two or three sentences: what this candidate is strong at, and the main gap."
}
```
```

- [ ] **Step 5: Substitute the bonus cap**

The fit prompt contains `{{BONUS_MAX}}`, which is config rather than per-candidate data. Replace it with the literal value from `role.json` so the prompt file needs no templating at load time:

```bash
cd /Users/hung/Coding/Odin/WorkSpace/CVsScanningAgent
python3 - <<'PY'
import json
from pathlib import Path
cfg = json.loads(Path("roles/fde/role.json").read_text())
p = Path("roles/fde/fit_prompt.md")
p.write_text(p.read_text().replace("{{BONUS_MAX}}", str(cfg["bonus_max"])))
print("bonus_max substituted:", cfg["bonus_max"])
PY
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_prompts.py -v`
Expected: PASS — 10 tests

- [ ] **Step 7: Commit**

```bash
git add roles/fde/redflag_prompt.md roles/fde/fit_prompt.md tests/test_prompts.py
git commit -m "feat: RedFlag and Fit judge prompts"
```

---

### Task 17: The skill, the scheduler, and an end-to-end run

**Files:**
- Create: `.claude/skills/screen-cvs/SKILL.md`, `scripts/install-schedule.sh`, `README.md`
- Test: `tests/test_end_to_end.py`

**Interfaces:**
- Consumes: the CLI from Task 15, the prompts from Task 16.
- Produces: `/screen-cvs` skill; `scripts/install-schedule.sh`; an end-to-end test proving the fixture pool produces a correct report with fake verdicts.

- [ ] **Step 1: Write the failing end-to-end test**

`tests/test_end_to_end.py`:

```python
"""End-to-end run over the fixture pool, with the judge stubbed.

This is the test that proves the pieces fit: five CVs in, a report and ledger
out, with the placeholder CV and the shared-template pair eliminated and the
strongest candidate shortlisted.
"""

import csv
import json
import sys
from pathlib import Path

import pytest

from screen.cli import main
from screen.config import load_role
from screen.ledger import load_ledger
from screen.paths import Paths
from screen.verdict import validate_verdict, verdict_key, write_verdict

sys.path.insert(0, str(Path(__file__).parent))
from fixtures.make_fixtures import build_all  # noqa: E402

ROLE = Path(__file__).resolve().parents[1] / "roles" / "fde"
CFG = load_role(ROLE)
OPENING = "704353"

# Which fixture each applicant submitted, and how strong the stub judge finds them.
POOL = [
    ("Alex Morgan", "clean", 0.85),
    ("Sam Rivera", "four_year", 0.55),
    ("Jordan Blake", "template_a", 0.40),
    ("Riley Chen", "template_b", 0.40),
    ("Pat Placeholder", "placeholder", 0.30),
    ("Casey Hidden", "hidden_text", 0.80),
    ("Dana Scan", "scanned", 0.00),
]


@pytest.fixture
def project(tmp_path):
    pdfs = build_all(tmp_path / "pdfs")
    src = tmp_path / "cvs"
    src.mkdir()
    for name, key, _ in POOL:
        (src / f"{name}.pdf").write_bytes(pdfs[key].read_bytes())

    csv_path = tmp_path / "export.csv"
    rows = ["Name,Email,LinkedIn,Location"]
    for name, key, _ in POOL:
        # Everyone has a LinkedIn except Pat, so the -8 penalty is exercised.
        linkedin = "" if name.startswith("Pat") else f"https://linkedin.com/in/{name.split()[0].lower()}"
        rows.append(f"{name},{name.split()[0].lower()}@example.com,{linkedin},\"Boston, MA\"")
    csv_path.write_text("\n".join(rows) + "\n")

    return tmp_path / "proj", src, csv_path


def _args(root, *rest):
    return ["--root", str(root), "--opening", OPENING, "--role", str(ROLE), *rest]


def _stub_judge(root, ids):
    """Score each candidate proportionally to their POOL strength."""
    paths = Paths(root=root, opening_id=OPENING)
    candidates = {int(c["id"]): c for c in json.loads(paths.candidates_json.read_text())}
    strength_by_name = {name: strength for name, _, strength in POOL}

    for cid in ids:
        candidate = candidates[cid]
        name = f"{candidate['first_name']} {candidate['last_name']}".strip()
        strength = strength_by_name.get(name, 0.5)

        precheck = json.loads((paths.prechecks / f"{cid}.json").read_text())
        redacted = (paths.redacted / f"{cid}.md").read_text()
        quote = next(
            (l.strip("-* ").strip() for l in redacted.splitlines() if l.strip().startswith(("-", "*"))),
            None,
        )
        scores = {}
        for c in CFG.criteria:
            score = round(c.max * strength) if quote else 0
            scores[c.key] = {
                "score": score,
                "quote": quote if score > 0 else None,
                "rationale": "stub",
            }
        raw = {
            "candidate_id": cid,
            "redflag": {"flags": [], "years_experience_estimate": {"value": 8, "confidence": "high"}},
            "fit": {"scores": scores, "bonus": {"points": 0, "justification": None}, "summary": f"{name} stub summary."},
        }
        verdict = validate_verdict(raw, CFG, redacted)
        verdict["verdict_key"] = verdict_key(redacted, precheck, CFG.rubric_version)
        write_verdict(verdict, paths.verdicts / f"{cid}.json")


def _full_run(root, src, csv_path, run_id="2026-08-28T0800"):
    assert main(_args(root, "fetch", "--source", "folder", "--path", str(src), "--csv", str(csv_path))) == 0
    assert main(_args(root, "parse")) == 0
    assert main(_args(root, "precheck", "--today", "2026-08")) == 0

    pending_file = root / "pending.json"
    main(_args(root, "pending", "--out", str(pending_file)))
    pending = json.loads(pending_file.read_text())["pending"]
    _stub_judge(root, pending)

    assert main(_args(root, "rank", "--finalize", "--run-id", run_id)) == 0
    assert main(_args(root, "report", "--run-id", run_id)) == 0
    return Paths(root=root, opening_id=OPENING)


def _name_status(paths):
    candidates = {int(c["id"]): c for c in json.loads(paths.candidates_json.read_text())}
    ledger = load_ledger(paths.ledger_json)
    return {
        f"{candidates[cid]['first_name']} {candidates[cid]['last_name']}".strip(): entry.status
        for cid, entry in ledger.items()
    }


def test_end_to_end_produces_report_and_ledger(project):
    root, src, csv_path = project
    paths = _full_run(root, src, csv_path)

    assert (paths.report / "2026-08-28T0800.html").exists()
    assert (paths.report / "latest.html").exists()
    assert (paths.report / "latest.md").exists()
    assert paths.ledger_json.exists()
    assert list(paths.runs.glob("*.json"))


def test_placeholder_cv_is_eliminated(project):
    root, src, csv_path = project
    statuses = _name_status(_full_run(root, src, csv_path))
    assert statuses["Pat Placeholder"] == "gated"


def test_hidden_text_cv_is_eliminated_despite_strong_content(project):
    root, src, csv_path = project
    statuses = _name_status(_full_run(root, src, csv_path))
    # This CV has the strongest text in the pool but stuffs hidden keywords.
    assert statuses["Casey Hidden"] == "gated"


def test_shared_template_pair_is_eliminated(project):
    root, src, csv_path = project
    statuses = _name_status(_full_run(root, src, csv_path))
    assert statuses["Jordan Blake"] == "gated"
    assert statuses["Riley Chen"] == "gated"


def test_scanned_cv_needs_manual_review_not_rejection(project):
    root, src, csv_path = project
    statuses = _name_status(_full_run(root, src, csv_path))
    assert statuses["Dana Scan"] == "needs_review"


def test_cap_is_twenty_percent_of_the_whole_pool(project):
    root, src, csv_path = project
    paths = _full_run(root, src, csv_path)
    record = json.loads(next(paths.runs.glob("*.cut.json")).read_text())
    assert record["cut"]["pool_size"] == 7
    assert record["cut"]["cap"] == 1


def test_strongest_clean_candidate_is_shortlisted(project):
    root, src, csv_path = project
    statuses = _name_status(_full_run(root, src, csv_path))
    assert statuses["Alex Morgan"] == "accepted"


def test_report_html_never_contains_pii_of_gated_candidates_cv_text(project):
    root, src, csv_path = project
    paths = _full_run(root, src, csv_path)
    html = (paths.report / "latest.html").read_text()
    # Names appear (the report re-attaches identity) but CV emails must not.
    assert "Alex Morgan" in html
    assert "alex.morgan@example.com" not in html


def test_redacted_markdown_contains_no_candidate_email(project):
    root, src, csv_path = project
    paths = _full_run(root, src, csv_path)
    from screen.redact import assert_clean

    for md in paths.redacted.glob("*.md"):
        assert assert_clean(md.read_text()) == [], md.name


def test_second_run_judges_nobody_new(project):
    root, src, csv_path = project
    _full_run(root, src, csv_path)

    pending_file = root / "pending2.json"
    assert main(_args(root, "fetch", "--source", "folder", "--path", str(src), "--csv", str(csv_path))) == 0
    assert main(_args(root, "parse")) == 0
    assert main(_args(root, "precheck", "--today", "2026-08")) == 0
    assert main(_args(root, "pending", "--out", str(pending_file))) == 0
    assert json.loads(pending_file.read_text())["pending"] == []


def test_accepted_candidate_survives_a_stronger_second_wave(project, tmp_path):
    root, src, csv_path = project
    _full_run(root, src, csv_path)
    before = _name_status(Paths(root=root, opening_id=OPENING))
    assert before["Alex Morgan"] == "accepted"

    # Three strong newcomers arrive; the cap grows to 2 but Alex must stay in.
    pdfs = build_all(tmp_path / "pdfs2")
    for extra in ("Blake Strong", "Quinn Strong", "Reese Strong"):
        (src / f"{extra}.pdf").write_bytes(pdfs["clean"].read_bytes())
    rows = csv_path.read_text().rstrip().splitlines()
    for extra in ("Blake Strong", "Quinn Strong", "Reese Strong"):
        rows.append(f"{extra},{extra.split()[0].lower()}@example.com,https://linkedin.com/in/{extra.split()[0].lower()},\"Boston, MA\"")
    csv_path.write_text("\n".join(rows) + "\n")

    main(_args(root, "fetch", "--source", "folder", "--path", str(src), "--csv", str(csv_path)))
    main(_args(root, "parse"))
    main(_args(root, "precheck", "--today", "2026-08"))
    pending_file = root / "pending3.json"
    main(_args(root, "pending", "--out", str(pending_file)))
    _stub_judge(root, json.loads(pending_file.read_text())["pending"])
    main(_args(root, "rank", "--finalize", "--run-id", "2026-08-29T0800"))
    main(_args(root, "report", "--run-id", "2026-08-29T0800"))

    after = _name_status(Paths(root=root, opening_id=OPENING))
    assert after["Alex Morgan"] == "accepted"


def test_csv_shortlist_matches_the_ledger(project):
    root, src, csv_path = project
    paths = _full_run(root, src, csv_path)
    with (paths.report / "2026-08-28T0800-shortlist.csv").open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    accepted = [n for n, s in _name_status(paths).items() if s == "accepted"]
    assert sorted(r["name"] for r in rows) == sorted(accepted)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_end_to_end.py -v`
Expected: FAIL — some assertions about which candidates are gated will fail until the whole chain works. Fix real defects surfaced here in the module that owns them; do not weaken these assertions, and do not special-case fixture names anywhere in `screen/`.

Note on the three "Strong" newcomers: they share the `clean.pdf` text, so the cross-pool duplicate check will gate them. That is correct behaviour and the test only asserts that Alex stays accepted.

- [ ] **Step 3: Write `.claude/skills/screen-cvs/SKILL.md`**

```markdown
---
name: screen-cvs
description: Screen Trakstar Hire applicants for an opening against the JD — fetch new CVs, run deterministic checks, judge each CV with a subagent, rank, apply the 20% cap, and write an HTML/Markdown/CSV report. Use when asked to screen CVs, review applicants, refresh the shortlist, or run the daily CV screening.
---

# Screen CVs

Runs the screening pipeline for one opening. Python does every deterministic
step; you do only the judging and the calibration.

**Never** invent scores, skip the quote requirement, or write to Trakstar. This
pipeline is report-only.

## 0. Setup

Work from the project root. Read `roles/fde/role.json` only if you need a number;
the CLI already applies it.

```bash
cd /Users/hung/Coding/Odin/WorkSpace/CVsScanningAgent
```

Default source is Trakstar. If `TRAKSTAR_API_KEY` is unset, fall back to
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

Read the window. These are the candidates straddging the accept line, with their
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
```

- [ ] **Step 4: Write `scripts/install-schedule.sh`**

```bash
#!/usr/bin/env bash
# Install (or refresh) the daily CV screening job.
#
# Usage: scripts/install-schedule.sh [HH] [MM]
# Default: 08:00 local. Re-running replaces the existing job.

set -euo pipefail

HOUR="${1:-8}"
MINUTE="${2:-0}"
LABEL="com.anduin.screen-cvs"
PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
CLAUDE_BIN="$(command -v claude || true)"

if [[ -z "$CLAUDE_BIN" ]]; then
  echo "error: 'claude' not found on PATH; install Claude Code first" >&2
  exit 1
fi

mkdir -p "$PROJECT/state/logs" "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>WorkingDirectory</key><string>$PROJECT</string>
  <key>ProgramArguments</key>
  <array>
    <string>$CLAUDE_BIN</string>
    <string>-p</string>
    <string>/screen-cvs</string>
    <string>--permission-mode</string>
    <string>acceptEdits</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>$HOUR</integer>
    <key>Minute</key><integer>$MINUTE</integer>
  </dict>
  <key>StandardOutPath</key><string>$PROJECT/state/logs/screen-cvs.out.log</string>
  <key>StandardErrorPath</key><string>$PROJECT/state/logs/screen-cvs.err.log</string>
  <key>RunAtLoad</key><false/>
</dict>
</plist>
PLIST_EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

printf 'Installed %s — runs daily at %02d:%02d\n' "$LABEL" "$HOUR" "$MINUTE"
echo "Logs:      $PROJECT/state/logs/"
echo "Run now:   launchctl start $LABEL"
echo "Remove:    launchctl unload $PLIST && rm $PLIST"
echo
echo "Note: .env must contain TRAKSTAR_API_KEY, and the job reads it via the skill."
```

Make it executable:

```bash
chmod +x scripts/install-schedule.sh
```

- [ ] **Step 5: Write `README.md`**

```markdown
# CV Screening Agent

Screens applicants for one Trakstar Hire opening against a job description,
eliminates carelessly AI-generated CVs, and produces a ranked shortlist capped at
20 % of the pool. Report only — it never writes to Trakstar.

Design: `docs/superpowers/specs/2026-08-27-cv-screening-agent-design.md`

## Setup

```bash
uv sync
cp .env.example .env      # then fill in TRAKSTAR_API_KEY (needs Super Admin)
```

## Run it

In Claude Code:

```
/screen-cvs
```

Or the deterministic stages alone:

```bash
uv run python -m screen run                              # fetch, parse, precheck, list pending
uv run python -m screen run --source folder --path ~/cvs # no API key needed
uv run python -m screen report --run-id <run>            # re-render a report
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

1. **Gates** eliminate: template placeholders, wrong company, duplicated bullets,
   bullets shared with another applicant, hidden text, under 4 years, explicit
   non-US with no authorisation.
2. **Penalties** subtract: no LinkedIn (−8), AI-slop signals (−5 each), shared
   template bullets (−10), 4–5 years (−10), no degree (−5), short stints (−5).
3. **Fit** scores 0–100 across seven criteria drawn from the JD, each needing a
   verbatim quote from the CV.
4. **Cap**: top 20 % of the whole pool. Decisions are sticky — an accepted
   candidate is never displaced by later applicants.

The judging model never sees names, emails, phones, addresses, or schools.

## Tests

```bash
uv run pytest
```

## Adding a role

Copy `roles/fde/` to `roles/<name>/`, edit `role.json` and the two prompts, then
pass `--role roles/<name>`.
```

- [ ] **Step 6: Run the end-to-end test and the full suite**

Run: `uv run pytest tests/test_end_to_end.py -v`
Expected: PASS — 12 tests

Run: `uv run pytest`
Expected: PASS — every test in the project

- [ ] **Step 7: Verify the skill loads and dry-run the folder path**

```bash
uv run python -m screen run --source folder --path tests/fixtures/pdfs --today "$(date +%Y-%m)" || true
ls -la .claude/skills/screen-cvs/SKILL.md
bash -n scripts/install-schedule.sh && echo "install-schedule.sh syntax OK"
```

The `screen run` line requires fixture PDFs on disk; generate them first with
`uv run python tests/fixtures/make_fixtures.py tests/fixtures/pdfs` if you want
to see a real report. Do not commit the generated PDFs (`data/` and generated
fixtures stay untracked; add `tests/fixtures/pdfs/` to `.gitignore` now).

- [ ] **Step 8: Commit**

```bash
echo "tests/fixtures/pdfs/" >> .gitignore
git add .claude/skills/screen-cvs/SKILL.md scripts/install-schedule.sh README.md \
        tests/test_end_to_end.py .gitignore
git commit -m "feat: screen-cvs skill, daily scheduler, end-to-end test, README"
```

---

## Verification checklist

Run after Task 17. Every line must pass before the pipeline touches real CVs.

- [ ] `uv run pytest` — all tests green
- [ ] `uv run python -m screen run --source folder --path <real CVs>` completes
- [ ] `/screen-cvs` in Claude Code produces `report/latest.html`
- [ ] Open the report: shortlist size equals `floor(0.20 × pool)`, every score
      shows a quote, every gate shows its evidence
- [ ] `grep -ril "@" data/*/redacted/ | head` finds no candidate email addresses
- [ ] Second consecutive run judges nobody (`pending: []`)
- [ ] `scripts/install-schedule.sh` installs, `launchctl start com.anduin.screen-cvs`
      produces a report, logs land in `state/logs/`
- [ ] `git status` shows no `data/`, `state/`, `report/`, or `.env` staged

## Spec coverage

| Spec section | Task(s) |
|---|---|
| §1 R1 20 % cap | 13 |
| §1 R2 careless-AI tiers | 4, 12, 16 |
| §1 R3 LinkedIn red flag | 6, 12 |
| §1 R4 JD match | 1 (rubric), 16 (prompt) |
| §1 R5 daily, no rework | 3, 9, 15, 17 |
| §1 R6 report only | 14 |
| §1 R7 Claude Code runtime | 16, 17 |
| §2 Trakstar API + folder fallback | 10 |
| §3 reference survey decisions | 4 (heuristics), 7 (redaction), 8 (cross-pool) |
| §4.1 pipeline stages | 3, 9, 10, 12, 13, 14, 15 |
| §4.2 judge blind to identity | 7, 16 |
| §4.3 two passes, schema, retry | 11, 16, 17 |
| §4.4 calibration window | 13, 15, 17 |
| §5.1 Tier 1/2/3 | 4, 12, 16 |
| §5.2 precheck payload | 9 |
| §5.3 gates and penalties | 12 |
| §5.4 fit rubric + anchors + tiebreak | 1, 13, 16 |
| §5.5 verdict schema | 11 |
| §6 sticky ledger, cap, quality floor | 13 |
| §7 idempotency and `--full` | 3, 9, 10, 15 |
| §8 report HTML/MD/CSV | 14 |
| §9 layout, skill flow, launchd | 15, 17 |
| §10 error handling | 9, 10, 11, 15 |
| §11 testing | every task; 17 for end-to-end |
| §12 Phase 2 hooks | 13 (`human_override`), 14 (run record) |

## Open items carried from the spec

| Item | Status |
|---|---|
| Trakstar API key (Super Admin) | Pending — build and verify with `--source folder`; Task 10 covers both paths |
| Degree: penalty vs hard gate | Penalty −5 + flag (`gates.degree_required: false` flips it) |
| Scheduled run hour | 08:00 local; `scripts/install-schedule.sh HH MM` overrides |
| DOCX CVs | `needs_review: unsupported_format`; a follow-up task can add conversion |
