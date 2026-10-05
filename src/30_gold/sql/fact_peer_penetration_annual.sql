-- fact_peer_penetration_annual (WP8c): peer group x core product x fiscal year (dense over gold.dim_peer_group),
-- aggregated from gold.fact_client_product_penetration_annual so the peer rates match the client-level fact exactly:
-- share of the peer group's active clients holding the product and the median activity of the holders.
WITH pg AS (SELECT peer_group_id, peer_group_name, industry_sector, industry_subsector FROM ${catalog}.gold.dim_peer_group),
prod AS (
  SELECT DISTINCT product_id, product_name, product_family, business_line, fiscal_year, fiscal_year_label, fiscal_year_end_date
  FROM ${catalog}.gold.fact_client_product_penetration_annual
),
agg AS (
  SELECT peer_group_id, product_id, fiscal_year,
         count(*) AS peer_group_clients, count_if(holds_product) AS peer_group_holders,
         percentile(CASE WHEN holds_product THEN activity_usd END, 0.5) AS median_activity_usd,
         max(CASE WHEN holds_product THEN activity_basis END) AS activity_basis,
         sum(CASE WHEN holds_product THEN activity_usd END) AS total_activity_usd
  FROM ${catalog}.gold.fact_client_product_penetration_annual
  WHERE peer_group_id IS NOT NULL
  GROUP BY 1, 2, 3
)
SELECT
  pg.peer_group_id, pg.peer_group_name, pg.industry_sector, pg.industry_subsector,
  p.fiscal_year, p.fiscal_year_label, p.fiscal_year_end_date,
  p.product_id, p.product_name, p.product_family, p.business_line,
  coalesce(a.peer_group_clients, 0) AS peer_group_clients, coalesce(a.peer_group_holders, 0) AS peer_group_holders,
  try_divide(a.peer_group_holders, a.peer_group_clients) AS penetration_pct,
  a.median_activity_usd, a.activity_basis, coalesce(a.total_activity_usd, 0) AS total_activity_usd
FROM pg
CROSS JOIN prod p
LEFT JOIN agg a ON a.peer_group_id = pg.peer_group_id AND a.product_id = p.product_id AND a.fiscal_year = p.fiscal_year
