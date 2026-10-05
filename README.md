# SMBC APAC Genie — Customer 360 + AI/BI Genie Agents

A demo-grade **Customer 360 data foundation** for SMBC Singapore (synthetic data only — no real
clients, people or figures) and a family of **11 AI/BI Genie Agents** built on it: RM/Credit Workbench
(account planning, opportunity ID, credit memo & spreading, early warning, profitability, onboarding),
Transaction Banking (cash/payments/liquidity, trade & SCF), and the AI-enabled + data-foundation agents.

> **Illustrative demo.** Every client, person and figure is synthetic, generated from a fixed seed. No SMBC data
> is used, and the project is not affiliated with or endorsed by SMBC.

Built from [`SMBC_APAC_Genie_Master_Prompt_v2_Customer360.md`](SMBC_APAC_Genie_Master_Prompt_v2_Customer360.md) (the brief).
The curated agents are in [`genie/`](genie/README.md): one folder per agent, with its definition (`space.yaml`),
trusted SQL functions (`functions.sql`) and the exact API payload (`<slug>.geniespace.json`).

## Status

**The data foundation and the 11 Genie Agents (formerly Genie spaces) are built, checked and handed over** in
catalog `smbc_genie` in a demo workspace (SCALE 0.1, as-of 2026-09-30): 213 of 216 Genie
benchmarks pass (98.6%), and all 88 starter questions were asked live (75 match their benchmark exactly; 13 answer
with a different scope, noted in the question bank).
The data-asset and data-quality report is [docs/DATA_ASSET_REPORT.md](docs/DATA_ASSET_REPORT.md).
See [docs/PLAN.md](docs/PLAN.md) for the plan and [docs/DECISIONS.md](docs/DECISIONS.md) for the decision log.

## Handover documents

| Document | For |
|---|---|
| [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) | The 30-minute demo (Genie One, then the agents) plus 5-minute modules; every question verified live, with the numbers to expect |
| [docs/GENIE_QUESTIONS.md](docs/GENIE_QUESTIONS.md) | Every question each agent answers: starter questions, tested benchmarks and their variants, answers, follow-ups ([CSV](docs/genie_questions.csv)) |
| [docs/slides/SMBC_APAC_Genie_deck.md](docs/slides/SMBC_APAC_Genie_deck.md) | Slide source for Claude Design: Genie Agents + Genie One for SMBC (22 slides + appendix) |
| [docs/GENIE_RUNBOOK.md](docs/GENIE_RUNBOOK.md) | Building, changing, evaluating and (when the owner decides) sharing the agents |
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) · [docs/ENTITY_RESOLUTION.md](docs/ENTITY_RESOLUTION.md) | The Customer 360 model and how 9,270 source records become 2,552 golden clients |
| [docs/TEARDOWN.md](docs/TEARDOWN.md) | Removing the demo (`make teardown` is a read-only dry run) |

| Phase | What | State |
|---|---|---|
| 0–1 | Discovery, plan, scaffold | ✅ done |
| 2 | Catalog, schemas, grants, tags, smoke test | ✅ done |
| 3 | Bronze + shared: synthetic sources, 13 storylines (62/62 storyline checks) | ✅ done |
| 4 | Silver: standardisation, entity resolution (P 0.976 / R 0.984), 526 DQ rules | ✅ done |
| 5 | Gold: hub (22 objects) + 73 facts / bases + `vw_client_360` | ✅ done |
| 6 | 43 metric views + must-answer queries per space (`metrics/`) | ✅ done |
| 7 | 11 Genie spaces (`genie/<slug>/`, `src/50_genie/run_genie.py`): 8 at 100%, 3 at 95% | ✅ done (not shared yet) |
| 8 | Data-asset and data-quality check (`src/60_validate/run_validate.py`) | ✅ done |
| 9 | Handover: demo script, question bank, slide source, runbook, data model, teardown | ✅ done |

## Layout

```
databricks.yml            Declarative Automation Bundle (CLI >= 1.19; validates on 0.299.x)
resources/jobs.yml        Serverless phase jobs + build_all orchestrator (no Genie job: agents are changed from a laptop, D50)
config/smbc_genie.yaml     Fill-ins, volumes, calibration, storyline switches
config/column_dictionary.yaml  (phase 5) single source of column comments
notebooks/                 Databricks notebooks that run the whole build in a workspace (00_run_all, 01-07)
src/smbc_genie_lib/        Importable library (config, fiscal, rng, naming, er, sql_runner) -> wheel
src/00_setup .. 60_validate/  Phase entry scripts + SQL
genie/<slug>/              Per-agent spec, instructions, benchmarks, <slug>.geniespace.json, question_bank.json
tests/unit/                pytest, no workspace needed (live checks run inside the phase runners and the audit)
docs/                      PLAN, DECISIONS, DATA_ASSET_REPORT + the handover documents above
scripts/                   discover.sh (Phase 0 read-only checks), teardown.py (dry run unless --execute)
```

## Run it in Databricks (notebooks)

1. **Workspace → Create → Git folder**, with this repository's URL.
2. Open `notebooks/00_run_all`, attach serverless compute, and fill in `warehouse_id` (a serverless or pro SQL
   warehouse).
3. **Run all.** That builds the catalog `smbc_genie`, all data assets, the 43 metric views and the 11 Genie Agents
   in about 1½–2 hours at scale 0.1. Set `genie_mode = create_and_evaluate` to also grade the agents' 216
   benchmark questions.

What you need and what each notebook builds: [notebooks/README.md](notebooks/README.md).

## Local setup

```bash
make venv        # uv venv + install dev deps
make test        # 649 unit tests, no workspace needed
make validate    # bundle validate (needs a logged-in profile)
```

## Run it from a laptop

```bash
# Authenticate (interactive SSO):
databricks auth login --host https://my-workspace.cloud.databricks.com --profile my-workspace
make discover PROFILE=my-workspace     # optional read-only checks
```

The phases run from a laptop (Databricks Connect serverless + the SQL warehouse), in this order:

```bash
P=my-workspace
W=my-warehouse-id                                                  # your SQL warehouse id
.venv/bin/python src/00_setup/run_setup.py --warehouse-id $W --profile $P   # catalog, schemas, ops tables, grants, tags
.venv/bin/python src/10_bronze_synth/run_bronze.py   --profile $P   # bronze + shared, ER, 62 storyline checks
.venv/bin/python src/20_silver/run_silver.py         --profile $P   # silver + DQ (gates: reconcile, golden keys)
.venv/bin/python src/30_gold/run_gold.py      --warehouse-id $W --profile $P   # gold hub + facts (PK / FK / SCD2 / comment gates)
.venv/bin/python src/40_metrics/run_metrics.py --warehouse-id $W --profile $P   # 43 metric views + must-answer queries
.venv/bin/python src/50_genie/run_genie.py --create --evaluate --warehouse-id $W --profile $P   # 11 Genie Agents + benchmarks
.venv/bin/python src/60_validate/run_validate.py --warehouse-id $W --profile $P   # audit -> docs/DATA_ASSET_REPORT.md
.venv/bin/python src/50_genie/build_question_bank.py --live --warehouse-id $W --profile $P   # re-capture docs/GENIE_QUESTIONS.md (optional)
```

The SQL-warehouse steps take `--warehouse-id`, or `SMBC_WAREHOUSE_ID`, or `warehouse_id` in
`config/smbc_genie.yaml`. Idempotent throughout (`IF NOT EXISTS`, `CREATE OR REPLACE`, `INSERT OVERWRITE`/`MERGE`).
Teardown is a separate, confirmation-gated step ([docs/TEARDOWN.md](docs/TEARDOWN.md)). The build never drops the catalog.

The bundle (`make deploy PROFILE=… WAREHOUSE_ID=…`, then `make build-dev`) runs the same steps as serverless jobs,
except the Genie Agents (D50). It validates but has not been deployed yet.

## Conventions

- Deterministic from `random_seed` (hash-based RNG; identical config → identical output).
- Japanese fiscal year (Apr–Mar); "today" is fixed at 2026-09-30 (H1 FY2026 close).
- All figures carry a USD equivalent; synthetic names only, checked against a real-entity blocklist.
