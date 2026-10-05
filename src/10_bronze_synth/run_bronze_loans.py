"""Phase 3c-2 — lending book: facilities, utilisation, covenants, collateral.

Writes ops.synthetic_facility (generation truth) + the raw bronze credit/core feeds:
bronze.credit_facility_terms, bronze.credit_covenant_test, bronze.credit_collateral, and the
Spark-expanded bronze.core_facility_balance_monthly (health-driven utilisation). Re-runs overwrite.

  .venv/bin/python src/10_bronze_synth/run_bronze_loans.py --profile my-workspace
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from smbc_genie_lib import credit, fragment, health, rng, storyline_injectors, truth  # noqa: E402
from smbc_genie_lib.config import load_config  # noqa: E402
from smbc_genie_lib.spark_io import get_spark, write_table  # noqa: E402

INGEST_TS = "2026-09-30T04:00:00"
BATCH = "P03C-20260930"


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 3c-2: facilities, covenants, collateral")
    ap.add_argument("--catalog", default=None)
    ap.add_argument("--profile", default=None)
    args = ap.parse_args()
    cfg = load_config(catalog=args.catalog) if args.catalog else load_config()
    c, seed = cfg.catalog, cfg.random_seed
    spark = get_spark(args.profile)

    import datetime as _dt
    from pyspark.sql.types import DateType, DoubleType, StringType, StructField, StructType
    as_of = _dt.date.fromisoformat(cfg.as_of_date)
    groups = truth.build_groups(cfg)
    entities = truth.build_entities(cfg, groups)
    gids = [g["group_id"] for g in groups]
    _, xref = fragment.fragment_all(cfg, entities, gids)
    obligor = {}
    for x in xref:
        if x["source_system"] == "credit_obligor" and not x["is_within_source_dup"]:
            obligor.setdefault(x["entity_id"], x["source_id"])

    facs = credit.build_facilities(cfg, entities, obligor)
    repriced = storyline_injectors.tighten_jc_lending_only(cfg, entities, xref, facs)   # storyline 8
    hlut = {(r["entity_id"], r["month"]): r["health"] for r in health.build_health_monthly(cfg, entities)}
    covs = credit.build_covenant_tests(cfg, facs, hlut)
    # storylines: Sunda's FY2025 breach path + the five covenant blind-spot names (D44)
    covs, blind = storyline_injectors.adjust_covenants(cfg, entities, facs, covs, hlut)
    coll = credit.build_collateral(cfg, facs)
    print(f"[p03c2] {len(facs):,} facilities ({repriced} JC lending-only repriced), {len(covs):,} covenant tests, "
          f"{len(coll):,} collateral")

    # ops generation-truth (typed)
    write_table(spark.createDataFrame(facs), f"{c}.ops.synthetic_facility",
                comment="Generation-truth lending facilities (entity_id, limit, base utilisation).")

    def meta(rows):
        for r in rows:
            r["_source_system"] = "credit_workflow"
            r["_batch_id"] = BATCH
            r["_ingest_ts"] = INGEST_TS
        return rows

    terms = [{"facility_id": f["facility_id"], "obligor_id": f["obligor_id"],
              "facility_type": f["facility_type"], "limit_usd": f["limit_usd"], "currency": f["currency"],
              "margin_bps": f["margin_bps"], "security_type": f["security_type"],
              "guarantor_type": f["guarantor_type"], "origination_date": f["origination_date"],
              "maturity_date": f["maturity_date"], "has_covenant": f["has_covenant"],
              "status": credit.facility_status(f, as_of), "closed_date": f["closed_date"]} for f in facs]
    write_table(spark.createDataFrame(meta(terms)), f"{c}.bronze.credit_facility_terms",
                comment="Bronze credit-workflow facility terms (type, limit, margin, security, guarantor).")
    write_table(spark.createDataFrame(meta(covs)), f"{c}.bronze.credit_covenant_test",
                comment="Bronze covenant tests (quarterly; leverage/ICR; headroom, breach, latest flag).")
    write_table(spark.createDataFrame(meta(coll)), f"{c}.bronze.credit_collateral",
                comment="Bronze collateral (type, appraised value, LTV, last valuation date).")
    print(f"[p03c2] wrote credit_facility_terms / credit_covenant_test / credit_collateral")

    # monthly utilisation (Spark, health-driven; storyline facilities pinned via util_override)
    pins = storyline_injectors.utilisation_overrides(cfg, entities, facs)
    pin_schema = StructType([StructField("facility_id", StringType()), StructField("d", DateType()),
                             StructField("util", DoubleType())])
    spark.createDataFrame([(k[0], k[1], float(v)) for k, v in pins.items()] or [("none", as_of, 0.0)],
                          pin_schema).createOrReplaceTempView("util_override")
    noise = rng.spark_unit_expr(seed, "f.facility_id", "cal.date")
    sql = f"""
    WITH base AS (
      SELECT f.facility_id, f.obligor_id, f.currency, cal.date AS d, f.limit_usd,
        coalesce(ov.util, least(1.08, greatest(0.05,
          f.base_utilisation + (1 - h.health)*0.40 + ({noise} - 0.5)*0.08))) AS util
      FROM {c}.ops.synthetic_facility f
      JOIN {c}.bronze.ref_calendar cal
        ON cal.is_month_end AND cal.date >= trunc(f.origination_date,'MM')
        AND cal.date <= least(DATE'{as_of.isoformat()}', last_day(coalesce(f.closed_date, f.maturity_date)))
      JOIN {c}.ops.synthetic_entity_health_monthly h
        ON h.entity_id = f.entity_id AND h.month = trunc(cal.date,'MM')
      LEFT JOIN util_override ov ON ov.facility_id = f.facility_id AND ov.d = cal.date
    )
    SELECT facility_id, obligor_id, d AS balance_date, currency,
      round(limit_usd, 2) AS limit_usd,
      round(limit_usd * util, 2) AS drawn_usd,
      round(limit_usd * util * fx.rate_per_usd, 2) AS drawn_lcy,
      round(util, 4) AS utilisation_pct,
      'core_banking' AS _source_system, '{BATCH}' AS _batch_id, '{INGEST_TS}' AS _ingest_ts
    FROM base JOIN {c}.bronze.fx_rate_daily fx ON fx.date = base.d AND fx.currency_code = base.currency
    """
    n = write_table(spark.sql(sql), f"{c}.bronze.core_facility_balance_monthly",
                    comment="Bronze month-end facility drawn/limit/utilisation (health-driven).")
    print(f"[p03c2] core_facility_balance_monthly: {n:,} rows")
    latest = [t for t in covs if t["is_latest_test"] and t["covenant_type"] == "Net Debt/EBITDA"]
    print(f"[p03c2] latest leverage tests: breached {sum(t['breached'] for t in latest)}, "
          f"headroom < 10% (not breached) {sum((not t['breached']) and t['headroom_pct'] < 0.10 for t in latest)}; "
          f"blind-spot obligors {blind}")
    print(f"[p03c2] scripted facilities: " + ", ".join(f"{f['facility_id']} {f['facility_type']} "
          f"{f['limit_usd'] / 1e6:.0f}m ({credit.facility_status(f, as_of)})"
          for f in facs[-3:]))
    print("[p03c2] done.")


if __name__ == "__main__":
    main()
