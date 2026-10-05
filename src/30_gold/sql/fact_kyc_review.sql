-- fact_kyc_review (WP8d, brief 5.6): one row per KYC / CDD review (silver.kyc_review): onboarding, periodic and
-- trigger reviews with due / completed dates, status and overdue days at the as-of date, risk before / after and
-- the client's KYC risk rating (KYC master record, which drives the periodic review cadence). Periodic reviews
-- scheduled after 31-Mar-2027 (beyond the calendar window, status Scheduled) are excluded.
WITH r AS (
  SELECT * FROM ${catalog}.silver.kyc_review WHERE due_date <= DATE'${calendar_end}'
),
kyc AS (
  SELECT source_id AS kyc_id, kyc_risk_rating FROM ${catalog}.silver.client_source_record WHERE source_system = 'kyc_customer'
)
SELECT
  r.review_id,
  r.kyc_id,
  dc.golden_client_sk,
  r.golden_client_id,
  r.client_group_id,
  r.review_type,
  r.trigger_reason,
  r.due_date,
  d.fiscal_year                                                                       AS due_fiscal_year,
  r.completed_date,
  r.status,
  r.completed_date IS NOT NULL                                                        AS is_completed,
  r.completed_date IS NULL AND r.due_date < DATE'${as_of_date}'                      AS is_overdue,
  CASE WHEN r.completed_date IS NULL AND r.due_date < DATE'${as_of_date}'
       THEN datediff(DATE'${as_of_date}', r.due_date) ELSE 0 END                     AS days_overdue,
  CASE WHEN r.completed_date IS NOT NULL THEN greatest(datediff(r.completed_date, r.due_date), 0) END AS days_late_on_completion,
  coalesce(r.completed_date > r.due_date, false)                                      AS completed_late,
  r.risk_before,
  r.risk_after,
  coalesce(r.risk_before <> r.risk_after, false)                                      AS risk_rating_changed,
  k.kyc_risk_rating,
  k.kyc_risk_rating = 'High'                                                          AS is_high_risk,
  r.reviewer_id,
  dc.coverage_office                                                                  AS booking_country,
  dc.segment
FROM r
JOIN ${catalog}.gold.dim_date d ON d.date = r.due_date
LEFT JOIN kyc k ON k.kyc_id = r.kyc_id
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = r.golden_client_id
  AND r.due_date <= dc.valid_to
  AND (r.due_date >= dc.valid_from OR dc.version_no = 1)
