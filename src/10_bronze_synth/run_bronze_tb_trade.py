"""Phase 3c-14 — transaction-banking & trade depth (+ storyline 5 HK CASA TDs, storyline 6 statistics).

  bronze.trade_event                          instrument lifecycle events (issue/amend/present/pay/expire)
  bronze.trade_presentation                   LC document presentations: discrepancies, turnaround, decision
  bronze.trade_scf_invoice                    SCF invoices per programme supplier (financed / paid at maturity)
  bronze.ext_trade_statistics                 vendor monthly trade flows, origin -> destination x HS chapter
  bronze.core_time_deposit                    TD placements (a chain per TD account + HK CASA placements)
  bronze.core_liquidity_structure             cash pools: type, header account, sweep frequency, fee
  bronze.core_liquidity_structure_participant participant accounts per structure (join / leave)
  bronze.pay_channel_usage                    monthly cust_no x channel: users, logins, payments, API calls
  bronze.fin_tb_fee                           monthly TB fees not derivable from payments / trade / FX
  ops.synthetic_channel_profile               truth: digital adoption + manual-instruction habit per client

Reads bronze.trade_finance_txn / scf_programme / scf_supplier (run 3c-8 run_bronze_trade.py first),
ops.synthetic_account, core_deposit_balance_monthly and pay_payment_message. Pure logic lives in
smbc_genie_lib.tb_trade; Spark joins TD principals to balances and aggregates payments into channel
usage and fees. Keyed by source ids (txn_id, party_id, cust_no, account_id). Re-runs overwrite.

  .venv/bin/python src/10_bronze_synth/run_bronze_tb_trade.py --profile my-workspace
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from smbc_genie_lib import fragment, health, rng, storylines, tb_trade, trade, truth  # noqa: E402
from smbc_genie_lib.config import load_config  # noqa: E402
from smbc_genie_lib.spark_io import get_spark, write_table  # noqa: E402

INGEST_TS = "2026-09-30T14:00:00"
BATCH = "P03C-20260930"
TAG = "[p03c14]"
META = [("_source_system", "s"), ("_batch_id", "s"), ("_ingest_ts", "s")]
# column specs: s=STRING (Python-built dates are ISO strings), i=INT, d=DOUBLE, b=BOOLEAN, dt=DATE
SPECS = {
    "trade_event": [("event_id", "s"), ("txn_id", "s"), ("event_seq", "i"), ("event_type", "s"),
                    ("event_date", "s"), ("event_detail", "s"), ("amount_usd", "d"),
                    ("outstanding_after_usd", "d"), ("presentation_id", "s")],
    "trade_presentation": [("presentation_id", "s"), ("txn_id", "s"), ("presentation_no", "i"),
                           ("is_re_presentation", "b"), ("received_ts", "s"), ("checked_ts", "s"),
                           ("turnaround_hours", "d"), ("sla_hours", "d"), ("is_over_sla", "b"),
                           ("is_discrepant", "b"), ("discrepancy_count", "i"), ("discrepancy_types", "s"),
                           ("primary_discrepancy_type", "s"), ("decision", "s"), ("amount_usd", "d"),
                           ("ops_team", "s")],
    "trade_scf_invoice": [("invoice_id", "s"), ("programme_id", "s"), ("supplier_id", "s"),
                          ("anchor_party_id", "s"), ("supplier_invoice_no", "s"), ("invoice_date", "s"),
                          ("approval_date", "s"), ("due_date", "s"), ("currency", "s"),
                          ("invoice_amount_usd", "d"), ("is_financed", "b"), ("financing_date", "s"),
                          ("financed_amount_usd", "d"), ("advance_rate", "d"), ("discount_rate_pct", "d"),
                          ("days_financed", "i"), ("discount_amount_usd", "d"), ("net_proceeds_usd", "d"),
                          ("status", "s")],
    "ext_trade_statistics": [("record_id", "s"), ("period_month", "s"), ("origin_country", "s"),
                             ("destination_country", "s"), ("flow_type", "s"), ("hs_chapter", "s"),
                             ("hs_description", "s"), ("trade_value_usd", "d"), ("data_status", "s"),
                             ("release_date", "s"), ("vendor", "s")],
    "core_liquidity_structure": [("structure_id", "s"), ("structure_name", "s"), ("structure_type", "s"),
                                 ("group_master_id", "s"), ("header_cust_no", "s"), ("header_account_id", "s"),
                                 ("header_country", "s"), ("pooling_currency", "s"), ("sweep_frequency", "s"),
                                 ("is_cross_border", "b"), ("interest_benefit_bps", "d"), ("monthly_fee_usd", "d"),
                                 ("start_date", "s"), ("end_date", "s"), ("status", "s")],
    "core_liquidity_structure_participant": [("structure_id", "s"), ("participant_account_id", "s"),
                                             ("participant_cust_no", "s"), ("participant_country", "s"),
                                             ("participant_currency", "s"), ("role", "s"), ("sweep_direction", "s"),
                                             ("target_balance_lcy", "d"), ("join_date", "s"), ("leave_date", "s")],
}
SOURCE = {"trade_event": "trade_finance_system", "trade_presentation": "trade_finance_system",
          "trade_scf_invoice": "scf_platform", "ext_trade_statistics": "trade_stats_vendor",
          "core_liquidity_structure": "core_banking", "core_liquidity_structure_participant": "core_banking"}
COMMENTS = {
    "trade_event": "Bronze trade instrument lifecycle events (issue/amend/present/accept/claim/pay/expire; "
                   "USD amount and outstanding after the event).",
    "trade_presentation": "Bronze LC document presentations (received/checked timestamps, turnaround vs SLA, "
                          "discrepancy types, waived/refused decision).",
    "trade_scf_invoice": "Bronze SCF invoices per programme supplier (approval, due date, financed flag, "
                         "financing date, days financed, discount rate and amount).",
    "ext_trade_statistics": "Bronze external trade statistics (fictional vendor): monthly USD flows by origin -> "
                            "destination country and HS chapter.",
    "core_liquidity_structure": "Bronze cash-pooling / sweep structures (type, header account, currency, sweep "
                                "frequency, interest benefit, fee, start/end).",
    "core_liquidity_structure_participant": "Bronze liquidity-structure participant accounts (role, country, "
                                            "currency, join/leave dates).",
}
TD_STAGE = [("placement_id", "s"), ("account_id", "s"), ("cust_no", "s"), ("currency", "s"),
            ("placement_date", "dt"), ("maturity_date", "dt"), ("tenor_months", "i"), ("interest_rate_pct", "d"),
            ("maturity_instruction", "s"), ("funding_source", "s"), ("funding_account_id", "s"),
            ("balance_month_end", "dt"), ("principal_usd", "d"), ("principal_lcy", "d")]
PROFILE_SPEC = [("cust_no", "s"), ("entity_id", "s"), ("billing_currency", "s"), ("portal_users", "i"),
                ("logins_per_user", "d"), ("api_from", "dt"), ("api_base_calls", "i"), ("h2h_from", "dt"),
                ("h2h_files", "i"), ("manual_share", "d"), ("fee_factor", "d")]


def _df(spark, rows, spec):
    from pyspark.sql.types import (BooleanType, DateType, DoubleType, IntegerType, StringType,
                                   StructField, StructType)
    types = {"s": StringType(), "i": IntegerType(), "d": DoubleType(), "b": BooleanType(), "dt": DateType()}
    schema = StructType([StructField(c, types[t], True) for c, t in spec])
    return spark.createDataFrame([[r.get(c) for c, _ in spec] for r in rows], schema)


def _retry(fn, *args, **kwargs):
    """Re-run an idempotent (overwrite / read) Spark call on transient Connect UNAVAILABLE errors."""
    for attempt in range(3):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            if "UNAVAILABLE" not in str(e) or attempt == 2:
                raise
            print(f"{TAG} transient Spark Connect error, retrying ({attempt + 1}/2)")
            time.sleep(20)


def _write(spark, c, name, rows):
    for r in rows:
        r.update(_source_system=SOURCE[name], _batch_id=BATCH, _ingest_ts=INGEST_TS)
    n = _retry(lambda: write_table(_df(spark, rows, SPECS[name] + META), f"{c}.bronze.{name}", comment=COMMENTS[name]))
    print(f"{TAG} bronze.{name:38} {n:>8,} rows")
    return n


def _q(spark, sql):
    return _retry(lambda: [r.asDict() for r in spark.sql(sql).collect()])


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 3c-14: transaction-banking & trade depth")
    ap.add_argument("--catalog", default=None)
    ap.add_argument("--profile", default=None)
    args = ap.parse_args()
    cfg = load_config(catalog=args.catalog) if args.catalog else load_config()
    c, seed = cfg.catalog, cfg.random_seed
    as_of = _dt.date.fromisoformat(cfg.as_of_date)
    spark = get_spark(args.profile)

    groups = truth.build_groups(cfg)
    entities = truth.build_entities(cfg, groups)
    _, xref = fragment.fragment_all(cfg, entities, [g["group_id"] for g in groups])

    def nondup(src):
        m = {}
        for x in xref:
            if x["source_system"] == src and not x["is_within_source_dup"]:
                m.setdefault(x["entity_id"], x["source_id"])
        return m

    party_entity = {v: k for k, v in nondup("trade_party").items()}
    core_custno = nondup("core_customer")
    hl = trade.health_lookup(health.build_health_monthly(cfg, entities))
    fx = trade.fx_lookup(cfg)

    # 1) trade lifecycle events + document presentations (from the landed instruments)
    txns = _q(spark, f"""SELECT txn_id, party_id, txn_date, product_type, maturity_date, amount_usd,
                           booking_location, status FROM {c}.bronze.trade_finance_txn""")
    dep = tb_trade.trade_depth(cfg, txns, party_entity, hl)
    _write(spark, c, "trade_event", dep["trade_event"])
    _write(spark, c, "trade_presentation", dep["trade_presentation"])
    print(f"{TAG} instruments whose lifecycle status differs from trade_finance_txn: {dep['status_mismatches']}")

    # 2) SCF invoices (reconcile to the landed programmes / suppliers)
    progs = _q(spark, f"SELECT * FROM {c}.bronze.scf_programme")
    sups = _q(spark, f"SELECT * FROM {c}.bronze.scf_supplier")
    _write(spark, c, "trade_scf_invoice", tb_trade.build_scf_invoices(cfg, progs, sups))

    # 3) external trade statistics
    _write(spark, c, "ext_trade_statistics", tb_trade.build_trade_statistics(cfg))

    # 4) time deposits: placement chains (principal = month-end balance) + HK CASA placements
    accts = _q(spark, f"""SELECT account_id, cust_no, entity_id, account_type, is_casa, currency, open_date,
                            active_from, base_balance_usd FROM {c}.ops.synthetic_account""")
    tds = tb_trade.td_schedule(cfg, accts)
    last_seq = {}
    for r in tds:
        last_seq[r["account_id"]] = max(last_seq.get(r["account_id"], 0), int(r["placement_id"][-3:]))
    hk = (tb_trade.hk_casa_placements(cfg, entities, accts, fx, last_seq)
          if storylines.on(cfg, storylines.HK_CASA) else [])
    _df(spark, tds + hk, TD_STAGE).createOrReplaceTempView("td_stage")
    td_sql = f"""
    SELECT s.placement_id, s.account_id, s.cust_no, s.currency, s.placement_date, s.maturity_date, s.tenor_months,
      round(coalesce(s.principal_lcy, b.balance_lcy), 2) AS principal_lcy,
      round(coalesce(s.principal_usd, b.balance_usd), 2) AS principal_usd, s.interest_rate_pct,
      round(coalesce(s.principal_lcy, b.balance_lcy) * s.interest_rate_pct / 100.0
            * datediff(s.maturity_date, s.placement_date) / 365.0, 2) AS interest_at_maturity_lcy,
      s.maturity_instruction, s.funding_source, s.funding_account_id,
      CASE WHEN s.maturity_date <= DATE'{as_of}' THEN 'Matured' ELSE 'Active' END AS status,
      'core_banking' AS _source_system, '{BATCH}' AS _batch_id, '{INGEST_TS}' AS _ingest_ts
    FROM td_stage s
    LEFT JOIN {c}.bronze.core_deposit_balance_monthly b
      ON b.account_id = s.account_id AND b.balance_date = s.balance_month_end
    """
    n = _retry(lambda: write_table(spark.sql(td_sql), f"{c}.bronze.core_time_deposit",
                    comment="Bronze time-deposit placements per TD / money-market account: placement and maturity "
                            "dates, tenor, rate, principal (LCY/USD = the account's month-end balance at placement), "
                            "maturity instruction, funding source (rollover / new funds / transfer from current account)."))
    print(f"{TAG} bronze.{'core_time_deposit':38} {n:>8,} rows ({len(hk)} HK CASA placements)")

    # 5) liquidity structures
    structures, parts = tb_trade.build_liquidity_structures(cfg, groups, entities, accts)
    _write(spark, c, "core_liquidity_structure", structures)
    _write(spark, c, "core_liquidity_structure_participant", parts)

    # 6) channel usage: payments hub aggregates + client channel profiles
    prof = tb_trade.channel_profile(cfg, entities, core_custno)
    n = _retry(lambda: write_table(_df(spark, prof, PROFILE_SPEC), f"{c}.ops.synthetic_channel_profile",
                    comment="Generation truth: per-client e-banking users, API / host-to-host adoption, "
                            "manual-instruction share (drives pay_channel_usage and fin_tb_fee)."))
    print(f"{TAG} ops.synthetic_channel_profile {n:,} rows")
    spark.table(f"{c}.ops.synthetic_channel_profile").createOrReplaceTempView("prof")
    start = tb_trade.CHANNEL_FROM.isoformat()

    def u(*cols):
        return rng.spark_unit_expr(seed, *cols)
    manual = ", ".join(f"'{ch}'" for ch in tb_trade.MANUAL_CHANNELS)
    usage_sql = f"""
    WITH months AS (SELECT date AS m FROM {c}.bronze.ref_calendar
                    WHERE day = 1 AND date BETWEEN DATE'{start}' AND DATE'{as_of}'),
    act AS (SELECT cust_no, min(active_from) AS af FROM {c}.ops.synthetic_account GROUP BY cust_no),
    cm AS (SELECT a.cust_no, m.m FROM act a JOIN months m ON last_day(m.m) >= a.af),
    pay AS (
      SELECT p.cust_no, trunc(p.payment_date, 'MM') AS m, p.channel, count(*) AS n, sum(p.amount_usd) AS v,
        sum(CASE WHEN p.stp_flag THEN 1 ELSE 0 END) AS n_stp,
        sum(CASE WHEN p.channel IN ({manual}) AND {u('p.payment_id', "'man'")} < pr.manual_share
                   * greatest(0.0, 1 - {tb_trade.MANUAL_DECLINE_PER_YEAR} * months_between(p.payment_date, DATE'{start}') / 12)
            THEN 1 ELSE 0 END) AS n_man
      FROM {c}.bronze.pay_payment_message p JOIN prof pr ON pr.cust_no = p.cust_no
      WHERE p.direction = 'Outbound' AND p.payment_date >= DATE'{start}'
      GROUP BY p.cust_no, trunc(p.payment_date, 'MM'), p.channel
    ),
    keys AS (
      SELECT cust_no, m, 'Portal' AS channel FROM cm
      UNION SELECT cust_no, m, channel FROM pay
      UNION SELECT cm.cust_no, cm.m, 'Payments API' FROM cm JOIN prof pr ON pr.cust_no = cm.cust_no
            WHERE pr.api_from IS NOT NULL AND cm.m >= trunc(pr.api_from, 'MM')
      UNION SELECT cm.cust_no, cm.m, 'Host-to-Host' FROM cm JOIN prof pr ON pr.cust_no = cm.cust_no
            WHERE pr.h2h_from IS NOT NULL AND cm.m >= trunc(pr.h2h_from, 'MM')
    ),
    base AS (
      SELECT k.m, k.cust_no, k.channel, pr.logins_per_user, pr.api_from, pr.api_base_calls, pr.h2h_from, pr.h2h_files,
        coalesce(p.n, 0) AS n, coalesce(p.v, 0.0) AS v, coalesce(p.n_stp, 0) AS n_stp, coalesce(p.n_man, 0) AS n_man,
        CASE WHEN k.channel = 'Portal'
             THEN greatest(1, CAST(round(pr.portal_users * (0.6 + 0.4 * {u('k.cust_no', 'k.m', "'usr'")})) AS INT))
             ELSE 0 END AS users
      FROM keys k JOIN prof pr ON pr.cust_no = k.cust_no
      LEFT JOIN pay p ON p.cust_no = k.cust_no AND p.m = k.m AND p.channel = k.channel
    )
    SELECT m AS usage_month, cust_no, channel, users AS active_users,
      CASE WHEN channel = 'Portal' THEN CAST(round(users * logins_per_user * (0.8 + 0.4 * {u('cust_no', 'm', "'log'")})
           * CASE WHEN month(m) IN (3, 6, 9, 12) THEN 1.15 ELSE 1.0 END) AS INT) ELSE 0 END AS logins,
      n AS payments_initiated, round(v, 2) AS payments_value_usd, n_stp AS stp_payments, n_man AS manual_instructions,
      n - n_man AS digital_payments,
      CASE WHEN channel = 'Payments API' THEN CAST(round(
             CASE WHEN api_from IS NOT NULL AND m >= trunc(api_from, 'MM')
                  THEN api_base_calls * (0.85 + 0.3 * {u('cust_no', 'm', "'api'")})
                       * least(1.0, (months_between(m, trunc(api_from, 'MM')) + 1) / 6.0) ELSE 0 END
             + n * (6 + 6 * {u('cust_no', 'm', "'apip'")})) AS INT) ELSE 0 END AS api_calls,
      CASE WHEN channel = 'Host-to-Host' THEN CAST(greatest(
             CASE WHEN h2h_from IS NOT NULL AND m >= trunc(h2h_from, 'MM')
                  THEN round(h2h_files * (0.8 + 0.4 * {u('cust_no', 'm', "'h2h'")})) ELSE 0 END,
             ceil(n / 5.0)) AS INT) ELSE 0 END AS files_transmitted,
      'payments_hub' AS _source_system, '{BATCH}' AS _batch_id, '{INGEST_TS}' AS _ingest_ts
    FROM base
    """
    n = _retry(lambda: write_table(spark.sql(usage_sql), f"{c}.bronze.pay_channel_usage",
                    comment="Bronze monthly channel usage per core customer x channel: e-banking users and logins, "
                            "payments initiated (outbound, = pay_payment_message), STP, manual (fax/paper) "
                            "instructions keyed by operations, API calls, host-to-host files."))
    print(f"{TAG} bronze.{'pay_channel_usage':38} {n:>8,} rows")

    # 7) TB fee lines not derivable from payments / trade / FX
    fee_sql = f"""
    WITH months AS (SELECT date AS m FROM {c}.bronze.ref_calendar
                    WHERE day = 1 AND date BETWEEN DATE'{start}' AND DATE'{as_of}'),
    acct AS (
      SELECT a.cust_no, m.m, sum(CASE a.account_type
               WHEN 'Current Account' THEN {tb_trade.ACCOUNT_FEE_USD['Current Account']}
               WHEN 'Savings Account' THEN {tb_trade.ACCOUNT_FEE_USD['Savings Account']} ELSE 0.0 END) AS usd
      FROM {c}.ops.synthetic_account a JOIN months m ON last_day(m.m) >= a.active_from
      WHERE a.is_casa GROUP BY a.cust_no, m.m
    ),
    lines AS (
      SELECT cust_no, m, 'Account Maintenance' AS fee_type, 'Cash' AS product_family, usd FROM acct
      UNION ALL SELECT cust_no, usage_month, 'Channel - Portal', 'Payments',
             greatest({tb_trade.PORTAL_MIN_FEE_USD}, {tb_trade.PORTAL_FEE_PER_USER_USD} * active_users)
        FROM {c}.bronze.pay_channel_usage WHERE channel = 'Portal'
      UNION ALL SELECT u.cust_no, u.usage_month, 'Channel - Host-to-Host', 'Payments', {tb_trade.H2H_FEE_USD}
        FROM {c}.bronze.pay_channel_usage u JOIN prof pr ON pr.cust_no = u.cust_no  -- subscribed connections
        WHERE u.channel = 'Host-to-Host' AND u.files_transmitted > 0 AND u.usage_month >= trunc(pr.h2h_from, 'MM')
      UNION ALL SELECT u.cust_no, u.usage_month, 'Channel - API', 'Payments',
             {tb_trade.API_FEE_USD} + {tb_trade.API_FEE_PER_1K_CALLS_USD} * u.api_calls / 1000.0
        FROM {c}.bronze.pay_channel_usage u JOIN prof pr ON pr.cust_no = u.cust_no  -- subscribed API access
        WHERE u.channel = 'Payments API' AND u.usage_month >= trunc(pr.api_from, 'MM')
      UNION ALL SELECT s.header_cust_no, m.m, 'Liquidity Structure', 'Liquidity',
             s.monthly_fee_usd / pr.fee_factor  -- the structure's fee is already the client's price
        FROM {c}.bronze.core_liquidity_structure s JOIN prof pr ON pr.cust_no = s.header_cust_no
        JOIN months m ON m.m >= trunc(CAST(s.start_date AS DATE), 'MM')
         AND (s.end_date IS NULL OR m.m <= CAST(s.end_date AS DATE))
    ),
    l AS (SELECT cust_no, m, fee_type, product_family, sum(usd) AS usd FROM lines GROUP BY ALL)
    SELECT concat(l.cust_no, '-', date_format(l.m, 'yyyyMM'), '-', CASE l.fee_type
             WHEN 'Account Maintenance' THEN 'AMF' WHEN 'Channel - Portal' THEN 'CHP'
             WHEN 'Channel - Host-to-Host' THEN 'CHH' WHEN 'Channel - API' THEN 'CHA' ELSE 'LQS' END) AS fee_id,
      l.cust_no, l.m AS fee_month, l.fee_type, l.product_family, pr.billing_currency AS fee_currency,
      round(l.usd * pr.fee_factor * fx.rate_per_usd, 2) AS fee_amount_lcy, round(l.usd * pr.fee_factor, 2) AS fee_amount_usd,
      'finance_engine' AS _source_system, '{BATCH}' AS _batch_id, '{INGEST_TS}' AS _ingest_ts
    FROM l JOIN prof pr ON pr.cust_no = l.cust_no
    JOIN {c}.bronze.fx_rate_daily fx ON fx.date = last_day(l.m) AND fx.currency_code = pr.billing_currency
    """
    n = _retry(lambda: write_table(spark.sql(fee_sql), f"{c}.bronze.fin_tb_fee",
                    comment="Bronze monthly transaction-banking fees per core customer x fee type not derivable from "
                            "payments / trade / FX: account maintenance, channel (portal, host-to-host, API), "
                            "liquidity structure. LCY = client's local billing currency."))
    print(f"{TAG} bronze.{'fin_tb_fee':38} {n:>8,} rows")
    _verify(spark, cfg, c)
    print(f"{TAG} done.")


def _verify(spark, cfg, c) -> None:
    """Row-level integrity and storyline numbers, from the written bronze tables."""
    as_of = cfg.as_of_date
    b = f"{c}.bronze"
    r = _q(spark, f"""SELECT
        (SELECT count(*) FROM {b}.trade_event e LEFT ANTI JOIN {b}.trade_finance_txn t USING (txn_id)) ev_orphans,
        (SELECT count(*) FROM {b}.trade_presentation p LEFT ANTI JOIN {b}.trade_finance_txn t USING (txn_id)) pr_orphans,
        (SELECT count(*) FROM {b}.trade_scf_invoice i LEFT ANTI JOIN {b}.scf_supplier s USING (supplier_id)) inv_orphans,
        (SELECT count(*) FROM {b}.core_time_deposit d LEFT ANTI JOIN {b}.core_account a USING (account_id)) td_orphans,
        (SELECT count(*) FROM {b}.core_time_deposit WHERE principal_usd IS NULL) td_no_principal,
        (SELECT count(*) FROM {b}.core_liquidity_structure_participant p LEFT ANTI JOIN {b}.core_account a
           ON a.account_id = p.participant_account_id) lq_orphans,
        (SELECT count(*) FROM {b}.pay_channel_usage u LEFT ANTI JOIN {b}.core_customer k USING (cust_no)) ch_orphans""")[0]
    print(f"{TAG} orphans: " + ", ".join(f"{k}={v}" for k, v in r.items()))
    o = _q(spark, f"""WITH last AS (SELECT txn_id, max_by(outstanding_after_usd, event_seq) o FROM {b}.trade_event GROUP BY txn_id)
        SELECT round(sum(t.outstanding_usd)/1e9, 3) txn_bn, round(sum(l.o)/1e9, 3) ev_bn,
          sum(CASE WHEN abs(t.outstanding_usd - l.o) > 0.01 THEN 1 ELSE 0 END) diff
        FROM {b}.trade_finance_txn t JOIN last l USING (txn_id)""")[0]
    print(f"{TAG} as-of outstanding: instruments ${o['txn_bn']}bn = events ${o['ev_bn']}bn ({o['diff']} differ)")
    d = _q(spark, f"""SELECT t.booking_location bc, count(*) n, round(avg(CAST(p.is_discrepant AS INT)), 3) disc,
        round(percentile_approx(p.turnaround_hours, 0.5), 1) med_h, round(avg(CAST(p.is_over_sla AS INT)), 3) over_sla
      FROM {b}.trade_presentation p JOIN {b}.trade_finance_txn t USING (txn_id)
      GROUP BY 1 ORDER BY n DESC LIMIT 6""")
    print(f"{TAG} presentations by booking country: " + "; ".join(
        f"{x['bc']} n={x['n']} disc={x['disc']:.0%} median={x['med_h']}h overSLA={x['over_sla']:.0%}" for x in d))
    s = _q(spark, f"""WITH o AS (SELECT programme_id, sum(financed_amount_usd) out FROM {b}.trade_scf_invoice
          WHERE is_financed AND financing_date <= '{as_of}' AND due_date > '{as_of}' GROUP BY programme_id)
        SELECT p.programme_id, p.utilisation, round(o.out / p.limit_usd, 4) inv_util FROM {b}.scf_programme p
        LEFT JOIN o USING (programme_id) ORDER BY 1""")
    print(f"{TAG} SCF utilisation (programme vs invoices at as-of): " + ", ".join(
        f"{x['programme_id']} {x['utilisation']:.1%}/{x['inv_util']:.1%}" for x in s))
    v = storylines.VN_IN
    m = _q(spark, f"""SELECT
        sum(CASE WHEN period_month BETWEEN '2026-04-01' AND '2026-09-30' THEN trade_value_usd END)
          / sum(CASE WHEN period_month BETWEEN '2025-04-01' AND '2025-09-30' THEN trade_value_usd END) - 1 yoy
      FROM {b}.ext_trade_statistics WHERE destination_country IN {tuple(v['import_countries'])}
        AND origin_country IN {tuple(v['source_countries'])} AND hs_chapter IN ('85', '87')""")[0]
    sh = _q(spark, f"""WITH smbc AS (SELECT sum(amount_usd) a FROM {b}.trade_finance_txn
          WHERE origin_country = 'CN' AND destination_country = 'VN' AND hs_chapter = '85'
            AND txn_date BETWEEN '2025-04-01' AND '2026-03-31'),
        mkt AS (SELECT sum(trade_value_usd) a FROM {b}.ext_trade_statistics WHERE origin_country = 'CN'
            AND destination_country = 'VN' AND hs_chapter = '85' AND period_month BETWEEN '2025-04-01' AND '2026-03-31')
        SELECT smbc.a / mkt.a share, mkt.a / 1e9 mkt_bn FROM smbc, mkt""")[0]
    print(f"{TAG} ext statistics: HS 85/87 into VN/IN from CN/KR/JP H1 FY2026 vs H1 FY2025 {m['yoy']:+.1%}; "
          f"SMBC share of CN->VN electronics FY2025 {sh['share']:.2%} of ${sh['mkt_bn']:.1f}bn")
    t = _q(spark, f"""SELECT count(*) n, sum(CASE WHEN status = 'Active' THEN 1 ELSE 0 END) act,
        round(sum(CASE WHEN status = 'Active' AND maturity_date <= date_add(DATE'{as_of}', 90) THEN principal_usd END)/1e9, 2) m90,
        round(sum(CASE WHEN funding_source = 'Transfer from Current Account' THEN principal_usd END)/1e6, 1) hk_m
      FROM {b}.core_time_deposit""")[0]
    print(f"{TAG} TDs: {t['n']:,} placements, {t['act']:,} active, ${t['m90']}bn maturing in the next 90 days; "
          f"HK CASA transfers ${t['hk_m']}m")
    lq = _q(spark, f"""WITH g AS (SELECT e.group_id, count(DISTINCT e.booking_country) ncc
          FROM {c}.ops.synthetic_account a JOIN {c}.ops.synthetic_truth_entity e USING (entity_id) GROUP BY 1),
        pooled AS (SELECT DISTINCT group_master_id FROM {b}.core_liquidity_structure WHERE status = 'Active')
        SELECT count(*) g3, sum(CASE WHEN p.group_master_id IS NULL THEN 1 ELSE 0 END) unpooled
        FROM g LEFT JOIN pooled p ON p.group_master_id = g.group_id WHERE g.ncc >= 3""")[0]
    print(f"{TAG} liquidity: {lq['g3']} groups with accounts in >= 3 countries, {lq['unpooled']} without an active pool")
    ch = _q(spark, f"""SELECT (SELECT sum(payments_initiated) FROM {b}.pay_channel_usage) usage_n,
        (SELECT count(*) FROM {b}.pay_payment_message WHERE direction = 'Outbound'
           AND payment_date >= '{tb_trade.CHANNEL_FROM}') pay_n,
        (SELECT round(sum(manual_instructions) / sum(payments_initiated), 4) FROM {b}.pay_channel_usage
           WHERE usage_month >= '2026-04-01') manual_fy26""")[0]
    print(f"{TAG} channel usage: payments initiated {ch['usage_n']:,} = outbound payments {ch['pay_n']:,}; "
          f"manual-instruction share FY2026 {ch['manual_fy26']:.1%}")
    f = _q(spark, f"""SELECT fee_type, round(sum(fee_amount_usd)/1e6, 2) m FROM {b}.fin_tb_fee
        WHERE fee_month BETWEEN '2025-04-01' AND '2026-03-01' GROUP BY 1 ORDER BY 2 DESC""")
    print(f"{TAG} FY2025 TB fees $m: " + ", ".join(f"{x['fee_type']}={x['m']}" for x in f))


if __name__ == "__main__":
    main()
