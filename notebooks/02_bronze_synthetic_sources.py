# Databricks notebook source
# MAGIC %md
# MAGIC # 02 · Bronze and shared: synthetic source systems, storylines and entity resolution
# MAGIC
# MAGIC Generates the synthetic bank (no real clients, people or figures) from a fixed seed, so the same settings
# MAGIC always give the same data:
# MAGIC
# MAGIC - the "true" client universe in `ops.synthetic_*` (380 client groups, about 2,500 legal entities);
# MAGIC - the raw source feeds in `bronze` (core banking, payments, credit, trade, treasury, CRM, KYC, early
# MAGIC   warning, finance, cash flow, news and market data), with realistic noise such as duplicate identities,
# MAGIC   wrong countries and a payments file that lands twice;
# MAGIC - what the Japan, EMEA and Americas lakehouses would share, in `shared` (simulated OpenSharing,
# MAGIC   formerly Delta Sharing);
# MAGIC - the 13 business storylines the Genie Agents are expected to find;
# MAGIC - the six quarterly entity-resolution runs that turn about 9,300 source records into about 2,550 golden
# MAGIC   clients;
# MAGIC - the 62 storyline checks, written to `ops.storyline_assertions`.
# MAGIC
# MAGIC About 35–45 minutes at scale 0.1. Each step overwrites its own tables, so re-running is safe. The steps run in
# MAGIC this notebook's process; use `from_step` to resume after a failure, or `only_step` to re-run one step
# MAGIC (step names are in `src/10_bronze_synth/run_bronze.py`).

# COMMAND ----------

# MAGIC %pip install -q "databricks-sdk>=0.145" "pyyaml>=5.4"

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %run ./_common

# COMMAND ----------

dbutils.widgets.text("from_step", "", "Resume from step (blank = all)")
dbutils.widgets.text("only_step", "", "Run only this step (blank = all)")

args = ["--catalog", CATALOG, "--scale", SCALE, "--as-of", AS_OF, "--seed", SEED, "--in-process"]
if widget("from_step"):
    args += ["--from", widget("from_step")]
if widget("only_step"):
    args += ["--only", widget("only_step")]
run_script("src/10_bronze_synth/run_bronze.py", *args)

# COMMAND ----------

# MAGIC %md
# MAGIC The storyline checks (all 62 should pass):

# COMMAND ----------

display(spark.sql(f"""SELECT storyline_id, storyline_name, assertion_key, expected, actual, passed
                      FROM {CATALOG}.ops.storyline_assertions ORDER BY int(storyline_id), assertion_key"""))
