-- fact_onboarding_feedback (WP8d, brief 5.6): one row per post-onboarding survey response (silver.kyc_feedback)
-- with the 1-5 score, its sentiment (Negative <= 2, Neutral 3, Positive >= 4), comment theme and text, and the
-- onboarding case it rates (booking country, segment, days to live, KYC-migration period).
WITH k AS (
  SELECT k.case_id, k.booking_country, k.segment, k.request_date,
         max(CASE WHEN s.stage_no = 6 THEN s.exited_date END) AS go_live_date
  FROM ${catalog}.silver.kyc_case k LEFT JOIN ${catalog}.silver.kyc_case_stage s ON s.case_id = k.case_id
  GROUP BY k.case_id, k.booking_country, k.segment, k.request_date
)
SELECT
  f.feedback_id,
  f.case_id,
  f.kyc_id,
  dc.golden_client_sk,
  f.golden_client_id,
  f.client_group_id,
  f.survey_date,
  CAST(f.score AS INT)                                                              AS satisfaction_score,
  CASE WHEN f.score <= 2 THEN 'Negative' WHEN f.score = 3 THEN 'Neutral' ELSE 'Positive' END AS sentiment,
  f.score <= 2                                                                      AS is_negative,
  f.theme,
  f.comment_text,
  k.booking_country,
  k.segment,
  k.request_date,
  k.go_live_date,
  datediff(k.go_live_date, k.request_date)                                          AS days_to_live,
  datediff(f.survey_date, k.go_live_date)                                           AS days_after_go_live,
  k.request_date >= DATE'2026-02-01'                                                AS is_after_kyc_migration
FROM ${catalog}.silver.kyc_feedback f
LEFT JOIN k ON k.case_id = f.case_id
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = f.golden_client_id
  AND f.survey_date <= dc.valid_to
  AND (f.survey_date >= dc.valid_from OR dc.version_no = 1)
