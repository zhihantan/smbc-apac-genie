-- fact_deposit_balance_monthly: silver.core_deposit_balance_monthly (42 month-ends) + dim_account attributes + the
-- as-was golden_client_sk (D11) + previous-month / prior-year balances + the TD maturity profile of the placements
-- live at each month-end and the month's placements / maturities / CASA -> TD transfers (silver.core_time_deposit)
-- + depositor ranks (client and group) per booking country x month-end.
WITH b AS (
  SELECT m.account_id, m.balance_date, m.cust_no, m.golden_client_id, m.client_group_id,
         upper(m.currency) AS currency, m.balance_lcy, m.balance_usd, m.avg_balance_usd, m.deposit_class,
         a.booking_country, a.account_type, a.product_id
  FROM ${catalog}.silver.core_deposit_balance_monthly m
  LEFT JOIN ${catalog}.gold.dim_account a ON a.account_id = m.account_id
), first_me AS (
  SELECT min(balance_date) AS d0 FROM b
), win AS (     -- TD maturity window in days (dim_threshold TD_MATURITY_WINDOW_DAYS, D29)
  SELECT CAST(threshold_value AS INT) AS days FROM ${catalog}.gold.dim_threshold WHERE threshold_code = 'TD_MATURITY_WINDOW_DAYS'
), live AS (   -- placements live at the month-end: placed on / before it, maturing after it
  SELECT b.account_id, b.balance_date,
         count(*)                                                                         AS n_live,
         sum(t.principal_usd)                                                             AS live_usd,
         min(t.maturity_date)                                                             AS next_maturity,
         sum(CASE WHEN t.maturity_date <= date_add(b.balance_date, w.days) THEN t.principal_usd ELSE 0 END) AS mat90_usd,
         sum(CASE WHEN t.maturity_date <= date_add(b.balance_date, w.days) THEN t.principal_lcy ELSE 0 END) AS mat90_lcy,
         sum(t.principal_usd * t.interest_rate_pct) / nullif(sum(t.principal_usd), 0)     AS wavg_rate
  FROM b CROSS JOIN win w JOIN ${catalog}.silver.core_time_deposit t
    ON t.account_id = b.account_id AND t.placement_date <= b.balance_date AND t.maturity_date > b.balance_date
  GROUP BY b.account_id, b.balance_date
), placed AS (
  SELECT account_id, last_day(placement_date) AS balance_date,
         sum(principal_usd)                                                                         AS placed_usd,
         sum(CASE WHEN funding_source = 'Transfer from Current Account' THEN principal_usd ELSE 0 END) AS from_ca_usd,
         sum(CASE WHEN funding_source = 'New Funds' THEN principal_usd ELSE 0 END)                    AS new_usd,
         sum(CASE WHEN funding_source = 'Rollover' THEN principal_usd ELSE 0 END)                     AS roll_usd
  FROM ${catalog}.silver.core_time_deposit
  GROUP BY account_id, last_day(placement_date)
), funded AS (   -- current account debited by a CASA -> TD transfer
  SELECT funding_account_id AS account_id, last_day(placement_date) AS balance_date, sum(principal_usd) AS moved_usd
  FROM ${catalog}.silver.core_time_deposit
  WHERE funding_source = 'Transfer from Current Account' AND funding_account_id IS NOT NULL
  GROUP BY funding_account_id, last_day(placement_date)
), matured AS (
  SELECT account_id, last_day(maturity_date) AS balance_date, sum(principal_usd) AS matured_usd
  FROM ${catalog}.silver.core_time_deposit
  WHERE maturity_date <= DATE'${as_of_date}'
  GROUP BY account_id, last_day(maturity_date)
), cli AS (    -- depositor rank of the client in its booking country at each month-end
  SELECT booking_country, balance_date, golden_client_id,
         row_number() OVER (PARTITION BY booking_country, balance_date
                            ORDER BY sum(balance_usd) DESC, golden_client_id) AS rnk
  FROM b GROUP BY booking_country, balance_date, golden_client_id
), grp AS (    -- ... and of its client group
  SELECT booking_country, balance_date, client_group_id,
         row_number() OVER (PARTITION BY booking_country, balance_date
                            ORDER BY sum(balance_usd) DESC, client_group_id) AS rnk
  FROM b WHERE client_group_id IS NOT NULL GROUP BY booking_country, balance_date, client_group_id
)
SELECT
  b.account_id,
  b.balance_date,
  trunc(b.balance_date, 'MM')                                                  AS month,
  dc.golden_client_sk,
  b.golden_client_id,
  coalesce(b.client_group_id, dc.client_group_id)                              AS client_group_id,
  b.cust_no,
  b.booking_country,
  b.account_type,
  b.product_id,
  b.deposit_class,
  b.deposit_class = 'CASA'                                                     AS is_casa,
  b.currency,
  b.balance_lcy,
  b.balance_usd,
  CASE WHEN b.deposit_class = 'CASA' THEN b.balance_usd ELSE 0.0 END           AS casa_balance_usd,
  CASE WHEN b.deposit_class = 'CASA' THEN 0.0 ELSE b.balance_usd END           AS td_balance_usd,
  b.avg_balance_usd,
  coalesce(pm.balance_usd, 0.0)                                                AS balance_usd_prev_month,
  b.balance_usd - coalesce(pm.balance_usd, 0.0)                                AS balance_change_mom_usd,
  CASE WHEN last_day(add_months(b.balance_date, -12)) < f.d0 THEN NULL
       ELSE coalesce(py.balance_usd, 0.0) END                                  AS balance_usd_prior_year,
  CAST(coalesce(l.n_live, 0) AS INT)                                           AS td_placements_live,
  coalesce(l.live_usd, 0.0)                                                    AS td_principal_live_usd,
  l.next_maturity                                                              AS td_next_maturity_date,
  datediff(l.next_maturity, b.balance_date)                                    AS td_days_to_next_maturity,
  CASE WHEN b.deposit_class = 'CASA' THEN NULL
       WHEN l.next_maturity IS NULL THEN 'No live TD'
       WHEN datediff(l.next_maturity, b.balance_date) <= 30 THEN '0-30 days'
       WHEN datediff(l.next_maturity, b.balance_date) <= 90 THEN '31-90 days'
       WHEN datediff(l.next_maturity, b.balance_date) <= 180 THEN '91-180 days'
       WHEN datediff(l.next_maturity, b.balance_date) <= 365 THEN '181-365 days'
       ELSE 'Over 365 days' END                                                AS td_maturity_bucket,
  coalesce(l.mat90_usd, 0.0)                                                   AS td_principal_maturing_90d_usd,
  coalesce(l.mat90_lcy, 0.0)                                                   AS td_principal_maturing_90d_lcy,
  coalesce(l.mat90_usd, 0.0) > 0                                               AS is_td_maturing_next_90d,
  l.wavg_rate                                                                  AS td_weighted_rate_pct,
  coalesce(p.placed_usd, 0.0)                                                  AS td_placed_usd,
  coalesce(p.from_ca_usd, 0.0)                                                 AS td_placed_from_current_account_usd,
  coalesce(p.new_usd, 0.0)                                                     AS td_placed_new_funds_usd,
  coalesce(p.roll_usd, 0.0)                                                    AS td_rolled_over_usd,
  coalesce(mt.matured_usd, 0.0)                                                AS td_matured_usd,
  coalesce(fd.moved_usd, 0.0)                                                  AS transferred_to_td_usd,
  CAST(cr.rnk AS INT)                                                          AS depositor_rank_in_country,
  cr.rnk <= 10                                                                 AS is_top10_depositor_in_country,
  CAST(gr.rnk AS INT)                                                          AS group_depositor_rank_in_country,
  coalesce(gr.rnk <= 10, false)                                                AS is_top10_group_depositor_in_country,
  b.balance_date = last_day(DATE'${as_of_date}')                               AS is_latest_month_end,
  dd.is_quarter_end                                                            AS is_fiscal_quarter_end,
  b.balance_date >= DATE'2025-12-31'                                           AS is_in_daily_window
FROM b
CROSS JOIN first_me f
JOIN ${catalog}.gold.dim_date dd ON dd.date = b.balance_date
LEFT JOIN b pm ON pm.account_id = b.account_id AND pm.balance_date = last_day(add_months(b.balance_date, -1))
LEFT JOIN b py ON py.account_id = b.account_id AND py.balance_date = last_day(add_months(b.balance_date, -12))
LEFT JOIN live l ON l.account_id = b.account_id AND l.balance_date = b.balance_date
LEFT JOIN placed p ON p.account_id = b.account_id AND p.balance_date = b.balance_date
LEFT JOIN funded fd ON fd.account_id = b.account_id AND fd.balance_date = b.balance_date
LEFT JOIN matured mt ON mt.account_id = b.account_id AND mt.balance_date = b.balance_date
LEFT JOIN cli cr ON cr.booking_country = b.booking_country AND cr.balance_date = b.balance_date
                AND cr.golden_client_id = b.golden_client_id
LEFT JOIN grp gr ON gr.booking_country = b.booking_country AND gr.balance_date = b.balance_date
                AND gr.client_group_id = b.client_group_id
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = b.golden_client_id
  AND b.balance_date <= dc.valid_to
  AND (b.balance_date >= dc.valid_from OR dc.version_no = 1)
