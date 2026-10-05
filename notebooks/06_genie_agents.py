# Databricks notebook source
# MAGIC %md
# MAGIC # 06 · The 11 Genie Agents
# MAGIC
# MAGIC Builds the 11 curated Genie Agents ("APAC Genie - …") from their source files, `genie/<slug>/space.yaml`
# MAGIC and `genie/<slug>/functions.sql`: data sources and column settings, the instruction block, example SQL, trusted
# MAGIC SQL functions, sample questions and 18–20 benchmark questions each. See `genie/README.md` for the format.
# MAGIC
# MAGIC | `mode` | What it does |
# MAGIC |---|---|
# MAGIC | `dry_run` (default) | Read-only. Builds each agent, runs the metadata gate and shows the differences from the live agent. Changes nothing |
# MAGIC | `create` | Deploys each agent's `fn_*` functions, then creates the agent, or updates the one already tracked in `genie/<slug>/space_id` (or found by its exact title). It reads the result back to check it, then asks the agent's first sample question as a smoke test (that leaves one conversation per agent) |
# MAGIC | `create_and_evaluate` | `create`, then runs every benchmark through Genie's evaluation and grades it into `ops.genie_benchmarks` and `genie/<slug>/eval_report.json` (about 8 minutes per agent) |
# MAGIC | `evaluate` | Evaluation only, on the existing agents |
# MAGIC
# MAGIC The agents are never shared and their permissions are never changed: only you (and workspace admins) can open
# MAGIC them until you share them. Updating an agent replaces its whole configuration with the repo version, so edits
# MAGIC made in the Genie UI are lost.
# MAGIC
# MAGIC **Needs:** notebook 05, a SQL warehouse in the `warehouse_id` widget (the agents run their queries on it) and
# MAGIC the catalog named `smbc_genie` (the agents' example SQL names it).

# COMMAND ----------

# MAGIC %pip install -q "databricks-sdk>=0.145" "pyyaml>=5.4"

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %run ./_common

# COMMAND ----------

dbutils.widgets.dropdown("mode", "dry_run", ["dry_run", "create", "create_and_evaluate", "evaluate"], "Mode")
dbutils.widgets.text("agents", "", "Agents (blank = all 11, or slugs)")

MODES = {"dry_run": ["--dry-run"], "create": ["--create"], "create_and_evaluate": ["--create", "--evaluate"],
         "evaluate": ["--evaluate"]}
args = ["--catalog", CATALOG, "--warehouse-id", need_warehouse(), *MODES[widget("mode", "dry_run")]]
if widget("agents"):
    args += ["--only", widget("agents")]
run_script("src/50_genie/run_genie.py", *args)

# COMMAND ----------

# MAGIC %md
# MAGIC The agents in this workspace and their links:

# COMMAND ----------

from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
spaces, token = [], None
while True:
    page = w.api_client.do("GET", "/api/2.0/genie/spaces", query={"page_size": 100, **({"page_token": token} if token else {})})
    spaces += page.get("spaces") or []
    token = page.get("next_page_token")
    if not token:
        break
ours = sorted((s["title"], s["space_id"]) for s in spaces if s.get("title", "").startswith("APAC Genie - "))
displayHTML("<br>".join(f'<a href="{w.config.host}/genie/rooms/{sid}" target="_blank">{title}</a>'
                        for title, sid in ours) or "No APAC Genie agents yet: run with mode = create.")
