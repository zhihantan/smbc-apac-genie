-- fact_next_best_product_score (WP8c): next-best-product propensity per golden client x product x score month
-- (silver.crm_nbp_score, model NBP-3.1, Apr-Sep 2026, top 3 products per CRM account and month). A golden client with
-- several CRM accounts keeps the best-scoring account's record per product (n_crm_accounts says how many scored).
-- Adds the drivers split into three, a high-propensity flag (dim_threshold NBP_HIGH_PROPENSITY) and whether the client
-- already holds the product family at that month-end (cross-sell vs deepen).
WITH s AS (
  SELECT n.*,
         row_number() OVER (PARTITION BY n.golden_client_id, n.score_month, n.product
                            ORDER BY n.propensity DESC, n.rank, n.crm_account_id) AS rn,
         count(*) OVER (PARTITION BY n.golden_client_id, n.score_month, n.product) AS n_crm_accounts
  FROM ${catalog}.silver.crm_nbp_score n
),
fam AS (
  SELECT DISTINCT golden_client_id, month_end_date, product_family
  FROM ${catalog}.gold.fact_product_holding_monthly WHERE is_active
)
SELECT
  s.score_month AS score_date, trunc(s.score_month, 'MM') AS score_month, d.fiscal_year, d.fiscal_year_label,
  d.fiscal_quarter_label,
  s.score_month = (SELECT max(score_month) FROM ${catalog}.silver.crm_nbp_score) AS is_latest_score_month,
  dc.golden_client_sk, s.golden_client_id, dc.client_group_id, s.crm_account_id, s.n_crm_accounts,
  s.product, dp.product_id, s.product_family,
  s.propensity, s.rank AS propensity_rank, s.rank = 1 AS is_top_ranked,
  s.propensity >= (SELECT threshold_value FROM ${catalog}.gold.dim_threshold WHERE threshold_code = 'NBP_HIGH_PROPENSITY')
    AS is_high_propensity,
  CASE WHEN s.propensity >= 0.7 THEN 'High (>= 0.7)' WHEN s.propensity >= 0.4 THEN 'Medium (0.4-0.7)' ELSE 'Low (< 0.4)' END
    AS propensity_band,
  s.top_drivers,
  trim(try_element_at(split(s.top_drivers, '[|]'), 1)) AS driver_1,
  trim(try_element_at(split(s.top_drivers, '[|]'), 2)) AS driver_2,
  trim(try_element_at(split(s.top_drivers, '[|]'), 3)) AS driver_3,
  s.expected_revenue_usd, s.expected_revenue_usd * s.propensity AS propensity_weighted_revenue_usd,
  s.model_version, s.owner_rm AS owner_rm_code, e.employee_id AS owner_rm_id,
  f.golden_client_id IS NOT NULL AS client_holds_product_family
FROM s
JOIN ${catalog}.gold.dim_date d ON d.date = s.score_month
LEFT JOIN ${catalog}.gold.dim_product dp ON dp.product_name = s.product
LEFT JOIN ${catalog}.gold.dim_employee e ON e.rm_code = s.owner_rm
LEFT JOIN fam f ON f.golden_client_id = s.golden_client_id AND f.month_end_date = s.score_month AND f.product_family = s.product_family
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = s.golden_client_id
  AND s.score_month <= dc.valid_to
  AND (s.score_month >= dc.valid_from OR dc.version_no = 1)
WHERE s.rn = 1
