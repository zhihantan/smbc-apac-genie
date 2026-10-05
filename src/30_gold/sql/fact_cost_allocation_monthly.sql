-- fact_cost_allocation_monthly (WP8c): allocated cost to serve per golden client x month x product family
-- (silver.fin_cost_allocation; source records of one golden client summed), with the line's revenue so a
-- cost / income ratio can be taken as SUM(cost) / SUM(revenue).
WITH cost AS (
  SELECT golden_client_id, month, product_family,
         sum(direct_cost_usd) AS direct_cost_usd, sum(rm_cost_usd) AS rm_cost_usd,
         sum(operations_cost_usd) AS operations_cost_usd, sum(ho_allocation_usd) AS ho_allocation_usd,
         sum(total_cost_usd) AS total_cost_usd, count(*) AS n_source_records,
         max(CAST(is_pnl_reconciled AS INT)) = 1 AS is_pnl_reconciled
  FROM ${catalog}.silver.fin_cost_allocation
  GROUP BY 1, 2, 3
),
rev AS (
  SELECT golden_client_id, month, product_family, sum(total_revenue_usd) AS total_revenue_usd
  FROM ${catalog}.silver.fin_client_revenue
  GROUP BY 1, 2, 3
)
SELECT c.month, d.month_end_date, d.fiscal_year, d.fiscal_year_label, d.fiscal_quarter_label, d.fiscal_half_label,
       dc.golden_client_sk, c.golden_client_id, dc.client_group_id, c.product_family,
       c.direct_cost_usd, c.rm_cost_usd, c.operations_cost_usd, c.ho_allocation_usd, c.total_cost_usd,
       coalesce(r.total_revenue_usd, 0) AS total_revenue_usd,
       c.n_source_records, c.is_pnl_reconciled
FROM cost c
JOIN ${catalog}.gold.dim_date d ON d.date = c.month
LEFT JOIN rev r ON r.golden_client_id = c.golden_client_id AND r.month = c.month AND r.product_family = c.product_family
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = c.golden_client_id
  AND d.month_end_date <= dc.valid_to
  AND (d.month_end_date >= dc.valid_from OR dc.version_no = 1)
