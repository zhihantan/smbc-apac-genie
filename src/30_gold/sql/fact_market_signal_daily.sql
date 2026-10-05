-- fact_market_signal_daily: listed parent x trading day (silver.ext_market_daily) with 1D / 30D price and CDS
-- changes, the 52-week-low flag, the external rating / outlook in force (primary agency first, as in
-- dim_client_group), agency actions since the previous trading day, the market-signal entry events used by the
-- unified feed (30-day cool-down), and the group's APAC credit exposure computed from silver = drawn lending
-- (core_facility_balance_monthly) + trade finance live (trade_finance_txn: txn_date <= d < maturity_date) at the
-- latest month-end on or before the trading day - the same definition as silver.group_exposure_by_er_run.
WITH m AS (
  SELECT x.*, datediff(x.trade_date, DATE'${history_start}') AS dn
  FROM ${catalog}.silver.ext_market_daily x
  WHERE x.trade_date BETWEEN DATE'${history_start}' AND DATE'${as_of_date}'
), w AS (
  SELECT m.*,
         lag(cds_spread_bps) OVER (PARTITION BY issuer_id ORDER BY trade_date)                       AS cds_prev_1d,
         lag(trade_date) OVER (PARTITION BY issuer_id ORDER BY trade_date)                           AS prev_trade_date,
         last(close_price) OVER (PARTITION BY issuer_id ORDER BY dn RANGE BETWEEN UNBOUNDED PRECEDING AND 30 PRECEDING) AS close_30d_ago,
         last(cds_spread_bps) OVER (PARTITION BY issuer_id ORDER BY dn RANGE BETWEEN UNBOUNDED PRECEDING AND 30 PRECEDING) AS cds_30d_ago,
         max(trade_date) OVER (PARTITION BY issuer_id)                                               AS last_trade_date,
         max(trade_date) OVER (PARTITION BY issuer_id, trunc(trade_date, 'MM'))                      AS last_trade_date_of_month
  FROM m
), f AS (
  SELECT w.*,
         close_price / nullif(close_30d_ago, 0) - 1                                                  AS chg30,
         cds_spread_bps - cds_30d_ago                                                                AS cds30,
         coalesce(close_price <= low_52w, false)                                                     AS is_low,
         coalesce(close_price / nullif(close_30d_ago, 0) - 1 <= -0.20, false)                        AS is_drop,
         coalesce(cds_spread_bps - cds_30d_ago >= 50, false)                                         AS is_widen
  FROM w
), ev AS (        -- entry events: condition true today and on no trading day of the previous 30 days
  SELECT f.*,
         is_low   AND coalesce(max(CAST(is_low AS INT))   OVER (PARTITION BY issuer_id ORDER BY dn RANGE BETWEEN 30 PRECEDING AND 1 PRECEDING), 0) = 0 AS low_event,
         is_drop  AND coalesce(max(CAST(is_drop AS INT))  OVER (PARTITION BY issuer_id ORDER BY dn RANGE BETWEEN 30 PRECEDING AND 1 PRECEDING), 0) = 0 AS drop_event,
         is_widen AND coalesce(max(CAST(is_widen AS INT)) OVER (PARTITION BY issuer_id ORDER BY dn RANGE BETWEEN 30 PRECEDING AND 1 PRECEDING), 0) = 0 AS widen_event
  FROM f
), agency AS (
  SELECT agency, row_number() OVER (ORDER BY count(DISTINCT issuer_id) DESC, agency) AS arank
  FROM ${catalog}.silver.ext_rating GROUP BY agency
), xr AS (        -- rating in force on the trading day: primary agency first, then its latest action
  SELECT f.issuer_id, f.trade_date,
         max_by(named_struct('rating', r.rating, 'agency', r.agency, 'outlook', r.outlook, 'd', r.action_date),
                struct(-a.arank, r.action_date, r.rating_id)) AS r
  FROM f JOIN ${catalog}.silver.ext_rating r ON r.issuer_id = f.issuer_id AND r.action_date <= f.trade_date AND r.rating IS NOT NULL
  JOIN agency a ON a.agency = r.agency
  GROUP BY f.issuer_id, f.trade_date
), acts AS (      -- agency actions (any agency) dated after the previous trading day, up to this one
  SELECT f.issuer_id, f.trade_date,
         count_if(r.action = 'Downgrade')                                    AS n_down,
         count_if(r.action = 'Upgrade')                                      AS n_up,
         count_if(r.action = 'Outlook Revised' AND r.outlook = 'Negative')   AS n_neg
  FROM f JOIN ${catalog}.silver.ext_rating r ON r.issuer_id = f.issuer_id AND r.action_date <= f.trade_date
   AND r.action_date > coalesce(f.prev_trade_date, date_sub(f.trade_date, 1))
  GROUP BY f.issuer_id, f.trade_date
), me AS (
  SELECT date AS d FROM ${catalog}.gold.dim_date WHERE is_month_end AND date <= DATE'${as_of_date}'
), lend AS (
  SELECT client_group_id, balance_date AS d, sum(drawn_usd) AS lending
  FROM ${catalog}.silver.core_facility_balance_monthly WHERE client_group_id IS NOT NULL GROUP BY 1, 2
), trd AS (
  SELECT t.client_group_id, me.d, sum(t.amount_usd) AS trade
  FROM ${catalog}.silver.trade_finance_txn t JOIN me ON t.txn_date <= me.d AND t.maturity_date > me.d
  WHERE t.client_group_id IS NOT NULL GROUP BY 1, 2
), expo AS (
  SELECT coalesce(l.client_group_id, t.client_group_id) AS client_group_id, coalesce(l.d, t.d) AS d,
         coalesce(l.lending, 0) AS lending, coalesce(t.trade, 0) AS trade
  FROM lend l FULL OUTER JOIN trd t ON t.client_group_id = l.client_group_id AND t.d = l.d
), thr AS (
  SELECT max(CASE WHEN threshold_code = 'MARKET_APAC_EXPOSURE_USD' THEN threshold_value END) AS apac_min
  FROM ${catalog}.gold.dim_threshold
)
SELECT
  e.issuer_id,
  e.trade_date,
  trunc(e.trade_date, 'MM')                                                    AS month,
  e.ticker,
  i.issuer_name,
  i.listing_venue,
  e.client_group_id,
  dl.golden_client_sk                                                          AS lead_golden_client_sk,
  e.currency,
  e.close_price,
  e.daily_return                                                               AS price_change_1d_pct,
  round(e.chg30, 6)                                                            AS price_change_30d_pct,
  e.volatility_30d,
  e.volume,
  e.market_cap_lcy,
  e.market_cap_usd,
  e.cds_spread_bps,
  round(e.cds_spread_bps - e.cds_prev_1d, 2)                                   AS cds_change_1d_bps,
  round(e.cds30, 2)                                                            AS cds_change_30d_bps,
  e.high_52w,
  e.low_52w,
  round(e.close_price / nullif(e.low_52w, 0) - 1, 6)                           AS pct_above_52w_low,
  e.is_low                                                                     AS is_52w_low,
  e.is_drop                                                                    AS is_price_drop_30d,
  e.is_widen                                                                   AS is_cds_widening_30d,
  e.low_event                                                                  AS is_52w_low_event,
  e.drop_event                                                                 AS is_price_drop_event,
  e.widen_event                                                                AS is_cds_widening_event,
  xr.r.rating                                                                  AS external_rating,
  xr.r.agency                                                                  AS external_rating_agency,
  xr.r.outlook                                                                 AS external_outlook,
  xr.r.d                                                                       AS external_rating_date,
  CAST(coalesce(ac.n_down, 0) AS INT)                                          AS rating_downgrades,
  CAST(coalesce(ac.n_up, 0) AS INT)                                            AS rating_upgrades,
  CAST(coalesce(ac.n_neg, 0) AS INT)                                           AS outlook_negative_changes,
  round(coalesce(x.lending, 0), 2)                                             AS apac_lending_drawn_usd,
  round(coalesce(x.trade, 0), 2)                                               AS apac_trade_outstanding_usd,
  round(coalesce(x.lending, 0) + coalesce(x.trade, 0), 2)                      AS apac_exposure_usd,
  CASE WHEN e.trade_date = last_day(e.trade_date) THEN e.trade_date
       ELSE last_day(add_months(e.trade_date, -1)) END                         AS apac_exposure_date,
  coalesce(x.lending, 0) + coalesce(x.trade, 0) > thr.apac_min                 AS is_apac_exposure_above_100m,
  e.trade_date = e.last_trade_date                                             AS is_latest_trade_date,
  e.trade_date = e.last_trade_date_of_month                                    AS is_last_trade_day_of_month,
  datediff(DATE'${as_of_date}', e.trade_date)                                  AS days_before_as_of
FROM ev e
CROSS JOIN thr
LEFT JOIN ${catalog}.silver.ext_issuer i ON i.issuer_id = e.issuer_id
LEFT JOIN xr ON xr.issuer_id = e.issuer_id AND xr.trade_date = e.trade_date
LEFT JOIN acts ac ON ac.issuer_id = e.issuer_id AND ac.trade_date = e.trade_date
LEFT JOIN expo x ON x.client_group_id = e.client_group_id
 AND x.d = CASE WHEN e.trade_date = last_day(e.trade_date) THEN e.trade_date ELSE last_day(add_months(e.trade_date, -1)) END
LEFT JOIN ${catalog}.gold.dim_client_group dg ON dg.client_group_id = e.client_group_id
LEFT JOIN ${catalog}.gold.dim_client dl
  ON  dl.golden_client_id = dg.lead_golden_client_id
  AND e.trade_date <= dl.valid_to
  AND (e.trade_date >= dl.valid_from OR dl.version_no = 1)
