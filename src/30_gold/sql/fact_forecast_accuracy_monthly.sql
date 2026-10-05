-- fact_forecast_accuracy_monthly: the scored (Backtest) rows of gold.fact_cashflow_forecast by target month, with
-- error measures and the client version valid in the target month (D11).
SELECT
  f.golden_client_id,
  f.window_start_date                                                    AS target_month,
  f.horizon_days,
  f.horizon_label,
  f.model_version,
  f.window_start_date >= DATE'2026-06-01'                                AS is_after_v2_upgrade,
  f.forecast_date,
  dc.golden_client_sk,
  f.client_group_id,
  f.booking_country,
  f.forecast_inflow_usd,
  f.actual_inflow_usd,
  f.forecast_inflow_usd - f.actual_inflow_usd                            AS forecast_error_usd,
  abs(f.forecast_inflow_usd - f.actual_inflow_usd)                       AS abs_error_usd,
  f.abs_pct_error,
  (f.forecast_inflow_usd - f.actual_inflow_usd) / nullif(f.actual_inflow_usd, 0) AS signed_pct_error,
  f.forecast_inflow_p10_usd,
  f.forecast_inflow_p90_usd,
  f.is_within_p10_p90,
  f.abs_pct_error <= th.threshold_value                                  AS is_within_10pct
FROM ${catalog}.gold.fact_cashflow_forecast f
JOIN ${catalog}.gold.dim_threshold th ON th.threshold_code = 'MAPE_GOOD'
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = f.golden_client_id
  AND f.window_start_date <= dc.valid_to
  AND (f.window_start_date >= dc.valid_from OR dc.version_no = 1)
WHERE f.forecast_type = 'Backtest'
