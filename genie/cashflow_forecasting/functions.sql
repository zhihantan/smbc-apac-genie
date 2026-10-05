-- UC SQL table functions for APAC Genie - Cashflow Forecasting (G3 / p07g3). Deployed by the Genie runner
-- (CREATE OR REPLACE). Client-group parameters match the exact group name first (e.g. 'Kinokawa Precision');
-- only when no group has that exact name is the text matched as a LIKE fragment. Forecast functions read the
-- latest forecast run (30-Sep-2026) and never add horizons; nothing uses CURRENT_DATE.

CREATE OR REPLACE FUNCTION smbc_genie.gold.fn_cashflow_forecast_by_entity(
  client_group STRING COMMENT 'Client group (global parent) name, e.g. Kinokawa Precision. Exact group name first; otherwise a name fragment matched with LIKE.'
)
RETURNS TABLE (
  `Client` STRING COMMENT 'Legal entity of the group (golden client display name).',
  `Horizon` STRING COMMENT 'Forecast horizon: 7D (1-7 Oct 2026), 30D (October 2026) or 90D (Oct-Dec 2026).',
  forecast_net_usd DOUBLE COMMENT 'Forecast net cash flow (inflows - expected outflows) over the horizon, USD (negative = net outflow).',
  forecast_net_p10_usd DOUBLE COMMENT 'Pessimistic (P10) net cash flow over the horizon, USD.',
  forecast_net_p90_usd DOUBLE COMMENT 'Optimistic (P90) net cash flow over the horizon, USD.'
)
COMMENT 'Latest cash-flow forecast (run 30-Sep-2026, model v2) for every APAC legal entity of ONE client group and every horizon (7D, 30D, 90D): forecast net cash flow with its P10 / P90 range. One row per entity and horizon - never add rows of different horizons. Use for "<group> forecast by entity and horizon", "7 / 30 / 90-day cash forecast for each <group> entity". USD.'
RETURN
  SELECT `Client`, `Horizon`,
         MEASURE(`Forecast Net USD`)     AS forecast_net_usd,
         MEASURE(`Forecast Net P10 USD`) AS forecast_net_p10_usd,
         MEASURE(`Forecast Net P90 USD`) AS forecast_net_p90_usd
  FROM smbc_genie.metrics.mv_cashflow_forecast
  WHERE `Is Latest Forecast`
    AND `Client Group` IN (
      SELECT g.group_name FROM smbc_genie.gold.dim_client_group g
      WHERE lower(g.group_name) = lower(trim(fn_cashflow_forecast_by_entity.client_group))
         OR (lower(g.group_name) LIKE concat('%', lower(trim(fn_cashflow_forecast_by_entity.client_group)), '%')
             AND NOT EXISTS (SELECT 1 FROM smbc_genie.gold.dim_client_group x
                             WHERE lower(x.group_name) = lower(trim(fn_cashflow_forecast_by_entity.client_group)))))
  GROUP BY ALL;

CREATE OR REPLACE FUNCTION smbc_genie.gold.fn_cashflow_shortfalls_with_rcf(
  horizon STRING DEFAULT '30D' COMMENT 'Forecast horizon of the latest run: 7D, 30D (default, the next 30 days = October 2026) or 90D.'
)
RETURNS TABLE (
  `Client` STRING COMMENT 'Legal entity (golden client display name).',
  `Segment` STRING COMMENT 'Client segment (e.g. Japanese Corporate, Financial Institution).',
  forecast_net_usd DOUBLE COMMENT 'Forecast net cash flow over the horizon, USD (negative = predicted net outflow).',
  forecast_net_p10_usd DOUBLE COMMENT 'Pessimistic (P10) forecast net cash flow over the horizon, USD.',
  deposits_usd DOUBLE COMMENT 'Deposits at the month-end before the window (30-Sep-2026), USD.',
  undrawn_rcf_usd DOUBLE COMMENT 'Undrawn committed revolving credit facility (RCF) headroom at 30-Sep-2026, USD.'
)
COMMENT 'Clients forecast to "go negative" over the horizon with an RCF to cover it: legal entities whose latest forecast (run 30-Sep-2026) shows a MATERIAL shortfall (net outflow of at least 50% of their typical monthly gross flow) and that have a committed revolving credit facility, with the forecast net and P10, their deposits and their undrawn RCF headroom. Use for "clients forecast to go negative in the next 30 days and their undrawn RCF", "shortfalls the RCF could cover", "RCF drawdown candidates". No client''s deposits actually turn negative; "go negative" means the material net outflow. USD; default horizon 30D.'
RETURN
  SELECT `Client`, `Segment`,
         MEASURE(`Forecast Net USD`)     AS forecast_net_usd,
         MEASURE(`Forecast Net P10 USD`) AS forecast_net_p10_usd,
         MEASURE(`Opening Deposits USD`) AS deposits_usd,
         MEASURE(`Undrawn RCF USD`)      AS undrawn_rcf_usd
  FROM smbc_genie.metrics.mv_cashflow_forecast
  WHERE `Is Latest Forecast` AND `Horizon` = fn_cashflow_shortfalls_with_rcf.horizon
    AND `Is Material Shortfall` AND `Has RCF`
  GROUP BY ALL;

CREATE OR REPLACE FUNCTION smbc_genie.gold.fn_cashflow_group_liquidity(
  client_group STRING COMMENT 'Client group (global parent) name, e.g. Kinokawa Precision. Exact group name first; otherwise a name fragment matched with LIKE.'
)
RETURNS TABLE (
  `Client` STRING COMMENT 'Legal entity of the group (golden client display name).',
  `Coverage Office` STRING COMMENT 'APAC booking location covering the entity (ISO-2).',
  closing_balance_usd DOUBLE COMMENT 'Closing deposit balance (CASA + TD) on 30-Sep-2026, USD.',
  net_cashflow_q2_fy2026_usd DOUBLE COMMENT 'Actual net cash flow (inflows - outflows) in Jul-Sep 2026 (Q2 FY2026), USD.',
  forecast_net_30d_usd DOUBLE COMMENT 'Forecast net cash flow for the next 30 days (October 2026), USD.',
  undrawn_rcf_usd DOUBLE COMMENT 'Undrawn committed RCF headroom at 30-Sep-2026, USD (0 = no RCF).',
  latest_liquidity_event STRING COMMENT 'Latest predicted liquidity need: Predicted Shortfall or Predicted Surplus (null = none).',
  latest_event_status STRING COMMENT 'Status of that event: Open, Actioned or Closed - No Action.'
)
COMMENT 'Liquidity snapshot TODAY of every APAC legal entity of ONE client group: closing deposit balance on 30-Sep-2026, actual net cash flow in Jul-Sep 2026, the 30-day forecast net cash flow, undrawn RCF headroom and the latest predicted shortfall / surplus with its status. Use for "liquidity position of <group>", "<group> cash flow actual vs forecast by entity", "does <group> have a funding need or surplus". USD.'
RETURN
  WITH grp AS (
    SELECT g.group_name FROM smbc_genie.gold.dim_client_group g
    WHERE lower(g.group_name) = lower(trim(fn_cashflow_group_liquidity.client_group))
       OR (lower(g.group_name) LIKE concat('%', lower(trim(fn_cashflow_group_liquidity.client_group)), '%')
           AND NOT EXISTS (SELECT 1 FROM smbc_genie.gold.dim_client_group x
                           WHERE lower(x.group_name) = lower(trim(fn_cashflow_group_liquidity.client_group))))
  ), bal AS (
    SELECT `Client`, MEASURE(`Closing Balance USD`) AS closing_balance_usd
    FROM smbc_genie.metrics.mv_client_cashflow
    WHERE `Date` = DATE'2026-09-30' AND `Client Group` IN (SELECT group_name FROM grp)
    GROUP BY ALL
  ), flow AS (
    SELECT `Client`, MEASURE(`Net Cashflow USD`) AS net_cashflow_usd
    FROM smbc_genie.metrics.mv_client_cashflow
    WHERE `Fiscal Quarter` = 'FY2026-Q2' AND `Client Group` IN (SELECT group_name FROM grp)
    GROUP BY ALL
  ), fc AS (
    SELECT `Client`, MEASURE(`Forecast Net USD`) AS forecast_net_usd, MEASURE(`Undrawn RCF USD`) AS undrawn_rcf_usd
    FROM smbc_genie.metrics.mv_cashflow_forecast
    WHERE `Is Latest Forecast` AND `Horizon` = '30D' AND `Client Group` IN (SELECT group_name FROM grp)
    GROUP BY ALL
  )
  SELECT c.display_name AS `Client`, c.coverage_office AS `Coverage Office`,
         b.closing_balance_usd, f.net_cashflow_usd AS net_cashflow_q2_fy2026_usd,
         fc.forecast_net_usd AS forecast_net_30d_usd, coalesce(fc.undrawn_rcf_usd, 0) AS undrawn_rcf_usd,
         c.latest_liquidity_event_type AS latest_liquidity_event, c.latest_liquidity_event_status AS latest_event_status
  FROM smbc_genie.gold.vw_client_360 c
  JOIN grp ON grp.group_name = c.group_name
  LEFT JOIN bal b ON b.`Client` = c.display_name
  LEFT JOIN flow f ON f.`Client` = c.display_name
  LEFT JOIN fc ON fc.`Client` = c.display_name;

-- test: SELECT * FROM smbc_genie.gold.fn_cashflow_forecast_by_entity('Kinokawa Precision')
-- test: SELECT * FROM smbc_genie.gold.fn_cashflow_shortfalls_with_rcf('30D')
-- test: SELECT * FROM smbc_genie.gold.fn_cashflow_group_liquidity('Kinokawa Precision')
