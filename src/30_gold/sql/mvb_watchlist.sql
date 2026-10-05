-- mvb_watchlist (WP8d, metric-view base for mv_watchlist, D13): record_type union of
--   'Event'                one row per watchlist register event (gold.fact_watchlist_event): type, reason, owner, action
--   'Month-End Membership' one row per client on the watchlist at each month-end Apr-2024..Sep-2026 (a client is on
--                          the list when the latest register event of one of its obligor records is not a removal),
--                          with the final EWS band, exposure, EAD and ECL at that month-end
--                          (gold.fact_ews_score_daily, gold.fact_credit_exposure_monthly).
-- Event measures (counts, overdue actions) live only on Event rows; point-in-time membership measures (clients,
-- exposure, ECL) only on Membership rows, so nothing double counts.
WITH ev AS (SELECT * FROM ${catalog}.gold.fact_watchlist_event),
me AS (
  SELECT date AS month_end_date FROM ${catalog}.gold.dim_date
  WHERE is_month_end AND date BETWEEN (SELECT last_day(min(event_date)) FROM ev) AND DATE'${as_of_date}'
),
obl_state AS (  -- latest event per obligor record at each month-end
  SELECT me.month_end_date, e.golden_client_id, e.obligor_id,
         max_by(named_struct('etype', e.event_type, 'band', e.band_to, 'cat', e.lead_category, 'reason', e.lead_reason,
                             'owner', e.owner_employee_id, 'owner_code', e.owner_rm_code, 'owner_name', e.owner_name,
                             'grp', e.client_group_id),
                struct(e.event_date, e.event_id)) AS s,
         max(CASE WHEN e.event_type = 'Added' THEN e.event_date END) AS last_added_date
  FROM me JOIN ev e ON e.event_date <= me.month_end_date
  GROUP BY me.month_end_date, e.golden_client_id, e.obligor_id
),
members AS (    -- client on the list at the month-end (any obligor record still on it)
  SELECT month_end_date, golden_client_id,
         max_by(s, struct(last_added_date, obligor_id)) AS s, max(last_added_date) AS added_date
  FROM obl_state WHERE s.etype <> 'Removed'
  GROUP BY month_end_date, golden_client_id
),
expo AS (
  SELECT golden_client_id, month_end_date, sum(drawn_usd) AS drawn_usd, sum(ead_usd) AS ead_usd, sum(ecl_usd) AS ecl_usd
  FROM ${catalog}.gold.fact_credit_exposure_monthly GROUP BY golden_client_id, month_end_date
),
band AS (
  SELECT golden_client_id, score_date, final_band, composite_score
  FROM ${catalog}.gold.fact_ews_score_daily WHERE is_month_end
)
SELECT
  'Event'                                AS record_type,
  e.event_id                             AS record_id,
  e.event_date                           AS record_date,
  last_day(e.event_date)                 AS month_end_date,
  e.golden_client_sk, e.golden_client_id, e.client_group_id, e.obligor_id,
  e.event_type, e.band_from, e.band_to,
  CAST(NULL AS STRING)                   AS watchlist_band,
  e.composite_score,
  e.lead_category, e.lead_reason, e.owner_employee_id, e.owner_rm_code, e.owner_name,
  e.action_type, e.action_due_date, e.action_status, e.is_action_overdue, e.is_action_open, e.action_days_overdue,
  e.is_on_watchlist_as_of, e.next_review_date,
  e.exposure_drawn_usd                   AS exposure_at_event_usd,
  e.ecl_usd                              AS ecl_at_event_usd,
  CAST(NULL AS DATE)                     AS added_date,
  CAST(NULL AS INT)                      AS days_on_watchlist,
  CAST(NULL AS DOUBLE)                   AS exposure_drawn_usd,
  CAST(NULL AS DOUBLE)                   AS ead_usd,
  CAST(NULL AS DOUBLE)                   AS ecl_usd,
  CAST(NULL AS BOOLEAN)                  AS is_as_of_month_end
FROM ev e
UNION ALL
SELECT
  'Month-End Membership',
  concat(m.golden_client_id, '|', CAST(m.month_end_date AS STRING)),
  m.month_end_date,
  m.month_end_date,
  dc.golden_client_sk, m.golden_client_id, m.s.grp, CAST(NULL AS STRING),
  NULL, NULL, NULL,
  coalesce(b.final_band, m.s.band),
  b.composite_score,
  m.s.cat, m.s.reason, m.s.owner, m.s.owner_code, m.s.owner_name,
  NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
  m.added_date,
  datediff(m.month_end_date, m.added_date),
  coalesce(x.drawn_usd, 0D), x.ead_usd, x.ecl_usd,
  m.month_end_date = DATE'${as_of_date}'
FROM members m
LEFT JOIN band b ON b.golden_client_id = m.golden_client_id AND b.score_date = m.month_end_date
LEFT JOIN expo x ON x.golden_client_id = m.golden_client_id AND x.month_end_date = m.month_end_date
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = m.golden_client_id
  AND m.month_end_date <= dc.valid_to
  AND (m.month_end_date >= dc.valid_from OR dc.version_no = 1)
