"""Storyline assertions -> ops.storyline_assertions (PLAN §5.4, §11; smbc_genie_lib.storyline_checks).

Runs every storyline check against the written tables, prints pass / fail and overwrites
ops.storyline_assertions with this run's results. Exits non-zero if any check fails, so a broken
generator fails the build fast.

  .venv/bin/python src/10_bronze_synth/run_storyline_checks.py --profile my-workspace
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from smbc_genie_lib import storyline_checks  # noqa: E402
from smbc_genie_lib.config import load_config  # noqa: E402
from smbc_genie_lib.spark_io import get_spark, write_table  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Storyline assertions")
    ap.add_argument("--catalog", default=None)
    ap.add_argument("--profile", default=None)
    args = ap.parse_args()
    cfg = load_config(catalog=args.catalog) if args.catalog else load_config()
    spark = get_spark(args.profile)
    now = _dt.datetime.now(_dt.timezone.utc)   # tz-aware: Spark reads a naive value as client-local time
    rows, failed = [], 0
    for ch in storyline_checks.checks(cfg.catalog):
        try:
            val = spark.sql(ch["sql"]).collect()[0]["actual"]
            actual = None if val is None else float(val)
            ok = actual is not None and ch["lo"] - 1e-9 <= actual <= ch["hi"] + 1e-9
            shown = "null" if actual is None else f"{actual:,.4f}".rstrip("0").rstrip(".")
        except Exception as e:  # noqa: BLE001 - a missing table / column is a failed check
            ok, shown = False, f"error: {str(e).splitlines()[0][:120]}"
        failed += not ok
        expected = f"{ch['lo']:g}" if ch["lo"] == ch["hi"] else f"[{ch['lo']:g}, {ch['hi']:g}]"
        print(f"[checks] {'PASS' if ok else 'FAIL'}  {ch['storyline_id']:>2} {ch['assertion_key']:<32} "
              f"{shown:>16}  expected {expected}")
        rows.append((ch["storyline_id"], ch["storyline_name"], ch["assertion_key"], ch["description"],
                     expected, shown, bool(ok), float(cfg.scale), now))
    schema = ("storyline_id int, storyline_name string, assertion_key string, description string, "
              "expected string, actual string, passed boolean, scale double, checked_at timestamp")
    write_table(spark.createDataFrame(rows, schema), f"{cfg.catalog}.ops.storyline_assertions",
                comment="Storyline assertions from the latest build (PLAN §5.4).")
    print(f"[checks] {len(rows) - failed}/{len(rows)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
