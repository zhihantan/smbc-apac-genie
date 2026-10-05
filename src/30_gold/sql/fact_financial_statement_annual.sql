-- fact_financial_statement_annual (WP8d, brief 5.3): spread financial statements in long format, one row per
-- credit obligor x fiscal year x statement line, from silver.credit_financial_statement, plus a derived
-- Net Debt line (Total Debt - Cash), prior-year amounts / YoY growth and the year's spreading task
-- (analyst, checker, basis, status). Each client keeps its own fiscal year-end (D17: Dec or Mar).
-- golden_client_sk = the dim_client version valid on the fiscal year-end (D11).
WITH fs AS (
  SELECT obligor_id, golden_client_id, client_group_id, CAST(fiscal_year AS INT) AS fiscal_year, fiscal_year_label,
         CAST(fiscal_year_end AS DATE) AS fiscal_year_end_date, statement_type, line_item,
         amount_usd, amount_lcy, currency, is_audited, is_spread, spread_date, false AS is_derived_line
  FROM ${catalog}.silver.credit_financial_statement
),
net_debt AS (   -- derived line: Net Debt = Total Debt - Cash (same statement year, same currency)
  SELECT obligor_id, golden_client_id, client_group_id, fiscal_year, fiscal_year_label, fiscal_year_end_date,
         'Balance Sheet' AS statement_type, 'Net Debt' AS line_item,
         sum(CASE WHEN line_item = 'Total Debt' THEN amount_usd END) - sum(CASE WHEN line_item = 'Cash' THEN amount_usd END) AS amount_usd,
         sum(CASE WHEN line_item = 'Total Debt' THEN amount_lcy END) - sum(CASE WHEN line_item = 'Cash' THEN amount_lcy END) AS amount_lcy,
         max(currency) AS currency, bool_and(is_audited) AS is_audited, bool_and(is_spread) AS is_spread,
         max(spread_date) AS spread_date, true AS is_derived_line
  FROM fs WHERE line_item IN ('Total Debt', 'Cash')
  GROUP BY obligor_id, golden_client_id, client_group_id, fiscal_year, fiscal_year_label, fiscal_year_end_date
  HAVING count(DISTINCT line_item) = 2
),
lines AS (SELECT * FROM fs UNION ALL SELECT * FROM net_debt),
line_meta (line_item, line_order, is_cost_or_outflow, is_subtotal) AS (
  VALUES ('Revenue', 10, false, false), ('COGS', 20, true, false), ('Gross Profit', 30, false, true),
         ('Operating Expenses', 40, true, false), ('EBITDA', 50, false, true), ('Depreciation & Amortisation', 60, true, false),
         ('EBIT', 70, false, true), ('Interest Expense', 80, true, false), ('Pre-Tax Profit', 90, false, true),
         ('Tax', 100, true, false), ('Net Income', 110, false, true),
         ('Cash', 210, false, false), ('Receivables', 220, false, false), ('Inventory', 230, false, false),
         ('Total Current Assets', 240, false, true), ('Fixed Assets', 250, false, false), ('Total Assets', 260, false, true),
         ('Payables', 270, false, false), ('Short-Term Debt', 280, false, false), ('Long-Term Debt', 290, false, false),
         ('Total Debt', 300, false, true), ('Net Debt', 305, false, true), ('Equity', 310, false, false),
         ('Cash Flow from Operations', 410, false, false), ('Capex', 420, true, false), ('Free Cash Flow', 430, false, true),
         ('Dividends Paid', 440, true, false)
),
with_prior AS (
  SELECT l.*,
         CASE WHEN lag(fiscal_year) OVER w = fiscal_year - 1 THEN lag(amount_usd) OVER w END AS prior_year_amount_usd,
         CASE WHEN lag(fiscal_year) OVER w = fiscal_year - 1 THEN lag(amount_lcy) OVER w END AS prior_year_amount_lcy
  FROM lines l
  WINDOW w AS (PARTITION BY obligor_id, line_item ORDER BY fiscal_year)
),
task AS (
  SELECT obligor_id, CAST(fiscal_year AS INT) AS fiscal_year, task_id, analyst_id, checker_id, status, statement_basis,
         received_date, spread_date AS task_spread_date
  FROM ${catalog}.silver.credit_spreading_task
)
SELECT
  f.obligor_id,
  dc.golden_client_sk,
  f.golden_client_id,
  f.client_group_id,
  f.fiscal_year,
  f.fiscal_year_label,
  f.fiscal_year_end_date,
  CAST(month(f.fiscal_year_end_date) AS INT)                                        AS fye_month,
  f.statement_type,
  f.line_item,
  CAST(m.line_order AS INT)                                                         AS line_order,
  coalesce(m.is_cost_or_outflow, false)                                             AS is_cost_or_outflow,
  coalesce(m.is_subtotal, false)                                                    AS is_subtotal,
  f.is_derived_line,
  f.amount_usd,
  f.amount_lcy,
  f.currency,
  f.prior_year_amount_usd,
  f.prior_year_amount_lcy,
  CASE WHEN f.prior_year_amount_lcy > 0 THEN f.amount_lcy / f.prior_year_amount_lcy - 1 END AS yoy_growth_pct,
  f.is_audited,
  t.statement_basis,
  f.is_spread,
  coalesce(f.spread_date, t.task_spread_date)                                       AS spread_date,
  t.task_id                                                                         AS spreading_task_id,
  t.status                                                                          AS spreading_status,
  t.received_date                                                                   AS statement_received_date,
  t.analyst_id                                                                      AS spreading_analyst_id,
  t.checker_id                                                                      AS spreading_checker_id
FROM with_prior f
LEFT JOIN line_meta m ON m.line_item = f.line_item
LEFT JOIN task t ON t.obligor_id = f.obligor_id AND t.fiscal_year = f.fiscal_year
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = f.golden_client_id
  AND f.fiscal_year_end_date <= dc.valid_to
  AND (f.fiscal_year_end_date >= dc.valid_from OR dc.version_no = 1)
