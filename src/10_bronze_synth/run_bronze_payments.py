"""Phase 3c-3 — payments hub (bronze.pay_payment_message, pay_repair_queue, pay_counterparty_bank).

~1.8M payments at SCALE 1.0 (scaled by SCALE), expanded in Spark from range() with deterministic
hash attributes. ~35% of supplier/trade payments route to OTHER banks (the FX-flow and
loan-service-elsewhere opportunity signals derive from these), ~92% STP, ~32% cross-border.
No payment predates its account's `active_from` (open date; a new client's first transaction,
D43): dates are drawn over the whole window and pre-activity draws dropped, so volume scales
with tenure, and the draw count is topped up so about the target count survives. Volume is size-weighted:
operating accounts (current, and savings at a lower rate) draw payments in proportion to
sqrt(balance), via a bucket table so the draw stays an equi-join. Supplier / trade-settlement
payments carry a client-scoped `counterparty_ref` (distinct suppliers per client). Payments key by
cust_no/account so gold resolves them via ER. Re-runs overwrite.

  .venv/bin/python src/10_bronze_synth/run_bronze_payments.py --profile my-workspace
"""
from __future__ import annotations

import argparse
import datetime as _dt
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from smbc_genie_lib import names, rng, storyline_injectors, truth  # noqa: E402
from smbc_genie_lib.config import load_config  # noqa: E402
from smbc_genie_lib.spark_io import get_spark, write_table  # noqa: E402
from smbc_genie_lib.sqlgen import pick_from_array, weighted_case  # noqa: E402

INGEST_TS = "2026-09-30T05:00:00"
BATCH = "P03C-20260930"
START = _dt.date(2024, 4, 1)
N_BUCKETS = 20_000
# relative payment activity by account type (term placements do not originate payments)
ACCOUNT_ACTIVITY = {"Current Account": 1.0, "Savings Account": 0.3, "Time Deposit": 0.0, "Money Market Deposit": 0.0}
REPAIR_REASONS = ["Invalid BIC", "Missing beneficiary", "Sanctions hold", "Format error",
                  "Duplicate instruction", "FX cover missing"]
CORRIDORS = ["CN", "JP", "KR", "VN", "IN", "US", "DE", "GB", "SG", "HK", "AU", "TH"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 3c-3: payments")
    ap.add_argument("--catalog", default=None)
    ap.add_argument("--profile", default=None)
    args = ap.parse_args()
    cfg = load_config(catalog=args.catalog) if args.catalog else load_config()
    c, seed = cfg.catalog, cfg.random_seed
    spark = get_spark(args.profile)

    # reference: counterparty banks (SMBC + fictional competitors + intercompany)
    bank_rows = ([{"bank_name": "SMBC", "bank_type": "SMBC"}]
                 + [{"bank_name": b, "bank_type": "Other Bank"} for b in names.COMPETITOR_BANKS]
                 + [{"bank_name": "Intercompany", "bank_type": "Intercompany"}])
    write_table(spark.createDataFrame(bank_rows), f"{c}.bronze.pay_counterparty_bank",
                comment="Payment counterparty banks (SMBC, fictional competitors, intercompany).")

    n_pay = max(1000, round(1_800_000 * cfg.scale))
    as_of = _dt.date.fromisoformat(cfg.as_of_date)
    days = (as_of - START).days
    # size-weighted payment buckets: each bucket maps to one operating account
    acc = sorted((r.asDict() for r in spark.table(f"{c}.ops.synthetic_account")
                  .select("account_id", "account_type", "base_balance_usd", "active_from").collect()),
                 key=lambda a: a["account_id"])
    w = [ACCOUNT_ACTIVITY.get(a["account_type"], 0.0) * math.sqrt(a["base_balance_usd"]) for a in acc]
    tot = sum(w)
    quota = [N_BUCKETS * x / tot for x in w]
    alloc = [int(q) for q in quota]
    for i in sorted(range(len(acc)), key=lambda i: quota[i] - alloc[i], reverse=True)[:N_BUCKETS - sum(alloc)]:
        alloc[i] += 1
    buckets, b = [], 0
    for a, k in zip(acc, alloc):
        for _ in range(k):
            buckets.append((b, a["account_id"]))
            b += 1
    spark.createDataFrame(buckets, "bucket int, account_id string").createOrReplaceTempView("pay_bucket")
    # share of draws that land on/after their account's active_from -> top-up factor
    kept = sum(k * max(0, (as_of - max(START, a["active_from"])).days) / days for a, k in zip(acc, alloc)) / N_BUCKETS
    n_draw = round(n_pay / kept)

    def u(salt):
        return rng.spark_unit_expr(seed, "p.id", f"'{salt}'")

    purpose = weighted_case(u("purp"), [("Supplier", 40), ("Trade Settlement", 14), ("Intercompany", 15),
                                        ("Payroll", 15), ("Tax", 8), ("Loan Service", 5), ("Dividend", 3)])
    channel = weighted_case(u("chan"), [("ISO 20022", 30), ("SWIFT MT", 22), ("Host-to-Host", 14),
                                        ("Payments API", 12), ("Portal", 10), ("RTGS", 7), ("FAST", 5)])
    sql = f"""
    WITH acct AS (
      SELECT b.bucket, a.account_id, a.cust_no, a.base_balance_usd, a.active_from, e.booking_country,
        CAST(greatest(5, least(400, sqrt(a.base_balance_usd) / 60)) AS INT) AS n_counterparties
      FROM pay_bucket b JOIN {c}.ops.synthetic_account a ON a.account_id = b.account_id
      JOIN {c}.ops.synthetic_truth_entity e ON e.entity_id = a.entity_id
    ),
    raw AS (
      SELECT p.id AS pay_seq, a.account_id, a.cust_no, a.booking_country, a.active_from, a.n_counterparties,
        DATE'{START.isoformat()}' + CAST({u('dt')} * {days} AS INT) AS payment_date,
        {purpose} AS payment_purpose, {channel} AS channel,
        CASE WHEN {u('dir')} < 0.55 THEN 'Outbound' ELSE 'Inbound' END AS direction,
        round(greatest(500.0, a.base_balance_usd * (0.008 + 0.06*{u('amt')}) * exp(({u('sz')}-0.5)*1.4)), 2) AS amount_usd,
        {u('cpty')} AS ucpty, {u('xb')} AS uxb, {u('stp')} AS ustp, {u('bank')} AS ubank,
        {u('corr')} AS ucorr, {u('rep')} AS urep, {u('ps')} AS ups
      FROM range({n_draw}) p
      JOIN acct a ON a.bucket = CAST(pmod(xxhash64({seed}L, p.id), {N_BUCKETS}) AS INT)
    ),
    active AS (SELECT * FROM raw WHERE payment_date >= active_from),
    enriched AS (
      SELECT active.*,
        CASE WHEN payment_purpose = 'Intercompany' THEN 'Intercompany'
             WHEN payment_purpose IN ('Supplier','Trade Settlement') AND ucpty < 0.35 THEN 'Other Bank'
             WHEN payment_purpose NOT IN ('Supplier','Trade Settlement') AND ucpty < 0.15 THEN 'Other Bank'
             ELSE 'SMBC' END AS counterparty_bank_type,
        (uxb < 0.32) AS is_cross_border,
        (ustp >= 0.92) AS is_repaired
      FROM active
    )
    SELECT concat('PAY-', lpad(CAST(pay_seq AS STRING), 9, '0')) AS payment_id,
      cust_no, account_id, payment_date, direction, channel, payment_purpose,
      booking_country AS debtor_country,
      CASE WHEN is_cross_border THEN {pick_from_array('ucorr', CORRIDORS)} ELSE booking_country END AS counterparty_country,
      is_cross_border, counterparty_bank_type,
      CASE WHEN counterparty_bank_type = 'Other Bank' THEN {pick_from_array('ubank', names.COMPETITOR_BANKS)}
           WHEN counterparty_bank_type = 'Intercompany' THEN 'Intercompany' ELSE 'SMBC' END AS counterparty_bank_name,
      amount_usd,
      CASE WHEN payment_purpose IN ('Supplier', 'Trade Settlement')
           THEN concat(cust_no, '-CP', lpad(CAST(pmod(xxhash64({seed}L, pay_seq, 'cp'), n_counterparties) AS STRING), 3, '0'))
      END AS counterparty_ref,
      (NOT is_repaired) AS stp_flag,
      CASE WHEN is_repaired THEN {pick_from_array('urep', REPAIR_REASONS)} ELSE NULL END AS repair_reason,
      CASE WHEN is_repaired THEN CAST(1800 + ups*86400 AS INT) ELSE CAST(2 + ups*40 AS INT) END AS processing_seconds,
      'payments_hub' AS _source_system, '{BATCH}' AS _batch_id,
      concat(CAST(date_add(payment_date, 1) AS STRING), 'T05:00:00') AS _ingest_ts   -- next-morning daily batch
    FROM enriched
    """
    pay_df = spark.sql(sql)
    # storyline 2: Kinokawa's loan service to the refinancing bank + its CN->VN trade settlements
    entities = truth.build_entities(cfg, truth.build_groups(cfg))
    accts = [r.asDict() for r in spark.table(f"{c}.ops.synthetic_account")
             .select("account_id", "cust_no", "entity_id", "account_type").collect()]
    kin = storyline_injectors.kinokawa_payments(cfg, entities, accts, n_draw)
    if kin:
        for r in kin:
            r.update(_source_system="payments_hub", _batch_id=BATCH,
                     _ingest_ts=(r["payment_date"] + _dt.timedelta(days=1)).isoformat() + "T05:00:00")
        pay_df = pay_df.unionByName(spark.createDataFrame(kin, pay_df.schema))
    n = write_table(pay_df, f"{c}.bronze.pay_payment_message",
                    comment="Bronze payment messages (channel, purpose, counterparty bank, STP/repair, corridor).")
    print(f"[p03c3] pay_payment_message: {n:,} rows (target {n_pay:,}; {n_draw:,} draws, "
          f"{kept:.3f} on/after account activity start; {len(kin)} scripted Kinokawa payments)")

    rq = write_table(
        spark.sql(f"""SELECT payment_id, cust_no, payment_date, repair_reason, processing_seconds, true AS repaired,
                      '{BATCH}' AS _batch_id, '{INGEST_TS}' AS _ingest_ts
                      FROM {c}.bronze.pay_payment_message WHERE stp_flag = false"""),
        f"{c}.bronze.pay_repair_queue", comment="Bronze repair queue (non-STP payments with reasons).")
    print(f"[p03c3] pay_repair_queue: {rq:,} rows")
    print("[p03c3] done.")


if __name__ == "__main__":
    main()
