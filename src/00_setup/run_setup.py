"""Phase 2 — catalog setup: schemas, ops tables, build_config, fiscal functions, grants, tags.

Idempotent. Runs locally (``--profile``) or as a serverless job (ambient auth). The foundation
DDL (schemas, ops tables, functions) must succeed; grants and tags are best-effort and logged as
warnings on failure (e.g. no account group, or a governed-tag clash -> smbc_ prefix fallback).
Never drops anything.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from smbc_genie_lib.sql_runner import SqlRunner, render, split_statements  # noqa: E402

SETUP_FILES = ["01_catalog_schemas.sql", "04_ops_tables.sql", "05_functions.sql"]

# Free-form tags; Phase-2 pre-check falls back to smbc_-prefixed keys on a governed-tag clash (D37).
SCHEMA_TAGS = {
    "bronze":  {"domain": "customer360", "layer": "bronze",  "synthetic": "true", "pii": "false"},
    "shared":  {"domain": "customer360", "layer": "shared",  "synthetic": "true", "pii": "false", "source_region": "multi"},
    "silver":  {"domain": "customer360", "layer": "silver",  "synthetic": "true", "pii": "false"},
    "gold":    {"domain": "customer360", "layer": "gold",    "synthetic": "true", "pii": "false"},
    "metrics": {"domain": "customer360", "layer": "metrics", "synthetic": "true", "pii": "false"},
    "ops":     {"domain": "customer360", "layer": "ops",     "synthetic": "true", "pii": "false"},
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SMBC APAC Genie - phase 2: catalog setup")
    p.add_argument("--catalog", default="smbc_genie")
    p.add_argument("--warehouse-id", required=True)
    p.add_argument("--as-of", default="2026-09-30")
    p.add_argument("--history-start", default="2023-04-01")
    p.add_argument("--scale", type=float, default=0.1)
    p.add_argument("--seed", type=int, default=20260930)
    p.add_argument("--consumer-group", default="account users")
    p.add_argument("--storage-root", default="")
    p.add_argument("--profile", default=None, help="CLI profile for local runs; omit inside a job")
    p.add_argument("--dry-run", action="store_true", help="print statements, execute nothing")
    return p.parse_args()


def main() -> None:
    a = parse_args()
    params = {
        "catalog": a.catalog,
        "as_of_date": a.as_of,
        "history_start": a.history_start,
        "consumer_group": a.consumer_group,
        "storage_root": a.storage_root,
    }
    run_id = f"p02-{dt.datetime.now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
    print(f"[p02] run_id={run_id} catalog={a.catalog} warehouse={a.warehouse_id} dry_run={a.dry_run}")

    if a.dry_run:
        for f in SETUP_FILES:
            stmts = split_statements(render((HERE / f).read_text(), params))
            print(f"\n--- {f}: {len(stmts)} statements ---")
            for s in stmts:
                print(s.splitlines()[0][:100] + " ...")
        return

    runner = SqlRunner(a.warehouse_id, params=params, profile=a.profile)
    log: list[tuple[str, str, str]] = []  # (step, status, message)

    # 1) Foundation DDL — must succeed.
    for f in SETUP_FILES:
        n = runner.run_file(HERE / f)
        log.append((f, "ok", f"{n} statements"))
        print(f"[p02] {f}: {n} statements ok")

    # 2) build_config — single source of truth for parameters.
    cfg_rows = {
        "as_of_date": a.as_of, "history_start": a.history_start, "scale": str(a.scale),
        "random_seed": str(a.seed), "catalog": a.catalog, "warehouse_id": a.warehouse_id,
        "build_run_id": run_id,
    }
    values = ",\n".join(
        f"('{k}', '{v}', current_timestamp())" for k, v in cfg_rows.items()
    )
    runner.execute(
        f"INSERT OVERWRITE {a.catalog}.ops.build_config (key, value, updated_at) VALUES\n{values}"
    )
    log.append(("build_config", "ok", f"{len(cfg_rows)} keys"))
    print(f"[p02] build_config: {len(cfg_rows)} keys ok")

    # 3) Grants (best-effort).
    grants = [
        f"GRANT USE CATALOG ON CATALOG {a.catalog} TO `{a.consumer_group}`",
        f"GRANT USE SCHEMA, SELECT, EXECUTE ON SCHEMA {a.catalog}.gold TO `{a.consumer_group}`",
        f"GRANT USE SCHEMA, SELECT, EXECUTE ON SCHEMA {a.catalog}.metrics TO `{a.consumer_group}`",
    ]
    for g in grants:
        try:
            runner.execute(g)
            log.append(("grant", "ok", g))
        except Exception as e:  # noqa: BLE001
            log.append(("grant", "warn", f"{g} -> {str(e)[:160]}"))
            print(f"[p02] WARN grant failed (continuing): {str(e)[:160]}")

    # 4) Tags (best-effort, with governed-tag -> smbc_ prefix fallback).
    tag_status = apply_tags(runner, a.catalog)
    log.append(("tags", tag_status[0], tag_status[1]))

    # 5) Write the run log.
    write_run_log(runner, a.catalog, run_id, a.scale, log)
    oks = sum(1 for _, s, _ in log if s == "ok")
    warns = sum(1 for _, s, _ in log if s == "warn")
    print(f"[p02] done: {oks} ok, {warns} warn. See {a.catalog}.ops.build_run_log (run_id={run_id}).")


def apply_tags(runner: "SqlRunner", catalog: str) -> tuple[str, str]:
    """Try free-form schema tags; on a governed-tag clash, retry with smbc_ prefix."""
    def _set(schema: str, kv: dict, prefix: str = "") -> None:
        pairs = ", ".join(f"'{prefix}{k}' = '{v}'" for k, v in kv.items())
        runner.execute(f"ALTER SCHEMA {catalog}.{schema} SET TAGS ({pairs})")

    try:
        for schema, kv in SCHEMA_TAGS.items():
            _set(schema, kv)
        print(f"[p02] tags: applied to {len(SCHEMA_TAGS)} schemas")
        return ("ok", "free-form tags applied")
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "govern" in msg.lower() or "tag policy" in msg.lower() or "not allowed" in msg.lower():
            try:
                for schema, kv in SCHEMA_TAGS.items():
                    _set(schema, kv, prefix="smbc_")
                print("[p02] tags: governed-tag clash -> applied with smbc_ prefix")
                return ("ok", "smbc_-prefixed tags applied (governed clash)")
            except Exception as e2:  # noqa: BLE001
                print(f"[p02] WARN tags failed even with prefix: {str(e2)[:160]}")
                return ("warn", f"tags failed: {str(e2)[:160]}")
        print(f"[p02] WARN tags failed (continuing): {msg[:160]}")
        return ("warn", f"tags failed: {msg[:160]}")


def write_run_log(runner: "SqlRunner", catalog: str, run_id: str, scale: float, log: list) -> None:
    def esc(s: str) -> str:   # backslash form: Databricks reads 'it''s' as "its"
        return s.replace("\\", "\\\\").replace("'", "\\'")

    rows = ",\n".join(
        f"('{run_id}', 'p02_setup', '{esc(step)}', NULL, NULL, '{status}', '{esc(msg)}', "
        f"{scale}, current_timestamp(), current_timestamp(), 0.0)"
        for step, status, msg in log
    )
    runner.execute(
        f"INSERT INTO {catalog}.ops.build_run_log "
        f"(run_id, phase, step, object_name, row_count, status, message, scale, started_at, finished_at, duration_sec) "
        f"VALUES\n{rows}"
    )


if __name__ == "__main__":
    main()
