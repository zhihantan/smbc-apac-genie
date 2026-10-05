# Teardown — removing the SMBC APAC Genie demo

This page explains how to remove the demo from the demo workspace: the 11 Genie Agents
(formerly Genie spaces, renamed in July 2026; the API paths still say `spaces`) titled "APAC Genie - …" and the
Unity Catalog catalog `smbc_genie`, which holds all of the synthetic data. Teardown is a separate, deliberate
step: the build never drops anything (DECISIONS D07; brief §3.6).

The tool is [`scripts/teardown.py`](../scripts/teardown.py). By default it runs a **dry run** that only reads
the workspace. It deletes nothing unless you pass `--execute` **and** type the catalog name.

> ⚠️ Dropping the catalog is not reversible from the workspace: Databricks has no `UNDROP CATALOG`. The way back
> is a rebuild from this repo (section 6), which takes about 3 hours.

## 1. What is removed and what is not

| Removed by `--execute` | How |
|---|---|
| The 11 Genie Agents tracked in `genie/<slug>/space_id` (their instructions, example SQL, benchmarks and conversations go with them) | Moved to the workspace **Trash** with `DELETE /api/2.0/genie/spaces/{space_id}` ("Move a Genie Space to the trash") |
| Catalog `smbc_genie`: schemas bronze, shared, silver, gold, metrics, ops and default, with every table, view, metric view and SQL function in them | `DROP CATALOG smbc_genie CASCADE` on warehouse `my-warehouse-id` |

| Never touched | Why it matters |
|---|---|
| Any other Genie Agent (the workspace holds about 20 from other demos) | Only ids tracked in `genie/<slug>/space_id` whose title starts with "APAC Genie - " are trashed |
| Permissions, sharing, the IP access list, workspace settings | The script has no code for them |
| SQL warehouse `my-warehouse-id` | Shared with other demos |
| The bundle (`databricks.yml`) and its jobs | Destroy it separately if deployed (section 2) |
| Local files, including `genie/<slug>/space_id` | They stay as a record; see section 6 before a rebuild |

## 2. Before you start

1. **Log in** with the build profile:
   `databricks auth login --host https://my-workspace.cloud.databricks.com --profile my-workspace`.
   If calls fail with "blocked by Databricks IP ACL", connect to the network or VPN the workspace allows. Do not
   change the IP access list.
2. **Check who runs it.** Dropping a catalog needs ownership or the `MANAGE` privilege on it
   ([DROP CATALOG](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-drop-catalog)).
   On 4 Oct 2026 the catalog owner was `you@example.com`, who also holds CAN MANAGE on the 11 agents.
3. **Destroy the bundle first if it is deployed.** The dry run reports it. On 4 Oct 2026 it was **not
   deployed**: there was no `/Workspace/Users/you@example.com/.bundle/smbc-apac-genie` folder and no
   job named `[smbc-genie] …`. If one appears later: `databricks bundle destroy -t dev -p my-workspace`.
4. **Keep what you need.** Every agent's configuration is in git (`genie/<slug>/space.yaml` and
   `<slug>.geniespace.json`), and so are the evaluation results (`eval_report.json`, `eval_history.jsonl`).
   Benchmark results in `ops.genie_benchmarks`, storyline checks in `ops.storyline_assertions` and the run log
   in `ops.build_run_log` are dropped with the catalog; `docs/DATA_ASSET_REPORT.md` keeps a summary.
5. **Note the catalog settings** the dry run prints (owner, isolation mode, storage root). You need them to
   recreate the catalog (section 6).

Use this script to remove the demo, or to start
again from an empty catalog.

## 3. Dry run (read-only)

```bash
make teardown
# same as:
.venv/bin/python scripts/teardown.py --profile my-workspace
```

The dry run makes only GET calls and `information_schema` SELECTs. It reports:

| Check | How |
|---|---|
| The 11 tracked agents | GET each id from `genie/<slug>/space_id`. **ok** = the title is exactly the expected one; **renamed** = it starts with "APAC Genie - " but differs (still ours, a warning); **foreign** = the title lacks the prefix (refused); **missing** = not found (already gone); **untracked** = no `space_id` file; **error** = the lookup failed or the file does not hold a 32-character id |
| Other agents titled "APAC Genie - …" | Listed so you can see stray copies; never touched |
| The catalog | Owner, isolation mode, storage root, then object counts per schema |
| The bundle | Its default workspace folder and any job named `[smbc-genie] …` |
| The plan | The exact, numbered steps `--execute` would run |

Result of the dry run on 4 Oct 2026 (exit code 0, nothing changed):

| Item | Result |
|---|---|
| Tracked agents | 11 of 11 **ok** (all 11 ids from section 1 of [GENIE_RUNBOOK.md](GENIE_RUNBOOK.md)) |
| Other "APAC Genie - …" agents | none |
| Catalog `smbc_genie` | owner `you@example.com`, isolation `ISOLATED` (bound to this workspace), managed storage on S3 |
| Bundle `smbc-apac-genie` | not deployed |
| Plan | 12 steps: trash the 11 agents, then `DROP CATALOG smbc_genie CASCADE` |

Objects the catalog held (dry run, 4 Oct 2026):

| Schema | Tables | Views | Metric views | Functions |
|---|---|---|---|---|
| bronze | 81 | 0 | 0 | 0 |
| default | 0 | 0 | 0 | 0 |
| gold | 93 | 1 | 0 | 40 |
| metrics | 0 | 0 | 43 | 0 |
| ops | 25 | 0 | 0 | 0 |
| shared | 11 | 0 | 0 | 0 |
| silver | 98 | 0 | 0 | 0 |
| **Total** | **308** | **1** | **43** | **40** = 392 objects |

The row counts behind these tables are in [DATA_ASSET_REPORT.md §1](DATA_ASSET_REPORT.md) (for example gold:
7,983,952 rows).

## 4. Execute

```bash
.venv/bin/python scripts/teardown.py --profile my-workspace --execute
```

What happens, in order:

1. The script runs every dry-run check again.
2. It **refuses to start** (exit 1, nothing changed) when any tracked agent is **foreign** or in **error**, when
   the catalog lookup fails, or when the catalog lacks one of the six build schemas (bronze, shared, silver,
   gold, metrics, ops). The last check stops a typo such as `--catalog main` from dropping someone else's data.
3. It prints the plan and asks: `Type the catalog name (smbc_genie) to trash the agents above and drop the
   catalog:`. Anything other than the exact name (blanks around it are ignored) stops the run with exit 2 and
   nothing changed. `yes`, `SMBC_GENIE` or an empty answer all stop it.
4. It trashes the agents one by one. If one fails, it **stops and does not drop the catalog**, so the remaining
   agents keep working. Re-run after fixing the cause; an agent that is already gone counts as done.
5. When every agent is gone, it runs `DROP CATALOG smbc_genie CASCADE`.

Exit codes: 0 done (or nothing to do), 1 refused or a step failed, 2 not confirmed.

## 5. What can be undone

| Removed | Recovery window | How |
|---|---|---|
| Genie Agents | The workspace Trash keeps items for 30 days, then deletes them permanently ([workspace objects](https://docs.databricks.com/aws/en/workspace/workspace-objects)) | Workspace → Trash → the item's menu → **Restore**. Not tried on this build: we did not test whether a restored agent keeps its id and conversations. |
| Catalog `smbc_genie` | `DROP CATALOG … CASCADE` soft-deletes the catalog; managed table and volume files are kept for 7 days, then deleted within 48 hours ([DROP CATALOG](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-drop-catalog)) | No self-service path: `UNDROP` supports tables and materialized views only and needs the parent schema and catalog to exist ([UNDROP](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-undrop-table)). Rebuild instead (section 6). |

## 6. Rebuilding after a teardown

The data is synthetic and generated from a fixed seed (D08: the same config gives the same output), so a
rebuild reproduces the same data. The one exception is the `ai_analyze_sentiment` label stored next to each
synthetic sentiment score (D18, D36), which an AI function produces. Follow the order in
[GENIE_RUNBOOK.md §11](GENIE_RUNBOOK.md) and the README ("Running the build"). Three points are specific to a
rebuild after teardown:

1. **Recreating the catalog is untested.** `src/00_setup/run_setup.py` runs `CREATE CATALOG IF NOT EXISTS
   smbc_genie` without a managed location. The metastore of this workspace has no storage root (checked 4 Oct
   2026), so that statement depends on the workspace's default storage. If it fails with a storage-location
   error, create the catalog in Catalog Explorer first, then re-run `run_setup.py` (it skips what exists). Set
   the workspace binding again if you want it `ISOLATED` like the original.
2. **Grants come back with Phase 2.** `run_setup.py` grants `USE CATALOG` and `USE SCHEMA, SELECT, EXECUTE` on
   gold and metrics to `account users` (its `--consumer-group` default), as the original build did.
3. **Decide what to do with `genie/<slug>/space_id`.** The runner updates the agent whose id is tracked there.
   After a teardown that id belongs to a trashed agent. Either restore the 11 agents from the Trash (the runner
   then updates them in place), or move the `space_id` files aside before `run_genie.py --create` so it creates
   new agents and records their new ids. The runner refuses to touch a tracked id whose title it cannot match,
   so it never overwrites the wrong agent.

## 7. Files

| File | Purpose |
|---|---|
| [`scripts/teardown.py`](../scripts/teardown.py) | Dry run and `--execute` |
| [`tests/unit/test_teardown.py`](../tests/unit/test_teardown.py) | Offline tests: ownership checks, plan order, typed confirmation, stop-on-failure, read-only dry run |
| `Makefile` target `teardown` | Runs the dry run only |
