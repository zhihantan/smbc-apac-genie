# Databricks notebook source
# MAGIC %md
# MAGIC # 04 · Gold: the Customer 360 star schema
# MAGIC
# MAGIC Builds `gold` from the specs in `src/30_gold/specs/` and their SQL in `src/30_gold/sql/`: the client hub
# MAGIC (`dim_client` with SCD2 history keyed on the golden client, `dim_client_group`, accounts, products, calendar,
# MAGIC thresholds and more), about 70 facts and metric-view bases, and `vw_client_360`. Every table gets typed
# MAGIC columns, a comment on every column, primary and foreign keys and clustering; the run verifies keys, foreign-key
# MAGIC coverage and the SCD2 lookups. About 10–12 minutes on the SQL warehouse.
# MAGIC
# MAGIC **Needs:** notebook 03 and a SQL warehouse in the `warehouse_id` widget.

# COMMAND ----------

# MAGIC %pip install -q "databricks-sdk>=0.145" "pyyaml>=5.4"

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %run ./_common

# COMMAND ----------

run_script("src/30_gold/run_gold.py", "--catalog", CATALOG, "--warehouse-id", need_warehouse())
