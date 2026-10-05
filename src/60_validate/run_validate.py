"""Phase 8 — data-asset inventory and data-quality check (read-only audit).

Audits the whole catalog independently of the build runners (constraints and comments are re-read
from information_schema, not from the specs) and writes docs/DATA_ASSET_REPORT.md plus a summary to
ops.build_run_log (phase p08_validate):

  inventory          objects / rows / columns / size per schema; table + column comment coverage;
                     smbc_ tags; PK / FK constraints
  gold integrity     every declared PK unique + not null, every declared FK resolves, golden keys
                     populated on client-grain facts, grain dates inside the calendar
  silver DQ          the latest DQ run: reconciliation, golden-key coverage, blocking failures, rule
                     results by layer / dimension / status, every warn / fail listed
  entity resolution  precision / recall / purity per ER run
  storylines         ops.storyline_assertions + the latest build run of every phase
  metric views       the 43 planned views present, each answering a MEASURE() query
  genie spaces       the 11 spaces created (genie/<slug>/space_id) and their benchmark pass rates

Run:  .venv/bin/python src/60_validate/run_validate.py --profile my-workspace [--skip-sizes]
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import yaml

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from smbc_genie_lib.config import default_warehouse_id, load_config, require_warehouse  # noqa: E402

TAG = "[p08]"
SCHEMAS = ("bronze", "shared", "silver", "gold", "metrics", "ops")
DEFAULT_WAREHOUSE = default_warehouse_id("")
REPORT = REPO / "docs" / "DATA_ASSET_REPORT.md"
FUTURE_OK = re.compile(r"^(dim_date|fx_rate_daily|fact_cashflow_forecast)$")   # grain dates may pass as-of
PROCESS_SLA = re.compile(r"(on_time|within_sla|by_due_date)$")   # timeliness of a business process, not of data


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SMBC APAC Genie - phase 8: data-asset and data-quality check")
    p.add_argument("--catalog", default="smbc_genie")
    p.add_argument("--profile", default=None)
    p.add_argument("--warehouse-id", default=DEFAULT_WAREHOUSE)
    p.add_argument("--parallel", type=int, default=8)
    p.add_argument("--skip-sizes", action="store_true", help="skip DESCRIBE DETAIL (bytes / files)")
    p.add_argument("--no-log", action="store_true", help="do not append to ops.build_run_log (fully read-only)")
    return p.parse_args()


class Warehouse:
    """Thread-local SqlRunner wrapper (one WorkspaceClient per worker thread)."""

    def __init__(self, warehouse_id: str, profile: Optional[str]):
        self.warehouse_id, self.profile, self._local = warehouse_id, profile, threading.local()

    def _execute(self, sql: str):
        if not hasattr(self._local, "r"):
            from smbc_genie_lib.sql_runner import SqlRunner
            self._local.r = SqlRunner(self.warehouse_id, profile=self.profile)
        return self._local.r.execute(sql)

    def rows(self, sql: str) -> List[List[Optional[str]]]:
        resp = self._execute(sql)
        return list(resp.result.data_array or []) if resp.result else []

    def first_as_dict(self, sql: str) -> Dict[str, Optional[str]]:
        resp = self._execute(sql)
        data = resp.result.data_array if resp.result else None
        return dict(zip([c.name for c in resp.manifest.schema.columns], data[0])) if data else {}

    def try_rows(self, sql: str) -> Tuple[List[List[Optional[str]]], Optional[str]]:
        try:
            return self.rows(sql), None
        except Exception as e:  # noqa: BLE001 - an audit records failures instead of stopping
            return [], str(e).split("\n")[0][:300]


def num(v: Optional[str]) -> float:
    return float(v) if v not in (None, "") else 0.0


def pct(a: float, b: float) -> str:
    return f"{100.0 * a / b:.2f}%" if b else "n/a"


def md_table(header: Sequence[str], rows: Sequence[Sequence[object]]) -> List[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join("" if v is None else str(v).replace("|", "/") for v in r) + " |" for r in rows]
    return out


class Audit:
    def __init__(self, a: argparse.Namespace):
        self.a, self.c = a, a.catalog
        self.cfg = load_config()
        self.wh = Warehouse(a.warehouse_id, a.profile)
        self.run_id = f"p08-{dt.datetime.now(dt.timezone.utc):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
        self.findings: List[Tuple[str, str, str]] = []      # (severity, area, text)
        self.log: List[Tuple[str, str, str, str, Optional[int]]] = []
        self.md: List[str] = []

    def find(self, sev: str, area: str, text: str) -> None:
        self.findings.append((sev, area, text))
        print(f"{TAG} {sev.upper():5} {area}: {text}")

    def pmap(self, fn, items):
        with ThreadPoolExecutor(self.a.parallel) as ex:
            return list(ex.map(fn, items))

    # ---- inventory -------------------------------------------------------------------------------
    def inventory(self) -> None:
        c, schemas = self.c, ", ".join(f"'{s}'" for s in SCHEMAS)
        objs = self.wh.rows(f"""SELECT table_schema, table_name, table_type, comment
          FROM {c}.information_schema.tables WHERE table_schema IN ({schemas}) ORDER BY 1, 2""")
        cols = {(r[0], r[1]): (int(r[2]), int(r[3])) for r in self.wh.rows(f"""
          SELECT table_schema, table_name, count(*), count_if(comment IS NULL OR trim(comment) = '')
          FROM {c}.information_schema.columns WHERE table_schema IN ({schemas}) GROUP BY ALL""")}
        tags: Dict[Tuple[str, str], int] = {}
        trows, terr = self.wh.try_rows(f"""SELECT schema_name, table_name, count(*) FROM {c}.information_schema.table_tags
          WHERE schema_name IN ({schemas}) AND tag_name LIKE 'smbc%' GROUP BY ALL""")
        for r in trows:
            tags[(r[0], r[1])] = int(r[2])
        cons: Dict[Tuple[str, str], Dict[str, int]] = {}
        for r in self.wh.rows(f"""SELECT table_schema, table_name, constraint_type, count(*)
          FROM {c}.information_schema.table_constraints WHERE table_schema IN ({schemas}) GROUP BY ALL"""):
            cons.setdefault((r[0], r[1]), {})[r[2]] = int(r[3])

        def measure(o):
            s, t, typ = o[0], o[1], (o[2] or "")
            if s == "metrics" or "METRIC" in typ.upper():
                return None, None, None
            n, err = self.wh.try_rows(f"SELECT count(*) FROM {c}.{s}.`{t}`")
            size = None
            if not self.a.skip_sizes and typ.upper() in ("MANAGED", "EXTERNAL"):
                try:
                    size = self.wh.first_as_dict(f"DESCRIBE DETAIL {c}.{s}.`{t}`").get("sizeInBytes")
                except Exception:  # noqa: BLE001 - size is informational
                    size = None
            return (int(n[0][0]) if n else None), size, err

        t0 = time.time()
        stats = self.pmap(measure, objs)
        print(f"{TAG} inventory: {len(objs)} objects measured in {time.time() - t0:.0f}s")
        self.objects = []
        for o, (n, size, err) in zip(objs, stats):
            ncol, nunc = cols.get((o[0], o[1]), (0, 0))
            self.objects.append(dict(schema=o[0], table=o[1], type=o[2], has_comment=bool((o[3] or "").strip()),
                                     rows=n, bytes=size, cols=ncol, uncommented=nunc,
                                     tags=tags.get((o[0], o[1]), 0), pk=cons.get((o[0], o[1]), {}).get("PRIMARY KEY", 0),
                                     fk=cons.get((o[0], o[1]), {}).get("FOREIGN KEY", 0), error=err))
        if terr:
            self.find("info", "inventory", f"table_tags not readable: {terr}")

        md = ["## 1. Inventory", "", "Rows are exact `count(*)`; comment coverage is from `information_schema`.", ""]
        summary = []
        for s in SCHEMAS:
            ob = [o for o in self.objects if o["schema"] == s]
            if not ob:
                continue
            ncols, nunc = sum(o["cols"] for o in ob), sum(o["uncommented"] for o in ob)
            size = sum(num(o["bytes"]) for o in ob)
            summary.append([s, len(ob), "n/a (metric views)" if s == "metrics" else f"{sum(o['rows'] or 0 for o in ob):,}", ncols,
                            pct(ncols - nunc, ncols), pct(sum(o["has_comment"] for o in ob), len(ob)),
                            sum(1 for o in ob if o["tags"]), sum(o["pk"] for o in ob), sum(o["fk"] for o in ob),
                            f"{size / 1e6:,.0f} MB" if size else ""])
            self.log.append(("inventory", f"{self.c}.{s}", "ok", f"{len(ob)} objects, {ncols} columns", len(ob)))
        md += md_table(["schema", "objects", "rows", "columns", "columns commented", "tables commented",
                        "tagged (smbc_)", "PKs", "FKs", "size"], summary)
        for s in ("silver", "gold", "metrics"):
            for o in self.objects:
                if o["schema"] == s and (o["uncommented"] or not o["has_comment"]):
                    self.find("warn", "comments", f"{s}.{o['table']}: table comment {'ok' if o['has_comment'] else 'MISSING'}, "
                                                  f"{o['uncommented']} of {o['cols']} columns without comment")
        for o in self.objects:
            if o["error"]:
                self.find("error", "inventory", f"{o['schema']}.{o['table']}: {o['error']}")
            elif o["rows"] == 0 and o["schema"] in ("silver", "gold"):
                self.find("warn", "inventory", f"{o['schema']}.{o['table']} is empty")
        self.md += md + [""]

        for s in ("gold", "silver", "bronze", "shared", "metrics"):
            ob = [o for o in self.objects if o["schema"] == s]
            if not ob:
                continue
            self.md += [f"<details><summary>{s}: {len(ob)} objects</summary>", ""]
            self.md += md_table(["object", "type", "rows", "columns", "uncommented", "PK", "FK", "tags"],
                                [[o["table"], o["type"], "" if o["rows"] is None else f"{o['rows']:,}", o["cols"],
                                  o["uncommented"], o["pk"], o["fk"], o["tags"]] for o in ob])
            self.md += ["", "</details>", ""]

    # ---- gold integrity --------------------------------------------------------------------------
    def gold_integrity(self) -> None:
        c = self.c
        pk_rows = self.wh.rows(f"""SELECT k.table_name, k.constraint_name, k.column_name
          FROM {c}.information_schema.table_constraints t
          JOIN {c}.information_schema.key_column_usage k
            ON k.constraint_schema = t.constraint_schema AND k.constraint_name = t.constraint_name
          WHERE t.table_schema = 'gold' AND t.constraint_type = 'PRIMARY KEY'
          ORDER BY k.table_name, k.ordinal_position""")
        pks: Dict[str, List[str]] = {}
        for t, _, col in pk_rows:
            pks.setdefault(t, []).append(col)
        fk_rows = self.wh.rows(f"""SELECT rc.constraint_name, f.table_name, f.column_name, p.table_name, p.column_name
          FROM {c}.information_schema.referential_constraints rc
          JOIN {c}.information_schema.key_column_usage f
            ON f.constraint_schema = rc.constraint_schema AND f.constraint_name = rc.constraint_name
          JOIN {c}.information_schema.key_column_usage p
            ON p.constraint_schema = rc.unique_constraint_schema AND p.constraint_name = rc.unique_constraint_name
           AND p.ordinal_position = f.position_in_unique_constraint
          WHERE rc.constraint_schema = 'gold' ORDER BY 1, f.ordinal_position""")
        fks: Dict[str, Dict[str, object]] = {}
        for name, ct, cc, pt, pc in fk_rows:
            fk = fks.setdefault(name, {"child": ct, "parent": pt, "cc": [], "pc": []})
            fk["cc"].append(cc)
            fk["pc"].append(pc)

        def pk_check(item):
            t, cols = item
            lst = ", ".join(f"`{x}`" for x in cols)
            nulls = " OR ".join(f"`{x}` IS NULL" for x in cols)
            r, err = self.wh.try_rows(f"SELECT count(*), count(DISTINCT {lst}), count_if({nulls}) FROM {c}.gold.`{t}`")
            return t, cols, r[0] if r else None, err

        def fk_check(item):
            name, fk = item
            on = " AND ".join(f"p.`{pc}` = f.`{cc}`" for cc, pc in zip(fk["cc"], fk["pc"]))
            nn = " AND ".join(f"f.`{cc}` IS NOT NULL" for cc in fk["cc"])
            r, err = self.wh.try_rows(f"""SELECT count(*), count_if({nn}),
                count_if({nn} AND NOT EXISTS (SELECT 1 FROM {c}.gold.`{fk['parent']}` p WHERE {on}))
              FROM {c}.gold.`{fk['child']}` f""")
            return name, fk, r[0] if r else None, err

        t0 = time.time()
        pk_res = self.pmap(pk_check, sorted(pks.items()))
        fk_res = self.pmap(fk_check, sorted(fks.items()))
        print(f"{TAG} gold integrity: {len(pk_res)} PKs, {len(fk_res)} FKs checked in {time.time() - t0:.0f}s")

        bad_pk = []
        for t, cols, r, err in pk_res:
            if err or r is None:
                bad_pk.append([t, ", ".join(cols), "", "", err])
                self.find("error", "gold PK", f"{t}: {err}")
            elif int(r[0]) != int(r[1]) or int(r[2]):
                bad_pk.append([t, ", ".join(cols), int(r[0]) - int(r[1]), r[2], ""])
                self.find("error", "gold PK", f"{t} ({', '.join(cols)}): {int(r[0]) - int(r[1])} duplicate, {r[2]} null keys")
        fk_tab, orphan_total = [], 0
        for name, fk, r, err in fk_res:
            if err or r is None:
                self.find("error", "gold FK", f"{name}: {err}")
                fk_tab.append([fk["child"], ", ".join(fk["cc"]), fk["parent"], "", "", "", err])
                continue
            total, nonnull, orphans = int(r[0]), int(r[1]), int(r[2])
            orphan_total += orphans
            if orphans:
                self.find("error", "gold FK", f"{fk['child']}.{'/'.join(fk['cc'])} -> {fk['parent']}: {orphans:,} orphan rows")
            nulls = total - nonnull
            if nulls and fk["parent"] == "dim_date" and fk["cc"][0] in pks.get(fk["child"], []):
                self.find("error", "gold FK", f"{fk['child']}.{fk['cc'][0]}: {nulls:,} grain dates NULL")
            if orphans or nulls:
                fk_tab.append([fk["child"], ", ".join(fk["cc"]), fk["parent"], f"{total:,}", f"{nulls:,}", f"{orphans:,}", ""])

        # golden keys on client-grain facts + grain-date bounds
        gcols = self.wh.rows(f"""SELECT table_name, collect_set(column_name)
          FROM {c}.information_schema.columns WHERE table_schema = 'gold'
           AND column_name IN ('golden_client_id', 'golden_client_sk') GROUP BY table_name""")

        def key_check(item):   # rows, without a golden client, golden id known but no dim_client version
            t, cl = item
            both = "golden_client_id" in cl and "golden_client_sk" in cl
            lost = "count_if(golden_client_id IS NOT NULL AND golden_client_sk IS NULL)" if both else "0"
            key = "golden_client_id" if "golden_client_id" in cl else "golden_client_sk"
            r, err = self.wh.try_rows(f"SELECT count(*), count_if(`{key}` IS NULL), {lost} FROM {c}.gold.`{t}`")
            return t, r[0] if r else None, err

        key_tab, lost_total = [], 0
        tables = {o["table"]: o for o in self.objects if o["schema"] == "gold"}
        for t, r, err in self.pmap(key_check, [(t, cl) for t, cl in gcols if t in tables]):
            if err or r is None:
                self.find("error", "golden keys", f"{t}: {err}")
                continue
            total, without, lost = int(r[0]), int(r[1]), int(r[2])
            lost_total += lost
            if lost:
                self.find("error", "golden keys", f"{t}: {lost:,} rows carry a golden_client_id with no dim_client "
                                                  f"version for their date (SCD2 lookup failure)")
            if without:
                key_tab.append([t, f"{total:,}", f"{without:,}", pct(without, total), f"{lost:,}"])
        self.no_client_rows = key_tab

        date_cols = [(t, cols[0]) for t, cols in pks.items()
                     if cols and t in tables and not FUTURE_OK.match(t)]
        dtypes = {(r[0], r[1]): r[2] for r in self.wh.rows(f"""SELECT table_name, column_name, data_type
          FROM {c}.information_schema.columns WHERE table_schema = 'gold'""")}
        date_pk = [(t, [x for x in pks[t] if dtypes.get((t, x)) == "DATE"]) for t, _ in date_cols]
        date_pk = [(t, d[0]) for t, d in date_pk if d]

        def date_check(item):
            t, col = item
            r, err = self.wh.try_rows(f"SELECT min(`{col}`), max(`{col}`) FROM {c}.gold.`{t}`")
            return t, col, r[0] if r else None

        late = []
        for t, col, r in self.pmap(date_check, date_pk):
            if r and r[1] and r[1] > str(self.cfg.as_of_date):
                late.append([t, col, r[0], r[1]])
                self.find("warn", "gold dates", f"{t}.{col} runs to {r[1]} (after as-of {self.cfg.as_of_date})")

        md = ["## 3. Gold integrity (re-derived from information_schema)", "",
              f"- Primary keys checked: **{len(pk_res)}**; violating: **{len(bad_pk)}**.",
              f"- Foreign keys checked: **{len(fk_res)}**; orphan rows in total: **{orphan_total:,}**.",
              f"- Rows whose golden_client_id finds no dim_client version for their date (SCD2 lookup failures): "
              f"**{lost_total:,}**. Rows without a golden client at all are by design (applicants that never went "
              f"live, prospect screenings, market-residual corridor rows, non-APAC entities, run-level rows); "
              f"**{len(key_tab)}** tables have some, listed below.",
              f"- Tables whose grain date passes the as-of date (forecast / calendar tables excluded): **{len(late)}**.", ""]
        if bad_pk:
            md += md_table(["table", "PK", "duplicates", "null keys", "error"], bad_pk) + [""]
        if fk_tab:
            md += ["FKs with NULL or orphan values:", ""]
            md += md_table(["child", "columns", "parent", "rows", "NULL", "orphans", "error"], fk_tab) + [""]
        if key_tab:
            md += md_table(["table", "rows", "rows without a golden client", "share", "lookup failures"], key_tab) + [""]
        if late:
            md += md_table(["table", "grain date", "min", "max"], late) + [""]
        self.md += md
        self.log.append(("gold_integrity", f"{self.c}.gold", "ok" if not (bad_pk or orphan_total or lost_total) else "error",
                         f"{len(pk_res)} PKs ({len(bad_pk)} bad), {len(fk_res)} FKs ({orphan_total} orphans), "
                         f"{lost_total} SCD2 lookup failures", len(fk_res)))

    # ---- silver DQ -------------------------------------------------------------------------------
    def silver_dq(self) -> None:
        c = self.c
        run = self.wh.rows(f"SELECT max(run_id) FROM {c}.silver.dq_results")[0][0]
        rec = self.wh.rows(f"""SELECT count(*), count_if(reconciles), sum(bronze_rows), sum(silver_rows),
            sum(quarantined_rows), sum(deduplicated_rows), sum(client_rows_in_scope), sum(client_rows_resolved)
          FROM {c}.silver.dq_table_reconciliation WHERE run_id = '{run}'""")[0]
        by_layer = self.wh.rows(f"""SELECT layer, count(*), count_if(status = 'pass'), count_if(status = 'warn'),
            count_if(status = 'fail'), count_if(is_blocking AND status = 'fail' AND layer = 'silver')
          FROM {c}.silver.dq_results WHERE run_id = '{run}' GROUP BY ALL ORDER BY 1""")
        by_dim = self.wh.rows(f"""SELECT dimension, count(*), count_if(status = 'pass'),
            round(sum(total_rows - failed_rows) / sum(total_rows), 6)
          FROM {c}.silver.dq_results WHERE run_id = '{run}' AND layer = 'silver' AND total_rows > 0
          GROUP BY ALL ORDER BY 1""")
        issues = self.wh.rows(f"""SELECT layer, table_name, rule_id, dimension, status, failed_rows, total_rows,
            round(pass_rate, 4), threshold, left(coalesce(detail, ''), 140)
          FROM {c}.silver.dq_results WHERE run_id = '{run}' AND status <> 'pass' ORDER BY layer DESC, status, table_name""")
        rules = self.wh.rows(f"""SELECT dq_dimension, count(*), count_if(is_blocking)
          FROM {c}.ops.dq_rules GROUP BY ALL ORDER BY 1""")
        n, ok = int(rec[0]), int(rec[1])
        blocking = sum(int(r[5]) for r in by_layer)
        cov = num(rec[7]) / num(rec[6]) if num(rec[6]) else 0.0
        if ok != n:
            self.find("error", "silver DQ", f"{n - ok} of {n} tables do not reconcile bronze = silver + quarantined + de-duplicated")
        if blocking:
            self.find("error", "silver DQ", f"{blocking} blocking rule failures in silver")
        if cov < 0.995:
            self.find("error", "silver DQ", f"golden-key coverage {cov:.4%} < 99.5%")
        for r in issues:
            if r[0] != "silver":
                continue
            if r[3] == "timeliness" and PROCESS_SLA.search(r[2]):
                self.find("info", "process SLA", f"{r[1]} {r[2]}: {r[7]} on time (an operational backlog the "
                                                 f"demo measures, not a data defect)")
            else:
                self.find("warn" if r[4] == "warn" else "error", "silver DQ", f"{r[1]} {r[2]} ({r[3]}): {r[4]} pass_rate {r[7]}")
        md = ["## 4. Silver data quality (latest DQ run)", "",
              f"- DQ run **{run}**; rule catalogue `ops.dq_rules`: **{sum(int(r[1]) for r in rules)}** rules, "
              f"**{sum(int(r[2]) for r in rules)}** blocking.",
              f"- Reconciliation: **{ok}/{n}** tables satisfy bronze = silver + quarantined + de-duplicated "
              f"({num(rec[2]):,.0f} = {num(rec[3]):,.0f} + {num(rec[4]):,.0f} + {num(rec[5]):,.0f}).",
              f"- Golden-key coverage of client-grain silver rows: **{cov:.4%}** of {num(rec[6]):,.0f} rows.",
              f"- Blocking-rule failures in silver: **{blocking}**.", ""]
        md += md_table(["dimension", "rules", "rule catalogue blocking"], [[r[0], r[1], r[2]] for r in rules]) + [""]
        md += md_table(["layer", "results", "pass", "warn", "fail", "blocking fails (silver)"], by_layer) + [""]
        md += md_table(["silver dimension", "results", "pass", "row-weighted pass rate"], by_dim) + [""]
        if issues:
            md += ["Every non-pass result (bronze results measure the designed source noise; they are what silver repairs):", ""]
            md += md_table(["layer", "table", "rule", "dimension", "status", "failed", "total", "pass rate",
                            "threshold", "detail"], issues) + [""]
        self.md += md
        self.log.append(("silver_dq", f"{self.c}.silver", "ok" if ok == n and not blocking else "error",
                         f"run {run}: {ok}/{n} reconcile, {blocking} blocking fails, coverage {cov:.4%}", n))

    # ---- ER, storylines, build logs --------------------------------------------------------------
    def er_and_storylines(self) -> None:
        c = self.c
        er = self.wh.rows(f"""SELECT run_id, run_date, rule_version, records_in, candidate_pairs, auto_match_pairs,
            steward_items, steward_items_open, golden_records, round(precision, 4), round(recall, 4), round(purity, 4)
          FROM {c}.ops.entity_resolution_runs WHERE source_system = 'ALL' ORDER BY run_date""")
        if er:
            last = er[-1]
            if num(last[9]) < 0.97 or num(last[10]) < 0.95:
                self.find("error", "ER", f"latest run {last[0]} precision {last[9]} / recall {last[10]} below 0.97 / 0.95")
        st = self.wh.rows(f"""SELECT storyline_id, storyline_name, count(*), count_if(passed), max(checked_at)
          FROM {c}.ops.storyline_assertions GROUP BY ALL ORDER BY 1""")
        fails = self.wh.rows(f"""SELECT storyline_id, assertion_key, description, expected, actual
          FROM {c}.ops.storyline_assertions WHERE NOT passed ORDER BY 1""")
        for f in fails:
            self.find("error", "storylines", f"#{f[0]} {f[1]}: expected {f[3]}, actual {f[4]}")
        logs = self.wh.rows(f"""WITH last AS (
            SELECT phase, max_by(run_id, started_at) run_id FROM {c}.ops.build_run_log
            WHERE phase <> 'p08_validate' GROUP BY phase)
          SELECT l.phase, l.run_id, min(b.started_at), max(b.finished_at), count(*),
            concat_ws(', ', collect_set(b.status))
          FROM last l JOIN {c}.ops.build_run_log b ON b.phase = l.phase AND b.run_id = l.run_id
          GROUP BY ALL ORDER BY 1""")
        passed = sum(int(r[3]) for r in st)
        total = sum(int(r[2]) for r in st)
        md = ["## 5. Entity resolution", ""]
        md += md_table(["run", "date", "rules", "records", "candidate pairs", "auto-match pairs", "steward items",
                        "open", "golden clients", "precision", "recall", "purity"], er) + [""]
        md += ["## 6. Storylines and build runs", "",
               f"Storyline assertions (`ops.storyline_assertions`): **{passed}/{total}** pass.", ""]
        md += md_table(["#", "storyline", "assertions", "passed", "checked at"], st) + [""]
        md += ["Latest run of every build phase (`ops.build_run_log`):", ""]
        md += md_table(["phase", "run", "started", "finished", "steps", "statuses"], logs) + [""]
        self.md += md
        self.log.append(("storylines", f"{self.c}.ops.storyline_assertions", "ok" if passed == total else "error",
                         f"{passed}/{total} pass", total))

    # ---- metric views ----------------------------------------------------------------------------
    def metric_views(self) -> None:
        plan = (REPO / "docs" / "PLAN.md").read_text()
        sec = plan[plan.find("## 8. Metric views"):plan.find("## 9. Genie spaces")]
        planned = sorted(set(re.findall(r"`(mv_[a-z0-9_]+)`", sec)))
        present = {o["table"] for o in self.objects if o["schema"] == "metrics"}
        missing = [v for v in planned if v not in present]
        for v in missing:
            self.find("error", "metric views", f"{v} planned but not in {self.c}.metrics")

        def probe(v):
            path = REPO / "metrics" / f"{v}.yaml"
            try:
                doc = yaml.safe_load(path.read_text()) if path.exists() else {}
                ms = [m["name"] for m in (doc.get("measures") or []) if isinstance(m, dict) and m.get("name")]
            except Exception:  # noqa: BLE001
                ms = []
            if not ms:
                return v, None, "no measures found in metrics/<view>.yaml"
            sel = ", ".join(f"MEASURE(`{m}`)" for m in ms[:40])
            r, err = self.wh.try_rows(f"SELECT {sel} FROM {self.c}.metrics.`{v}`")
            return v, len(ms), err

        res = self.pmap(probe, sorted(present))
        bad = [(v, n, e) for v, n, e in res if e]
        for v, n, e in bad:
            self.find("error", "metric views", f"{v}: {e}")
        md = ["## 7. Metric views", "",
              f"- Planned (PLAN §8): **{len(planned)}**; present in `{self.c}.metrics`: **{len(present)}**; "
              f"missing: **{len(missing)}**{(' (' + ', '.join(missing) + ')') if missing else ''}.",
              f"- Every measure of each present view queried with `MEASURE()` over the whole view: "
              f"**{len(res) - len(bad)}/{len(res)}** views answer.", ""]
        if res:
            md += md_table(["view", "measures", "probe"], [[v, n, e or "ok"] for v, n, e in res]) + [""]
        self.md += md
        self.log.append(("metric_views", f"{self.c}.metrics", "ok" if not (missing or bad) else "error",
                         f"{len(present)}/{len(planned)} present, {len(bad)} failing", len(present)))

    # ---- Genie spaces --------------------------------------------------------------------------------
    def genie_spaces(self) -> None:
        slugs = sorted(p.parent.name for p in (REPO / "genie").glob("*/space.yaml"))
        rows, err = self.wh.try_rows(f"""SELECT space_slug, count(*), count_if(last_eval_pass), max(space_pass_rate),
            max(checked_at), max_by(space_id, checked_at) FROM {self.c}.ops.genie_benchmarks GROUP BY 1""")
        res = {r[0]: r for r in rows}
        tab, below = [], 0
        for s in slugs:
            sid_path = REPO / "genie" / s / "space_id"
            r = res.get(s)
            # the space_id file is written where run_genie.py --create ran; elsewhere (e.g. a fresh clone)
            # fall back to the space its last benchmark run recorded
            sid = (sid_path.read_text().strip() if sid_path.exists() else "") or (r[5] if r and r[5] else "")
            rate = num(r[3]) if r and r[3] is not None else None
            if not sid:
                self.find("error", "Genie", f"{s}: no space created (genie/{s}/space_id missing)")
            if rate is None:   # created but not evaluated yet (run_genie.py --evaluate): not a data defect
                self.find("warn", "Genie", f"{s}: no benchmark evaluation in ops.genie_benchmarks")
            elif rate < 0.85:
                below += 1
                self.find("warn", "Genie", f"{s}: benchmark pass rate {rate:.0%} < 85%")
            tab.append([s, sid, r[1] if r else "", r[2] if r else "", f"{rate:.0%}" if rate is not None else "", r[4] if r else ""])
        total = sum(int(r[1]) for r in rows) if rows else 0
        passed = sum(int(r[2]) for r in rows) if rows else 0
        self.md += ["## 9. Genie spaces", "",
                    f"- Spaces authored (`genie/<slug>/space.yaml`): **{len(slugs)}**; created: "
                    f"**{sum(1 for t in tab if t[1])}**; below the 85% benchmark target: **{below}**.",
                    f"- Benchmarks (`ops.genie_benchmarks`, graded by Genie eval-runs + result comparison): "
                    f"**{passed}/{total}** pass ({pct(passed, total)}).", ""]
        self.md += md_table(["space", "space id", "benchmarks", "pass", "pass rate", "last evaluated"], tab) + [""]
        self.log.append(("genie", f"{self.c} genie spaces", "ok" if not below and len(tab) == len(slugs) else "warn",
                         f"{len(slugs)} spaces, {passed}/{total} benchmarks pass", len(slugs)))

    # ---- headline KPIs (coherence snapshot across domains) -----------------------------------------
    def kpis(self) -> None:
        g, a = f"{self.c}.gold", f"DATE'{self.cfg.as_of_date}'"
        items = [
            ("Golden clients / client groups (current)",
             f"SELECT concat(format_number(count(*), 0), ' / ', format_number(count(DISTINCT client_group_id), 0)) "
             f"FROM {g}.dim_client WHERE is_current"),
            ("Deposits at the as-of month-end (USD bn) / CASA ratio",
             f"SELECT concat(round(sum(balance_usd) / 1e9, 2), ' / ', round(sum(casa_balance_usd) / sum(balance_usd), 3)) "
             f"FROM {g}.fact_deposit_balance_monthly WHERE balance_date = {a}"),
            ("Lending drawn / limit at the as-of month-end (USD bn) / ECL (USD m)",
             f"SELECT concat(round(sum(drawn_usd) / 1e9, 2), ' / ', round(sum(limit_usd) / 1e9, 2), ' / ', "
             f"round(sum(ecl_usd) / 1e6, 1)) FROM {g}.fact_credit_exposure_monthly WHERE month_end_date = {a}"),
            ("EWS final band at the as-of date (Green / Amber / Red clients)",
             f"SELECT concat(count_if(final_band = 'Green'), ' / ', count_if(final_band = 'Amber'), ' / ', "
             f"count_if(final_band = 'Red')) FROM {g}.fact_ews_score_daily WHERE score_date = {a}"),
            ("Trade finance outstanding at the as-of month-end (USD bn)",
             f"SELECT round(sum(outstanding_usd) / 1e9, 3) FROM {g}.fact_trade_outstanding_monthly WHERE month_end_date = {a}"),
            ("Payments H1 FY2026 (count / USD bn)",
             f"SELECT concat(format_number(count(*), 0), ' / ', round(sum(amount_usd) / 1e9, 2)) "
             f"FROM {g}.fact_payment_transaction WHERE payment_date BETWEEN DATE'2026-04-01' AND {a}"),
            ("Client revenue FY2025 (USD m) / net contribution (USD m)",
             f"SELECT concat(round(sum(total_revenue_usd) / 1e6, 1), ' / ', round(sum(net_contribution_usd) / 1e6, 1)) "
             f"FROM {g}.fact_client_revenue_monthly WHERE fiscal_year = 2025"),
            ("Onboarding cases (all / live)",
             f"SELECT concat(count(*), ' / ', count_if(is_live)) FROM {g}.fact_onboarding_case"),
        ]
        res = self.pmap(lambda it: (it[0],) + self.wh.try_rows(it[1]), items)
        rows = [[label, (r[0][0] if r and r[0] else "") if not err else f"n/a ({err[:80]})"] for label, r, err in res]
        self.md += ["## 8. Headline KPIs (gold, as-of " + str(self.cfg.as_of_date) + ")", "",
                    "A coherence snapshot: totals across domains, read from gold.", ""]
        self.md += md_table(["KPI", "value"], rows) + [""]

    # ---- report ----------------------------------------------------------------------------------
    def write(self) -> None:
        sev = {s: [f for f in self.findings if f[0] == s] for s in ("error", "warn", "info")}
        head = [f"# Data-asset and data-quality report — catalog `{self.c}`", "",
                f"Generated by `src/60_validate/run_validate.py` (run `{self.run_id}`, "
                f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC). As-of date {self.cfg.as_of_date}, "
                f"SCALE {self.cfg.scale}. Read-only audit: constraints, comments and counts are re-derived from the "
                f"workspace, not from the build specs.", "",
                "## 2. Findings", "",
                f"**{len(sev['error'])} errors, {len(sev['warn'])} warnings, {len(sev['info'])} notes.**", ""]
        for s in ("error", "warn", "info"):
            for _, area, text in sev[s]:
                head.append(f"- **{s}** — {area}: {text}")
        if not self.findings:
            head.append("- none")
        body = self.md
        inv_end = next((i for i, l in enumerate(body) if l.startswith("## 3.")), len(body))
        REPORT.write_text("\n".join(head[:4] + body[:inv_end] + head[4:] + [""] + body[inv_end:]) + "\n")
        print(f"{TAG} report written: {REPORT.relative_to(REPO)} ({len(sev['error'])} errors, {len(sev['warn'])} warnings)")

    def write_log(self) -> None:
        now = f"TIMESTAMP'{dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M:%S}'"

        def q(s: Optional[str]) -> str:
            return "NULL" if s is None else "'" + str(s).replace("\\", "\\\\").replace("'", "\\'") + "'"

        rows = [f"({q(self.run_id)}, 'p08_validate', {q(step)}, {q(obj)}, {'NULL' if n is None else int(n)}, "
                f"{q(st)}, {q(msg[:900])}, {float(self.cfg.scale)}, {now}, {now}, 0.0)"
                for step, obj, st, msg, n in self.log]
        errors = sum(1 for f in self.findings if f[0] == "error")
        warns = sum(1 for f in self.findings if f[0] == "warn")
        rows.append(f"({q(self.run_id)}, 'p08_validate', 'summary', {q(self.c)}, {len(self.findings)}, "
                    f"{q('error' if errors else 'ok')}, {q(f'{errors} errors, {warns} warnings')}, "
                    f"{float(self.cfg.scale)}, {now}, {now}, 0.0)")
        try:
            self.wh.rows(f"INSERT INTO {self.c}.ops.build_run_log (run_id, phase, step, object_name, row_count, status, "
                         f"message, scale, started_at, finished_at, duration_sec) VALUES\n" + ",\n".join(rows))
        except Exception as e:  # noqa: BLE001 - logging must never mask the audit result
            print(f"{TAG} WARN could not write ops.build_run_log: {str(e)[:200]}")


def main() -> None:
    a = parse_args()
    require_warehouse(a.warehouse_id, TAG)
    audit = Audit(a)
    t0 = time.time()
    audit.inventory()
    audit.gold_integrity()
    audit.silver_dq()
    audit.er_and_storylines()
    audit.metric_views()
    audit.kpis()
    audit.genie_spaces()
    audit.write()
    if not a.no_log:
        audit.write_log()
    errors = sum(1 for f in audit.findings if f[0] == "error")
    print(f"{TAG} done in {time.time() - t0:.0f}s: {errors} errors, "
          f"{sum(1 for f in audit.findings if f[0] == 'warn')} warnings (run_id={audit.run_id})")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
