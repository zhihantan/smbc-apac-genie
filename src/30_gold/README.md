# Gold layer (Phase 5) — framework and hub

Customer 360 star schema in `smbc_genie.gold`: the hub dimensions (WP8b) and the domain facts
(WP8c / WP8d / WP8e) are all declared the same way and built by one runner. Framework code:
`src/smbc_genie_lib/gold.py` (pure Python, tested by `tests/unit/test_gold.py`). Decisions: D11
(SCD2 as-was), D12 (group grain), D13 (multi-grain), D16 (as-of, never `CURRENT_DATE`), D25 (comments),
D29 (thresholds), D31 (arrays), D38 (explicit DDL then `INSERT OVERWRITE`).

## Layout: one YAML spec + one SQL file per object

```
src/30_gold/
  run_gold.py            the runner (DDL -> load -> ANALYZE -> tags -> verify -> ops.build_run_log)
  specs/_columns.yaml    shared gold column comments (golden_client_sk, client_group_id, month, ...)
  specs/<table>.yaml     columns (type + comment), PK, FKs, CLUSTER BY, checks, reconciliations
  sql/<table>.sql        ONE SELECT / WITH query yielding the spec's columns by name (any order)
```

For each table the runner renders `CREATE OR REPLACE TABLE <t> (<col> <TYPE> [NOT NULL] COMMENT '...',
CONSTRAINT pk_<t> PRIMARY KEY (...) RELY, CONSTRAINT fk_<t>_<cols> FOREIGN KEY (...) REFERENCES ... NOT
ENFORCED RELY) CLUSTER BY (...) COMMENT '...'`, then `INSERT OVERWRITE <t> SELECT <spec cols> FROM
(<your SQL>)`, `ANALYZE TABLE ... FOR ALL COLUMNS` and `smbc_layer / smbc_domain / smbc_synthetic` tags.
Build order comes from the `${catalog}.gold.<x>` tables and functions your SQL reads (plus FK targets
and `depends_on`); independent tables build in parallel.

### Adding a table (WP8c / d / e)

1. `specs/fact_x.yaml` (file stem = `table`):

```yaml
table: fact_deposit_balance_monthly
domain: tb                          # your --domain
comment: >-
  Month-end deposit balances per account ... (what an RM / analyst needs to know; point-in-time = never sum months)
grain: one row per account per month-end
primary_key: [account_id, balance_date]
foreign_keys:                       # target columns default to the target's PK
  - {columns: [golden_client_sk], references: dim_client}
  - {columns: [account_id], references: dim_account}
  - {columns: [balance_date], references: dim_date}
  - {columns: [currency], references: dim_currency}
cluster_by: [balance_date, golden_client_sk]      # <= 4 columns or AUTO
columns:
  account_id: STRING                              # comment from specs/_columns.yaml or config/column_dictionary.yaml
  balance_date: {type: DATE, comment: Month-end date of the snapshot.}
  golden_client_sk: BIGINT
  golden_client_id: {type: STRING, not_null: true}
  balance_usd: {type: DOUBLE, comment: "Month-end balance in USD (point-in-time, do not sum across months)."}
checks:                                           # optional: SQL returning one number of violations (<= max, default 0)
  - {name: no_negative_balances, sql: "SELECT count_if(balance_usd < 0) FROM ${table}"}
reconcile:                                        # optional: gold (default count(*) of the table) vs silver, |diff| <= tolerance
  - {name: rows_vs_silver, silver: "SELECT count(*) FROM ${catalog}.silver.core_deposit_balance_monthly"}
```

2. `sql/fact_x.sql`: a single query over **silver (+ gold) only** — never bronze, shared or ops.
3. `.venv/bin/python src/30_gold/run_gold.py --profile my-workspace --only fact_x` (or `--domain tb`),
   then `.venv/bin/python -m pytest tests/unit/test_gold.py -q` (it loads and validates every spec).

Placeholders: `${catalog}`, `${as_of_date}` (2026-09-30), `${history_start}` (2023-04-01),
`${calendar_end}` (2027-03-31), `${open_end}` (9999-12-31), `${fy_start_month}`, `${scale}`, `${seed}`
and every config threshold (`${rorwa_hurdle}`, `${raroc_hurdle}`, `${roe_hurdle}`,
`${single_borrower_attention_usd}`, `${ews_amber_min}`, `${ews_red_min}`, `${covenant_headroom_warn}`);
checks also get `${table}`. An unknown placeholder fails the build.

Comments: column comment = spec > `specs/_columns.yaml` > `config/column_dictionary.yaml`; a table or
column without one fails before anything runs, and an `information_schema` gate re-checks after the build.
The config dictionary is written for silver (e.g. its `run_id` is the silver DQ run): give gold columns their
own comment unless the shared wording fits (`--dry-run` shows the resolved COMMENTs).

Gotchas the loader rejects with a clear error:
- an unquoted comment containing a comma inside `{flow: mapping}` (YAML splits it) — quote it;
- `'it''s'` in SQL: Databricks reads two adjacent literals (`its`) — write `'it\'s'`;
- `CURRENT_DATE()` / `now()` — use `DATE'${as_of_date}'` (D16);
- more than one statement in a SQL body.

## Keys every fact uses

- `golden_client_sk` BIGINT = the dim_client version valid on the fact date, plus `golden_client_id`
  on every client-grain fact; group-grain facts carry `client_group_id` + `lead_golden_client_sk`.
- Date keys are DATE columns FK to `dim_date.date` (window 2023-04-01..2027-03-31; only FK the grain date).
- Money: `<x>_usd` everywhere, plus `<x>_lcy` + `currency` where the source has local currency
  (`gold.fx_rate_daily` is dense by currency x day; `gold.fn_usd(amount, currency, date)` wraps it).
- Product names: join `dim_product ON product_name = <name>` for `product_id`; RM codes: join
  `dim_employee ON rm_code = <code>` for `employee_id`; accounts: `dim_account.account_id`.

### SCD2 as-was lookup (D11) — copy this into fact SQL

```sql
-- golden_client_sk of the version valid on the fact date; dates before the client's first version
-- resolve to version 1, dates after the as-of date to the current (open-ended) version
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = f.golden_client_id
  AND f.balance_date <= dc.valid_to
  AND (f.balance_date >= dc.valid_from OR dc.version_no = 1)
-- SELECT dc.golden_client_sk, f.golden_client_id, ...
```

Group-grain facts (lead entity's version on the fact date):

```sql
LEFT JOIN ${catalog}.gold.dim_client_group dg ON dg.client_group_id = f.client_group_id
LEFT JOIN ${catalog}.gold.dim_client dl
  ON  dl.golden_client_id = dg.lead_golden_client_id
  AND f.month <= dl.valid_to
  AND (f.month >= dl.valid_from OR dl.version_no = 1)
-- SELECT dl.golden_client_sk AS lead_golden_client_sk, f.client_group_id, ...
```

Undated rows (masters, current snapshots) take the current version: `... AND dc.is_current`.
`gold.client_sk_join()` / `gold.group_lead_sk_join()` return these snippets. Versions have calendar-month
validity (`valid_from` = 1st of a month, `valid_to` = month-end, current = 9999-12-31): the attributes of a
version are the month-end states, so a month-end snapshot sees that month-end's rating / band / watchlist.
`golden_client_sk` = numeric part of `golden_client_id` x 1000 + `version_no` (stable across re-runs).

## The hub (WP8b)

| Table | Grain / PK | Built from (silver) |
|---|---|---|
| dim_date | day `date` (2023-04-01..2027-03-31, JP fiscal fields, month-end / business-day / as-of / FYTD flags) | generated |
| dim_client | golden client version `golden_client_sk` (SCD2) | ER golden identity + CRM, credit, EWS, KYC, ext rating, finance feeds |
| dim_client_group | group `client_group_id` (as of 30-Sep-2026) | dim_client current + Tokyo HO master + shares + ext rating |
| xref_client_source | source record `(source_system, source_id)` | xref_client_source + client_source_record |
| dim_contact | `contact_id` | crm_contact + crm_activity |
| dim_account | `account_id` | core_account |
| dim_booking_entity / dim_country | `booking_entity_id` (13 APAC + JP/EMEA/AMER) / `country_code` | ref_booking_entity, ref_country + static ISO list |
| dim_currency / fx_rate_daily | `currency_code` / `(currency_code, date)` dense | ref_currency, fx_rate_daily |
| dim_product | `product_id` (business line > family > product, + CRM sales products) | static product master (checked against every silver product name) |
| dim_employee | `employee_id` (`rm_code` unique) | CRM team history + workflow staff ids |
| dim_industry / dim_peer_group | `(industry_sector, industry_subsector)` / `peer_group_id` | ref_industry_peer, ext_peer_benchmark |
| dim_signal_type | `signal_code` (CRM signals, EWS triggers, gold-derived NEWS_* / MARKET_* / EXT_RATING_* / PARENT_RATING_* / RM_NOTE_*) | crm_signal, ews_trigger_catalog |
| dim_ews_trigger | `trigger_code` | ews_trigger_catalog + ews_signal |
| dim_onboarding_stage | `stage_no` (7 stages + SLA days) | kyc_case_stage |
| dim_threshold | `threshold_code` (hurdles, USD 500m, EWS 40 / 70, stage SLAs, question cut-offs) | config/smbc_genie.yaml + dim_onboarding_stage |
| dim_competitor_bank | `bank_id` | pay_counterparty_bank, crm_wallet_estimate |
| dim_scf_programme | `programme_id` | scf_programme, scf_supplier |
| dim_account_plan_initiative | `initiative_id` (detail dim, D15) | crm_account_plan_initiative |
| fn_usd | scalar function | fx_rate_daily |

**Standard client block** (metric views, PLAN §8) from `dim_client` joined on `golden_client_sk`, nested
`dim_client_group` on `client_group_id`: Client Group = `group_name`; Client = `display_name`
(`short_name` / `legal_name` / `aliases_text` for LIKE matching); Segment = `segment`; Relationship Tier =
`relationship_tier`; Is Japanese Corporate = `is_japanese_corporate`; Industry = `industry_sector`
(+ Subsector = `industry_subsector`); Coverage Office = `coverage_office` / `coverage_office_name`;
Primary RM = `primary_rm_name` (`primary_rm_code`, `primary_rm_office`); Rating Grade =
`internal_rating_grade` (`rating_equivalent`); IFRS 9 Stage = `ifrs9_stage`; Watchlist = `watchlist_flag`;
KYC Risk Rating = `kyc_risk_rating`; EWS Band = `ews_band`. Group-grain views use `dim_client_group`
(`group_segment`, `group_relationship_tier`, `lead_office`, `lead_rm_name`, `worst_internal_rating_grade`,
`worst_ews_band`, `is_japanese_group`). Do not expose the ARRAY columns as dimensions (D31).

## Running

```
.venv/bin/python src/30_gold/run_gold.py --profile my-workspace          # everything
    [--only t1,t2] [--domain hub] [--with-deps] [--parallel 6] [--list] [--dry-run] [--skip-verify] [--strict]
    [--skip-invalid-specs]
```

- `--list` prints the waves and inputs; `--dry-run` prints the rendered SQL (no workspace needed).
- A malformed spec stops the run with the reason (exit 2); while several people add specs in parallel,
  `--skip-invalid-specs` builds everything else and lists the skipped specs. Only the selected tables
  must pass the comment gate before the build.
- Tables whose silver inputs do not exist yet are **pending** (dependents too) and the rest still build; a
  check whose inputs are missing is skipped. Re-run any time — every step is `CREATE OR REPLACE` /
  `INSERT OVERWRITE`. Exit 0 = ok, 1 = a build or verification failure, 2 = comment gate, 3 = pending with `--strict`.
- FK constraints are reconciled after every run: any declared FK missing from `information_schema` is re-added
  with `ALTER TABLE ... ADD CONSTRAINT` - every FK of the tables built in the run (incl. FKs whose target did not
  exist yet: first run, the dim_client <-> dim_client_group pair) and the FKs of other existing spec tables that
  point into a table the run re-created (Unity Catalog was observed to drop those on hub rebuilds). So
  `--only dim_client` cannot strip the facts' FKs; a re-add failure on a table outside the run is a warning only.
- Verification per table: rows >= `min_rows`, PK unique and not null, every FK value resolves, `scd2:`
  integrity (no overlap / gap, version sequence, exactly one open-ended current version), spec `checks`
  and `reconcile`; all steps are logged to `ops.build_run_log` (phase `p05_gold`).
