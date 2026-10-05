"""Phase 3c-6 — relationship economics: revenue, cost, capital, returns, deal pricing.

  ops.synthetic_relationship_econ   per-obligor as-of econ params (grade, LGD, RWA, cost base)
  bronze.fin_relationship_pnl       per obligor per fiscal quarter: revenue by product, cost,
                                    EL, RWA, economic capital, RORWA / RAROC / ROE vs hurdles
  bronze.fin_deal_pricing           per facility: priced vs standalone-hurdle margin, exceptions

Spark aggregates the heavy inputs (facility balances, deposits, payments, health grade) per
obligor-quarter; the economics + return formulas live in smbc_genie_lib.profitability (pure
Python, unit-tested). Keyed by obligor_id. Re-runs overwrite.

  .venv/bin/python src/10_bronze_synth/run_bronze_profitability.py --profile my-workspace
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from smbc_genie_lib import profitability as prof  # noqa: E402
from smbc_genie_lib.config import load_config  # noqa: E402
from smbc_genie_lib.spark_io import get_spark, write_table  # noqa: E402

INGEST_TS = "2026-09-30T08:00:00"
BATCH = "P03C-20260930"
WINDOW_START = "2024-04-01"   # FY2024 Q1 .. FY2026 Q2 (as-of)

_LGD_CASE = ("CASE t.security_type WHEN 'Unsecured' THEN 0.45 WHEN 'Real Estate' THEN 0.30 "
             "WHEN 'Receivables' THEN 0.35 WHEN 'Cash' THEN 0.18 WHEN 'Fixed Assets' THEN 0.35 "
             "WHEN 'Inventory' THEN 0.40 ELSE 0.45 END")


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 3c-6: profitability + deal pricing")
    ap.add_argument("--catalog", default=None)
    ap.add_argument("--profile", default=None)
    args = ap.parse_args()
    cfg = load_config(catalog=args.catalog) if args.catalog else load_config()
    c = cfg.catalog
    spark = get_spark(args.profile)
    from pyspark.sql.types import (StructType, StructField, StringType, IntegerType,
                                   DoubleType, BooleanType, DateType)
    as_of = _dt.date.fromisoformat(cfg.as_of_date)

    agg_sql = f"""
    WITH obl AS (
      SELECT source_id AS obligor_id, entity_id FROM {c}.ops.synthetic_truth_xref
      WHERE source_system = 'credit_obligor' AND NOT is_within_source_dup
    ),
    cust AS (
      SELECT entity_id, source_id AS cust_no FROM {c}.ops.synthetic_truth_xref
      WHERE source_system = 'core_customer' AND NOT is_within_source_dup
    ),
    cal AS (
      SELECT date, fiscal_year AS fy, fiscal_quarter AS fq FROM {c}.bronze.ref_calendar
      WHERE date BETWEEN DATE'{WINDOW_START}' AND DATE'{as_of.isoformat()}'
    ),
    fac_m AS (
      SELECT b.obligor_id, trunc(b.balance_date, 'MM') AS mo,
        sum(b.drawn_usd) AS drawn, sum(b.limit_usd) AS lim,
        sum(b.drawn_usd * t.margin_bps) / nullif(sum(b.drawn_usd), 0) AS wgt_margin,
        sum(b.limit_usd * {_LGD_CASE}) / nullif(sum(b.limit_usd), 0) AS lgd_blended,
        count(*) AS n_fac
      FROM {c}.bronze.core_facility_balance_monthly b
      JOIN {c}.bronze.credit_facility_terms t ON t.facility_id = b.facility_id
      GROUP BY b.obligor_id, trunc(b.balance_date, 'MM')
    ),
    fac_q AS (
      SELECT fm.obligor_id, cal.fy, cal.fq, avg(fm.drawn) AS avg_drawn, avg(fm.lim) AS avg_limit,
        avg(fm.wgt_margin) AS wgt_margin_bps, avg(fm.lgd_blended) AS lgd_blended, max(fm.n_fac) AS n_fac
      FROM fac_m fm JOIN cal ON cal.date = fm.mo
      GROUP BY fm.obligor_id, cal.fy, cal.fq
    ),
    dep_m AS (
      SELECT o.obligor_id, trunc(d.balance_date, 'MM') AS mo, sum(d.balance_usd) AS bal,
        sum(CASE WHEN d.deposit_class = 'CASA' THEN d.balance_usd ELSE 0 END) AS casa_bal
      FROM {c}.bronze.core_deposit_balance_monthly d JOIN cust cu ON cu.cust_no = d.cust_no
      JOIN obl o ON o.entity_id = cu.entity_id
      GROUP BY o.obligor_id, trunc(d.balance_date, 'MM')
    ),
    dep_q AS (
      SELECT dm.obligor_id, cal.fy, cal.fq, avg(dm.bal) AS avg_dep_bal,
        sum(dm.casa_bal) / nullif(sum(dm.bal), 0) AS casa_share
      FROM dep_m dm JOIN cal ON cal.date = dm.mo GROUP BY dm.obligor_id, cal.fy, cal.fq
    ),
    pay_q AS (
      SELECT o.obligor_id, cal.fy, cal.fq, count(*) AS n_pay,
        sum(CASE WHEN p.is_cross_border THEN p.amount_usd ELSE 0 END) AS xb_amt
      FROM {c}.bronze.pay_payment_message p JOIN cust cu ON cu.cust_no = p.cust_no
      JOIN obl o ON o.entity_id = cu.entity_id
      JOIN cal ON cal.date = p.payment_date
      GROUP BY o.obligor_id, cal.fy, cal.fq
    ),
    grd_q AS (
      SELECT o.obligor_id, cal.fy, cal.fq, avg(h.grade_effective) AS grade_avg
      FROM obl o JOIN {c}.ops.synthetic_entity_health_monthly h ON h.entity_id = o.entity_id
      JOIN cal ON cal.date = h.month GROUP BY o.obligor_id, cal.fy, cal.fq
    ),
    acct AS (
      SELECT o.obligor_id, count(*) AS n_acct FROM {c}.ops.synthetic_account a
      JOIN obl o ON o.entity_id = a.entity_id GROUP BY o.obligor_id
    ),
    ent AS (
      SELECT o.obligor_id, e.segment, e.relationship_tier, e.industry_sector, e.industry_subsector
      FROM obl o JOIN {c}.ops.synthetic_truth_entity e ON e.entity_id = o.entity_id
    )
    SELECT f.obligor_id, f.fy, f.fq, f.avg_drawn, f.avg_limit,
      coalesce(f.wgt_margin_bps, 0.0) AS wgt_margin_bps, coalesce(f.lgd_blended, 0.45) AS lgd_blended,
      coalesce(d.avg_dep_bal, 0.0) AS avg_dep_bal, coalesce(d.casa_share, 0.0) AS casa_share,
      coalesce(p.n_pay, 0) AS n_pay, coalesce(p.xb_amt, 0.0) AS xb_amt,
      g.grade_avg, coalesce(a.n_acct, 0) AS n_acct,
      ent.segment, ent.relationship_tier, ent.industry_sector, ent.industry_subsector
    FROM fac_q f
    LEFT JOIN dep_q d ON d.obligor_id = f.obligor_id AND d.fy = f.fy AND d.fq = f.fq
    LEFT JOIN pay_q p ON p.obligor_id = f.obligor_id AND p.fy = f.fy AND p.fq = f.fq
    LEFT JOIN grd_q g ON g.obligor_id = f.obligor_id AND g.fy = f.fy AND g.fq = f.fq
    LEFT JOIN acct a ON a.obligor_id = f.obligor_id
    JOIN ent ON ent.obligor_id = f.obligor_id
    """
    rows = spark.sql(agg_sql).collect()
    print(f"[p03c6] relationship-quarter aggregates: {len(rows):,}")

    pnl_rows = []
    for r in rows:
        agg = {k: r[k] for k in ("avg_drawn", "avg_limit", "wgt_margin_bps", "lgd_blended",
                                 "avg_dep_bal", "casa_share", "n_pay", "xb_amt", "n_acct",
                                 "relationship_tier", "segment")}
        agg["grade_avg"] = r["grade_avg"] if r["grade_avg"] is not None else 5.0
        p = prof.relationship_pnl(cfg, agg)
        pnl_rows.append({
            "obligor_id": r["obligor_id"], "fiscal_year": int(r["fy"]), "fiscal_quarter": int(r["fq"]),
            "fiscal_quarter_label": f"FY{int(r['fy'])}-Q{int(r['fq'])}",
            "segment": r["segment"], "relationship_tier": r["relationship_tier"],
            "industry_sector": r["industry_sector"], "industry_subsector": r["industry_subsector"],
            **p,
        })

    pnl_schema = StructType([
        StructField("obligor_id", StringType()), StructField("fiscal_year", IntegerType()),
        StructField("fiscal_quarter", IntegerType()), StructField("fiscal_quarter_label", StringType()),
        StructField("segment", StringType()), StructField("relationship_tier", StringType()),
        StructField("industry_sector", StringType()), StructField("industry_subsector", StringType()),
        StructField("ead", DoubleType()), StructField("rwa", DoubleType()),
        StructField("economic_capital", DoubleType()), StructField("book_equity", DoubleType()),
        StructField("internal_grade", IntegerType()), StructField("rev_lending", DoubleType()),
        StructField("rev_deposits", DoubleType()), StructField("rev_payments", DoubleType()),
        StructField("rev_other", DoubleType()), StructField("revenue_total", DoubleType()),
        StructField("cost_to_serve", DoubleType()), StructField("expected_loss", DoubleType()),
        StructField("net_profit", DoubleType()), StructField("net_profit_annualised", DoubleType()),
        StructField("rorwa", DoubleType()), StructField("raroc", DoubleType()), StructField("roe", DoubleType()),
        StructField("below_rorwa_hurdle", BooleanType()), StructField("below_raroc_hurdle", BooleanType()),
        StructField("below_roe_hurdle", BooleanType()), StructField("_source_system", StringType()),
        StructField("_batch_id", StringType()), StructField("_ingest_ts", StringType())])
    pnl_cols = [f.name for f in pnl_schema.fields if f.name not in ("_source_system", "_batch_id", "_ingest_ts")]
    pnl_tuple = [tuple(pr[k] for k in pnl_cols) + ("profitability_engine", BATCH, INGEST_TS) for pr in pnl_rows]
    write_table(spark.createDataFrame(pnl_tuple, pnl_schema), f"{c}.bronze.fin_relationship_pnl",
                comment="Relationship P&L per obligor per fiscal quarter: revenue by product, cost, EL, "
                        "RWA, economic capital, RORWA/RAROC/ROE vs hurdles.")

    # ops truth: per-obligor as-of economics (latest quarter in the window)
    latest = {}
    for pr in pnl_rows:
        k = pr["obligor_id"]
        if k not in latest or (pr["fiscal_year"], pr["fiscal_quarter"]) > (latest[k]["fiscal_year"], latest[k]["fiscal_quarter"]):
            latest[k] = pr
    econ_schema = StructType([
        StructField("obligor_id", StringType()), StructField("as_of_date", DateType()),
        StructField("internal_grade", IntegerType()), StructField("risk_weight", DoubleType()),
        StructField("lgd_blended", DoubleType()), StructField("ead", DoubleType()),
        StructField("rwa", DoubleType()), StructField("economic_capital", DoubleType()),
        StructField("segment", StringType()), StructField("relationship_tier", StringType()),
        StructField("rorwa", DoubleType()), StructField("below_rorwa_hurdle", BooleanType())])
    econ_tuple = [(pr["obligor_id"], as_of, pr["internal_grade"],
                   round(prof.risk_weight(pr["internal_grade"]), 4), None, pr["ead"], pr["rwa"],
                   pr["economic_capital"], pr["segment"], pr["relationship_tier"], pr["rorwa"],
                   pr["below_rorwa_hurdle"]) for pr in latest.values()]
    # fill lgd_blended from the aggregate rows (latest quarter)
    lgd_latest = {r["obligor_id"]: r["lgd_blended"] for r in rows}
    econ_tuple = [(t[0], t[1], t[2], t[3], round(lgd_latest.get(t[0], 0.45), 4), *t[5:]) for t in econ_tuple]
    write_table(spark.createDataFrame(econ_tuple, econ_schema), f"{c}.ops.synthetic_relationship_econ",
                comment="Generation-truth per-obligor as-of economics (grade, LGD, RWA, capital, RORWA).")
    print(f"[p03c6] fin_relationship_pnl: {len(pnl_rows):,} rows; relationship_econ: {len(econ_tuple):,}")

    # deal pricing (facility = deal signed at origination): origination RAROC vs the hurdle, exception
    # reason, and realised relationship RAROC over the (up to) four quarters after signing
    import hashlib
    from smbc_genie_lib import fiscal, storyline_injectors
    fac_rows = spark.sql(f"""
      SELECT f.facility_id, f.obligor_id, f.limit_usd, f.base_utilisation, f.security_type,
             f.origination_date, t.facility_type, t.margin_bps, e.internal_rating_grade AS grade
      FROM {c}.ops.synthetic_facility f
      JOIN {c}.bronze.credit_facility_terms t ON t.facility_id = f.facility_id
      JOIN {c}.ops.synthetic_truth_entity e ON e.entity_id = f.entity_id""").collect()
    q_start = {1: 4, 2: 7, 3: 10, 4: 1}
    by_obl_q = {}
    for pr in pnl_rows:
        qs = _dt.date(pr["fiscal_year"] + (pr["fiscal_quarter"] == 4), q_start[pr["fiscal_quarter"]], 1)
        by_obl_q.setdefault(pr["obligor_id"], []).append(
            (qs, pr["raroc"], 4.0 * (pr["rev_deposits"] + pr["rev_payments"] + pr["rev_other"]), pr["ead"]))
    APPROVERS = ["Credit Committee", "Regional CRO", "Head of Coverage", "Deputy CCO"]
    raroc_hurdle = float(cfg.thresholds.get("raroc_hurdle", 0.12))
    deals = []
    for r in sorted(fac_rows, key=lambda x: x["facility_id"]):
        qs = sorted(by_obl_q.get(r["obligor_id"], []))
        first = next((q for q in qs if q[0] >= r["origination_date"]), qs[0] if qs else None)
        drawn = r["limit_usd"] * r["base_utilisation"]
        share = min(1.0, (drawn + 0.5 * (r["limit_usd"] - drawn)) / first[3]) if first and first[3] else 0.0
        dp = prof.deal_pricing(cfg, facility_id=r["facility_id"], obligor_id=r["obligor_id"],
                               limit_usd=r["limit_usd"], base_utilisation=r["base_utilisation"],
                               priced_margin_bps=float(r["margin_bps"]), security_type=r["security_type"],
                               grade=r["grade"], ancillary_revenue=(first[2] * share) if first else 0.0)
        after = [q for q in qs if q[0] > r["origination_date"]][:4]
        realised = round(sum(q[1] for q in after) / len(after), 4) if len(after) >= 2 else None
        deals.append({**dp, "signing_date": r["origination_date"], "product": r["facility_type"],
                      "amount_usd": round(r["limit_usd"], 2), "realised_raroc": realised})
    reasons = storyline_injectors.exception_reasons(cfg, deals)
    deal_tuple = []
    for d in deals:
        h = int.from_bytes(hashlib.blake2b(d["facility_id"].encode(), digest_size=4).digest(), "big")
        exc = d["approved_below_hurdle"]
        deal_tuple.append((d["facility_id"], d["obligor_id"], d["signing_date"],
                           fiscal.fiscal_quarter_label(d["signing_date"]), d["product"], d["amount_usd"],
                           d["internal_grade"], d["ead"], d["rwa"], d["lgd"], d["priced_margin_bps"],
                           d["hurdle_margin_bps"], d["margin_gap_bps"], d["standalone_rorwa"],
                           d["meets_standalone_hurdle"], d["pricing_status"], d["origination_raroc"], exc,
                           d["realised_raroc"],
                           None if d["realised_raroc"] is None or not exc else d["realised_raroc"] >= raroc_hurdle,
                           APPROVERS[h % len(APPROVERS)] if exc else None, reasons.get(d["facility_id"]),
                           "profitability_engine", BATCH, INGEST_TS))
    deal_schema = StructType([
        StructField("facility_id", StringType()), StructField("obligor_id", StringType()),
        StructField("signing_date", DateType()), StructField("signing_quarter", StringType()),
        StructField("product", StringType()), StructField("amount_usd", DoubleType()),
        StructField("internal_grade", IntegerType()), StructField("ead", DoubleType()),
        StructField("rwa", DoubleType()), StructField("lgd", DoubleType()),
        StructField("priced_margin_bps", DoubleType()), StructField("hurdle_margin_bps", DoubleType()),
        StructField("margin_gap_bps", DoubleType()), StructField("standalone_rorwa", DoubleType()),
        StructField("meets_standalone_hurdle", BooleanType()), StructField("pricing_status", StringType()),
        StructField("origination_raroc", DoubleType()), StructField("approved_below_hurdle", BooleanType()),
        StructField("realised_raroc", DoubleType()), StructField("caught_up", BooleanType()),
        StructField("exception_approver", StringType()), StructField("exception_reason", StringType()),
        StructField("_source_system", StringType()), StructField("_batch_id", StringType()),
        StructField("_ingest_ts", StringType())])
    n_deal = write_table(spark.createDataFrame(deal_tuple, deal_schema), f"{c}.bronze.fin_deal_pricing",
                         comment="Deal pricing per facility: origination vs realised RAROC, below-hurdle approvals "
                                 "and exception reasons; standalone RoRWA-hurdle margin.")
    rel = [d for d in deals if reasons.get(d["facility_id"]) == "Relationship"]
    print(f"[p03c6] fin_deal_pricing: {n_deal:,} rows; approved below hurdle "
          f"{sum(d['approved_below_hurdle'] for d in deals):,}; 'Relationship' exceptions {len(rel)} "
          f"({sum(d['realised_raroc'] >= raroc_hurdle for d in rel)} caught up)")

    # verification
    v = spark.sql(f"""SELECT round(percentile_approx(rorwa,0.5),4) rorwa_med,
        round(percentile_approx(raroc,0.5),4) raroc_med, round(percentile_approx(roe,0.5),4) roe_med,
        round(avg(CASE WHEN below_rorwa_hurdle THEN 1 ELSE 0 END),3) pct_below_rorwa,
        round(avg(CASE WHEN below_raroc_hurdle THEN 1 ELSE 0 END),3) pct_below_raroc,
        round(avg(CASE WHEN below_roe_hurdle THEN 1 ELSE 0 END),3) pct_below_roe
      FROM {c}.bronze.fin_relationship_pnl WHERE fiscal_year=2026 AND fiscal_quarter=2""").collect()[0]
    print(f"[p03c6] as-of medians: RORWA={v['rorwa_med']} RAROC={v['raroc_med']} ROE={v['roe_med']}")
    print(f"[p03c6] below hurdle: RORWA={v['pct_below_rorwa']:.0%} RAROC={v['pct_below_raroc']:.0%} ROE={v['pct_below_roe']:.0%}")
    rev = spark.sql(f"""SELECT round(sum(rev_lending)/1e6,1) lend, round(sum(rev_deposits)/1e6,1) dep,
        round(sum(rev_payments)/1e6,1) pay, round(sum(rev_other)/1e6,1) oth
      FROM {c}.bronze.fin_relationship_pnl WHERE fiscal_year=2026 AND fiscal_quarter=2""").collect()[0]
    print(f"[p03c6] as-of qtr revenue $m: lending={rev['lend']} deposits={rev['dep']} payments={rev['pay']} other={rev['oth']}")
    print("[p03c6] done.")


if __name__ == "__main__":
    main()
