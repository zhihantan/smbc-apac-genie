# Databricks notebook source
# MAGIC %md
# MAGIC # 05 · Metric views: the governed business definitions
# MAGIC
# MAGIC Creates the 43 Unity Catalog metric views in `metrics` from the YAML in `metrics/` (CASA ratio, RoRWA, EWS
# MAGIC bands, plan attainment, forecast accuracy and the rest, each defined once). For each view it then:
# MAGIC
# MAGIC - checks that every measure answers with `MEASURE()`;
# MAGIC - reconciles key measures with gold, to within 0.01%;
# MAGIC - runs the must-answer queries of `metrics/_answers/` (the expected answers behind the Genie benchmarks);
# MAGIC - writes `metrics/_glossary.md`.
# MAGIC
# MAGIC About 10 minutes on the SQL warehouse.
# MAGIC
# MAGIC **Needs:** notebook 04 and a SQL warehouse in the `warehouse_id` widget.

# COMMAND ----------

# MAGIC %pip install -q "databricks-sdk>=0.145" "pyyaml>=5.4"

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %run ./_common

# COMMAND ----------

run_script("src/40_metrics/run_metrics.py", "--catalog", CATALOG, "--warehouse-id", need_warehouse())
