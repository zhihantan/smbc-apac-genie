-- Space 9: APAC Genie - Cashflow Forecasting (brief §5.9, PLAN §10).
-- One MEASURE() query per must-answer question; these become the Genie benchmarks (expected SQL names its
-- output columns). `-- rows:` lines are written by src/40_metrics/run_metrics.py. Written by WP8e (WP9b).
-- Forecast measures return the latest forecast run of the selection and need ONE Horizon (never add horizons or
-- runs): "next 30 days" = Is Latest Forecast AND Horizon = '30D'.

-- Q1: 30-day net cash-flow forecast for a named group (Kinokawa Precision) with P10 / P90.
-- views: mv_cashflow_forecast
-- rows: 1
SELECT `Client Group`,
       MEASURE(`Forecast Inflow USD`)  AS forecast_inflows_usd,
       MEASURE(`Forecast Outflow USD`) AS expected_outflows_usd,
       MEASURE(`Forecast Net USD`)     AS forecast_net_usd,
       MEASURE(`Forecast Net P10 USD`) AS forecast_net_p10_usd,
       MEASURE(`Forecast Net P90 USD`) AS forecast_net_p90_usd
FROM smbc_genie.metrics.mv_cashflow_forecast
WHERE `Is Latest Forecast` AND `Horizon` = '30D' AND `Client Group` = 'Kinokawa Precision'
GROUP BY ALL;

-- Q1b: The same group by entity and horizon (7 / 30 / 90 days).
-- views: mv_cashflow_forecast
-- rows: 21
SELECT `Client`, `Horizon`,
       MEASURE(`Forecast Net USD`)     AS forecast_net_usd,
       MEASURE(`Forecast Net P10 USD`) AS forecast_net_p10_usd,
       MEASURE(`Forecast Net P90 USD`) AS forecast_net_p90_usd
FROM smbc_genie.metrics.mv_cashflow_forecast
WHERE `Is Latest Forecast` AND `Client Group` = 'Kinokawa Precision'
GROUP BY ALL
ORDER BY `Client`, `Horizon`;

-- Q2: Clients forecast to go negative in the next 30 days (a material net outflow: at least 50% of their typical monthly gross flow) and their undrawn RCF.
-- views: mv_cashflow_forecast
-- rows: 25
SELECT `Client`, `Segment`,
       MEASURE(`Forecast Net USD`)     AS forecast_net_usd,
       MEASURE(`Forecast Net P10 USD`) AS forecast_net_p10_usd,
       MEASURE(`Opening Deposits USD`) AS deposits_usd,
       MEASURE(`Undrawn RCF USD`)      AS undrawn_rcf_usd
FROM smbc_genie.metrics.mv_cashflow_forecast
WHERE `Is Latest Forecast` AND `Horizon` = '30D' AND `Is Material Shortfall` AND `Has RCF`
GROUP BY ALL
ORDER BY forecast_net_usd
LIMIT 25;

-- Q2b: How many clients are forecast net negative / with a material shortfall in the next 30 days, with and without an RCF.
-- views: mv_cashflow_forecast
-- rows: 2
SELECT `Has RCF`,
       MEASURE(`Clients Forecast`)                AS clients_forecast,
       MEASURE(`Clients Forecast Negative`)       AS clients_forecast_net_negative,
       MEASURE(`Clients with Material Shortfall`) AS clients_with_material_shortfall,
       MEASURE(`Undrawn RCF USD`)                 AS undrawn_rcf_usd
FROM smbc_genie.metrics.mv_cashflow_forecast
WHERE `Is Latest Forecast` AND `Horizon` = '30D'
GROUP BY ALL
ORDER BY `Has RCF`;

-- Q3: Forecast accuracy by horizon and segment before vs after model v2.
-- views: mv_forecast_accuracy
-- rows: 10
SELECT `Horizon`, `Segment`, `Model Version`,
       MEASURE(`MAPE %`)              AS mape,
       MEASURE(`Bias %`)              AS bias,
       MEASURE(`Interval Coverage %`) AS p10_p90_coverage,
       MEASURE(`Scored Forecasts`)    AS scored_forecasts
FROM smbc_genie.metrics.mv_forecast_accuracy
GROUP BY ALL
ORDER BY `Horizon`, `Segment`, `Model Version`;

-- Q3b: The model upgrade (storyline) - MAPE of model v1 vs v2 overall.
-- views: mv_forecast_accuracy
-- rows: 2
SELECT `Model Version`,
       MEASURE(`MAPE %`)              AS mape,
       MEASURE(`Interval Coverage %`) AS p10_p90_coverage,
       MEASURE(`Scored Forecasts`)    AS scored_forecasts
FROM smbc_genie.metrics.mv_forecast_accuracy
GROUP BY ALL
ORDER BY `Model Version`;

-- Q4: Predicted surpluses above USD 20m not yet placed (no time deposit followed).
-- views: mv_liquidity_events
-- rows: 13
SELECT `Client`, `Month` AS event_month, `Event Status`,
       MEASURE(`Predicted Amount USD`) AS predicted_surplus_usd
FROM smbc_genie.metrics.mv_liquidity_events
WHERE `Is Large Surplus` AND `Is Surplus Not Placed`
GROUP BY ALL
ORDER BY event_month DESC, predicted_surplus_usd DESC;

-- Q5: Seasonality - which industries have March and September inflow peaks (seasonality index by calendar month, FY2024-FY2025)?
-- views: mv_client_cashflow
-- rows: 16
WITH s AS (
  SELECT `Industry`, `Calendar Month`, MEASURE(`Seasonality Index`) AS seasonality_index
  FROM smbc_genie.metrics.mv_client_cashflow
  WHERE `Month` BETWEEN DATE'2024-04-01' AND DATE'2026-03-01'
  GROUP BY ALL
)
SELECT `Industry`,
       max(CASE WHEN `Calendar Month` = 3 THEN seasonality_index END) AS march_index,
       max(CASE WHEN `Calendar Month` = 9 THEN seasonality_index END) AS september_index,
       max(CASE WHEN `Calendar Month` NOT IN (3, 9) THEN seasonality_index END) AS highest_other_month_index
FROM s WHERE `Industry` IS NOT NULL
GROUP BY `Industry`
ORDER BY march_index + september_index DESC;

-- Q6: Predicted shortfalls (Jun-Sep 2026) followed by an RCF drawdown within 10 days.
-- views: mv_liquidity_events
-- rows: 1
SELECT MEASURE(`Shortfall Events`)                    AS predicted_shortfalls,
       MEASURE(`Shortfalls Covered by RCF Within 10D`) AS followed_by_rcf_drawdown_within_10d,
       MEASURE(`Action Amount USD`)                   AS amount_drawn_usd,
       MEASURE(`Revenue from Actions USD`)            AS annual_revenue_from_actions_usd
FROM smbc_genie.metrics.mv_liquidity_events
WHERE `Month` BETWEEN DATE'2026-06-01' AND DATE'2026-09-01' AND `Event Type` = 'Predicted Shortfall';

-- Q6b: The 15 shortfalls covered by an RCF drawdown within 10 days - client, days to draw and amount.
-- views: mv_liquidity_events
-- rows: 15
SELECT `Client`, `Month` AS event_month,
       MEASURE(`Predicted Amount USD`)   AS predicted_shortfall_usd,
       MEASURE(`Average Days to Action`) AS days_to_drawdown,
       MEASURE(`Action Amount USD`)      AS rcf_drawn_usd
FROM smbc_genie.metrics.mv_liquidity_events
WHERE `Month` BETWEEN DATE'2026-06-01' AND DATE'2026-09-01' AND `Is RCF Drawdown Within 10D`
GROUP BY ALL
ORDER BY event_month, `Client`;

-- Q7: Inflow volatility ranking for clients in the Red / Amber EWS band today (monthly inflows Oct-2025 to Sep-2026, average at least USD 0.5m a month).
-- views: mv_client_cashflow
-- rows: 5
SELECT `Client`, `Current EWS Band`,
       MEASURE(`Average Monthly Inflow USD`) AS average_monthly_inflow_usd,
       MEASURE(`Inflow Volatility USD`)      AS inflow_stddev_usd,
       MEASURE(`Inflow Volatility CV %`)     AS inflow_coefficient_of_variation
FROM smbc_genie.metrics.mv_client_cashflow
WHERE `Month` >= DATE'2025-10-01' AND `Current EWS Band` IN ('Red', 'Amber')
GROUP BY ALL
HAVING MEASURE(`Average Monthly Inflow USD`) >= 500000
ORDER BY inflow_coefficient_of_variation DESC
LIMIT 15;

-- Q8: Forecast bias by booking country, model v1 vs v2.
-- views: mv_forecast_accuracy
-- rows: 26
SELECT `Booking Country`, `Model Version`,
       MEASURE(`Bias %`) AS forecast_bias,
       MEASURE(`MAPE %`) AS mape
FROM smbc_genie.metrics.mv_forecast_accuracy
GROUP BY ALL
ORDER BY `Booking Country`, `Model Version`;
