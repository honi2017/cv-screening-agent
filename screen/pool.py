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
