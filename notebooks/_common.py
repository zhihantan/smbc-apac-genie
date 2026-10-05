# Databricks notebook source
# MAGIC %md
# MAGIC ## Shared set-up for the build notebooks
# MAGIC
# MAGIC Every step notebook runs this with `%run ./_common`. It finds the repo folder, defines the common widgets
# MAGIC (catalog, SQL warehouse, scale, as-of date, seed) and provides `run_script()`, which runs one of the repo's
# MAGIC build scripts in this notebook's Python process, exactly as `python <script> <args>` would on a laptop.

# COMMAND ----------

import os
import runpy
import sys
import time
from pathlib import Path


def _repo_root() -> Path:
    """The repo folder (a Git folder or an imported copy) that holds this notebooks/ folder."""
    def is_repo(p: Path) -> bool:
        return (p / "src" / "smbc_genie_lib").is_dir() and (p / "genie").is_dir()

    here = Path(os.getcwd())
    for p in (here, *here.parents):
        if is_repo(p):
            return p
    nb = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
    for p in Path("/Workspace" + nb).parents:
        if is_repo(p):
            return p
    raise RuntimeError(f"cannot find the repo folder (src/smbc_genie_lib) above {here} or {nb}")


REPO = _repo_root()
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

dbutils.widgets.text("catalog", "smbc_genie", "Catalog (keep smbc_genie)")
dbutils.widgets.text("warehouse_id", "", "SQL warehouse id")
dbutils.widgets.text("scale", "0.1", "Scale (0.1 demo size, 1.0 full)")
dbutils.widgets.text("as_of", "2026-09-30", "As-of date")
dbutils.widgets.text("seed", "20260930", "Random seed")


def widget(name: str, default: str = "") -> str:
    try:
        value = dbutils.widgets.get(name)
    except Exception:  # widget not defined in this notebook
        return default
    return value.strip() if value and value.strip() else default


CATALOG = widget("catalog", "smbc_genie")
WAREHOUSE_ID = widget("warehouse_id")
SCALE, AS_OF, SEED = widget("scale", "0.1"), widget("as_of", "2026-09-30"), widget("seed", "20260930")

from smbc_genie_lib.config import export_env

# every script (and every load_config() inside it) sees the widget values as SMBC_* settings
export_env(catalog=CATALOG, warehouse_id=WAREHOUSE_ID, scale=SCALE, as_of_date=AS_OF, random_seed=SEED)


def need_warehouse() -> str:
    """The warehouse_id widget; when it is empty, stop and list the SQL warehouses you can see."""
    if WAREHOUSE_ID:
        return WAREHOUSE_ID
    from databricks.sdk import WorkspaceClient

    seen = [f"  {w.id}  {w.name}  ({w.warehouse_type}, {w.state})" for w in WorkspaceClient().warehouses.list()]
    raise ValueError("Set the warehouse_id widget to a serverless or pro SQL warehouse. Warehouses you can see:\n"
                     + ("\n".join(seen) or "  (none)"))


def run_script(rel_path: str, *args: str) -> None:
    """Run one build script of the repo in this process; raise when it exits non-zero."""
    script = REPO / rel_path
    print(f"=== {rel_path} {' '.join(args)}", flush=True)
    saved, t0, code = sys.argv, time.time(), 0
    sys.argv = [str(script), *args]
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    finally:
        sys.argv = saved
    print(f"=== {rel_path}: exit {code} after {time.time() - t0:.0f} s", flush=True)
    if code:
        raise RuntimeError(f"{rel_path} failed with exit code {code}; see the output above")


print(f"repo {REPO} | catalog {CATALOG} | warehouse {WAREHOUSE_ID or '(not set)'} | scale {SCALE} | as-of {AS_OF}")
