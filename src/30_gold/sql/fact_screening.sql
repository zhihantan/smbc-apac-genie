-- fact_screening (WP8d, brief 5.6): one row per sanctions / PEP / adverse-media screening alert (silver.kyc_screening):
-- onboarding screening of applicants and ongoing screening of KYC clients, with list, hit type, true-match outcome,
-- disposition and resolution hours. Ongoing alerts of KYC clients take the client's booking country and segment;
-- onboarding alerts take the case's.
WITH k AS (
  SELECT case_id, booking_country, segment, client_group_id, intake_group_ref FROM ${catalog}.silver.kyc_case
)
SELECT
  s.screening_id,
  s.screening_date,
  dc.golden_client_sk,
  s.golden_client_id,
  coalesce(s.client_group_id, k.client_group_id, k.intake_group_ref)                        AS client_group_id,
  s.subject_ref,
  s.case_id,
  s.screening_context,
  s.list_name,
  CASE WHEN s.list_name LIKE '%Sanctions%' OR s.list_name LIKE '%OFAC%' THEN 'Sanctions'
       WHEN s.list_name LIKE 'PEP%' THEN 'PEP'
       WHEN s.list_name = 'Adverse Media' THEN 'Adverse Media'
       ELSE 'Watchlist' END                                                                  AS list_category,
  s.hit_type,
  coalesce(s.is_true_match, false)                                                           AS is_true_match,
  s.disposition,
  s.resolution_hours,
  s.resolved_by,
  coalesce(k.booking_country, dc.coverage_office)                                            AS booking_country,
  coalesce(k.segment, dc.segment)                                                            AS segment
FROM ${catalog}.silver.kyc_screening s
LEFT JOIN k ON k.case_id = s.case_id
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = s.golden_client_id
  AND s.screening_date <= dc.valid_to
  AND (s.screening_date >= dc.valid_from OR dc.version_no = 1)
WHERE s.screening_date <= DATE'${as_of_date}'
