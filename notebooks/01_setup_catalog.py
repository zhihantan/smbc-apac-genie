# Databricks notebook source
# MAGIC %md
# MAGIC # 01 · Catalog, schemas, grants and tags
# MAGIC
# MAGIC Creates the catalog (`smbc_genie` by default) and its six schemas (`bronze`, `shared`, `silver`, `gold`,
# MAGIC `metrics`, `ops`), the `ops` run-log and check tables, the Japanese fiscal-year SQL functions, the
# MAGIC `smbc_*` tags and the read grants. Idempotent (`IF NOT EXISTS`); it never drops anything. About a minute.
# MAGIC
# MAGIC **Needs:** permission to create a catalog (or an existing, empty catalog of that name that you own), and a
# MAGIC SQL warehouse (serverless or pro) in the `warehouse_id` widget.
# MAGIC
# MAGIC `consumer_group` receives `USE CATALOG`, `USE SCHEMA` and `SELECT` on the catalog's schemas, plus
# MAGIC `EXECUTE` on `gold` and `metrics` functions. The default, `account users`, means everyone in the account;
# MAGIC change it to a group of your own to narrow access.

# COMMAND ----------

# MAGIC %pip install -q "databricks-sdk>=0.145" "pyyaml>=5.4"

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %run ./_common

# COMMAND ----------

dbutils.widgets.text("consumer_group", "account users", "Group granted read access")

run_script("src/00_setup/run_setup.py", "--catalog", CATALOG, "--warehouse-id", need_warehouse(),
           "--scale", SCALE, "--as-of", AS_OF, "--seed", SEED,
           "--consumer-group", widget("consumer_group", "account users"))
