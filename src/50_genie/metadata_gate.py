"""Phase 7 metadata gate (brief §8 "Metadata hygiene gate"; PLAN §9).

Before a space is created: every asset of the space and every one of its columns / measures must carry a
non-empty comment, and every entity-matching column must fit the Genie value dictionary (<= 1,024 distinct
values of <= 127 characters). After the functions are deployed, every listed SQL function must carry a
comment in Unity Catalog. The gate also refreshes the column snapshot genie/_schema/<fqn>.json that the
builder reads (DESCRIBE TABLE EXTENDED ... AS JSON, digested).

Run:  .venv/bin/python src/50_genie/metadata_gate.py --profile my-workspace [--only <slug>] [--snapshot-all]
Exit: 0 clean, 1 gate failures.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Mapping

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (str(REPO / "src"), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from smbc_genie_lib import genie as g  # noqa: E402
from genie_ws import TAG, Workspace, first_line  # noqa: E402


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_", suffix=path.suffix)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def snapshot(ws: Workspace, identifiers: Iterable[str], schema_dir: Path = g.SCHEMA_DIR,
             write: bool = True) -> Dict[str, Dict]:
    """DESCRIBE each asset, write genie/_schema/<fqn>.json (only when it changed) and return the digests."""
    out: Dict[str, Dict] = {}
    for ident in identifiers:
        ident = ident.lower()
        _, _, rows = ws.sql(f"DESCRIBE TABLE EXTENDED {ident} AS JSON")
        dig = g.schema_digest(json.loads(rows[0][0]), ident)
        text = json.dumps(dig, indent=1, sort_keys=True) + "\n"
        p = g.schema_path(ident, schema_dir)
        if write and (not p.exists() or p.read_text() != text):
            _atomic_write(p, text)
        out[ident] = dig
    return out


def _q(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def entity_matching_problems(ws: Workspace, built: g.BuiltSpace, schema: Mapping[str, Mapping],
                             limits: Mapping) -> List[str]:
    """Distinct values and max length of every entity-matching column vs the Genie dictionary limits."""
    problems, n_ok = [], 0
    max_n, max_len = int(limits["api_entity_matching_values_max"]), int(limits["api_entity_matching_value_chars_max"])
    for col in built.entity_matching:
        ident, name = ".".join(col.split(".")[:3]), col.split(".", 3)[3]
        is_mv = (schema.get(ident) or {}).get("type") == "METRIC_VIEW"
        sql = (f"SELECT COUNT(*), MAX(LENGTH(v)) FROM (SELECT {_q(name)} AS v FROM {ident} GROUP BY ALL) WHERE v IS NOT NULL"
               if is_mv else f"SELECT COUNT(DISTINCT {_q(name)}), MAX(LENGTH({_q(name)})) FROM {ident}")
        try:
            _, _, rows = ws.sql(sql)
            n, ln = int(rows[0][0] or 0), int(rows[0][1] or 0)
        except Exception as e:  # noqa: BLE001
            problems.append(f"{col}: entity-matching probe failed: {first_line(e)}")
            continue
        status = "ok"
        if n > max_n:
            problems.append(f"{col}: {n} distinct values > {max_n} (entity matching limit) - use LIKE in example SQL instead")
            status = "TOO MANY"
        if ln > max_len:
            problems.append(f"{col}: values up to {ln} chars > {max_len} (entity matching limit)")
            status = "TOO LONG"
        if status == "ok":   # one summary line instead of ~70 per space (notebook output has a size limit)
            n_ok += 1
        else:
            print(f"{TAG}   entity matching {col:70} {n:>5} values, max {ln:>3} chars  {status}")
    print(f"{TAG}   entity matching: {n_ok}/{len(built.entity_matching)} columns within the dictionary limits")
    return problems


def gate(ws: Workspace, built: g.BuiltSpace, schema: Mapping[str, Mapping], limits: Mapping) -> List[str]:
    problems: List[str] = []
    for ident in built.assets:
        dig = schema.get(ident)
        if dig is None:
            problems.append(f"{ident}: not found / not described")
            continue
        gaps = g.metadata_gaps(dig)
        problems += gaps
        print(f"{TAG}   metadata {ident:55} {len(dig['columns']):>3} columns  {'ok' if not gaps else f'{len(gaps)} gaps'}")
    problems += entity_matching_problems(ws, built, schema, limits)
    return problems


def function_problems(ws: Workspace, built: g.BuiltSpace, catalog: str) -> List[str]:
    """Every listed SQL function exists in UC with a non-empty comment (Genie sees only the comment)."""
    problems = []
    listed = [f["identifier"] for f in built.serialized["instructions"]["sql_functions"]]
    for fqn in listed:
        cat, sch, name = fqn.split(".")
        _, _, rows = ws.sql(f"SELECT comment FROM {cat}.information_schema.routines "
                            f"WHERE routine_schema = '{sch}' AND routine_name = '{name}'")
        if not rows:
            problems.append(f"{fqn}: function not found")
        elif not g.one_line(rows[0][0]):
            problems.append(f"{fqn}: function has no comment")
    return problems


def all_assets(ws: Workspace, catalog: str) -> List[str]:
    _, _, rows = ws.sql(f"SELECT table_schema, table_name FROM {catalog}.information_schema.tables "
                        f"WHERE table_schema IN ('gold', 'metrics') ORDER BY 1, 2")
    return [f"{catalog}.{s}.{t}".lower() for s, t in rows]


def main(argv: List[str] | None = None) -> int:
    from build_space_specs import build_one, load_inputs

    p = argparse.ArgumentParser(description="SMBC APAC Genie - phase 7 metadata gate")
    p.add_argument("--profile", default=None)
    p.add_argument("--warehouse-id", default=None)
    p.add_argument("--catalog", default=None)
    p.add_argument("--only", default="", help="comma-separated space slugs")
    p.add_argument("--snapshot-all", action="store_true", help="snapshot every gold + metrics object, then exit")
    a = p.parse_args(argv)
    shared = g.load_shared()
    catalog = a.catalog or shared["catalog"]
    ws = Workspace(a.warehouse_id or shared["warehouse_id"], a.profile)
    if a.snapshot_all:
        ids = all_assets(ws, catalog)
        snapshot(ws, ids)
        print(f"{TAG} snapshot: {len(ids)} objects -> {g.SCHEMA_DIR.relative_to(REPO)}")
        return 0
    slugs = [s for s in a.only.split(",") if s] or g.space_slugs()
    bad = 0
    for slug in slugs:
        src, answers = load_inputs(slug)
        idents = [g.render(d["identifier"], {"catalog": catalog}).lower() for d in src.raw.get("data_sources") or []]
        schema = snapshot(ws, idents)
        built = build_one(src, shared, schema, answers, catalog, None)
        probs = gate(ws, built, schema, shared["limits"])
        print(f"{TAG} gate {slug}: {'PASS' if not probs else 'FAIL'}")
        for x in probs:
            print(f"{TAG}   ! {x}")
        bad += bool(probs)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
