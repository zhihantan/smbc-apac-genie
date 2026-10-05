"""Phase 3 entry point — synthetic truth, bronze and shared feeds, in dependency order (PLAN §5.5).

Runs every generator, then the late storyline hook, entity resolution (Phase 4a, which the Meridian
checks need) and the storyline assertions, and appends one row per logged table to ops.build_run_log.
On a laptop each generator runs as its own process (each opens its own serverless Spark session); on
Databricks compute (a notebook or a job task) they run in this process on the ambient session.
Stops at the first failing step. EWS runs twice: its watchlist feeds the credit workflow, whose DPD
then feeds EWS's informational DPD triggers (the composite score, hence the watchlist, ignores DPD).

  .venv/bin/python src/10_bronze_synth/run_bronze.py --profile my-workspace [--from STEP] [--only STEP]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import runpy
import subprocess
import sys
import time
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

STEPS = [  # (step, script, tables whose row counts are logged)
    ("p03a_truth_ref", "run_truth_ref.py", ["ops.synthetic_truth_entity", "bronze.ref_calendar", "bronze.fx_rate_daily"]),
    ("p03b_sources", "run_bronze_sources.py", ["bronze.core_customer", "bronze.kyc_customer", "ops.synthetic_truth_xref"]),
    ("p03c01_deposits", "run_bronze_deposits.py", ["bronze.core_account", "bronze.core_deposit_balance_monthly",
                                                    "bronze.core_deposit_balance_daily"]),
    ("p03c02_loans", "run_bronze_loans.py", ["bronze.credit_facility_terms", "bronze.credit_covenant_test",
                                              "bronze.core_facility_balance_monthly"]),
    ("p03c03_payments", "run_bronze_payments.py", ["bronze.pay_payment_message", "bronze.pay_repair_queue"]),
    ("p03c04_financials", "run_bronze_financials.py", ["bronze.credit_financial_statement", "bronze.credit_ratio"]),
    ("p03c15_external", "run_bronze_external.py", ["bronze.ext_news", "bronze.ext_market_daily", "bronze.ext_rating"]),
    ("p03c07_fx", "run_bronze_fx.py", ["bronze.tsy_fx_deal", "bronze.tsy_fx_wallet_estimate"]),
    ("p03c08_trade", "run_bronze_trade.py", ["bronze.trade_finance_txn", "bronze.scf_programme", "bronze.scf_supplier"]),
    ("p03c09_cashflow", "run_bronze_cashflow.py", ["bronze.cf_actual_monthly", "bronze.cf_forecast"]),
    ("p03c06_profitability", "run_bronze_profitability.py", ["bronze.fin_relationship_pnl", "bronze.fin_deal_pricing"]),
    ("p03c16_shared", "run_bronze_shared.py", ["shared.share_jp_group_master", "bronze.dq_share_refresh_log"]),
    ("p03c05_ews_pass1", "run_bronze_ews.py", ["bronze.ews_score", "bronze.ews_watchlist"]),
    ("p03c12_credit_risk", "run_bronze_credit_risk.py", ["bronze.core_dpd", "bronze.core_rating_history",
                                                          "bronze.fin_capital_allocation", "bronze.cf_event"]),
    ("p03c05_ews_pass2", "run_bronze_ews.py", ["bronze.ews_signal", "bronze.ews_watchlist_event", "bronze.ews_override"]),
    ("p03c14_tb_trade", "run_bronze_tb_trade.py", ["bronze.trade_event", "bronze.core_time_deposit", "bronze.pay_channel_usage"]),
    ("p03c10_crm", "run_bronze_crm.py", ["bronze.crm_opportunity", "bronze.crm_account_plan"]),
    ("p03c13_crm_engagement", "run_bronze_crm_engagement.py", ["bronze.crm_activity", "bronze.crm_signal"]),
    ("p03c11_kyc", "run_bronze_kyc.py", ["bronze.kyc_case", "bronze.kyc_review"]),
    ("p03late_storylines", "run_bronze_storylines_late.py", ["bronze.pay_payment_message", "ops.er_record_availability"]),
    ("p04a_entity_resolution", "../20_silver/entity_resolution/run_er.py", ["silver.xref_client_source",
                                                                          "silver.client_golden_identity"]),
    ("p03z_checks", "run_storyline_checks.py", ["ops.storyline_assertions"]),
]


def run_step(script: Path, argv: list, in_process: bool) -> int:
    """Exit code of one generator: a child process (laptop), or runpy in this process (Databricks compute,
    where only this process holds the ambient Spark session)."""
    if not in_process:
        return subprocess.call([sys.executable, str(script), *argv])
    saved = sys.argv
    sys.argv = [str(script), *argv]
    try:
        runpy.run_path(str(script), run_name="__main__")
        return 0
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except Exception:  # noqa: BLE001 - report and stop, like a failed child process
        traceback.print_exc()
        return 1
    finally:
        sys.argv = saved


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 3: synthetic truth, bronze and shared feeds")
    ap.add_argument("--catalog", default=None)
    ap.add_argument("--profile", default=None)
    ap.add_argument("--from", dest="start", default=None, help="resume from this step")
    ap.add_argument("--only", default=None, help="run just this step")
    ap.add_argument("--scale", default=None, help="override config scale (exported as SMBC_SCALE)")
    ap.add_argument("--as-of", dest="as_of", default=None, help="override config as_of_date")
    ap.add_argument("--seed", default=None, help="override config random_seed")
    ap.add_argument("--in-process", action="store_true",
                    help="run generators in this process (default on Databricks compute)")
    args = ap.parse_args()
    from smbc_genie_lib.config import export_env, load_config
    from smbc_genie_lib.spark_io import get_spark, on_databricks
    export_env(scale=args.scale, as_of_date=args.as_of, random_seed=args.seed)
    in_process = args.in_process or on_databricks()
    cfg = load_config(catalog=args.catalog) if args.catalog else load_config()
    names = [s[0] for s in STEPS]
    steps = STEPS[names.index(args.start):] if args.start else STEPS
    steps = [s for s in steps if s[0] == args.only] if args.only else steps
    run_id = "P03-" + _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    extra = (["--catalog", args.catalog] if args.catalog else []) + (["--profile", args.profile] if args.profile else [])
    log = []
    for step, script, tables in steps:
        t0 = _dt.datetime.now()
        print(f"===== {step} ({t0:%H:%M:%S}) =====", flush=True)
        rc = run_step((REPO / "src" / "10_bronze_synth" / script).resolve(), extra, in_process)
        t1 = _dt.datetime.now()
        log.append((step, script, tables, rc, t0, t1))
        if rc != 0:
            print(f"FAILED: {step} (exit {rc})", flush=True)
            break
    spark = get_spark(args.profile)
    rows = []
    for step, script, tables, rc, t0, t1 in log:
        for t in tables:
            try:
                n = spark.table(f"{cfg.catalog}.{t}").count()
            except Exception:  # noqa: BLE001 - table not built (failed step)
                n = None
            rows.append((run_id, "p03", step, f"{cfg.catalog}.{t}", n, "ok" if rc == 0 else f"exit {rc}",
                         float(cfg.scale), t0, t1, (t1 - t0).total_seconds()))
    if rows:
        spark.createDataFrame(rows, "run_id string, phase string, step string, object_name string, row_count bigint, "
                                    "message string, scale double, started_at timestamp, finished_at timestamp, "
                                    "duration_sec double").write.mode("append").saveAsTable(f"{cfg.catalog}.ops.build_run_log")
    failed = [s for s in log if s[3] != 0]
    print(f"[p03] {len(log) - len(failed)}/{len(log)} steps ok; run {run_id} logged to ops.build_run_log")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
