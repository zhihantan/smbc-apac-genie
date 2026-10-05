-- fact_client_cashflow_daily: daily client cash flows by payment purpose from silver.pay_payment_message (inbound =
-- inflow, outbound = outflow; the same source as silver.cf_actual_monthly) + one Closing Balance row per client and
-- date from the deposits (silver.core_deposit_balance_daily from Dec-2025; month-ends of
-- silver.core_deposit_balance_monthly from Apr-2024 to Nov-2025) + one Month Total row per client and month (dense from
-- the client's first cash-flow month to the as-of month) with the month's inflows / outflows and the client's average
-- monthly inflow (the seasonality baseline), with the as-was golden_client_sk (D11).
WITH flows AS (
  SELECT golden_client_id, payment_date AS d, payment_purpose AS cat, 'Flow' AS record_type,
         max(client_group_id) AS client_group_id,
         sum(CASE WHEN direction = 'Inbound' THEN amount_usd ELSE 0.0 END)  AS inflow_usd,
         sum(CASE WHEN direction = 'Outbound' THEN amount_usd ELSE 0.0 END) AS outflow_usd,
         count_if(direction = 'Inbound')  AS n_in,
         count_if(direction = 'Outbound') AS n_out,
         CAST(NULL AS DOUBLE) AS bal, CAST(NULL AS DOUBLE) AS casa, CAST(NULL AS DOUBLE) AS td, false AS is_daily,
         CAST(NULL AS DOUBLE) AS month_in, CAST(NULL AS DOUBLE) AS month_out, CAST(NULL AS DOUBLE) AS baseline
  FROM ${catalog}.silver.pay_payment_message
  GROUP BY golden_client_id, payment_date, payment_purpose
), bal_daily AS (
  SELECT golden_client_id, balance_date AS d, 'Closing Balance' AS cat, 'Balance' AS record_type,
         max(client_group_id) AS client_group_id, 0.0 AS inflow_usd, 0.0 AS outflow_usd, 0 AS n_in, 0 AS n_out,
         sum(balance_usd) AS bal,
         sum(CASE WHEN deposit_class = 'CASA' THEN balance_usd ELSE 0.0 END) AS casa,
         sum(CASE WHEN deposit_class = 'CASA' THEN 0.0 ELSE balance_usd END) AS td, true AS is_daily,
         CAST(NULL AS DOUBLE) AS month_in, CAST(NULL AS DOUBLE) AS month_out, CAST(NULL AS DOUBLE) AS baseline
  FROM ${catalog}.silver.core_deposit_balance_daily
  GROUP BY golden_client_id, balance_date
), bal_me AS (
  SELECT golden_client_id, balance_date AS d, 'Closing Balance' AS cat, 'Balance' AS record_type,
         max(client_group_id) AS client_group_id, 0.0 AS inflow_usd, 0.0 AS outflow_usd, 0 AS n_in, 0 AS n_out,
         sum(balance_usd) AS bal,
         sum(CASE WHEN deposit_class = 'CASA' THEN balance_usd ELSE 0.0 END) AS casa,
         sum(CASE WHEN deposit_class = 'CASA' THEN 0.0 ELSE balance_usd END) AS td, false AS is_daily,
         CAST(NULL AS DOUBLE) AS month_in, CAST(NULL AS DOUBLE) AS month_out, CAST(NULL AS DOUBLE) AS baseline
  FROM ${catalog}.silver.core_deposit_balance_monthly
  WHERE balance_date BETWEEN DATE'2024-04-30' AND DATE'2025-11-30'
  GROUP BY golden_client_id, balance_date
), mflows AS (   -- client x month totals of the cash flows
  SELECT golden_client_id, trunc(payment_date, 'MM') AS m, max(client_group_id) AS client_group_id,
         sum(CASE WHEN direction = 'Inbound' THEN amount_usd ELSE 0.0 END)  AS i,
         sum(CASE WHEN direction = 'Outbound' THEN amount_usd ELSE 0.0 END) AS o
  FROM ${catalog}.silver.pay_payment_message
  GROUP BY golden_client_id, trunc(payment_date, 'MM')
), firsts AS (
  SELECT golden_client_id, min(m) AS m0, max(client_group_id) AS client_group_id FROM mflows GROUP BY golden_client_id
), months AS (
  SELECT DISTINCT month_start_date AS m FROM ${catalog}.gold.dim_date
  WHERE month_start_date <= trunc(DATE'${as_of_date}', 'MM')
), mtot AS (     -- dense months: a month without flows has 0 inflows / outflows
  SELECT f.golden_client_id, last_day(mo.m) AS d, 'Month Total' AS cat, 'Month Total' AS record_type,
         f.client_group_id, 0.0 AS inflow_usd, 0.0 AS outflow_usd, 0 AS n_in, 0 AS n_out,
         CAST(NULL AS DOUBLE) AS bal, CAST(NULL AS DOUBLE) AS casa, CAST(NULL AS DOUBLE) AS td, false AS is_daily,
         coalesce(mf.i, 0.0) AS month_in, coalesce(mf.o, 0.0) AS month_out,
         avg(coalesce(mf.i, 0.0)) OVER (PARTITION BY f.golden_client_id) AS baseline
  FROM firsts f
  JOIN months mo ON mo.m >= f.m0
  LEFT JOIN mflows mf ON mf.golden_client_id = f.golden_client_id AND mf.m = mo.m
), u AS (
  SELECT * FROM flows UNION ALL SELECT * FROM bal_daily UNION ALL SELECT * FROM bal_me UNION ALL SELECT * FROM mtot
)
SELECT
  u.golden_client_id,
  u.d                                                                  AS cashflow_date,
  u.cat                                                                AS cashflow_category,
  u.record_type,
  CASE WHEN u.record_type = 'Balance' THEN 'Balance'
       WHEN u.record_type = 'Month Total' THEN 'Month Total'
       WHEN u.cat IN ('Supplier', 'Payroll', 'Tax', 'Trade Settlement') THEN 'Operating'
       WHEN u.cat IN ('Loan Service', 'Dividend') THEN 'Financing'
       ELSE 'Intercompany' END                                         AS cashflow_category_group,
  trunc(u.d, 'MM')                                                     AS month,
  month(u.d)                                                           AS calendar_month,
  dc.golden_client_sk,
  coalesce(u.client_group_id, dc.client_group_id)                      AS client_group_id,
  dc.coverage_office                                                   AS booking_country,
  u.inflow_usd,
  u.outflow_usd,
  u.inflow_usd - u.outflow_usd                                         AS net_cashflow_usd,
  CAST(u.n_in AS INT)                                                  AS inflow_count,
  CAST(u.n_out AS INT)                                                 AS outflow_count,
  u.bal                                                                AS closing_balance_usd,
  u.casa                                                               AS closing_casa_balance_usd,
  u.td                                                                 AS closing_td_balance_usd,
  u.is_daily                                                           AS is_daily_balance,
  u.d = last_day(u.d)                                                  AS is_month_end,
  u.month_in                                                           AS month_inflow_usd,
  u.month_out                                                          AS month_outflow_usd,
  u.month_in - u.month_out                                             AS month_net_usd,
  u.baseline                                                           AS inflow_baseline_usd
FROM u
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = u.golden_client_id
  AND u.d <= dc.valid_to
  AND (u.d >= dc.valid_from OR dc.version_no = 1)
