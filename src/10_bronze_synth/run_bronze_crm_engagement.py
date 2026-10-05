"""Phase 3c-13 — CRM engagement & opportunity signals (+ storylines 2, 5, 6, 10).

  bronze.crm_contact                  ~6,000 fictional client contacts (fixed)
  bronze.crm_account_team_history     coverage team by role with valid_from / valid_to (RM changes)
  bronze.crm_activity                 calls / meetings / emails / visits: note text, tone, next action
  bronze.crm_signal                   opportunity signals from real behaviour + the RM's action
  bronze.crm_account_plan_initiative  initiatives per account plan
  bronze.crm_wallet_estimate          group x FY x product family wallet and share of wallet
  bronze.crm_nbp_score                monthly next-best-product propensity, last 6 months
  bronze.crm_opportunity              pipeline incl. source_signal_id and the storyline deals
  bronze.crm_account_plan             group plans by product family incl. revised_target_usd /
                                      mid_year_revision
  bronze.crm_next_best_product        latest-month top-3 picks of crm_nbp_score
  ops.synthetic_crm_account           truth: CRM account -> entity, tier, neglect / blind-spot flags

The last three replace the Phase 3c-10 outputs (run_bronze_crm.py), whose pipeline, plans and NBP
are rebuilt here because they link to the signals: run this after run_bronze_crm.py. Spark collects
the behaviour aggregates (crm_engagement.load_features: payments, deposits, facilities, FX, trade,
SCF, ext_news, health); the rest is pure Python (smbc_genie_lib.crm_engagement). Keyed by
crm_account_id (plans / wallet also carry the group-master id). Re-runs overwrite.

  .venv/bin/python src/10_bronze_synth/run_bronze_crm_engagement.py --profile my-workspace
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from smbc_genie_lib import crm_engagement as ce  # noqa: E402
from smbc_genie_lib import fragment, truth  # noqa: E402
from smbc_genie_lib.config import load_config  # noqa: E402
from smbc_genie_lib.spark_io import get_spark, write_table  # noqa: E402

INGEST_TS = "2026-09-30T12:30:00"
BATCH = "P03C-20260930"
META = [("_source_system", "s"), ("_batch_id", "s"), ("_ingest_ts", "s")]
# column specs: s=STRING (dates are ISO strings, as landed), i=INT, d=DOUBLE, b=BOOLEAN, dt=DATE
SPECS = {
    "crm_contact": [("contact_id", "s"), ("crm_account_id", "s"), ("first_name", "s"), ("last_name", "s"),
                    ("full_name", "s"), ("job_title", "s"), ("function", "s"), ("contact_role", "s"),
                    ("seniority", "s"), ("is_primary", "b"), ("email", "s"), ("preferred_channel", "s"),
                    ("relationship_strength", "s"), ("created_date", "s"), ("is_active", "b"),
                    ("inactive_since", "s")],
    "crm_account_team_history": [("assignment_id", "s"), ("crm_account_id", "s"), ("team_role", "s"),
                                 ("employee_id", "s"), ("rm_code", "s"), ("member_name", "s"),
                                 ("member_office", "s"), ("is_primary", "b"), ("valid_from", "s"),
                                 ("valid_to", "s"), ("is_current", "b"), ("change_reason", "s")],
    "crm_activity": [("activity_id", "s"), ("crm_account_id", "s"), ("activity_date", "s"), ("activity_type", "s"),
                     ("rm_code", "s"), ("contact_id", "s"), ("purpose", "s"), ("subject", "s"),
                     ("product_discussed", "s"), ("product_family", "s"), ("note_text", "s"), ("raw_tone", "s"),
                     ("sentiment_score", "d"), ("action_required", "b"), ("next_action", "s"),
                     ("next_action_due_date", "s"), ("next_action_status", "s"), ("related_signal_id", "s"),
                     ("related_opportunity_id", "s"), ("duration_minutes", "i")],
    "crm_signal": [("signal_id", "s"), ("crm_account_id", "s"), ("signal_code", "s"), ("signal_name", "s"),
                   ("signal_category", "s"), ("polarity", "s"), ("source_quadrant", "s"), ("detected_date", "s"),
                   ("strength", "d"), ("confidence", "d"), ("estimated_revenue_usd", "d"),
                   ("observed_amount_usd", "d"), ("metric_value", "d"), ("currency_pair", "s"),
                   ("corridor_origin", "s"), ("corridor_destination", "s"), ("related_ref", "s"),
                   ("recommended_product", "s"), ("signal_detail", "s"), ("owner_rm", "s"), ("status", "s"),
                   ("status_date", "s"), ("actioned_date", "s"), ("actioned_by", "s"), ("dismissed_reason", "s"),
                   ("linked_opportunity_id", "s"), ("detection_engine", "s")],
    "crm_account_plan_initiative": [("initiative_id", "s"), ("plan_id", "s"), ("crm_account_id", "s"),
                                    ("group_ref", "s"), ("fiscal_year", "i"), ("product_family", "s"),
                                    ("initiative_name", "s"), ("description", "s"), ("target_revenue_usd", "d"),
                                    ("status", "s"), ("due_date", "s"), ("completed_date", "s"),
                                    ("owner_employee_id", "s"), ("owner_rm", "s"), ("priority", "s"),
                                    ("linked_opportunity_id", "s"), ("created_date", "s")],
    "crm_wallet_estimate": [("wallet_id", "s"), ("group_ref", "s"), ("crm_account_id", "s"), ("fiscal_year", "i"),
                            ("fiscal_year_label", "s"), ("product_family", "s"), ("estimated_wallet_usd", "d"),
                            ("smbc_revenue_usd", "d"), ("smbc_revenue_basis", "s"), ("share_of_wallet", "d"),
                            ("top_competitor_bank", "s"), ("top_competitor_share", "d"),
                            ("estimation_method", "s"), ("estimate_date", "s")],
    "crm_nbp_score": [("crm_account_id", "s"), ("score_month", "s"), ("product", "s"), ("product_family", "s"),
                      ("propensity", "d"), ("rank", "i"), ("top_drivers", "s"), ("expected_revenue_usd", "d"),
                      ("model_version", "s"), ("owner_rm", "s")],
    "crm_opportunity": [("opportunity_id", "s"), ("crm_account_id", "s"), ("owner_rm", "s"), ("product", "s"),
                        ("product_family", "s"), ("stage", "s"), ("status", "s"), ("win_probability", "d"),
                        ("amount_usd", "d"), ("expected_revenue_usd", "d"), ("created_date", "s"),
                        ("expected_close_date", "s"), ("actual_close_date", "s"), ("lost_reason", "s"),
                        ("source_type", "s"), ("source_signal_id", "s"), ("segment", "s"),
                        ("relationship_tier", "s"), ("booking_country", "s")],
    "crm_account_plan": [("plan_line_id", "s"), ("plan_id", "s"), ("crm_account_id", "s"), ("group_ref", "s"),
                         ("fiscal_year", "i"), ("fiscal_year_label", "s"), ("product_family", "s"),
                         ("prior_year_actual_usd", "d"), ("plan_optimism", "d"), ("planned_revenue_usd", "d"),
                         ("revised_target_usd", "d"), ("mid_year_revision", "b"), ("revision_date", "s"),
                         ("revision_reason", "s"), ("actual_revenue_usd", "d"), ("plan_status", "s"),
                         ("approved_date", "s"), ("owner_rm", "s"), ("strategic_priority", "s"),
                         ("wallet_share_target", "d"), ("plan_objective", "s")],
    "crm_next_best_product": [("crm_account_id", "s"), ("owner_rm", "s"), ("rank", "i"),
                              ("recommended_product", "s"), ("product_family", "s"), ("rationale", "s"),
                              ("propensity_score", "d"), ("expected_revenue_usd", "d"), ("segment", "s"),
                              ("score_month", "s"), ("model_version", "s")],
}
TRUTH_SPEC = [("crm_account_id", "s"), ("entity_id", "s"), ("group_id", "s"), ("relationship_tier", "s"),
              ("coverage_start", "dt"), ("is_neglected", "b"), ("last_contact_date", "dt"),
              ("is_blind_spot_group", "b")]
COMMENTS = {
    "crm_contact": "Bronze CRM client contacts (fictional names, title, function, primary flag, synthetic email).",
    "crm_account_team_history": "Bronze CRM coverage team by role (primary/secondary RM, credit analyst, TB sales) "
                                "with validity.",
    "crm_activity": "Bronze RM activities (call/meeting/email/visit): purpose, product, note text <= 40 words, tone, "
                    "next action.",
    "crm_signal": "Bronze opportunity signals derived from client behaviour/news, with RM action, status and linked "
                  "opportunity.",
    "crm_account_plan_initiative": "Bronze account-plan initiatives (product family, target revenue, status, due "
                                   "date, owner).",
    "crm_wallet_estimate": "Bronze group x FY x product-family wallet estimate, SMBC revenue and share of wallet.",
    "crm_nbp_score": "Bronze monthly next-best-product propensity per CRM client x product (last 6 months, top "
                     "products).",
    "crm_opportunity": "Bronze CRM pipeline (stage, amount, expected revenue, close dates, outcome, source signal).",
    "crm_account_plan": "Bronze group account plans by product family: target on prior-year actual, mid-year revision.",
    "crm_next_best_product": "Bronze next-best-product top-3 picks per CRM client (latest month of crm_nbp_score).",
}
TARGETS = {"crm_contact": "6,000 fixed", "crm_activity": f"{ce.ACTIVITIES_AT_SCALE_1:,} x SCALE",
           "crm_signal": f"{ce.SIGNALS_AT_SCALE_1:,} x SCALE", "crm_nbp_score": "~180k x SCALE",
           "crm_account_plan_initiative": f"{ce.INITIATIVES_AT_SCALE_1:,} x SCALE"}
KEYS = {"crm_contact": "contact_id", "crm_account_team_history": "assignment_id", "crm_activity": "activity_id",
        "crm_signal": "signal_id", "crm_account_plan_initiative": "initiative_id", "crm_wallet_estimate": "wallet_id",
        "crm_nbp_score": "concat(crm_account_id, score_month, product)", "crm_opportunity": "opportunity_id",
        "crm_account_plan": "plan_line_id", "crm_next_best_product": "concat(crm_account_id, rank)"}


def _df(spark, rows, spec):
    from pyspark.sql.types import (BooleanType, DateType, DoubleType, IntegerType, StringType,
                                   StructField, StructType)
    types = {"s": StringType(), "i": IntegerType(), "d": DoubleType(), "b": BooleanType(), "dt": DateType()}
    schema = StructType([StructField(c, types[t], True) for c, t in spec])
    return spark.createDataFrame([[r.get(c) for c, _ in spec] for r in rows], schema)


def _verify(spark, c: str, out) -> None:
    b = f"{c}.bronze"
    for t, key in KEYS.items():
        r = spark.sql(f"SELECT count(*) n, count(DISTINCT {key}) k, count(*) - count({key}) nulls "
                      f"FROM {b}.{t}").collect()[0]
        print(f"[p03c13] {t:28} {r['n']:>7,} rows  dup keys={r['n'] - r['k']}  null keys={r['nulls']}"
              + (f"  (target {TARGETS[t]})" if t in TARGETS else ""))
    ri = {
        "unknown crm_account_id": " + ".join(
            f"(SELECT count(*) FROM {b}.{t} x LEFT ANTI JOIN {b}.crm_account a ON a.account_id = x.crm_account_id"
            f" WHERE x.crm_account_id IS NOT NULL)" for t in KEYS),
        "opportunity.source_signal_id not in crm_signal": f"""SELECT count(*) FROM {b}.crm_opportunity o
            LEFT ANTI JOIN {b}.crm_signal s ON s.signal_id = o.source_signal_id WHERE o.source_signal_id IS NOT NULL""",
        "signal.linked_opportunity_id not in crm_opportunity": f"""SELECT count(*) FROM {b}.crm_signal s
            LEFT ANTI JOIN {b}.crm_opportunity o ON o.opportunity_id = s.linked_opportunity_id
            WHERE s.linked_opportunity_id IS NOT NULL""",
        "activity.contact_id not in crm_contact": f"""SELECT count(*) FROM {b}.crm_activity a
            LEFT ANTI JOIN {b}.crm_contact k ON k.contact_id = a.contact_id WHERE a.contact_id IS NOT NULL""",
        "initiative.plan_id not in crm_account_plan": f"""SELECT count(*) FROM {b}.crm_account_plan_initiative i
            LEFT ANTI JOIN {b}.crm_account_plan p ON p.plan_id = i.plan_id""",
        "team RM not a staff RM": f"""SELECT count(*) FROM {b}.crm_account_team_history t
            LEFT ANTI JOIN {c}.ops.synthetic_truth_person p ON p.employee_id = t.employee_id""",
        "facility signals not in credit_facility_terms": f"""SELECT count(*) FROM {b}.crm_signal s
            LEFT ANTI JOIN {b}.credit_facility_terms f ON f.facility_id = s.related_ref
            WHERE s.signal_code = 'FACILITY_MATURING_12M'""",
    }
    bad = {k: spark.sql(f"SELECT {q}" if k == "unknown crm_account_id" else q).collect()[0][0] for k, q in ri.items()}
    print("[p03c13] referential integrity (0 = clean): " + ", ".join(f"{k}={v}" for k, v in bad.items()))
    rows = spark.sql(f"""SELECT signal_code, count(*) n,
        sum(CASE WHEN status IN ('New','Actioned') THEN 1 ELSE 0 END) open,
        round(avg(CASE WHEN linked_opportunity_id IS NOT NULL THEN 1.0 ELSE 0 END), 3) conv
      FROM {b}.crm_signal GROUP BY signal_code ORDER BY n DESC""").collect()
    print("[p03c13] signals (n/open/converted%): "
          + ", ".join(f"{r['signal_code']}={r['n']}/{r['open']}/{r['conv']:.0%}" for r in rows))
    rows = spark.sql(f"""SELECT coalesce(s.signal_code, o.source_type) src,
        sum(CASE WHEN o.status = 'Won' THEN 1 ELSE 0 END) won,
        sum(CASE WHEN o.status IN ('Won','Lost') THEN 1 ELSE 0 END) closed
      FROM {b}.crm_opportunity o LEFT JOIN {b}.crm_signal s ON s.signal_id = o.source_signal_id
      GROUP BY 1 ORDER BY closed DESC""").collect()
    print("[p03c13] win rate by source: " + ", ".join(f"{r['src']}={r['won']}/{r['closed']}" for r in rows))
    r = spark.sql(f"""WITH last AS (SELECT crm_account_id, max(activity_date) last_dt FROM {b}.crm_activity GROUP BY 1)
      SELECT t.relationship_tier, count(*) accts,
        sum(CASE WHEN coalesce(l.last_dt, '') < '2026-07-02' THEN 1 ELSE 0 END) stale
      FROM {c}.ops.synthetic_crm_account t LEFT JOIN last l USING (crm_account_id) GROUP BY 1 ORDER BY 1""").collect()
    print("[p03c13] CRM accounts with no RM contact in the 90 days to as-of: "
          + ", ".join(f"{x['relationship_tier']}={x['stale']}/{x['accts']}" for x in r))
    tone = spark.sql(f"SELECT raw_tone, count(*) n FROM {b}.crm_activity GROUP BY 1 ORDER BY 1").collect()
    print("[p03c13] activity tone: " + ", ".join(f"{x['raw_tone']}={x['n']}" for x in tone))
    _verify_storylines(spark, c)


def _verify_storylines(spark, c: str) -> None:
    b = f"{c}.bronze"
    acct = f"""(SELECT x.source_id crm_account_id, e.storyline_key, e.is_group_lead, e.booking_country
               FROM {c}.ops.synthetic_truth_xref x JOIN {c}.ops.synthetic_truth_entity e ON e.entity_id = x.entity_id
               WHERE x.source_system = 'crm_account' AND NOT x.is_within_source_dup)"""
    q = {
        "kinokawa signals": f"""SELECT concat_ws(',', sort_array(collect_list(s.signal_code))) FROM {b}.crm_signal s
            JOIN {acct} a USING (crm_account_id) WHERE a.storyline_key = 'kinokawa' AND s.signal_code IN
            ('TRADE_CORRIDOR_GROWTH','SCF_ANCHOR_CANDIDATE','FX_FLOW_VIA_OTHER_BANK','LOAN_SERVICE_TO_OTHER_BANK')""",
        "kinokawa SCF opp (created, USD m, source)": f"""SELECT concat_ws(';', collect_list(concat(o.created_date, ' ',
            round(o.amount_usd / 1e6, 1), ' ', s.signal_code)))
            FROM {b}.crm_opportunity o JOIN {acct} a USING (crm_account_id)
            JOIN {b}.crm_signal s ON s.signal_id = o.source_signal_id
            WHERE a.storyline_key = 'kinokawa' AND o.product = 'Supply Chain Finance'""",
        "kinokawa NBP rank 1 (lead)": f"""SELECT first(n.recommended_product) FROM {b}.crm_next_best_product n
            JOIN {acct} a USING (crm_account_id)
            WHERE a.storyline_key = 'kinokawa' AND a.is_group_lead AND n.rank = 1""",
        "kinokawa FY2026 plan revised": f"""SELECT concat(bool_and(p.mid_year_revision), ' on ', max(p.revision_date))
            FROM {b}.crm_account_plan p JOIN {acct} a USING (crm_account_id)
            WHERE a.storyline_key = 'kinokawa' AND p.fiscal_year = 2026""",
        "VN/IN corridor opps created/closed/won (all TCG-sourced)": f"""SELECT concat(count(*), '/',
            sum(CASE WHEN o.status IN ('Won','Lost') THEN 1 ELSE 0 END), '/',
            sum(CASE WHEN o.status = 'Won' THEN 1 ELSE 0 END), ' (into VN/IN in FY2026: ',
            sum(CASE WHEN s.corridor_destination IN ('VN','IN') AND o.created_date >= '2026-04-01' THEN 1 ELSE 0 END),
            ')') FROM {b}.crm_opportunity o JOIN {b}.crm_signal s ON s.signal_id = o.source_signal_id
            WHERE s.signal_code = 'TRADE_CORRIDOR_GROWTH'""",
        "banksia green loans in pipeline": f"""SELECT count(*) FROM {b}.crm_opportunity o
            JOIN {acct} a USING (crm_account_id)
            WHERE a.storyline_key = 'banksia' AND o.product = 'Green Loan' AND o.status = 'Open'""",
        "banksia SLL_ELIGIBLE signals": f"""SELECT count(*) FROM {b}.crm_signal s JOIN {acct} a USING (crm_account_id)
            WHERE a.storyline_key = 'banksia' AND s.signal_code = 'SLL_ELIGIBLE'""",
        "hk_casa DEPOSIT_SURPLUS 2026 (detected / days to action)": f"""
            SELECT concat_ws(', ', sort_array(collect_list(concat(
            s.detected_date, '/', datediff(to_date(s.actioned_date), to_date(s.detected_date)))))) FROM {b}.crm_signal s
            JOIN {acct} a USING (crm_account_id) WHERE a.storyline_key = 'hk_casa' AND s.signal_code = 'DEPOSIT_SURPLUS'
            AND s.detected_date >= '2026-01-01'""",
    }
    for k, sql in q.items():
        print(f"[p03c13] storyline {k}: {spark.sql(sql).collect()[0][0]}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 3c-13: CRM engagement & opportunity signals")
    ap.add_argument("--catalog", default=None)
    ap.add_argument("--profile", default=None)
    args = ap.parse_args()
    cfg = load_config(catalog=args.catalog) if args.catalog else load_config()
    c = cfg.catalog
    spark = get_spark(args.profile)

    groups = truth.build_groups(cfg)
    entities = truth.build_entities(cfg, groups)
    _, xref = fragment.fragment_all(cfg, entities, [g["group_id"] for g in groups])
    features = ce.load_features(spark, cfg)
    print("[p03c13] features: " + ", ".join(f"{k}={len(v):,}" for k, v in features.items()))
    if not features["news"]:
        print("[p03c13] bronze.ext_news not found: CAPEX_NEWS / MA_NEWS signals skipped (re-run after Phase 3c-15)")
    out = ce.build_crm(cfg, groups, entities, truth.build_people(cfg), xref, features)

    for t, spec in SPECS.items():
        for r in out[t]:
            r.update(_source_system="crm_platform", _batch_id=BATCH, _ingest_ts=INGEST_TS)
        write_table(_df(spark, out[t], spec + META), f"{c}.bronze.{t}", comment=COMMENTS[t])
    write_table(_df(spark, out["truth_crm_account"], TRUTH_SPEC), f"{c}.ops.synthetic_crm_account",
                comment="Generation truth: CRM account -> entity/group, tier, neglected coverage, blind-spot group.")
    for k, v in ce.storyline_checks(cfg, entities, xref, out).items():
        print(f"[p03c13] (in-memory) {k}: {v}")
    _verify(spark, c, out)
    print("[p03c13] done.")


if __name__ == "__main__":
    main()
