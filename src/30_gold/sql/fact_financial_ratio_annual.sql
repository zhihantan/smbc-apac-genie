-- fact_financial_ratio_annual (WP8d, brief 5.3): client financial ratios per credit obligor x fiscal year x
-- ratio (silver.credit_ratio) with the peer benchmark of the year (silver.ext_peer_benchmark: P25 / median /
-- P75), the client's percentile rank within its peer group and a direction-aware worse-than-P25 flag
-- (D13 precomputed peer flags). Direction: leverage, DSO, DIO and the cash conversion cycle are better when
-- LOWER; coverage, liquidity, margins, returns, DPO, free cash flow and growth are better when HIGHER.
WITH r AS (
  SELECT obligor_id, golden_client_id, client_group_id, CAST(fiscal_year AS INT) AS fiscal_year, fiscal_year_label,
         CAST(fiscal_year_end AS DATE) AS fiscal_year_end_date, ratio_name, ratio_value, peer_group_id
  FROM ${catalog}.silver.credit_ratio
),
meta (ratio_name, ratio_category, ratio_unit, higher_is_better, ratio_order) AS (
  VALUES ('Net Debt/EBITDA', 'Leverage', 'x', false, 1), ('Debt/Equity', 'Leverage', 'x', false, 2),
         ('ICR', 'Coverage', 'x', true, 3), ('DSCR', 'Coverage', 'x', true, 4),
         ('Current Ratio', 'Liquidity', 'x', true, 5), ('Quick Ratio', 'Liquidity', 'x', true, 6),
         ('Gross Margin', 'Profitability', 'fraction', true, 7), ('EBITDA Margin', 'Profitability', 'fraction', true, 8),
         ('Net Margin', 'Profitability', 'fraction', true, 9), ('ROE', 'Profitability', 'fraction', true, 10),
         ('ROA', 'Profitability', 'fraction', true, 11), ('DSO', 'Working Capital', 'days', false, 12),
         ('DIO', 'Working Capital', 'days', false, 13), ('DPO', 'Working Capital', 'days', true, 14),
         ('Cash Conversion Cycle', 'Working Capital', 'days', false, 15), ('Free Cash Flow', 'Cash Flow', 'usd', true, 16),
         ('Revenue YoY Growth', 'Growth', 'fraction', true, 17)
),
ranked AS (    -- percentile of the value within peer group x ratio x fiscal year (non-null values only)
  SELECT r.*,
         CASE WHEN ratio_value IS NOT NULL THEN percent_rank() OVER (
           PARTITION BY peer_group_id, ratio_name, fiscal_year, ratio_value IS NULL ORDER BY ratio_value) END AS value_pct_rank,
         CASE WHEN lag(fiscal_year) OVER (PARTITION BY obligor_id, ratio_name ORDER BY fiscal_year) = fiscal_year - 1
              THEN lag(ratio_value) OVER (PARTITION BY obligor_id, ratio_name ORDER BY fiscal_year) END AS prior_year_value
  FROM r
),
spread AS (    -- spread status of the statement year behind the ratios
  SELECT obligor_id, CAST(fiscal_year AS INT) AS fiscal_year, bool_and(is_spread) AS is_spread
  FROM ${catalog}.silver.credit_financial_statement GROUP BY obligor_id, CAST(fiscal_year AS INT)
)
SELECT
  x.obligor_id,
  dc.golden_client_sk,
  x.golden_client_id,
  x.client_group_id,
  x.fiscal_year,
  x.fiscal_year_label,
  x.fiscal_year_end_date,
  x.peer_group_id,
  x.ratio_name,
  m.ratio_category,
  m.ratio_unit,
  m.higher_is_better,
  CAST(m.ratio_order AS INT)                                                       AS ratio_order,
  x.ratio_value,
  x.prior_year_value,
  x.ratio_value - x.prior_year_value                                               AS change_vs_prior_year,
  b.p25                                                                            AS peer_p25,
  b.p50                                                                            AS peer_median,
  b.p75                                                                            AS peer_p75,
  CAST(b.n_clients AS INT)                                                         AS peer_n_clients,
  x.ratio_value - b.p50                                                            AS gap_to_peer_median,
  x.value_pct_rank                                                                 AS value_percentile_rank,
  CASE WHEN m.higher_is_better THEN x.value_pct_rank ELSE 1 - x.value_pct_rank END AS peer_percentile_rank,
  CASE WHEN x.ratio_value IS NULL OR b.p25 IS NULL THEN NULL
       WHEN m.higher_is_better THEN x.ratio_value < b.p25 ELSE x.ratio_value > b.p75 END AS worse_than_p25,
  CASE WHEN x.ratio_value IS NULL OR b.p25 IS NULL THEN NULL
       WHEN m.higher_is_better THEN x.ratio_value > b.p75 ELSE x.ratio_value < b.p25 END AS better_than_p75,
  CASE WHEN x.ratio_value IS NULL OR b.p25 IS NULL THEN NULL
       WHEN (m.higher_is_better AND x.ratio_value > b.p75) OR (NOT m.higher_is_better AND x.ratio_value < b.p25) THEN 'Top quartile'
       WHEN (m.higher_is_better AND x.ratio_value >= b.p50) OR (NOT m.higher_is_better AND x.ratio_value <= b.p50) THEN 'Better than median'
       WHEN (m.higher_is_better AND x.ratio_value >= b.p25) OR (NOT m.higher_is_better AND x.ratio_value <= b.p75) THEN 'Worse than median'
       ELSE 'Bottom quartile' END                                                  AS peer_quartile_position,
  coalesce(s.is_spread, false)                                                     AS is_spread
FROM ranked x
LEFT JOIN meta m ON m.ratio_name = x.ratio_name
LEFT JOIN ${catalog}.silver.ext_peer_benchmark b
  ON b.peer_group_id = x.peer_group_id AND b.ratio_name = x.ratio_name AND CAST(b.fiscal_year AS INT) = x.fiscal_year
LEFT JOIN spread s ON s.obligor_id = x.obligor_id AND s.fiscal_year = x.fiscal_year
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = x.golden_client_id
  AND x.fiscal_year_end_date <= dc.valid_to
  AND (x.fiscal_year_end_date >= dc.valid_from OR dc.version_no = 1)
