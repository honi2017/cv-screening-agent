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
from screen.rank import CutResult, assess, rank_and_cut
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
    from screen.parse import parse_pdf, sha256_file, write_parsed

    paths, _cfg = _context(args)
    candidates = _load_json(paths.candidates_json) or []
    parsed, failed = [], {}

    for candidate in candidates:
        cid = int(candidate["id"])
        # Resumes are saved under their real extension -- .pdf or .docx (about
        # 23% of the real pool is DOCX, REAL-DATA-ADDENDUM section D) -- so
        # resolve whichever exists rather than assuming .pdf.
        resume = next(
            (paths.resumes / f"{cid}{ext}" for ext in (".pdf", ".docx") if (paths.resumes / f"{cid}{ext}").exists()),
            None,
        )
        md_path = paths.parsed / f"{cid}.md"
        meta_path = paths.parsed / f"{cid}.meta.json"
        if resume is None:
            failed[cid] = "missing_resume"
            continue

        existing = _load_json(meta_path)
        if existing and existing.get("sha256") == sha256_file(resume) and md_path.exists():
            continue
        try:
            write_parsed(parse_pdf(resume), md_path, meta_path)
            parsed.append(cid)
        except Exception as exc:  # a broken resume must not stop the batch
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

    pending: list[int] = []
    cached: list[int] = []
    needs_review: dict[int, str] = {}
    # Count only candidates that were judged before (a verdict file already
    # existed) and are now pending again -- that is the honest signal that
    # the rubric moved, as opposed to a first-ever run where everything is
    # pending simply because nothing has been judged yet.
    previously_judged_now_pending = 0

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
            if existing is not None:
                previously_judged_now_pending += 1
        else:
            cached.append(cid)

    # A pool that was fully judged before and is now fully pending means the
    # rubric moved. Interactive runs can proceed with --full; a scheduled run
    # must not silently re-judge everyone. Gating on "previously judged"
    # candidates specifically (rather than on the ledger existing) means this
    # fires even before the first `rank` has ever run -- the ledger is a
    # downstream artifact and its absence must not mask a rubric bump.
    rubric_change = bool(
        previously_judged_now_pending > 1 and not cached and not args.full
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

    cut = CutResult(**state["cut"])

    # JSON stringifies dict keys, so needs_review_reasons comes back off disk
    # keyed by str; coerce back to int (mirrors screen.pool.load_pool_duplicates)
    # or the report's candidate-id lookups silently miss and the "needs manual
    # review" section -- the one section a human must act on -- names nobody.
    stored_reasons = state.get("needs_review_reasons")
    needs_review_reasons = (
        {int(k): v for k, v in stored_reasons.items()} if stored_reasons else needs_review
    )

    data = report_mod.ReportInput(
        run_id=run_id,
        cut=cut,
        assessments=assessments,
        candidates=candidates,
        prechecks=prechecks,
        verdicts=verdicts,
        ledger=ledger,
        needs_review=needs_review_reasons,
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

    # `--out` is a shared flag but every test (and every real invocation)
    # passes it *after* the subcommand name (e.g. `pending --out FILE`).
    # argparse hands remaining tokens to the chosen subparser once it sees
    # the subcommand, so `--out` must be registered on each subparser too --
    # registering it only on the top-level parser makes `pending --out FILE`
    # fail with "unrecognized arguments" before main() ever runs. A shared
    # parent parser keeps the definition (and its help text) in one place.
    out_parent = argparse.ArgumentParser(add_help=False)
    out_parent.add_argument("--out", help="also write this command's JSON output to a file")

    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser("fetch", parents=[out_parent], help="pull candidates and CV files")
    p_fetch.add_argument("--source", choices=("trakstar", "folder"), default="trakstar")
    p_fetch.add_argument("--path", help="folder of PDFs (with --source folder)")
    p_fetch.add_argument("--csv", help="optional Trakstar CSV export for LinkedIn/location")
    p_fetch.set_defaults(func=cmd_fetch)

    sub.add_parser("parse", parents=[out_parent], help="PDF/DOCX to markdown").set_defaults(
        func=cmd_parse
    )

    p_pre = sub.add_parser("precheck", parents=[out_parent], help="run deterministic signal detection")
    p_pre.add_argument("--force", action="store_true")
    p_pre.add_argument("--today", help="YYYY-MM, for reproducible date maths")
    p_pre.set_defaults(func=cmd_precheck)

    p_pending = sub.add_parser("pending", parents=[out_parent], help="list candidates still needing judgment")
    p_pending.add_argument("--full", action="store_true", help="re-judge everyone")
    p_pending.set_defaults(func=cmd_pending)

    p_rank = sub.add_parser("rank", parents=[out_parent], help="rank, cut, and update the ledger")
    p_rank.add_argument("--prepare", action="store_true")
    p_rank.add_argument("--finalize", action="store_true")
    p_rank.add_argument("--calibration", help="JSON file with {order: [...], note: str}")
    p_rank.add_argument("--run-id", dest="run_id")
    p_rank.set_defaults(func=cmd_rank)

    p_report = sub.add_parser("report", parents=[out_parent], help="render HTML, Markdown, CSV")
    p_report.add_argument("--run-id", dest="run_id")
    p_report.set_defaults(func=cmd_report)

    p_run = sub.add_parser("run", parents=[out_parent], help="fetch, parse, precheck, then list pending")
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
    except Exception as exc:  # unexpected error -> exit 1, per the documented contract
        print(f"unexpected error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
