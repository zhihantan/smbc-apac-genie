"""Storyline assertions (PLAN §5.4 "key assertions", §11 test_storylines; ops.storyline_assertions).

Each check is a SQL query over the written tables that returns one number (`actual`) and the band it
must fall in. They are run by src/10_bronze_synth/run_storyline_checks.py (results to
ops.storyline_assertions) and by tests/test_storylines.py. Storyline entities are found through the
ground truth (ops.synthetic_truth_*), which only the checks may use.
"""
from __future__ import annotations

from typing import Dict, List

NAMES = {1: "Sunda Energi Nusantara EWS cascade", 2: "Kinokawa wallet loss and recapture",
         3: "Meridian Agri entity resolution", 4: "Tanaka Chemical JP parent support",
         5: "HK CASA migration", 6: "VN and IN trade surge", 7: "KYC migration backlog",
         8: "Below-hurdle cluster", 9: "Cash-flow model upgrade", 10: "AU renewables sponsor",
         11: "Covenant blind spot", 12: "Reprocessed payments file", 13: "Delta Share staleness"}


def _ent(c: str, key: str, lead: bool = True, country: str = "") -> str:
    cond = " AND is_group_lead" if lead else ""
    cond += f" AND booking_country = '{country}'" if country else ""
    return f"(SELECT entity_id FROM {c}.ops.synthetic_truth_entity WHERE storyline_key = '{key}'{cond})"


def _src(c: str, src: str, key: str, lead: bool = True, country: str = "") -> str:
    return (f"(SELECT source_id FROM {c}.ops.synthetic_truth_xref WHERE source_system = '{src}' "
            f"AND NOT is_within_source_dup AND entity_id IN {_ent(c, key, lead, country)})")


def checks(c: str) -> List[Dict]:
    """All storyline checks for catalog `c` (sid, key, description, sql -> `actual`, lo, hi)."""
    sunda_obl, sunda_cust = _src(c, "credit_obligor", "sunda"), _src(c, "core_customer", "sunda")
    tanaka_obl = _src(c, "credit_obligor", "tanaka")
    hk_cust = _src(c, "core_customer", "hk_casa", lead=False)
    out: List[Dict] = []

    def add(sid, key, desc, sql, lo, hi=None):
        out.append({"storyline_id": sid, "storyline_name": NAMES[sid], "assertion_key": key,
                    "description": desc, "sql": sql, "lo": lo, "hi": lo if hi is None else hi})

    # 1 Sunda ---------------------------------------------------------------------------------
    jan_news = (f"FROM {c}.bronze.ext_news WHERE company_id IN {_src(c, 'ext_company_master', 'sunda', lead=False)} "
                f"AND published_date BETWEEN '2026-01-01' AND '2026-01-31' AND subtopic = 'Coal Regulation'")
    add(1, "jan_news_items", "Coal-regulation news items in Jan-2026", f"SELECT count(*) AS actual {jan_news}", 8)
    add(1, "jan_news_min_sentiment", "Lowest Jan-2026 sentiment >= -0.7", f"SELECT min(raw_sentiment) AS actual {jan_news}", -0.7, -0.5)
    add(1, "jan_news_max_sentiment", "Highest Jan-2026 sentiment <= -0.5", f"SELECT max(raw_sentiment) AS actual {jan_news}", -0.7, -0.5)
    add(1, "feb_outflow_pct", "Deposit outflow over the 30 days from 1-Feb-2026",
        f"""SELECT 1 - sum(CASE WHEN balance_date = DATE'2026-03-03' THEN balance_usd END)
                 / sum(CASE WHEN balance_date = DATE'2026-02-01' THEN balance_usd END) AS actual
            FROM {c}.bronze.core_deposit_balance_daily WHERE cust_no IN {sunda_cust}""", 0.30, 0.40)
    add(1, "mar_utilisation", "Max utilisation at end-Mar-2026",
        f"""SELECT max(utilisation_pct) AS actual FROM {c}.bronze.core_facility_balance_monthly
            WHERE obligor_id IN {sunda_obl} AND balance_date = DATE'2026-03-31'""", 0.95, 1.08)
    for dpd in (15, 30):
        add(1, f"dpd_{dpd}_in_april", f"DPD reached {dpd} in Apr-2026",
            f"""SELECT count(*) AS actual FROM {c}.bronze.core_dpd WHERE obligor_id IN {sunda_obl}
                AND days_past_due = {dpd} AND dpd_date BETWEEN '2026-04-01' AND '2026-04-30'""", 1, 99)
    add(1, "fy2025_breach", "FY2025 leverage test 4.6x vs 4.0x, breached",
        f"""SELECT count(*) AS actual FROM {c}.bronze.credit_covenant_test WHERE obligor_id IN {sunda_obl}
            AND test_basis = 'FY2025 Annual' AND actual = 4.6 AND threshold = 4.0 AND breached""", 1)
    add(1, "june_downgrade", "Jun-2026 downgrade 7 -> 9 with Stage 3",
        f"""SELECT count(*) AS actual FROM {c}.bronze.core_rating_history WHERE obligor_id IN {sunda_obl}
            AND effective_date BETWEEN '2026-06-01' AND '2026-06-30' AND previous_grade = 7
            AND internal_grade = 9 AND ifrs9_stage = 3""", 1)
    add(1, "red_end_june", "Final EWS band Red on 30-Jun-2026 (1 = yes)",
        f"""SELECT max(CASE WHEN final_band = 'Red' THEN 1 ELSE 0 END) AS actual FROM {c}.bronze.ews_score
            WHERE obligor_id IN {sunda_obl} AND score_date = DATE'2026-06-30'""", 1)
    add(1, "ecl_delta_usd", "ECL increase into June (USD)",
        f"""SELECT sum(CASE WHEN month = DATE'2026-06-01' THEN ecl_usd END)
                 - sum(CASE WHEN month = DATE'2025-12-01' THEN ecl_usd END) AS actual
            FROM {c}.bronze.fin_capital_allocation WHERE obligor_id IN {sunda_obl}""", 45e6, 51e6)
    add(1, "score_early_jan", "Average EWS score 1-5 Jan 2026",
        f"""SELECT avg(composite_score) AS actual FROM {c}.bronze.ews_score WHERE obligor_id IN {sunda_obl}
            AND score_date BETWEEN DATE'2026-01-01' AND DATE'2026-01-05'""", 20, 24)
    add(1, "score_end_june", "EWS score on 30-Jun-2026",
        f"""SELECT max(composite_score) AS actual FROM {c}.bronze.ews_score WHERE obligor_id IN {sunda_obl}
            AND score_date = DATE'2026-06-30'""", 76, 80)
    add(1, "review_days_late", "FY2025 annual review submitted days late",
        f"""SELECT max(days_late) AS actual FROM {c}.bronze.credit_review WHERE obligor_id IN {sunda_obl}
            AND review_type = 'Annual' AND review_fiscal_year = 2025""", 23)

    # 3 Meridian ------------------------------------------------------------------------------
    mer = f"(SELECT source_system, source_id FROM {c}.ops.synthetic_truth_xref WHERE entity_id IN {_ent(c, 'meridian')})"
    for run, n in (("ER-20251231", 3), ("ER-20260331", 1)):
        add(3, f"golden_records_{run}", f"Golden records for Meridian's scripted records at {run}",
            f"""SELECT count(DISTINCT m.golden_client_id) AS actual FROM {c}.silver.er_cluster_membership m
                JOIN {mer} x ON x.source_system = m.source_system AND x.source_id = m.source_id
                WHERE m.run_id = '{run}'""", n)
    add(3, "exposure_change_pct", "Meridian group exposure change on the Mar-2026 resolution",
        f"""SELECT max(resolution_change_pct) AS actual FROM {c}.silver.group_exposure_by_er_run
            WHERE run_id = 'ER-20260331' AND client_group_id = 'SYN-G-0005'""", 0.35, 0.45)
    add(3, "threshold_crossed", "Crossed the USD 500m attention threshold on resolution (1 = yes)",
        f"""SELECT max(CASE WHEN crossed_threshold_on_resolution THEN 1 ELSE 0 END) AS actual
            FROM {c}.silver.group_exposure_by_er_run WHERE run_id = 'ER-20260331' AND client_group_id = 'SYN-G-0005'""", 1)
    add(3, "th_case_unmatched_at_intake", "Meridian Thai onboarding case not matched to the group at intake",
        f"""SELECT count(*) AS actual FROM {c}.bronze.kyc_case WHERE kyc_id IN
            {_src(c, 'kyc_customer', 'meridian', lead=False, country='TH')} AND NOT intake_group_match""", 1, 9)

    # 4 Tanaka --------------------------------------------------------------------------------
    add(4, "computed_amber_days", "Days Jul-Sep 2026 with computed band Amber",
        f"""SELECT count(*) AS actual FROM {c}.bronze.ews_score WHERE obligor_id IN {tanaka_obl}
            AND score_date >= DATE'2026-07-06' AND band = 'Amber'""", 80, 92)
    add(4, "final_green_days", "Days Jul-Sep 2026 with final band Green after the override",
        f"""SELECT count(*) AS actual FROM {c}.bronze.ews_score WHERE obligor_id IN {tanaka_obl}
            AND score_date >= DATE'2026-07-06' AND final_band = 'Green'""", 80, 92)
    add(4, "override_reason", "Overrides citing parent support confirmed (JP data)",
        f"""SELECT count(*) AS actual FROM {c}.bronze.ews_override WHERE obligor_id IN {tanaka_obl}
            AND rationale LIKE 'Parent support confirmed (JP data)%'""", 2)
    add(4, "keepwell", "Active keepwell from the JP parent",
        f"""SELECT count(*) AS actual FROM {c}.shared.share_jp_support_letters WHERE apac_obligor_ref IN {tanaka_obl}
            AND support_type = 'Keepwell' AND status = 'Active'""", 1, 9)
    add(4, "parent_upgrade_jun", "JP parent upgraded in Jun-2026",
        f"""SELECT count(*) AS actual FROM {c}.shared.share_jp_parent_rating WHERE global_group_id = 'SYN-G-0003'
            AND rating_action = 'Upgrade' AND rating_date BETWEEN DATE'2026-06-01' AND DATE'2026-06-30'""", 1)

    # 5 HK CASA -------------------------------------------------------------------------------
    hk = (f"FROM {c}.bronze.core_deposit_balance_monthly m JOIN {c}.ops.synthetic_account a ON a.account_id = m.account_id "
          f"JOIN {c}.ops.synthetic_truth_entity e ON e.entity_id = a.entity_id WHERE e.booking_country = 'HK'")
    for d, lo, hi in (("2026-05-31", 0.605, 0.635), ("2026-08-31", 0.475, 0.505)):
        add(5, f"hk_casa_{d[:7]}", f"HK CASA ratio at {d}",
            f"""SELECT sum(CASE WHEN m.deposit_class = 'CASA' THEN m.balance_usd END) / sum(m.balance_usd) AS actual
                {hk} AND m.balance_date = DATE'{d}'""", lo, hi)
    add(5, "moved_usd", "Trio transfers from current accounts into 3-6 month TDs, Jun-Aug 2026 (USD)",
        f"""SELECT sum(principal_usd) AS actual FROM {c}.bronze.core_time_deposit WHERE cust_no IN {hk_cust}
            AND funding_source = 'Transfer from Current Account'
            AND placement_date BETWEEN DATE'2026-06-01' AND DATE'2026-08-31' AND tenor_months BETWEEN 3 AND 6""",
        850e6, 950e6)

    # 7 KYC backlog (SQL twins of kyc.py's measures) -------------------------------------------
    add(7, "median_days_to_live_before", "Median days to live, requests before Feb-2026",
        f"""SELECT percentile(datediff(go_live_date, request_date), 0.5) AS actual FROM {c}.ops.synthetic_onboarding_case
            WHERE status = 'Live' AND request_date < DATE'2026-02-01'""", 19, 23)
    add(7, "median_days_to_live_peak", "Median days to live, Mar-May 2026 requests",
        f"""SELECT percentile(datediff(go_live_date, request_date), 0.5) AS actual FROM {c}.ops.synthetic_onboarding_case
            WHERE status = 'Live' AND request_date BETWEEN DATE'2026-03-01' AND DATE'2026-05-31'""", 36, 40)
    add(7, "fi_kyc_docs_delta", "FI KYC Docs stage days, Mar-May 2026 minus pre-migration",
        f"""SELECT avg(CASE WHEN k.request_date BETWEEN '2026-03-01' AND '2026-05-31' THEN s.days_in_stage END)
                 - avg(CASE WHEN k.request_date < '2026-02-01' THEN s.days_in_stage END) AS actual
            FROM {c}.bronze.kyc_case_stage s JOIN {c}.bronze.kyc_case k ON k.case_id = s.case_id
            WHERE k.segment = 'Financial Institution' AND s.stage_name = 'KYC Docs' AND s.exited_date IS NOT NULL""", 10, 14)
    od = (f"""WITH me AS (SELECT date FROM {c}.bronze.ref_calendar WHERE is_month_end
                AND date BETWEEN DATE'2025-04-01' AND DATE'2026-05-31'),
              r AS (SELECT r.review_id, CAST(r.due_date AS DATE) due, CAST(r.completed_date AS DATE) done
                    FROM {c}.bronze.kyc_review r JOIN {c}.bronze.kyc_customer k ON k.kyc_id = r.kyc_id
                    WHERE r.review_type = 'Periodic' AND k.kyc_risk_rating = 'High'),
              s AS (SELECT me.date, count(r.review_id) n FROM me LEFT JOIN r
                    ON r.due < me.date AND (r.done IS NULL OR r.done > me.date) GROUP BY me.date)""")
    add(7, "may_overdue_ratio", "High-risk overdue at end-May-2026 vs the Apr-25..Jan-26 average",
        f"""{od} SELECT max(CASE WHEN date = DATE'2026-05-31' THEN n END)
                 / avg(CASE WHEN date <= DATE'2026-01-31' THEN n END) AS actual FROM s""", 2.7, 3.6)
    add(7, "backlog_cleared", "Share of the end-May-2026 backlog completed by as-of",
        f"""SELECT avg(CASE WHEN completed_date IS NOT NULL THEN 1.0 ELSE 0.0 END) AS actual FROM {c}.bronze.kyc_review
            WHERE review_type = 'Periodic' AND CAST(due_date AS DATE) < DATE'2026-05-31'
            AND (completed_date IS NULL OR CAST(completed_date AS DATE) > DATE'2026-05-31')""", 0.65, 0.75)

    # 8 Below-hurdle cluster ------------------------------------------------------------------
    add(8, "relationship_exceptions", "FY2025 deals approved below hurdle on 'Relationship' exceptions",
        f"""SELECT count(*) AS actual FROM {c}.bronze.fin_deal_pricing WHERE exception_reason = 'Relationship'
            AND signing_date BETWEEN DATE'2025-04-01' AND DATE'2025-12-31'""", 9)
    add(8, "relationship_caught_up", "... of which realised RAROC caught up",
        f"""SELECT count(*) AS actual FROM {c}.bronze.fin_deal_pricing WHERE exception_reason = 'Relationship'
            AND caught_up""", 3)

    # 9 Cash-flow model upgrade ---------------------------------------------------------------
    for v, lo, hi in (("v1", 0.17, 0.19), ("v2", 0.10, 0.12)):
        add(9, f"mape_{v}", f"Forecast MAPE, model {v}",
            f"SELECT avg(abs_pct_error) AS actual FROM {c}.bronze.cf_forecast WHERE model_version = '{v}'", lo, hi)
    add(9, "predicted_shortfalls", "Predicted shortfalls Jun-Sep 2026",
        f"""SELECT count(*) AS actual FROM {c}.bronze.cf_event WHERE event_type = 'Predicted Shortfall'
            AND event_date BETWEEN '2026-06-01' AND '2026-09-30'""", 23)
    add(9, "rcf_drawdowns_10d", "... followed by an RCF drawdown within 10 days",
        f"""SELECT count(*) AS actual FROM {c}.bronze.cf_event WHERE event_type = 'Predicted Shortfall'
            AND event_date BETWEEN '2026-06-01' AND '2026-09-30' AND linked_schedule_event_id IS NOT NULL
            AND days_to_action <= 10""", 15)

    # 10 Banksia ------------------------------------------------------------------------------
    add(10, "expansion_sentiment", "Average sentiment of Banksia expansion news Apr-Jul 2026",
        f"""SELECT avg(raw_sentiment) AS actual FROM {c}.bronze.ext_news WHERE company_id IN
            {_src(c, 'ext_company_master', 'banksia', lead=False)} AND topic = 'Expansion'
            AND published_date BETWEEN '2026-04-01' AND '2026-07-31'""", 0.6, 1.0)

    # 11 Covenant blind spot ------------------------------------------------------------------
    low = (f"(SELECT DISTINCT obligor_id FROM {c}.bronze.credit_covenant_test WHERE is_latest_test "
           f"AND covenant_type = 'Net Debt/EBITDA' AND NOT breached AND headroom_pct >= 0 AND headroom_pct < 0.10)")
    add(11, "low_headroom_clients", "Clients with 0-10% leverage headroom at the latest test", f"SELECT count(*) AS actual FROM {low}", 5)
    add(11, "low_headroom_not_on_watchlist", "... of which not on the watchlist",
        f"""SELECT count(*) AS actual FROM {low} l WHERE l.obligor_id NOT IN
            (SELECT obligor_id FROM {c}.bronze.ews_watchlist)""", 3)
    add(11, "breached_latest", "Clients breaching a leverage covenant at the latest test (Sunda)",
        f"""SELECT count(DISTINCT obligor_id) AS actual FROM {c}.bronze.credit_covenant_test WHERE is_latest_test
            AND covenant_type = 'Net Debt/EBITDA' AND breached""", 1)

    # 12 Payments replay ----------------------------------------------------------------------
    add(12, "replay_equals_original", "Replayed messages minus originals dated 1-17 Jun 2026 (0 = exact)",
        f"""SELECT sum(CASE WHEN _batch_id = 'PAY_20260617_R' THEN 1 ELSE 0 END)
                 - sum(CASE WHEN _batch_id <> 'PAY_20260617_R' AND payment_date BETWEEN DATE'2026-06-01'
                       AND DATE'2026-06-17' THEN 1 ELSE 0 END) AS actual FROM {c}.bronze.pay_payment_message""", 0)

    # 13 Delta Share staleness ----------------------------------------------------------------
    add(13, "stale_days", "Stale days for share_jp_parent_rating (all in 11-15 Aug 2026)",
        f"""SELECT count(*) AS actual FROM {c}.bronze.dq_share_refresh_log WHERE table_name = 'share_jp_parent_rating'
            AND is_stale AND log_date BETWEEN '2026-08-11' AND '2026-08-15'""", 5)
    add(13, "stale_days_elsewhere", "Stale days for that table outside the outage",
        f"""SELECT count(*) AS actual FROM {c}.bronze.dq_share_refresh_log WHERE table_name = 'share_jp_parent_rating'
            AND is_stale AND log_date NOT BETWEEN '2026-08-11' AND '2026-08-15'""", 0)
    add(13, "tanaka_reconfirmation_stale", "Tanaka's August re-confirmation flagged source_data_stale",
        f"""SELECT count(*) AS actual FROM {c}.bronze.ews_override WHERE obligor_id IN {tanaka_obl}
            AND override_date = DATE'2026-08-13' AND source_data_stale""", 1)
    # 2 Kinokawa -------------------------------------------------------------------------------
    kin_ids = (f"(SELECT source_id FROM {c}.ops.synthetic_truth_xref WHERE NOT is_within_source_dup "
               f"AND entity_id IN {_ent(c, 'kinokawa', lead=False)})")
    kin_crm = _src(c, "crm_account", "kinokawa", lead=False)
    h1 = lambda col, y: f"{col} BETWEEN DATE'{y}-04-01' AND DATE'{y}-09-30'"
    add(2, "revenue_yoy", "Group revenue H1 FY2026 vs H1 FY2025",
        f"""SELECT sum(CASE WHEN {h1('CAST(month AS DATE)', 2026)} THEN total_revenue_usd END)
                 / sum(CASE WHEN {h1('CAST(month AS DATE)', 2025)} THEN total_revenue_usd END) - 1 AS actual
            FROM {c}.bronze.fin_client_revenue WHERE client_source_id IN {kin_ids}""", -0.25, -0.19)
    add(2, "deposit_change", "Group deposits (avg month-end) H1 FY2026 vs H1 FY2025",
        f"""SELECT avg(CASE WHEN {h1('balance_date', 2026)} THEN b END) / avg(CASE WHEN {h1('balance_date', 2025)} THEN b END) - 1 AS actual
            FROM (SELECT balance_date, sum(balance_usd) b FROM {c}.bronze.core_deposit_balance_monthly
                  WHERE cust_no IN {_src(c, 'core_customer', 'kinokawa', lead=False)} GROUP BY balance_date)""", -0.05, 0.05)
    add(2, "cn_vn_settlements_growth", "CN->VN trade settlements H1 FY2026 vs H1 FY2025",
        f"""SELECT sum(CASE WHEN {h1('payment_date', 2026)} THEN amount_usd END)
                 / sum(CASE WHEN {h1('payment_date', 2025)} THEN amount_usd END) - 1 AS actual
            FROM {c}.bronze.pay_payment_message WHERE _batch_id <> 'PAY_20260617_R' AND payment_purpose = 'Trade Settlement'
            AND counterparty_country = 'VN' AND cust_no IN {_src(c, 'core_customer', 'kinokawa', lead=False, country='CN')}""",
        0.45, 0.55)
    add(2, "plan_mid_year_revision", "FY2026 account plan revised mid-year",
        f"""SELECT count(*) AS actual FROM {c}.bronze.crm_account_plan WHERE group_ref = 'SYN-G-0001'
            AND fiscal_year = 2026 AND mid_year_revision""", 1, 99)
    add(2, "nbp_rank1_scf", "Accounts whose latest NBP rank 1 is Supply Chain Finance",
        f"""SELECT count(*) AS actual FROM {c}.bronze.crm_nbp_score WHERE crm_account_id IN {kin_crm} AND rank = 1
            AND product_family = 'Supply Chain Finance'
            AND score_month = (SELECT max(score_month) FROM {c}.bronze.crm_nbp_score)""", 1, 9)
    add(2, "scf_opportunity_aug", "USD 120m SCF opportunity created in Aug-2026 from a signal",
        f"""SELECT count(*) AS actual FROM {c}.bronze.crm_opportunity WHERE crm_account_id IN {kin_crm}
            AND amount_usd = 120000000 AND created_date BETWEEN '2026-08-01' AND '2026-08-31'
            AND source_signal_id IS NOT NULL""", 1)

    # 5 HK CASA: surplus signals actioned late -------------------------------------------------
    add(5, "surplus_signal_min_lag", "Shortest action lag on the trio's May deposit-surplus signals (days)",
        f"""SELECT min(datediff(CAST(actioned_date AS DATE), CAST(detected_date AS DATE))) AS actual
            FROM {c}.bronze.crm_signal WHERE signal_code = 'DEPOSIT_SURPLUS'
            AND crm_account_id IN {_src(c, 'crm_account', 'hk_casa', lead=False)}
            AND detected_date BETWEEN '2026-05-01' AND '2026-05-31'""", 40, 120)

    # 6 VN and IN trade surge --------------------------------------------------------------------
    lcs = (f"FROM {c}.bronze.trade_finance_txn WHERE product_type = 'Import LC' AND destination_country IN ('VN', 'IN') "
           f"AND origin_country IN ('CN', 'KR', 'JP') AND commodity IN ('Electronics', 'Auto Parts')")
    add(6, "import_lc_yoy", "Import LCs into VN / IN, H1 FY2026 vs H1 FY2025 (count)",
        f"""SELECT sum(CASE WHEN {h1('txn_date', 2026)} THEN 1 ELSE 0 END)
                 / sum(CASE WHEN {h1('txn_date', 2025)} THEN 1 ELSE 0 END) - 1 AS actual {lcs}""", 0.35, 0.45)
    add(6, "sg_booking_share", "Share of those H1 FY2026 LCs booked in Singapore",
        f"""SELECT avg(CASE WHEN booking_location = 'SG' THEN 1.0 ELSE 0.0 END) AS actual {lcs} AND {h1('txn_date', 2026)}""",
        0.42, 0.48)
    tcg = (f"FROM {c}.bronze.crm_opportunity o JOIN {c}.bronze.crm_signal s ON s.signal_id = o.source_signal_id "
           f"WHERE s.signal_code = 'TRADE_CORRIDOR_GROWTH' AND s.corridor_destination IN ('VN', 'IN') "
           f"AND o.created_date >= '2026-04-01'")
    add(6, "corridor_opps_created", "Corridor-growth opportunities into VN / IN this fiscal year", f"SELECT count(*) AS actual {tcg}", 14)
    add(6, "corridor_opps_closed", "... closed", f"SELECT count(*) AS actual {tcg} AND o.stage IN ('Won', 'Lost')", 10)
    add(6, "corridor_opps_won", "... won", f"SELECT count(*) AS actual {tcg} AND o.stage = 'Won'", 6)

    # 8 Below-hurdle cluster: single-product JC lending relationships --------------------------
    add(8, "jc_single_product_below_hurdle", "JC lending-only relationships below the RoRWA hurdle (FY2026-Q2)",
        f"""SELECT count(*) AS actual FROM {c}.bronze.fin_relationship_pnl p
            JOIN {c}.ops.synthetic_truth_xref x ON x.source_id = p.obligor_id AND x.source_system = 'credit_obligor'
            WHERE p.fiscal_year = 2026 AND p.fiscal_quarter = 2 AND p.segment = 'Japanese Corporate'
            AND p.below_rorwa_hurdle AND x.entity_id NOT IN (SELECT entity_id FROM {c}.ops.synthetic_truth_xref
                WHERE source_system IN ('core_customer', 'tsy_counterparty', 'trade_party'))""", 36, 44)
    add(8, "ssf_raroc", "Sponsor & Structured Finance RAROC (FY2026-Q2, aggregate)",
        f"""SELECT sum(net_profit_annualised) / sum(economic_capital) AS actual FROM {c}.bronze.fin_relationship_pnl
            WHERE fiscal_year = 2026 AND fiscal_quarter = 2 AND segment = 'Sponsor & Structured Finance'""", 0.13, 0.15)

    # 10 Banksia ---------------------------------------------------------------------------------
    bk_crm = _src(c, "crm_account", "banksia", lead=False)
    add(10, "green_loan_opps", "Open green-loan opportunities",
        f"""SELECT count(*) AS actual FROM {c}.bronze.crm_opportunity WHERE crm_account_id IN {bk_crm}
            AND lower(product) LIKE '%green%' AND stage NOT IN ('Won', 'Lost')""", 2)
    add(10, "sll_signal", "SLL_ELIGIBLE signals",
        f"SELECT count(*) AS actual FROM {c}.bronze.crm_signal WHERE crm_account_id IN {bk_crm} AND signal_code = 'SLL_ELIGIBLE'", 1)
    return out
