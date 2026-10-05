-- fact_cashflow_forecast: (1) Backtests - silver.cf_forecast (one-month-ahead collections forecast per core customer,
-- model v1 / v2) summed to the golden client, with expected outflows = average monthly outflows of the 3 months
-- before the run (>= 2 months, silver.cf_actual_monthly - the rule of the liquidity-event engine), actuals, empirical
-- P10 / P90 (percentiles of actual / forecast per model version), undrawn RCF and opening deposits at the month-end
-- before the target month. (2) Forward - the engine's segment projection (silver.cf_projection, seasonal) for Oct-Dec
-- 2026 allocated to clients by their share of the segment's last-12-month inflows: 7D = 7/31 of October, 30D =
-- October, 90D = Oct-Dec; outflows from Jul-Sep 2026; intervals from the v2 backtests; headroom at 30-Sep-2026.
WITH fc AS (
  SELECT golden_client_id, max(client_group_id) AS client_group_id, forecast_run_date, target_month, model_version,
         sum(forecast_inflows_usd) AS f_in, sum(actual_inflows_usd) AS a_in
  FROM ${catalog}.silver.cf_forecast
  GROUP BY golden_client_id, forecast_run_date, target_month, model_version
), act AS (
  SELECT golden_client_id, month, sum(inflows_usd) AS i, sum(outflows_usd) AS o
  FROM ${catalog}.silver.cf_actual_monthly
  GROUP BY golden_client_id, month
), q AS (
  SELECT model_version, percentile(a_in / f_in, 0.1) AS q10, percentile(a_in / f_in, 0.9) AS q90
  FROM fc WHERE f_in > 0 GROUP BY model_version
), rcf AS (
  SELECT b.golden_client_id, b.balance_date, sum(greatest(b.limit_usd - b.drawn_usd, 0.0)) AS undrawn
  FROM ${catalog}.silver.core_facility_balance_monthly b
  JOIN ${catalog}.silver.credit_facility_terms t ON t.facility_id = b.facility_id
  WHERE t.facility_type = 'Revolving Credit Facility'
  GROUP BY b.golden_client_id, b.balance_date
), dep AS (
  SELECT golden_client_id, balance_date, sum(balance_usd) AS bal
  FROM ${catalog}.silver.core_deposit_balance_monthly
  GROUP BY golden_client_id, balance_date
), bt_out AS (   -- expected outflows: months run-1 .. run-3
  SELECT f.golden_client_id, f.forecast_run_date, f.model_version, avg(a.o) AS exp_out, count(a.o) AS n_m
  FROM fc f
  LEFT JOIN act a ON a.golden_client_id = f.golden_client_id
   AND a.month BETWEEN add_months(f.forecast_run_date, -3) AND add_months(f.forecast_run_date, -1)
  GROUP BY f.golden_client_id, f.forecast_run_date, f.model_version
), bt_gross AS (  -- typical gross monthly flow: months run-1 .. run-6
  SELECT f.golden_client_id, f.forecast_run_date, f.model_version, avg(a.i + a.o) AS gross
  FROM fc f
  JOIN act a ON a.golden_client_id = f.golden_client_id
   AND a.month BETWEEN add_months(f.forecast_run_date, -6) AND add_months(f.forecast_run_date, -1)
  GROUP BY f.golden_client_id, f.forecast_run_date, f.model_version
), backtest AS (
  SELECT f.golden_client_id, f.client_group_id, f.forecast_run_date AS forecast_date, 30 AS horizon_days, f.model_version,
         'Backtest' AS forecast_type, '30D' AS horizon_label, f.target_month AS window_start_date,
         last_day(f.target_month) AS window_end_date,
         concat('Collections model ', f.model_version, ' one-month-ahead forecast (cash-flow engine backtest)') AS method,
         f.f_in AS f_in, CASE WHEN o.n_m >= 2 THEN o.exp_out END AS f_out, q.q10, q.q90,
         f.a_in, a.o AS a_out, CAST(o.n_m AS INT) AS n_m,
         last_day(add_months(f.target_month, -1)) AS basis_date, g.gross, 1.0 AS win_months
  FROM fc f
  JOIN q ON q.model_version = f.model_version
  JOIN bt_out o ON o.golden_client_id = f.golden_client_id AND o.forecast_run_date = f.forecast_run_date
               AND o.model_version = f.model_version
  LEFT JOIN act a ON a.golden_client_id = f.golden_client_id AND a.month = f.target_month
  LEFT JOIN bt_gross g ON g.golden_client_id = f.golden_client_id AND g.forecast_run_date = f.forecast_run_date
                       AND g.model_version = f.model_version
), cli AS (      -- forward: clients with cash flows in the last 12 months, their segment and inflow share
  SELECT a.golden_client_id, c.segment, c.client_group_id, sum(a.i) AS i12,
         sum(sum(a.i)) OVER (PARTITION BY c.segment) AS seg_i12
  FROM act a
  JOIN ${catalog}.gold.dim_client c ON c.golden_client_id = a.golden_client_id AND c.is_current
  WHERE a.month BETWEEN add_months(trunc(DATE'${as_of_date}', 'MM'), -11) AND trunc(DATE'${as_of_date}', 'MM')
    AND c.segment IS NOT NULL
  GROUP BY a.golden_client_id, c.segment, c.client_group_id
), fw_gross AS (  -- typical gross monthly flow: the 6 months to the as-of month (Apr-Sep 2026)
  SELECT golden_client_id, avg(i + o) AS gross
  FROM act
  WHERE month BETWEEN add_months(trunc(DATE'${as_of_date}', 'MM'), -5) AND trunc(DATE'${as_of_date}', 'MM')
  GROUP BY golden_client_id
), fw_out AS (
  SELECT golden_client_id, avg(o) AS exp_out, count(o) AS n_m
  FROM act
  WHERE month BETWEEN add_months(trunc(DATE'${as_of_date}', 'MM'), -2) AND trunc(DATE'${as_of_date}', 'MM')
  GROUP BY golden_client_id
), hz (horizon_days, horizon_label, n_months) AS (
  VALUES (7, '7D', 1), (30, '30D', 1), (90, '90D', 3)
), proj AS (
  SELECT h.horizon_days, p.segment, max(p.method) AS method,
         sum(p.projected_inflows_usd) AS seg_in
  FROM hz h JOIN ${catalog}.silver.cf_projection p
    ON p.target_month BETWEEN add_months(trunc(DATE'${as_of_date}', 'MM'), 1)
                          AND add_months(trunc(DATE'${as_of_date}', 'MM'), h.n_months)
  GROUP BY h.horizon_days, p.segment
), fwd AS (
  SELECT c.golden_client_id, c.client_group_id, DATE'${as_of_date}' AS forecast_date, h.horizon_days, 'v2' AS model_version,
         'Forward' AS forecast_type, h.horizon_label,
         date_add(DATE'${as_of_date}', 1) AS window_start_date,
         CASE WHEN h.horizon_days = 7 THEN date_add(DATE'${as_of_date}', 7)
              ELSE last_day(add_months(DATE'${as_of_date}', h.n_months)) END AS window_end_date,
         concat('Cash-flow engine segment projection (', p.method, '; ai_forecast unavailable) allocated by the client share of ',
                'last-12-month segment inflows') AS method,
         p.seg_in * c.i12 / nullif(c.seg_i12, 0) * CASE WHEN h.horizon_days = 7
              THEN 7.0 / day(last_day(add_months(DATE'${as_of_date}', 1))) ELSE 1.0 END AS f_in,
         CASE WHEN o.n_m >= 2 THEN o.exp_out * h.n_months * CASE WHEN h.horizon_days = 7
              THEN 7.0 / day(last_day(add_months(DATE'${as_of_date}', 1))) ELSE 1.0 END END AS f_out,
         q.q10, q.q90, CAST(NULL AS DOUBLE) AS a_in, CAST(NULL AS DOUBLE) AS a_out, CAST(coalesce(o.n_m, 0) AS INT) AS n_m,
         last_day(DATE'${as_of_date}') AS basis_date, gr.gross,
         h.n_months * CASE WHEN h.horizon_days = 7 THEN 7.0 / day(last_day(add_months(DATE'${as_of_date}', 1))) ELSE 1.0 END AS win_months
  FROM cli c
  CROSS JOIN hz h
  JOIN proj p ON p.segment = c.segment AND p.horizon_days = h.horizon_days
  JOIN q ON q.model_version = 'v2'
  LEFT JOIN fw_out o ON o.golden_client_id = c.golden_client_id
  LEFT JOIN fw_gross gr ON gr.golden_client_id = c.golden_client_id
), u AS (
  SELECT * FROM backtest UNION ALL SELECT * FROM fwd
)
SELECT
  u.golden_client_id,
  u.forecast_date,
  u.horizon_days,
  u.model_version,
  u.forecast_type,
  u.forecast_type = 'Forward'                                            AS is_latest_forecast,
  u.horizon_label,
  u.window_start_date,
  u.window_end_date,
  u.method,
  dc.golden_client_sk,
  coalesce(u.client_group_id, dc.client_group_id)                        AS client_group_id,
  dc.coverage_office                                                     AS booking_country,
  u.f_in                                                                 AS forecast_inflow_usd,
  u.f_out                                                                AS forecast_outflow_usd,
  u.f_in - u.f_out                                                       AS forecast_net_usd,
  u.f_in * u.q10                                                         AS forecast_inflow_p10_usd,
  u.f_in * u.q90                                                         AS forecast_inflow_p90_usd,
  u.f_in * u.q10 - u.f_out                                               AS forecast_net_p10_usd,
  u.f_in * u.q90 - u.f_out                                               AS forecast_net_p90_usd,
  u.a_in                                                                 AS actual_inflow_usd,
  CASE WHEN u.forecast_type = 'Backtest' THEN coalesce(u.a_out, 0.0) END AS actual_outflow_usd,
  CASE WHEN u.forecast_type = 'Backtest' THEN u.a_in - coalesce(u.a_out, 0.0) END AS actual_net_usd,
  abs(u.f_in - u.a_in) / nullif(u.a_in, 0)                               AS abs_pct_error,
  CASE WHEN u.forecast_type = 'Backtest' THEN u.a_in BETWEEN u.f_in * u.q10 AND u.f_in * u.q90 END AS is_within_p10_p90,
  coalesce(d.bal, 0.0)                                                   AS opening_balance_usd,
  coalesce(d.bal, 0.0) + (u.f_in - u.f_out)                              AS projected_closing_balance_usd,
  u.f_in - u.f_out < 0                                                   AS is_forecast_net_negative,
  u.gross                                                                AS typical_gross_flow_usd,
  abs(u.f_in - u.f_out) / nullif(u.gross * u.win_months, 0)              AS severity_ratio,
  coalesce(u.f_in - u.f_out < 0 AND abs(u.f_in - u.f_out) >= 0.5 * u.gross * u.win_months, false) AS is_material_shortfall,
  coalesce(d.bal, 0.0) + (u.f_in - u.f_out) < 0                          AS is_projected_balance_negative,
  r.golden_client_id IS NOT NULL                                         AS has_rcf,
  coalesce(r.undrawn, 0.0)                                               AS undrawn_rcf_usd,
  u.n_m                                                                  AS outflow_basis_months
FROM u
LEFT JOIN dep d ON d.golden_client_id = u.golden_client_id AND d.balance_date = u.basis_date
LEFT JOIN rcf r ON r.golden_client_id = u.golden_client_id AND r.balance_date = u.basis_date
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = u.golden_client_id
  AND u.forecast_date <= dc.valid_to
  AND (u.forecast_date >= dc.valid_from OR dc.version_no = 1)
