"""Phase 3b — identity-bearing bronze source systems + the ground-truth xref.

Fragments the 2,500 true entities into ~8k messy records across 7 sources, each projected into
its own differently-named, mostly-STRING schema (brief §3.2) with ingest metadata. Writes the
ground-truth map to ops.synthetic_truth_xref for Phase-4 ER scoring. Deterministic; re-runs
overwrite.

  .venv/bin/python src/10_bronze_synth/run_bronze_sources.py --profile my-workspace
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from smbc_genie_lib import fragment, kyc, onboarding, rng, truth  # noqa: E402
from smbc_genie_lib.config import load_config  # noqa: E402
from smbc_genie_lib.spark_io import get_spark, write_table  # noqa: E402

INGEST_TS = "2026-09-30T02:00:00"
BATCH = "P03B-20260930"

# Per-source projection: out_column -> canonical key (or ("gen", fn) for a generated extra).
PROJECTIONS = {
    "ext_company_master": [
        ("company_id", "source_id"), ("registered_name", "name_recorded"),
        ("country_code", "country_code"), ("parent_company_id", "parent_group_id"),
        ("lei_code", "lei"), ("tax_ref", "tax_id"),
        ("listing_status", ("gen", lambda r, s: "Listed" if rng.unit(s, "list", r["source_id"]) < 0.12 else "Private")),
    ],
    "core_customer": [
        ("cust_no", "source_id"), ("cust_name", "name_recorded"), ("cntry", "country_code"),
        ("parent_cust_grp", "parent_group_id"), ("lei_code", "lei"), ("tax_no", "tax_id"),
        ("cust_type", ("gen", lambda r, s: "CORP")),
    ],
    "crm_account": [
        ("account_id", "source_id"), ("account_name", "name_recorded"), ("country", "country_code"),
        ("ultimate_parent_id", "parent_group_id"), ("lei", "lei"), ("segment_label", "segment_label"),
        ("rm_code", ("gen", lambda r, s: f"RM{rng.hash64(s, 'rm', r['source_id']) % 120 + 1:03d}")),
    ],
    "kyc_customer": [
        ("kyc_id", "source_id"), ("legal_entity_name", "name_recorded"),
        ("country_of_incorp", "country_code"), ("parent_entity_id", "parent_group_id"),
        ("lei", "lei"), ("tax_identification_no", "tax_id"),
        ("kyc_risk_rating", "kyc_risk"), ("customer_since", "customer_since"),
    ],
    "credit_obligor": [
        ("obligor_id", "source_id"), ("obligor_name", "name_recorded"), ("country", "country_code"),
        ("parent_obligor_grp", "parent_group_id"), ("lei", "lei"), ("internal_grade", "internal_grade"),
    ],
    "trade_party": [
        ("party_id", "source_id"), ("party_name", "name_recorded"), ("party_country", "country_code"),
        ("lei", "lei"),
    ],
    "tsy_counterparty": [
        ("cpty_id", "source_id"), ("cpty_name", "name_recorded"), ("cpty_country", "country_code"),
        ("lei", "lei"), ("tax_id", "tax_id"),
    ],
}
META_COLS = ["_source_system", "_source_file", "_batch_id", "_ingest_ts"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 3b: bronze source systems + ground-truth xref")
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
    records, xref = fragment.fragment_all(cfg, entities, gids)
    print(f"[p03b] fragmented {len(entities):,} entities -> {len(records):,} source records")
    # KYC master attributes from the KYC platform's own logic (3c-11): the current risk rating
    # (matches the latest review) and the relationship start (go-live for in-window new clients)
    by_id = {e["entity_id"]: e for e in entities}
    cohort = onboarding.new_client_cohort(cfg, entities, xref)
    for r, x in zip(records, xref):
        if r["source_system"] == "kyc_customer":
            e = by_id[x["entity_id"]]
            r["kyc_risk"] = kyc.risk_rating(cfg, e)
            r["customer_since"] = onboarding.customer_since(cfg, e, cohort).isoformat()

    by_source: dict = {s: [] for s in fragment.IDENTITY_SOURCES}
    for r in records:
        by_source[r["source_system"]].append(r)

    def all_string_df(rows, cols):
        schema = StructType([StructField(col, StringType(), True) for col in cols])
        data = [[None if r.get(col) is None else str(r.get(col)) for col in cols] for r in rows]
        return spark.createDataFrame(data, schema)

    total = 0
    for src, proj in PROJECTIONS.items():
        src_file = f"{src}_{BATCH}.csv"
        out_cols = [oc for oc, _ in proj] + META_COLS
        projected = []
        for r in by_source[src]:
            row = {}
            for oc, spec in proj:
                if isinstance(spec, tuple) and spec[0] == "gen":
                    row[oc] = spec[1](r, seed)
                else:
                    row[oc] = r.get(spec)
            row["_source_system"] = src
            row["_source_file"] = src_file
            row["_batch_id"] = BATCH
            row["_ingest_ts"] = INGEST_TS
            projected.append(row)
        df = all_string_df(projected, out_cols)
        n = write_table(df, f"{c}.bronze.{src}", comment=f"Bronze landing from the {src} source system (raw, mostly STRING; identity fragmented).")
        total += n
        print(f"[p03b]   bronze.{src:22} {n:>7,} rows")

    # ground-truth xref (ops) — typed is fine (no nulls in the key/flag columns)
    xref_df = spark.createDataFrame(xref)
    nx = write_table(xref_df, f"{c}.ops.synthetic_truth_xref",
                     comment="Ground truth: (source_system, source_id) -> true entity_id/group_id, for ER scoring.")
    print(f"[p03b]   ops.synthetic_truth_xref {nx:>7,} rows")
    assert nx == len(records), "xref must cover every source record"
    print(f"[p03b] done: {total:,} bronze source rows across 7 systems + {nx:,} xref rows")


if __name__ == "__main__":
    main()
