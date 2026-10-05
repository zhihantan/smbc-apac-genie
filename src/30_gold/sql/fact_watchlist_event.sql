-- fact_watchlist_event (WP8d, brief 5.4): credit watchlist register events (silver.ews_watchlist_event):
-- Added / Escalated / De-escalated / Removed with the band move, lead reason, owner RM, the review action plan
-- (type, due, completed, status as of the as-of date) and the client's exposure and ECL at the event month-end.
WITH e AS (
  SELECT * FROM ${catalog}.silver.ews_watchlist_event WHERE event_date <= DATE'${as_of_date}'
),
expo AS (
  SELECT golden_client_id, balance_date, sum(drawn_usd) AS drawn_usd
  FROM ${catalog}.silver.core_facility_balance_monthly WHERE golden_client_id IS NOT NULL GROUP BY golden_client_id, balance_date
),
cap AS (
  SELECT golden_client_id, month, sum(ecl_usd) AS ecl_usd, sum(ead_usd) AS ead_usd
  FROM ${catalog}.silver.fin_capital_allocation WHERE golden_client_id IS NOT NULL GROUP BY golden_client_id, month
),
review AS (     -- next watchlist review date of names on the list today
  SELECT golden_client_id, min(review_date) AS next_review_date FROM ${catalog}.silver.ews_watchlist GROUP BY golden_client_id
),
seq AS (
  SELECT e.*,
         row_number() OVER (PARTITION BY obligor_id ORDER BY event_date DESC, event_id DESC) = 1 AS is_latest_event
  FROM e
)
SELECT
  s.event_id,
  s.obligor_id,
  dc.golden_client_sk,
  s.golden_client_id,
  s.client_group_id,
  s.event_date,
  s.event_type,
  s.band_from,
  s.band_to,
  s.composite_score,
  s.lead_category,
  s.lead_reason,
  s.owner_rm                                                                        AS owner_rm_code,
  emp.employee_id                                                                   AS owner_employee_id,
  emp.employee_name                                                                 AS owner_name,
  s.action_type,
  s.action_due_date,
  s.action_completed_date,
  s.action_status,
  CAST(coalesce(s.days_overdue, 0) AS INT)                                          AS action_days_overdue,
  s.action_status = 'Overdue'                                                       AS is_action_overdue,
  s.action_status IN ('Open', 'Overdue')                                            AS is_action_open,
  s.is_latest_event,
  s.is_latest_event AND s.event_type <> 'Removed'                                   AS is_on_watchlist_as_of,
  CASE WHEN s.is_latest_event AND s.event_type <> 'Removed' THEN r.next_review_date END AS next_review_date,
  coalesce(x.drawn_usd, 0D)                                                         AS exposure_drawn_usd,
  c.ead_usd,
  c.ecl_usd
FROM seq s
LEFT JOIN ${catalog}.gold.dim_employee emp ON emp.rm_code = s.owner_rm
LEFT JOIN expo x ON x.golden_client_id = s.golden_client_id AND x.balance_date = last_day(s.event_date)
LEFT JOIN cap c ON c.golden_client_id = s.golden_client_id AND c.month = trunc(s.event_date, 'MM')
LEFT JOIN review r ON r.golden_client_id = s.golden_client_id
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = s.golden_client_id
  AND s.event_date <= dc.valid_to
  AND (s.event_date >= dc.valid_from OR dc.version_no = 1)
