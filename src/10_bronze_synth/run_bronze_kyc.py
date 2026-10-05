"""Phase 3c-11 — onboarding & KYC (+ the kyc-migration-backlog storyline).

  bronze.kyc_case         onboarding cases (new clients, all live, + open/withdrawn/rejected applicants)
  bronze.kyc_case_stage   stage events (Request -> ... -> Account Open -> First Transaction)
  bronze.kyc_review       review history: onboarding / periodic / trigger, due vs completed
  bronze.kyc_document     documents per case (received / outstanding / waived / cancelled)
  bronze.kyc_screening    sanctions / PEP / adverse-media hits (onboarding + ongoing)
  bronze.kyc_feedback     post-go-live survey score + comment theme
  ops.synthetic_onboarding_case   truth: case -> entity/group, go-live, first transaction

Pure Python (smbc_genie_lib.kyc), keyed by KYC-platform ids for ER. New clients' dates match the
accounts/payments from 3c-1/3c-3 (both read the same onboarding schedule). Re-runs overwrite.

  .venv/bin/python src/10_bronze_synth/run_bronze_kyc.py --profile my-workspace
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from smbc_genie_lib import fragment, kyc, onboarding, truth  # noqa: E402
from smbc_genie_lib.config import load_config  # noqa: E402
from smbc_genie_lib.spark_io import get_spark, write_table  # noqa: E402

INGEST_TS = "2026-09-30T13:00:00"
BATCH = "P03C-20260930"
META = [("_source_system", "s"), ("_batch_id", "s"), ("_ingest_ts", "s")]
# column specs: s=STRING (dates are ISO strings, as landed), i=INT, d=DOUBLE, b=BOOLEAN, dt=DATE
SPECS = {
    "kyc_case": [("case_id", "s"), ("applicant_id", "s"), ("kyc_id", "s"), ("applicant_name", "s"),
                 ("booking_country", "s"), ("segment", "s"), ("products_requested", "s"),
                 ("primary_product", "s"), ("request_date", "s"), ("current_stage", "s"), ("status", "s"),
                 ("status_date", "s"), ("blocker_reason", "s"), ("outcome_reason", "s"),
                 ("documents_outstanding", "i"), ("owner_id", "s"), ("rm_code", "s"),
                 ("intake_group_match", "b"), ("intake_group_ref", "s")],
    "kyc_case_stage": [("case_id", "s"), ("stage_no", "i"), ("stage_name", "s"), ("entered_date", "s"),
                       ("exited_date", "s"), ("days_in_stage", "i"), ("sla_days", "i"), ("is_sla_met", "b"),
                       ("is_current", "b"), ("actor_id", "s")],
    "kyc_review": [("review_id", "s"), ("kyc_id", "s"), ("review_type", "s"), ("trigger_reason", "s"),
                   ("due_date", "s"), ("completed_date", "s"), ("status", "s"), ("days_overdue", "i"),
                   ("risk_before", "s"), ("risk_after", "s"), ("reviewer_id", "s")],
    "kyc_document": [("document_id", "s"), ("case_id", "s"), ("document_type", "s"), ("is_mandatory", "b"),
                     ("requested_date", "s"), ("received_date", "s"), ("status", "s")],
    "kyc_screening": [("screening_id", "s"), ("subject_ref", "s"), ("case_id", "s"),
                      ("screening_context", "s"), ("screening_date", "s"), ("list_name", "s"),
                      ("hit_type", "s"), ("is_true_match", "b"), ("resolution_hours", "d"),
                      ("disposition", "s"), ("resolved_by", "s")],
    "kyc_feedback": [("feedback_id", "s"), ("case_id", "s"), ("kyc_id", "s"), ("survey_date", "s"),
                     ("score", "i"), ("comment_text", "s"), ("theme", "s")],
}
TRUTH_SPEC = [("case_id", "s"), ("applicant_id", "s"), ("kyc_id", "s"), ("entity_id", "s"), ("group_id", "s"),
              ("applicant_kind", "s"), ("segment", "s"), ("request_date", "dt"), ("go_live_date", "dt"),
              ("first_txn_date", "dt"), ("status", "s"), ("days_to_live", "i"), ("is_migration_peak", "b"),
              ("intake_group_match", "b")]
COMMENTS = {
    "kyc_case": "Bronze onboarding cases: new clients (live) + applicants (open/withdrawn/rejected).",
    "kyc_case_stage": "Bronze onboarding stage events (entered/exited, days in stage, SLA met).",
    "kyc_review": "Bronze KYC review history (onboarding/periodic/trigger; due vs completed; risk before/after).",
    "kyc_document": "Bronze onboarding documents per case (received/outstanding/waived/cancelled).",
    "kyc_screening": "Bronze name-screening hits (sanctions/PEP/adverse media; true match; resolution hours).",
    "kyc_feedback": "Bronze post-go-live onboarding survey (score, comment, theme).",
}


def _df(spark, rows, spec):
    from pyspark.sql.types import (BooleanType, DateType, DoubleType, IntegerType, StringType,
                                   StructField, StructType)
    types = {"s": StringType(), "i": IntegerType(), "d": DoubleType(), "b": BooleanType(), "dt": DateType()}
    schema = StructType([StructField(c, types[t], True) for c, t in spec])
    return spark.createDataFrame([[r.get(c) for c, _ in spec] for r in rows], schema)


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 3c-11: onboarding & KYC")
    ap.add_argument("--catalog", default=None)
    ap.add_argument("--profile", default=None)
    args = ap.parse_args()
    cfg = load_config(catalog=args.catalog) if args.catalog else load_config()
    c = cfg.catalog
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

    cohort = onboarding.new_client_cohort(cfg, entities, xref)
    out = kyc.build_kyc(cfg, groups, entities, truth.build_people(cfg), nondup("kyc_customer"),
                        nondup("crm_account"), cohort)
    for t, spec in SPECS.items():
        for r in out[t]:
            r.update(_source_system="kyc_platform", _batch_id=BATCH, _ingest_ts=INGEST_TS)
        n = write_table(_df(spark, out[t], spec + META), f"{c}.bronze.{t}", comment=COMMENTS[t])
        print(f"[p03c11] bronze.{t:16} {n:>7,} rows")
    n = write_table(_df(spark, out["truth_onboarding_case"], TRUTH_SPEC), f"{c}.ops.synthetic_onboarding_case",
                    comment="Generation truth: onboarding case -> entity/group, go-live, first transaction.")
    print(f"[p03c11] ops.synthetic_onboarding_case {n:,} rows")
    spark.sql(f"DROP TABLE IF EXISTS {c}.bronze.kyc_onboarding_case")  # superseded by kyc_case (draft)

    # verification, from the written bronze tables
    st = spark.sql(f"SELECT status, count(*) n FROM {c}.bronze.kyc_case GROUP BY status ORDER BY n DESC").collect()
    print("[p03c11] case status: " + ", ".join(f"{r['status']}={r['n']}" for r in st))
    before, peak = kyc.days_to_live_medians(out["truth_onboarding_case"])
    print(f"[p03c11] median days to live: {before:.0f} before the migration -> {peak:.0f} for Mar-May 2026 requests")
    print(f"[p03c11] FI KYC Docs stage: +{kyc.fi_kyc_docs_delta(out['kyc_case'], out['kyc_case_stage']):.1f} days "
          f"(Mar-May 2026 vs before)")
    od = spark.sql(f"""
      WITH me AS (SELECT date FROM {c}.bronze.ref_calendar
                  WHERE is_month_end AND date BETWEEN DATE'2025-04-01' AND DATE'{cfg.as_of_date}'),
      r AS (SELECT r.review_id, CAST(r.due_date AS DATE) due, CAST(r.completed_date AS DATE) done, k.kyc_risk_rating
            FROM {c}.bronze.kyc_review r JOIN {c}.bronze.kyc_customer k ON k.kyc_id = r.kyc_id
            WHERE r.review_type = 'Periodic')
      SELECT me.date, count(CASE WHEN r.kyc_risk_rating = 'High' THEN 1 END) hi, count(r.review_id) n
      FROM me LEFT JOIN r ON r.due < me.date AND (r.done IS NULL OR r.done > me.date)
      GROUP BY me.date ORDER BY me.date""").collect()
    base = [r["hi"] for r in od if r["date"] <= _dt.date(2026, 1, 31)]
    may = next(r["hi"] for r in od if r["date"] == _dt.date(2026, 5, 31))
    print(f"[p03c11] high-risk periodic overdue: May-26 {may} vs {sum(base) / len(base):.1f} normal "
          f"({may / (sum(base) / len(base)):.1f}x); all-risk by month-end: "
          + " ".join(f"{r['date']:%y%m}={r['n']}" for r in od if r["date"] >= _dt.date(2026, 1, 1)))
    as_of = _dt.date.fromisoformat(cfg.as_of_date)
    print(f"[p03c11] May-26 backlog cleared by {as_of}: {kyc.backlog_cleared_share(out['kyc_review'], as_of):.0%}")
    q8 = spark.sql(f"""SELECT count(*) n FROM {c}.ops.synthetic_onboarding_case
      WHERE status = 'Live' AND go_live_date >= DATE'2026-04-01' AND go_live_date <= DATE'{as_of}' - 60
        AND datediff(first_txn_date, go_live_date) > 60""").collect()[0]["n"]
    pre = spark.sql(f"""SELECT count(*) n FROM {c}.bronze.pay_payment_message p
      JOIN {c}.ops.synthetic_account a ON a.account_id = p.account_id
      JOIN {c}.ops.synthetic_onboarding_case o ON o.entity_id = a.entity_id
      WHERE p.payment_date < o.first_txn_date""").collect()[0]["n"]
    print(f"[p03c11] FY2026 new clients with no transaction within 60 days: {q8}; "
          f"new-client payments before first transaction: {pre}")
    print("[p03c11] done.")


if __name__ == "__main__":
    main()
