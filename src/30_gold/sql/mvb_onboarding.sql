-- mvb_onboarding (WP8d, metric-view base for mv_onboarding_funnel, D13): record_type union of
--   'Case'             one row per onboarding case (gold.fact_onboarding_case)                     [is_primary_row]
--   'Product Request'  one row per case x product requested (exploded from products_requested)
--   'Document'         one row per KYC document requested (gold.fact_kyc_document)
--   'Feedback'         one row per post-onboarding survey response (gold.fact_onboarding_feedback)
-- Case descriptors (booking country, segment, request month, status, stage, owner, blocker, match timing, SLA /
-- stuck flags) repeat on every row; case measures (live, days to live, transacted within 60 days ...) are
-- populated only on Case rows, document / feedback / product measures only on their own rows.
WITH c AS (SELECT * FROM ${catalog}.gold.fact_onboarding_case),
cd AS (         -- case descriptors shared by every row type
  SELECT case_id, golden_client_sk, golden_client_id, client_group_id, booking_country, segment, request_date,
         trunc(request_date, 'MM') AS request_month, request_fiscal_year, status AS case_status, current_stage, owner_id,
         blocker_reason, match_timing, primary_product, kyc_migration_period, is_over_sla AS case_over_sla,
         is_stuck_in_kyc_docs_15d AS case_stuck_in_kyc_docs_15d
  FROM c
)
SELECT
  'Case' AS record_type, c.case_id AS record_id, true AS is_primary_row, c.request_date AS record_date,
  d.*,
  c.products_requested, c.n_products_requested, c.is_open, c.is_live, c.is_withdrawn, c.is_rejected,
  c.matched_existing_group, c.go_live_date, c.go_live_fiscal_year, c.days_to_live, c.first_transaction_lag_days,
  c.transacted_within_60d, c.days_in_current_stage, c.documents_outstanding,
  CAST(NULL AS STRING) AS product_requested,
  CAST(NULL AS STRING) AS document_type, CAST(NULL AS STRING) AS document_status, CAST(NULL AS BOOLEAN) AS is_mandatory_document,
  CAST(NULL AS BOOLEAN) AS is_document_outstanding, CAST(NULL AS INT) AS document_days_outstanding,
  CAST(NULL AS INT) AS satisfaction_score, CAST(NULL AS STRING) AS feedback_sentiment, CAST(NULL AS BOOLEAN) AS is_negative_feedback,
  CAST(NULL AS STRING) AS feedback_theme, CAST(NULL AS STRING) AS feedback_comment
FROM c JOIN cd d ON d.case_id = c.case_id
UNION ALL
SELECT
  'Product Request', concat(p.case_id, '|', p.product), false, d.request_date,
  d.*,
  NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
  p.product,
  NULL, NULL, NULL, NULL, NULL,
  NULL, NULL, NULL, NULL, NULL
FROM (SELECT DISTINCT case_id, trim(prod) AS product FROM c LATERAL VIEW explode(split(products_requested, ';')) x AS prod) p
JOIN cd d ON d.case_id = p.case_id
UNION ALL
SELECT
  'Document', k.document_id, false, k.requested_date,
  d.*,
  NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
  NULL,
  k.document_type, k.status, k.is_mandatory, k.is_outstanding, k.days_outstanding,
  NULL, NULL, NULL, NULL, NULL
FROM ${catalog}.gold.fact_kyc_document k
JOIN cd d ON d.case_id = k.case_id
UNION ALL
SELECT
  'Feedback', f.feedback_id, false, f.survey_date,
  d.*,
  NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
  NULL,
  NULL, NULL, NULL, NULL, NULL,
  f.satisfaction_score, f.sentiment, f.is_negative, f.theme, f.comment_text
FROM ${catalog}.gold.fact_onboarding_feedback f
JOIN cd d ON d.case_id = f.case_id
