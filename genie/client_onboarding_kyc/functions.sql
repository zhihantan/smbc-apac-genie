-- APAC Genie - Client Onboarding & KYC: UC SQL table functions (trusted assets, genie/README.md).
-- Case- and review-level drill-downs that the metric views cannot give (they have no case / review id):
-- the open onboarding cases at 30-Sep-2026 and the KYC reviews overdue at 30-Sep-2026, per booking country.
-- Flags are the same gold columns the metric views read (fact_onboarding_case.is_over_sla = Cases Over SLA,
-- fact_kyc_review.is_overdue = Overdue at the Sep-2026 month-end), so the counts reconcile with the views.
-- test: SELECT * FROM ${catalog}.gold.fn_onboarding_open_cases('ALL')
-- test: SELECT * FROM ${catalog}.gold.fn_onboarding_open_cases('SG')
-- test: SELECT * FROM ${catalog}.gold.fn_kyc_overdue_reviews('ALL')

CREATE OR REPLACE FUNCTION ${catalog}.gold.fn_onboarding_open_cases(
  booking_country STRING COMMENT 'Booking country ISO-2 code, e.g. SG, HK, TH, ID; ALL (or an empty string) returns every booking country'
)
RETURNS TABLE (
  case_id STRING COMMENT 'Onboarding case id (ONB-nnnnn)',
  applicant_name STRING COMMENT 'Legal name of the applicant entity as requested',
  client_group STRING COMMENT 'Existing client group the case is linked to; null = no existing group identified',
  booking_country STRING COMMENT 'Booking country of the request (ISO-2)',
  request_segment STRING COMMENT 'Segment of the requesting entity (Japanese Corporate, Non-Japanese Large Corporate, Financial Institution, Sponsor & Structured Finance, Public Sector)',
  request_date DATE COMMENT 'Date the onboarding request was submitted',
  current_stage STRING COMMENT 'Funnel stage the case is currently in: Request, KYC Docs, Screening, Risk Assessment, Credit/Product Approval or Account Open',
  days_open INT COMMENT 'Days since the request at 30-Sep-2026',
  days_in_current_stage INT COMMENT 'Days spent so far in the current stage at 30-Sep-2026',
  current_stage_sla_days INT COMMENT 'SLA days of the current stage',
  is_over_sla BOOLEAN COMMENT 'True when the current stage has run longer than its SLA (over SLA right now)',
  is_stuck_in_kyc_docs_15d BOOLEAN COMMENT 'True when the case has been in the KYC Docs stage for more than 15 days',
  blocker_reason STRING COMMENT 'Why the case is stuck, e.g. Awaiting Client Documents, Workflow Migration Backlog',
  documents_outstanding INT COMMENT 'KYC documents still outstanding on the case',
  owner STRING COMMENT 'Onboarding officer owning the case'
)
COMMENT 'Case-level list (one row per case) of the onboarding cases still open at 30-Sep-2026: case id, applicant, linked client group, booking country, segment, request date, current stage, days open, days in the current stage vs its SLA, over-SLA and stuck-in-KYC-Docs flags, blocker reason, documents outstanding and the owning onboarding officer. Use only when the user wants the individual cases (case ids, applicant names, case details or ages). For counts or breakdowns (open cases over SLA by owner, blocker reason, stage or country) use mv_onboarding_funnel with Case Status = Open and the Cases Over SLA measure instead. Pass a booking country code (SG, HK, ...) or ALL.'
RETURN
  SELECT c.case_id, c.applicant_name, g.group_name, c.booking_country, c.segment, c.request_date, c.current_stage,
         c.days_open, c.days_in_current_stage, c.current_stage_sla_days, c.is_over_sla, c.is_stuck_in_kyc_docs_15d,
         c.blocker_reason, c.documents_outstanding, e.employee_name
  FROM ${catalog}.gold.fact_onboarding_case c
  LEFT JOIN ${catalog}.gold.dim_client_group g ON g.client_group_id = c.client_group_id
  LEFT JOIN ${catalog}.gold.dim_employee e ON e.employee_id = c.owner_id
  WHERE c.is_open
    AND (UPPER(TRIM(COALESCE(fn_onboarding_open_cases.booking_country, 'ALL'))) IN ('ALL', '')
         OR c.booking_country = UPPER(TRIM(fn_onboarding_open_cases.booking_country)));

CREATE OR REPLACE FUNCTION ${catalog}.gold.fn_kyc_overdue_reviews(
  booking_country STRING COMMENT 'Booking country ISO-2 code, e.g. SG, HK, TH, ID; ALL (or an empty string) returns every booking country'
)
RETURNS TABLE (
  review_id STRING COMMENT 'KYC review id (KYR-nnnnnn-nn)',
  client STRING COMMENT 'Legal entity (client display name) under review',
  client_group STRING COMMENT 'Client group (global parent) of the client',
  booking_country STRING COMMENT 'Booking country / coverage office of the client at the due date (ISO-2)',
  review_type STRING COMMENT 'Periodic, Trigger or Onboarding',
  client_risk_rating STRING COMMENT 'Client KYC risk rating from the KYC master (High, Medium, Low) - High = high-risk review',
  due_date DATE COMMENT 'Date the review was due',
  days_overdue INT COMMENT 'Days past due at 30-Sep-2026',
  reviewer STRING COMMENT 'KYC officer assigned to the review'
)
COMMENT 'Review-level list (one row per review) of the KYC reviews overdue at 30-Sep-2026 (past due and not completed): review id, client, client group, booking country, review type (Periodic / Trigger / Onboarding), client KYC risk rating (High = high-risk), due date, days overdue and the assigned reviewer. Use only when the user wants the individual overdue reviews or clients. For counts, breakdowns or month-end trends of overdue reviews (by risk rating, country, review type or month) use mv_kyc_health instead. Pass a booking country code (SG, HK, ...) or ALL.'
RETURN
  SELECT r.review_id, v.display_name, v.group_name, r.booking_country, r.review_type, r.kyc_risk_rating,
         r.due_date, r.days_overdue, e.employee_name
  FROM ${catalog}.gold.fact_kyc_review r
  LEFT JOIN ${catalog}.gold.vw_client_360 v ON v.golden_client_id = r.golden_client_id
  LEFT JOIN ${catalog}.gold.dim_employee e ON e.employee_id = r.reviewer_id
  WHERE r.is_overdue
    AND (UPPER(TRIM(COALESCE(fn_kyc_overdue_reviews.booking_country, 'ALL'))) IN ('ALL', '')
         OR r.booking_country = UPPER(TRIM(fn_kyc_overdue_reviews.booking_country)));
