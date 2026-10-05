"""Phase 5 — gold Customer 360 star schema (PLAN §7; DECISIONS D11, D16, D25, D29, D38).

Builds every gold object declared in src/30_gold/specs/<name>.yaml with its SELECT in
src/30_gold/sql/<name>.sql (framework: smbc_genie_lib.gold; conventions: src/30_gold/README.md):
explicit CREATE OR REPLACE TABLE (typed columns + COMMENT, PK / FK RELY, CLUSTER BY, table comment),
INSERT OVERWRITE ... SELECT, ANALYZE TABLE ... FOR ALL COLUMNS and smbc_ tags, in dependency order on
the SQL warehouse. Then re-adds any declared FK missing in the catalog (all specs: deferred targets and
FKs dropped when their target was re-created), verifies row counts, PK uniqueness / not-null, FK
resolution, SCD2 integrity, silver reconciliations and the spec's checks, gates 100% table / column
comments (D25) and logs every step to ops.build_run_log. Tables whose silver inputs do not exist yet are
reported as pending (their dependents too), so the runner is safe to re-run as silver fills in.

Run:  .venv/bin/python src/30_gold/run_gold.py --profile my-workspace
      [--only dim_client,dim_client_group] [--domain hub] [--with-deps] [--parallel 6]
      [--list] [--dry-run] [--skip-verify] [--skip-analyze] [--strict] [--skip-invalid-specs]
"""
from __future__ import annotations

import argparse
import datetime as dt
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

from smbc_genie_lib import gold  # noqa: E402
from smbc_genie_lib.config import default_warehouse_id, export_env, load_config, require_warehouse  # noqa: E402
from smbc_genie_lib.sql_runner import split_statements  # noqa: E402

TAG = "[p05a]"
DEFAULT_WAREHOUSE = default_warehouse_id("")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SMBC APAC Genie - phase 5: gold Customer 360 star schema")
    p.add_argument("--catalog", default="smbc_genie")
    p.add_argument("--profile", default=None, help="CLI profile for local runs; omit inside a job")
    p.add_argument("--warehouse-id", default=DEFAULT_WAREHOUSE)
    p.add_argument("--only", default="", help="comma-separated gold tables")
    p.add_argument("--domain", default="", help="comma-separated spec domains (e.g. hub)")
    p.add_argument("--with-deps", action="store_true", help="also (re)build upstream gold dependencies")
    p.add_argument("--parallel", type=int, default=6, help="tables built concurrently per wave")
    p.add_argument("--list", action="store_true", help="print the build plan and exit")
    p.add_argument("--dry-run", action="store_true", help="print the rendered SQL, execute nothing")
    p.add_argument("--skip-verify", action="store_true")
    p.add_argument("--skip-analyze", action="store_true")
    p.add_argument("--strict", action="store_true", help="exit non-zero when tables are pending")
    p.add_argument("--skip-invalid-specs", action="store_true",
                   help="skip (and report) specs that fail to load instead of aborting - for parallel development")
    p.add_argument("--scale", default=None, help="override config scale (exported as SMBC_SCALE)")
    p.add_argument("--as-of", dest="as_of", default=None, help="override config as_of_date")
    p.add_argument("--seed", default=None, help="override config random_seed")
    a = p.parse_args()
    export_env(scale=a.scale, as_of_date=a.as_of, random_seed=a.seed)
    if not a.dry_run and not a.list:
        require_warehouse(a.warehouse_id, TAG)
    return a


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

    def scalar_row(self, sql: str) -> List[Optional[str]]:
        rows = self.rows(sql)
        return rows[0] if rows else []


class Build:
    def __init__(self, a: argparse.Namespace):
        self.a = a
        self.cfg = load_config()
        self.params = gold.build_params(self.cfg, a.catalog)
        self.spec_errors: List[str] = []
        self.specs = gold.load_specs(errors=self.spec_errors if a.skip_invalid_specs else None)
        for err in self.spec_errors:
            print(f"{TAG} INVALID spec skipped: {err}")
        self.dicts = gold.load_dictionaries()
        self.run_id = f"p05-{dt.datetime.now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
        self.log: List[Tuple] = []          # build_run_log rows
        self.status: Dict[str, str] = {}    # table -> built | pending | failed | blocked
        self.notes: Dict[str, str] = {}
        self.failures: List[str] = []
        self.lock = threading.Lock()
        self.wh: Optional[Warehouse] = None
        self.existing: Set[Tuple[str, str]] = set()

    # ---- helpers ----------------------------------------------------------------------------------
    def sql(self, text: str, table: Optional[str] = None) -> str:
        params = dict(self.params)
        if table:
            params["table"] = self.specs[table].fqn(self.a.catalog)
        return gold.render(text, params)

    def record(self, step: str, obj: str, status: str, msg: str = "", rows: Optional[int] = None,
               t0: Optional[float] = None) -> None:
        now = time.time()
        with self.lock:
            self.log.append((step, obj, status, msg, rows, t0 or now, now))

    def refresh_existing(self) -> None:
        found = set()
        for schema in ("bronze", "silver", "shared", "gold", "ops"):
            try:
                for r in self.wh.rows(f"SHOW TABLES IN {self.a.catalog}.{schema}"):
                    found.add((schema, str(r[1]).lower()))
            except Exception:  # noqa: BLE001 - a missing schema just has no tables
                pass
        self.existing = found

    # ---- plan -------------------------------------------------------------------------------------
    def plan(self) -> List[str]:
        only = [s.strip() for s in self.a.only.split(",") if s.strip()]
        dom = [s.strip() for s in self.a.domain.split(",") if s.strip()]
        return gold.select(self.specs, only, dom, self.a.with_deps)

    def missing_inputs(self, name: str, order: List[str]) -> List[str]:
        """Inputs not available: non-gold tables that do not exist, gold deps that did not build
        in this run (or, outside the selection, do not exist)."""
        spec, missing = self.specs[name], []
        for dep in sorted(gold.hard_deps(spec, self.specs)):
            if dep in order:
                if self.status.get(dep) != "built":
                    missing.append(f"gold.{dep} ({self.status.get(dep, 'not built')})")
            elif self.specs[dep].kind != "function" and ("gold", dep) not in self.existing:
                missing.append(f"gold.{dep}")
        for schema, table in sorted(gold.sql_refs(spec.body)):
            if (schema != "gold" or table not in self.specs) and (schema, table) not in self.existing:
                missing.append(f"{schema}.{table}")      # silver not built yet, or a gold table without a spec
        return missing

    # ---- build ------------------------------------------------------------------------------------
    def build_one(self, name: str) -> None:
        spec, cat, t0 = self.specs[name], self.a.catalog, time.time()
        try:
            if spec.kind == "function":
                for stmt in split_statements(self.sql(spec.body)):
                    self.wh.execute(stmt)
            else:
                skip = [fk.name(name) for fk in spec.foreign_keys   # added by reconcile_fks once the target exists
                        if (fk.ref_schema, fk.ref_table) not in self.existing]
                self.wh.execute(self.sql(gold.render_create(spec, cat, self.specs, skip)))
                if spec.kind == "table":
                    self.wh.execute(self.sql(gold.render_insert(spec, cat)))
                    if not self.a.skip_analyze:
                        self.wh.execute(gold.render_analyze(spec, cat))
                tag_sql = gold.render_tags(spec, cat)
                if tag_sql:
                    try:
                        self.wh.execute(tag_sql)
                    except Exception as e:  # noqa: BLE001 - tags are best-effort (governed-tag clashes)
                        self.record("tags", spec.fqn(cat), "warn", str(e)[:300])
            rows = None
            if spec.kind != "function":
                rows = int(self.wh.scalar_row(f"SELECT count(*) FROM {spec.fqn(cat)}")[0])
            with self.lock:
                self.status[name] = "built"
                self.existing.add(("gold", name))
            self.record("build", spec.fqn(cat), "ok", f"{spec.kind} built", rows, t0)
            print(f"{TAG} built   {name:38} {'' if rows is None else f'{rows:>12,} rows'}  ({time.time() - t0:5.1f}s)")
        except Exception as e:  # noqa: BLE001
            msg = str(e).replace("\n", " ")
            with self.lock:
                self.status[name] = "failed"
                self.notes[name] = msg[:400]
                self.failures.append(f"{name}: build failed: {msg[:400]}")
            self.record("build", spec.fqn(cat), "error", msg[:1000], None, t0)
            print(f"{TAG} FAILED  {name}: {msg[:300]}")

    def existing_fks(self) -> Set[Tuple[str, str]]:
        rows = self.wh.rows(f"SELECT table_name, constraint_name FROM {self.a.catalog}.information_schema.table_constraints "
                            f"WHERE table_schema = 'gold' AND constraint_type = 'FOREIGN KEY'")
        return {(str(r[0]), str(r[1])) for r in rows}

    def reconcile_fks(self) -> List[str]:
        """Re-add declared FKs missing in the catalog: every FK of the tables built in this run (incl. FKs
        deferred at create time - target not built yet, the dim_client <-> dim_client_group pair), plus FKs
        of any other existing spec table that point INTO a table this run re-created (Unity Catalog can drop
        the constraints that reference a replaced table, so a hub rebuild must not strip the facts' FKs).
        Failures count as problems only for this run's tables; elsewhere they are warnings."""
        built = {n for n, st in self.status.items() if st == "built"}
        have, added, problems, warns = self.existing_fks(), [], [], []
        for name in sorted(self.specs):
            spec = self.specs[name]
            if spec.kind != "table" or ("gold", name) not in self.existing:
                continue
            mine = name in built
            for fk in spec.foreign_keys:
                if (name, fk.name(name)) in have or not (mine or fk.ref_table in built):
                    continue
                sink = problems if mine else warns
                if (fk.ref_schema, fk.ref_table) not in self.existing:
                    sink.append(f"{name}: FK {fk.name(name)} not added ({fk.ref_schema}.{fk.ref_table} missing)")
                    continue
                try:
                    self.wh.execute(gold.add_fk_sql(spec, fk, self.a.catalog, self.specs))
                    added.append(f"{name}.{fk.name(name)}")
                except Exception as e:  # noqa: BLE001 - a concurrent run may have added it meanwhile
                    if "ALREADY_EXISTS" not in str(e):
                        sink.append(f"{name}: FK {fk.name(name)} not added: {str(e)[:200]}")
        for w in warns:
            print(f"{TAG} WARN    {w}")
        declared = sum(len(self.specs[n].foreign_keys) for n in built if self.specs[n].kind == "table")
        self.record("fk", f"{self.a.catalog}.gold", "error" if problems else ("warn" if warns else "ok"),
                    f"{declared} FKs on built tables; re-added {len(added)}: {', '.join(added)[:2000]}; "
                    f"warnings: {'; '.join(warns)[:800]}")
        print(f"{TAG} constraints: {declared} FKs declared on the built tables, {len(added)} re-added"
              + (f" ({', '.join(added[:8])}{' ...' if len(added) > 8 else ''})" if added else ""))
        return problems

    # ---- verify -----------------------------------------------------------------------------------
    def absent_inputs(self, sql: str) -> List[str]:
        """Tables a check reads that do not exist (yet): the check is skipped, not failed."""
        return [f"{s}.{t}" for s, t in sorted(gold.sql_refs(sql)) if (s, t) not in self.existing]

    def verify_one(self, name: str) -> List[str]:
        spec, cat, problems, lines = self.specs[name], self.a.catalog, [], []
        try:
            if spec.kind == "table":
                n, dups, nulls = (int(x) for x in self.wh.scalar_row(gold.pk_check_sql(spec, cat)))
                lines.append(f"rows={n:,} pk_dups={dups} pk_nulls={nulls}")
                if dups or nulls:
                    problems.append(f"PK {spec.primary_key}: {dups} duplicate keys, {nulls} NULL keys")
                if n < spec.min_rows:
                    problems.append(f"{n} rows < min_rows {spec.min_rows}")
                for fk in spec.foreign_keys:
                    if (fk.ref_schema, fk.ref_table) not in self.existing:
                        problems.append(f"FK {fk.columns} -> {fk.ref_table}: target missing")
                        continue
                    n_fk, orphans, n_null = (int(x) for x in
                                             self.wh.scalar_row(gold.fk_check_sql(spec, fk, cat, self.specs)))
                    lines.append(f"fk {','.join(fk.columns)}->{fk.ref_table}: {n_fk - orphans}/{n_fk} resolved"
                                 + (f", {n_null} null" if n_null else ""))
                    if orphans:
                        problems.append(f"FK {fk.columns} -> {fk.ref_table}: {orphans} of {n_fk} unresolved")
            for chk in gold.scd2_checks(spec) + spec.checks:
                absent = self.absent_inputs(chk["sql"])
                if absent:
                    lines.append(f"{chk['name']}=skipped (missing {', '.join(absent)})")
                    continue
                v = float(self.wh.scalar_row(self.sql(chk["sql"], name))[0] or 0)
                ok = v <= float(chk.get("max", 0))
                lines.append(f"{chk['name']}={v:g}{'' if ok else ' FAIL'}")
                if not ok:
                    problems.append(f"check {chk['name']} = {v:g} (max {chk.get('max', 0)})")
            for rec in spec.reconcile:
                absent = self.absent_inputs(rec["silver"] + " " + rec.get("gold", ""))
                if absent:
                    lines.append(f"{rec['name']}: skipped (missing {', '.join(absent)})")
                    continue
                g_sql = rec.get("gold", "SELECT count(*) FROM ${table}")
                g = float(self.wh.scalar_row(self.sql(g_sql, name))[0] or 0)
                s = float(self.wh.scalar_row(self.sql(rec["silver"], name))[0] or 0)
                tol = float(rec.get("tolerance", 0))
                ok = abs(g - s) <= tol
                lines.append(f"{rec['name']}: gold={g:,.0f} silver={s:,.0f}{'' if ok else ' FAIL'}")
                if not ok:
                    problems.append(f"reconcile {rec['name']}: gold {g:,.0f} vs silver {s:,.0f} (tol {tol:g})")
        except Exception as e:  # noqa: BLE001
            problems.append(f"verification error: {str(e)[:300]}")
        status = "error" if problems else "ok"
        self.record("verify", spec.fqn(cat), status, "; ".join(lines + problems)[:3000])
        print(f"{TAG} {'verify ' if not problems else 'VERIFY!'} {name:38} " + " | ".join(lines))
        for p in problems:
            print(f"{TAG}     !! {p}")
        return [f"{name}: {p}" for p in problems]

    def comment_gate(self, names: List[str]) -> List[str]:
        objs = [n for n in names if self.specs[n].kind in ("table", "view") and self.status.get(n) == "built"]
        if not objs:
            return []
        rows = self.wh.rows(gold.comment_gate_sql(self.a.catalog, objs))
        gaps = [f"{r[0]}.{r[1]}" if r[1] else str(r[0]) for r in rows]
        self.record("comment_gate", f"{self.a.catalog}.gold", "error" if gaps else "ok",
                    f"{len(objs)} objects; missing comments: {', '.join(gaps[:50]) or 'none'}")
        print(f"{TAG} comment gate: {len(objs)} objects, {len(gaps)} without comment"
              + (f": {', '.join(gaps[:20])}" if gaps else ""))
        return [f"comment missing: {g}" for g in gaps]

    def write_log(self) -> None:
        def esc(s: Optional[str]) -> str:
            return "NULL" if s is None else gold.sql_str(s)

        def ts(t: float) -> str:   # UTC wall clock, like the warehouse's current_timestamp() in other phases
            return f"TIMESTAMP'{dt.datetime.fromtimestamp(t, tz=dt.timezone.utc):%Y-%m-%d %H:%M:%S}'"

        rows = [f"({esc(self.run_id)}, 'p05_gold', {esc(step)}, {esc(obj)}, "
                f"{'NULL' if n is None else int(n)}, {esc(st)}, {esc(msg)}, {float(self.cfg.scale)}, "
                f"{ts(t0)}, {ts(t1)}, {round(t1 - t0, 2)})" for step, obj, st, msg, n, t0, t1 in self.log]
        for i in range(0, len(rows), 200):
            try:
                self.wh.execute(
                    f"INSERT INTO {self.a.catalog}.ops.build_run_log (run_id, phase, step, object_name, row_count, "
                    f"status, message, scale, started_at, finished_at, duration_sec) VALUES\n" + ",\n".join(rows[i:i + 200]))
            except Exception as e:  # noqa: BLE001 - logging must never mask the build result
                print(f"{TAG} WARN could not write ops.build_run_log: {str(e)[:200]}")
                return

    # ---- main -------------------------------------------------------------------------------------
    def run(self) -> int:
        order = self.plan()
        gaps = gold.comment_gaps({n: self.specs[n] for n in order}, self.dicts)
        if gaps:
            print(f"{TAG} comment gate FAILED before build ({len(gaps)} missing): {', '.join(gaps)}")
            return 2
        if self.a.list:
            for wave_no, wave in enumerate(gold.levels(self.specs, order)):
                for n in wave:
                    s = self.specs[n]
                    ins = sorted(f"{a}.{b}" for a, b in gold.sql_refs(s.body))
                    print(f"wave {wave_no}  {s.domain:10} {s.kind:8} {n:38} <- {', '.join(ins)}")
            return 0
        if self.a.dry_run:
            for n in order:
                s = self.specs[n]
                print(f"-- ===== {n} ({s.kind}) =====")
                if s.kind == "function":
                    print(self.sql(s.body) + ";\n")
                    continue
                print(self.sql(gold.render_create(s, self.a.catalog, self.specs)) + ";\n")
                if s.kind == "table":
                    print(self.sql(gold.render_insert(s, self.a.catalog)) + ";\n")
            return 0

        print(f"{TAG} run_id={self.run_id} catalog={self.a.catalog} warehouse={self.a.warehouse_id} "
              f"as_of={self.params['as_of_date']} tables={len(order)}")
        self.wh = Warehouse(self.a.warehouse_id, self.a.profile)
        self.refresh_existing()
        for wave in gold.levels(self.specs, order):
            ready = []
            for n in wave:
                missing = self.missing_inputs(n, order)
                if missing:   # waiting for silver (pending) or behind a failed gold table (blocked)
                    self.status[n] = "blocked" if any("(failed)" in m or "(blocked)" in m for m in missing) else "pending"
                    self.notes[n] = "missing " + ", ".join(missing)
                    self.record("build", self.specs[n].fqn(self.a.catalog), "skipped", self.notes[n])
                    print(f"{TAG} {self.status[n]:7} {n:38} ({self.notes[n]})")
                else:
                    ready.append(n)
            with ThreadPoolExecutor(max_workers=max(1, self.a.parallel)) as ex:
                list(ex.map(self.build_one, ready))
        built = [n for n in order if self.status.get(n) == "built"]
        problems: List[str] = self.reconcile_fks() if built else []
        if not self.a.skip_verify and built:
            with ThreadPoolExecutor(max_workers=max(1, self.a.parallel)) as ex:
                for res in ex.map(self.verify_one, built):
                    problems += res
            problems += self.comment_gate(built)
        self.write_log()

        pend = {n: self.notes.get(n, "") for n in order if self.status.get(n) in ("pending", "blocked")}
        failed = [n for n in order if self.status.get(n) == "failed"]
        print(f"\n{TAG} summary: {len(built)} built, {len(pend)} pending, {len(failed)} failed, "
              f"{len(problems)} verification problems (run_id={self.run_id})")
        for n, why in pend.items():
            print(f"{TAG}   pending {n}: {why}")
        for f in self.failures + problems:
            print(f"{TAG}   PROBLEM {f}")
        for err in self.spec_errors:
            print(f"{TAG}   SKIPPED invalid spec: {err}")
        if failed or problems or self.failures:
            return 1
        return 3 if (pend and self.a.strict) else 0


def main() -> None:
    try:
        build = Build(parse_args())
    except gold.SpecError as e:     # a malformed spec / SQL body / dictionary: say which and stop
        print(f"{TAG} SPEC ERROR: {e}\n{TAG} fix it, or pass --skip-invalid-specs to build the other tables")
        sys.exit(2)
    sys.exit(build.run())


if __name__ == "__main__":
    main()
