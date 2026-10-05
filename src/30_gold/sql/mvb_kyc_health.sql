-- mvb_kyc_health (WP8d, metric-view base for mv_kyc_health, D13): record_type union of
--   'Review Snapshot' one row per KYC review x month-end with activity (gold.fact_kyc_review_snapshot_monthly)
--   'Screening'       one row per screening alert (gold.fact_screening)
-- Client descriptors (booking country, segment, KYC risk rating) and a common month_end_date / status sit on both;
-- review flags only on Review Snapshot rows, screening fields only on Screening rows.
WITH kyc AS (
  SELECT source_id AS kyc_id, kyc_risk_rating FROM ${catalog}.silver.client_source_record WHERE source_system = 'kyc_customer'
)
SELECT
  'Review Snapshot'                      AS record_type,
  concat(s.review_id, '|', CAST(s.month_end_date AS STRING)) AS record_id,
  s.month_end_date                       AS record_date,
  s.month_end_date,
  s.golden_client_sk, s.golden_client_id, s.client_group_id, s.booking_country, s.segment,
  s.kyc_risk_rating, s.is_high_risk,
  s.status_at_month_end                  AS status,
  s.review_id, s.review_type, s.due_date, s.completed_date,
  s.is_due_in_month, s.is_completed_in_month, s.is_completed_on_time, s.is_overdue_at_month_end, s.overdue_days_at_month_end,
  CAST(NULL AS STRING)  AS screening_id, CAST(NULL AS STRING) AS screening_context, CAST(NULL AS STRING) AS list_name,
  CAST(NULL AS STRING)  AS list_category, CAST(NULL AS STRING) AS hit_type, CAST(NULL AS BOOLEAN) AS is_true_match,
  CAST(NULL AS DOUBLE)  AS resolution_hours, CAST(NULL AS BOOLEAN) AS is_screening_alert
FROM ${catalog}.gold.fact_kyc_review_snapshot_monthly s
UNION ALL
SELECT
  'Screening', c.screening_id, c.screening_date, last_day(c.screening_date),
  c.golden_client_sk, c.golden_client_id, c.client_group_id, c.booking_country, c.segment,
  k.kyc_risk_rating, k.kyc_risk_rating = 'High',
  c.disposition,
  NULL, NULL, NULL, NULL,
  NULL, NULL, NULL, NULL, NULL,
  c.screening_id, c.screening_context, c.list_name, c.list_category, c.hit_type, c.is_true_match, c.resolution_hours, true
FROM ${catalog}.gold.fact_screening c
LEFT JOIN kyc k ON k.kyc_id = c.subject_ref
