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
