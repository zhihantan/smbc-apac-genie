-- fact_client_pnl_waterfall_annual (WP8c): client x fiscal year ROE waterfall from gold.fact_client_revenue_monthly
-- (FY2024, FY2025 and FY2026 to the as-of date = H1): revenue -> opex -> credit cost -> pre-tax profit -> tax ->
-- net income -> allocated capital -> ROE. Tax = statutory corporate rate of the coverage office (fixed business rule
-- in gold, no tax credit on losses). ROE = annualised net income / average allocated capital (12.5% of RWA);
-- RoRWA / RAROC use the relationship-P&L net profit exactly as the monthly fact does. Flags use unwinsorised ratios.
WITH tax AS (
  SELECT * FROM VALUES
    ('SG', 0.17D), ('HK', 0.165D), ('CN', 0.25D), ('TH', 0.20D), ('ID', 0.22D), ('IN', 0.25D), ('AU', 0.30D),
    ('VN', 0.20D), ('MY', 0.24D), ('TW', 0.20D), ('KR', 0.24D), ('PH', 0.25D), ('NZ', 0.28D)
    AS t(coverage_office, tax_rate)
),
fy AS (
  SELECT fiscal_year, max(fiscal_year_label) AS fiscal_year_label, max(fiscal_year_end_date) AS fiscal_year_end_date,
         count(DISTINCT month_start_date) AS months_in_period, max(date) AS period_end_date
  FROM ${catalog}.gold.dim_date
  WHERE date <= DATE'${as_of_date}'
    AND month_start_date >= (SELECT min(month) FROM ${catalog}.gold.fact_client_revenue_monthly)
  GROUP BY 1
),
agg AS (
  SELECT golden_client_id, fiscal_year,
         sum(total_revenue_usd) AS revenue_usd, sum(nii_usd) AS nii_usd, sum(fee_usd) AS fee_usd, sum(trading_usd) AS trading_usd,
         sum(CASE WHEN business_line = 'Lending' THEN total_revenue_usd ELSE 0 END) AS lending_revenue_usd,
         sum(CASE WHEN business_line = 'Transaction Banking' THEN total_revenue_usd ELSE 0 END) AS tb_revenue_usd,
         sum(CASE WHEN business_line = 'Markets' THEN total_revenue_usd ELSE 0 END) AS markets_revenue_usd,
         sum(allocated_cost_usd) AS opex_usd, sum(credit_cost_usd) AS credit_cost_usd,
         sum(net_contribution_usd) AS pre_tax_profit_usd, sum(ecl_charge_usd) AS ecl_charge_usd,
         sum(ead_usd) AS ead_sum, sum(rwa_usd) AS rwa_sum, sum(economic_capital_usd) AS ec_sum,
         sum(allocated_capital_usd) AS ac_sum, sum(cost_of_capital_usd) AS cost_of_capital_usd,
         sum(relationship_net_profit_usd) AS relationship_net_profit_usd,
         max(CASE WHEN NOT is_prior_year_only THEN month END) AS last_month
  FROM ${catalog}.gold.fact_client_revenue_monthly
  GROUP BY 1, 2
  HAVING sum(abs(total_revenue_usd)) + sum(rwa_usd) > 0
),
last_row AS (     -- client-month attributes at the last month of the period
  SELECT golden_client_id, fiscal_year, ead_usd AS period_end_ead_usd, products_held_count, product_count_bucket,
         is_single_product_lending, is_lending_only_relationship, is_deposit_only
  FROM (SELECT f.*, row_number() OVER (PARTITION BY golden_client_id, fiscal_year ORDER BY month DESC) AS rn
        FROM ${catalog}.gold.fact_client_revenue_monthly f WHERE f.is_primary_row) x
  WHERE rn = 1
),
calc AS (
  SELECT a.*, fy.fiscal_year_label, fy.fiscal_year_end_date, fy.period_end_date, fy.months_in_period,
         l.period_end_ead_usd, l.products_held_count, l.product_count_bucket, l.is_single_product_lending,
         l.is_lending_only_relationship, l.is_deposit_only,
         a.ead_sum / fy.months_in_period AS avg_ead_usd, a.rwa_sum / fy.months_in_period AS avg_rwa_usd,
         a.ec_sum / fy.months_in_period AS avg_economic_capital_usd,
         a.ac_sum / fy.months_in_period AS avg_allocated_capital_usd,
         12.0 / fy.months_in_period AS ann
  FROM agg a
  JOIN fy ON fy.fiscal_year = a.fiscal_year
  LEFT JOIN last_row l ON l.golden_client_id = a.golden_client_id AND l.fiscal_year = a.fiscal_year
),
with_tax AS (
  SELECT c.*, dc.golden_client_sk, dc.client_group_id, dc.coverage_office,
         coalesce(t.tax_rate, 0.20D) AS tax_rate,
         greatest(c.pre_tax_profit_usd, 0) * coalesce(t.tax_rate, 0.20D) AS tax_usd
  FROM calc c
  LEFT JOIN ${catalog}.gold.dim_client dc
    ON  dc.golden_client_id = c.golden_client_id
    AND c.period_end_date <= dc.valid_to
    AND (c.period_end_date >= dc.valid_from OR dc.version_no = 1)
  LEFT JOIN tax t ON t.coverage_office = dc.coverage_office
),
ratios AS (
  SELECT w.*,
         w.pre_tax_profit_usd - w.tax_usd AS net_income_usd,
         try_divide((w.pre_tax_profit_usd - w.tax_usd) * w.ann, w.avg_allocated_capital_usd) AS roe_raw,
         try_divide(w.relationship_net_profit_usd * w.ann, w.avg_rwa_usd) AS rorwa_raw,
         try_divide(w.relationship_net_profit_usd * w.ann, w.avg_economic_capital_usd) AS raroc_raw
  FROM with_tax w
)
SELECT
  r.fiscal_year, r.fiscal_year_label, r.fiscal_year_end_date, r.period_end_date, r.months_in_period,
  r.months_in_period = 12 AS is_full_year,
  r.golden_client_sk, r.golden_client_id, r.client_group_id, r.coverage_office AS tax_jurisdiction,
  r.revenue_usd, r.nii_usd, r.fee_usd, r.trading_usd, r.lending_revenue_usd, r.tb_revenue_usd, r.markets_revenue_usd,
  r.opex_usd, r.credit_cost_usd, r.pre_tax_profit_usd, r.tax_rate, r.tax_usd, r.net_income_usd,
  r.net_income_usd * r.ann AS net_income_annualised_usd,
  r.avg_ead_usd, r.period_end_ead_usd, r.avg_rwa_usd, r.avg_economic_capital_usd, r.avg_allocated_capital_usd,
  r.cost_of_capital_usd, r.net_income_usd - r.cost_of_capital_usd AS economic_profit_usd, r.ecl_charge_usd,
  r.relationship_net_profit_usd, r.relationship_net_profit_usd * r.ann AS relationship_net_profit_annualised_usd,
  -- winsorise only real ratios: greatest() skips NULLs, so a NULL ratio (no capital) must stay NULL explicitly
  CASE WHEN r.roe_raw IS NOT NULL THEN least(1.0D, greatest(-0.25D, r.roe_raw)) END AS roe,
  CASE WHEN r.rorwa_raw IS NOT NULL THEN least(1.0D, greatest(-0.25D, r.rorwa_raw)) END AS rorwa,
  CASE WHEN r.raroc_raw IS NOT NULL THEN least(1.0D, greatest(-0.25D, r.raroc_raw)) END AS raroc,
  CASE WHEN r.roe_raw IS NOT NULL THEN least(1.0D, greatest(-0.25D, r.roe_raw)) - ${roe_hurdle} END AS roe_hurdle_gap,
  r.roe_raw < ${roe_hurdle} AS below_roe_hurdle,
  r.rorwa_raw < ${rorwa_hurdle} AS below_rorwa_hurdle,
  r.raroc_raw < ${raroc_hurdle} AS below_raroc_hurdle,
  try_divide(r.credit_cost_usd, r.revenue_usd) AS credit_cost_to_revenue,
  CASE WHEN r.revenue_usd > 0 THEN r.credit_cost_usd > r.revenue_usd *
       (SELECT threshold_value FROM ${catalog}.gold.dim_threshold WHERE threshold_code = 'CREDIT_COST_SHARE_HIGH') END
    AS is_credit_cost_above_30pct,
  try_divide(r.opex_usd, r.revenue_usd) AS cost_income_ratio,
  try_divide(r.revenue_usd * r.ann, r.avg_rwa_usd) AS revenue_per_rwa,
  r.avg_rwa_usd > 0 AS has_capital,
  r.products_held_count, r.product_count_bucket, coalesce(r.is_single_product_lending, false) AS is_single_product_lending,
  coalesce(r.is_lending_only_relationship, false) AS is_lending_only_relationship, coalesce(r.is_deposit_only, false) AS is_deposit_only
FROM ratios r
