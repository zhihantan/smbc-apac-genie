-- fact_peer_benchmark_annual (WP8d, brief 5.3): peer benchmarks per peer group x ratio x fiscal year
-- (silver.ext_peer_benchmark) with the peer group's industry and the ratio direction, plus how many of our
-- clients sit in the bottom quartile (direction-aware, same rule as fact_financial_ratio_annual).
WITH meta (ratio_name, ratio_category, ratio_unit, higher_is_better) AS (
  VALUES ('Net Debt/EBITDA', 'Leverage', 'x', false), ('Debt/Equity', 'Leverage', 'x', false),
         ('ICR', 'Coverage', 'x', true), ('DSCR', 'Coverage', 'x', true),
         ('Current Ratio', 'Liquidity', 'x', true), ('Quick Ratio', 'Liquidity', 'x', true),
         ('Gross Margin', 'Profitability', 'fraction', true), ('EBITDA Margin', 'Profitability', 'fraction', true),
         ('Net Margin', 'Profitability', 'fraction', true), ('ROE', 'Profitability', 'fraction', true),
         ('ROA', 'Profitability', 'fraction', true), ('DSO', 'Working Capital', 'days', false),
         ('DIO', 'Working Capital', 'days', false), ('DPO', 'Working Capital', 'days', true),
         ('Cash Conversion Cycle', 'Working Capital', 'days', false), ('Free Cash Flow', 'Cash Flow', 'usd', true),
         ('Revenue YoY Growth', 'Growth', 'fraction', true)
),
b AS (
  SELECT peer_group_id, ratio_name, CAST(fiscal_year AS INT) AS fiscal_year, p25, p50, p75, n_clients
  FROM ${catalog}.silver.ext_peer_benchmark
),
clients AS (   -- our clients' values against the benchmark
  SELECT r.peer_group_id, r.ratio_name, CAST(r.fiscal_year AS INT) AS fiscal_year,
         count(r.ratio_value) AS n_client_values,
         count_if(CASE WHEN m.higher_is_better THEN r.ratio_value < b.p25 ELSE r.ratio_value > b.p75 END) AS n_bottom_quartile
  FROM ${catalog}.silver.credit_ratio r
  JOIN meta m ON m.ratio_name = r.ratio_name
  JOIN b ON b.peer_group_id = r.peer_group_id AND b.ratio_name = r.ratio_name AND b.fiscal_year = CAST(r.fiscal_year AS INT)
  GROUP BY r.peer_group_id, r.ratio_name, CAST(r.fiscal_year AS INT)
)
SELECT
  b.peer_group_id,
  pg.peer_group_name,
  pg.industry_sector,
  pg.industry_subsector,
  b.ratio_name,
  m.ratio_category,
  m.ratio_unit,
  m.higher_is_better,
  b.fiscal_year,
  dd.fiscal_year_label,
  dd.date                                                         AS fiscal_year_end_date,
  b.p25                                                           AS peer_p25,
  b.p50                                                           AS peer_median,
  b.p75                                                           AS peer_p75,
  b.p75 - b.p25                                                   AS peer_iqr,
  CAST(b.n_clients AS INT)                                        AS peer_n_clients,
  CAST(coalesce(c.n_client_values, 0) AS INT)                     AS n_client_values,
  CAST(coalesce(c.n_bottom_quartile, 0) AS INT)                   AS n_clients_worse_than_p25
FROM b
LEFT JOIN meta m ON m.ratio_name = b.ratio_name
LEFT JOIN ${catalog}.gold.dim_peer_group pg ON pg.peer_group_id = b.peer_group_id
LEFT JOIN clients c ON c.peer_group_id = b.peer_group_id AND c.ratio_name = b.ratio_name AND c.fiscal_year = b.fiscal_year
LEFT JOIN ${catalog}.gold.dim_date dd ON dd.fiscal_year = b.fiscal_year AND dd.is_fiscal_year_end   -- 31 Mar closing the JP fiscal year
