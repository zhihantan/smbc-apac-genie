-- fact_deposit_balance_daily: silver.core_deposit_balance_daily (dense per account from its first day in the
-- 1-Dec-2025..30-Sep-2026 window) + dim_account attributes + the as-was golden_client_sk (D11), the 1-day / 30-day
-- prior balances (rows are dense, so lag(30) = 30 calendar days) and the day's TD placements / maturities
-- (silver.core_time_deposit). Balances are point-in-time; the TD columns are flows.
WITH b AS (
  SELECT d.account_id, d.balance_date, d.cust_no, d.golden_client_id, d.client_group_id,
         upper(d.currency) AS currency, d.balance_lcy, d.balance_usd, d.deposit_class,
         lag(d.balance_usd, 1)  OVER (PARTITION BY d.account_id ORDER BY d.balance_date) AS prev_1d,
         lag(d.balance_usd, 30) OVER (PARTITION BY d.account_id ORDER BY d.balance_date) AS prev_30d
  FROM ${catalog}.silver.core_deposit_balance_daily d
), placed AS (   -- placements credited to the TD account
  SELECT account_id, placement_date AS d,
         sum(principal_usd) AS placed_usd,
         sum(CASE WHEN funding_source = 'Transfer from Current Account' THEN principal_usd ELSE 0 END) AS from_ca_usd
  FROM ${catalog}.silver.core_time_deposit
  GROUP BY account_id, placement_date
), funded AS (   -- the current account debited by a CASA -> TD transfer
  SELECT funding_account_id AS account_id, placement_date AS d, sum(principal_usd) AS moved_usd
  FROM ${catalog}.silver.core_time_deposit
  WHERE funding_source = 'Transfer from Current Account' AND funding_account_id IS NOT NULL
  GROUP BY funding_account_id, placement_date
), matured AS (
  SELECT account_id, maturity_date AS d, sum(principal_usd) AS matured_usd
  FROM ${catalog}.silver.core_time_deposit
  WHERE maturity_date <= DATE'${as_of_date}'
  GROUP BY account_id, maturity_date
)
SELECT
  b.account_id,
  b.balance_date,
  trunc(b.balance_date, 'MM')                                              AS month,
  dc.golden_client_sk,
  b.golden_client_id,
  coalesce(b.client_group_id, dc.client_group_id)                          AS client_group_id,
  b.cust_no,
  a.booking_country,
  a.account_type,
  a.product_id,
  b.deposit_class,
  b.deposit_class = 'CASA'                                                 AS is_casa,
  b.currency,
  b.balance_lcy,
  b.balance_usd,
  CASE WHEN b.deposit_class = 'CASA' THEN b.balance_usd ELSE 0.0 END       AS casa_balance_usd,
  CASE WHEN b.deposit_class = 'CASA' THEN 0.0 ELSE b.balance_usd END       AS td_balance_usd,
  b.prev_1d                                                                AS balance_usd_prev_day,
  b.prev_30d                                                               AS balance_usd_30d_prior,
  b.balance_usd - coalesce(b.prev_1d, b.balance_usd)                       AS balance_change_1d_usd,
  coalesce(p.placed_usd, 0.0)                                              AS td_placed_usd,
  coalesce(p.from_ca_usd, 0.0)                                             AS td_placed_from_current_account_usd,
  coalesce(f.moved_usd, 0.0)                                               AS transferred_to_td_usd,
  coalesce(mt.matured_usd, 0.0)                                            AS td_matured_usd,
  dd.is_month_end,
  b.balance_date = DATE'${as_of_date}'                                     AS is_as_of_date,
  dd.is_business_day_sg
FROM b
JOIN ${catalog}.gold.dim_date dd ON dd.date = b.balance_date
LEFT JOIN ${catalog}.gold.dim_account a ON a.account_id = b.account_id
LEFT JOIN placed p ON p.account_id = b.account_id AND p.d = b.balance_date
LEFT JOIN funded f ON f.account_id = b.account_id AND f.d = b.balance_date
LEFT JOIN matured mt ON mt.account_id = b.account_id AND mt.d = b.balance_date
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = b.golden_client_id
  AND b.balance_date <= dc.valid_to
  AND (b.balance_date >= dc.valid_from OR dc.version_no = 1)
