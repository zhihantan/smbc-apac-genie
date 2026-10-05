-- fact_account_plan_annual (WP8c): client group x fiscal year x product family, FY2023-FY2026. Union of the CRM
-- account-plan lines (silver.crm_account_plan: target, mid-year revision, owner RM), the wallet estimates
-- (silver.crm_wallet_estimate) and the actual revenue (gold.fact_client_revenue_monthly, product feeds, summed over
-- the group's golden clients), so groups without a plan still show revenue and wallet. Year to date = the fiscal
-- year's months up to the as-of date (FY2026 = Apr-Sep 2026); prior YTD = the same months one year earlier.
WITH fy AS (
  SELECT fiscal_year, max(fiscal_year_label) AS fiscal_year_label, max(fiscal_year_end_date) AS fiscal_year_end_date,
         count(DISTINCT CASE WHEN date <= DATE'${as_of_date}' THEN month_start_date END) AS months_elapsed
  FROM ${catalog}.gold.dim_date GROUP BY 1
),
plan AS (SELECT * FROM ${catalog}.silver.crm_account_plan),
plan_hdr AS (     -- one plan per group x fiscal year
  SELECT client_group_id, fiscal_year, max(plan_id) AS plan_id, max(crm_account_id) AS plan_crm_account_id,
         max(plan_status) AS plan_status, max(approved_date) AS plan_approved_date, max(owner_rm) AS owner_rm_code,
         max(strategic_priority) AS strategic_priority, max(plan_objective) AS plan_objective,
         max(CAST(mid_year_revision AS INT)) = 1 AS plan_revised_mid_year
  FROM plan GROUP BY 1, 2
),
wallet AS (SELECT * FROM ${catalog}.silver.crm_wallet_estimate),
rev AS (
  SELECT client_group_id, fiscal_year, product_family, month, sum(total_revenue_usd) AS rev
  FROM ${catalog}.gold.fact_client_revenue_monthly
  WHERE revenue_source = 'Product feed' AND client_group_id IS NOT NULL
  GROUP BY 1, 2, 3, 4
),
rev_fy AS (SELECT client_group_id, fiscal_year, product_family, sum(rev) AS rev_ytd FROM rev GROUP BY 1, 2, 3),
rev_prior AS (    -- the same months one year earlier
  SELECT client_group_id, fiscal_year + 1 AS fiscal_year, product_family, sum(rev) AS rev_prior_ytd
  FROM rev WHERE add_months(month, 12) <= DATE'${as_of_date}'
  GROUP BY 1, 2, 3
),
init AS (
  SELECT client_group_id, fiscal_year, product_family,
         count(*) AS initiatives_planned, count_if(status = 'Completed') AS initiatives_completed,
         count_if(is_open) AS initiatives_open, count_if(is_overdue) AS initiatives_overdue,
         sum(target_revenue_usd) AS initiatives_target_revenue_usd
  FROM ${catalog}.gold.dim_account_plan_initiative GROUP BY 1, 2, 3
),
keys AS (
  SELECT client_group_id, fiscal_year, product_family FROM plan
  UNION SELECT client_group_id, fiscal_year, product_family FROM wallet
  UNION SELECT client_group_id, fiscal_year, product_family FROM rev_fy
  UNION SELECT client_group_id, fiscal_year, product_family FROM init
)
SELECT
  k.client_group_id, dl.golden_client_sk AS lead_golden_client_sk,
  k.fiscal_year, fy.fiscal_year_label, fy.fiscal_year_end_date, k.product_family,
  k.fiscal_year = (SELECT fiscal_year FROM ${catalog}.gold.dim_date WHERE date = DATE'${as_of_date}') AS is_current_fiscal_year,
  least(fy.months_elapsed, 12) AS months_elapsed,
  h.plan_id IS NOT NULL AS group_has_plan, p.plan_line_id IS NOT NULL AS has_plan_line,
  h.plan_id, p.plan_line_id, h.plan_crm_account_id, h.plan_status, h.plan_approved_date, h.strategic_priority,
  h.plan_objective, h.owner_rm_code, e.employee_id AS owner_rm_id, coalesce(h.plan_revised_mid_year, false) AS plan_revised_mid_year,
  p.planned_revenue_usd AS revenue_target_usd, p.revised_target_usd,
  coalesce(p.revised_target_usd, p.planned_revenue_usd) AS effective_target_usd,
  coalesce(p.mid_year_revision, false) AS mid_year_revision, p.revision_date, p.revision_reason, p.plan_optimism,
  p.wallet_share_target,
  CASE WHEN r.rev_ytd IS NOT NULL THEN r.rev_ytd
       WHEN k.fiscal_year >= (SELECT min(fiscal_year) FROM rev) AND k.product_family <> 'Supply Chain Finance' THEN 0D
       ELSE coalesce(p.actual_revenue_usd, nxt.prior_year_actual_usd) END AS revenue_actual_ytd_usd,
  CASE WHEN r.rev_ytd IS NOT NULL
         OR (k.fiscal_year >= (SELECT min(fiscal_year) FROM rev) AND k.product_family <> 'Supply Chain Finance')
       THEN 'Finance revenue' ELSE 'CRM recorded actual' END AS actual_basis,
  p.actual_revenue_usd AS crm_actual_revenue_usd,
  CASE WHEN rp.rev_prior_ytd IS NOT NULL THEN rp.rev_prior_ytd
       WHEN k.fiscal_year - 1 >= (SELECT min(fiscal_year) FROM rev) AND k.product_family <> 'Supply Chain Finance' THEN 0D
       WHEN fy.months_elapsed >= 12 THEN p.prior_year_actual_usd END AS revenue_prior_ytd_usd,
  p.prior_year_actual_usd,
  w.estimated_wallet_usd, w.smbc_revenue_usd AS wallet_smbc_revenue_usd, w.smbc_revenue_basis,
  w.smbc_revenue_usd * 12.0 / least(fy.months_elapsed, 12) AS wallet_smbc_revenue_annualised_usd,
  w.share_of_wallet, cb.bank_id AS top_competitor_bank_id, w.top_competitor_bank, w.top_competitor_share,
  w.estimation_method,
  coalesce(i.initiatives_planned, 0) AS initiatives_planned, coalesce(i.initiatives_completed, 0) AS initiatives_completed,
  coalesce(i.initiatives_open, 0) AS initiatives_open, coalesce(i.initiatives_overdue, 0) AS initiatives_overdue,
  coalesce(i.initiatives_target_revenue_usd, 0) AS initiatives_target_revenue_usd
FROM keys k
JOIN fy ON fy.fiscal_year = k.fiscal_year
LEFT JOIN plan p ON p.client_group_id = k.client_group_id AND p.fiscal_year = k.fiscal_year AND p.product_family = k.product_family
LEFT JOIN plan nxt ON nxt.client_group_id = k.client_group_id AND nxt.fiscal_year = k.fiscal_year + 1 AND nxt.product_family = k.product_family
LEFT JOIN plan_hdr h ON h.client_group_id = k.client_group_id AND h.fiscal_year = k.fiscal_year
LEFT JOIN wallet w ON w.client_group_id = k.client_group_id AND w.fiscal_year = k.fiscal_year AND w.product_family = k.product_family
LEFT JOIN rev_fy r ON r.client_group_id = k.client_group_id AND r.fiscal_year = k.fiscal_year AND r.product_family = k.product_family
LEFT JOIN rev_prior rp ON rp.client_group_id = k.client_group_id AND rp.fiscal_year = k.fiscal_year AND rp.product_family = k.product_family
LEFT JOIN init i ON i.client_group_id = k.client_group_id AND i.fiscal_year = k.fiscal_year AND i.product_family = k.product_family
LEFT JOIN ${catalog}.gold.dim_employee e ON e.rm_code = h.owner_rm_code
LEFT JOIN ${catalog}.gold.dim_competitor_bank cb ON cb.bank_name = w.top_competitor_bank
LEFT JOIN ${catalog}.gold.dim_client_group dg ON dg.client_group_id = k.client_group_id
LEFT JOIN ${catalog}.gold.dim_client dl
  ON  dl.golden_client_id = dg.lead_golden_client_id
  AND least(fy.fiscal_year_end_date, DATE'${as_of_date}') <= dl.valid_to
  AND (least(fy.fiscal_year_end_date, DATE'${as_of_date}') >= dl.valid_from OR dl.version_no = 1)
