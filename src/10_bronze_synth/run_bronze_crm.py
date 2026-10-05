"""Phase 3c-10 — CRM: pipeline, account plans, next-best-product.

  bronze.crm_opportunity        sales pipeline (product, stage, win probability, expected revenue)
  bronze.crm_account_plan       annual plans (planned vs actual; plan optimism 8-12%)
  bronze.crm_next_best_product  NBP recommendations from real product gaps

All pure Python, keyed by crm_account_id for ER. Re-runs overwrite.

  .venv/bin/python src/10_bronze_synth/run_bronze_crm.py --profile my-workspace
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from smbc_genie_lib import crm, fragment, truth  # noqa: E402
from smbc_genie_lib.config import load_config  # noqa: E402
from smbc_genie_lib.spark_io import get_spark, write_table  # noqa: E402

INGEST_TS = "2026-09-30T12:00:00"
BATCH = "P03C-20260930"


def _meta(rows):
    for r in rows:
        r["_source_system"] = "crm_platform"
        r["_batch_id"] = BATCH
        r["_ingest_ts"] = INGEST_TS
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 3c-10: CRM")
    ap.add_argument("--catalog", default=None)
    ap.add_argument("--profile", default=None)
    args = ap.parse_args()
    cfg = load_config(catalog=args.catalog) if args.catalog else load_config()
    c = cfg.catalog
    spark = get_spark(args.profile)

    groups = truth.build_groups(cfg)
    entities = truth.build_entities(cfg, groups)
    gids = [g["group_id"] for g in groups]
    _, xref = fragment.fragment_all(cfg, entities, gids)

    def nondup(src):
        m = {}
        for x in xref:
            if x["source_system"] == src and not x["is_within_source_dup"]:
                m.setdefault(x["entity_id"], x["source_id"])
        return m

    crm_map = nondup("crm_account")
    has_lending = set(nondup("credit_obligor"))
    has_fx = set(nondup("tsy_counterparty"))
    has_trade = set(nondup("trade_party"))

    opps = crm.build_opportunities(cfg, entities, crm_map)
    plans = crm.build_account_plans(cfg, entities, crm_map)
    nbp = crm.build_nbp(cfg, entities, crm_map, has_lending, has_fx, has_trade)

    write_table(spark.createDataFrame(_meta(opps)), f"{c}.bronze.crm_opportunity",
                comment="Bronze CRM sales pipeline (product, stage, win probability, expected revenue).")
    write_table(spark.createDataFrame(_meta(plans)), f"{c}.bronze.crm_account_plan",
                comment="Bronze CRM annual account plans (planned vs actual revenue; plan optimism).")
    write_table(spark.createDataFrame(_meta(nbp)), f"{c}.bronze.crm_next_best_product",
                comment="Bronze next-best-product recommendations from product gaps (ranked by propensity).")
    print(f"[p03c10] crm accounts={len(crm_map):,}: opportunities={len(opps):,}, plans={len(plans):,}, nbp={len(nbp):,}")

    stages = spark.sql(f"""SELECT stage, count(*) n, round(sum(expected_revenue_usd)/1e6,1) rev_m
      FROM {c}.bronze.crm_opportunity GROUP BY stage ORDER BY n DESC""").collect()
    print("[p03c10] pipeline by stage: " + ", ".join(f"{r['stage']}={r['n']}(${r['rev_m']}m)" for r in stages))
    po = spark.sql(f"""SELECT round(avg(plan_optimism),4) opt, round(avg(planned_revenue_usd/actual_revenue_usd),4) ratio
      FROM {c}.bronze.crm_account_plan""").collect()[0]
    print(f"[p03c10] avg plan optimism {po['opt']} (planned/actual {po['ratio']})")
    nb = spark.sql(f"""SELECT recommended_product, count(*) n FROM {c}.bronze.crm_next_best_product
      GROUP BY recommended_product ORDER BY n DESC""").collect()
    print("[p03c10] NBP mix: " + ", ".join(f"{r['recommended_product']}={r['n']}" for r in nb))
    print("[p03c10] done.")


if __name__ == "__main__":
    main()
