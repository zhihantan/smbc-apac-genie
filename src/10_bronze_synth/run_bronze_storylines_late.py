"""Late storyline hook (PLAN §5.5 step 8, `apply_to_bronze`): runs after every bronze generator.

  bronze.pay_payment_message   + the month-to-date replay batch PAY_20260617_R: the 1-17 Jun 2026
                                 messages land a second time (same payment_id, later ingest), which
                                 silver de-duplicates and logs to dq_results (storyline 12)
  ops.er_record_availability   Meridian's scripted identity records exist before the first ER run, so
                                 the v1 runs see all five and split them 3 ways (storyline 3)

It runs last so no generator that reads payments double-counts the replay. Idempotent: the replay
rows are deleted and re-inserted.

  .venv/bin/python src/10_bronze_synth/run_bronze_storylines_late.py --profile my-workspace
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from smbc_genie_lib import storylines, truth  # noqa: E402
from smbc_genie_lib.config import load_config  # noqa: E402
from smbc_genie_lib.spark_io import get_spark, write_table  # noqa: E402

BATCH = "P03C-20260930"
REPLAY_INGEST_TS = "2026-06-18T23:40:00"   # after every original it repeats


def main() -> None:
    ap = argparse.ArgumentParser(description="Late storyline hook: payments replay + ER availability")
    ap.add_argument("--catalog", default=None)
    ap.add_argument("--profile", default=None)
    args = ap.parse_args()
    cfg = load_config(catalog=args.catalog) if args.catalog else load_config()
    c = cfg.catalog
    spark = get_spark(args.profile)
    pay = f"{c}.bronze.pay_payment_message"
    r = storylines.PAY_REPLAY

    # 1) payments replay (storyline 12)
    spark.sql(f"DELETE FROM {pay} WHERE _batch_id = '{r['batch_id']}'")
    if storylines.on(cfg, storylines.PAYMENTS_REPLAY):
        cols = [f.name for f in spark.table(pay).schema.fields]
        sel = ", ".join(f"'{r['batch_id']}' AS _batch_id" if col == "_batch_id"
                        else f"'{REPLAY_INGEST_TS}' AS _ingest_ts" if col == "_ingest_ts" else col
                        for col in cols)
        spark.sql(f"""INSERT INTO {pay} SELECT {sel} FROM {pay}
          WHERE _batch_id = '{BATCH}' AND payment_date BETWEEN DATE'{r['replay_from']}' AND DATE'{r['replay_to']}'""")
    n = spark.sql(f"SELECT count(*) n FROM {pay} WHERE _batch_id = '{r['batch_id']}'").collect()[0]["n"]
    print(f"[p03late] payments replay {r['batch_id']}: {n:,} duplicate messages "
          f"({r['replay_from']}..{r['replay_to']})")

    # 2) Meridian's scripted records exist before the first ER run (storyline 3)
    entities = truth.build_entities(cfg, truth.build_groups(cfg))
    lead = storylines.lead_entity(entities, storylines.MERIDIAN["key"])["entity_id"]
    avail = spark.sql(f"""SELECT source_system, source_id, DATE'2020-01-01' AS available_from
      FROM {c}.ops.synthetic_truth_xref WHERE entity_id = '{lead}'""")
    na = write_table(avail, f"{c}.ops.er_record_availability",
                     comment="ER record-availability overrides (storyline 3: Meridian's scripted records).")
    print(f"[p03late] ops.er_record_availability: {na} Meridian record(s)")
    print("[p03late] done.")


if __name__ == "__main__":
    main()
