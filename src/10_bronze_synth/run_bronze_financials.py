"""Phase 3c-4 — spread financial statements, ratios, and peer benchmarks.

bronze.credit_financial_statement (long P&L/BS/CF), bronze.credit_ratio, and the Spark-computed
bronze.ext_peer_benchmark (P25/P50/P75 per peer group x ratio x fiscal year). Keyed by obligor_id
so gold resolves to the golden client via ER. Re-runs overwrite.

  .venv/bin/python src/10_bronze_synth/run_bronze_financials.py --profile my-workspace
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from smbc_genie_lib import financials, fragment, health, truth  # noqa: E402
from smbc_genie_lib.config import load_config  # noqa: E402
from smbc_genie_lib.spark_io import get_spark, write_table  # noqa: E402

INGEST_TS = "2026-09-30T06:00:00"
BATCH = "P03C-20260930"


def _d(x):
    return x.isoformat() if x is not None else None


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 3c-4: financials + ratios + peer benchmarks")
    ap.add_argument("--catalog", default=None)
    ap.add_argument("--profile", default=None)
    args = ap.parse_args()
    cfg = load_config(catalog=args.catalog) if args.catalog else load_config()
    c = cfg.catalog
    spark = get_spark(args.profile)
    from pyspark.sql.types import (StructType, StructField, StringType, IntegerType,
                                   DoubleType, BooleanType)

    groups = truth.build_groups(cfg)
    entities = truth.build_entities(cfg, groups)
    gids = [g["group_id"] for g in groups]
    _, xref = fragment.fragment_all(cfg, entities, gids)
    obligor = {}
    for x in xref:
        if x["source_system"] == "credit_obligor" and not x["is_within_source_dup"]:
            obligor.setdefault(x["entity_id"], x["source_id"])
    hlut = {(r["entity_id"], r["month"]): r["health"] for r in health.build_health_monthly(cfg, entities)}
    lines, ratios = financials.build_statements(cfg, entities, hlut, obligor)
    print(f"[p03c4] {len(obligor):,} borrowers -> {len(lines):,} statement lines, {len(ratios):,} ratios")

    line_schema = StructType([
        StructField("obligor_id", StringType()), StructField("fiscal_year", IntegerType()),
        StructField("fiscal_year_label", StringType()), StructField("fiscal_year_end", StringType()),
        StructField("statement_type", StringType()), StructField("line_item", StringType()),
        StructField("amount_usd", DoubleType()), StructField("amount_lcy", DoubleType()),
        StructField("currency", StringType()), StructField("is_audited", BooleanType()),
        StructField("is_spread", BooleanType()), StructField("spread_date", StringType()),
        StructField("_source_system", StringType()), StructField("_batch_id", StringType()),
        StructField("_ingest_ts", StringType())])
    line_rows = [(ln["obligor_id"], ln["fiscal_year"], ln["fiscal_year_label"], _d(ln["fiscal_year_end"]),
                  ln["statement_type"], ln["line_item"], ln["amount_usd"], ln["amount_lcy"], ln["currency"],
                  ln["is_audited"], ln["is_spread"], _d(ln["spread_date"]),
                  "credit_workflow", BATCH, INGEST_TS) for ln in lines]
    write_table(spark.createDataFrame(line_rows, line_schema), f"{c}.bronze.credit_financial_statement",
                comment="Bronze spread financials (long: P&L/BS/CF lines per obligor per fiscal year).")

    ratio_schema = StructType([
        StructField("obligor_id", StringType()), StructField("fiscal_year", IntegerType()),
        StructField("fiscal_year_label", StringType()), StructField("fiscal_year_end", StringType()),
        StructField("ratio_name", StringType()), StructField("ratio_value", DoubleType()),
        StructField("peer_group_id", StringType()), StructField("_source_system", StringType()),
        StructField("_batch_id", StringType()), StructField("_ingest_ts", StringType())])
    ratio_rows = [(r["obligor_id"], r["fiscal_year"], r["fiscal_year_label"], _d(r["fiscal_year_end"]),
                   r["ratio_name"], r["ratio_value"], r["peer_group_id"],
                   "credit_workflow", BATCH, INGEST_TS) for r in ratios]
    write_table(spark.createDataFrame(ratio_rows, ratio_schema), f"{c}.bronze.credit_ratio",
                comment="Bronze computed financial ratios per obligor per fiscal year.")
    print(f"[p03c4] wrote credit_financial_statement + credit_ratio")

    pb = spark.sql(f"""
      SELECT peer_group_id, ratio_name, fiscal_year,
        round(percentile_approx(ratio_value, 0.25), 4) AS p25,
        round(percentile_approx(ratio_value, 0.50), 4) AS p50,
        round(percentile_approx(ratio_value, 0.75), 4) AS p75,
        count(*) AS n_clients,
        'external_vendor' AS _source_system, '{BATCH}' AS _batch_id, '{INGEST_TS}' AS _ingest_ts
      FROM {c}.bronze.credit_ratio WHERE ratio_value IS NOT NULL
      GROUP BY peer_group_id, ratio_name, fiscal_year""")
    n = write_table(pb, f"{c}.bronze.ext_peer_benchmark",
                    comment="Bronze peer benchmark percentiles (P25/P50/P75) per peer group x ratio x fiscal year.")
    print(f"[p03c4] ext_peer_benchmark: {n:,} rows")
    print("[p03c4] done.")


if __name__ == "__main__":
    main()
