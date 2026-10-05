# APAC Genie - Cashflow Forecasting - general instructions

Generated from genie/cashflow_forecasting/space.yaml + genie/_shared.yaml (20 lines; the space's single text instruction). Do not edit.

- Fiscal year runs 1 April to 31 March. FY2026 = Apr 2026 to Mar 2027. Q1 = Apr-Jun, Q2 = Jul-Sep, Q3 = Oct-Dec, Q4 = Jan-Mar. H1 = Apr-Sep, H2 = Oct-Mar.
- "This year" / "YTD" means fiscal year to date; "latest month" means September 2026 unless the user names a month.
- Default currency is USD. Use local currency only when asked.
- "Client" or "group" without qualification means the client group (global parent); "entity" means the individual legal entity. When asked "which clients", answer at group level with the top 20 and show the coverage office.
- "APAC" means all APAC booking entities. "Country" means booking country unless the user says "risk country", "client country" or "HQ country". "Global" or "including Japan" means use the regional (JP/APAC/EMEA/AMER) dimension.
- "Japanese corporates" means Is Japanese Corporate = true. "Strategic clients" means Relationship Tier = Strategic.
- Balance-type measures (deposits, exposure, RWA, ECL, scores, headcount) are point-in-time month-end values: group by month when a question spans several months; never sum them across months. Use the Average measure when the user says "average".
- Year-on-year compares the same fiscal period in the prior fiscal year.
- Prefer measures from the metric views; use MEASURE() syntax.
- If the question is ambiguous between two metric views, ask one clarifying question rather than guessing.
- Today = 30 Sep 2026 (H1 FY2026 close); never use CURRENT_DATE() or NOW(). "Last N days" = Date BETWEEN DATE'2026-09-30' - (N-1) AND DATE'2026-09-30'. Client names have no value dictionary: match them with LIKE '%name%'.
- Views: mv_cashflow_forecast = forward forecasts run on 30-Sep-2026 (7D, 30D, 90D) and monthly backtests; mv_forecast_accuracy = MAPE, bias and P10-P90 coverage by model version; mv_liquidity_events = predicted shortfalls / surpluses and the action that followed (RCF drawdown, TD placed); mv_client_cashflow = actual daily inflows / outflows, closing balance, volatility and seasonality.
- Forecast measures return the latest run of the selection: always pick ONE `Horizon` ('7D', '30D' or '90D') and never add runs or horizons; 'next 30 days' = `Is Latest Forecast` AND `Horizon` = '30D'. Fiscal periods use the `Fiscal Year` / `Fiscal Half` labels ('FY2024' = Apr 2024-Mar 2025, 'FY2025' = Apr 2025-Mar 2026), never rebuilt from `Month`.
- 'Forecast to go negative' = `Is Material Shortfall` (forecast net outflow >= 50% of the client's typical monthly gross flow; no client's deposits actually turn negative). `Clients Forecast Negative` counts any net outflow. RCF cover = `Has RCF` with `Undrawn RCF USD`.
- Accuracy is backtested for the 30D horizon only; before vs after the model upgrade = `Model Version` 'v1' vs 'v2' (v2 from the June-2026 targets). Return MAPE %, Bias %, Interval Coverage % and CV exactly as the measures give them - fractions (0.11 = 11%), never multiplied by 100.
- Liquidity events: `Event Type` = 'Predicted Shortfall' or 'Predicted Surplus'; `Month` is the event month (June-September 2026 = `Month` BETWEEN DATE'2026-06-01' AND DATE'2026-09-01'). Unplaced large surpluses = `Is Large Surplus` AND `Is Surplus Not Placed`; RCF follow-up = `Is RCF Drawdown Within 10D`.
- mv_client_cashflow: `Closing Balance USD` is point-in-time on `Date` (the last date of the selection); seasonality = `Seasonality Index` by `Industry` and `Calendar Month` (3 = March, 9 = September); inflow volatility = `Inflow Volatility USD` and `Inflow Volatility CV %` by `Client` over 12 months (`Month` >= DATE'2025-10-01').
- Red / Amber clients today = `Current EWS Band` IN ('Red', 'Amber') in mv_client_cashflow (`EWS Band` is the band at each row's month-end). `Client Group` has a value dictionary: filter it with = on the exact group name; entity questions list `Client`; add no `IS NOT NULL` filters unless asked and LIMIT only for a top N.
- Functions (STRING arguments): one group's forecast by entity and horizon = smbc_genie.gold.fn_cashflow_forecast_by_entity('<exact group name>'); material shortfalls of clients with an RCF = fn_cashflow_shortfalls_with_rcf('30D'); one group's liquidity snapshot by entity = fn_cashflow_group_liquidity('<exact group name>').
- Answer with one summary sentence that states the period and filters used, then the table. Keep measure values unscaled and unrounded in the SQL; name output columns plainly.
