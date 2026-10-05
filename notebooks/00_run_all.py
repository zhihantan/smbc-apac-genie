# Databricks notebook source
# MAGIC %md
# MAGIC # 00 · Build everything, end to end
# MAGIC
# MAGIC Runs notebooks 01–07 in order, each as a child run with the widget values below. At scale 0.1 that takes
# MAGIC about 1½–2 hours, plus about 1½ hours if you also evaluate the Genie Agents. Each child run's output is linked
# MAGIC from its cell result. Every step is idempotent, so after a failure fix the cause and set `start_at` to the
# MAGIC failed step.
# MAGIC
# MAGIC | Notebook | Builds | Time at scale 0.1 |
# MAGIC |---|---|---|
# MAGIC | 01_setup_catalog | catalog, schemas, ops tables, fiscal functions, grants, tags | about 1 min |
# MAGIC | 02_bronze_synthetic_sources | synthetic sources, storylines, entity resolution, storyline checks | 35–45 min |
# MAGIC | 03_silver_data_quality | silver tables, 526 data-quality rules | 10–15 min |
# MAGIC | 04_gold_customer_360 | the gold Customer 360 star schema | 10–12 min |
# MAGIC | 05_metric_views | 43 metric views, reconciliations, must-answer queries | about 10 min |
# MAGIC | 06_genie_agents | the 11 Genie Agents (`genie_mode`) | 15–30 min; evaluation adds about 8 min per agent |
# MAGIC | 07_validate_data_assets | the read-only audit, `docs/DATA_ASSET_REPORT.md` | about 5 min |
# MAGIC
# MAGIC **Before you start:** run this on serverless compute and put a serverless or pro SQL warehouse id in
# MAGIC `warehouse_id`. Keep the catalog name `smbc_genie`. You need permission to create a catalog (or an existing,
# MAGIC empty catalog of that name that you own). `genie_mode = dry_run` only makes sense when the agents already exist.

# COMMAND ----------

dbutils.widgets.text("catalog", "smbc_genie", "Catalog (keep smbc_genie)")
dbutils.widgets.text("warehouse_id", "", "SQL warehouse id")
dbutils.widgets.text("scale", "0.1", "Scale (0.1 demo size, 1.0 full)")
dbutils.widgets.text("as_of", "2026-09-30", "As-of date")
dbutils.widgets.text("seed", "20260930", "Random seed")
dbutils.widgets.dropdown("genie_mode", "create", ["create", "create_and_evaluate", "dry_run"], "Genie Agents")
dbutils.widgets.dropdown("start_at", "01", ["01", "02", "03", "04", "05", "06", "07"], "Start at notebook")

# COMMAND ----------

import time

params = {k: dbutils.widgets.get(k).strip() for k in ("catalog", "warehouse_id", "scale", "as_of", "seed")}
if not params["warehouse_id"]:
    raise ValueError("Set the warehouse_id widget to a serverless or pro SQL warehouse first.")
genie_mode, start_at = dbutils.widgets.get("genie_mode"), dbutils.widgets.get("start_at")

STEPS = [  # (notebook, timeout in minutes)
    ("01_setup_catalog", 30),
    ("02_bronze_synthetic_sources", 180),
    ("03_silver_data_quality", 90),
    ("04_gold_customer_360", 90),
    ("05_metric_views", 90),
    ("06_genie_agents", 300),
    ("07_validate_data_assets", 60),
]
for name, minutes in STEPS:
    if name[:2] < start_at:
        continue
    args = dict(params, **({"mode": genie_mode} if name.startswith("06") else {}))
    t0 = time.time()
    print(f"{name}: started", flush=True)
    dbutils.notebook.run(f"./{name}", minutes * 60, args)
    print(f"{name}: done in {(time.time() - t0) / 60:.1f} min", flush=True)
print("All steps done. Open docs/DATA_ASSET_REPORT.md for the audit and notebook 06's last cell for the agent links.")
