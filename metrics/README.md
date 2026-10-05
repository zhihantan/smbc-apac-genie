# Metric views (Phase 6): framework, how to add a view, verified syntax

The 43 Unity Catalog metric views in `smbc_genie.metrics` (PLAN §8, D14) are the main answer surface of the 11
Genie spaces. Every view is **one YAML file** here. The runner expands the shared blocks, strips runner-only
keys, creates the view, validates it, reconciles it against gold and writes the glossary.

```
metrics/
  <view>.yaml                 one spec per view (file stem = view name, mv_*)
  _blocks/calendar.yaml       standard calendar block (grain day | month | fiscal_year)
  _blocks/client.yaml         standard client block (dim_client version + nested dim_client_group)
  _blocks/client_group.yaml   group-grain equivalent (dim_client_group + its lead entity)
  _answers/<space_slug>.sql   one MEASURE() query per must-answer question (become Genie benchmarks)
  _glossary.md                GENERATED - name, definition, unit, grain, caveats
src/smbc_genie_lib/metrics.py framework (pure Python, tested by tests/unit/test_metrics.py)
src/40_metrics/run_metrics.py runner
```

Reference implementations: `mv_tb_deposits.yaml` (point-in-time windows, prior-month / prior-year offsets,
flows, CASA ratio, depositor rank) and `mv_client_profitability.yaml` (annualised SUM/SUM returns, window
MEDIAN, below-hurdle counts, a constant-key join to `gold.dim_threshold`). Copy one of them.

## How to add a view (step by step)

1. **Read the gold spec(s)** of your source in `src/30_gold/specs/<fact>.yaml`: the grain, which columns are
   point-in-time (balances, RWA, counts at a date) and which are flows, the precomputed flags / buckets, the
   primary-row flag of multi-grain facts, the PY columns. `DESCRIBE` the table if the spec is unclear. Sources
   are **gold only** (`${catalog}.gold.fact_*` / `gold.mvb_*` / gold dims). If a question needs a flag or bucket
   that gold lacks, ask for it - do not compute business rules in the view.
2. **Create `metrics/<view>.yaml`** with the runner-only header and the blocks:
   ```yaml
   view: mv_ews_scores                      # = file stem; must be one of the 43 in PLAN §8
   space: early_warning_monitoring          # space slug (brief Appendix D) - PLAN §8 assignment is checked
   comment: >-                              # -> DDL COMMENT (UC keeps this one) + YAML comment
     What the view answers, period covered, units, and the point-in-time rule if any.
   grain: one row per client per business day (gold.fact_ews_score_daily)   # appended as "Grain: ..."
   source: ${catalog}.gold.fact_ews_score_daily
   blocks:
     calendar: {date: source.score_date, grain: day, what: the score date}   # day | month | fiscal_year
     client: {key: source.golden_client_sk}                                   # or client_group: {key: source.client_group_id}
   ```
   Group-grain facts use `client_group` instead of `client`. A view without a client (e.g. DQ score per table)
   sets `client_block_exempt: <reason>`; `calendar_block_exempt: <reason>` likewise (rare).
3. **Add the view's own `dimensions` and `measures`** (each: `name`, `expr`, `comment`, `display_name`,
   `synonyms` (1-10, banker jargon), `format` - required on every measure). Use the patterns below. Fields from
   the blocks come first, so your fields may reference them by backticked name (`` `Month` ``).
4. **Add `validate:`** (`dims:` 2-3 dimensions, optional `where:`) and **at least 2 `reconcile:` entries**
   (more is better: one per measure kind - flow, window, ratio, median, prior period):
   ```yaml
   reconcile:
     - name: red_exposure_today                    # unique
       measure: Exposure in Red USD
       where: "`Date` = DATE'2026-09-30'"          # optional, on view fields
       by: [Industry]                              # optional: keyed comparison (gold SQL returns keys..., value)
       gold: >-                                    # direct SQL over gold, same filter
         SELECT ..., sum(exposure_usd) FROM ${catalog}.gold.fact_ews_score_daily WHERE ... GROUP BY 1
       tolerance: 0.0001                           # relative, default 0.01%
   ```
5. **Offline check:** `.venv/bin/python -m pytest tests/unit/test_metrics.py -q` (validates every view file)
   and `.venv/bin/python src/40_metrics/run_metrics.py --dry-run --only <view>` (prints the DDL).
6. **Build + verify:** `.venv/bin/python src/40_metrics/run_metrics.py --profile my-workspace --only <view>`
   - create, tags, validation query, all-dimensions probe, reconciliations, UC coverage (comment /
   display_name / format per column), glossary, answers, `ops.build_run_log`. Fix until `0 problems`.
7. **Answers:** add the space's must-answer questions to `metrics/_answers/<space_slug>.sql` (format below)
   and re-run with `--space <slug>`; the runner writes the `-- rows: n` lines.

While several people add views in parallel, pass `--skip-invalid` so one half-written file does not stop the
others. Never edit `_blocks/*` for one view - patch a block field per view with `overrides:` (comment,
display_name, synonyms, format only).

## View spec reference

| Key | Sent to the warehouse? | Meaning |
|---|---|---|
| `view`, `space`, `grain` | no | name (= stem), owning space slug, grain sentence (appended to the comment) |
| `comment`, `source`, `filter`, `joins`, `dimensions` (alias `fields`), `measures` | yes | metric-view YAML 1.1 (`version: 1.1` is added by the runner) |
| `blocks` | no (expanded) | `calendar: {date, grain, what, fy}`, `client: {key}`, `client_group: {key}` |
| `overrides` | no (applied) | `{<block field>: {comment, display_name, synonyms, format}}` |
| `validate`, `reconcile` | no | runner checks (above) |
| `glossary: {caveats: [...]}`, `notes`, `also_in` | no | glossary text / free notes / other spaces using the view |
| field / measure `unit`, `caveat` | no | glossary unit override / per-measure caveat |
| `client_block_exempt`, `calendar_block_exempt` | no | reason a block does not apply |

Placeholders (rendered at build): `${catalog}`, `${as_of_date}` 2026-09-30, `${as_of_month}` 2026-09-01,
`${as_of_fiscal_year}` FY2026, `${as_of_fiscal_year_no}` 2026, `${fy_start_month}` 4, `${history_start}`, and the
config thresholds (`${rorwa_hurdle}`, `${raroc_hurdle}`, `${roe_hurdle}`, `${ews_amber_min}`, `${ews_red_min}`,
`${covenant_headroom_warn}`, `${single_borrower_attention_usd}`). Prefer gold flags or a `dim_threshold` join.

What the validator rejects (offline, before anything runs): missing comment / display_name / format, more than
10 or duplicate synonyms, a `... USD` measure without `{type: currency, currency_code: USD}`, a `... %` measure
without `{type: percentage}`, names that are not unique case-insensitively across dimensions and measures, a join
alias equal to a field name, a field referencing a later field, ARRAY columns in a dimension (D31), a window order
that is not a dimension, a window measure referencing a window measure, `MEASURE()` of an undefined / later
measure, aggregates in dimensions, non-aggregating measures, `CURRENT_DATE` / `now()` (D16), `$$`, sources outside
gold, literal `smbc_genie.` (use `${catalog}`), < 2 reconciliations, unknown keys.

## The shared blocks

**Calendar** (`_blocks/calendar.yaml`) - D33: every hierarchy field is derived **from the window order field by
name**, never from the source column (the docs warn that a hierarchy on the source column breaks the order-field
link and window measures grouped by it return wrong results):

| grain | fields (in this order) | window order field |
|---|---|---|
| `day` | Date, Month (`` TRUNC(`Date`,'MM') ``), Fiscal Year, Fiscal Quarter, Fiscal Half, Is Month End, Is Latest Month | `Date` |
| `month` | Month (`TRUNC(<date>,'MM')`), Fiscal Year, Fiscal Quarter, Fiscal Half, Is Month End (always TRUE), Is Latest Month | `Month` |
| `fiscal_year` | Fiscal Year Number (`fy` param or derived from `date`), Fiscal Year, Is Latest Fiscal Year | `Fiscal Year Number` |

Labels match `gold.dim_date`: `FY2026`, `FY2026-Q2`, `FY2026-H1`; Month is a DATE (Sep 2026 = `DATE'2026-09-01'`);
Is Latest Month = Sep 2026 (from config, never CURRENT_DATE).

**Client** (`_blocks/client.yaml`) - joins `dc` = `gold.dim_client` on the fact's `golden_client_sk` (the SCD2
version valid on the row's date, D11) with `dc.dg` = `gold.dim_client_group` nested. Fields: Client Group, Client,
Segment, Relationship Tier, Is Japanese Corporate, Industry, Subsector, Coverage Office, Primary RM, Primary RM
Office (where the RM sits - "RMs in Singapore"), Rating Grade, IFRS 9 Stage, Watchlist, KYC Risk Rating, EWS Band. Your fields / measures may use `dc.*` and `dc.dg.*` (e.g.
`dc.primary_rm_office`, `dc.dg.is_strategic_group`) - never `dc.aliases` / `dc.source_systems_present` (ARRAY).

**Client group** (`_blocks/client_group.yaml`) - joins `dg` = `gold.dim_client_group` on `client_group_id`, with
`dg.lead` = the lead entity's current `dim_client` version. Same field names (Client becomes Lead Client) with
group-level values: group segment / tier / industry, lead office, lead RM and its office, worst rating / stage /
band, any client on the watchlist, lead entity's KYC risk.

## Measure patterns (all verified on this warehouse)

```yaml
# Flow (revenue, volume, counts of events): plain SUM / COUNT - adds up over any period
- {name: Total Revenue USD, expr: SUM(source.total_revenue_usd), format: &usd {type: currency, currency_code: USD,
   decimal_places: {type: max, places: 0}, abbreviation: compact}, ...}

# Point-in-time (balance, exposure, RWA at a date, score, headcount) - D32: latest period of the selection
- name: End of Month Balance USD
  expr: SUM(source.balance_usd)
  window: &eom [{order: Month, range: current, semiadditive: last}]      # daily views: order: Date

# Ratio: SUM/SUM inside one measure, or MEASURE()/MEASURE() of measures defined ABOVE (windows allowed)
- {name: CASA Ratio %, expr: MEASURE(`CASA Balance USD`) / NULLIF(MEASURE(`End of Month Balance USD`), 0), format: {type: percentage}}
- {name: Top-10 Depositor Concentration %, expr: "SUM(CASE WHEN source.is_top10 THEN source.balance_usd ELSE 0 END) / NULLIF(SUM(source.balance_usd), 0)", window: *eom}

# Prior period of a point-in-time measure + change / growth (compose with MEASURE)
- {name: CASA Balance Prior Month USD, expr: SUM(source.casa_balance_usd),
   window: [{order: Month, range: current, semiadditive: last, offset: -1 month}]}     # -12 month = prior year
- {name: CASA Change vs Prior Month USD, expr: MEASURE(`CASA Balance USD`) - MEASURE(`CASA Balance Prior Month USD`)}

# Movement over any run of months, when gold has a month-on-month change column: a flow
- {name: CASA Movement USD, expr: SUM(CASE WHEN source.is_casa THEN source.balance_change_mom_usd ELSE 0 END)}

# Annualised returns at any grain (PLAN §8): 12 x SUM(monthly return) / SUM(monthly balance)
- {name: RoRWA %, expr: 12 * SUM(source.relationship_net_profit_usd) / NULLIF(SUM(source.rwa_usd), 0)}
- {name: Average RWA USD, expr: SUM(source.rwa_usd) / NULLIF(COUNT(DISTINCT source.month), 0)}

# Distribution across clients: MEDIAN / PERCENTILE over primary rows; as a window = latest period
- name: Median Client RoRWA %
  expr: MEDIAN(CASE WHEN source.is_primary_row AND source.has_rwa_in_quarter THEN source.rorwa_fq END)
  window: *eom
- {name: P90 Days to Live, expr: "PERCENTILE(CASE WHEN source.is_primary_row THEN source.days_to_live END, 0.9)"}   # exact; not PERCENTILE_APPROX

# Distinct / filtered counts
- {name: Depositors, expr: COUNT(DISTINCT CASE WHEN source.balance_usd > 0 THEN source.golden_client_id END), window: *eom}
- {name: Open Signals, expr: COUNT(1) FILTER (WHERE source.status = 'Open')}

# Fiscal YTD of a flow (cumulative within the fiscal year)
- name: Revenue FYTD USD
  expr: SUM(source.total_revenue_usd)
  window: [{order: Month, range: cumulative, semiadditive: last}, {order: Fiscal Year, range: current, semiadditive: last}]

# Rolling: trailing N <unit> is EXCLUSIVE of the anchor unless you add `inclusive`
- {name: Balance 30D Average USD, expr: SUM(source.balance_usd) / 30, window: [{order: Date, range: trailing 30 day inclusive, semiadditive: last}]}

# Threshold from gold.dim_threshold (one row) via a constant-key join
joins: [{name: thr, source: "${catalog}.gold.dim_threshold", "on": "thr.threshold_code = 'RORWA_HURDLE'"}]
- {name: RoRWA Hurdle %, expr: MAX(thr.threshold_value)}
- {name: RoRWA Gap to Hurdle pts, expr: MEASURE(`RoRWA %`) - MEASURE(`RoRWA Hurdle %`)}
```

Formats: currency `{type: currency, currency_code: USD, decimal_places: {type: max, places: 0}, abbreviation: compact}`;
percentage `{type: percentage, decimal_places: {type: max, places: 1}}` (values are FRACTIONS: 0.25 shows as 25%);
counts `{type: number, decimal_places: {type: exact, places: 0}}`; dates on a dimension
`{type: date, date_format: year_month_day}`. YAML anchors (`&usd` / `*usd`) are fine - the runner writes the YAML out
in full. Names: human-readable, ASCII (letters, digits, space, `% & ( ) / + . -`); end money measures with `USD`,
ratios with `%`.

## Verified syntax matrix (DBSQL 2026.38, 2026-10-04)

Probes (local working files, `scratch/` is git-ignored): `scratch/wp9a_probe.py` (59 checks), `wp9a_probe2.py`
(11, filter interactions), `wp9a_probe3.py` (11, threshold joins / medians), `wp9a_block_probe.py` (the real blocks
at all 3 grains) - 81 + 3 checks, each compared with an exact expected value on synthetic data (e.g. month-end
CASA 660 vs a 21-month sum; FYTD 67.5; trailing-3M inclusive 1971 vs exclusive 1962); all throw-away
`metrics._wp9a_*` objects were dropped. The YAML below is the exact syntax that passed.

| # | Feature | Result |
|---|---|---|
| 1 | `window: [{order: Month, range: current, semiadditive: last}]` | WORKS: by Month = that month; no grouping = latest month (not the sum); grouped by another field = summed across its members at the last month |
| 2 | Hierarchy (`Fiscal Year` / `Fiscal Quarter`) derived from the order field by name: `` CONCAT('FY', CASE WHEN MONTH(`Month`) >= 4 THEN YEAR(`Month`) ELSE YEAR(`Month`) - 1 END) `` | WORKS: point-in-time grouped by FY / quarter = its last month. (Same formula on the source column also returned correct values here, but the docs warn it breaks - do not use.) |
| 3 | Daily chain `Date` -> `` TRUNC(`Date`,'MM') `` -> Fiscal Quarter, window ordered by Date | WORKS: by Month = last day of the month; by quarter = last day |
| 4 | `offset: -1 month` (range current) | WORKS: previous month-end; NULL for the first month |
| 5 | `range: trailing 1 month` / `trailing 1 day` (exclusive by default) | WORKS: previous month / previous day |
| 6 | `offset: -12 month` + `(MEASURE(a) - MEASURE(py)) / NULLIF(MEASURE(py), 0)` | WORKS: YoY of a point-in-time measure |
| 7 | `offset: -1` on an INT order field (Fiscal Year Number, dense month index) | WORKS: prior fiscal year total of a flow |
| 8 | `range: trailing 3 month inclusive` / `trailing 3 month` | WORKS: inclusive includes the anchor month, default excludes it |
| 9 | `range: cumulative`; FYTD = `[{order: Month, range: cumulative, semiadditive: last}, {order: Fiscal Year, range: current, semiadditive: last}]` | WORKS: running total; FYTD resets in April |
| 10 | Ratio of two window measures `MEASURE(a)/MEASURE(b)`; ratio inside one window measure; window measure whose expr uses `MEASURE()` of a PLAIN measure; `MEASURE(window) - MEASURE(window offset)` | WORKS |
| 11 | `FILTER (WHERE ...)` and `CASE` in plain and window measures | WORKS |
| 12 | `COUNT(DISTINCT ...)` plain / window / over a joined column | WORKS |
| 13 | `MEDIAN`, `PERCENTILE(x, 0.9)` (exact, interpolated), `PERCENTILE_APPROX` (nearest rank: 320 where exact gives 300), MEDIAN in a window, MEDIAN of a row-level ratio | WORKS - use PERCENTILE for P90 |
| 14 | `format` currency USD (`decimal_places` exact/max, `abbreviation: compact`, `hide_group_separator`), number, percentage, `date` on a dimension | WORKS - stored in UC column metadata (`DESCRIBE TABLE EXTENDED ... AS JSON`) |
| 15 | `display_name`, `synonyms` (<= 10), `comment` | WORKS - comment in `information_schema.columns`, display_name / synonyms / format in the column metadata |
| 16 | `%` in names (`CASA Ratio %`) | WORKS |
| 17 | Nested joins 3 levels (dim_client -> dim_client_group -> dim_country) + a sibling nested join (dim_client -> dim_booking_entity); measures over level-3 columns | WORKS |
| 18 | Join source with ARRAY columns (gold.dim_client) | WORKS (D31: just never expose the arrays) |
| 19 | SQL-query `source:`; SQL-query join source with `"on": "TRUE"`; constant-key join `"on": "thr.threshold_code = 'RORWA_HURDLE'"`; `using: [col]` | WORKS |
| 20 | Fields referencing earlier fields by backticked name | WORKS |
| 21 | Query shapes: `ORDER BY ... LIMIT` and `HAVING MEASURE(x) > 0` directly on a metric view; CTE wrapping + join of two metric-view queries (D35) | WORKS |
| 22 | `ALTER VIEW ... SET TAGS` on a metric view | WORKS |
| 23 | DDL `COMMENT '...'` vs YAML `comment` | the DDL COMMENT wins (UC rewrites the stored YAML comment) - the runner sends the same text to both |
| 24 | WHERE on the order field or a field derived from it (`Fiscal Year`, `Is Latest Month`) with offset / cumulative windows | WORKS: applied after the window (Apr-2026 prior month still = Mar-2026 under `Fiscal Year = 'FY2026'`) |
| 25 | WHERE on other fields (client, country) with windows | filters rows first (population semantics) - as intended |
| 26 | `semiadditive: last` when grouping by a non-order field and not filtering a month | last month PER GROUP (an account that stopped in Jun shows its Jun balance) - filter a Month for point-in-time questions |
| 27 | Window measure referencing another window measure (`MEASURE(window)` inside a window) | FAILS: `METRIC_VIEW_WINDOW_MEASURE_REFERENCES_WINDOW_MEASURE` - compose in a non-window measure instead |
| 28 | Same name for a dimension and a measure (case-insensitive) | FAILS: `Measure and dimension names must be unique` |
| 29 | `format: {type: percent}` | FAILS: parse error - it is `percentage` |
| 30 | 11 synonyms | FAILS: `INVALID_PARAMETER_VALUE` (max 10) |
| 31 | A field named like a join alias (field `Client`, join `client`) | FAILS for every later field: `INVALID_EXTRACT_BASE_FIELD_TYPE` on `client.segment` - the blocks use aliases `dc` / `dg` |
| 32 | WHERE on a field the order field is derived FROM (filter `Month` when the window orders by a month index built from Month) | prior-period value becomes NULL - order and filter on the same field |

Also proven in the Phase-2 smoke test (`src/00_setup/run_smoke.py`): `MEASURE()` inside a SQL table function,
metric views as Genie `data_sources.tables`.

## Runner

```
.venv/bin/python src/40_metrics/run_metrics.py --profile my-workspace
    [--only mv_a,mv_b] [--space <slug>] [--list] [--dry-run] [--parallel 4]
    [--skip-reconcile] [--skip-answers] [--no-glossary] [--skip-invalid] [--strict]
```
- `--list`: the 43 planned views, spec status, sources. `--dry-run`: rendered DDL + validation SQL, writes the
  glossary, touches no workspace.
- Views whose gold sources do not exist yet are **pending** (skipped, logged). Exit 0 ok, 1 any failure (create,
  validation, dimension probe, reconciliation, coverage, answer), 2 spec error, 3 pending with `--strict`.
- Every step goes to `ops.build_run_log` (phase `p06_metrics`; steps create / validate / validate_dims / reconcile /
  coverage / glossary / answer).
- `CREATE OR REPLACE` drops object grants - SELECT is granted at schema level (D34), so nothing to re-grant.

## Answers files (`_answers/<space_slug>.sql`)

```sql
-- Q3: CASA ratio trend, monthly, Japanese vs non-Japanese corporates since FY2025.   <- required header, unique id
-- views: mv_tb_deposits                                                             <- optional note
-- rows: 36                                                                          <- written by the runner
SELECT `Month`, MEASURE(`CASA Ratio %`) AS casa_ratio FROM smbc_genie.metrics.mv_tb_deposits
WHERE `Month` >= DATE'2025-04-01' GROUP BY ALL ORDER BY `Month`;
```
One statement per question (variants as `Q3b`); name every output column; no CURRENT_DATE; reference views as
`smbc_genie.metrics.<view>` (or `${catalog}.metrics.<view>`). Cross-view questions: one CTE per view, then join
(D35). Queries whose views are not built yet are skipped.

## Gotchas

- Point-in-time questions must filter a Month / Date (or use Is Latest Month): see row 26.
- Order and filter on the same calendar field family: block fields are all derived from the order field, so
  filters on Month / Fiscal Year / Quarter / Half / Is Latest Month are window-safe.
- Quote YAML values that start with a backtick or contain `: ` (the loader repairs an unquoted `on:` key, which
  PyYAML would read as `true`).
- Multi-grain facts: count clients and read client-level values on the primary row (`is_primary_row`).
- RWA, capital and other balances in flow-grain facts are monthly snapshots: average them
  (`SUM / COUNT(DISTINCT month)`) or use them inside annualised ratios - never sum them as amounts.
