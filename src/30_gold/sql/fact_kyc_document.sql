-- fact_kyc_document (WP8d, brief 5.6, PLAN 7 +): one row per KYC document requested in an onboarding case
-- (silver.kyc_document) with status (Received, Outstanding, Waived, Cancelled), days to receive / days outstanding
-- at the as-of date, and the case context used by 'documents outstanding for cases stuck in KYC Docs > 15 days'.
WITH k AS (
  SELECT case_id, golden_client_id, client_group_id, intake_group_ref, booking_country, segment, status, current_stage,
         owner_id, request_date
  FROM ${catalog}.silver.kyc_case
),
kd AS (
  SELECT case_id, days_in_stage, exited_date FROM ${catalog}.silver.kyc_case_stage WHERE stage_name = 'KYC Docs'
)
SELECT
  d.document_id,
  d.case_id,
  d.kyc_id,
  dc.golden_client_sk,
  k.golden_client_id,
  coalesce(k.client_group_id, k.intake_group_ref)                                  AS client_group_id,
  d.document_type,
  d.is_mandatory,
  d.requested_date,
  d.received_date,
  d.status,
  d.status = 'Outstanding'                                                          AS is_outstanding,
  d.status = 'Received'                                                             AS is_received,
  CASE WHEN d.received_date IS NOT NULL THEN datediff(d.received_date, d.requested_date) END AS days_to_receive,
  CASE WHEN d.status = 'Outstanding' THEN datediff(DATE'${as_of_date}', d.requested_date) END AS days_outstanding,
  k.status                                                                          AS case_status,
  k.current_stage                                                                   AS case_current_stage,
  CAST(kd.days_in_stage AS INT)                                                     AS case_kyc_docs_days,
  k.status = 'Open' AND k.current_stage = 'KYC Docs' AND coalesce(kd.days_in_stage, 0) > 15 AS case_stuck_in_kyc_docs_15d,
  k.booking_country,
  k.segment,
  k.owner_id,
  k.request_date
FROM ${catalog}.silver.kyc_document d
JOIN k ON k.case_id = d.case_id
LEFT JOIN kd ON kd.case_id = d.case_id
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = k.golden_client_id
  AND d.requested_date <= dc.valid_to
  AND (d.requested_date >= dc.valid_from OR dc.version_no = 1)
