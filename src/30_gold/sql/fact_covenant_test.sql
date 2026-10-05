-- fact_covenant_test (WP8d, brief 5.3 / 5.4): one row per facility x covenant x test date
-- (silver.credit_covenant_test) with the headroom bucket, a breach / tight-headroom flag, the previous test,
-- the facility balance at the test month-end and is_latest_test = the latest test of the facility's covenant on
-- or before the as-of date (D13 precomputed flag; computed over the full test history, then tests dated before
-- the calendar window are dropped). Headroom = (threshold - actual) / threshold for maximum
-- covenants (Net Debt/EBITDA) and (actual - threshold) / threshold for minimum covenants (ICR).
WITH t AS (
  SELECT facility_id, obligor_id, golden_client_id, client_group_id, covenant_type, test_date, test_basis,
         threshold, actual, headroom_pct, breached, waiver
  FROM ${catalog}.silver.credit_covenant_test
  WHERE test_date <= DATE'${as_of_date}'
),
seq AS (
  SELECT t.*,
         row_number() OVER (PARTITION BY facility_id, covenant_type ORDER BY test_date DESC) = 1 AS is_latest_test,
         lag(actual) OVER (PARTITION BY facility_id, covenant_type ORDER BY test_date)          AS prior_actual,
         lag(test_date) OVER (PARTITION BY facility_id, covenant_type ORDER BY test_date)       AS prior_test_date,
         lag(headroom_pct) OVER (PARTITION BY facility_id, covenant_type ORDER BY test_date)    AS prior_headroom_pct
  FROM t
),
bal AS (
  SELECT facility_id, balance_date, drawn_usd, limit_usd FROM ${catalog}.silver.core_facility_balance_monthly
),
wl_now AS (     -- clients on the credit watchlist at the as-of date (latest register event is not a removal)
  SELECT golden_client_id FROM (
    SELECT golden_client_id, obligor_id, max_by(event_type, struct(event_date, event_id)) AS last_event
    FROM ${catalog}.silver.ews_watchlist_event WHERE event_date <= DATE'${as_of_date}' AND golden_client_id IS NOT NULL
    GROUP BY golden_client_id, obligor_id)
  WHERE last_event <> 'Removed' GROUP BY golden_client_id
),
asof AS (
  SELECT fiscal_year AS as_of_fiscal_year FROM ${catalog}.gold.dim_date WHERE date = DATE'${as_of_date}'
)
SELECT
  s.facility_id,
  s.covenant_type,
  s.test_date,
  s.obligor_id,
  dc.golden_client_sk,
  s.golden_client_id,
  s.client_group_id,
  s.test_basis,
  CASE s.covenant_type WHEN 'Net Debt/EBITDA' THEN 'Leverage' WHEN 'ICR' THEN 'Interest Cover' ELSE 'Other' END AS covenant_category,
  CASE s.covenant_type WHEN 'ICR' THEN 'Minimum' ELSE 'Maximum' END                         AS covenant_direction,
  s.threshold,
  s.actual,
  s.headroom_pct,
  CASE WHEN s.breached OR s.headroom_pct < 0 THEN 'Breached'
       WHEN s.headroom_pct < ${covenant_headroom_warn} THEN '0-10%'
       WHEN s.headroom_pct < 0.20 THEN '10-20%'
       WHEN s.headroom_pct < 0.30 THEN '20-30%'
       ELSE '30%+' END                                                                       AS headroom_bucket,
  coalesce(s.breached, false)                                                                AS is_breached,
  NOT coalesce(s.breached, false) AND s.headroom_pct >= 0 AND s.headroom_pct < ${covenant_headroom_warn} AS is_tight_headroom,
  coalesce(s.waiver, false)                                                                  AS is_waived,
  s.is_latest_test,
  s.prior_test_date,
  s.prior_actual,
  s.actual - s.prior_actual                                                                  AS actual_change_vs_prior,
  s.prior_headroom_pct,
  d.fiscal_year                                                                              AS test_fiscal_year,
  d.fiscal_quarter_label                                                                     AS test_fiscal_quarter_label,
  d.fiscal_year = a.as_of_fiscal_year                                                        AS is_current_fy_test,
  ft.facility_type,
  ft.status                                                                                  AS facility_status,
  ft.status = 'Active'                                                                       AS is_facility_active,
  b.drawn_usd                                                                                AS drawn_usd_at_test,
  b.limit_usd                                                                                AS limit_usd_at_test,
  w.golden_client_id IS NOT NULL                                                             AS client_on_watchlist_as_of
FROM seq s
CROSS JOIN asof a
JOIN ${catalog}.gold.dim_date d ON d.date = s.test_date
LEFT JOIN ${catalog}.silver.credit_facility_terms ft ON ft.facility_id = s.facility_id
LEFT JOIN bal b ON b.facility_id = s.facility_id AND b.balance_date = last_day(s.test_date)
LEFT JOIN wl_now w ON w.golden_client_id = s.golden_client_id
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = s.golden_client_id
  AND s.test_date <= dc.valid_to
  AND (s.test_date >= dc.valid_from OR dc.version_no = 1)
-- tests dated before the calendar window (facilities that matured before FY2023) cannot key to dim_date
WHERE s.test_date >= DATE'${history_start}'
