# Build notebooks

These Databricks notebooks build the whole demo inside a workspace: the synthetic data assets (bronze, shared,
silver, gold), the 43 metric views and the 11 Genie Agents. Each notebook is a thin wrapper around one of the
repo's build scripts (`src/…/run_*.py`), so a notebook run and a laptop run do exactly the same work.

## What you need

- A Unity Catalog workspace with **serverless** compute for notebooks. Any serverless environment version works
  (tested on environment 1, Python 3.10). Each notebook only adds `databricks-sdk>=0.145` (and PyYAML if it is
  missing); it never upgrades numpy or pandas, because that breaks the environment's own Spark libraries.
- A **SQL warehouse** (serverless or pro) you can use. Put its id in the `warehouse_id` widget. The gold,
  metric-view, Genie and audit steps run on it, and the Genie Agents run their queries on it.
- Permission to **create a catalog** (or an existing, empty catalog named `smbc_genie` that you own). Keep the
  name `smbc_genie`: the agents' example SQL and functions name it.
- **Genie** enabled in the workspace. AI functions are optional:
  - `ai_analyze_sentiment` adds a sentiment label next to the deterministic score.
  - `ai_forecast` is tried for the cash-flow projection; without it the build uses a seasonal fallback.

## Run it

1. In the workspace: **Workspace → Create → Git folder**, and paste this repository's URL.
2. Open `notebooks/00_run_all`, attach serverless compute and fill in `warehouse_id`.
3. Choose `genie_mode`:
   - `create` builds the agents.
   - `create_and_evaluate` also grades their 216 benchmark questions (adds about 1½ hours).
4. Click **Run all**. The other steps take about 1½–2 hours at scale 0.1.

You can also run the notebooks one by one, in order. Each one starts with a short description of what it builds.

| Notebook | Builds |
|---|---|
| `01_setup_catalog` | catalog, six schemas, `ops` tables, Japanese fiscal-year functions, grants, tags |
| `02_bronze_synthetic_sources` | the synthetic source systems, regional shares, 13 storylines, entity resolution, 62 storyline checks |
| `03_silver_data_quality` | cleaned silver tables and the 526 data-quality rules |
| `04_gold_customer_360` | the gold Customer 360 star schema and `vw_client_360` |
| `05_metric_views` | the 43 metric views, with reconciliations and must-answer queries |
| `06_genie_agents` | the 11 Genie Agents from `genie/<slug>/space.yaml`. The default `mode = dry_run` changes nothing |
| `07_validate_data_assets` | the read-only audit, written to `docs/DATA_ASSET_REPORT.md` |

## Good to know

- **Deterministic.** The same widget values always produce the same data. At scale 0.1, event facts are a 10%
  sample, while clients, groups and the storyline clients are full size.
- **Safe to re-run.** Every step is idempotent (`IF NOT EXISTS`, `CREATE OR REPLACE`, `INSERT OVERWRITE`), and
  nothing is ever dropped.
- **The agents are private.** They stay visible only to you (and workspace admins) until you share them; see
  `docs/GENIE_RUNBOOK.md` §10. Notebook 01 grants read access on the data to the `consumer_group` widget
  (default `account users`).
- **Updates replace the whole agent.** Re-running notebook 06 with `create` replaces each agent's configuration
  with the repo version, so edits made in the Genie UI are lost. It also asks one smoke-test question per agent.
- **Removing everything.** Run `scripts/teardown.py` from a laptop; see `docs/TEARDOWN.md`.
