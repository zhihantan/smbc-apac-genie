"""Phase 3c-7 — FX & treasury deal flow.

  ops.synthetic_fx_relationship   per-client FX wallet truth (turnover, SMBC share, hedge ratio)
  bronze.tsy_fx_deal              SMBC-booked FX deals (spot/forward/swap, margin, revenue)
  bronze.tsy_fx_wallet_estimate   per client per fiscal quarter: SMBC wallet share + recapture
                                  opportunity (competitor volume x margin) — the Kinokawa hook

Deals are Spark-expanded from range() with deterministic hash attributes (like payments) and
keyed by the treasury-counterparty id (cpty_id) so gold resolves them to the golden client via
ER. Notionals tie back to each client's captured wallet so revenue concentrates. Re-runs
overwrite.

  .venv/bin/python src/10_bronze_synth/run_bronze_fx.py --profile my-workspace
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from smbc_genie_lib import fx, rng, truth, fragment  # noqa: E402
from smbc_genie_lib.config import load_config  # noqa: E402
from smbc_genie_lib.spark_io import get_spark, write_table  # noqa: E402
from smbc_genie_lib.sqlgen import pick_from_array, weighted_case  # noqa: E402

INGEST_TS = "2026-09-30T09:00:00"
BATCH = "P03C-20260930"
START = _dt.date(2024, 4, 1)
_EM_RE = "IDR|VND|INR|PHP|THB|MYR|CNY|TWD|KRW"


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 3c-7: FX & treasury deals")
    ap.add_argument("--catalog", default=None)
    ap.add_argument("--profile", default=None)
    args = ap.parse_args()
    cfg = load_config(catalog=args.catalog) if args.catalog else load_config()
    c, seed = cfg.catalog, cfg.random_seed
    spark = get_spark(args.profile)

    groups = truth.build_groups(cfg)
    entities = truth.build_entities(cfg, groups)
    gids = [g["group_id"] for g in groups]
    _, xref = fragment.fragment_all(cfg, entities, gids)
    cpty_map = {}
    for x in xref:
        if x["source_system"] == "tsy_counterparty" and not x["is_within_source_dup"]:
            cpty_map.setdefault(x["entity_id"], x["source_id"])

    rels = fx.build_fx_relationships(cfg, entities, cpty_map)
    n_fx = len(rels)
    write_table(spark.createDataFrame(rels), f"{c}.ops.synthetic_fx_relationship",
                comment="Generation-truth FX relationships (turnover, SMBC wallet share, hedge ratio).")
    print(f"[p03c7] fx relationships: {n_fx:,}")

    as_of = _dt.date.fromisoformat(cfg.as_of_date)
    days = (as_of - START).days
    window_years = days / 365.25
    n_deals = max(1000, round(600_000 * cfg.scale))
    deals_per_client = n_deals / n_fx

    def u(salt):
        return rng.spark_unit_expr(seed, "d.id", f"'{salt}'")

    product = weighted_case(u("prod"), fx.FX_PRODUCTS)
    deal_sql = f"""
    WITH rel AS (
      SELECT *, CAST(row_number() OVER (ORDER BY cpty_id) AS INT) - 1 AS idx
      FROM {c}.ops.synthetic_fx_relationship
    ),
    raw AS (
      SELECT d.id AS deal_seq, r.cpty_id, r.booking_country, r.primary_ccy_pair,
        r.fx_annual_turnover_usd, r.smbc_wallet_share, r.hedge_ratio,
        DATE'{START.isoformat()}' + CAST({u('dt')} * {days} AS INT) AS deal_date,
        {product} AS product_type,
        CASE WHEN {u('pair')} < 0.70 THEN r.primary_ccy_pair
             ELSE {pick_from_array(u('pairpick'), fx.MAJOR_PAIRS)} END AS ccy_pair,
        CASE WHEN {u('dir')} < 0.5 THEN 'Client Buys USD' ELSE 'Client Sells USD' END AS direction,
        (r.fx_annual_turnover_usd * r.smbc_wallet_share * {window_years}) / {deals_per_client}
          * exp(({u('notl')} - 0.5) * 1.2) AS notional_raw,
        {u('hedge')} AS uhedge, {u('mn')} AS umargin, {u('ten')} AS uten
      FROM range({n_deals}) d
      JOIN rel r ON r.idx = CAST(pmod(xxhash64({seed}L, d.id), {n_fx}) AS INT)
    ),
    enr AS (
      SELECT raw.*,
        greatest(50000.0, notional_raw) AS notional_usd,
        (CASE product_type WHEN 'FX Spot' THEN 3.0 WHEN 'FX Forward' THEN 8.0 ELSE 5.0 END)
          * (CASE WHEN ccy_pair RLIKE '{_EM_RE}' THEN 2.0 ELSE 1.0 END)
          * (0.8 + 0.4 * umargin) AS margin_bps,
        CASE WHEN uhedge < hedge_ratio THEN 'Hedge' ELSE 'Trading' END AS hedge_type,
        CASE product_type WHEN 'FX Spot' THEN 2
             WHEN 'FX Forward' THEN CAST(30 + uten * 335 AS INT)
             ELSE CAST(7 + uten * 173 AS INT) END AS tenor_days
      FROM raw
    )
    SELECT concat('FXD-', lpad(CAST(deal_seq AS STRING), 9, '0')) AS deal_id,
      cpty_id, deal_date, product_type, ccy_pair, direction,
      round(notional_usd, 2) AS notional_usd, round(margin_bps, 2) AS margin_bps,
      round(notional_usd * margin_bps / 10000.0, 2) AS revenue_usd,
      hedge_type, tenor_days, date_add(deal_date, tenor_days) AS value_date,
      booking_country AS booking_location,
      'treasury_system' AS _source_system, '{BATCH}' AS _batch_id, '{INGEST_TS}' AS _ingest_ts
    FROM enr
    """
    n = write_table(spark.sql(deal_sql), f"{c}.bronze.tsy_fx_deal",
                    comment="Bronze SMBC-booked FX deals (spot/forward/swap, margin, revenue, hedge/trading).")
    print(f"[p03c7] tsy_fx_deal: {n:,} rows (target {n_deals:,})")

    wallet_sql = f"""
    WITH deal_q AS (
      SELECT d.cpty_id, cal.fiscal_year AS fy, cal.fiscal_quarter AS fq,
        sum(d.notional_usd) AS smbc_vol, sum(d.revenue_usd) AS smbc_rev, count(*) AS n_deals,
        sum(d.notional_usd * d.margin_bps) / nullif(sum(d.notional_usd), 0) AS avg_margin_bps
      FROM {c}.bronze.tsy_fx_deal d JOIN {c}.bronze.ref_calendar cal ON cal.date = d.deal_date
      GROUP BY d.cpty_id, cal.fiscal_year, cal.fiscal_quarter
    )
    SELECT dq.cpty_id, dq.fy AS fiscal_year, dq.fq AS fiscal_quarter,   -- source-keyed: no truth entity_id
      concat('FY', dq.fy, '-Q', dq.fq) AS fiscal_quarter_label,
      round(f.fx_annual_turnover_usd / 4.0, 2) AS est_total_wallet_usd,
      round(dq.smbc_vol, 2) AS smbc_volume_usd, dq.n_deals,
      round(least(1.0, dq.smbc_vol / nullif(f.fx_annual_turnover_usd / 4.0, 0)), 4) AS wallet_share,
      round(greatest(0.0, f.fx_annual_turnover_usd / 4.0 - dq.smbc_vol), 2) AS competitor_volume_usd,
      round(dq.smbc_rev, 2) AS revenue_captured_usd,
      round(greatest(0.0, f.fx_annual_turnover_usd / 4.0 - dq.smbc_vol) * dq.avg_margin_bps / 10000.0, 2)
        AS revenue_opportunity_usd,
      'treasury_system' AS _source_system, '{BATCH}' AS _batch_id, '{INGEST_TS}' AS _ingest_ts
    FROM deal_q dq JOIN {c}.ops.synthetic_fx_relationship f ON f.cpty_id = dq.cpty_id
    """
    nw = write_table(spark.sql(wallet_sql), f"{c}.bronze.tsy_fx_wallet_estimate",
                     comment="Bronze FX wallet estimate per client per quarter (SMBC share + recapture opportunity).")
    print(f"[p03c7] tsy_fx_wallet_estimate: {nw:,} rows")

    v = spark.sql(f"""SELECT product_type, count(*) n, round(sum(notional_usd)/1e9,2) notl_bn,
        round(sum(revenue_usd)/1e6,2) rev_m, round(avg(margin_bps),1) avg_margin
      FROM {c}.bronze.tsy_fx_deal GROUP BY product_type ORDER BY n DESC""").collect()
    for r in v:
        print(f"[p03c7]   {r['product_type']:>11}: {r['n']:>7,} deals, ${r['notl_bn']}bn, ${r['rev_m']}m rev, {r['avg_margin']}bps")
    w = spark.sql(f"""SELECT round(avg(wallet_share),3) avg_share, round(sum(revenue_captured_usd)/1e6,1) cap,
        round(sum(revenue_opportunity_usd)/1e6,1) opp
      FROM {c}.bronze.tsy_fx_wallet_estimate WHERE fiscal_year=2026 AND fiscal_quarter=2""").collect()[0]
    print(f"[p03c7] as-of qtr: avg SMBC wallet share {w['avg_share']}, captured ${w['cap']}m, recapture opportunity ${w['opp']}m")
    print("[p03c7] done.")


if __name__ == "__main__":
    main()
