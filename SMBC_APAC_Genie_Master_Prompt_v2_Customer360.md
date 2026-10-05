# MASTER PROMPT v2 — "APAC Genie" for SMBC Singapore: Customer 360 + RM/Credit Workbench + Transactional Banking

> Paste everything below this line into Claude Code. Edit the `## 0. Fill-ins` block first.

---

## 0. Fill-ins (edit before running)

```
DATABRICKS_PROFILE      = <cli auth profile name, e.g. smbc-demo>
WORKSPACE_CLOUD         = <aws | azure | gcp>
SQL_WAREHOUSE_NAME      = <serverless SQL warehouse for Genie + DDL>
CATALOG                 = smbc_genie
STORAGE_ROOT            = <optional external location; blank = managed default>
AS_OF_DATE              = 2026-09-30        # H1 FY2026 close; "latest closed month" everywhere
HISTORY_START           = 2023-04-01        # 3.5 Japanese fiscal years for YoY
SCALE                   = 1.0               # 0.1 for a fast dev run, 1.0 for the demo build
RANDOM_SEED             = 20260930
SPACES_TO_BUILD         = all               # core = spaces 1-8 | all = spaces 1-11
USE_AI_FUNCTIONS        = auto              # auto = use ai_analyze_sentiment / ai_forecast etc. if available, else synthesise outputs
SIMULATE_DELTA_SHARING  = true              # build the JP / EMEA / Americas "shared-in" tables locally (no second workspace needed)
OWNER_GROUP             = <UC group to own the catalog, e.g. smbc_genie_admins>
CONSUMER_GROUP          = <UC group with SELECT + Genie run access, e.g. smbc_genie_users>
```

---

## 1. Your role and mission

You are a senior Databricks solutions architect and data engineer. Deliver, end to end, a demo-grade **Customer 360 Data Foundation** for **SMBC Singapore** (Sumitomo Mitsui Banking Corporation, Asia Pacific Division HQ) and a family of **AI/BI Genie Spaces** built on it. The business frame is SMBC's **RM/Credit Workbench** programme:

| Workbench area | Use cases (from SMBC's Customer360 overview) |
|---|---|
| RM | Account Planning · Opportunity Identification |
| Credit | Credit Memo Generation · Early Warning Monitoring |
| RM + Credit | Profitability & ROE Analysis |
| Others | Onboarding |
| AI-enabled capabilities | Financial Spreading · Cashflow Forecasting · Sentiment Analysis · Signals |
| Data foundation | Internal/external × quantitative/qualitative data · Entity Resolution · Transaction Data · Delta Sharing across JP / APAC / Americas / EMEA · Data Governance |

Plus the **Transactional Banking** use cases an APAC wholesale bank runs every day (cash and payments, liquidity, trade finance, supply chain finance).

Deliver:

1. Catalog **`smbc_genie`** in medallion layout (bronze → silver → gold) with a **`shared`** schema that mirrors what arrives via Delta Sharing from the JP, EMEA and Americas regional lakehouses, and a **`metrics`** schema of Unity Catalog **Metric Views**.
2. Deterministic **synthetic data** across all four Customer 360 quadrants (internal quantitative, internal qualitative, external quantitative, external qualitative) with an **entity-resolution** story (messy source identities → one golden client record) and embedded business storylines so every space produces interesting, defensible answers.
3. **Eleven Genie Spaces** (8 core + 3 extended), all named `APAC Genie - <Topic>`, each answering from metric views, with general instructions, trusted assets, sample questions and benchmarks.
4. Repeatable deployment (Databricks Asset Bundle), validation tests, demo script, teardown.

Work phase by phase (Section 7). Show the plan before each phase, verify, commit. Where unsure of current Databricks syntax or API shape (Metric View YAML, Genie API, `ai_*` functions, Delta Sharing), **check the live docs or your Databricks tooling first** — do not rely on memory.

---

## 2. Business context (synthetic modelling only — never real clients, people or figures)

- **SMBC Singapore** runs the APAC wholesale franchise of a Japanese megabank. No retail. Relationship managers (RMs) cover client groups; credit analysts write memos and run annual reviews; Transaction Banking sells cash, liquidity and trade products; Head Office (Tokyo) owns the global relationship with the Japanese parent companies.
- **Client structure matters.** A Japanese parent (covered from Tokyo, data in the JP lakehouse) has subsidiaries across APAC (covered from Singapore and local offices), EMEA and the Americas. Account planning and credit decisions need the **global group view**, which only exists if the regions share data — this is the Delta Sharing story on the slide.
- **Segments:** `Japanese Corporate` (APAC subsidiaries of Japanese groups — the historic core: automotive and parts, electronics and semiconductors, trading houses, shipping and logistics, chemicals, construction and real estate), `Non-Japanese Large Corporate` (energy and utilities, infrastructure, mining and metals, agri and food, telecom and tech, consumer), `Financial Institution`, `Sponsor & Structured Finance`, `Public Sector`.
- **Relationship tiers:** `Strategic` (account plan mandatory, named RM + product partners), `Core`, `Transactional`.
- **Footprint — 13 APAC booking locations, Singapore hub:** SG, HK, AU (Sydney), IN (Mumbai), ID (Jakarta), TH (Bangkok), MY (Kuala Lumpur), VN (Ho Chi Minh City), CN (Shanghai), PH (Manila), KR (Seoul), TW (Taipei), NZ (Auckland). Tokyo = Head Office, not an APAC booking location. Other regions appear only as data providers: `JP`, `EMEA`, `AMER`.
- **Currencies:** local-currency originals plus a USD equivalent on every monetary column. USD, SGD, JPY, HKD, AUD, INR, IDR, THB, MYR, VND, CNY, PHP, KRW, TWD, NZD, EUR, GBP; daily FX table to USD; month-end rates derived.
- **Fiscal calendar — critical:** Japanese FY runs **1 April – 31 March**. `FY2026` = Apr 2026 – Mar 2027; Q1 Apr–Jun, Q2 Jul–Sep, Q3 Oct–Dec, Q4 Jan–Mar; H1 Apr–Sep. `AS_OF_DATE` 2026-09-30 is **H1 FY2026 close** — the moment RMs refresh account plans and credit runs mid-year reviews.
- **Regulatory colour (illustrative only):** MAS in Singapore; IFRS 9 staging (1/2/3); Basel-style RWA; AML/CFT with STR filings. Other locations carry a `regulator` attribute; do not model their rules.

---

## 3. Non-negotiable requirements

### 3.1 Catalog layout

```
smbc_genie
├── bronze    # raw landings per APAC source system; raw types; ingestion metadata; deliberate DQ noise and identity mess
├── shared    # tables as they would arrive via Delta Sharing from JP / EMEA / AMER lakehouses (simulated locally when SIMULATE_DELTA_SHARING)
├── silver    # cleansed, typed, deduplicated, conformed; ENTITY RESOLUTION happens here; SCD2 golden client; dq_results
├── gold      # Customer 360 star schema: golden client hub + fact_* + dim_*; constraints; clustering; every column commented
├── metrics   # Unity Catalog Metric Views only (mv_*); the primary Genie answer surface
└── ops       # build_run_log, dq_rules, storyline_assertions, genie_benchmarks, entity_resolution_runs
```

### 3.2 Medallion and Customer 360 rules

- **Bronze mirrors source systems**, with one prefix per system (Section 6.1). Mostly `STRING` columns; `_ingest_ts`, `_source_system`, `_source_file`, `_batch_id`. Inject 3–5% realistic dirtiness **plus deliberate identity fragmentation**: the same legal entity spelled differently across core banking, CRM, trade, treasury and KYC (`KINOKAWA PRECISION SG PTE LTD` / `Kinokawa Precision (Singapore) Pte. Ltd.` / `Kinokawa Precision Singapore`), inconsistent country codes, a stale parent link, duplicate customer records within one system. Never inject dirtiness that would make a storyline unrecoverable.
- **Shared** holds what other regions would publish: group master, parent-level exposure and revenue, parent financials, parent ratings, EMEA/AMER subsidiary exposure. Tag `source_region` and `ingest_method = 'delta_sharing'`. If a second workspace and share are available, offer to wire a real Delta Share in Phase 10; otherwise generate locally.
- **Silver performs entity resolution**: deterministic match on fictional LEI-like IDs and tax IDs → fuzzy match on normalised name + country (+ address tokens) → steward queue for low-confidence pairs. Output `silver.client_xref` (`golden_client_id`, `source_system`, `source_id`, `match_method`, `match_score`, `steward_decision`) and `silver.client_golden` as **SCD2**. Log every run in `ops.entity_resolution_runs`. Also apply standard DQ rules (`ops.dq_rules` → `silver.dq_results`), deduplicate on business keys, quarantine orphans.
- **Gold is a Customer 360 star schema** centred on `dim_client` (golden record, SCD2) and `dim_client_group` (global parent). Every fact carries `golden_client_sk` so any KPI in any space can be rolled up to client → group → segment → coverage office. Informational `PRIMARY KEY` / `FOREIGN KEY` (`NOT ENFORCED`, `RELY` where supported), `CLUSTER BY` (date + client/entity), `ANALYZE TABLE ... COMPUTE STATISTICS FOR ALL COLUMNS`.
- **Point-in-time facts** (balances, exposure, RWA, ECL, scores, headcount) exist at **month-end snapshot** grain for metric views; daily grain only where the use case needs it (deposits, EWS scores, cashflow, limits). Snapshot metric views carry the "never sum across months" instruction.
- **Every table and every column has a `COMMENT`** written for an RM/credit analyst and for Genie: meaning, unit, currency, sign, allowed values, grain. Hard gate before any Genie Space is created.
- **Four quadrants represented:** internal quantitative (balances, transactions, exposure, revenue), internal qualitative (RM call notes, credit memo text, account-plan narratives, complaint logs — short synthetic text), external quantitative (spread financial statements, external ratings, market data for listed parents, trade statistics), external qualitative (news headlines with topic and sentiment, regulatory announcements, ESG controversies).

### 3.3 Metric Views

- All KPIs live in `smbc_genie.metrics` as `CREATE OR REPLACE VIEW ... WITH METRICS LANGUAGE YAML AS $$ ... $$`, YAML `version: 1.1` (verify current default and runtime requirement first). `source` = a gold fact; `joins` to gold dims via the `source.` namespace; human-readable dimension/measure names; top-level `comment`; measure/dimension comments wherever the spec supports them.
- 2–4 metric views per space; 8–15 dimensions and 6–12 measures each; ratios as single aggregate expressions (`SUM(x)/SUM(y)`) so they aggregate correctly at any grain.
- Validate each with `SELECT <dims>, MEASURE(<measure>) FROM ... GROUP BY ALL`; reconcile ≥ 2 measures against gold; record in `ops.build_run_log`.
- `dim_client` attributes (segment, tier, Japanese-corporate flag, industry, coverage office, RM, rating, stage, watchlist, KYC risk, EWS band) are exposed as dimensions in **every** metric view so the same client vocabulary works across all eleven spaces.

### 3.4 Genie Spaces

- Exact names (Section 5): core 1–8 always; extended 9–11 when `SPACES_TO_BUILD = all`.
- Assets per space: its metric views + only the dims needed for drill-through (`dim_client`, `dim_client_group`, `dim_date`, `dim_booking_entity`, `dim_product`, `dim_employee` as relevant). ≤ 12 assets. Never bronze/shared/silver.
- Each space ships with: 2–3 sentence description; general instructions (≤ 20 lines — more instructions reduce accuracy); trusted assets = 4–6 parameterised example queries + 2–3 UC SQL functions; 6–8 sample questions; 12–20 benchmarks with expected SQL stored in `ops.genie_benchmarks` and `genie/<slug>/benchmarks.json`.
- Create programmatically if the Genie API/SDK supports create/update of spaces (check for a `genie/spaces` endpoint and a serialised-space payload); else emit `genie/<slug>/space_spec.json` + `docs/GENIE_RUNBOOK.md` and stop before the UI step.
- Warehouse = `SQL_WAREHOUSE_NAME`; `CONSUMER_GROUP` gets run access.

### 3.5 Synthetic-data integrity

- Deterministic from `RANDOM_SEED`; identical output for identical config.
- Full referential integrity **after silver**; bronze may contain orphans and duplicate identities by design.
- Plausible distributions: Pareto concentration (top 10% of groups ≈ 60% of deposits / 55% of drawn lending), lognormal tickets, business-day seasonality per location, quarter-end and March fiscal-year-end effects.
- No real company names, people, account numbers, BICs, LEIs or addresses. Fictional APAC/Japanese-flavoured name bank (`Kinokawa Precision (Singapore) Pte Ltd`, `Hayashi Marine Logistics (HK) Ltd`, `Sunda Energi Nusantara`, `Meridian Agri Holdings`, `Tanaka Chemical Asia`). ~380 parent groups → ~2,500 APAC legal entities (+ ~600 EMEA/AMER subsidiaries in `shared`).
- Qualitative text (RM notes, news headlines, memo excerpts) is template-generated, short (≤ 40 words), and never names real entities.

### 3.6 Engineering conventions

- Databricks Asset Bundle (`databricks.yml`, `dev` target); `src/` notebooks or `.py`; one job per phase + `build_all`.
- SQL for DDL, metric views, grants; PySpark for generation, entity resolution and transforms. Parameters from `config/smbc_genie.yaml`.
- Idempotent (`IF NOT EXISTS`, `CREATE OR REPLACE VIEW`, overwrite or `MERGE`). Never `DROP CATALOG` / `DROP SCHEMA ... CASCADE` without my explicit confirmation.
- `tests/`: row-count bands, FK coverage, DQ bands, **entity-resolution precision/recall against the known synthetic truth table**, storyline assertions, metric-view reconciliation.
- `docs/`: `README.md`, `DATA_MODEL.md` (Mermaid ER), `ENTITY_RESOLUTION.md`, `GENIE_RUNBOOK.md`, `DEMO_SCRIPT.md`, `DECISIONS.md`.
- If CLI/MCP/network are unavailable, still produce the full repo with exact run commands; never fake execution results.

---

## 4. Customer 360 gold hub — shared dimensions (build once, reuse everywhere)

| Dimension | Grain / key | Key attributes |
|---|---|---|
| `dim_client` (golden record) | SCD2 on `golden_client_sk`; business key `golden_client_id` | `legal_name`, `short_name`, `aliases` (array), `lei_like_id`, `country_of_incorporation`, `client_group_id`, `immediate_parent_id`, `segment`, `is_japanese_corporate`, `relationship_tier`, `industry_sector`, `industry_subsector`, `peer_group_id`, `is_listed`, `coverage_office` (booking entity), `primary_rm_id`, `credit_analyst_id`, `internal_rating_grade` (1–10), `rating_equivalent`, `external_rating`, `ifrs9_stage`, `watchlist_flag`, `ews_band` (Green/Amber/Red), `kyc_risk_rating`, `kyc_next_review_date`, `onboarding_date`, `has_account_plan`, `esg_rating`, `source_systems_present` (array), `golden_record_confidence`, `valid_from`, `valid_to`, `is_current` |
| `dim_client_group` | global parent group | `client_group_id`, `group_name`, `hq_country`, `hq_region` (JP/APAC/EMEA/AMER), `global_relationship_owner_region`, `group_industry`, `group_external_rating`, `is_strategic_group`, `n_entities_apac`, `n_entities_global` |
| `xref_client_source` | golden ↔ source identity | `golden_client_id`, `source_system`, `source_id`, `source_name_as_recorded`, `match_method` (deterministic / fuzzy / steward / unmatched), `match_score`, `resolved_at` |
| `dim_contact` | key client contacts (fictional) | `contact_id`, `golden_client_id`, `role` (CFO / Treasurer / Procurement Head / CEO), `last_interaction_date`, `relationship_strength` |
| `dim_date` | 2023-04-01 → 2027-03-31 | `fiscal_year`, `fiscal_year_label`, `fiscal_quarter`, `fiscal_half`, `fiscal_month_no`, `is_month_end`, `is_business_day_sg`, `is_latest_closed_month` |
| `dim_booking_entity` | 13 APAC + 3 provider regions | `booking_entity_id`, `city`, `country_name`, `region_cluster`, `regulator`, `local_currency`, `is_hub`, `is_apac` |
| `dim_product` | hierarchy | `business_line` → `product_family` → `product` (Lending; Transaction Banking → Cash / Liquidity / Payments / Trade Finance / SCF; Markets → FX / Rates; Sustainable Finance) |
| `dim_employee` | fictional staff | `employee_id`, `name`, `role` (RM / Credit Analyst / TB Sales / Onboarding Officer), `coverage_office`, `team` |
| `dim_industry` | sector taxonomy + peer groups | `industry_sector`, `industry_subsector`, `peer_group_id`, `is_carbon_intensive`, `sector_limit_usd` |
| `dim_signal_type` | signal catalogue | `signal_code`, `signal_name`, `polarity` (opportunity / risk / neutral), `source_quadrant`, `default_weight`, `description` |
| `dim_ews_trigger` | early-warning rule book | `trigger_code`, `name`, `threshold_description`, `severity`, `score_points`, `auto_watchlist_flag` |
| `dim_onboarding_stage` | onboarding funnel | `stage_no`, `stage_name` (Request → KYC Docs → Screening → Risk Assessment → Credit/Product Approval → Account Open → First Transaction), `sla_days` |
| `dim_currency`, `fx_rate_daily` | | as in any bank model |

---

## 5. The eleven Genie Spaces — what to build and the questions each must answer

The "Must-answer questions" are the north star: they become sample questions, benchmarks and the acceptance test. Build the data so each has a crisp answer at `AS_OF_DATE`.

### TIER A — RM Workbench

### 5.1 `APAC Genie - Account Planning` (core)

**Users:** RMs, Team Heads, Japanese Corporate Banking Dept, client strategy.
**Job to be done:** refresh the H1 account plan for a client group in minutes — what we hold, what we earn, how it compares to plan, what the group looks like globally, who we talk to.

**Gold facts:** `fact_account_plan_annual` (client group × FY: revenue target by product family, wallet estimate, planned initiatives (text), plan status, owner RM, mid-year revision), `fact_client_revenue_monthly` (golden client × month × product: NII, fees, trading revenue, allocated cost, credit cost, RWA — the integration fact used by spaces 1, 2, 5), `fact_product_holding_monthly` (client × product: active flag, balance or 12-month volume, first/last use), `fact_wallet_estimate_annual` (group × FY: estimated bankable wallet by product family, SMBC share %, top competitor bank share — fictional banks), `fact_rm_activity` (call/meeting/email: date, RM, contact, purpose, note text, sentiment score, next action), `fact_group_global_exposure_monthly` (group × region × month: committed, drawn, deposits, revenue — APAC from gold, JP/EMEA/AMER from `shared`).

**Metric views:**
- `mv_account_plan_progress` — dims: Fiscal Year, Client Group, Client, Segment, Tier, Coverage Office, Primary RM, Product Family. Measures: Revenue Target USD, Revenue Actual YTD USD, Attainment %, Run-Rate Full-Year USD, Gap to Target USD, Initiatives Planned, Initiatives Completed.
- `mv_relationship_footprint` — dims: Month, Client Group, Client, Segment, Product Family, Product, Booking Country. Measures: Products Held, Active Product Count, Lending Drawn USD, Deposits USD, TB Volume 12M USD, FX Notional 12M USD, Revenue 12M USD, Share of Wallet %, Products Held vs Peer Median.
- `mv_global_group_relationship` — dims: Month, Client Group, Region (JP/APAC/EMEA/AMER), Booking Country, Product Family. Measures: Global Committed USD, Global Drawn USD, Global Deposits USD, Global Revenue 12M USD, APAC Share of Group Revenue %, Entity Count.
- `mv_rm_engagement` — dims: Month, RM, Coverage Office, Client Group, Tier, Activity Type, Contact Role. Measures: Activities, Clients Touched, Strategic Clients Not Touched 90D, Average Sentiment, Next Actions Open.

**Must-answer questions:**
1. Give me the account-plan status for `Kinokawa Precision` group: FY2026 target vs H1 actual by product family, and the full-year run-rate.
2. Which Strategic-tier groups are below 40% plan attainment at H1, by coverage office and RM?
3. What products does `Tanaka Chemical Asia` group hold with us across APAC, and which entities hold nothing but deposits?
4. Show the global relationship with `Hayashi Marine Logistics` — exposure, deposits and revenue by region including Japan, EMEA and Americas.
5. Our share of wallet by segment and product family in FY2025 — where is it lowest?
6. Which Strategic clients have had no RM contact in the last 90 days?
7. RM book summary: for each RM in Singapore, number of groups, revenue YTD and attainment.
8. Which Japanese-corporate groups grew revenue > 20% YoY and which fell > 15%? Break down by product.
9. For groups where APAC is < 20% of global group revenue, what is the APAC product footprint vs the rest of the group?
10. List account-plan initiatives still open for Q3 FY2026 by RM.

### 5.2 `APAC Genie - Opportunity Identification` (core)

**Users:** RMs, TB sales, product partners, Head of Coverage.
**Job to be done:** turn Customer 360 data into a ranked list of actionable opportunities — product gaps vs peers, cash surpluses, trade and FX flows we see but do not serve, maturing facilities, external triggers (expansion, M&A, capex).

**Gold facts:** `fact_opportunity_signal` (golden client × signal date × `signal_code`: e.g. `PRODUCT_GAP_VS_PEERS`, `DEPOSIT_SURPLUS`, `FX_FLOW_VIA_OTHER_BANK` (derived from payments to/from third-party banks in a currency pair we do not trade for them), `TRADE_CORRIDOR_GROWTH`, `FACILITY_MATURING_12M`, `LOAN_SERVICE_TO_OTHER_BANK` (debt refinanced elsewhere), `CAPEX_NEWS`, `MA_NEWS`, `SLL_ELIGIBLE`, `SCF_ANCHOR_CANDIDATE`; estimated annual revenue USD; confidence; status open / in pipeline / dismissed; dismissed reason), `fact_next_best_product_score` (client × product × month: propensity 0–1, rank, top 3 drivers text, model version), `fact_pipeline_opportunity` (CRM: stage, product, amount, expected revenue, probability, expected close, owner, won/lost, source signal id), `fact_peer_penetration_annual` (peer group × product: % of peers holding, median volume).

**Metric views:**
- `mv_opportunity_signals` — dims: Signal Date, Month, Signal Type, Polarity, Source Quadrant, Client Group, Client, Segment, Tier, Industry, Coverage Office, RM, Status. Measures: Signals, Open Signals, Estimated Revenue USD, Average Confidence, Converted to Pipeline %, Days Open Average.
- `mv_next_best_product` — dims: Month, Client Group, Client, Segment, Product Family, Product, RM, Rank. Measures: Average Propensity, Clients With Score ≥ 0.7, Estimated Revenue USD.
- `mv_pipeline` — dims: Expected Close Quarter, Stage, Product, Segment, Booking Country, RM, Source Signal Type. Measures: Pipeline Amount USD, Weighted Pipeline USD, Expected Revenue USD, Opportunity Count, Win Rate %, Average Cycle Days.
- `mv_product_penetration` — dims: Peer Group, Industry, Segment, Product Family, Product, Client Group. Measures: Penetration % (clients holding), Peer Penetration %, Penetration Gap pts, Clients With Gap.

**Must-answer questions:**
1. Top 25 open opportunities by estimated revenue across APAC, with signal type, RM and days open.
2. Which clients are paying USD/JPY or USD/VND through other banks (FX flow we do not capture), and how much flow?
3. Clients with a deposit surplus signal in the last 60 days that have no time-deposit or investment product with us.
4. Which groups have facilities maturing in the next 12 months with no refinancing opportunity in the pipeline?
5. Product penetration gap vs peers for Japanese automotive-parts subsidiaries — which products are under-penetrated?
6. Next-best-product: top 20 clients by propensity for supply chain finance and the main drivers.
7. Opportunities created from trade-corridor-growth signals into Vietnam and India this fiscal year, and their conversion rate.
8. Which capex/expansion news signals in Australia have not been actioned by an RM?
9. Pipeline by expected close quarter and stage for H2 FY2026, weighted value by product.
10. Win rate by source signal type — which signals actually convert?

### TIER B — Credit Workbench

### 5.3 `APAC Genie - Credit Memo & Financial Spreading` (core)

**Users:** Credit analysts, credit officers, RMs preparing annual reviews and new-money requests.
**Job to be done:** assemble the quantitative "credit memo pack" for a client in one conversation — spread financials and ratios, peer comparison, facility and covenant terms, collateral, exposure, pricing, review workflow status. Genie serves the data; memo text generation is an optional agent on top (Phase 10).

**Gold facts:** `fact_financial_statement_annual` (golden client × fiscal year-end × statement type P&L / BS / CF: standard spread lines — revenue, COGS, EBITDA, EBIT, interest expense, net income, cash, receivables, inventory, payables, total assets, total debt (short/long), equity, CFO, capex, dividends; audited flag; currency; source external/internal; spread date; analyst), `fact_financial_ratio_annual` (leverage Net Debt/EBITDA, ICR, DSCR, current ratio, quick ratio, gross/EBITDA/net margin, ROE, ROA, DSO/DIO/DPO, cash conversion cycle, FCF, debt/equity, YoY growth), `fact_peer_benchmark_annual` (peer group × ratio × FY: P25/P50/P75), `fact_facility_terms` (facility × client: type, limit, drawn, margin, maturity, security type, guarantor (parent keepwell / guarantee / none), covenant list), `fact_covenant_test` (facility × covenant × test date: covenant type, threshold, actual, headroom %, breached flag, waiver), `fact_collateral` (facility × collateral: type, appraised value USD, LTV, last valuation date), `fact_credit_review_workflow` (review id, client, type annual / new money / amendment / watchlist, due date, submitted date, approved date, status, analyst, approver, days in preparation, memo excerpt text).

**Metric views:**
- `mv_financial_spreads` — dims: Fiscal Year End, Client Group, Client, Segment, Industry, Peer Group, Statement Line, Audited. Measures: Amount USD, YoY Growth %, Revenue USD, EBITDA USD, Net Debt USD, Equity USD, Free Cash Flow USD.
- `mv_financial_ratios_vs_peers` — dims: Fiscal Year, Client, Client Group, Peer Group, Ratio Name. Measures: Ratio Value, Peer P25, Peer Median, Peer P75, Percentile Rank in Peer Group, Clients Below Peer P25.
- `mv_facilities_covenants_collateral` — dims: Client Group, Client, Facility, Facility Type, Covenant Type, Test Date, Security Type, Guarantor Type. Measures: Limit USD, Drawn USD, Covenant Headroom % (min), Breaches, Waivers, Collateral Value USD, LTV %, Weighted Margin bps.
- `mv_credit_review_workflow` — dims: Month, Review Type, Status, Coverage Office, Analyst, Approver, Segment. Measures: Reviews Due, Overdue, Average Days in Preparation, Approved on First Submission %, Memos Awaiting Approval.

**Must-answer questions:**
1. Credit memo pack for `Sunda Energi Nusantara`: last 3 years of spread P&L and balance sheet, key ratios, peer percentile, facilities, covenants with headroom, collateral and current exposure.
2. Which clients breached or have < 10% headroom on a leverage covenant at the latest test?
3. Net Debt/EBITDA and ICR for all Indonesian mining clients vs their peer median, FY2023–FY2025.
4. Clients whose FY2025 financials are not yet spread, by analyst and days overdue.
5. Annual reviews due in Q3 FY2026 by analyst, with the current status and days in preparation.
6. Collateral coverage: facilities with LTV above 70% or a valuation older than 24 months.
7. Which Japanese-corporate subsidiaries rely on a parent guarantee or keepwell, and what is the parent's external rating (from the Japan share)?
8. Working-capital cycle (DSO/DIO/DPO) trend for electronics-sector clients, FY2023–FY2025.
9. Covenant waivers granted this fiscal year and the associated exposure.
10. New-money requests approved in H1 FY2026: amount, margin and time to approval by coverage office.

### 5.4 `APAC Genie - Early Warning Monitoring` (core)

**Users:** Credit portfolio management, credit officers, RMs, risk committee secretariat.
**Job to be done:** see deterioration before it becomes a downgrade — a daily composite EWS score built from internal behaviour, covenant and payment data, external ratings, market data and news sentiment; explain *why* a client is Amber or Red; track watchlist actions.

**Gold facts:** `fact_ews_signal_daily` (golden client × date × `trigger_code`: `DPD_15` / `DPD_30` / `DPD_60`, `OVERDUE_INTEREST`, `UTILISATION_GT_90`, `DEPOSIT_OUTFLOW_30PCT_30D`, `PAYMENT_RETURN`, `SALARY_PAYMENT_DELAY`, `COVENANT_BREACH`, `COVENANT_HEADROOM_LT_10`, `EXT_RATING_DOWNGRADE`, `PARENT_RATING_DOWNGRADE` (from JP share), `NEGATIVE_NEWS_SENTIMENT`, `TRADE_DOC_DISCREPANCY_SPIKE`, `LIMIT_EXCESS`, `AUDITOR_QUALIFICATION`, `KEY_MGMT_CHANGE`; severity; score points; source quadrant; evidence text), `fact_ews_score_daily` (client × date: composite score 0–100, band, band change flag, days in band, top 3 contributing triggers, override flag, override reason — e.g. parent support confirmed via JP data), `fact_watchlist_event` (client: added/removed/escalated, date, reason, action plan text, owner, next review), `fact_dpd_daily` (facility × date: days past due, overdue amount USD), `fact_covenant_test` (shared with 5.3), `fact_rating_migration` (grade/stage from–to, reason).

**Metric views:**
- `mv_ews_scores` — dims: Date, Month, Client Group, Client, Segment, Tier, Industry, Coverage Office, RM, Credit Analyst, Band, Rating Grade, Stage. Measures: Average Score, Latest Score, Clients Red, Clients Amber, Entered Red (count), Exposure in Red USD, Exposure in Amber USD, Days in Current Band Average, Overrides.
- `mv_ews_signals` — dims: Date, Month, Trigger, Severity, Source Quadrant, Client Group, Client, Industry, Coverage Office. Measures: Signals Fired, Distinct Clients, Score Points Total, Exposure Affected USD, Signals per Client.
- `mv_watchlist` — dims: Month, Event Type, Reason, Coverage Office, Industry, Owner. Measures: Watchlist Clients, Added, Removed, Watchlist Exposure USD, ECL on Watchlist USD, Actions Overdue.
- `mv_delinquency` — dims: Date, Month, Booking Country, Segment, Industry, DPD Bucket. Measures: Overdue Amount USD, Facilities Overdue, Clients Overdue, 30+ DPD Rate %, Cure Rate %.

**Must-answer questions:**
1. Which clients are Red today, what exposure do they carry, and what are the top three triggers for each?
2. Walk me through `Sunda Energi Nusantara`'s EWS score month by month since January 2026 and which signals fired when.
3. Clients that moved from Green to Amber in the last 30 days, by coverage office and industry.
4. Exposure in Amber and Red by industry sector at each month-end since April 2025.
5. Which signals most often precede a downgrade within 90 days? (Count of trigger → downgrade pairs.)
6. Clients with a deposit-outflow signal AND a utilisation spike in the same month this fiscal year.
7. EWS overrides this year and the reasons — how many cite parent support from Japan?
8. Watchlist actions overdue by owner.
9. 30+ DPD rate by booking country and segment, trend by month.
10. Clients with covenant headroom below 10% that are NOT on the watchlist.

### TIER C — RM + Credit

### 5.5 `APAC Genie - Client Profitability & ROE` (core)

**Users:** Coverage heads, Finance business partners, pricing committee, RMs.
**Job to be done:** relationship-level profitability and returns — revenue by product, allocated costs, credit cost, capital consumed, RoRWA / RAROC / ROE vs hurdle; deal pricing at origination vs realised.

**Gold facts:** `fact_client_revenue_monthly` (shared), `fact_capital_allocation_monthly` (client × month: RWA, allocated capital at target CET1, cost of capital, expected loss), `fact_cost_allocation_monthly` (client × month: direct cost, RM cost, operations cost, HO allocation), `fact_deal_pricing` (deal × client: origination RAROC, approved below hurdle flag, exception reason, realised RAROC after 12 months), `fact_client_pnl_waterfall_annual` (client × FY: revenue → opex → credit cost → tax → net income → allocated capital → ROE).

**Metric views:**
- `mv_client_profitability` — dims: Month, Fiscal Year, Client Group, Client, Segment, Tier, Is Japanese Corporate, Industry, Coverage Office, RM, Product Family, Product. Measures: Total Revenue USD, NII USD, Fee Revenue USD, Trading Revenue USD, Allocated Cost USD, Credit Cost USD, Net Contribution USD, Average RWA USD, RoRWA %, RAROC %, Products per Client, Revenue YoY %.
- `mv_roe_waterfall` — dims: Fiscal Year, Client Group, Segment, Coverage Office, Industry. Measures: Revenue USD, Opex USD, Credit Cost USD, Tax USD, Net Income USD, Allocated Capital USD, ROE %, Hurdle Gap pts, Clients Below Hurdle, Exposure Below Hurdle USD.
- `mv_deal_pricing` — dims: Signing Quarter, Product, Segment, Coverage Office, Approved Below Hurdle, Exception Reason. Measures: Deals, Amount USD, Average Origination RAROC %, Average Realised RAROC %, Below-Hurdle Deals %, RAROC Slippage pts.

**Must-answer questions:**
1. Top and bottom 20 client groups by ROE in FY2025, with revenue, capital and credit cost.
2. Which Japanese-corporate relationships are single-product lending with RoRWA below the 8% hurdle, and what cross-sell would lift them?
3. ROE waterfall for `Hayashi Marine Logistics` group, FY2024 vs FY2025.
4. Revenue mix by product family for Strategic clients vs Core clients.
5. Deals approved below hurdle in the last 4 quarters — by exception reason and whether realised RAROC caught up.
6. Net contribution by coverage office and segment, H1 FY2026 vs H1 FY2025.
7. Which groups consume the most capital relative to revenue (lowest revenue per unit of RWA)?
8. Clients where credit cost exceeded 30% of revenue in FY2025.
9. Products per client by segment and its relationship with RoRWA (bucketed).
10. RM-level profitability: revenue, RWA and RoRWA per RM in Hong Kong.

### TIER D — Others

### 5.6 `APAC Genie - Client Onboarding & KYC` (core)

**Users:** Onboarding operations, KYC/CDD teams, RMs chasing their clients' go-live, compliance.
**Job to be done:** funnel and cycle-time visibility across the onboarding stages, bottlenecks, SLA, documents outstanding, periodic-review health, screening — and whether a new entity already exists elsewhere in the group (entity resolution at onboarding).

**Gold facts:** `fact_onboarding_case` (case: golden client (or provisional id), group, segment, booking entity, products requested, request date, current stage, status open / live / withdrawn / rejected, blocker reason, documents outstanding, owner, RM, matched existing group flag), `fact_onboarding_stage_event` (case × stage: entered, exited, days, SLA met), `fact_kyc_review` (client, review type onboarding / periodic / trigger, due, completed, risk before/after, overdue days), `fact_screening` (date, list, hit type, true match, resolution hours), `fact_onboarding_feedback` (post-live survey score, comment text).

**Metric views:**
- `mv_onboarding_funnel` — dims: Request Month, Booking Country, Segment, Tier, Products Requested, Current Stage, Status, Blocker Reason, Owner. Measures: Cases Opened, Cases Live, Cases Withdrawn, Open Cases, Conversion to Live %, Matched to Existing Group %.
- `mv_onboarding_cycle_time` — dims: Request Month, Booking Country, Segment, Stage. Measures: Median Days to Live, P90 Days to Live, Average Days in Stage, SLA Met %, Cases Over SLA.
- `mv_kyc_health` — dims: Month, Booking Country, Review Type, Risk Rating, Segment, Status. Measures: Reviews Due, Completed, Overdue, Overdue Rate %, Average Overdue Days, High-Risk Overdue, Screening True Match Rate %.

**Must-answer questions:**
1. Median and P90 days to go live by booking country, by request month since April 2025.
2. Which stage adds the most days for Financial Institution clients, and did it change after February 2026?
3. Open onboarding cases over SLA right now, by owner and blocker reason.
4. Documents outstanding by type for cases stuck in KYC Docs more than 15 days.
5. Share of new onboardings that were matched to an existing client group — and cases where the match was found only after account opening.
6. Periodic KYC reviews overdue by risk rating and country, trend since January 2026.
7. Onboarding requests by segment and product requested this fiscal year vs last.
8. Clients onboarded in FY2026 that have not transacted within 60 days of go-live.
9. Screening true-match rate and average resolution hours by list.
10. Post-onboarding satisfaction by country and the most common negative comment themes.

### TIER E — Transactional Banking

### 5.7 `APAC Genie - Transactional Banking: Cash, Payments & Liquidity` (core)

**Users:** Head of Transaction Banking APAC, Cash and Liquidity product heads, TB sales, RMs.
**Job to be done:** operating deposits, CASA behaviour, payment flows and counterparties (the richest signal source in Customer 360), liquidity structures (pooling, sweeps), channel adoption, STP.

**Gold facts:** `fact_deposit_balance_daily` and `fact_deposit_balance_monthly` (account × day / month-end: balances LCY/USD, average balance, rate), `fact_payment_transaction` (one row per payment: channel SWIFT MT / ISO 20022 / host-to-host / API / portal / RTGS / FAST; direction; debtor and creditor country; `counterparty_bank_type` SMBC / other-bank / intercompany; `payment_purpose` supplier / payroll / tax / intercompany / loan-service / dividend / trade-settlement; `is_cross_border`; `stp_flag`; `repair_reason`; processing seconds), `fact_liquidity_structure_monthly` (structure id: type physical pooling / notional pooling / sweep / interest optimisation; header account; participants; countries; balances), `fact_channel_usage_monthly` (client × channel: active users, logins, API calls, straight-through %), `fact_tb_fee_income_monthly`.

**Metric views:**
- `mv_tb_deposits` — dims: Month, Fiscal Year, Fiscal Quarter, Booking Country, Client Group, Client, Segment, Tier, Is Japanese Corporate, Industry, Account Type, Currency. Measures: End of Month Balance USD, Average Balance USD, CASA Balance USD, Time Deposit Balance USD, CASA Ratio, Depositors, Accounts, Top-10 Depositor Concentration % (trusted query if window measures unsupported).
- `mv_tb_payments` — dims: Month, Booking Country, Channel, Direction, Currency, Is Cross-Border, Corridor, Counterparty Bank Type, Payment Purpose, Client Group, Segment, Repair Reason. Measures: Volume USD, Count, Average Ticket USD, STP Rate, Repair Rate, Cross-Border Share %, Share Paid via Other Banks %, Fee Income USD.
- `mv_tb_liquidity_structures` — dims: Month, Structure Type, Client Group, Header Country, Participant Country, Currency. Measures: Structures, Participants, Pooled Balance USD, Interest Saved USD (estimated), Clients Without Pooling (with ≥ 3 countries).
- `mv_tb_channel_adoption` — dims: Month, Client Group, Segment, Channel, Booking Country. Measures: Active Clients, Active Users, Logins, API Calls, Digital Share of Payments %, Manual Instruction Share %.

**Must-answer questions:**
1. Total CASA balance by booking country at end September 2026 and the movement versus August.
2. Which 20 client groups drove the largest CASA outflow in Q2 FY2026, and did it move to time deposits or leave the bank?
3. CASA ratio trend, monthly, Japanese vs non-Japanese corporates since FY2025.
4. Payment volume by purpose and counterparty bank type — how much of our clients' supplier payments go to accounts at other banks?
5. STP rate by channel and currency this fiscal year; where are repairs concentrated?
6. Multi-country groups (≥ 3 APAC countries) with no liquidity pooling structure — ranked by total balances.
7. Top 10 depositor concentration by booking country.
8. Clients whose payment volume through us dropped > 30% YoY while balances held steady.
9. Digital channel adoption by segment — who still sends manual instructions?
10. Time-deposit maturities in the next 90 days by country and currency.

### 5.8 `APAC Genie - Transactional Banking: Trade & Supply Chain Finance` (core)

**Users:** Head of Trade APAC, trade product and sales, trade operations, RMs.
**Job to be done:** trade finance issuance and outstanding by product and corridor, SCF programme health, trade ops turnaround and document discrepancies, and the trade-flow signals that feed opportunity identification and early warning.

**Gold facts:** `fact_trade_finance_transaction` (import/export LC issuance and confirmation, guarantees/SBLC, documentary collections, trade loans, receivables purchase: amount, tenor, corridor from/to, commodity, counterparty bank country, fee USD, status), `fact_trade_document_check` (presentation: received, checked, discrepant flag, discrepancy type, turnaround hours, amended), `fact_scf_programme_drawdown` (programme × supplier × invoice: anchor buyer, supplier country, invoice amount, financed amount, days financed, rate), `fact_trade_corridor_monthly` (corridor × commodity × month: volume USD, count, SMBC share estimate vs trade statistics), `fact_tb_fee_income_monthly` (shared).

**Metric views:**
- `mv_tb_trade_finance` — dims: Month, Fiscal Year, Booking Country, Product, Corridor From, Corridor To, Commodity, Client Group, Segment, Industry, Tenor Bucket, Counterparty Bank Country. Measures: Issuance USD, Transactions, Outstanding USD, Average Tenor Days, Fee Income USD, Fee Yield bps, Active Trade Clients.
- `mv_trade_operations` — dims: Month, Booking Country, Product, Discrepancy Type, Operations Team. Measures: Presentations, Discrepancy Rate %, Median Turnaround Hours, Amendments per Transaction, Presentations Over SLA.
- `mv_tb_scf` — dims: Month, Programme, Anchor Buyer, Anchor Country, Supplier Country, Supplier Segment. Measures: Programme Limit USD, Financed USD, Utilisation %, Suppliers Onboarded, Suppliers Active, Average Days Financed, Yield bps.
- `mv_trade_corridors` — dims: Month, Corridor From, Corridor To, Commodity, Industry. Measures: Corridor Volume USD, Growth YoY %, SMBC Share Estimate %, Clients Active in Corridor, Clients in Corridor Without Trade Product.

**Must-answer questions:**
1. Trade finance issuance by product and corridor, H1 FY2026 vs H1 FY2025 — fastest-growing corridors.
2. Import LC volume into Vietnam and India by commodity and client segment this fiscal year.
3. SCF programme utilisation and supplier activation by anchor — programmes above 85% or below 40%.
4. Document discrepancy rate by booking country and discrepancy type; which clients have a rising discrepancy rate (an early-warning input)?
5. Trade ops turnaround: median hours by product and country vs SLA.
6. Clients active in the CN→VN electronics corridor (visible from payments) that have no trade product with us.
7. Guarantees and SBLCs outstanding by beneficiary country and expiry quarter.
8. Fee yield on trade products by segment — are Japanese-corporate trade fees below non-Japanese?
9. Anchor-buyer candidates for new SCF programmes: large buyers paying ≥ 50 distinct suppliers through us.
10. Trade exposure to commodity sectors flagged carbon-intensive, trend by quarter.

### TIER F — AI-enabled capabilities (extended)

### 5.9 `APAC Genie - Cashflow Forecasting` (extended)

**Users:** TB liquidity advisory, RMs, credit (liquidity risk of borrowers), the client-facing "cash forecast" proposition.
**Job to be done:** client-level actual cashflows derived from transaction data, 7/30/90-day forecasts with intervals, forecast accuracy, and predicted shortfalls/surpluses that create credit (RCF/overdraft) and deposit/investment opportunities.

**Gold facts:** `fact_client_cashflow_daily` (golden client × date × category: inflow / outflow USD by purpose; net; closing balance), `fact_cashflow_forecast` (client × forecast date × horizon 7/30/90 × model version: forecast inflow, outflow, net, P10/P90, method — `ai_forecast` if available else synthetic), `fact_forecast_accuracy_monthly` (client × horizon × model: MAPE, bias, coverage of P10–P90), `fact_liquidity_need_event` (client × date: predicted shortfall / surplus amount, horizon, action taken RCF drawdown / TD placed / none, days to action).

**Metric views:**
- `mv_client_cashflow` — dims: Date, Month, Client Group, Client, Segment, Industry, Booking Country, Cashflow Category. Measures: Inflow USD, Outflow USD, Net Cashflow USD, Closing Balance USD, Inflow Volatility (stddev), Seasonality Index.
- `mv_cashflow_forecast` — dims: Forecast Date, Horizon, Model Version, Client Group, Client, Segment. Measures: Forecast Net USD, Forecast P10 USD, Forecast P90 USD, Forecast Inflow USD, Forecast Outflow USD, Clients Forecast Negative.
- `mv_forecast_accuracy` — dims: Month, Horizon, Model Version, Segment, Industry, Booking Country. Measures: MAPE %, Bias %, Interval Coverage %, Clients Within 10% MAPE.
- `mv_liquidity_events` — dims: Event Month, Event Type, Horizon, Client Group, Segment, Action Taken, RM. Measures: Events, Predicted Amount USD, Actioned %, Average Days to Action, Revenue from Actions USD.

**Must-answer questions:** 30-day net cashflow forecast for a named group with P10/P90; clients forecast to go negative in the next 30 days and their undrawn RCF; forecast accuracy by horizon and segment before vs after model v2; predicted surpluses > USD 20m not yet placed; seasonality: which industries have March and September inflow peaks; shortfall events that were followed by an RCF drawdown within 10 days; inflow volatility ranking for Red/Amber EWS clients; forecast bias by booking country.

### 5.10 `APAC Genie - Signals & Sentiment` (extended)

**Users:** RMs, credit analysts, client strategy, the "signals" product team.
**Job to be done:** one unified feed of external and internal qualitative signals — news topics and sentiment, RM-note sentiment, market signals on listed parents — that both Opportunity Identification and Early Warning consume; answer "what is happening around this client group" in seconds.

**Gold facts:** `fact_news_item` (date, client group, source type, headline (synthetic), topic — expansion / M&A / management change / litigation / ESG controversy / rating action / earnings / regulatory / supply-chain disruption, sentiment −1..1 (via `ai_analyze_sentiment` or synthetic), relevance, region), `fact_rm_note_sentiment` (note id, client, RM, date, sentiment, topics extracted, action required flag), `fact_market_signal_daily` (listed parent × date: share price change 1D/30D, CDS spread change, external rating/outlook, 52-week low flag), `fact_signal_event` (unified: date, client, signal code, polarity, strength, source quadrant, consumed-by opportunity/EWS, acknowledged by RM).

**Metric views:**
- `mv_news_sentiment` — dims: Date, Month, Client Group, Segment, Industry, Topic, Source Type, Region, Polarity. Measures: Items, Average Sentiment, Negative Items, Positive Items, Sentiment Change 30D, Groups With Negative Trend.
- `mv_internal_sentiment` — dims: Month, Client Group, RM, Coverage Office, Topic, Action Required. Measures: Notes, Average Note Sentiment, Negative Notes, Divergence (internal vs external sentiment), Actions Open.
- `mv_market_signals` — dims: Date, Client Group, Industry, External Rating, Outlook. Measures: Average Share Price Change 30D %, Average CDS Change bps, Groups at 52-Week Low, Rating Downgrades, Outlook Changes to Negative.
- `mv_signal_feed` — dims: Date, Month, Signal Type, Polarity, Source Quadrant, Client Group, Segment, Consumed By, Acknowledged. Measures: Signals, Average Strength, Unacknowledged Signals, Signals per Group, Opportunity Signals, Risk Signals.

**Must-answer questions:** everything about a named group in the last 90 days (news, sentiment, market, RM notes); groups whose external sentiment turned negative while RM notes stayed positive (blind spot); most common topics behind negative sentiment by industry; listed parents at 52-week lows with APAC exposure > USD 100m; unacknowledged risk signals by RM; positive expansion signals in Australia and India with no pipeline; sentiment trend for coal and shipping sectors; rating-action signals from JP-shared parent data.

### TIER G — Data foundation (extended)

### 5.11 `APAC Genie - Customer 360 Data Foundation` (extended)

**Users:** Data stewards, data governance, the Customer 360 platform team, audit.
**Job to be done:** prove the foundation works — entity-resolution quality, golden-record coverage by source, data quality by dimension, Delta Sharing freshness from JP/EMEA/AMER, and lineage/governance facts a steward is asked about.

**Gold facts:** `fact_entity_resolution_run` (run × source system: records in, matched deterministic, matched fuzzy, steward-resolved, unmatched, merged duplicates, split records, precision/recall vs synthetic truth), `fact_golden_record_coverage_monthly` (golden client × source system: present, last updated, attribute completeness %), `fact_dq_score_monthly` (table × DQ dimension completeness / validity / uniqueness / timeliness / consistency: score, failed rules, rows failed), `fact_delta_share_freshness_daily` (provider region × share × table: last refreshed, lag hours, row count, schema drift flag), `fact_steward_queue` (pair id, candidate names, score, assigned, decision, days open).

**Metric views:**
- `mv_entity_resolution` — dims: Run Date, Source System, Match Method, Segment, Coverage Office. Measures: Records In, Matched, Match Rate %, Fuzzy Share %, Steward Decisions, Duplicates Merged, Precision %, Recall %, Steward Queue Open.
- `mv_golden_record_coverage` — dims: Month, Source System, Segment, Tier, Coverage Office. Measures: Golden Clients, Clients Present in Source %, Clients in All Core Sources %, Average Attribute Completeness %, Clients Missing Credit Data, Clients Missing KYC Data.
- `mv_data_quality` — dims: Month, Layer, Table, DQ Dimension, Rule. Measures: DQ Score %, Rules Failed, Rows Failed, Tables Below 95%.
- `mv_delta_sharing_freshness` — dims: Date, Provider Region, Share, Table. Measures: Average Lag Hours, Max Lag Hours, Tables Stale (> 24h), Row Count, Schema Drift Events.

**Must-answer questions:** entity-resolution match rate and precision by source system for the latest run; how many client records collapsed into how many golden records; steward queue aging; golden clients missing from KYC or credit sources by tier; DQ score by table and dimension this month vs last; Delta Share freshness from Japan over the last 30 days and any stale tables; which exposures changed by more than 20% after the March 2026 resolution of `Meridian Agri Holdings`; attribute completeness for Strategic clients.

---

## 6. Synthetic data specification

### 6.1 Source systems to simulate

| Prefix | Simulated system | Quadrant | Bronze / shared tables (indicative) |
|---|---|---|---|
| `core_` | Core banking (deposits, loans) | Int. quant | `core_customer`, `core_account`, `core_deposit_balance`, `core_loan_facility`, `core_loan_schedule`, `core_dpd`, `core_rating_history` |
| `pay_` | Payments hub | Int. quant / Transaction | `pay_payment_message`, `pay_repair_queue`, `pay_counterparty_bank` |
| `trade_` | Trade finance platform | Int. quant / Transaction | `trade_instrument`, `trade_event`, `trade_presentation`, `trade_scf_programme`, `trade_scf_invoice` |
| `tsy_` | Treasury / FX | Int. quant | `tsy_fx_deal`, `tsy_fx_rate_daily` |
| `crm_` | CRM | Int. quant + Int. qual | `crm_account`, `crm_contact`, `crm_opportunity`, `crm_activity` (with `note_text`), `crm_account_plan`, `crm_wallet_estimate` |
| `credit_` | Credit workflow / spreading tool | Int. quant + Int. qual + Ext. quant | `credit_financial_statement`, `credit_ratio`, `credit_facility_terms`, `credit_covenant_test`, `credit_collateral`, `credit_review` (with `memo_excerpt`) |
| `kyc_` | KYC / onboarding workflow | Int. quant | `kyc_customer`, `kyc_case`, `kyc_case_stage`, `kyc_review`, `kyc_document`, `kyc_screening` |
| `ext_` | External data vendors | Ext. quant + Ext. qual | `ext_company_master` (LEI-like, registry), `ext_rating`, `ext_market_daily`, `ext_news` (headline, topic, raw sentiment), `ext_peer_benchmark`, `ext_trade_statistics` |
| `ews_` | Early-warning engine | Int. quant | `ews_trigger_catalog`, `ews_signal`, `ews_score`, `ews_override`, `ews_watchlist` |
| `fin_` | Finance / profitability | Int. quant | `fin_client_revenue`, `fin_cost_allocation`, `fin_capital_allocation`, `fin_deal_pricing` |
| `cf_` | Cashflow forecasting service | Int. quant | `cf_actual_daily`, `cf_forecast`, `cf_event` |
| `dq_` | Platform / governance | — | `dq_rule_result`, `dq_share_refresh_log` |
| `share_jp_` | JP lakehouse via Delta Sharing | Shared | `share_jp_group_master`, `share_jp_parent_exposure_monthly`, `share_jp_parent_financials`, `share_jp_parent_rating`, `share_jp_support_letters` (keepwell/guarantee register) |
| `share_emea_`, `share_amer_` | EMEA / Americas lakehouses | Shared | `share_<region>_entity_master`, `share_<region>_exposure_monthly`, `share_<region>_revenue_monthly` |
| `ref_` | Reference data | — | `ref_country`, `ref_currency`, `ref_industry_peer_group`, `ref_holiday`, `ref_product` |

**Entity-resolution truth table.** The generator must first create the *true* universe (`ops.synthetic_truth_client`: one row per true legal entity with its group) and then fragment it into source-system records with controlled noise (name variants, 2–4 source IDs per entity, 6% duplicates within a source, 2% wrong country, 3% stale parent link, 1.5% entities present in only one source). Silver resolution is evaluated against this truth table (precision/recall in tests).

### 6.2 Target volumes at `SCALE = 1.0` (dims fixed; facts scale linearly)

- 380 groups → 2,500 APAC legal entities (+ 600 EMEA/AMER entities in `shared`, 380 JP parents) → ≈ 7,900 fragmented source records across systems before resolution.
- Accounts 9,000; deposit daily ≈ 8M rows, monthly ≈ 380k; payments ≈ 1.8M; trade instruments ≈ 140k with 400k events and 90k presentations; SCF 40 programmes / 1,900 suppliers / 300k invoices; FX deals ≈ 300k.
- Loan facilities 1,900 → monthly snapshots ≈ 80k; covenant tests ≈ 11k; collateral ≈ 2,600; financial statements 2,500 clients × 3 FYs × 3 statement types ≈ 22k lines-tables (store long format ≈ 700k lines); ratios ≈ 7,500 × ratio set; peer benchmarks 40 peer groups × 20 ratios × 3 FYs.
- Credit reviews ≈ 3,600; EWS signals ≈ 260k; EWS scores daily 2,500 × ~900 ≈ 2.3M (or business days only); watchlist events ≈ 600; DPD daily on ≈ 300 facilities with any arrears.
- CRM: activities ≈ 60k (with note text), opportunities ≈ 2,600, account plans 380 × 4 FYs, wallet estimates 380 × 3; opportunity signals ≈ 45k; NBP scores 2,500 × 12 products × monthly (keep last 6 months only ≈ 180k).
- Onboarding cases ≈ 2,100 with ≈ 12k stage events; KYC reviews ≈ 9,000; screenings ≈ 150k.
- Revenue monthly client × product ≈ 600k; cost and capital allocation ≈ 105k each; deal pricing ≈ 1,100.
- Cashflow actual daily ≈ 2,500 × 900 × 6 categories (aggregate to ≈ 3M); forecasts weekly × 3 horizons × 2 model versions ≈ 1.4M; events ≈ 1,800.
- News ≈ 25k items; RM note sentiment ≈ 60k; market daily for ≈ 220 listed parents ≈ 200k; unified signals ≈ 300k.
- Entity-resolution runs: 6 (quarterly); coverage monthly 2,500 × 8 sources × 42; DQ scores monthly ≈ 120 tables × 5 dimensions × 42; share freshness daily 3 regions × ~6 tables × 900.

### 6.3 Realism rules

- Pareto concentration; lognormal tickets; business-day seasonality by location; March fiscal-year-end spikes in deposits, payments, limit utilisation and covenant tests (most covenants test on FY-end financials, i.e. March for Japanese groups, December for many others).
- Rating grade correlates with sector, segment and country; Japanese-corporate subsidiaries rate better (parent support) and price tighter.
- Financial statements are internally consistent (BS balances, CF reconciles to cash movement within rounding); ratios are computed, not drawn.
- Payments carry realistic counterparties: ≈ 35% of supplier payments go to other banks; a subset of clients route FX settlements via other banks (opportunity signal).
- News sentiment is weakly correlated with subsequent rating migration (so "signals that precede downgrades" has a real answer) and with industry cycles (coal, shipping negative in 2025–26; renewables, data centres positive).
- Cashflow forecasts: model v1 until May 2026 (MAPE ≈ 18%), v2 from June 2026 (MAPE ≈ 11%).
- Budget/plan optimism: account-plan targets +8–12% over prior-year actuals.

### 6.4 Embedded storylines (make them true in the data; assert in `tests/test_storylines.py` and `ops.storyline_assertions`)

1. **`Sunda Energi Nusantara` — the full early-warning cascade (Credit + Signals).** Jan 2026 negative news cluster (coal regulation, sentiment ≈ −0.6) → Feb deposit outflow 35% in 30 days → Mar utilisation 95% → Apr 15 DPD then 30 DPD → May covenant breach (Net Debt/EBITDA 4.6x vs 4.0x) on FY2025 spread → Jun downgrade grade 7 → 9, Stage 3, watchlist Red, ECL +USD 48m. EWS score 22 → 78 over five months; annual review submitted 23 days late. (5.3, 5.4, 5.10, 5.11.)
2. **`Kinokawa Precision` — wallet loss and recapture (RM).** Refinanced a USD 400m facility elsewhere in Q4 FY2025; from Jan 2026 payments show loan-service outflows to another bank (signal `LOAN_SERVICE_TO_OTHER_BANK`); revenue −22% YoY while deposits hold; FY2026 account plan reset mid-year; CN→VN trade flows up 50% → `TRADE_CORRIDOR_GROWTH` + `SCF_ANCHOR_CANDIDATE` + `FX_FLOW_VIA_OTHER_BANK` signals; NBP ranks SCF #1; a USD 120m SCF opportunity enters pipeline in Aug 2026. (5.1, 5.2, 5.7, 5.8.)
3. **`Meridian Agri Holdings` — why entity resolution matters (Foundation).** Exists as five source records (core banking ×2 with different spellings, CRM, trade, KYC) and one wrong country; resolved in the March 2026 run into one golden record; group exposure appears 40% higher after resolution and crosses the single-borrower attention threshold; a 2025 onboarding case for a new Thai subsidiary was not matched to the group at the time. (5.11, 5.6, 5.3.)
4. **`Tanaka Chemical Asia` — parent support from the JP share (Credit + Foundation).** APAC subsidiary turns Amber (utilisation + weak FY2025 ICR) in Jul 2026, but `share_jp_support_letters` shows a keepwell and `share_jp_parent_rating` shows a parent upgrade in Jun 2026 → EWS override to Green with reason "parent support confirmed (JP data)". (5.4, 5.1, 5.3.)
5. **HK CASA migration (TB + Cashflow).** Three Japanese electronics subsidiaries in Hong Kong move ≈ USD 900m from current accounts to 3–6-month time deposits Jun–Aug 2026; HK CASA ratio 62% → 49%; cashflow forecasts had flagged surpluses in May; `DEPOSIT_SURPLUS` signals fired but were actioned 40+ days late. (5.7, 5.9, 5.2.)
6. **Vietnam and India trade surge (TB + RM).** Import LC issuance into VN and IN +40% YoY in H1 FY2026 (electronics, auto parts; corridors from CN/KR/JP); Singapore books 45%; 14 corridor-growth opportunities created, 60% converted. (5.8, 5.2.)
7. **Onboarding backlog after KYC migration (Others).** Feb 2026 workflow migration: median days-to-live 21 → 38 in Mar–May 2026, FI segment worst (KYC Docs stage +12 days); high-risk periodic reviews overdue peak May at 3× normal; 70% cleared by Sep. (5.6.)
8. **Below-hurdle cluster (Profitability).** ≈ 40 single-product Japanese-corporate lending relationships sit below the 8% RoRWA hurdle; Sponsor & Structured Finance segment leads at ≈ 14%; 9 deals approved below hurdle in FY2025 on "relationship" exceptions, 3 of which caught up in realised RAROC. (5.5, 5.2.)
9. **Cashflow model upgrade (AI).** Model v2 from Jun 2026 cuts 30-day MAPE 18% → 11%; 23 predicted shortfalls in Jun–Sep, 15 followed by an RCF drawdown within 10 days (validation of the signal). (5.9.)
10. **Australian renewables sponsor — positive signals (Signals + RM).** Strongly positive expansion news Apr–Jul 2026, two green loans in pipeline, one SLL-eligible signal; demonstrates the opportunity side of the signal feed. (5.10, 5.2.)
11. **Covenant blind spot (Credit).** Five clients with leverage headroom < 10% at the FY2025 test; three are not on the watchlist (gap the Credit Memo and EWS spaces should both surface). (5.3, 5.4.)
12. **Reprocessed payments file (Medallion).** Bronze payments batch for 17 Jun 2026 landed twice; silver dedup removes ≈ 41k duplicates and logs them in `dq_results`; TB metrics unaffected. (5.7, 5.11.)
13. **Delta Share staleness (Foundation).** The JP share's `parent_rating` table goes stale for 5 days in Aug 2026 (lag > 24h) — visible in freshness metrics and flagged in the EWS override audit. (5.11.)

### 6.5 Generation approach

- Pure PySpark generators (pandas → Spark allowed for dims and text) under `src/10_bronze_synth/generators/`, one per source prefix, each exposing `generate(spark, cfg, truth) -> dict[str, DataFrame]`. Order: `truth` (true entities and groups) → reference → `shared` → quantitative facts → qualitative text → `fragment_identities()` → `storylines.py` injection → write.
- Seed per partition key (hash of entity id + seed) for reproducibility under parallelism.
- Qualitative text from templates with slot-filling (topic, product, amount bucket, tone); sentiment either computed by `ai_analyze_sentiment` in silver (if `USE_AI_FUNCTIONS` resolves to available) or assigned by the template's tone and lightly noised.
- Cashflow forecasts via `ai_forecast` over `fact_client_cashflow_daily` if available; otherwise a synthetic forecast = actual × (1 + noise) with the v1/v2 accuracy profile.
- Bronze written as Delta with `_batch_id`, `_ingest_ts`; re-runs overwrite by default.

---

## 7. Execution plan (plan → execute → verify → commit, per phase)

**Phase 0 — Discover and confirm (no writes).** CLI/SDK version, auth profile, cloud, workspace URL; SQL warehouses; runtimes supporting Metric View YAML 1.1; `CREATE CATALOG` privilege; whether Genie Spaces can be created via API/SDK; whether `ai_analyze_sentiment` / `ai_forecast` are available on the warehouse; whether a Databricks MCP server or `databricks-solutions/ai-dev-kit` skills are installed (prefer them); whether a second workspace exists for a real Delta Share. Report; ask ≤ 5 blocking questions; otherwise proceed with fill-in defaults.

**Phase 1 — Scaffold.** Repo tree (Appendix C), `databricks.yml`, `config/smbc_genie.yaml`, `requirements.txt`, `Makefile`/`scripts/`, `docs/DECISIONS.md`.

**Phase 2 — Catalog setup.** Catalog; schemas `bronze, shared, silver, gold, metrics, ops`; grants (`OWNER_GROUP` owner; `CONSUMER_GROUP` `USE CATALOG`/`USE SCHEMA`/`SELECT` on gold + metrics only); UC tags (`domain`, `layer`, `quadrant`, `pii=false`, `synthetic=true`, `source_region`); `ops` tables; SQL functions `fn_fiscal_year`, `fn_fiscal_quarter`, `fn_latest_closed_month`, `fn_usd`.

**Phase 3 — Synthetic truth + bronze + shared.** Truth universe → reference → shared-in tables → quantitative feeds → qualitative text → identity fragmentation → storyline injection → write. Row counts to `ops.build_run_log`.

**Phase 4 — Silver, including entity resolution.** DQ rules; standardisation; **resolution pipeline** (deterministic → fuzzy with blocking on country + name tokens → steward queue with a seeded auto-decision for the demo) → `client_xref`, `client_golden` SCD2, `ops.entity_resolution_runs` with precision/recall vs truth; conform shared-in tables to golden/group keys; quarantine orphans; `dq_results`. Document in `docs/ENTITY_RESOLUTION.md`.

**Phase 5 — Gold.** Hub dims, facts, constraints, clustering, comments, statistics; Mermaid ER in `docs/DATA_MODEL.md`; a `gold.vw_client_360` convenience view (one row per golden client with the headline attributes and latest KPIs) for drill-through.

**Phase 6 — Metric views.** One SQL file per view under `src/40_metrics/`; create, validate with `MEASURE()`, reconcile, record; `metrics/_glossary.md` of definitions copied into each space's instructions.

**Phase 7 — Genie Spaces.** `genie/<slug>/space_spec.json`, `benchmarks.json`, `instructions.md` per space; metadata-completeness gate; create via API if supported, else runbook and stop.

**Phase 8 — Validation.** `tests/` (counts, FKs, DQ bands, ER precision/recall ≥ 0.97/0.95, storylines, reconciliations); execute every benchmark's expected SQL; if the Genie API allows, run benchmarks and report pass rate (target ≥ 85%); up to three fix-and-rerun iterations, then report residuals.

**Phase 9 — Handover.** `README.md`; `DEMO_SCRIPT.md` — a 30-minute narrative that follows storyline 1 (Sunda Energi) through Signals → Early Warning → Credit Memo → Profitability, storyline 2 (Kinokawa) through Account Planning → Opportunity → TB, and storyline 3 (Meridian Agri) in the Data Foundation space; `TEARDOWN.md` + `scripts/teardown.sql` (confirmation required).

**Phase 10 — Optional (ask me before starting).** (a) Credit-memo drafting agent that calls the Credit Memo Genie space via the Genie Conversation API and assembles a memo draft from the pack; (b) real Delta Share from a second workspace into `shared`; (c) UC row filters by coverage office and column masks on note text; (d) AI/BI dashboards per space from the same metric views; (e) a scheduled job that rolls `AS_OF_DATE` monthly.

---

## 8. Genie Space configuration standard (apply to all eleven)

**Description template:** "Ask about <domain> for SMBC's APAC clients. Figures are USD unless you ask for local currency; periods follow the Japanese fiscal year (Apr–Mar). Client names roll up to client groups; data is synthetic and as of 30 Sep 2026."

**General instructions — base block (then ≤ 10 domain lines from Section 5):**
```
- Fiscal year runs 1 April to 31 March. FY2026 = Apr 2026 to Mar 2027. Q1 = Apr-Jun, Q2 = Jul-Sep, Q3 = Oct-Dec, Q4 = Jan-Mar. H1 = Apr-Sep, H2 = Oct-Mar.
- "This year" / "YTD" means fiscal year to date; "latest month" means September 2026 unless the user names a month.
- Default currency is USD. Use local currency only when asked.
- "Client" or "group" without qualification means the client group (global parent); "entity" means the individual legal entity. When asked "which clients", answer at group level with the top 20 and show the coverage office.
- "APAC" means all APAC booking entities. "Country" means booking country unless the user says "risk country", "client country" or "HQ country". "Global" or "including Japan" means use the regional (JP/APAC/EMEA/AMER) dimension.
- "Japanese corporates" means Is Japanese Corporate = true. "Strategic clients" means Relationship Tier = Strategic.
- Balance-type measures (deposits, exposure, RWA, ECL, scores, headcount) are point-in-time month-end values: group by month when a question spans several months; never sum them across months. Use the Average measure when the user says "average".
- Year-on-year compares the same fiscal period in the prior fiscal year.
- Prefer measures from the metric views; use MEASURE() syntax.
- If the question is ambiguous between two metric views, ask one clarifying question rather than guessing.
```

**Trusted assets:** parameterised example queries with named parameters (`:client_group`, `:fiscal_year`, `:month`, `:booking_entity`), each tied to a must-answer question (e.g. "credit memo pack for :client_group", "account plan status for :client_group", "EWS timeline for :client_group", "CASA movement by country for :month"); SQL functions in `smbc_genie.gold`.

**Benchmarks:** `{question, expected_sql, expected_checks: {min_rows, must_contain_columns, top_row_contains}}`; every must-answer question plus 4–8 phrasing variants (casual, abbreviated, with a typo, in banker jargon such as "utilisation", "headroom", "RoRWA", "CASA", "DPD").

**Metadata hygiene gate:** before creating a space, verify every column of every asset has a non-empty comment and every metric view measure has a description; fail the phase otherwise.

---

## 9. Definition of done

- [ ] `smbc_genie` with `bronze`, `shared`, `silver`, `gold`, `metrics`, `ops`; grants and tags applied.
- [ ] Synthetic truth universe generated; bronze and shared feeds written at `SCALE` with identity fragmentation and all 13 storylines injected.
- [ ] Silver entity resolution produces `client_xref` and SCD2 `client_golden` with precision ≥ 0.97 and recall ≥ 0.95 against truth; DQ logged; duplicate 17-June payments batch removed and recorded.
- [ ] Gold Customer 360 star schema with constraints, clustering, statistics, 100% column comments and `vw_client_360`.
- [ ] ≥ 40 metric views created, validated with `MEASURE()`, reconciled; `metrics/_glossary.md` complete.
- [ ] 8 (or 11) Genie Spaces created or fully specified with the exact names in Section 5, each with instructions, trusted assets, sample questions, benchmarks; `CONSUMER_GROUP` can run them.
- [ ] Every must-answer question returns a sensible result via its expected SQL; storyline assertions pass.
- [ ] `tests/` pass at `SCALE = 0.1` and `1.0`; `databricks bundle deploy` + `build_all` runs clean from an empty catalog.
- [ ] Docs complete: README, DATA_MODEL (ER), ENTITY_RESOLUTION, GENIE_RUNBOOK, DEMO_SCRIPT, DECISIONS, TEARDOWN.

---

## 10. Appendices

### A. Metric view skeleton (verify current syntax before use)

```sql
CREATE OR REPLACE VIEW smbc_genie.metrics.mv_ews_scores
WITH METRICS
LANGUAGE YAML
COMMENT 'Early-warning composite scores per client per day (0-100; Green <40, Amber 40-69, Red >=70). Point-in-time; group by Date or Month across periods.'
AS $$
version: 1.1
comment: Daily early-warning scores and bands with client, coverage and credit attributes.
source: smbc_genie.gold.fact_ews_score_daily
joins:
  - name: client
    source: smbc_genie.gold.dim_client
    on: source.golden_client_sk = client.golden_client_sk
  - name: grp
    source: smbc_genie.gold.dim_client_group
    on: client.client_group_id = grp.client_group_id
  - name: cal
    source: smbc_genie.gold.dim_date
    on: source.score_date = cal.date
dimensions:
  - name: Date
    expr: source.score_date
  - name: Month
    expr: DATE_TRUNC('MONTH', source.score_date)
  - name: Fiscal Year
    expr: cal.fiscal_year_label
  - name: Client Group
    expr: grp.group_name
  - name: Client
    expr: client.legal_name
  - name: Segment
    expr: client.segment
  - name: Relationship Tier
    expr: client.relationship_tier
  - name: Industry
    expr: client.industry_sector
  - name: Coverage Office
    expr: client.coverage_office
  - name: Band
    expr: source.band
  - name: Rating Grade
    expr: client.internal_rating_grade
measures:
  - name: Average Score
    expr: AVG(source.composite_score)
  - name: Clients Red
    expr: COUNT(DISTINCT CASE WHEN source.band = 'Red' THEN source.golden_client_sk END)
  - name: Clients Amber
    expr: COUNT(DISTINCT CASE WHEN source.band = 'Amber' THEN source.golden_client_sk END)
  - name: Exposure in Red USD
    expr: SUM(CASE WHEN source.band = 'Red' THEN source.drawn_exposure_usd END)
  - name: Entered Red
    expr: SUM(CASE WHEN source.band = 'Red' AND source.band_change_flag THEN 1 ELSE 0 END)
  - name: Overrides
    expr: SUM(CASE WHEN source.override_flag THEN 1 ELSE 0 END)
$$;

-- validation
SELECT `Industry`, MEASURE(`Clients Red`) AS red, MEASURE(`Exposure in Red USD`) AS red_usd
FROM smbc_genie.metrics.mv_ews_scores
WHERE `Date` = DATE'2026-09-30'
GROUP BY ALL ORDER BY red_usd DESC;
```

### B. `space_spec.json` shape

```json
{
  "title": "APAC Genie - Early Warning Monitoring",
  "description": "...",
  "warehouse_name": "<SQL_WAREHOUSE_NAME>",
  "assets": [
    "smbc_genie.metrics.mv_ews_scores", "smbc_genie.metrics.mv_ews_signals",
    "smbc_genie.metrics.mv_watchlist", "smbc_genie.metrics.mv_delinquency",
    "smbc_genie.gold.dim_client", "smbc_genie.gold.dim_client_group", "smbc_genie.gold.dim_date"
  ],
  "general_instructions": "...",
  "sample_questions": ["..."],
  "trusted_queries": [{"name": "EWS timeline for a client group", "parameters": [{"name": "client_group", "type": "STRING"}], "sql": "..."}],
  "sql_functions": ["smbc_genie.gold.fn_fiscal_year", "smbc_genie.gold.fn_latest_closed_month"],
  "benchmarks_file": "benchmarks.json"
}
```

### C. Repo layout

```
smbc-apac-genie/
├── databricks.yml
├── config/smbc_genie.yaml
├── src/
│   ├── 00_setup/            # catalog, schemas, grants, tags, ops tables, SQL functions
│   ├── 10_bronze_synth/     # truth.py, generators/, fragment_identities.py, storylines.py, run_bronze.py
│   ├── 20_silver/           # dq_rules.sql, entity_resolution/, transforms per domain, scd2_client.py
│   ├── 30_gold/             # dims.sql, facts.sql, constraints.sql, comments.sql, vw_client_360.sql
│   ├── 40_metrics/          # one .sql per metric view + validate_metrics.py
│   └── 50_genie/            # build_space_specs.py, create_spaces.py (API path), run_benchmarks.py
├── genie/<space_slug>/{space_spec.json, benchmarks.json, instructions.md}
├── metrics/_glossary.md
├── tests/
├── docs/{README.md, DATA_MODEL.md, ENTITY_RESOLUTION.md, GENIE_RUNBOOK.md, DEMO_SCRIPT.md, DECISIONS.md, TEARDOWN.md}
└── scripts/
```

### D. Space slugs

`account_planning`, `opportunity_identification`, `credit_memo_financial_spreading`, `early_warning_monitoring`, `client_profitability_roe`, `client_onboarding_kyc`, `tb_cash_payments_liquidity`, `tb_trade_scf`, `cashflow_forecasting`, `signals_sentiment`, `customer360_data_foundation`.

### E. How to begin

Start with Phase 0 now. Print the discovery report, the proposed repo tree, and any blocking questions. Do not create anything in the workspace until I reply "go".
