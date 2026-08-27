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
