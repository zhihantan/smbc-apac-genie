"""Phase 3c-1 — latent health + deposit accounts + deposit balances.

Writes ops.synthetic_entity_health_monthly and ops.synthetic_account (generation truth with
entity_id + params), bronze.core_account (raw source projection), then Spark-expands
bronze.core_deposit_balance_monthly (full history) and bronze.core_deposit_balance_daily
(recent window = max(9 months, 42*SCALE months)). Balances are health-driven with quarter-/
March-end seasonality and deterministic hash noise, and start at each account's `active_from`
(new clients from the onboarding cohort, D43: first transaction). Re-runs overwrite.

  .venv/bin/python src/10_bronze_synth/run_bronze_deposits.py --profile my-workspace
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from smbc_genie_lib import accounts, fragment, health, onboarding, rng, storyline_injectors, truth  # noqa: E402
from smbc_genie_lib.config import load_config  # noqa: E402
from smbc_genie_lib.spark_io import get_spark, write_table  # noqa: E402

INGEST_TS = "2026-09-30T03:00:00"
BATCH = "P03C-20260930"


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 3c-1: health + accounts + deposits")
    ap.add_argument("--catalog", default=None)
    ap.add_argument("--profile", default=None)
    args = ap.parse_args()
    cfg = load_config(catalog=args.catalog) if args.catalog else load_config()
    c, seed = cfg.catalog, cfg.random_seed
    spark = get_spark(args.profile)
    from pyspark.sql.types import StructType, StructField, StringType

    groups = truth.build_groups(cfg)
    entities = truth.build_entities(cfg, groups)
    gids = [g["group_id"] for g in groups]
    _, xref = fragment.fragment_all(cfg, entities, gids)
    core_custno = {}
    for x in xref:
        if x["source_system"] == "core_customer" and not x["is_within_source_dup"]:
            core_custno.setdefault(x["entity_id"], x["source_id"])

    # 1) health backbone (ops)
    hm = health.build_health_monthly(cfg, entities)
    write_table(spark.createDataFrame(hm), f"{c}.ops.synthetic_entity_health_monthly",
                comment="Latent monthly credit health per entity (drives balances, utilisation, EWS).")
    print(f"[p03c] health_monthly: {len(hm):,} rows")

    # 2) accounts (ops generation-truth + bronze raw projection)
    cohort = onboarding.new_client_cohort(cfg, entities, xref)
    accts = accounts.build_accounts(cfg, entities, core_custno, cohort)
    # storylines: Sunda's February outflow and the HK CASA migration (deposit-only multipliers / shapes)
    hlut = {(r["entity_id"], r["month"]): r["health"] for r in hm}
    story = storyline_injectors.deposit_story(cfg, entities, accts, hlut)
    for a in accts:
        a.update(story.get(a["account_id"], {"deposit_mult": 1.0, "story_kind": None, "story_rate": 0.0}))
    write_table(spark.createDataFrame(accts), f"{c}.ops.synthetic_account",
                comment="Generation-truth deposit accounts (entity_id, base balance, active_from).")
    acct_cols = ["cust_no", "account_id", "account_type", "currency", "open_date", "status",
                 "_source_system", "_source_file", "_batch_id", "_ingest_ts"]
    rows = [[a["cust_no"], a["account_id"], a["account_type"], a["currency"],
             a["open_date"].isoformat(), "ACTIVE", "core_banking",
             f"core_account_{BATCH}.csv", BATCH, INGEST_TS] for a in accts]
    schema = StructType([StructField(x, StringType(), True) for x in acct_cols])
    write_table(spark.createDataFrame(rows, schema), f"{c}.bronze.core_account",
                comment="Bronze core-banking accounts (raw, mostly STRING).")
    n_new = sum(1 for a in accts if a["entity_id"] in cohort)
    print(f"[p03c] accounts: {len(accts):,} rows ({n_new:,} belong to {len(cohort):,} in-window new clients)")

    # 3) daily balances (recent window), then 4) month-end balances: history before the window from the
    #    monthly formula, months inside the window = the daily balance on the month-end date (and the month's
    #    average daily balance), so the two tables always agree. The daily series passes exactly through the
    #    monthly formula on month-ends: intra-month noise fades into the month-end noise, and the FY-end /
    #    quarter-end uplift builds over the last 7 days. Nothing is generated after as-of.
    as_of = _dt.date.fromisoformat(cfg.as_of_date)
    window_months = max(9, round(42 * cfg.scale))
    dstart = (as_of.replace(day=1) - _dt.timedelta(days=1)).replace(day=1)
    for _ in range(window_months - 1):
        dstart = (dstart - _dt.timedelta(days=1)).replace(day=1)
    story_m = story_d = storyline_injectors.deposit_factor_sql("cal.date")
    seasonal = "(1 + CASE WHEN month(cal.date)=3 THEN 0.18 WHEN month(cal.date) IN (6,9,12) THEN 0.08 ELSE 0.0 END)"
    s_amp = "CASE WHEN month(cal.date)=3 THEN 0.18 WHEN month(cal.date) IN (6,9,12) THEN 0.08 ELSE 0.0 END"
    ramp_end = "greatest(0.0, (day(cal.date) - (day(last_day(cal.date)) - 7)) / 7.0)"     # 1 on the month-end
    w = "((day(last_day(cal.date)) - day(cal.date)) / greatest(1.0, day(last_day(cal.date)) - 1.0))"  # 0 on it
    noise_d = rng.spark_unit_expr(seed, "a.account_id", "cal.date", "'daily'")
    noise_me = rng.spark_unit_expr(seed, "a.account_id", "last_day(cal.date)")    # = the monthly formula's noise
    daily_sql = f"""
    WITH base AS (
      SELECT a.account_id, a.cust_no, a.currency, a.is_casa, cal.date AS d,
        a.base_balance_usd * a.deposit_mult * (0.4 + 0.8*h.health) * ({story_d})
          * (1 + {s_amp} * {ramp_end})
          * (1 + {w} * ((({noise_d}) - 0.5)*0.08 + 0.03*sin(day(cal.date)/5.0))
               + (1 - {w}) * (({noise_me}) - 0.5)*0.06) AS bal_usd
      FROM {c}.ops.synthetic_account a
      JOIN {c}.bronze.ref_calendar cal
        ON cal.date BETWEEN DATE'{dstart.isoformat()}' AND DATE'{as_of.isoformat()}' AND cal.date >= a.active_from
      JOIN {c}.ops.synthetic_entity_health_monthly h
        ON h.entity_id = a.entity_id AND h.month = trunc(cal.date,'MM')
    )
    SELECT account_id, cust_no, d AS balance_date, currency,
      round(bal_usd * fx.rate_per_usd, 2) AS balance_lcy,
      round(bal_usd, 2) AS balance_usd,
      CASE WHEN is_casa THEN 'CASA' ELSE 'TD' END AS deposit_class,
      'core_banking' AS _source_system, '{BATCH}' AS _batch_id, '{INGEST_TS}' AS _ingest_ts
    FROM base JOIN {c}.bronze.fx_rate_daily fx ON fx.date = base.d AND fx.currency_code = base.currency
    """
    nda = write_table(spark.sql(daily_sql), f"{c}.bronze.core_deposit_balance_daily",
                      comment=f"Bronze daily deposit balances, window {dstart.isoformat()}..{as_of.isoformat()}.")
    print(f"[p03c] core_deposit_balance_daily ({dstart}..{as_of}): {nda:,} rows")

    noise_m = rng.spark_unit_expr(seed, "a.account_id", "cal.date")
    noise_avg = rng.spark_unit_expr(seed, "account_id", "d", "'avg'")
    monthly_sql = f"""
    WITH base AS (
      SELECT a.account_id, a.cust_no, a.currency, a.entity_id, a.is_casa, cal.date AS d,
        a.base_balance_usd * a.deposit_mult * (0.4 + 0.8*h.health) * ({story_m}) * {seasonal}
          * (1 + ({noise_m} - 0.5)*0.06) AS bal_usd
      FROM {c}.ops.synthetic_account a
      JOIN {c}.bronze.ref_calendar cal
        ON cal.is_month_end AND cal.date >= trunc(a.active_from,'MM') AND cal.date < DATE'{dstart.isoformat()}'
      JOIN {c}.ops.synthetic_entity_health_monthly h
        ON h.entity_id = a.entity_id AND h.month = trunc(cal.date,'MM')
    ),
    hist AS (
      SELECT account_id, cust_no, d AS balance_date, currency,
        round(bal_usd * fx.rate_per_usd, 2) AS balance_lcy, round(bal_usd, 2) AS balance_usd,
        round(bal_usd * (0.96 + 0.08*({noise_avg})), 2) AS avg_balance_usd,
        CASE WHEN is_casa THEN 'CASA' ELSE 'TD' END AS deposit_class
      FROM base JOIN {c}.bronze.fx_rate_daily fx ON fx.date = base.d AND fx.currency_code = base.currency
    ),
    win AS (
      SELECT dd.account_id, dd.cust_no, dd.balance_date, dd.currency, dd.balance_lcy, dd.balance_usd,
        round(avg(dd.balance_usd) OVER (PARTITION BY dd.account_id, trunc(dd.balance_date, 'MM')), 2) AS avg_balance_usd,
        dd.deposit_class, cal.is_month_end
      FROM {c}.bronze.core_deposit_balance_daily dd JOIN {c}.bronze.ref_calendar cal ON cal.date = dd.balance_date
    )
    SELECT *, 'core_banking' AS _source_system, '{BATCH}' AS _batch_id, '{INGEST_TS}' AS _ingest_ts FROM hist
    UNION ALL
    SELECT account_id, cust_no, balance_date, currency, balance_lcy, balance_usd, avg_balance_usd, deposit_class,
      'core_banking', '{BATCH}', '{INGEST_TS}' FROM win WHERE is_month_end
    """
    nmo = write_table(spark.sql(monthly_sql), f"{c}.bronze.core_deposit_balance_monthly",
                      comment="Bronze month-end deposit balances per account (health-driven, seasonal; equals the "
                              "daily balance on month-ends inside the daily window).")
    print(f"[p03c] core_deposit_balance_monthly: {nmo:,} rows")
    hk = spark.sql(f"""
      SELECT m.balance_date, round(sum(m.balance_usd)/1e9, 3) bn,
        round(sum(CASE WHEN m.deposit_class = 'CASA' THEN m.balance_usd END) / sum(m.balance_usd), 4) casa
      FROM {c}.bronze.core_deposit_balance_monthly m JOIN {c}.ops.synthetic_account a ON a.account_id = m.account_id
      JOIN {c}.ops.synthetic_truth_entity e ON e.entity_id = a.entity_id
      WHERE e.booking_country = 'HK' AND m.balance_date IN (DATE'2026-05-31', DATE'2026-08-31')
      GROUP BY m.balance_date ORDER BY 1""").collect()
    print("[p03c] HK book (storyline 5): " + ", ".join(f"{r['balance_date']} {r['bn']}bn CASA {r['casa']:.1%}" for r in hk))
    print(f"[p03c] done.")


if __name__ == "__main__":
    main()
