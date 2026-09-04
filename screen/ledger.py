"""The sticky record of every screening decision.

Once a candidate is accepted they stay accepted: a later run that finds three
stronger applicants must not un-shortlist someone the team may already have
contacted. That promise lives here, and it is why the ledger refuses to load a
corrupt file rather than starting fresh.

Must not import from screen.rank -- rank imports this module, and a cycle
would break both.
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
