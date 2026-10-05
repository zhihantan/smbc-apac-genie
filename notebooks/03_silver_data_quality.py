# Databricks notebook source
# MAGIC %md
# MAGIC # 03 · Silver: standardisation and data quality
# MAGIC
# MAGIC Cleans and types every bronze and shared table into `silver`, removes the replayed payments file, stamps each
# MAGIC client-grain row with its golden client from entity resolution, and runs the 526 data-quality rules of
# MAGIC `config/dq_rules.yaml` (results in `silver.dq_results`, history in `ops`). The run fails if a table does not
# MAGIC reconcile (bronze = silver + quarantined + de-duplicated) or a blocking rule fails. About 10–15 minutes.
# MAGIC
# MAGIC **Needs:** notebook 02.

# COMMAND ----------

# MAGIC %pip install -q "databricks-sdk>=0.145" "pyyaml>=5.4"

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %run ./_common

# COMMAND ----------

run_script("src/20_silver/run_silver.py", "--catalog", CATALOG)
