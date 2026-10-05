"""Phase 3c-8 — trade finance & supply-chain finance (+ storyline 6, the VN/IN import-LC surge).

  ops.synthetic_trade_relationship  per-client trade truth (turnover, import share, corridor)
  ops.synthetic_scf_programme       programme truth: anchor entity, launch date, as-of utilisation
  bronze.trade_finance_txn          LCs / guarantees / collections / trade loans / receivables with
                                    corridor (origin -> destination), commodity (HS chapter), currency,
                                    lifecycle status, closed date and outstanding
  bronze.scf_programme              supply-chain finance programmes (anchor party, limit, drawn, launch)
  bronze.scf_supplier               supplier network (onboarded + prospective = growth pipeline)

Pure Python (smbc_genie_lib.trade), keyed by the trade-party id (party_id) for ER. Status comes from
trade.instrument_lifecycle, which the TB-depth step (3c-14, run_bronze_tb_trade.py) expands into
trade_event / trade_presentation / trade_scf_invoice — so run this step first. Re-runs overwrite.

  .venv/bin/python src/10_bronze_synth/run_bronze_trade.py --profile my-workspace
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from smbc_genie_lib import fragment, health, storylines, trade, truth  # noqa: E402
from smbc_genie_lib.config import load_config  # noqa: E402
from smbc_genie_lib.spark_io import get_spark, write_table  # noqa: E402

INGEST_TS = "2026-09-30T10:00:00"
BATCH = "P03C-20260930"
META = [("_source_system", "s"), ("_batch_id", "s"), ("_ingest_ts", "s")]
# column specs: s=STRING, i=INT, d=DOUBLE, b=BOOLEAN, dt=DATE (instrument dates stay DATE, as before)
TXN_SPEC = [("txn_id", "s"), ("party_id", "s"), ("txn_date", "dt"), ("product_type", "s"), ("direction", "s"),
            ("origin_country", "s"), ("destination_country", "s"), ("counterparty_country", "s"),
            ("counterparty_bank_country", "s"), ("beneficiary_country", "s"), ("commodity", "s"),
            ("hs_chapter", "s"), ("currency", "s"), ("amount_ccy", "d"), ("amount_usd", "d"), ("tenor_days", "i"),
            ("maturity_date", "dt"), ("status", "s"), ("closed_date", "dt"), ("outstanding_usd", "d"),
            ("fee_bps", "d"), ("commission_usd", "d"), ("is_sustainable_trade", "b"), ("booking_location", "s")]
PROG_SPEC = [("programme_id", "s"), ("anchor_party_id", "s"), ("programme_type", "s"), ("launch_date", "s"),
             ("currency", "s"), ("limit_usd", "d"), ("drawn_usd", "d"), ("utilisation", "d"),
             ("n_suppliers_total", "i"), ("primary_corridor", "s")]
SUP_SPEC = [("supplier_id", "s"), ("programme_id", "s"), ("anchor_party_id", "s"), ("supplier_name", "s"),
            ("supplier_country", "s"), ("is_onboarded", "b"), ("onboarded_date", "s"), ("n_invoices_financed", "i"),
            ("financed_amount_usd", "d"), ("avg_days_paid_early", "i"), ("discount_rate_bps", "d"),
            ("routes_to_other_bank", "b")]
REL_SPEC = [("entity_id", "s"), ("party_id", "s"), ("booking_country", "s"), ("segment", "s"),
            ("trade_annual_turnover_usd", "d"), ("import_share", "d"), ("primary_corridor", "s"), ("is_corporate", "b")]
OPS_PROG_SPEC = [("programme_id", "s"), ("anchor_party_id", "s"), ("anchor_entity_id", "s"), ("programme_type", "s"),
                 ("launch_date", "dt"), ("limit_usd", "d"), ("drawn_usd", "d"), ("utilisation", "d")]


def _df(spark, rows, spec):
    from pyspark.sql.types import (BooleanType, DateType, DoubleType, IntegerType, StringType,
                                   StructField, StructType)
    types = {"s": StringType(), "i": IntegerType(), "d": DoubleType(), "b": BooleanType(), "dt": DateType()}
    schema = StructType([StructField(c, types[t], True) for c, t in spec])
    return spark.createDataFrame([[r.get(c) for c, _ in spec] for r in rows], schema)


def _meta(rows, src):
    for r in rows:
        r.update(_source_system=src, _batch_id=BATCH, _ingest_ts=INGEST_TS)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 3c-8: trade finance + SCF")
    ap.add_argument("--catalog", default=None)
    ap.add_argument("--profile", default=None)
    args = ap.parse_args()
    cfg = load_config(catalog=args.catalog) if args.catalog else load_config()
    c = cfg.catalog
    spark = get_spark(args.profile)

    groups = truth.build_groups(cfg)
    entities = truth.build_entities(cfg, groups)
    _, xref = fragment.fragment_all(cfg, entities, [g["group_id"] for g in groups])
    party_map = {}
    for x in xref:
        if x["source_system"] == "trade_party" and not x["is_within_source_dup"]:
            party_map.setdefault(x["entity_id"], x["source_id"])

    rels = trade.build_trade_relationships(cfg, entities, party_map)
    progs = trade.build_scf_programmes(cfg, rels)
    sups = trade.build_scf_suppliers(cfg, progs)
    txns = trade.build_trade_txns(cfg, entities, rels, trade.health_lookup(health.build_health_monthly(cfg, entities)))

    write_table(_df(spark, rels, REL_SPEC), f"{c}.ops.synthetic_trade_relationship",
                comment="Generation-truth trade relationships (turnover, import share, corridor, SCF anchor).")
    write_table(_df(spark, progs, OPS_PROG_SPEC), f"{c}.ops.synthetic_scf_programme",
                comment="Generation-truth SCF programmes (anchor entity, launch date, as-of utilisation).")
    prog_rows = _meta([{**p, "launch_date": p["launch_date"].isoformat()} for p in progs], "scf_platform")
    write_table(_df(spark, prog_rows, PROG_SPEC + META), f"{c}.bronze.scf_programme",
                comment="Bronze SCF programmes (anchor party, type, launch date, limit, drawn, utilisation).")
    sup_rows = _meta([{**s, "onboarded_date": s["onboarded_date"] and s["onboarded_date"].isoformat()} for s in sups],
                     "scf_platform")
    write_table(_df(spark, sup_rows, SUP_SPEC + META), f"{c}.bronze.scf_supplier",
                comment="Bronze SCF suppliers (onboarded + prospective; financed outstanding, invoices "
                        "financed in the last 12 months, discount margin, routing).")
    print(f"[p03c8] trade rels={len(rels):,}, scf_programme={len(progs):,}, scf_supplier={len(sups):,}")
    n = write_table(_df(spark, _meta(txns, "trade_finance_system"), TXN_SPEC + META), f"{c}.bronze.trade_finance_txn",
                    comment="Bronze trade finance instruments (LC/guarantee/collection/loan/receivables): corridor, "
                            "commodity (HS chapter), currency, fees, lifecycle status, closed date, outstanding.")
    n_win = sum(t["txn_date"] >= trade.TXN_START for t in txns)
    print(f"[p03c8] trade_finance_txn: {n:,} rows = {n_win:,} issued from {trade.TXN_START} (target "
          f"{max(2000, round(trade.TXNS_AT_SCALE_1 * cfg.scale)):,}) + {n - n_win:,} FY2023 legacy still open then")

    # verification, from the written bronze tables
    t = f"{c}.bronze.trade_finance_txn"
    for r in spark.sql(f"""SELECT product_type, count(*) n, round(sum(amount_usd)/1e9,2) amt_bn,
        round(sum(commission_usd)/1e6,2) comm_m, round(sum(outstanding_usd)/1e9,2) out_bn FROM {t}
      GROUP BY product_type ORDER BY n DESC""").collect():
        print(f"[p03c8]   {r['product_type']:>22}: {r['n']:>6,} txns, ${r['amt_bn']}bn, ${r['comm_m']}m comm, "
              f"${r['out_bn']}bn outstanding")
    st = spark.sql(f"SELECT status, count(*) n FROM {t} GROUP BY status ORDER BY n DESC").collect()
    print("[p03c8] status at as-of: " + ", ".join(f"{r['status']}={r['n']:,}" for r in st))
    corr = spark.sql(f"""SELECT concat(origin_country, '->', destination_country) k, count(*) n FROM {t}
      GROUP BY 1 ORDER BY n DESC LIMIT 5""").collect()
    print("[p03c8] top corridors: " + ", ".join(f"{r['k']}={r['n']:,}" for r in corr))
    v = storylines.VN_IN
    sel = (f"product_type = 'Import LC' AND destination_country IN {tuple(v['import_countries'])} "
           f"AND origin_country IN {tuple(v['source_countries'])} AND commodity IN {trade.SURGE_COMMODITIES}")
    s = spark.sql(f"""SELECT
        count(CASE WHEN txn_date BETWEEN DATE'2025-04-01' AND DATE'2025-09-30' THEN 1 END) n25,
        count(CASE WHEN txn_date BETWEEN DATE'2026-04-01' AND DATE'2026-09-30' THEN 1 END) n26,
        sum(CASE WHEN txn_date BETWEEN DATE'2025-04-01' AND DATE'2025-09-30' THEN amount_usd END) a25,
        sum(CASE WHEN txn_date BETWEEN DATE'2026-04-01' AND DATE'2026-09-30' THEN amount_usd END) a26,
        count(CASE WHEN txn_date >= DATE'2026-04-01' AND booking_location = 'SG' THEN 1 END) sg26,
        sum(CASE WHEN txn_date >= DATE'2026-04-01' AND booking_location = 'SG' THEN amount_usd END) sga26
      FROM {t} WHERE {sel}""").collect()[0]
    print(f"[p03c8] VN/IN import LCs (electronics & auto parts from CN/KR/JP): H1 FY2025 {s['n25']} -> H1 FY2026 "
          f"{s['n26']} (count {s['n26'] / s['n25'] - 1:+.1%}, USD {s['a26'] / s['a25'] - 1:+.1%}); SG books "
          f"{s['sg26'] / s['n26']:.1%} of count / {s['sga26'] / s['a26']:.1%} of USD")
    sc = spark.sql(f"""SELECT count(*) n, sum(CASE WHEN is_onboarded THEN 1 ELSE 0 END) onb,
        round(sum(financed_amount_usd)/1e9,2) fin_bn, sum(CASE WHEN NOT is_onboarded THEN 1 ELSE 0 END) prospect
      FROM {c}.bronze.scf_supplier""").collect()[0]
    print(f"[p03c8] SCF suppliers: {sc['n']} ({sc['onb']} onboarded ${sc['fin_bn']}bn financed outstanding, "
          f"{sc['prospect']} prospective); programme utilisation "
          + ", ".join(f"{p['programme_id']}={p['utilisation']:.0%}" for p in progs))
    print("[p03c8] done.")


if __name__ == "__main__":
    main()
