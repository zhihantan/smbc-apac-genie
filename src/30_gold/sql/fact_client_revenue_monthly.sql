-- fact_client_revenue_monthly (WP8c): the integration fact of spaces 1, 2 and 5 - golden client x month x
-- product family, Apr-2024 .. Sep-2026. Revenue (NII / fees / trading) from silver.fin_client_revenue, allocated
-- cost from silver.fin_cost_allocation (same grain), expected loss + capital from silver.fin_capital_allocation
-- on the Corporate Lending row (1:1 with the capital months), plus two lines taken from the quarterly relationship
-- P&L (silver.fin_relationship_pnl): 'Other TB & Advisory' revenue (only reported there; allocated to the
-- quarter's months by the client's monthly lending + deposit + payments revenue) and the relationship net profit
-- (finance hurdle basis; spread evenly over the quarter's capital months) that is the numerator of RoRWA / RAROC.
-- Rows that existed 12 months earlier but not now are kept with zero current amounts so prior-year sums are exact.
WITH months AS (
  SELECT month_start_date AS month, date AS month_end_date, fiscal_year, fiscal_year_label, fiscal_quarter,
         fiscal_quarter_label, fiscal_half, fiscal_half_label
  FROM ${catalog}.gold.dim_date
  WHERE is_month_end AND date <= DATE'${as_of_date}'
    AND month_start_date >= (SELECT min(month) FROM ${catalog}.silver.fin_client_revenue)
),
rev AS (
  SELECT golden_client_id, month, product_family, max(business_line) AS business_line,
         sum(nii_usd) AS nii_usd, sum(fee_usd) AS fee_usd, sum(trading_usd) AS trading_usd,
         sum(total_revenue_usd) AS total_revenue_usd, sum(activity_basis_usd) AS activity_basis_usd,
         max(CAST(is_pnl_reconciled AS INT)) = 1 AS is_pnl_reconciled
  FROM ${catalog}.silver.fin_client_revenue
  GROUP BY 1, 2, 3
),
cost AS (
  SELECT golden_client_id, month, product_family,
         sum(direct_cost_usd) AS direct_cost_usd, sum(rm_cost_usd) AS rm_cost_usd,
         sum(operations_cost_usd) AS operations_cost_usd, sum(ho_allocation_usd) AS ho_allocation_usd,
         sum(total_cost_usd) AS allocated_cost_usd
  FROM ${catalog}.silver.fin_cost_allocation
  GROUP BY 1, 2, 3
),
cap AS (          -- obligors of the same golden client are summed
  SELECT golden_client_id, month, fiscal_year, fiscal_quarter,
         sum(expected_loss_usd) AS credit_cost_usd, sum(ecl_change_usd) AS ecl_charge_usd, sum(ead_usd) AS ead_usd,
         sum(rwa_usd) AS rwa_usd, sum(economic_capital_usd) AS economic_capital_usd,
         sum(allocated_capital_usd) AS allocated_capital_usd, sum(book_equity_usd) AS book_equity_usd,
         sum(cost_of_capital_usd) AS cost_of_capital_usd
  FROM ${catalog}.silver.fin_capital_allocation
  GROUP BY 1, 2, 3, 4
),
pnl AS (          -- quarterly relationship P&L per golden client
  SELECT golden_client_id, fiscal_year, fiscal_quarter, sum(rev_other) AS rev_other, sum(net_profit) AS net_profit
  FROM ${catalog}.silver.fin_relationship_pnl
  GROUP BY 1, 2, 3
),
inscope AS (
  SELECT golden_client_id, month, sum(total_revenue_usd) AS inscope_rev
  FROM rev WHERE product_family IN ('Corporate Lending', 'Cash', 'Liquidity', 'Payments')
  GROUP BY 1, 2
),
alloc AS (        -- relationship-P&L lines spread over the quarter's capital months
  SELECT c.golden_client_id, c.month, p.rev_other, p.net_profit,
         coalesce(i.inscope_rev, 0) AS w,
         sum(coalesce(i.inscope_rev, 0)) OVER (PARTITION BY c.golden_client_id, c.fiscal_year, c.fiscal_quarter) AS w_q,
         count(*) OVER (PARTITION BY c.golden_client_id, c.fiscal_year, c.fiscal_quarter) AS n_q
  FROM cap c
  JOIN pnl p ON p.golden_client_id = c.golden_client_id AND p.fiscal_year = c.fiscal_year AND p.fiscal_quarter = c.fiscal_quarter
  LEFT JOIN inscope i ON i.golden_client_id = c.golden_client_id AND i.month = c.month
),
alloc2 AS (
  SELECT golden_client_id, month,
         rev_other * CASE WHEN w_q > 0 THEN w / w_q ELSE 1.0 / n_q END AS other_rev,
         net_profit / n_q AS relationship_net_profit_usd
  FROM alloc
),
cur AS (          -- current-period rows: product feed + allocated other TB & advisory revenue
  SELECT golden_client_id, month, product_family, business_line, 'Product feed' AS revenue_source,
         nii_usd, fee_usd, trading_usd, total_revenue_usd, activity_basis_usd, is_pnl_reconciled
  FROM rev
  UNION ALL
  SELECT golden_client_id, month, 'Other TB & Advisory', 'Transaction Banking', 'Relationship P&L allocation',
         0D, other_rev, 0D, other_rev, 0D, true
  FROM alloc2 WHERE other_rev <> 0
),
keys AS (         -- current keys plus the keys a year after each row (prior-year-only rows)
  SELECT golden_client_id, month, product_family FROM cur
  UNION
  SELECT golden_client_id, add_months(month, 12), product_family FROM cur
  WHERE add_months(month, 12) <= DATE'${as_of_date}'
),
cap_cl AS (
  SELECT c.*, a.relationship_net_profit_usd
  FROM cap c LEFT JOIN alloc2 a ON a.golden_client_id = c.golden_client_id AND a.month = c.month
),
joined AS (
  SELECT k.golden_client_id, k.month, k.product_family,
         coalesce(c.business_line, py.business_line) AS business_line,
         coalesce(c.revenue_source, 'Prior-year only') AS revenue_source,
         c.golden_client_id IS NULL AS is_prior_year_only,
         coalesce(c.nii_usd, 0) AS nii_usd, coalesce(c.fee_usd, 0) AS fee_usd, coalesce(c.trading_usd, 0) AS trading_usd,
         coalesce(c.total_revenue_usd, 0) AS total_revenue_usd, coalesce(c.activity_basis_usd, 0) AS activity_basis_usd,
         coalesce(c.is_pnl_reconciled, false) AS is_pnl_reconciled,
         coalesce(co.direct_cost_usd, 0) AS direct_cost_usd, coalesce(co.rm_cost_usd, 0) AS rm_cost_usd,
         coalesce(co.operations_cost_usd, 0) AS operations_cost_usd, coalesce(co.ho_allocation_usd, 0) AS ho_allocation_usd,
         coalesce(co.allocated_cost_usd, 0) AS allocated_cost_usd,
         cl.golden_client_id IS NOT NULL AS is_capital_row,
         coalesce(cl.credit_cost_usd, 0) AS credit_cost_usd, coalesce(cl.ecl_charge_usd, 0) AS ecl_charge_usd,
         coalesce(cl.ead_usd, 0) AS ead_usd, coalesce(cl.rwa_usd, 0) AS rwa_usd,
         coalesce(cl.economic_capital_usd, 0) AS economic_capital_usd,
         coalesce(cl.allocated_capital_usd, 0) AS allocated_capital_usd, coalesce(cl.book_equity_usd, 0) AS book_equity_usd,
         coalesce(cl.cost_of_capital_usd, 0) AS cost_of_capital_usd,
         cl.relationship_net_profit_usd,
         coalesce(py.total_revenue_usd, 0) AS total_revenue_py_usd,
         coalesce(py.total_revenue_usd, 0) - coalesce(pyc.allocated_cost_usd, 0) - coalesce(pycl.credit_cost_usd, 0)
           AS net_contribution_py_usd
  FROM keys k
  LEFT JOIN cur c ON c.golden_client_id = k.golden_client_id AND c.month = k.month AND c.product_family = k.product_family
  LEFT JOIN cost co ON co.golden_client_id = k.golden_client_id AND co.month = k.month AND co.product_family = k.product_family
  LEFT JOIN cap_cl cl ON cl.golden_client_id = k.golden_client_id AND cl.month = k.month AND k.product_family = 'Corporate Lending'
  LEFT JOIN cur py ON py.golden_client_id = k.golden_client_id AND py.month = add_months(k.month, -12)
                  AND py.product_family = k.product_family
  LEFT JOIN cost pyc ON pyc.golden_client_id = k.golden_client_id AND pyc.month = add_months(k.month, -12)
                    AND pyc.product_family = k.product_family
  LEFT JOIN cap pycl ON pycl.golden_client_id = k.golden_client_id AND pycl.month = add_months(k.month, -12)
                    AND k.product_family = 'Corporate Lending'
),
ranked AS (
  SELECT j.*, m.month_end_date, m.fiscal_year, m.fiscal_year_label, m.fiscal_quarter, m.fiscal_quarter_label,
         m.fiscal_half, m.fiscal_half_label,
         CASE WHEN j.is_prior_year_only THEN 0
              ELSE row_number() OVER (PARTITION BY j.golden_client_id, j.month
                                      ORDER BY CASE WHEN j.is_prior_year_only THEN 1 ELSE 0 END,
                                               CASE WHEN j.is_capital_row THEN 0 ELSE 1 END,
                                               j.total_revenue_usd DESC, j.product_family) END AS rn
  FROM joined j JOIN months m ON m.month = j.month
),
qret AS (         -- client x fiscal quarter returns on the finance (relationship P&L) numerator
  SELECT golden_client_id, fiscal_year, fiscal_quarter,
         try_divide(12 * sum(relationship_net_profit_usd), sum(rwa_usd)) AS rorwa_raw,
         try_divide(12 * sum(relationship_net_profit_usd), sum(economic_capital_usd)) AS raroc_raw
  FROM ranked WHERE is_capital_row
  GROUP BY 1, 2, 3
),
hold AS (
  SELECT golden_client_id, month, products_held_count, is_single_product_lending, is_deposit_only
  FROM ${catalog}.gold.fact_product_holding_monthly WHERE is_client_month_primary_row
)
SELECT
  r.month, r.month_end_date, r.fiscal_year, r.fiscal_year_label, r.fiscal_quarter, r.fiscal_quarter_label,
  r.fiscal_half, r.fiscal_half_label,
  dc.golden_client_sk, r.golden_client_id, dc.client_group_id,
  r.product_family, r.business_line, r.revenue_source,
  r.product_family IN ('Corporate Lending', 'Cash', 'Liquidity', 'Payments', 'Other TB & Advisory') AS is_relationship_pnl_line,
  r.nii_usd, r.fee_usd, r.trading_usd, r.total_revenue_usd, r.activity_basis_usd,
  r.direct_cost_usd, r.rm_cost_usd, r.operations_cost_usd, r.ho_allocation_usd, r.allocated_cost_usd,
  r.credit_cost_usd, r.ecl_charge_usd,
  r.total_revenue_usd - r.allocated_cost_usd - r.credit_cost_usd AS net_contribution_usd,
  r.ead_usd, r.rwa_usd, r.economic_capital_usd, r.allocated_capital_usd, r.book_equity_usd, r.cost_of_capital_usd,
  r.relationship_net_profit_usd,
  r.total_revenue_py_usd, r.net_contribution_py_usd,
  r.is_capital_row, r.rn = 1 AS is_primary_row, r.is_prior_year_only, r.is_pnl_reconciled,
  h.products_held_count,
  CASE WHEN h.products_held_count IS NULL OR h.products_held_count = 0 THEN '0 products'
       WHEN h.products_held_count = 1 THEN '1 product'
       WHEN h.products_held_count = 2 THEN '2 products'
       WHEN h.products_held_count <= 4 THEN '3-4 products'
       WHEN h.products_held_count <= 7 THEN '5-7 products'
       ELSE '8+ products' END AS product_count_bucket,
  coalesce(h.is_single_product_lending, false) AS is_single_product_lending,
  coalesce(h.is_deposit_only, false) AS is_deposit_only,
  array_contains(dc.source_systems_present, 'credit_obligor')
    AND NOT array_contains(dc.source_systems_present, 'core_customer')
    AND NOT array_contains(dc.source_systems_present, 'tsy_counterparty')
    AND NOT array_contains(dc.source_systems_present, 'trade_party') AS is_lending_only_relationship,
  q.rorwa_raw IS NOT NULL AS has_rwa_in_quarter,
  -- winsorise only real ratios: greatest() skips NULLs, so no RWA must stay NULL explicitly
  CASE WHEN r.rn = 1 AND q.rorwa_raw IS NOT NULL THEN least(1.0D, greatest(-0.25D, q.rorwa_raw)) END AS rorwa_fq,
  CASE WHEN r.rn = 1 AND q.raroc_raw IS NOT NULL THEN least(1.0D, greatest(-0.25D, q.raroc_raw)) END AS raroc_fq,
  q.rorwa_raw < ${rorwa_hurdle} AS below_rorwa_hurdle,
  q.raroc_raw < ${raroc_hurdle} AS below_raroc_hurdle
FROM ranked r
LEFT JOIN qret q ON q.golden_client_id = r.golden_client_id AND q.fiscal_year = r.fiscal_year AND q.fiscal_quarter = r.fiscal_quarter
LEFT JOIN hold h ON h.golden_client_id = r.golden_client_id AND h.month = r.month
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = r.golden_client_id
  AND r.month_end_date <= dc.valid_to
  AND (r.month_end_date >= dc.valid_from OR dc.version_no = 1)
