"""Phase 3a — write the synthetic truth universe (ops.synthetic_truth_*) and reference
dimensions (bronze.ref_*). Deterministic; re-runs overwrite. Runs locally via Databricks
Connect (serverless) or inside the p03 job (ambient Spark).

  .venv/bin/python src/10_bronze_synth/run_truth_ref.py --profile my-workspace
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from smbc_genie_lib import reference, truth  # noqa: E402
from smbc_genie_lib.config import load_config  # noqa: E402
from smbc_genie_lib.spark_io import create_rows_df, get_spark, write_table  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Phase 3a: truth universe + reference dimensions")
    p.add_argument("--catalog", default=None)
    p.add_argument("--profile", default=None)
    return p.parse_args()


def main() -> None:
    a = parse_args()
    cfg = load_config(catalog=a.catalog) if a.catalog else load_config()
    c = cfg.catalog
    spark = get_spark(a.profile)

    # ---- truth universe (ops) --------------------------------------------------------
    groups = truth.build_groups(cfg)
    entities = truth.build_entities(cfg, groups)
    people = truth.build_people(cfg)
    products = truth.build_products()

    specs = [
        (groups, f"{c}.ops.synthetic_truth_group", "True client groups (pre-fragmentation)."),
        (entities, f"{c}.ops.synthetic_truth_entity", "True legal entities with latent credit health."),
        (people, f"{c}.ops.synthetic_truth_person", "Fictional SMBC staff (RM / credit / TB / onboarding)."),
        (products, f"{c}.ops.synthetic_truth_product", "Product hierarchy (business line / family / product)."),
    ]
    # ---- reference dimensions (bronze.ref_*) -----------------------------------------
    specs += [
        (reference.build_calendar(cfg), f"{c}.bronze.ref_calendar", "Daily calendar with Japanese fiscal attributes."),
        (reference.build_currencies(cfg), f"{c}.bronze.ref_currency", "Currencies and base FX per USD."),
        (reference.build_fx_daily(cfg), f"{c}.bronze.fx_rate_daily", "Daily FX rate to USD (random walk)."),
        (reference.build_countries(cfg), f"{c}.bronze.ref_country", "Countries / booking locations and regulators."),
        (reference.build_booking_entities(cfg), f"{c}.bronze.ref_booking_entity", "13 APAC booking entities + JP/EMEA/AMER provider regions."),
        (reference.build_industry_peers(cfg), f"{c}.bronze.ref_industry_peer", "Industry sectors, subsectors and peer groups."),
    ]

    print(f"[p03a] catalog={c} writing {len(specs)} tables")
    results = []
    for rows, fqn, comment in specs:
        df = create_rows_df(spark, rows)
        n = write_table(df, fqn, comment=comment)
        results.append((fqn, n))
        print(f"[p03a]   {fqn:48} {n:>8,} rows")

    # expectations (dims are fixed regardless of SCALE)
    counts = dict(results)
    assert counts[f"{c}.ops.synthetic_truth_group"] == 380, "expected 380 groups"
    assert counts[f"{c}.ops.synthetic_truth_entity"] == 2500, "expected 2500 entities"
    print(f"[p03a] done: {sum(n for _, n in results):,} rows across {len(results)} tables")


if __name__ == "__main__":
    main()
