"""Phase 3c-16 — shared lakehouses (simulated Delta Sharing from JP / EMEA / AMER).

  shared.share_jp_group_master              380 groups as mastered by Tokyo HO (JP parent, global tier / RM)
  shared.share_jp_parent_exposure_monthly   JP-booked committed / drawn / deposits / revenue x family x month
  shared.share_jp_parent_financials         JP parents' consolidated financials (JPY, March FYE), FY2022-FY2025
  shared.share_jp_parent_rating             Tokyo HO rating history of each JP parent
  shared.share_jp_support_letters           keepwell / parent-guarantee register for APAC subsidiaries
  shared.share_<emea|amer>_entity_master    ~600 EMEA / AMER subsidiaries of the client groups
  shared.share_<emea|amer>_exposure_monthly committed / drawn / deposits per entity x family x month
  shared.share_<emea|amer>_revenue_monthly  revenue per entity x family x month
  bronze.dq_share_refresh_log               daily 09:00 SGT freshness check per provider table

Pure Python (smbc_genie_lib.shared) over the truth universe plus two bronze inputs read here: the
credit facility terms (the register mirrors their parent-guarantee / keepwell flags) and each
group's APAC trailing-12M revenue (calibrates the regional wallets). Shared rows carry the D27
columns (source_region, ingest_method, _share_name, _provider_version, _shared_at). Re-runs overwrite.

  .venv/bin/python src/10_bronze_synth/run_bronze_shared.py --profile my-workspace
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from smbc_genie_lib import fragment, onboarding, profitability as prof, shared, storylines as S, truth  # noqa: E402
from smbc_genie_lib.config import load_config  # noqa: E402
from smbc_genie_lib.spark_io import get_spark, write_table  # noqa: E402

INGEST_TS = "2026-09-30T09:05:00"
BATCH = "P03C-20260930"
META = [("_source_system", "s"), ("_batch_id", "s"), ("_ingest_ts", "s")]
D27 = [("source_region", "s"), ("ingest_method", "s"), ("_share_name", "s"), ("_provider_version", "i"),
       ("_shared_at", "s")]
FIN_LINES = ["revenue", "operating_income", "ebitda", "net_income", "interest_expense", "total_assets",
             "total_debt", "cash", "net_debt", "equity"]


def _money(*cols):
    return [(f"{c}_{x}", "d") for c in cols for x in ("lcy", "usd")]


# column specs: s=STRING, i=INT, d=DOUBLE, b=BOOLEAN, dt=DATE (typed as the providers publish them)
ENTITY = [("provider_entity_id", "s"), ("legal_name", "s"), ("short_name", "s"), ("country_code", "s"),
          ("city", "s"), ("global_group_id", "s"), ("immediate_parent_name", "s"), ("immediate_parent_type", "s"),
          ("lei_like_id", "s"), ("industry_sector", "s"), ("regional_segment", "s"), ("booking_office", "s"),
          ("regional_rm_code", "s"), ("reporting_currency", "s"), ("relationship_start_date", "dt"),
          ("relationship_status", "s"), ("closed_date", "dt")]
EXPOSURE = [("provider_entity_id", "s"), ("global_group_id", "s"), ("month_end_date", "dt"), ("product_family", "s"),
            ("booking_office", "s"), ("currency", "s")] + _money("committed", "drawn", "deposits")
REVENUE = [("provider_entity_id", "s"), ("global_group_id", "s"), ("month_end_date", "dt"), ("product_family", "s"),
           ("revenue_type", "s"), ("currency", "s")] + _money("revenue")
SPECS = {
    "share_jp_group_master": [
        ("global_group_id", "s"), ("group_name_global", "s"), ("jp_parent_id", "s"), ("jp_parent_legal_name", "s"),
        ("jp_parent_lei", "s"), ("jp_parent_city", "s"), ("hq_country", "s"), ("hq_region", "s"),
        ("global_segment", "s"), ("global_industry", "s"), ("global_tier", "s"),
        ("global_relationship_owner_region", "s"), ("global_rm_code", "s"), ("global_rm_name", "s"),
        ("global_coverage_unit", "s"), ("relationship_since", "dt"), ("n_subsidiaries_emea", "i"),
        ("n_subsidiaries_amer", "i"), ("master_status", "s")],
    "share_jp_parent_exposure_monthly": [
        ("jp_counterparty_id", "s"), ("jp_counterparty_name", "s"), ("counterparty_type", "s"),
        ("global_group_id", "s"), ("month_end_date", "dt"), ("product_family", "s"), ("booking_office", "s"),
        ("currency", "s")] + _money("committed", "drawn", "deposits", "revenue"),
    "share_jp_parent_financials": [
        ("jp_parent_id", "s"), ("global_group_id", "s"), ("fiscal_year", "i"), ("fiscal_year_label", "s"),
        ("fiscal_year_end", "dt"), ("accounting_standard", "s"), ("currency", "s")]
        + [(f"{k}_jpy", "d") for k in FIN_LINES] + [(f"{k}_usd", "d") for k in FIN_LINES]
        + [("net_debt_to_ebitda", "d"), ("interest_coverage", "d"), ("equity_ratio", "d"), ("roe", "d"),
           ("operating_margin", "d"), ("n_consolidated_subsidiaries", "i"), ("audit_opinion", "s"),
           ("results_published_date", "dt"), ("ghg_scope12_kt", "d")],
    "share_jp_parent_rating": [
        ("rating_id", "s"), ("jp_parent_id", "s"), ("global_group_id", "s"), ("rating_date", "dt"),
        ("rating_action", "s"), ("review_type", "s"), ("grade_from", "i"), ("grade_to", "i"),
        ("rating_equivalent", "s"), ("outlook", "s"), ("rating_reason", "s"), ("next_review_date", "dt"),
        ("valid_to", "dt"), ("is_current", "b")],
    "share_jp_support_letters": [
        ("letter_id", "s"), ("jp_parent_id", "s"), ("global_group_id", "s"), ("support_type", "s"),
        ("is_legally_binding", "b"), ("coverage_scope", "s"), ("covered_facility_id", "s"),
        ("apac_obligor_ref", "s"), ("subsidiary_name", "s"), ("subsidiary_country", "s"), ("subsidiary_lei", "s"),
        ("beneficiary_booking_entity", "s"), ("parent_ownership_pct", "d"), ("support_amount_usd", "d"),
        ("currency", "s"), ("issue_date", "dt"), ("expiry_date", "dt"), ("last_confirmed_date", "dt"),
        ("status", "s"), ("status_date", "dt")],
    "share_emea_entity_master": ENTITY,
    "share_amer_entity_master": ENTITY + [("naics_code", "s")],
    "share_emea_exposure_monthly": EXPOSURE + [("ifrs9_stage", "i")],
    "share_amer_exposure_monthly": EXPOSURE,
    "share_emea_revenue_monthly": REVENUE,
    "share_amer_revenue_monthly": REVENUE,
}
LOG_SPEC = [("log_date", "s"), ("checked_at", "s"), ("provider_region", "s"), ("share_name", "s"),
            ("table_name", "s"), ("scheduled_refresh_at", "s"), ("refresh_status", "s"), ("refreshed_at", "s"),
            ("provider_version", "i"), ("lag_hours", "d"), ("is_stale", "b"), ("sla_hours", "i"),
            ("row_count", "i"), ("schema_version", "i"), ("schema_drift_flag", "b"), ("schema_change", "s"),
            ("error_message", "s")]
_SHARED = ("Simulated Delta Sharing from the {region} lakehouse (D27: source_region, ingest_method, _share_name, "
           "_provider_version, _shared_at in SGT). ")
COMMENTS = {
    "share_jp_group_master": "Tokyo HO global group master: one row per client group (global_group_id = group "
                             "master key), JP parent legal name, global tier, global RM, owner region.",
    "share_jp_parent_exposure_monthly": "JP-booked month-end committed / drawn / deposits and monthly revenue per "
                                        "JP parent (or JP-booked group entity) x product family; JPY + USD.",
    "share_jp_parent_financials": "JP parents' consolidated annual financials (JPY + USD, March FYE) with leverage, "
                                  "coverage and equity ratios; ghg_scope12_kt added Jun-2026 (not back-filled).",
    "share_jp_parent_rating": "Tokyo HO internal rating history of each JP parent (grade 1-10, equivalent, "
                              "outlook, action, reason); valid_to / is_current per action.",
    "share_jp_support_letters": "Keepwell (general, per subsidiary) and parent-guarantee (per facility) register: "
                                "JP parent -> APAC subsidiary obligor, status, expiry, last reconfirmation.",
    "share_emea_entity_master": "EMEA subsidiaries of the client groups (provider entity id, legal name, country, "
                                "regional holding / parent, regional RM, relationship dates).",
    "share_amer_entity_master": "Americas subsidiaries of the client groups (provider entity id, legal name, country, "
                                "regional holding / parent, regional RM); naics_code added Jun-2025 (not back-filled).",
    "share_emea_exposure_monthly": "EMEA month-end committed / drawn / deposits per subsidiary x product family "
                                   "(EUR/GBP/USD + USD); ifrs9_stage added Jan-2025 (not back-filled).",
    "share_amer_exposure_monthly": "Americas month-end committed / drawn / deposits per subsidiary x product family (USD).",
    "share_emea_revenue_monthly": "EMEA monthly revenue per subsidiary x product family (NII / fees / trading).",
    "share_amer_revenue_monthly": "Americas monthly revenue per subsidiary x product family (NII / fees / trading).",
}


def _df(spark, rows, spec):
    from pyspark.sql.types import (BooleanType, DateType, DoubleType, IntegerType, StringType,
                                   StructField, StructType)
    types = {"s": StringType(), "i": IntegerType(), "d": DoubleType(), "b": BooleanType(), "dt": DateType()}
    schema = StructType([StructField(c, types[t], True) for c, t in spec])
    return spark.createDataFrame([[r.get(c) for c, _ in spec] for r in rows], schema)


def _apac_revenue(spark, c: str, as_of: _dt.date):
    """Each group's APAC trailing-12M revenue (deposit NII, lending margin + fees, FX, trade fees)."""
    lo = (as_of.replace(day=1) - _dt.timedelta(days=330)).replace(day=1).isoformat()
    hi = as_of.isoformat()
    arr_bps = prof.ARRANGEMENT_FEE_BPS / prof.ARRANGEMENT_TENOR_YEARS
    rows = spark.sql(f"""
    WITH x AS (SELECT source_system, source_id, group_id FROM {c}.ops.synthetic_truth_xref),
    dep AS (SELECT x.group_id, sum(d.balance_usd * CASE WHEN d.deposit_class = 'CASA'
              THEN {prof.DEPOSIT_SPREAD_CASA} ELSE {prof.DEPOSIT_SPREAD_TD} END) / 12 AS rev
            FROM {c}.bronze.core_deposit_balance_monthly d
            JOIN x ON x.source_system = 'core_customer' AND x.source_id = d.cust_no
            WHERE d.balance_date BETWEEN DATE'{lo}' AND DATE'{hi}' GROUP BY x.group_id),
    lend AS (SELECT x.group_id, sum(b.drawn_usd * t.margin_bps / 1e4
               + greatest(b.limit_usd - b.drawn_usd, 0) * {prof.COMMITMENT_FEE_BPS} / 1e4
               + b.limit_usd * {arr_bps} / 1e4) / 12 AS rev
             FROM {c}.bronze.core_facility_balance_monthly b
             JOIN {c}.bronze.credit_facility_terms t ON t.facility_id = b.facility_id
             JOIN x ON x.source_system = 'credit_obligor' AND x.source_id = b.obligor_id
             WHERE b.balance_date BETWEEN DATE'{lo}' AND DATE'{hi}' GROUP BY x.group_id),
    fx AS (SELECT x.group_id, sum(f.revenue_usd) AS rev FROM {c}.bronze.tsy_fx_deal f
           JOIN x ON x.source_system = 'tsy_counterparty' AND x.source_id = f.cpty_id
           WHERE f.deal_date BETWEEN DATE'{lo}' AND DATE'{hi}' GROUP BY x.group_id),
    trd AS (SELECT x.group_id, sum(t.commission_usd) AS rev FROM {c}.bronze.trade_finance_txn t
            JOIN x ON x.source_system = 'trade_party' AND x.source_id = t.party_id
            WHERE t.txn_date BETWEEN DATE'{lo}' AND DATE'{hi}' GROUP BY x.group_id)
    SELECT g.group_id, coalesce(dep.rev, 0) + coalesce(lend.rev, 0) + coalesce(fx.rev, 0) + coalesce(trd.rev, 0) AS rev
    FROM {c}.ops.synthetic_truth_group g LEFT JOIN dep USING (group_id) LEFT JOIN lend USING (group_id)
      LEFT JOIN fx USING (group_id) LEFT JOIN trd USING (group_id)""").collect()
    return {r["group_id"]: float(r["rev"]) for r in rows}


def _facilities(spark, c: str, xref):
    """bronze.credit_facility_terms (+ closed_date when the lending step provides it) with entity_id."""
    cols = set(spark.table(f"{c}.bronze.credit_facility_terms").columns)
    closed = "CAST(closed_date AS DATE) AS closed_date" if "closed_date" in cols else "CAST(NULL AS DATE) AS closed_date"
    rows = spark.sql(f"""SELECT facility_id, obligor_id, facility_type, limit_usd, currency, guarantor_type,
        CAST(origination_date AS DATE) AS origination_date, CAST(maturity_date AS DATE) AS maturity_date, {closed}
        FROM {c}.bronze.credit_facility_terms""").collect()
    entity_of = {x["source_id"]: x["entity_id"] for x in xref if x["source_system"] == "credit_obligor"}
    return [{**r.asDict(), "entity_id": entity_of.get(r["obligor_id"])} for r in rows]


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 3c-16: shared lakehouses (simulated Delta Sharing)")
    ap.add_argument("--catalog", default=None)
    ap.add_argument("--profile", default=None)
    args = ap.parse_args()
    cfg = load_config(catalog=args.catalog) if args.catalog else load_config()
    c = cfg.catalog
    as_of = _dt.date.fromisoformat(cfg.as_of_date)
    spark = get_spark(args.profile)

    groups = truth.build_groups(cfg)
    entities = truth.build_entities(cfg, groups)
    _, xref = fragment.fragment_all(cfg, entities, [g["group_id"] for g in groups])
    cohort = onboarding.new_client_cohort(cfg, entities, xref)
    since = {e["entity_id"]: onboarding.customer_since(cfg, e, cohort) for e in entities}
    facs = _facilities(spark, c, xref)
    apac = _apac_revenue(spark, c, as_of)
    print(f"[p03c16] inputs: {len(facs):,} facility terms, APAC 12M revenue for {len(apac)} groups "
          f"(${sum(apac.values()) / 1e6:,.0f}m)")
    out = shared.build_shared(cfg, groups, entities, xref, facs, apac, since)

    for region, t in shared.TABLES:
        n = write_table(_df(spark, out[t], SPECS[t] + D27), f"{c}.shared.{t}",
                        comment=_SHARED.format(region=region) + COMMENTS[t])
        print(f"[p03c16] shared.{t:34} {n:>7,} rows")
    log = [{**r, "_source_system": "share_monitor", "_batch_id": BATCH, "_ingest_ts": INGEST_TS}
           for r in out["dq_share_refresh_log"]]
    n = write_table(_df(spark, log, LOG_SPEC + META), f"{c}.bronze.dq_share_refresh_log",
                    comment="Bronze Delta Sharing freshness log: one row per provider region x shared table x day "
                            "(09:00 SGT check): latest provider version, refreshed_at, lag hours, status, row count, "
                            "schema drift.")
    print(f"[p03c16] bronze.dq_share_refresh_log            {n:>7,} rows")
    _verify(spark, cfg, groups, entities, out, apac)
    print("[p03c16] done.")


def _verify(spark, cfg, groups, entities, out, apac) -> None:
    c = cfg.catalog
    q = lambda sql: spark.sql(sql).collect()  # noqa: E731
    one = lambda sql: q(sql)[0][0]  # noqa: E731
    ids = " UNION ALL ".join(f"SELECT DISTINCT global_group_id g FROM {c}.shared.{t}" for _, t in shared.TABLES)
    orphan = one(f"SELECT count(*) FROM ({ids}) s LEFT ANTI JOIN {c}.ops.synthetic_truth_group t ON t.group_id = s.g")
    no_parent = one(f"""SELECT count(*) FROM {c}.ops.synthetic_truth_group g JOIN {c}.shared.share_jp_group_master m
        ON m.global_group_id = g.group_id WHERE g.segment = 'Japanese Corporate' AND m.jp_parent_id IS NULL""")
    bad_fac = one(f"""SELECT count(*) FROM {c}.shared.share_jp_support_letters l LEFT ANTI JOIN
        {c}.bronze.credit_facility_terms f ON f.facility_id = l.covered_facility_id WHERE l.covered_facility_id IS NOT NULL""")
    bad_obl = one(f"""SELECT count(*) FROM {c}.shared.share_jp_support_letters l LEFT ANTI JOIN
        {c}.bronze.credit_obligor o ON o.obligor_id = l.apac_obligor_ref""")
    uncovered = one(f"""
        WITH x AS (SELECT source_id, group_id FROM {c}.ops.synthetic_truth_xref WHERE source_system = 'credit_obligor'),
        f AS (SELECT t.facility_id, t.obligor_id, t.guarantor_type FROM {c}.bronze.credit_facility_terms t
              JOIN x ON x.source_id = t.obligor_id JOIN {c}.shared.share_jp_group_master m
              ON m.global_group_id = x.group_id AND m.jp_parent_id IS NOT NULL
              WHERE t.guarantor_type IN ('Parent Guarantee', 'Keepwell'))
        SELECT count(*) FROM f WHERE NOT EXISTS (SELECT 1 FROM {c}.shared.share_jp_support_letters l
          WHERE (f.guarantor_type = 'Parent Guarantee' AND l.covered_facility_id = f.facility_id)
             OR (f.guarantor_type = 'Keepwell' AND l.support_type = 'Keepwell' AND l.apac_obligor_ref = f.obligor_id))""")
    print(f"[p03c16] integrity: group ids not in truth={orphan}, JC groups without JP parent={no_parent}, "
          f"letters -> unknown facility={bad_fac} / unknown obligor={bad_obl}, JP-parented PG/keepwell facilities "
          f"without a letter={uncovered}")
    ent = one(f"SELECT count(*) FROM {c}.shared.share_emea_entity_master") + \
        one(f"SELECT count(*) FROM {c}.shared.share_amer_entity_master")
    orphans = sum(one(f"""SELECT count(*) FROM {c}.shared.share_{r}_{k}_monthly x LEFT ANTI JOIN
        {c}.shared.share_{r}_entity_master e ON e.provider_entity_id = x.provider_entity_id""")
                  for r in ("emea", "amer") for k in ("exposure", "revenue"))
    print(f"[p03c16] EMEA+AMER subsidiaries={ent} (monthly rows with no master entity: {orphans})")

    # storyline 4: Tanaka keepwell + parent upgrade
    tanaka = S.lead_entity(entities, "tanaka")
    kw = q(f"""SELECT l.letter_id, l.subsidiary_name, l.status, l.issue_date, l.last_confirmed_date
        FROM {c}.shared.share_jp_support_letters l JOIN {c}.ops.synthetic_truth_xref x
        ON x.source_system = 'credit_obligor' AND x.source_id = l.apac_obligor_ref
        WHERE x.entity_id = '{tanaka['entity_id']}' AND l.support_type = 'Keepwell'""")
    print("[p03c16] Tanaka keepwell: " + "; ".join(f"{r['letter_id']} {r['subsidiary_name']} {r['status']} issued "
                                                   f"{r['issue_date']} reconfirmed {r['last_confirmed_date']}" for r in kw))
    rt = q(f"""SELECT rating_date, rating_action, grade_from, grade_to, outlook, _shared_at
        FROM {c}.shared.share_jp_parent_rating WHERE global_group_id = '{tanaka['group_id']}' ORDER BY rating_date""")
    print("[p03c16] Tanaka JP parent rating: " + ", ".join(
        f"{r['rating_date']} {r['rating_action']} {r['grade_from']}->{r['grade_to']} ({r['outlook']})" for r in rt))

    # storyline 13: JP parent-rating share stale 11-15 Aug 2026
    st = q(f"""SELECT log_date, lag_hours, refresh_status, provider_version FROM {c}.bronze.dq_share_refresh_log
        WHERE table_name = '{S.SHARE_STALE['table']}' AND is_stale ORDER BY log_date""")
    print("[p03c16] parent_rating stale days: " + ", ".join(f"{r['log_date']} ({r['lag_hours']}h, {r['refresh_status']}, "
                                                            f"v{r['provider_version']})" for r in st))
    a, b = (d.isoformat() for d in S.SHARE_STALE["gap"])
    in_gap = one(f"""SELECT count(*) FROM {c}.shared.{S.SHARE_STALE['table']}
        WHERE substr(_shared_at, 1, 10) BETWEEN '{a}' AND '{b}'""")
    held = q(f"""SELECT substr(_shared_at, 1, 10) d, count(*) n FROM {c}.shared.{S.SHARE_STALE['table']}
        WHERE rating_date BETWEEN date_sub(DATE'{a}', 1) AND DATE'{b}' GROUP BY 1""")
    other = q(f"""SELECT table_name, count(*) n FROM {c}.bronze.dq_share_refresh_log WHERE is_stale
        AND table_name <> '{S.SHARE_STALE['table']}' GROUP BY table_name ORDER BY table_name""")
    print(f"[p03c16] parent_rating rows shared during the gap: {in_gap}; rating actions dated "
          f"{S.SHARE_STALE['gap'][0] - _dt.timedelta(days=1)}..{b} published on: "
          + ", ".join(f"{r['d']} ({r['n']})" for r in held))
    print("[p03c16] other stale table-days (isolated EMEA/AMER failures): "
          + ", ".join(f"{r['table_name']}={r['n']}" for r in other))
    lag = q(f"""SELECT provider_region, round(avg(lag_hours), 1) avg_h, round(max(lag_hours), 1) max_h,
        sum(CASE WHEN refresh_status = 'Late' THEN 1 ELSE 0 END) late, sum(CAST(schema_drift_flag AS INT)) drift
        FROM {c}.bronze.dq_share_refresh_log GROUP BY provider_region ORDER BY provider_region""")
    print("[p03c16] lag by region: " + ", ".join(f"{r['provider_region']} avg {r['avg_h']}h max {r['max_h']}h "
                                                 f"late={r['late']} drift={r['drift']}" for r in lag))
    mism = 0
    for _, t in shared.TABLES:
        logged = one(f"""SELECT row_count FROM {c}.bronze.dq_share_refresh_log
            WHERE table_name = '{t}' AND log_date = '{cfg.as_of_date}'""")
        mism += logged != one(f"SELECT count(*) FROM {c}.shared.{t}")
    print(f"[p03c16] log row_count at as-of vs table counts: {len(shared.TABLES) - mism}/{len(shared.TABLES)} match")

    # global relationship: APAC share of group revenue (12M) and Hayashi by region
    share = shared.apac_share_by_group(apac, out, _dt.date.fromisoformat(cfg.as_of_date).replace(day=1))
    seg = {g["group_id"]: g["segment"] for g in groups}
    jc = sorted(v for gid, v in share.items() if seg[gid] == "Japanese Corporate")
    print(f"[p03c16] APAC share of 12M group revenue: JC median {jc[len(jc) // 2]:.0%}; groups below 20%: "
          f"{sum(v < 0.2 for v in share.values())} of {len(share)} with a non-APAC relationship")
    hay = next(g["group_id"] for g in groups if g["storyline_key"] == "hayashi")
    me = cfg.as_of_date
    parts = []
    for region, exp, rev in (("JP", "share_jp_parent_exposure_monthly", "share_jp_parent_exposure_monthly"),
                             ("EMEA", "share_emea_exposure_monthly", "share_emea_revenue_monthly"),
                             ("AMER", "share_amer_exposure_monthly", "share_amer_revenue_monthly")):
        e = q(f"""SELECT round(sum(committed_usd) / 1e6, 1) c, round(sum(deposits_usd) / 1e6, 1) d
            FROM {c}.shared.{exp} WHERE global_group_id = '{hay}' AND month_end_date = DATE'{me}'""")[0]
        r = one(f"""SELECT round(sum(revenue_usd) / 1e6, 2) FROM {c}.shared.{rev} WHERE global_group_id = '{hay}'
            AND month_end_date > add_months(DATE'{me}', -12)""")
        parts.append(f"{region} committed ${e['c']}m deposits ${e['d']}m revenue12M ${r}m")
    print(f"[p03c16] Hayashi global (APAC revenue12M ${apac.get(hay, 0) / 1e6:.2f}m): " + "; ".join(parts))



if __name__ == "__main__":
    main()
