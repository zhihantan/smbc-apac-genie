"""Phase 3c-12 — credit & risk lifecycle (+ Sunda / Kinokawa / cash-flow-upgrade / HK CASA storyline parts).

  bronze.core_rating_history     rating actions (grade 1-10, rating equivalent, IFRS 9 stage, reason)
  bronze.core_dpd                daily days past due while a facility is in arrears (cure dates)
  bronze.core_loan_schedule      drawdown / repayment / prepayment / maturity events per facility
  bronze.credit_review           annual / new-money / amendment / watchlist reviews + memo excerpts
  bronze.credit_spreading_task   spreading task per obligor x FY statement (FY2023-FY2025)
  bronze.fin_capital_allocation  monthly per obligor: EAD, RWA, PD, LGD, stage, ECL, capital
  bronze.fin_cost_allocation     monthly per client x product family: cost to serve
  bronze.fin_client_revenue      monthly per client x product family: NII, fees, FX, trade
  bronze.cf_event                predicted liquidity shortfalls / surpluses and the action taken
  ops.synthetic_arrears_episode  truth: arrears episode -> entity, default flag, storyline

Reads the landed lending, deposit, payment, FX, trade, cash-flow and P&L feeds (run after 3c-1..3c-11);
the logic is pure Python (smbc_genie_lib.credit_risk). Keyed by obligor_id / facility_id / cust_no /
the finance engine's client reference for ER. Re-runs overwrite.

  .venv/bin/python src/10_bronze_synth/run_bronze_credit_risk.py --profile my-workspace
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from smbc_genie_lib import credit_risk as cr, fragment, reference, storylines as sl, truth  # noqa: E402
from smbc_genie_lib.config import load_config  # noqa: E402
from smbc_genie_lib.spark_io import get_spark, write_table  # noqa: E402

INGEST_TS = "2026-09-30T14:00:00"
BATCH = "P03C-20260930"
META = [("_source_system", "s"), ("_batch_id", "s"), ("_ingest_ts", "s")]
# column specs: s=STRING (dates are ISO strings, as landed), i=INT, d=DOUBLE, b=BOOLEAN, dt=DATE
SPECS = {
    "core_rating_history": ("core_banking", [
        ("rating_event_id", "s"), ("obligor_id", "s"), ("effective_date", "s"), ("rating_action", "s"),
        ("previous_grade", "i"), ("internal_grade", "i"), ("notch_change", "i"), ("rating_equivalent", "s"),
        ("origination_grade", "i"), ("ifrs9_stage", "i"), ("previous_stage", "i"), ("action_reason", "s"),
        ("rating_model", "s"), ("review_id", "s"), ("approver_id", "s")]),
    "core_dpd": ("core_banking", [
        ("facility_id", "s"), ("obligor_id", "s"), ("dpd_date", "s"), ("days_past_due", "i"), ("dpd_bucket", "s"),
        ("overdue_amount_lcy", "d"), ("overdue_amount_usd", "d"), ("currency", "s"), ("arrears_episode_id", "s"),
        ("due_date", "s"), ("cure_date", "s"), ("is_cured", "b")]),
    "core_loan_schedule": ("core_banking", [
        ("schedule_event_id", "s"), ("facility_id", "s"), ("obligor_id", "s"), ("event_type", "s"),
        ("event_date", "s"), ("amount_lcy", "d"), ("amount_usd", "d"), ("currency", "s"), ("drawn_after_usd", "d"),
        ("is_contractual", "b"), ("status", "s"), ("purpose", "s")]),
    "credit_review": ("credit_workflow", [
        ("review_id", "s"), ("obligor_id", "s"), ("facility_id", "s"), ("review_type", "s"),
        ("review_fiscal_year", "i"), ("due_date", "s"), ("preparation_start_date", "s"), ("submitted_date", "s"),
        ("approved_date", "s"), ("status", "s"), ("days_late", "i"), ("days_in_preparation", "i"),
        ("submission_count", "i"), ("outcome", "s"), ("blocker_reason", "s"), ("current_grade", "i"),
        ("recommended_grade", "i"), ("approved_grade", "i"), ("requested_amount_usd", "d"),
        ("proposed_margin_bps", "i"), ("is_pricing_exception", "b"), ("exception_reason", "s"),
        ("analyst_id", "s"), ("approver_id", "s"), ("memo_excerpt", "s"), ("memo_tone", "s"),
        ("memo_sentiment_score", "d")]),
    "credit_spreading_task": ("credit_workflow", [
        ("task_id", "s"), ("obligor_id", "s"), ("fiscal_year", "i"), ("fiscal_year_label", "s"),
        ("fiscal_year_end", "s"), ("statement_basis", "s"), ("received_date", "s"), ("due_date", "s"),
        ("spread_date", "s"), ("checked_date", "s"), ("analyst_id", "s"), ("checker_id", "s"), ("status", "s"),
        ("days_to_spread", "i"), ("days_overdue", "i"), ("is_overdue", "b")]),
    "fin_capital_allocation": ("profitability_engine", [
        ("obligor_id", "s"), ("month", "s"), ("as_of_date", "s"), ("fiscal_year", "i"), ("fiscal_quarter", "i"),
        ("internal_grade", "i"), ("rating_equivalent", "s"),
        ("ifrs9_stage", "i"), ("days_past_due_max", "i"), ("dpd_bucket", "s"), ("drawn_usd", "d"), ("limit_usd", "d"),
        ("ead_usd", "d"), ("ccf", "d"), ("risk_weight", "d"), ("rwa_usd", "d"), ("pd_12m", "d"), ("pd_lifetime", "d"),
        ("lgd", "d"), ("remaining_tenor_years", "d"), ("ecl_12m_usd", "d"), ("ecl_lifetime_usd", "d"),
        ("ecl_usd", "d"), ("ecl_change_usd", "d"), ("expected_loss_usd", "d"), ("economic_capital_usd", "d"),
        ("book_equity_usd", "d"), ("allocated_capital_usd", "d"), ("cost_of_capital_usd", "d"),
        ("is_individually_assessed", "b")]),
    "fin_cost_allocation": ("profitability_engine", [
        ("client_source_system", "s"), ("client_source_id", "s"), ("month", "s"), ("fiscal_year", "i"),
        ("fiscal_quarter", "i"), ("product_family", "s"), ("direct_cost_usd", "d"), ("rm_cost_usd", "d"),
        ("operations_cost_usd", "d"), ("ho_allocation_usd", "d"), ("total_cost_usd", "d"), ("is_pnl_reconciled", "b")]),
    "fin_client_revenue": ("profitability_engine", [
        ("client_source_system", "s"), ("client_source_id", "s"), ("month", "s"), ("fiscal_year", "i"),
        ("fiscal_quarter", "i"), ("business_line", "s"), ("product_family", "s"), ("nii_usd", "d"), ("fee_usd", "d"),
        ("trading_usd", "d"), ("total_revenue_usd", "d"), ("activity_basis_usd", "d"), ("is_pnl_reconciled", "b")]),
    "cf_event": ("cashflow_engine", [
        ("event_id", "s"), ("cust_no", "s"), ("event_type", "s"), ("event_date", "s"), ("forecast_run_date", "s"),
        ("forecast_target_month", "s"), ("model_version", "s"), ("horizon_days", "i"), ("forecast_inflows_usd", "d"),
        ("expected_outflows_usd", "d"), ("predicted_net_usd", "d"), ("predicted_amount_usd", "d"),
        ("severity_ratio", "d"), ("has_rcf", "b"), ("undrawn_rcf_usd", "d"), ("action_taken", "s"),
        ("action_date", "s"), ("days_to_action", "i"), ("action_amount_usd", "d"), ("linked_facility_id", "s"),
        ("linked_schedule_event_id", "s"), ("est_action_revenue_usd", "d"), ("event_status", "s")]),
}
TRUTH_SPEC = [("episode_id", "s"), ("facility_id", "s"), ("obligor_id", "s"), ("entity_id", "s"),
              ("due_date", "dt"), ("cure_date", "dt"), ("kind", "s"), ("is_default", "b"), ("storyline_key", "s")]
COMMENTS = {
    "core_rating_history": "Bronze internal rating actions per obligor from FY2023 "
                           "(grade 1-10, rating equivalent, IFRS 9 stage, reason).",
    "core_dpd": "Bronze daily days-past-due per facility while in arrears (overdue amount, episode, due/cure dates).",
    "core_loan_schedule": "Bronze facility drawdown/repayment/prepayment/maturity events behind month-end drawn.",
    "credit_review": "Bronze credit reviews (annual/new money/amendment/watchlist): dates, status, grades, memo.",
    "credit_spreading_task": "Bronze financial-spreading tasks per obligor x FY statement (received/spread/checked).",
    "fin_capital_allocation": "Bronze monthly capital per obligor: EAD, RWA, PD, LGD, IFRS 9 stage, ECL, capital.",
    "fin_cost_allocation": "Bronze monthly cost to serve per client x product family (direct/RM/operations/HO).",
    "fin_client_revenue": "Bronze monthly revenue per client x product family (lending/deposit NII, fees, FX, trade).",
    "cf_event": "Bronze predicted liquidity shortfall/surplus events and the action taken (RCF drawdown / TD).",
}


def _df(spark, rows, spec):
    from pyspark.sql.types import (BooleanType, DateType, DoubleType, IntegerType, StringType,
                                   StructField, StructType)
    types = {"s": StringType(), "i": IntegerType(), "d": DoubleType(), "b": BooleanType(), "dt": DateType()}
    schema = StructType([StructField(c, types[t], True) for c, t in spec])
    return spark.createDataFrame([[r.get(c) for c, _ in spec] for r in rows], schema)


def _write(spark, rows, spec, fqn, comment, chunk=100_000):
    """createDataFrame is capped per call: build big tables in chunks and union them."""
    df = None
    for i in range(0, max(1, len(rows)), chunk):
        part = _df(spark, rows[i:i + chunk], spec)
        df = part if df is None else df.unionByName(part)
    return write_table(df, fqn, comment=comment)


def _date(s):
    return _dt.date.fromisoformat(s) if s else None


def load_inputs(spark, cfg) -> dict:
    """Collect the landed feeds this step derives from (small aggregates; dates as date objects)."""
    c, as_of = cfg.catalog, cfg.as_of_date
    q = lambda sql: [r.asDict() for r in spark.sql(sql).collect()]  # noqa: E731
    inp = {
        "terms": q(f"""SELECT facility_id, obligor_id, facility_type, limit_usd, currency,
                         CAST(margin_bps AS INT) margin_bps, security_type, guarantor_type, origination_date,
                         maturity_date, has_covenant FROM {c}.bronze.credit_facility_terms"""),
        "balances": q(f"""SELECT facility_id, balance_date, limit_usd, drawn_usd
                          FROM {c}.bronze.core_facility_balance_monthly"""),
        "watchlist": q(f"SELECT obligor_id, band, months_on_watch FROM {c}.bronze.ews_watchlist"),
        "waivers": q(f"SELECT facility_id, test_date, covenant_type FROM {c}.bronze.credit_covenant_test WHERE waiver"),
        "pnl": q(f"""SELECT obligor_id, fiscal_year, fiscal_quarter, rev_lending, rev_deposits, rev_payments,
                       cost_to_serve FROM {c}.bronze.fin_relationship_pnl"""),
        "forecasts": q(f"""SELECT cust_no, forecast_run_date, target_month, model_version, forecast_inflows_usd
                           FROM {c}.bronze.cf_forecast"""),
    }
    health = {}
    for r in spark.sql(f"""SELECT h.entity_id, h.month, h.health FROM {c}.ops.synthetic_entity_health_monthly h
                           WHERE h.entity_id IN (SELECT entity_id FROM {c}.ops.synthetic_truth_xref
                                                 WHERE source_system = 'credit_obligor')""").collect():
        health.setdefault(r["entity_id"], {})[r["month"]] = float(r["health"])
    inp["health"] = health
    inp["statements"] = {
        (r["obligor_id"], r["fiscal_year"]): {"fye": _date(r["fiscal_year_end"]), "is_spread": r["is_spread"],
                                              "is_audited": r["is_audited"], "spread_date": _date(r["spread_date"])}
        for r in q(f"""SELECT DISTINCT obligor_id, fiscal_year, fiscal_year_end, is_spread, is_audited, spread_date
                       FROM {c}.bronze.credit_financial_statement""")}
    ratios = {}
    for r in q(f"""SELECT obligor_id, fiscal_year, ratio_name, ratio_value FROM {c}.bronze.credit_ratio
                   WHERE ratio_name IN ('Net Debt/EBITDA', 'ICR')"""):
        ratios.setdefault((r["obligor_id"], r["fiscal_year"]), {})[r["ratio_name"]] = r["ratio_value"]
    inp["ratios"] = ratios
    inp["deal_pricing"] = {r["facility_id"]: r for r in q(
        f"SELECT facility_id, meets_standalone_hurdle, exception_reason FROM {c}.bronze.fin_deal_pricing")}
    inp["deposits"] = {(r["cust_no"], r["m"]): (float(r["casa"]), float(r["td"])) for r in q(f"""
        SELECT cust_no, trunc(balance_date, 'MM') m,
          sum(CASE WHEN deposit_class = 'CASA' THEN balance_usd ELSE 0 END) casa,
          sum(CASE WHEN deposit_class = 'TD' THEN balance_usd ELSE 0 END) td
        FROM {c}.bronze.core_deposit_balance_monthly WHERE balance_date <= DATE'{as_of}' GROUP BY 1, 2""")}
    inp["payments"] = {(r["cust_no"], r["m"]): (int(r["n"]), float(r["xb"])) for r in q(f"""
        SELECT cust_no, trunc(payment_date, 'MM') m, count(*) n,
          sum(CASE WHEN is_cross_border THEN amount_usd ELSE 0 END) xb
        FROM {c}.bronze.pay_payment_message GROUP BY 1, 2""")}
    inp["n_accounts"] = {r["cust_no"]: int(r["n"]) for r in q(
        f"SELECT cust_no, count(*) n FROM {c}.bronze.core_account GROUP BY 1")}
    inp["fx_revenue"] = {(r["cpty_id"], r["m"]): (float(r["rev"]), float(r["vol"])) for r in q(f"""
        SELECT cpty_id, trunc(deal_date, 'MM') m, sum(revenue_usd) rev, sum(notional_usd) vol
        FROM {c}.bronze.tsy_fx_deal GROUP BY 1, 2""")}
    inp["trade_fees"] = {(r["party_id"], r["m"]): (float(r["fee"]), float(r["vol"])) for r in q(f"""
        SELECT party_id, trunc(txn_date, 'MM') m, sum(commission_usd) fee, sum(amount_usd) vol
        FROM {c}.bronze.trade_finance_txn GROUP BY 1, 2""")}
    inp["actuals"] = {(r["cust_no"], r["month"]): (float(r["inflows_usd"]), float(r["outflows_usd"])) for r in q(
        f"SELECT cust_no, month, inflows_usd, outflows_usd FROM {c}.bronze.cf_actual_monthly")}
    inp["fx"] = {(r["currency_code"], r["date"]): r["rate_per_usd"] for r in reference.build_fx_daily(cfg)}
    return inp


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 3c-12: credit & risk lifecycle")
    ap.add_argument("--catalog", default=None)
    ap.add_argument("--profile", default=None)
    args = ap.parse_args()
    cfg = load_config(catalog=args.catalog) if args.catalog else load_config()
    c = cfg.catalog
    spark = get_spark(args.profile)

    groups = truth.build_groups(cfg)
    entities = truth.build_entities(cfg, groups)
    _, xref = fragment.fragment_all(cfg, entities, [g["group_id"] for g in groups])
    inp = load_inputs(spark, cfg)
    print(f"[p03c12] inputs: {len(inp['terms']):,} facilities, {len(inp['balances']):,} balance rows, "
          f"{len(inp['pnl']):,} P&L rows, {len(inp['forecasts']):,} forecasts")
    out = cr.build_credit_risk(cfg, entities, xref, truth.build_people(cfg), inp)

    for t, (src, spec) in SPECS.items():
        for r in out[t]:
            r.update(_source_system=src, _batch_id=BATCH, _ingest_ts=INGEST_TS)
        n = _write(spark, out[t], spec + META, f"{c}.bronze.{t}", COMMENTS[t])
        print(f"[p03c12] bronze.{t:24} {n:>9,} rows")
    n = _write(spark, out["truth_arrears_episode"], TRUTH_SPEC, f"{c}.ops.synthetic_arrears_episode",
               "Generation truth: arrears episode -> entity, kind (technical/arrears/default/storyline), default.")
    print(f"[p03c12] ops.synthetic_arrears_episode   {n:>9,} rows")
    verify_storylines(spark, cfg, out)
    verify_consistency(spark, cfg, out["_meta"])
    print("[p03c12] done.")


def verify_storylines(spark, cfg, out) -> None:
    """Sunda / cash-flow upgrade / Kinokawa checks from the written tables."""
    c, s, e = cfg.catalog, sl.SUNDA, out["_meta"]["sunda_ecl"]
    q = lambda sql: spark.sql(sql).collect()  # noqa: E731
    sunda = q(f"""SELECT facility_id, max(CASE WHEN dpd_date = '2026-04-15' THEN days_past_due END) a15,
        max(CASE WHEN dpd_date = '2026-04-30' THEN days_past_due END) a30,
        max(CASE WHEN dpd_date = '2026-06-30' THEN days_past_due END) j30, max(cure_date) cure
      FROM {c}.bronze.core_dpd WHERE due_date = '{s['missed_payment_due']}' GROUP BY 1""")
    print("[p03c12] DPD episodes due 31-Mar-2026: " + "; ".join(
        f"{r['facility_id']} 15-Apr={r['a15']} 30-Apr={r['a30']} 30-Jun={r['j30']} cure={r['cure']}" for r in sunda))
    rt = q(f"""SELECT obligor_id, ifrs9_stage, action_reason FROM {c}.bronze.core_rating_history
               WHERE effective_date = '{s['downgrade_date']}' AND rating_action = 'Downgrade'
               AND previous_grade = {s['grade_from']} AND internal_grade = {s['grade_to']}""")
    print("[p03c12] downgrades 7->9 on 12-Jun-2026: " + "; ".join(
        f"{r['obligor_id']} stage {r['ifrs9_stage']} ({r['action_reason'][:40]}...)" for r in rt))
    if e:
        print(f"[p03c12] Sunda group ECL May->Jun-2026: {e['ecl_may'] / 1e6:.1f}m -> {e['ecl_jun'] / 1e6:.1f}m "
              f"(+{e['delta'] / 1e6:.1f}m; lead EAD {e['lead_ead_jun'] / 1e6:.1f}m, "
              f"individually assessed LGD {e['lgd']:.2f})")
    rv = q(f"""SELECT obligor_id, submitted_date, days_late FROM {c}.bronze.credit_review
               WHERE review_type = 'Annual' AND review_fiscal_year = 2025 AND due_date = '{s['review_due']}'""")
    print("[p03c12] FY2025 annual reviews due 29-May-2026: " + "; ".join(
        f"{r['obligor_id']} submitted {r['submitted_date']} ({r['days_late']} days late)" for r in rv))
    n_short, n_follow = cr.shortfalls_followed(out)
    print(f"[p03c12] predicted shortfalls Jun-Sep 2026: {n_short}, "
          f"followed by an RCF drawdown within 10 days: {n_follow}")
    chk = q(f"""SELECT count(*) n FROM {c}.bronze.cf_event e JOIN {c}.bronze.core_loan_schedule l
                ON l.schedule_event_id = e.linked_schedule_event_id
                WHERE e.action_taken = 'RCF Drawdown' AND l.event_type = 'Drawdown'
                AND datediff(CAST(l.event_date AS DATE), CAST(e.event_date AS DATE)) = e.days_to_action""")[0]["n"]
    print(f"[p03c12] cf_event RCF actions joined to core_loan_schedule drawdowns: {chk}")
    kp = q(f"""SELECT facility_id, event_date, amount_usd FROM {c}.bronze.core_loan_schedule
               WHERE event_type = 'Prepayment' AND purpose = 'Refinanced with another bank'""")
    print("[p03c12] Kinokawa prepayment: " + "; ".join(
        f"{r['facility_id']} {r['event_date']} USD {r['amount_usd'] / 1e6:.0f}m" for r in kp))


def verify_consistency(spark, cfg, meta) -> None:
    """Stage mix, arrears, loan schedule vs month-end balances, reconciliation to fin_relationship_pnl."""
    c = cfg.catalog
    q = lambda sql: spark.sql(sql).collect()  # noqa: E731
    st = q(f"""SELECT ifrs9_stage, count(*) n FROM {c}.bronze.fin_capital_allocation
               WHERE month = '{cfg.as_of_date[:8]}01' GROUP BY 1 ORDER BY 1""")
    print("[p03c12] obligors by IFRS 9 stage at as-of: " + ", ".join(f"S{r['ifrs9_stage']}={r['n']}" for r in st))
    ep = q(f"""SELECT kind, count(*) n, count(DISTINCT facility_id) f FROM {c}.ops.synthetic_arrears_episode
               GROUP BY 1 ORDER BY 1""")
    print("[p03c12] arrears episodes: " + ", ".join(f"{r['kind']}={r['n']} ({r['f']} fac)" for r in ep))
    gap = q(f"""WITH b AS (SELECT facility_id, balance_date, drawn_usd FROM {c}.bronze.core_facility_balance_monthly
                           WHERE balance_date <= DATE'{cfg.as_of_date}'),
      t AS (SELECT facility_id, origination_date, maturity_date FROM {c}.bronze.credit_facility_terms),
      f AS (SELECT facility_id, min(balance_date) first_me FROM b GROUP BY 1),
      o AS (SELECT f.facility_id,
              CASE WHEN t.origination_date < DATE'{cr.HISTORY_START}' THEN b.drawn_usd ELSE 0 END opening
            FROM f JOIN t ON t.facility_id = f.facility_id
            JOIN b ON b.facility_id = f.facility_id AND b.balance_date = f.first_me),
      ev AS (SELECT facility_id, CAST(event_date AS DATE) d,
               CASE WHEN event_type = 'Drawdown' THEN amount_usd ELSE -amount_usd END amt
             FROM {c}.bronze.core_loan_schedule WHERE status = 'Settled'),
      x AS (SELECT b.facility_id, b.balance_date, b.drawn_usd, o.opening + coalesce(sum(ev.amt), 0) rebuilt
            FROM b JOIN o ON o.facility_id = b.facility_id JOIN t ON t.facility_id = b.facility_id
            LEFT JOIN ev ON ev.facility_id = b.facility_id AND ev.d <= b.balance_date
            WHERE trunc(b.balance_date, 'MM') < trunc(t.maturity_date, 'MM')
            GROUP BY b.facility_id, b.balance_date, b.drawn_usd, o.opening)
      SELECT count(*) n, sum(CASE WHEN abs(rebuilt - drawn_usd) > 0.05 THEN 1 ELSE 0 END) bad FROM x""")[0]
    print(f"[p03c12] loan schedule rebuilds month-end drawn: {gap['n'] - gap['bad']:,}/{gap['n']:,} facility-months "
          f"(mismatches: {gap['bad']}; Kinokawa's prepay facility {meta['kinokawa_facility']} awaits integration)")
    rec = q(f"""WITH r AS (SELECT client_source_id obligor_id, fiscal_year fy, fiscal_quarter fq,
          sum(CASE WHEN product_family = 'Corporate Lending' THEN total_revenue_usd ELSE 0 END) lend,
          sum(CASE WHEN product_family IN ('Cash', 'Liquidity') THEN total_revenue_usd ELSE 0 END) dep,
          sum(CASE WHEN product_family = 'Payments' THEN total_revenue_usd ELSE 0 END) pay
        FROM {c}.bronze.fin_client_revenue WHERE client_source_system = 'credit_obligor' GROUP BY 1, 2, 3),
      k AS (SELECT client_source_id obligor_id, fiscal_year fy, fiscal_quarter fq, sum(total_cost_usd) cost
        FROM {c}.bronze.fin_cost_allocation WHERE client_source_system = 'credit_obligor' GROUP BY 1, 2, 3),
      a AS (SELECT obligor_id, fiscal_year fy, fiscal_quarter fq,
          avg(ead_usd) ead, avg(rwa_usd) rwa, avg(book_equity_usd) be, avg(economic_capital_usd) ecap,
          sum(expected_loss_usd) el FROM {c}.bronze.fin_capital_allocation GROUP BY 1, 2, 3),
      d AS (SELECT p.*, abs(coalesce(r.lend, 0) - p.rev_lending) d1, abs(coalesce(r.dep, 0) - p.rev_deposits) d2,
          abs(coalesce(r.pay, 0) - p.rev_payments) d3, abs(coalesce(k.cost, 0) - p.cost_to_serve) d4,
          a.ead a_ead, a.rwa a_rwa, a.be a_be, a.ecap a_ecap, a.el a_el
        FROM {c}.bronze.fin_relationship_pnl p
        LEFT JOIN r ON r.obligor_id = p.obligor_id AND r.fy = p.fiscal_year AND r.fq = p.fiscal_quarter
        LEFT JOIN k ON k.obligor_id = p.obligor_id AND k.fy = p.fiscal_year AND k.fq = p.fiscal_quarter
        LEFT JOIN a ON a.obligor_id = p.obligor_id AND a.fy = p.fiscal_year AND a.fq = p.fiscal_quarter)
      SELECT count(*) n, sum(CASE WHEN greatest(d1, d2, d3, d4) <= 0.01 THEN 1 ELSE 0 END) exact,
        round(max(d1 + d2 + d3 + d4), 2) max_diff, round(sum(a_ead) / sum(ead), 4) ead_ratio,
        round(sum(a_rwa) / sum(rwa), 4) rwa_ratio, round(sum(a_be) / sum(book_equity), 4) be_ratio,
        round(sum(a_ecap) / sum(economic_capital), 4) ecap_ratio, round(sum(a_el) / sum(expected_loss), 4) el_ratio,
        round(avg(CASE WHEN abs(a_rwa - rwa) <= 0.005 * rwa THEN 1.0 ELSE 0.0 END), 3) rwa_match_share FROM d""")[0]
    print(f"[p03c12] vs fin_relationship_pnl: {rec['exact']:,}/{rec['n']:,} obligor-quarters exact on lending/"
          f"deposit/payment revenue + cost (max diff {rec['max_diff']}); capital sum ratios EAD {rec['ead_ratio']}, "
          f"book equity {rec['be_ratio']}, RWA {rec['rwa_ratio']} ({rec['rwa_match_share']:.0%} within 0.5%), "
          f"ECAP {rec['ecap_ratio']}, EL {rec['el_ratio']}")
    sp = q(f"""SELECT status, count(*) n FROM {c}.bronze.credit_spreading_task
               WHERE fiscal_year = 2025 GROUP BY 1 ORDER BY 2 DESC""")
    print("[p03c12] FY2025 spreading tasks: " + ", ".join(f"{r['status']}={r['n']}" for r in sp))


if __name__ == "__main__":
    main()
