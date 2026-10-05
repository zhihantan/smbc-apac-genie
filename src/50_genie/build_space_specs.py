"""Phase 7 builder: genie/<slug>/space.yaml (+ functions.sql) -> Genie serialized_space v2 and artefacts.

Per space (PLAN §9) it writes, next to the source:
  space_spec.json          brief Appendix B summary
  instructions.md          the rendered single text-instruction block
  benchmarks.json          benchmark catalogue {id, key, question, expected_sql, expected_checks, ...}
  <slug>.geniespace.json   the exact create / update body (serialized_space as an object)
Offline: column configs come from the genie/_schema snapshot (refreshed by metadata_gate.py / run_genie.py).

Run:  .venv/bin/python src/50_genie/build_space_specs.py [--only <slug>[,<slug>]]
Exit: 0 ok, 2 spec errors.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (str(REPO / "src"), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from smbc_genie_lib import genie as g  # noqa: E402
from smbc_genie_lib.metrics import SPACES  # noqa: E402

TAG = "[p07]"


def load_inputs(slug: str) -> Tuple[g.SpaceSource, Dict[str, Any]]:
    return g.load_space(slug), g.load_answers(slug)


def build_one(src: g.SpaceSource, shared: Mapping[str, Any], schema: Mapping[str, Mapping], answers: Mapping[str, Any],
              catalog: Optional[str], warehouse_id: Optional[str]) -> g.BuiltSpace:
    return g.build_space(src, shared, schema, answers, SPACES, catalog=catalog, warehouse_id=warehouse_id)


def _write(path: Path, text: str) -> bool:
    if path.exists() and path.read_text() == text:
        return False
    path.write_text(text)
    return True


def write_artifacts(built: g.BuiltSpace, genie_dir: Path = g.GENIE_DIR) -> List[str]:
    d = genie_dir / built.slug
    files = {
        "space_spec.json": json.dumps(built.space_spec(), indent=2, ensure_ascii=False) + "\n",
        "instructions.md": built.instructions_md(),
        "benchmarks.json": json.dumps(built.benchmarks, indent=2, ensure_ascii=False) + "\n",
        f"{built.slug}.geniespace.json": json.dumps(built.geniespace_json(), indent=2, ensure_ascii=False) + "\n",
    }
    return [name for name, text in files.items() if _write(d / name, text)]


def summarize(built: g.BuiltSpace) -> str:
    c = built.counts()
    return (f"{c['assets']} assets, {c['entity_matching_columns']} entity-matching cols, {c['instruction_lines']} instruction lines, "
            f"{c['example_sqls']} example SQLs, {c['sql_functions']} functions, {c['sample_questions']} sample questions, "
            f"{c['benchmarks']} benchmarks ({c['instructions_total']}/100 instructions)")


def build_all(slugs: List[str], shared: Mapping[str, Any], catalog: Optional[str] = None,
              warehouse_id: Optional[str] = None, schema: Optional[Mapping[str, Mapping]] = None,
              write: bool = True) -> Dict[str, g.BuiltSpace]:
    out = {}
    for slug in slugs:
        try:
            src, answers = load_inputs(slug)
        except Exception as e:  # noqa: BLE001
            print(f"{TAG} {slug}: cannot load: {e}")
            continue
        cat = catalog or shared["catalog"]
        idents = [g.render(d.get("identifier", ""), {"catalog": cat}).lower() for d in src.raw.get("data_sources") or []]
        sch = schema if schema is not None else g.load_schema(idents)
        built = build_one(src, shared, sch, answers, cat, warehouse_id)
        out[slug] = built
        for w in built.warnings:
            print(f"{TAG}   warn  {slug}: {w}")
        for e in built.errors:
            print(f"{TAG}   ERROR {slug}: {e}")
        if built.ok and write:
            changed = write_artifacts(built)
            print(f"{TAG} build {slug}: {summarize(built)}; wrote {', '.join(changed) or 'nothing (unchanged)'}")
        elif built.ok:
            print(f"{TAG} build {slug}: {summarize(built)} (not written)")
        else:
            print(f"{TAG} build {slug}: FAILED ({len(built.errors)} errors)")
    return out


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="SMBC APAC Genie - phase 7 builder (offline)")
    p.add_argument("--only", default="", help="comma-separated space slugs (default: every genie/*/space.yaml)")
    p.add_argument("--catalog", default=None)
    a = p.parse_args(argv)
    shared = g.load_shared()
    slugs = [s for s in a.only.split(",") if s] or g.space_slugs()
    built = build_all(slugs, shared, a.catalog)
    return 2 if any(not b.ok for b in built.values()) or len(built) < len(slugs) else 0


if __name__ == "__main__":
    sys.exit(main())
