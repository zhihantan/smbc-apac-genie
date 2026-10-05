"""Phase 4b — silver standardisation + data quality (PLAN §6, §11; DECISIONS D11, D13, D22, D25, D37).

  silver.<t>                      typed, trimmed, de-duplicated, conformed copy of each bronze / shared fact
                                  and reference table in config/table_specs.yaml: golden_client_id through
                                  silver.xref_client_source from the table's own identity key, client_group_id
                                  from silver.client_golden_identity (shared / group-grain rows: the mastered
                                  group key); comments from config/column_dictionary.yaml, smbc_ tags (D37)
  silver.quarantine_<t>           rows failing a blocking rule (rule id + reason), only where there are any
  silver.dq_table_reconciliation  per table: bronze rows = silver + quarantined + de-duplicated, golden-key
                                  coverage, landing dirtiness
  silver.dq_results               this run's result per rule and layer (bronze / shared landing, silver)
  silver.dq_score_history         monthly score per layer x table x dimension, Apr-2023..as-of (latest = run)
  silver.golden_record_coverage_monthly  golden client x month x source system present (record availability)
  silver.golden_attribute_completeness   per golden client: which key attributes are populated
  ops.dq_rules                    the rule catalogue (generated from table_specs + config/dq_rules.yaml)

Runs after entity resolution (src/20_silver/entity_resolution/run_er.py) and reads its silver tables.
Spec-driven and re-runnable (overwrites; dq_results replaces this as-of date's run).

  .venv/bin/python src/20_silver/run_silver.py --profile my-workspace [--only t1,t2]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

import yaml  # noqa: E402

from smbc_genie_lib import dq, silver, storylines  # noqa: E402
from smbc_genie_lib.config import export_env, load_config  # noqa: E402
from smbc_genie_lib.er import records as er_records  # noqa: E402
from smbc_genie_lib.silver import q  # noqa: E402
from smbc_genie_lib.spark_io import get_spark, sql_escape, write_table  # noqa: E402

TAG = "[p04]"
WORKERS = 6
DICTIONARY = REPO / "config" / "column_dictionary.yaml"
# column specs of the outputs built here: (name, Spark type)
OUT = {
    "dq_results": [("run_id", "string"), ("run_ts", "timestamp"), ("rule_id", "string"), ("layer", "string"),
                   ("table_name", "string"), ("column_name", "string"), ("dimension", "string"),
                   ("rule_kind", "string"), ("severity", "string"), ("is_blocking", "boolean"), ("action", "string"),
                   ("total_rows", "bigint"), ("failed_rows", "bigint"), ("pass_rate", "double"),
                   ("threshold", "double"), ("status", "string"), ("sample_keys", "array<string>"),
                   ("detail", "string"), ("storyline", "int")],
    "dq_score_history": [("month", "date"), ("layer", "string"), ("table_name", "string"), ("dimension", "string"),
                         ("score", "double"), ("rules_evaluated", "int"), ("rules_failed", "int"),
                         ("rows_evaluated", "bigint"), ("rows_failed", "bigint"), ("basis", "string"),
                         ("incident_key", "string"), ("incident_note", "string"), ("storyline", "int"),
                         ("run_id", "string")],
    "dq_table_reconciliation": [
        ("run_id", "string"), ("table_name", "string"), ("source_table", "string"), ("domain", "string"),
        ("bronze_rows", "bigint"), ("deduplicated_rows", "bigint"), ("quarantined_rows", "bigint"),
        ("silver_rows", "bigint"), ("reconciles", "boolean"), ("dedup_by_batch", "map<string,bigint>"),
        ("quarantine_by_rule", "map<string,bigint>"), ("landing_dirty_rows", "bigint"),
        ("landing_dirty_pct", "double"), ("client_rows_in_scope", "bigint"), ("client_rows_resolved", "bigint"),
        ("golden_key_coverage", "double"), ("landing_resolution_rate", "double"), ("n_columns", "int"),
        ("undeclared_columns", "array<string>"), ("missing_columns", "array<string>"), ("loaded_at", "timestamp")],
}
OPS_EXTRA = [("rule_kind", "STRING", "Generated check type (key_present, key_unique, golden_key, valid_values, ...) or hand-written kind"),
             ("origin", "STRING", "generated (from config/table_specs.yaml) | hand_written (config/dq_rules.yaml)"),
             ("is_blocking", "BOOLEAN", "Failing rows are quarantined to silver.quarantine_<table>"),
             ("action_on_fail", "STRING", "quarantine | deduplicate | flag"),
             ("evaluated_layers", "STRING", "Layers the rule is measured on: bronze (landing) and/or silver"),
             ("rule_grain", "STRING", "row | table (one check per table, e.g. freshness)"),
             ("rule_parts", "ARRAY<STRING>", "Checks combined in a composite rule (valid values, date order, ...)"),
             ("rule_from", "STRING", "FROM clause of a hand-written rule"),
             ("rule_where", "STRING", "Scope filter: only rows where this holds are evaluated"),
             ("storyline", "INT", "Storyline number the rule evidences (brief §6.4), if any"),
             ("updated_at", "TIMESTAMP", "When the catalogue row was loaded")]


# ---- comments + tags ----------------------------------------------------------------------------
class Dictionary:
    """config/column_dictionary.yaml: shared column descriptions + per-table overrides (D25)."""

    def __init__(self, path: Path):
        d = yaml.safe_load(path.read_text()) if path.exists() else {}
        self.columns = d.get("columns") or {}
        self.tables = d.get("tables") or {}
        self.table_comments = d.get("table_comments") or {}
        self.missing: set = set()

    def get(self, table: str, column: str):
        text = (self.tables.get(table) or {}).get(column) or self.columns.get(column)
        if text is None:
            self.missing.add(f"{table}.{column}")
        return text


def commented(df, table: str, dictionary: Dictionary):
    from pyspark.sql import functions as F
    out = []
    for c in df.columns:
        text = dictionary.get(table, c)
        out.append(F.col(f"`{c}`").alias(c, metadata={"comment": text}) if text else F.col(f"`{c}`"))
    return df.select(*out)


def set_tags(spark, fqn: str, tags: Dict[str, str]) -> None:
    pairs = ", ".join(f"'{k}' = '{v}'" for k, v in tags.items())
    try:
        spark.sql(f"ALTER TABLE {fqn} SET TAGS ({pairs})")
    except Exception as e:  # noqa: BLE001 - tags are governance metadata; never fail the load on them
        print(f"{TAG} WARN tags on {fqn}: {str(e)[:120]}")


def write_out(spark, df, fqn: str, table: str, ctx) -> int:
    n = write_table(commented(df, table, ctx.dictionary), fqn,
                    comment=ctx.dictionary.table_comments.get(table) or ctx.specs.tables.get(table, None) and
                    ctx.specs.tables[table].comment)
    set_tags(spark, fqn, silver.table_tags(table, ctx.specs.tags))
    return n


def spark_type(t: str):
    from pyspark.sql import types as T
    simple = {"string": T.StringType(), "timestamp": T.TimestampType(), "date": T.DateType(), "boolean": T.BooleanType(),
              "bigint": T.LongType(), "int": T.IntegerType(), "double": T.DoubleType()}
    if t in simple:
        return simple[t]
    if t.startswith("array<"):
        return T.ArrayType(spark_type(t[6:-1]))
    if t.startswith("map<"):
        k, v = t[4:-1].split(",")
        return T.MapType(spark_type(k.strip()), spark_type(v.strip()))
    raise ValueError(f"unsupported type {t}")


def rows_df(spark, rows: List[Dict[str, Any]], spec):
    import pandas as pd
    from pyspark.sql.types import StructField, StructType
    schema = StructType([StructField(n, spark_type(t), True) for n, t in spec])
    data = [[r.get(n) for n, _ in spec] for r in rows]
    if not data:
        return spark.createDataFrame([], schema)
    return spark.createDataFrame(pd.DataFrame(data, columns=[n for n, _ in spec], dtype=object), schema=schema)


# ---- per-table load -----------------------------------------------------------------------------
class Ctx:
    def __init__(self, spark, cfg, specs, rules, schemas, dictionary, run_id, run_ts):
        self.spark, self.cfg, self.specs, self.rules = spark, cfg, specs, rules
        self.schemas, self.dictionary, self.run_id, self.run_ts = schemas, dictionary, run_id, run_ts
        self.c = cfg.catalog


def _results(ctx, agg, rules, layer, label, table_name) -> List[Dict[str, Any]]:
    out = []
    for i, r in enumerate(rules):
        parts = r.parts_for(layer)
        detail = {p[0]: int(agg[f"p_{i}_{j}"]) for j, p in enumerate(parts) if agg[f"p_{i}_{j}"]} or None
        out.append(dq.result_row(r, label, table_name, agg[f"n_{i}"], agg[f"f_{i}"], agg[f"s_{i}"],
                                 ctx.run_id, ctx.run_ts, detail))
    return out


def _table_grain(ctx, rules, relation, layer, label, table_name) -> List[Dict[str, Any]]:
    out = []
    for r in rules:
        if r.grain == "table" and r.expr_for(layer):
            x = ctx.spark.sql(dq.table_rule_sql(relation, r)).collect()[0]
            out.append(dq.result_row(r, label, table_name, x["total"], x["failed"], x["samples"], ctx.run_id, ctx.run_ts))
    return out


def load_table(ctx: Ctx, spec) -> Dict[str, Any]:
    spark, c, conform = ctx.spark, ctx.c, ctx.specs.conform
    t0, schema = time.time(), ctx.schemas[spec.name]
    blocking = dq.blocking_rules(ctx.rules, spec.name)
    sql, cols, drift = silver.staged_sql(spec, schema, c, conform,
                                         [(r.rule_id, r.expr_for("bronze"), r.joins, r.exposes) for r in blocking])
    view = f"p04_stg_{spec.name}"
    spark.sql(sql).createOrReplaceTempView(view)
    staged_cols = spark.table(view).columns
    mine = [r for r in ctx.rules if r.table == spec.name and r.origin == "generated" and r.kind != "references"]
    landing, land_name = spec.schema, spec.source  # 'bronze' | 'shared'

    # 1) the landing, measured before de-duplication / quarantine, plus the load counts
    rows_b = [r for r in mine if r.grain == "row" and r.expr_for("bronze")]
    every = " AND ".join(f"coalesce({r.expr_for('bronze')}, true)" for r in rows_b) or "true"
    extra = ["count_if(_dedup_rank > 1) AS __dedup", "count_if(_dedup_rank = 1 AND size(_dq_block) > 0) AS __quar",
             f"count_if(NOT ({every})) AS __dirty"]
    dtype = cols.get(spec.date_column or "", "")
    if dtype in ("date", "timestamp"):
        extra.append(f"CAST(min({q(spec.date_column)}) AS DATE) AS __min_date")
    scope = None
    if spec.client:
        scope = silver.client_scope(spec, conform) or "true"
        extra += [f"count_if({scope}) AS __scope", f"count_if(({scope}) AND golden_client_id IS NOT NULL) AS __resolved"]
    agg = spark.sql(dq.batch_sql(view, spec.keys, rows_b, "bronze", c, staged_cols, extra)).collect()[0].asDict()
    results = _results(ctx, agg, rows_b, "bronze", landing, land_name)
    dedup_by_batch, quar_by_rule = {}, {}
    if agg["__dedup"]:
        bcol = "_batch_id" if "_batch_id" in cols else "'(no batch id)'"
        dedup_by_batch = {r["b"]: int(r["n"]) for r in spark.sql(
            f"SELECT coalesce(CAST({bcol} AS STRING), '(none)') AS b, count(*) AS n FROM {view} WHERE _dedup_rank > 1 "
            f"GROUP BY 1").collect()}
        for res in results:
            if res["rule_kind"] == "key_unique":
                res["detail"] = json.dumps({"removed_by_batch": dedup_by_batch}, sort_keys=True)
    if agg["__quar"]:
        quar_by_rule = {r["r"]: int(r["n"]) for r in spark.sql(
            f"SELECT r, count(*) AS n FROM (SELECT explode(_dq_block) AS r FROM {view} WHERE _dedup_rank = 1) GROUP BY r"
        ).collect()}
    results += _table_grain(ctx, mine, view, "bronze", landing, land_name)

    # 2) silver + quarantine
    fqn, qfqn = f"{c}.{spec.target}", f"{c}.{spec.quarantine}"
    sel = silver.silver_select(cols, spec, conform)
    keep = spark.table(view).where("_dedup_rank = 1 AND size(_dq_block) = 0").select(*[f"`{x}`" for x in sel])
    n_silver = write_out(spark, keep, fqn, spec.name, ctx)
    n_quar = 0
    if agg["__quar"]:
        reasons = silver.reason_map_sql({r.rule_id: r.description for r in blocking})
        qdf = spark.sql(f"""SELECT {', '.join(q(x) for x in sel)}, _dq_block[0] AS _dq_rule_id, _dq_block AS _dq_rule_ids,
                                   element_at({reasons}, _dq_block[0]) AS _dq_reason, '{ctx.run_id}' AS _dq_run_id,
                                   CAST('{ctx.run_ts.isoformat(sep=' ')}' AS TIMESTAMP) AS _quarantined_at
                            FROM {view} WHERE _dedup_rank = 1 AND size(_dq_block) > 0""")
        n_quar = write_table(commented(qdf, spec.name, ctx.dictionary), qfqn,
                             comment=f"Quarantined rows of silver.{spec.name}: rows of {spec.source} failing a blocking "
                                     f"DQ rule (rule id + reason); never published to silver.")
        set_tags(spark, qfqn, silver.table_tags(spec.name, ctx.specs.tags))
    else:
        spark.sql(f"DROP TABLE IF EXISTS {qfqn}")

    # 3) the published silver table
    rows_s = [r for r in mine if r.grain == "row" and r.kind != "key_unique" and r.expr_for("silver")]
    sextra = [f"count_if({scope}) AS __scope", f"count_if(({scope}) AND golden_client_id IS NOT NULL) AS __resolved"] \
        if scope else []
    sagg = spark.sql(dq.batch_sql(fqn, spec.keys, rows_s, "silver", c, (), sextra)).collect()[0].asDict()
    results += _results(ctx, sagg, rows_s, "silver", "silver", spec.target)
    u = spark.sql(dq.unique_sql(fqn, spec.keys)).collect()[0]
    ur = next(r for r in mine if r.kind == "key_unique")
    results.append(dq.result_row(ur, "silver", spec.target, u["total"], u["failed"], u["samples"], ctx.run_id, ctx.run_ts))
    results += _table_grain(ctx, mine, fqn, "silver", "silver", spec.target)

    total = int(agg["__total"])
    summary = {
        "run_id": ctx.run_id, "table_name": spec.target, "source_table": spec.source, "domain": spec.domain,
        "bronze_rows": total, "deduplicated_rows": int(agg["__dedup"]), "quarantined_rows": int(agg["__quar"]),
        "silver_rows": int(n_silver), "reconciles": total == n_silver + int(agg["__quar"]) + int(agg["__dedup"])
        and n_quar == int(agg["__quar"]), "dedup_by_batch": dedup_by_batch or None,
        "quarantine_by_rule": quar_by_rule or None, "landing_dirty_rows": int(agg["__dirty"]),
        "landing_dirty_pct": agg["__dirty"] / total if total else 0.0,
        "client_rows_in_scope": int(sagg["__scope"]) if scope else None,
        "client_rows_resolved": int(sagg["__resolved"]) if scope else None,
        "golden_key_coverage": (sagg["__resolved"] / sagg["__scope"] if sagg["__scope"] else 1.0) if scope else None,
        "landing_resolution_rate": (agg["__resolved"] / agg["__scope"] if agg["__scope"] else 1.0) if scope else None,
        "n_columns": len(sel), "undeclared_columns": drift["undeclared"] or None,
        "missing_columns": drift["missing"] or None, "loaded_at": ctx.run_ts,
        "_min_date": agg.get("__min_date"), "_secs": time.time() - t0}
    print(f"{TAG} {spec.target:44} bronze {total:>9,} = silver {n_silver:>9,} + quarantined {agg['__quar']:>6,} "
          f"+ de-duplicated {agg['__dedup']:>6,} {'ok' if summary['reconciles'] else 'MISMATCH'} "
          f"({summary['_secs']:.0f}s)", flush=True)
    spark.catalog.dropTempView(view)
    return {"summary": summary, "results": results}


# ---- main ---------------------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 4b: silver standardisation + data quality")
    ap.add_argument("--catalog", default=None)
    ap.add_argument("--profile", default=None)
    ap.add_argument("--only", default=None, help="comma-separated tables (development: no catalogue-wide outputs)")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--scale", default=None, help="override config scale (exported as SMBC_SCALE)")
    ap.add_argument("--as-of", dest="as_of", default=None, help="override config as_of_date")
    ap.add_argument("--seed", default=None, help="override config random_seed")
    args = ap.parse_args()
    export_env(scale=args.scale, as_of_date=args.as_of, random_seed=args.seed)
    cfg = load_config(catalog=args.catalog) if args.catalog else load_config()
    c, as_of = cfg.catalog, cfg.as_of_date
    spark = get_spark(args.profile)
    t0 = time.time()
    specs, rcfg = silver.load_specs(), dq.load_rules_config()
    run_id, run_ts = f"DQ-{as_of.replace('-', '')}", _dt.datetime.now().replace(microsecond=0)
    params = {"catalog": c, "as_of": as_of, **{k: v for k, v in cfg.thresholds.items()}}

    schemas = {n: {f.name: f.dataType.simpleString() for f in spark.table(f"{c}.{s.source}").schema.fields}
               for n, s in specs.tables.items()}
    conformance = {}
    for n, s in specs.tables.items():
        cols, _ = silver.resolve_columns(s, schemas[n])
        conformance[n] = silver.conformance_expr(cols, schemas[n])
    rules = dq.generate_rules(specs, as_of, rcfg.get("thresholds"), conformance) + dq.hand_rules(rcfg)
    dq.check_catalogue(rules)
    counts = dq.dimension_counts(rules)
    print(f"{TAG} run {run_id}: {len(specs.tables)} tables, {len(rules)} DQ rules "
          f"({sum(r.origin == 'generated' for r in rules)} generated, {sum(r.origin == 'hand_written' for r in rules)} "
          f"hand-written; {', '.join(f'{k} {v}' for k, v in counts.items())}; {sum(r.blocking for r in rules)} blocking)")
    ctx = Ctx(spark, cfg, specs, rules, schemas, Dictionary(DICTIONARY), run_id, run_ts)

    only = set(args.only.split(",")) if args.only else None
    outs: List[Dict[str, Any]] = []
    failed: List[str] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for level in silver.load_order(specs):
            todo = [specs.tables[n] for n in level if only is None or n in only]
            for spec, fut in [(s, pool.submit(load_table, ctx, s)) for s in todo]:
                try:
                    outs.append(fut.result())
                except Exception as e:  # noqa: BLE001 - report every failing table, then exit non-zero
                    failed.append(spec.name)
                    print(f"{TAG} ERROR {spec.name}: {str(e)[:600]}", flush=True)
    results = [r for o in outs for r in o["results"]]
    summaries = [o["summary"] for o in outs]
    if only is None and not failed:
        results += soft_references(ctx, [s for s in specs.tables.values()])
        hand = [r for r in rules if r.origin == "hand_written"]
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for r, fut in [(r, pool.submit(lambda r=r: spark.sql(dq.hand_sql(r, params)).collect()[0])) for r in hand]:
                try:
                    x = fut.result()
                    results.append(dq.result_row(r, r.target_layer, dq.rule_table_name(r, specs), x["total"],
                                                 x["failed"], x["samples"], run_id, run_ts))
                except Exception as e:  # noqa: BLE001
                    failed.append(r.rule_id)
                    print(f"{TAG} ERROR rule {r.rule_id}: {str(e)[:400]}", flush=True)
        write_catalogue_outputs(ctx, rules, results, summaries, params, rcfg)
    report(ctx, results, summaries, rules)
    print(f"{TAG} done in {time.time() - t0:.0f}s{'; FAILED: ' + ', '.join(failed) if failed else ''}.")
    sys.exit(1 if failed else 0)


def soft_references(ctx, specs_list) -> List[Dict[str, Any]]:
    """Soft foreign keys (references rules) on the published silver tables, once every table is loaded."""
    out = []
    for spec in specs_list:
        rules = [r for r in ctx.rules if r.table == spec.name and r.kind == "references"]
        if rules:
            fqn = f"{ctx.c}.{spec.target}"
            agg = ctx.spark.sql(dq.batch_sql(fqn, spec.keys, rules, "silver", ctx.c)).collect()[0].asDict()
            out += _results(ctx, agg, rules, "silver", "silver", spec.target)
    return out


def write_catalogue_outputs(ctx, rules, results, summaries, params, rcfg) -> None:
    spark, c = ctx.spark, ctx.c
    # ops.dq_rules (Phase-2 DDL + appended columns)
    fqn = f"{c}.ops.dq_rules"
    have = {f.name for f in spark.table(fqn).schema.fields}
    missing = [(n, t, cm) for n, t, cm in OPS_EXTRA if n not in have]
    if missing:
        spark.sql(f"ALTER TABLE {fqn} ADD COLUMNS ({', '.join(f'{n} {t} COMMENT {chr(39)}{cm}{chr(39)}' for n, t, cm in missing)})")
    cat = dq.catalogue_rows(rules, ctx.specs, params)
    for r in cat:
        r["updated_at"] = ctx.run_ts
    spec = [("rule_id", "string"), ("layer", "string"), ("table_name", "string"), ("column_name", "string"),
            ("dq_dimension", "string"), ("rule_expr", "string"), ("severity", "string"), ("threshold", "double"),
            ("description", "string"), ("rule_kind", "string"), ("origin", "string"), ("is_blocking", "boolean"),
            ("action_on_fail", "string"), ("evaluated_layers", "string"), ("rule_grain", "string"),
            ("rule_parts", "array<string>"), ("rule_from", "string"), ("rule_where", "string"), ("storyline", "int"),
            ("updated_at", "timestamp")]
    rows_df(spark, cat, spec).createOrReplaceTempView("p04_dq_rules")
    names = ", ".join(n for n, _ in spec)
    spark.sql(f"INSERT OVERWRITE {fqn} ({names}) SELECT {names} FROM p04_dq_rules")
    print(f"{TAG} ops.dq_rules {spark.table(fqn).count():,} rules")

    # silver.dq_results: this as-of date's run replaces any previous one
    res = f"{c}.silver.dq_results"
    df = rows_df(spark, results, OUT["dq_results"])
    if spark.catalog.tableExists(res):
        spark.sql(f"DELETE FROM {res} WHERE run_id = '{ctx.run_id}'")
        commented(df, "dq_results", ctx.dictionary).write.mode("append").option("mergeSchema", "true").saveAsTable(res)
        text = sql_escape(ctx.dictionary.table_comments.get("dq_results", ""))
        spark.sql(f"COMMENT ON TABLE {res} IS '{text}'")
        set_tags(spark, res, silver.table_tags("dq_results", ctx.specs.tags))
    else:
        write_out(spark, df, res, "dq_results", ctx)
    print(f"{TAG} silver.dq_results {len(results):,} results")

    for s in summaries:
        s.pop("_secs", None)
    starts = {s["table_name"].split(".")[-1]: s.pop("_min_date") for s in summaries}
    write_out(spark, rows_df(spark, summaries, OUT["dq_table_reconciliation"]), f"{c}.silver.dq_table_reconciliation",
              "dq_table_reconciliation", ctx)

    # DQ history (42 months, latest = this run)
    hist_cfg = rcfg.get("history") or {}
    months = dq.month_starts(hist_cfg.get("start", "2023-04"), ctx.cfg.as_of_date)
    starts = {k: v.replace(day=1) for k, v in starts.items() if v}
    hist = dq.simulate_history(ctx.cfg.random_seed, dq.scores(results), months, starts, hist_cfg)
    for h in hist:
        h["run_id"] = ctx.run_id
    n = write_out(spark, rows_df(spark, hist, OUT["dq_score_history"]), f"{c}.silver.dq_score_history",
                  "dq_score_history", ctx)
    print(f"{TAG} silver.dq_score_history {n:,} rows ({len(months)} months)")

    # golden-record coverage + attribute completeness
    fields = {src: list(cols) for src, cols in er_records.SOURCE_COLUMNS.items()}
    cov = spark.sql(silver.coverage_sql(c, ctx.specs, er_records.SOURCE_PRIORITY, fields, months[0].isoformat(),
                                        months[-1].isoformat()))
    n = write_out(spark, cov, f"{c}.silver.golden_record_coverage_monthly", "golden_record_coverage_monthly", ctx)
    print(f"{TAG} silver.golden_record_coverage_monthly {n:,} rows")
    att = spark.sql(silver.attribute_sql(c, ctx.specs, er_records.SOURCE_PRIORITY))
    n = write_out(spark, att, f"{c}.silver.golden_attribute_completeness", "golden_attribute_completeness", ctx)
    print(f"{TAG} silver.golden_attribute_completeness {n:,} rows")


def report(ctx, results, summaries, rules) -> None:
    """Gates (spec WP8a): reconciliation, golden-key coverage, blocking failure rate, dirtiness, replay."""
    spark, c = ctx.spark, ctx.c
    rec_ok = sum(s["reconciles"] for s in summaries)
    print(f"{TAG} gate reconciliation: {rec_ok}/{len(summaries)} tables bronze = silver + quarantined + de-duplicated "
          f"({'PASS' if rec_ok == len(summaries) else 'FAIL'}); totals bronze {sum(s['bronze_rows'] for s in summaries):,}"
          f" = silver {sum(s['silver_rows'] for s in summaries):,} + quarantined "
          f"{sum(s['quarantined_rows'] for s in summaries):,} + de-duplicated {sum(s['deduplicated_rows'] for s in summaries):,}")
    for s in summaries:
        if s["quarantined_rows"] or s["deduplicated_rows"]:
            print(f"{TAG}   {s['table_name']:44} quarantined {s['quarantined_rows']:>6,} {s['quarantine_by_rule'] or ''} "
                  f"de-duplicated {s['deduplicated_rows']:>6,} {s['dedup_by_batch'] or ''}")
    cl = [s for s in summaries if s["client_rows_in_scope"] is not None]
    scope, resolved = sum(s["client_rows_in_scope"] for s in cl), sum(s["client_rows_resolved"] for s in cl)
    land = [r for r in results if r["rule_kind"] == "golden_key" and r["layer"] in ("bronze", "shared")]
    l_tot, l_fail = sum(r["total_rows"] for r in land), sum(r["failed_rows"] for r in land)
    cov = resolved / scope if scope else 1.0
    print(f"{TAG} gate golden-key coverage of silver client-grain facts: {cov:.4%} of {scope:,} rows in "
          f"{len(cl)} tables ({'PASS' if cov >= 0.995 else 'FAIL'} >= 99.5%); landing resolution "
          f"{1 - l_fail / l_tot if l_tot else 1:.4%} ({l_fail:,} unresolved rows quarantined)")
    blk = [r for r in results if r["is_blocking"] and r["layer"] == "silver"]
    b_tot, b_fail = sum(r["total_rows"] for r in blk), sum(r["failed_rows"] for r in blk)
    rate = b_fail / b_tot if b_tot else 0.0
    print(f"{TAG} gate silver failure rate on blocking rules: {rate:.4%} ({b_fail:,} of {b_tot:,} checks, {len(blk)} "
          f"rule results; {'PASS' if rate < 0.001 else 'FAIL'} < 0.1%)")
    by_status = {}
    for r in results:
        by_status.setdefault((r["layer"], r["status"]), 0)
        by_status[(r["layer"], r["status"])] += 1
    print(f"{TAG} results by layer/status: " + ", ".join(f"{k[0]} {k[1]} {v}" for k, v in sorted(by_status.items())))
    if any(s["table_name"].endswith("pay_payment_message") for s in summaries):
        replay = storylines.PAY_REPLAY["batch_id"]
        x = spark.sql(f"""SELECT (SELECT count(*) FROM {c}.silver.pay_payment_message) s_n,
                                 (SELECT round(sum(amount_usd), 2) FROM {c}.silver.pay_payment_message) s_amt,
                                 (SELECT count(*) FROM {c}.bronze.pay_payment_message WHERE _batch_id <> '{replay}') b_n,
                                 (SELECT round(sum(amount_usd), 2) FROM {c}.bronze.pay_payment_message
                                  WHERE _batch_id <> '{replay}') b_amt,
                                 (SELECT count(*) FROM {c}.bronze.pay_payment_message WHERE _batch_id = '{replay}') r_n
                       """).collect()[0]
        pay = next(s for s in summaries if s["table_name"].endswith("pay_payment_message"))
        ok = x["s_n"] == x["b_n"] and x["s_amt"] == x["b_amt"] and pay["deduplicated_rows"] == x["r_n"]
        print(f"{TAG} gate payments replay: {replay} {x['r_n']:,} messages; de-duplicated {pay['deduplicated_rows']:,} "
              f"{pay['dedup_by_batch']}; silver {x['s_n']:,} rows / USD {x['s_amt']:,.2f} vs non-replay bronze "
              f"{x['b_n']:,} / USD {x['b_amt']:,.2f} ({'PASS' if ok else 'FAIL'})")
    ident = [r for r in results if r["rule_kind"] == "identity" and r["severity"] != "info"]
    if ident:
        d = spark.sql(f"""
            WITH x AS (SELECT source_system, source_id, golden_client_id,
                              row_number() OVER (PARTITION BY source_system, golden_client_id ORDER BY source_id) AS rn
                       FROM {c}.silver.xref_client_source)
            SELECT r.source_system, count(*) AS n,
                   count_if(x.rn > 1 OR r.country_check IN ('corrected', 'suspect') OR
                            (r.parent_ref_as_recorded IS NOT NULL AND g.client_group_id IS NOT NULL
                             AND r.parent_ref_as_recorded <> g.client_group_id)) AS dirty
            FROM {c}.silver.client_source_record r
            JOIN x ON x.source_system = r.source_system AND x.source_id = r.source_id
            LEFT JOIN {c}.silver.client_golden_identity g ON g.golden_client_id = x.golden_client_id
            GROUP BY r.source_system ORDER BY r.source_system""").collect()
        n, dirty = sum(r["n"] for r in d), sum(r["dirty"] for r in d)
        mean = sum(r["failed_rows"] for r in ident) / max(1, sum(r["total_rows"] for r in ident))
        print(f"{TAG} bronze dirtiness, identity feeds: {dirty / n:.2%} of {n:,} records with >= 1 issue (duplicate, "
              f"wrong country, stale parent); mean failure rate per check {mean:.2%}; "
              + ", ".join(f"{r['source_system']} {r['dirty'] / r['n']:.1%}" for r in d))
    pay = [s for s in summaries if s["table_name"].endswith("pay_payment_message")]
    if pay:
        print(f"{TAG} bronze dirtiness, payments feed: {pay[0]['landing_dirty_pct']:.2%} of "
              f"{pay[0]['bronze_rows']:,} messages fail >= 1 landing rule")
    if ctx.dictionary.missing:
        print(f"{TAG} column comments missing for {len(ctx.dictionary.missing)} columns, e.g. "
              f"{sorted(ctx.dictionary.missing)[:8]}")
    else:
        print(f"{TAG} column comments: 100% of silver columns commented from config/column_dictionary.yaml")


if __name__ == "__main__":
    main()
