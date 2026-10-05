"""Phase 6 - Unity Catalog metric views (PLAN §8 / §10; DECISIONS D14, D16, D31-D35).

Builds every metric view declared in metrics/<view>.yaml (framework: smbc_genie_lib.metrics; how to add a
view: metrics/README.md). Per view: expand the shared blocks (metrics/_blocks: calendar, client /
client_group), validate the spec offline, then on the SQL warehouse

  1. CREATE OR REPLACE VIEW <catalog>.metrics.<v> WITH METRICS LANGUAGE YAML COMMENT '...' AS $$ ... $$
     (runner-only keys stripped) + smbc_ tags (grants are schema-level, D34);
  2. validate: SELECT <2-3 dims>, MEASURE(<every measure>) ... GROUP BY ALL runs, plus one query selecting
     every dimension (catches a broken dimension expression);
  3. reconcile every `reconcile:` entry (>= 2 per view): MEASURE() vs direct gold SQL, 0.01% relative;
  4. coverage: every column has a comment and a display_name and every measure a format, read back from
     DESCRIBE TABLE EXTENDED ... AS JSON (what Unity Catalog / Genie actually see);

then regenerates metrics/_glossary.md, runs the must-answer query files metrics/_answers/<space>.sql (row
counts written back into the `-- rows:` lines; they become Genie benchmarks) and logs every step to
ops.build_run_log (phase p06_metrics). Views whose gold sources do not exist yet are pending (skipped), so
the runner is safe to re-run while gold fills in.

Run:  .venv/bin/python src/40_metrics/run_metrics.py --profile my-workspace
      [--only mv_a,mv_b] [--space tb_cash_payments_liquidity] [--dry-run] [--list] [--parallel 4]
      [--skip-reconcile] [--skip-answers] [--no-glossary] [--skip-invalid] [--strict]
Exit: 0 ok, 1 a build / validation / reconciliation / coverage / answer failure, 2 spec error, 3 pending (--strict).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from smbc_genie_lib import metrics as mx  # noqa: E402
from smbc_genie_lib.config import default_warehouse_id, load_config, require_warehouse  # noqa: E402

TAG = "[p06]"
PHASE = "p06_metrics"
DEFAULT_WAREHOUSE = default_warehouse_id("")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SMBC APAC Genie - phase 6: metric views, glossary, must-answer queries")
    p.add_argument("--catalog", default="smbc_genie")
    p.add_argument("--profile", default=None, help="CLI profile for local runs; omit inside a job")
    p.add_argument("--warehouse-id", default=DEFAULT_WAREHOUSE)
    p.add_argument("--only", default="", help="comma-separated views (mv_...)")
    p.add_argument("--space", default="", help="comma-separated Genie space slugs (brief Appendix D)")
    p.add_argument("--parallel", type=int, default=4, help="views built concurrently")
    p.add_argument("--list", action="store_true", help="print the 43 planned views and their spec status, then exit")
    p.add_argument("--dry-run", action="store_true", help="print the rendered DDL (and write the glossary); no workspace")
    p.add_argument("--skip-reconcile", action="store_true")
    p.add_argument("--skip-answers", action="store_true")
    p.add_argument("--no-glossary", action="store_true")
    p.add_argument("--skip-invalid", action="store_true",
                   help="skip (and report) invalid view specs instead of stopping - for parallel development")
    p.add_argument("--strict", action="store_true", help="exit 3 when views are pending")
    return p.parse_args(argv)


class Warehouse:
    """Thread-local SqlRunner wrapper (one WorkspaceClient per worker thread)."""

    def __init__(self, warehouse_id: str, profile: Optional[str]):
        self.warehouse_id, self.profile, self._local = warehouse_id, profile, threading.local()

    def _runner(self):
        if not hasattr(self._local, "r"):
            from smbc_genie_lib.sql_runner import SqlRunner
            self._local.r = SqlRunner(self.warehouse_id, profile=self.profile)
        return self._local.r

    def execute(self, sql: str):
        return self._runner().execute(sql)

    def rows(self, sql: str) -> List[List[Optional[str]]]:
        resp = self.execute(sql)
        return list(resp.result.data_array or []) if resp.result else []

    def count(self, sql: str) -> int:
        resp = self.execute(sql)
        man = getattr(resp, "manifest", None)
        if man is not None and getattr(man, "total_row_count", None) is not None:
            return int(man.total_row_count)
        return len(resp.result.data_array or []) if resp.result else 0


class Build:
    def __init__(self, a: argparse.Namespace):
        self.a = a
        self.cfg = load_config()
        self.params = mx.build_params(self.cfg, a.catalog)
        cat = mx.load_views(strict=not a.skip_invalid)
        self.views, self.blocks, self.spec_errors = cat.views, cat.blocks, cat.errors
        for err in self.spec_errors:
            print(f"{TAG} INVALID spec skipped: {err[:600]}")
        self.run_id = f"p06-{dt.datetime.now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
        self.log: List[Tuple] = []
        self.status: Dict[str, str] = {}          # view -> built | pending | failed
        self.summary: Dict[str, Dict[str, object]] = {}
        self.problems: List[str] = []
        self.lock = threading.Lock()
        self.wh: Optional[Warehouse] = None
        self.gold_tables: Set[str] = set()
        self.metric_views: Set[str] = set()

    # ---- helpers --------------------------------------------------------------------------------------
    def render(self, text: str) -> str:
        return mx.substitute(text, self.params)

    def record(self, step: str, obj: str, status: str, msg: str = "", rows: Optional[int] = None,
               t0: Optional[float] = None) -> None:
        now = time.time()
        with self.lock:
            self.log.append((step, obj, status, msg, rows, t0 or now, now))

    def problem(self, view: str, msg: str) -> None:
        with self.lock:
            self.problems.append(f"{view}: {msg}")

    def refresh_existing(self) -> None:
        self.gold_tables = {str(r[0]).lower() for r in self.wh.rows(
            f"SELECT table_name FROM {self.a.catalog}.information_schema.tables WHERE table_schema = 'gold'")}
        self.metric_views = {str(r[0]).lower() for r in self.wh.rows(
            f"SELECT table_name FROM {self.a.catalog}.information_schema.tables WHERE table_schema = '{mx.SCHEMA}'")}

    def plan(self) -> List[str]:
        only = [s.strip() for s in self.a.only.split(",") if s.strip()]
        space = [s.strip() for s in self.a.space.split(",") if s.strip()]
        return mx.select(self.views, only, space)

    # ---- per view ----------------------------------------------------------------------------------
    def build_one(self, name: str) -> None:
        spec, cat = self.views[name], self.a.catalog
        fqn, info = spec.fqn(cat), {"space": spec.space}
        self.summary[name] = info
        missing = sorted(f"{s}.{t}" for s, t in mx.sql_refs(spec) if s == "gold" and t not in self.gold_tables)
        if missing:
            with self.lock:
                self.status[name] = "pending"
            info["status"] = f"pending (missing {', '.join(missing)})"
            self.record("create", fqn, "skipped", f"pending: missing {', '.join(missing)}")
            print(f"{TAG} pending {name:34} (missing {', '.join(missing)})")
            return
        t0 = time.time()
        try:
            self.wh.execute(mx.render_ddl(spec, self.params))
            self.record("create", fqn, "ok", f"{len(spec.field_names)} dimensions, {len(spec.measure_names)} measures, "
                        f"{len(spec.joins)} joins", None, t0)
        except Exception as e:  # noqa: BLE001
            msg = str(e).replace("\n", " ")
            with self.lock:
                self.status[name] = "failed"
            info["status"] = "failed (create)"
            self.record("create", fqn, "error", msg[:1500], None, t0)
            self.problem(name, f"create failed: {msg[:400]}")
            print(f"{TAG} FAILED  {name}: {msg[:400]}")
            return
        with self.lock:
            self.status[name] = "built"
            self.metric_views.add(name)
        try:
            self.wh.execute(mx.render_tags(spec, cat))
        except Exception as e:  # noqa: BLE001 - tags are best-effort (governed-tag clashes)
            self.record("tags", fqn, "warn", str(e)[:300])
        self.validate_one(spec, info)
        if not self.a.skip_reconcile:
            self.reconcile_one(spec, info)
        self.coverage_one(spec, info)
        info["status"] = info.get("status", "built")
        print(f"{TAG} built   {name:34} dims={len(spec.field_names)} measures={len(spec.measure_names)} "
              f"validate={info.get('validate_rows')} reconcile={info.get('reconcile')} coverage={info.get('coverage')} "
              f"({time.time() - t0:5.1f}s)")

    def validate_one(self, spec: mx.ViewSpec, info: Dict[str, object]) -> None:
        fqn, t0 = spec.fqn(self.a.catalog), time.time()
        sql = self.render(mx.validation_sql(spec, self.a.catalog))
        try:
            n = self.wh.count(sql)
            info["validate_rows"] = n
            self.record("validate", fqn, "ok" if n > 0 else "warn",
                        f"{len(spec.measure_names)} measures by {spec.validate.get('dims') or 'default dims'}: {n} rows", n, t0)
            if n == 0:
                self.problem(spec.name, "validation query returned 0 rows")
        except Exception as e:  # noqa: BLE001 - pinpoint the failing measures one by one
            bad = []
            for m in spec.measure_names:
                try:
                    self.wh.count(self.render(mx.measure_sql(spec, self.a.catalog, m)))
                except Exception as e2:  # noqa: BLE001
                    bad.append(f"{m}: {str(e2).splitlines()[0][:160]}")
            msg = f"validation failed: {str(e).splitlines()[0][:300]}; failing measures: {'; '.join(bad) or 'none alone'}"
            info["validate_rows"] = "ERROR"
            self.record("validate", fqn, "error", msg[:2000], None, t0)
            self.problem(spec.name, msg[:500])
        t1 = time.time()
        try:
            self.wh.count(self.render(mx.dims_probe_sql(spec, self.a.catalog)))
            self.record("validate_dims", fqn, "ok", f"all {len(spec.field_names)} dimensions selectable", None, t1)
        except Exception as e:  # noqa: BLE001
            bad = []
            for d in spec.field_names:
                try:
                    self.wh.count(f"SELECT {mx.q(d)}, MEASURE({mx.q(spec.measure_names[0])}) FROM {fqn} GROUP BY ALL LIMIT 1")
                except Exception as e2:  # noqa: BLE001
                    bad.append(f"{d}: {str(e2).splitlines()[0][:160]}")
            msg = f"dimension probe failed: {'; '.join(bad) or str(e)[:300]}"
            self.record("validate_dims", fqn, "error", msg[:2000], None, t1)
            self.problem(spec.name, msg[:500])

    def reconcile_one(self, spec: mx.ViewSpec, info: Dict[str, object]) -> None:
        fqn, passed = spec.fqn(self.a.catalog), 0
        for rec in spec.reconcile:
            t0, by = time.time(), list(rec.get("by") or [])
            obj = f"{fqn}#{rec['name']}"
            try:
                where = self.render(rec["where"]) if rec.get("where") else None
                mv_rows = self.wh.rows(self.render(mx.measure_sql(spec, self.a.catalog, rec["measure"], where, by)))
                gold_rows = self.wh.rows(self.render(rec["gold"]))
                tol = float(rec.get("tolerance", mx.DEFAULT_TOLERANCE))
                ok, detail = mx.compare_rows(mv_rows, gold_rows, len(by), tol)
                msg = f"{rec['measure']}{' by ' + ', '.join(by) if by else ''}: {detail} (tol {tol:g})"
                self.record("reconcile", obj, "ok" if ok else "error", msg[:2000], len(gold_rows), t0)
                if ok:
                    passed += 1
                else:
                    self.problem(spec.name, f"reconcile {rec['name']}: {detail[:300]}")
                print(f"{TAG}   {'recon ok' if ok else 'RECON!!'} {spec.name}#{rec['name']}: {msg[:220]}")
            except Exception as e:  # noqa: BLE001
                msg = str(e).replace("\n", " ")
                self.record("reconcile", obj, "error", msg[:1500], None, t0)
                self.problem(spec.name, f"reconcile {rec['name']} error: {msg[:300]}")
                print(f"{TAG}   RECON!! {spec.name}#{rec['name']}: {msg[:220]}")
        info["reconcile"] = f"{passed}/{len(spec.reconcile)}"

    def coverage_one(self, spec: mx.ViewSpec, info: Dict[str, object]) -> None:
        fqn, t0 = spec.fqn(self.a.catalog), time.time()
        try:
            js = json.loads(self.wh.rows(f"DESCRIBE TABLE EXTENDED {fqn} AS JSON")[0][0])
            gaps = mx.coverage_gaps(js, spec)
            cols = len(js.get("columns", []))
            info["coverage"] = f"{cols - len({g.split(':')[0] for g in gaps})}/{cols}"
            self.record("coverage", fqn, "error" if gaps else "ok",
                        f"{cols} columns; comment / display_name / format gaps: {', '.join(gaps) or 'none'}", cols, t0)
            if gaps:
                self.problem(spec.name, f"coverage gaps: {', '.join(gaps[:10])}")
        except Exception as e:  # noqa: BLE001
            info["coverage"] = "ERROR"
            self.record("coverage", fqn, "error", str(e)[:1000], None, t0)
            self.problem(spec.name, f"coverage check error: {str(e)[:300]}")

    # ---- answers -----------------------------------------------------------------------------------
    def run_answers(self, spaces: List[str]) -> None:
        for slug in spaces:
            path = mx.ANSWERS_DIR / f"{slug}.sql"
            if not path.exists():
                continue
            text = path.read_text()
            try:
                answers = mx.parse_answers(text)
            except mx.MetricSpecError as e:
                self.record("answers", str(path.relative_to(REPO)), "error", str(e)[:1000])
                self.problem(slug, f"answers file: {e}")
                continue
            counts: Dict[str, Optional[int]] = {}
            for ans in answers:
                obj, t0 = f"{slug}#{ans.qid}", time.time()
                missing = [v for v in ans.views if v not in self.metric_views or self.status.get(v) in ("pending", "failed")]
                if missing:
                    counts[ans.qid] = ans.rows
                    self.record("answer", obj, "skipped", f"views not built: {', '.join(missing)}")
                    print(f"{TAG}   answer  {obj:40} skipped (views not built: {', '.join(missing)})")
                    continue
                try:
                    n = self.wh.count(self.render(ans.sql))
                    counts[ans.qid] = n
                    self.record("answer", obj, "ok" if n > 0 else "warn", f"{ans.question[:300]} -> {n} rows", n, t0)
                    print(f"{TAG}   answer  {obj:40} {n:>6} rows  {ans.question[:70]}")
                    if n == 0:
                        self.problem(slug, f"{ans.qid} returned 0 rows")
                except Exception as e:  # noqa: BLE001
                    counts[ans.qid] = None
                    msg = str(e).replace("\n", " ")
                    self.record("answer", obj, "error", msg[:1500], None, t0)
                    self.problem(slug, f"{ans.qid} failed: {msg[:300]}")
                    print(f"{TAG}   ANSWER! {obj:40} {msg[:200]}")
            new = mx.write_answer_rows(text, counts)
            if new != text:
                path.write_text(new)

    # ---- log ---------------------------------------------------------------------------------------
    def write_log(self) -> None:
        def esc(s: Optional[str]) -> str:
            return "NULL" if s is None else mx.sql_str(s)

        def ts(t: float) -> str:   # UTC wall clock, like the warehouse's current_timestamp() in other phases
            return f"TIMESTAMP'{dt.datetime.fromtimestamp(t, tz=dt.timezone.utc):%Y-%m-%d %H:%M:%S}'"

        rows = [f"({esc(self.run_id)}, {esc(PHASE)}, {esc(step)}, {esc(obj)}, {'NULL' if n is None else int(n)}, "
                f"{esc(st)}, {esc(msg)}, {float(self.cfg.scale)}, {ts(t0)}, {ts(t1)}, {round(t1 - t0, 2)})"
                for step, obj, st, msg, n, t0, t1 in self.log]
        for i in range(0, len(rows), 200):
            try:
                self.wh.execute(
                    f"INSERT INTO {self.a.catalog}.ops.build_run_log (run_id, phase, step, object_name, row_count, "
                    f"status, message, scale, started_at, finished_at, duration_sec) VALUES\n" + ",\n".join(rows[i:i + 200]))
            except Exception as e:  # noqa: BLE001 - logging must never mask the build result
                print(f"{TAG} WARN could not write ops.build_run_log: {str(e)[:200]}")
                return

    def write_glossary(self) -> None:
        text = mx.render_glossary(self.views, self.blocks, self.params)
        old = mx.GLOSSARY_PATH.read_text() if mx.GLOSSARY_PATH.exists() else ""
        if text != old:
            mx.GLOSSARY_PATH.write_text(text)
        print(f"{TAG} glossary: {mx.GLOSSARY_PATH.relative_to(REPO)} ({len(self.views)} views)")

    # ---- main --------------------------------------------------------------------------------------
    def run(self) -> int:
        order = self.plan()
        if self.a.list:
            for v, slug in sorted(mx.PLAN_VIEWS.items(), key=lambda kv: (mx.SPACES[kv[1]][0], kv[0])):
                s = self.views.get(v)
                state = (f"spec ok: {len(s.field_names)} dims, {len(s.measure_names)} measures, "
                         f"{len(s.reconcile)} reconciliations <- {', '.join(f'{a}.{b}' for a, b in sorted(mx.sql_refs(s)))}"
                         if s else "no spec yet")
                print(f"space {mx.SPACES[slug][0]:>2} {slug:32} {v:36} {state}")
            return 0
        if self.a.dry_run:
            for n in order:
                print(f"-- ===== {n} ({self.views[n].space}) =====")
                print(mx.render_ddl(self.views[n], self.params) + ";\n")
                print("-- validate:\n" + self.render(mx.validation_sql(self.views[n], self.a.catalog)) + ";\n")
            for p in sorted(mx.ANSWERS_DIR.glob("*.sql")) if mx.ANSWERS_DIR.exists() else []:
                print(f"-- answers {p.name}: {len(mx.parse_answers(p.read_text()))} queries parse")
            if not self.a.no_glossary:
                self.write_glossary()
            return 0

        print(f"{TAG} run_id={self.run_id} catalog={self.a.catalog} warehouse={self.a.warehouse_id} "
              f"as_of={self.params['as_of_date']} views={len(order)} of {len(self.views)} specs ({len(mx.PLAN_VIEWS)} planned)")
        self.wh = Warehouse(self.a.warehouse_id, self.a.profile)
        self.refresh_existing()
        with ThreadPoolExecutor(max_workers=max(1, self.a.parallel)) as ex:
            list(ex.map(self.build_one, order))
        if not self.a.no_glossary:
            self.write_glossary()
            self.record("glossary", str(mx.GLOSSARY_PATH.relative_to(REPO)), "ok", f"{len(self.views)} views", len(self.views))
        if not self.a.skip_answers:   # --space: those spaces; --only: the spaces of those views; else all
            space = [s.strip() for s in self.a.space.split(",") if s.strip()]
            spaces = space or (sorted({self.views[n].space for n in order}) if self.a.only else sorted(mx.SPACES))
            self.run_answers(spaces)
        self.write_log()

        built = [n for n in order if self.status.get(n) == "built"]
        pend = [n for n in order if self.status.get(n) == "pending"]
        failed = [n for n in order if self.status.get(n) == "failed"]
        print(f"\n{TAG} summary: {len(built)} built, {len(pend)} pending, {len(failed)} failed, "
              f"{len(self.problems)} problems (run_id={self.run_id})")
        for n in order:
            i = self.summary.get(n, {})
            print(f"{TAG}   {n:34} {str(i.get('status', '?')):10} validate_rows={i.get('validate_rows')} "
                  f"reconcile={i.get('reconcile')} coverage={i.get('coverage')}")
        for p in self.problems:
            print(f"{TAG}   PROBLEM {p}")
        for err in self.spec_errors:
            print(f"{TAG}   SKIPPED invalid spec: {err[:300]}")
        if failed or self.problems:
            return 1
        return 3 if (pend and self.a.strict) else 0


def main(argv: Optional[List[str]] = None) -> None:
    try:
        a = parse_args(argv)
        if not a.dry_run and not a.list:
            require_warehouse(a.warehouse_id, TAG)
        build = Build(a)
    except mx.MetricSpecError as e:     # a malformed view / block / answers file: say which and stop
        print(f"{TAG} SPEC ERROR: {e}\n{TAG} fix it, or pass --skip-invalid to build the other views")
        sys.exit(2)
    sys.exit(build.run())


if __name__ == "__main__":
    main()
