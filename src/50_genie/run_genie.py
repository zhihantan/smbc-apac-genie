"""Phase 7 entry point - the 11 Genie spaces via REST (PLAN §9; DECISIONS D06, D42; genie/README.md).

For each genie/<slug>/space.yaml (or --only):
  build      YAML -> serialized_space v2 + space_spec.json, instructions.md, benchmarks.json,
             <slug>.geniespace.json (build_space_specs.py; ids stable, lists pre-sorted)
  gate       refresh the genie/_schema snapshot (DESCRIBE), fail when an asset column / measure lacks a comment
             or an entity-matching column exceeds the dictionary limits (metadata_gate.py)
  --create   deploy functions.sql, create or update OUR space (tracked in genie/<slug>/space_id, exact title),
             GET round-trip diff, benchmark catalogue -> ops.genie_benchmarks (create_spaces.py)
  --evaluate expected-SQL checks, eval-runs (fallback: Conversation API), grading, ops.genie_benchmarks,
             genie/<slug>/eval_report.json (run_benchmarks.py) - sequential, one space at a time
  --dry-run  read-only: build + gate + the diff against the live space (+ expected-SQL checks with --evaluate);
             writes nothing (no files, no functions, no spaces, no ops rows)
Never touches spaces not titled `APAC Genie - ...`, never changes permissions.

Run:  .venv/bin/python src/50_genie/run_genie.py --profile my-workspace --only early_warning_monitoring --create --evaluate
      .venv/bin/python src/50_genie/run_genie.py --build-only            # offline, all spaces
Exit: 0 ok, 1 gate / create / evaluation problems, 2 spec errors.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (str(REPO / "src"), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from smbc_genie_lib import genie as g  # noqa: E402
from smbc_genie_lib.config import default_warehouse_id, require_warehouse  # noqa: E402
from build_space_specs import build_all, build_one, load_inputs, summarize, write_artifacts  # noqa: E402

TAG = "[p07]"


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SMBC APAC Genie - phase 7: Genie spaces via REST")
    p.add_argument("--catalog", default=None)
    p.add_argument("--profile", default=None, help="CLI profile for local runs; omit inside a job")
    p.add_argument("--warehouse-id", default=None)
    p.add_argument("--only", "--spaces", dest="only", default="", help="comma-separated slugs ('all' = every space)")
    p.add_argument("--build-only", action="store_true", help="offline build of the artefacts; no workspace calls")
    p.add_argument("--create", action="store_true", help="deploy functions + create / update the space + round-trip")
    p.add_argument("--evaluate", action="store_true", help="expected-SQL checks + Genie evaluation -> ops.genie_benchmarks")
    p.add_argument("--dry-run", action="store_true", help="read-only: show what --create / --evaluate would do")
    p.add_argument("--eval-method", choices=["auto", "eval_runs", "conversation"], default="auto")
    p.add_argument("--label", default="", help="free text stored with the evaluation summary (e.g. iteration 2)")
    p.add_argument("--skip-gate", action="store_true", help="report gate problems but continue (development only)")
    p.add_argument("--no-smoke", action="store_true", help="skip the post-create smoke question (Conversation API)")
    for legacy in ("--scale", "--as-of", "--seed"):     # accepted for bundle-job compatibility, unused
        p.add_argument(legacy, default=None, help=argparse.SUPPRESS)
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)   # progress visible when redirected to a log
    except Exception:  # noqa: BLE001
        pass
    a = parse_args(argv)
    shared = g.load_shared()
    catalog = a.catalog or shared["catalog"]
    warehouse = a.warehouse_id or shared["warehouse_id"] or default_warehouse_id()
    slugs = [s for s in a.only.split(",") if s and s != "all"] or g.space_slugs()
    missing = [s for s in slugs if not (g.GENIE_DIR / s / "space.yaml").exists()]
    if missing:
        print(f"{TAG} no space.yaml for: {', '.join(missing)}")
        slugs = [s for s in slugs if s not in missing]
    if a.build_only:
        built = build_all(slugs, shared, catalog, warehouse)
        return 2 if any(not b.ok for b in built.values()) or len(built) < len(slugs) else 0

    from genie_ws import Workspace, first_line
    from metadata_gate import function_problems, gate, snapshot
    from create_spaces import SpaceSafetyError, create_or_update, tracked_id
    from run_benchmarks import evaluate, run_sql_checks, write_ops

    ws = Workspace(require_warehouse(warehouse, TAG), a.profile)
    rc = 0
    for slug in slugs:
        print(f"{TAG} ==== {slug}")
        try:
            src, answers = load_inputs(slug)
        except g.GenieSpecError as e:
            print(f"{TAG}   ERROR {e}")
            rc = max(rc, 2)
            continue
        idents = [g.render(d.get("identifier", ""), {"catalog": catalog}).lower() for d in src.raw.get("data_sources") or []]
        try:
            schema = snapshot(ws, idents, write=not a.dry_run)
        except Exception as e:  # noqa: BLE001
            print(f"{TAG}   ERROR describing assets: {first_line(e)}")
            rc = max(rc, 1)
            continue
        built = build_one(src, shared, schema, answers, catalog, warehouse)
        for w in built.warnings:
            print(f"{TAG}   warn  {w}")
        if not built.ok:
            for e in built.errors:
                print(f"{TAG}   ERROR {e}")
            rc = max(rc, 2)
            continue
        print(f"{TAG}   build: {summarize(built)}")
        problems = gate(ws, built, schema, shared["limits"])
        if problems:
            for x in problems:
                print(f"{TAG}   GATE  {x}")
            rc = max(rc, 1)
            if not a.skip_gate:
                print(f"{TAG}   gate FAILED - not creating / evaluating {slug}")
                continue
        else:
            print(f"{TAG}   gate: PASS")
        if not a.dry_run:
            changed = write_artifacts(built)
            print(f"{TAG}   artefacts: {', '.join(changed) or 'unchanged'}")
        sid = tracked_id(slug)
        if a.create or (a.dry_run and not a.evaluate):
            try:
                res = create_or_update(ws, built, dry_run=a.dry_run, smoke=not a.no_smoke)
            except SpaceSafetyError as e:
                print(f"{TAG}   REFUSED: {e}")
                rc = max(rc, 1)
                continue
            sid = res.get("space_id")
            probs = list(res["problems"])
            if not a.dry_run:
                probs += function_problems(ws, built, catalog)
                probs += [f"round-trip: {d}" for d in res.get("diffs", [])]
                write_ops(ws, catalog, built, sid)
            for x in probs:
                print(f"{TAG}   PROBLEM {x}")
            rc = max(rc, 1 if probs else 0)
        if a.evaluate:
            if a.dry_run:
                res = run_sql_checks(ws, built)
                ok = sum(1 for r in res.values() if r["pass"])
                print(f"{TAG}   [dry-run] expected SQL ok {ok}/{len(res)}; would evaluate space {sid or '(none yet)'}")
                rc = max(rc, 0 if ok == len(res) else 1)
                continue
            if not sid:
                print(f"{TAG}   ERROR no tracked space for {slug} - run --create first")
                rc = max(rc, 1)
                continue
            summary = evaluate(ws, catalog, built, sid, a.eval_method, a.label)
            rc = max(rc, 0 if summary["pass_rate"] >= 0.85 else 1)
    return rc


if __name__ == "__main__":
    sys.exit(main())
