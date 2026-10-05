-- dim_peer_group: peer groups of silver.ref_industry_peer with their benchmark coverage (silver.ext_peer_benchmark).
WITH pg AS (
  SELECT peer_group_id, min(industry_sector) AS industry_sector, min(industry_subsector) AS industry_subsector,
         bool_or(coalesce(is_carbon_intensive, false)) AS is_carbon_intensive
  FROM ${catalog}.silver.ref_industry_peer WHERE peer_group_id IS NOT NULL GROUP BY peer_group_id
), bm AS (
  SELECT peer_group_id, count(DISTINCT ratio_name) AS n_ratios, min(fiscal_year) AS fy_min, max(fiscal_year) AS fy_max
  FROM ${catalog}.silver.ext_peer_benchmark GROUP BY peer_group_id
), latest AS (
  SELECT b.peer_group_id, max(b.n_clients) AS n_peers
  FROM ${catalog}.silver.ext_peer_benchmark b JOIN bm ON bm.peer_group_id = b.peer_group_id AND b.fiscal_year = bm.fy_max
  GROUP BY b.peer_group_id
)
SELECT
  pg.peer_group_id,
  concat(pg.industry_sector, ' - ', pg.industry_subsector) AS peer_group_name,
  pg.industry_sector,
  pg.industry_subsector,
  pg.is_carbon_intensive,
  CAST(coalesce(bm.n_ratios, 0) AS INT)                    AS n_benchmark_ratios,
  CAST(bm.fy_min AS INT)                                   AS first_benchmark_fiscal_year,
  CAST(bm.fy_max AS INT)                                   AS last_benchmark_fiscal_year,
  CAST(l.n_peers AS BIGINT)                                AS n_peers_latest
FROM pg LEFT JOIN bm ON bm.peer_group_id = pg.peer_group_id LEFT JOIN latest l ON l.peer_group_id = pg.peer_group_id
