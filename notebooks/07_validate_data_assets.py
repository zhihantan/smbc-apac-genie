# Databricks notebook source
# MAGIC %md
# MAGIC # 07 · Data-asset and data-quality audit
# MAGIC
# MAGIC A read-only audit of everything the build created. It re-derives each figure from the workspace, not from
# MAGIC the build specs:
# MAGIC
# MAGIC - object and row counts, column comments and tags per schema;
# MAGIC - gold primary and foreign keys, with zero orphans;
# MAGIC - silver data quality;
# MAGIC - entity-resolution precision and recall;
# MAGIC - the 62 storyline checks and the 43 metric views;
# MAGIC - headline KPIs and the Genie Agents' benchmark pass rates.
# MAGIC
# MAGIC It writes `docs/DATA_ASSET_REPORT.md` in this repo folder and, unless `write_log = no`, one row per section to
# MAGIC `ops.build_run_log`. It fails when it finds an error. About 5 minutes.
# MAGIC
# MAGIC **Needs:** notebooks 01–05 (and 06 for the Genie section), and a SQL warehouse in the `warehouse_id` widget.

# COMMAND ----------

# MAGIC %pip install -q "databricks-sdk>=0.145" "pyyaml>=5.4"

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %run ./_common

# COMMAND ----------

dbutils.widgets.dropdown("write_log", "yes", ["yes", "no"], "Append to ops.build_run_log")

try:
    run_script("src/60_validate/run_validate.py", "--catalog", CATALOG, "--warehouse-id", need_warehouse(),
               *([] if widget("write_log", "yes") == "yes" else ["--no-log"]))
finally:
    report = REPO / "docs" / "DATA_ASSET_REPORT.md"
    if report.exists():
        print(f"report: {report}")
        print("\n".join(line for line in report.read_text().splitlines() if line.startswith("- ")))
