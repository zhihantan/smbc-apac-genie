-- fact_client_product_penetration_annual (WP8c): dense golden client x core product x fiscal year (FY2024-FY2026;
-- FY2026 to the as-of date) for every client with an active product in the year (gold.fact_product_holding_monthly):
-- does the client hold the product, how widely its peer group (industry subsector, dim_client.peer_group_id) holds it,
-- and the gap. A gap = not held while at least half of the peer group holds it.
WITH fy AS (
  SELECT fiscal_year, max(fiscal_year_label) AS fiscal_year_label, max(fiscal_year_end_date) AS fiscal_year_end_date,
         least(max(fiscal_year_end_date), DATE'${as_of_date}') AS ref_date
  FROM ${catalog}.gold.dim_date
  WHERE fiscal_year BETWEEN (SELECT min(fiscal_year) FROM ${catalog}.gold.fact_client_revenue_monthly)
                        AND (SELECT fiscal_year FROM ${catalog}.gold.dim_date WHERE date = DATE'${as_of_date}')
  GROUP BY 1
),
hold AS (         -- client x product x FY usage
  SELECT h.golden_client_id, h.fiscal_year, h.product_id,
         count_if(h.is_active) AS months_active,
         avg(CASE WHEN h.is_held_at_month_end AND h.holding_basis IN ('Account balance', 'Facility limit') THEN h.balance_usd END) AS avg_balance_usd,
         sum(h.volume_month_usd) AS fy_volume_usd
  FROM ${catalog}.gold.fact_product_holding_monthly h
  JOIN fy ON fy.fiscal_year = h.fiscal_year
  GROUP BY 1, 2, 3
),
clients AS (      -- clients with any active product in the fiscal year
  SELECT golden_client_id, fiscal_year FROM hold GROUP BY 1, 2 HAVING sum(months_active) > 0
),
products AS (
  SELECT product_id, product_name, product_family, business_line FROM ${catalog}.gold.dim_product WHERE product_type = 'Core product'
),
grid AS (
  SELECT c.golden_client_id, c.fiscal_year, fy.fiscal_year_label, fy.fiscal_year_end_date, fy.ref_date,
         p.product_id, p.product_name, p.product_family, p.business_line,
         coalesce(h.months_active, 0) > 0 AS holds_product, coalesce(h.months_active, 0) AS months_active,
         CASE WHEN coalesce(h.months_active, 0) > 0 THEN coalesce(h.avg_balance_usd, h.fy_volume_usd) END AS activity_usd,
         CASE WHEN h.avg_balance_usd IS NOT NULL THEN 'Average balance' WHEN h.fy_volume_usd > 0 THEN 'Annual volume' END AS activity_basis
  FROM clients c
  JOIN fy ON fy.fiscal_year = c.fiscal_year
  CROSS JOIN products p
  LEFT JOIN hold h ON h.golden_client_id = c.golden_client_id AND h.fiscal_year = c.fiscal_year AND h.product_id = p.product_id
),
with_dc AS (
  SELECT g.*, dc.golden_client_sk, dc.client_group_id, dc.peer_group_id, dc.industry_sector
  FROM grid g
  LEFT JOIN ${catalog}.gold.dim_client dc
    ON  dc.golden_client_id = g.golden_client_id
    AND g.ref_date <= dc.valid_to
    AND (g.ref_date >= dc.valid_from OR dc.version_no = 1)
),
peer AS (
  SELECT w.*,
         CASE WHEN w.peer_group_id IS NOT NULL THEN count(*) OVER pp END AS peer_group_clients,
         CASE WHEN w.peer_group_id IS NOT NULL THEN count_if(w.holds_product) OVER pp END AS peer_group_holders,
         CASE WHEN w.peer_group_id IS NOT NULL THEN avg(CASE WHEN w.holds_product THEN 1.0D ELSE 0.0D END) OVER pp END AS peer_penetration_pct,
         CASE WHEN w.industry_sector IS NOT NULL THEN avg(CASE WHEN w.holds_product THEN 1.0D ELSE 0.0D END) OVER ps END AS sector_penetration_pct,
         avg(CASE WHEN w.holds_product THEN 1.0D ELSE 0.0D END) OVER pa AS all_clients_penetration_pct
  FROM with_dc w
  WINDOW pp AS (PARTITION BY w.peer_group_id, w.product_id, w.fiscal_year),
         ps AS (PARTITION BY w.industry_sector, w.product_id, w.fiscal_year),
         pa AS (PARTITION BY w.product_id, w.fiscal_year)
)
SELECT
  fiscal_year, fiscal_year_label, fiscal_year_end_date, golden_client_sk, golden_client_id, client_group_id, peer_group_id,
  product_id, product_name, product_family, business_line,
  holds_product, CASE WHEN holds_product THEN 1 ELSE 0 END AS holds_product_flag, months_active, activity_usd, activity_basis,
  peer_group_clients, peer_group_holders, peer_penetration_pct,
  peer_penetration_pct - CASE WHEN holds_product THEN 1 ELSE 0 END AS penetration_gap_pts,
  NOT holds_product AND peer_penetration_pct >= 0.5 AS is_gap_vs_peers,
  sector_penetration_pct, sector_penetration_pct - CASE WHEN holds_product THEN 1 ELSE 0 END AS gap_vs_sector_pts,
  all_clients_penetration_pct, all_clients_penetration_pct - CASE WHEN holds_product THEN 1 ELSE 0 END AS gap_vs_all_clients_pts,
  NOT holds_product AND all_clients_penetration_pct >= 0.5 AS is_gap_vs_all_clients
FROM peer
