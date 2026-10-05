-- fact_kyc_review_snapshot_monthly (WP8d, brief 5.6, PLAN 7 +): month-end status of KYC reviews
-- (silver.kyc_review) for every month-end Apr-2023..Sep-2026 in which the review was due, was completed or was
-- overdue at the month-end. Overdue at a month-end = due before the month-end and not completed by it (the
-- storyline 7 definition). Point-in-time: count overdue per month-end, never sum overdue across months.
WITH me AS (
  SELECT date AS month_end_date FROM ${catalog}.gold.dim_date
  WHERE is_month_end AND date BETWEEN DATE'${history_start}' AND DATE'${as_of_date}'
),
kyc AS (
  SELECT source_id AS kyc_id, kyc_risk_rating FROM ${catalog}.silver.client_source_record WHERE source_system = 'kyc_customer'
),
r AS (
  SELECT review_id, kyc_id, golden_client_id, client_group_id, review_type, due_date, completed_date, risk_before, risk_after,
         last_day(least(due_date, coalesce(completed_date, due_date)))                                     AS start_me,
         CASE WHEN completed_date IS NULL OR completed_date > DATE'${as_of_date}' THEN DATE'${as_of_date}'
              ELSE last_day(greatest(completed_date, due_date)) END                                        AS end_me
  FROM ${catalog}.silver.kyc_review
),
grid AS (
  SELECT r.*, me.month_end_date,
         trunc(r.due_date, 'MM') = trunc(me.month_end_date, 'MM')                                          AS is_due_in_month,
         coalesce(trunc(r.completed_date, 'MM') = trunc(me.month_end_date, 'MM'), false)                   AS is_completed_in_month,
         r.due_date < me.month_end_date AND (r.completed_date IS NULL OR r.completed_date > me.month_end_date) AS is_overdue_at_month_end
  FROM r JOIN me ON me.month_end_date BETWEEN r.start_me AND r.end_me
)
SELECT
  g.review_id,
  g.month_end_date,
  trunc(g.month_end_date, 'MM')                                                                           AS month,
  g.kyc_id,
  dc.golden_client_sk,
  g.golden_client_id,
  g.client_group_id,
  g.review_type,
  k.kyc_risk_rating,
  k.kyc_risk_rating = 'High'                                                                              AS is_high_risk,
  coalesce(g.risk_before, g.risk_after)                                                                   AS review_risk_rating,
  g.due_date,
  g.completed_date,
  g.is_due_in_month,
  g.is_completed_in_month,
  g.is_completed_in_month AND g.completed_date <= g.due_date                                              AS is_completed_on_time,
  g.is_overdue_at_month_end,
  CASE WHEN g.is_overdue_at_month_end THEN datediff(g.month_end_date, g.due_date) ELSE 0 END             AS overdue_days_at_month_end,
  CASE WHEN g.is_completed_in_month THEN 'Completed' WHEN g.is_overdue_at_month_end THEN 'Overdue' ELSE 'Due' END AS status_at_month_end,
  dc.coverage_office                                                                                      AS booking_country,
  dc.segment
FROM grid g
LEFT JOIN kyc k ON k.kyc_id = g.kyc_id
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = g.golden_client_id
  AND g.month_end_date <= dc.valid_to
  AND (g.month_end_date >= dc.valid_from OR dc.version_no = 1)
WHERE g.is_due_in_month OR g.is_completed_in_month OR g.is_overdue_at_month_end
