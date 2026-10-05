"""Phase 3c-9 — cash-flow actuals + collections forecast (+ model-upgrade storyline).

  bronze.cf_actual_monthly   per client per month: inflows / outflows / net cash flow (from payments)
  bronze.cf_forecast         monthly collections backtest forecast vs actual, model v1 -> v2,
                             abs % error (MAPE improves ~0.18 -> ~0.11 at the 2026-06 upgrade)
  bronze.cf_projection       forward 6-month collections projection (ai_forecast where available,
                             else a deterministic seasonal projection), by segment

Keyed by cust_no for ER. The error model + version split live in smbc_genie_lib.cashflow
(unit-tested); the aggregation / fan-out run in Spark. Re-runs overwrite.

  .venv/bin/python src/10_bronze_synth/run_bronze_cashflow.py --profile my-workspace
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from smbc_genie_lib import rng  # noqa: E402
from smbc_genie_lib.config import load_config  # noqa: E402
from smbc_genie_lib.spark_io import get_spark, write_table  # noqa: E402

INGEST_TS = "2026-09-30T11:00:00"
BATCH = "P03C-20260930"


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 3c-9: cash-flow actuals + forecast")
    ap.add_argument("--catalog", default=None)
    ap.add_argument("--profile", default=None)
    args = ap.parse_args()
    cfg = load_config(catalog=args.catalog) if args.catalog else load_config()
    c, seed = cfg.catalog, cfg.random_seed
    spark = get_spark(args.profile)
    v2_from = cfg.realism.get("forecast_v2_from", "2026-06-01")
    mape_v1 = float(cfg.realism.get("forecast_mape_v1", 0.18))
    mape_v2 = float(cfg.realism.get("forecast_mape_v2", 0.11))

    # 1) monthly actuals from the payments hub
    actual_sql = f"""
    SELECT cust_no, trunc(payment_date, 'MM') AS month,
      round(sum(CASE WHEN direction = 'Inbound' THEN amount_usd ELSE 0 END), 2) AS inflows_usd,
      round(sum(CASE WHEN direction = 'Outbound' THEN amount_usd ELSE 0 END), 2) AS outflows_usd,
      round(sum(CASE WHEN direction = 'Inbound' THEN amount_usd ELSE -amount_usd END), 2) AS net_cf_usd,
      count(*) AS n_payments,
      'cashflow_engine' AS _source_system, '{BATCH}' AS _batch_id, '{INGEST_TS}' AS _ingest_ts
    FROM {c}.bronze.pay_payment_message
    GROUP BY cust_no, trunc(payment_date, 'MM')
    """
    na = write_table(spark.sql(actual_sql), f"{c}.bronze.cf_actual_monthly",
                     comment="Bronze monthly operating cash-flow actuals per client (from payments).")
    print(f"[p03c9] cf_actual_monthly: {na:,} rows")

    # 2) collections backtest forecast (one-month-ahead), model v1 -> v2 at the upgrade date.
    #    forecast = actual * (1 + 4*mape*(u-0.5)); E|error| = mape -> MAPE ~ configured values.
    u = rng.spark_unit_expr(seed, "cust_no", "month", "'cffc'")
    fc_sql = f"""
    WITH a AS (SELECT cust_no, month, inflows_usd FROM {c}.bronze.cf_actual_monthly WHERE inflows_usd > 0),
    f AS (
      SELECT cust_no, month AS target_month,
        add_months(month, -1) AS forecast_run_date,
        CASE WHEN month >= DATE'{v2_from}' THEN 'v2' ELSE 'v1' END AS model_version,
        CASE WHEN month >= DATE'{v2_from}' THEN {mape_v2} ELSE {mape_v1} END AS mape,
        inflows_usd AS actual_inflows_usd, {u} AS u
      FROM a
    )
    SELECT cust_no, forecast_run_date, target_month, model_version,
      round(actual_inflows_usd * (1 + 4 * mape * (u - 0.5)), 2) AS forecast_inflows_usd,
      actual_inflows_usd,
      round(abs(actual_inflows_usd * (1 + 4 * mape * (u - 0.5)) - actual_inflows_usd)
            / actual_inflows_usd, 4) AS abs_pct_error,
      'cashflow_engine' AS _source_system, '{BATCH}' AS _batch_id, '{INGEST_TS}' AS _ingest_ts
    FROM f
    """
    nf = write_table(spark.sql(fc_sql), f"{c}.bronze.cf_forecast",
                     comment="Bronze monthly collections forecast vs actual, model v1->v2 (abs % error).")
    print(f"[p03c9] cf_forecast: {nf:,} rows")

    # 3) forward 6-month collections projection by segment — ai_forecast if available, else seasonal.
    seg_sql = f"""
      SELECT e.segment, trunc(p.payment_date, 'MM') AS month,
        sum(CASE WHEN p.direction = 'Inbound' THEN p.amount_usd ELSE 0 END) AS inflows
      FROM {c}.bronze.pay_payment_message p
      JOIN {c}.ops.synthetic_account a ON a.account_id = p.account_id
      JOIN {c}.ops.synthetic_truth_entity e ON e.entity_id = a.entity_id
      GROUP BY e.segment, trunc(p.payment_date, 'MM')
    """
    spark.sql(seg_sql).createOrReplaceTempView("seg_monthly")
    method = "ai_forecast"
    try:
        proj = spark.sql(f"""
          SELECT segment, month AS target_month, round(inflows_forecast, 2) AS projected_inflows_usd,
            round(coalesce(inflows_lower, inflows_forecast * 0.9), 2) AS lower_usd,
            round(coalesce(inflows_upper, inflows_forecast * 1.1), 2) AS upper_usd,
            'ai_forecast' AS method, 'cashflow_engine' AS _source_system,
            '{BATCH}' AS _batch_id, '{INGEST_TS}' AS _ingest_ts
          FROM ai_forecast(TABLE(seg_monthly), horizon => 6, time_col => 'month',
                           value_col => 'inflows', group_col => 'segment')
        """)
        n_proj = write_table(proj, f"{c}.bronze.cf_projection",
                             comment="Bronze forward collections projection by segment (ai_forecast).")
    except Exception as e:  # noqa: BLE001 - fall back to a deterministic seasonal projection
        print(f"[p03c9] ai_forecast unavailable ({str(e)[:70]}...); using seasonal projection")
        method = "seasonal"
        up = rng.spark_unit_expr(seed, "segment", "h")
        proj = spark.sql(f"""
          WITH base AS (
            SELECT segment, avg(inflows) AS avg_inf, max(month) AS last_m FROM seg_monthly
            GROUP BY segment
          ), h AS (SELECT explode(sequence(1, 6)) AS h)
          SELECT b.segment, add_months(b.last_m, h.h) AS target_month,
            round(b.avg_inf * (1 + 0.02 * h.h) * (1 + ({up} - 0.5) * 0.06), 2) AS projected_inflows_usd,
            round(b.avg_inf * (1 + 0.02 * h.h) * 0.9, 2) AS lower_usd,
            round(b.avg_inf * (1 + 0.02 * h.h) * 1.1, 2) AS upper_usd,
            'seasonal' AS method, 'cashflow_engine' AS _source_system,
            '{BATCH}' AS _batch_id, '{INGEST_TS}' AS _ingest_ts
          FROM base b CROSS JOIN h
        """)
        n_proj = write_table(proj, f"{c}.bronze.cf_projection",
                             comment="Bronze forward collections projection by segment (seasonal fallback).")
    print(f"[p03c9] cf_projection ({method}): {n_proj:,} rows")

    mape = spark.sql(f"""SELECT model_version, round(avg(abs_pct_error), 4) mape, count(*) n
      FROM {c}.bronze.cf_forecast GROUP BY model_version ORDER BY model_version""").collect()
    print("[p03c9] forecast MAPE by model: " + ", ".join(f"{r['model_version']}={r['mape']} (n={r['n']:,})" for r in mape))
    print("[p03c9] done.")


if __name__ == "__main__":
    main()
