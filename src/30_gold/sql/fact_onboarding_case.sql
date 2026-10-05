-- fact_onboarding_case (WP8d, brief 5.6, D43): one row per onboarding case (silver.kyc_case) with the funnel
-- outcome, go-live (end of the Account Open stage) and first transaction (end of the First Transaction stage)
-- from silver.kyc_case_stage, SLA position, documents outstanding, the post-live survey and the entity-resolution
-- view of the group match. match_timing: 'At Intake' = linked to an existing client group when the request was
-- made; 'After Account Opening' = not linked at intake but the live client resolves (ER) into a group with other
-- clients (e.g. Meridian's Thai subsidiary); 'New Group' = no existing group identified (single-entity group, or
-- an unmatched applicant that never went live). Applicants that never went live have no golden client.
WITH k AS (
  SELECT * FROM ${catalog}.silver.kyc_case
),
st AS (
  SELECT case_id,
         max(CASE WHEN stage_no = 6 THEN exited_date END)                       AS go_live_date,
         max(CASE WHEN stage_no = 7 THEN exited_date END)                       AS first_transaction_date,
         max(CASE WHEN stage_no = 2 THEN days_in_stage END)                     AS kyc_docs_days,
         max_by(named_struct('no', stage_no, 'days', days_in_stage, 'sla', sla_days), stage_no) FILTER (WHERE is_current) AS cur,
         count_if(NOT is_sla_met)                                               AS n_stages_over_sla,
         count(*)                                                               AS n_stages_entered
  FROM ${catalog}.silver.kyc_case_stage GROUP BY case_id
),
grp AS (        -- golden clients per client group (ER view of the group)
  SELECT client_group_id, count(*) AS n_clients FROM ${catalog}.silver.client_golden_identity
  WHERE client_group_id IS NOT NULL GROUP BY client_group_id
),
fb AS (
  SELECT case_id, max_by(named_struct('score', score, 'theme', theme), survey_date) AS f
  FROM ${catalog}.silver.kyc_feedback GROUP BY case_id
),
stages AS (SELECT stage_no, stage_name, sla_days, cumulative_sla_days FROM ${catalog}.gold.dim_onboarding_stage),
golive_sla AS (SELECT cumulative_sla_days FROM stages WHERE stage_name = 'Account Open'),
th AS (SELECT threshold_value AS first_txn_days FROM ${catalog}.gold.dim_threshold WHERE threshold_code = 'FIRST_TRANSACTION_DAYS')
SELECT
  k.case_id,
  k.applicant_id,
  k.kyc_id,
  dc.golden_client_sk,
  k.golden_client_id,
  coalesce(k.client_group_id, k.intake_group_ref)                                          AS client_group_id,
  k.applicant_name,
  k.booking_country,
  k.segment,
  k.products_requested,
  CAST(size(split(k.products_requested, ';')) AS INT)                                      AS n_products_requested,
  k.primary_product,
  p.product_id                                                                             AS primary_product_id,
  k.request_date,
  rd.fiscal_year                                                                           AS request_fiscal_year,
  rd.fiscal_year_label                                                                     AS request_fiscal_year_label,
  CASE WHEN k.request_date < DATE'2026-02-01' THEN 'Pre-migration'
       WHEN k.request_date < DATE'2026-03-01' THEN 'Migration (Feb-2026)'
       WHEN k.request_date < DATE'2026-06-01' THEN 'Backlog peak (Mar-May 2026)'
       ELSE 'Recovery (from Jun-2026)' END                                                AS kyc_migration_period,
  k.request_date >= DATE'2026-02-01'                                                       AS is_after_kyc_migration,
  k.current_stage,
  CAST(cs.stage_no AS INT)                                                                 AS current_stage_no,
  k.status,
  k.status_date,
  k.status = 'Open'                                                                        AS is_open,
  k.status = 'Live'                                                                        AS is_live,
  k.status = 'Withdrawn'                                                                   AS is_withdrawn,
  k.status = 'Rejected'                                                                    AS is_rejected,
  k.blocker_reason,
  k.outcome_reason,
  CAST(k.documents_outstanding AS INT)                                                     AS documents_outstanding,
  k.owner_id,
  k.rm_code,
  rm.employee_id                                                                           AS rm_employee_id,
  k.intake_group_match,
  k.intake_group_ref,
  CASE WHEN k.intake_group_match THEN 'At Intake'
       WHEN k.golden_client_id IS NOT NULL AND coalesce(g.n_clients, 0) > 1 THEN 'After Account Opening'
       ELSE 'New Group' END                                                                AS match_timing,
  k.intake_group_match OR (k.golden_client_id IS NOT NULL AND coalesce(g.n_clients, 0) > 1) AS matched_existing_group,
  st.go_live_date,
  gd.fiscal_year                                                                           AS go_live_fiscal_year,
  CASE WHEN st.go_live_date IS NOT NULL THEN datediff(st.go_live_date, k.request_date) END AS days_to_live,
  CASE WHEN st.go_live_date IS NOT NULL THEN datediff(st.go_live_date, k.request_date) > gs.cumulative_sla_days END AS is_over_sla_to_live,
  st.first_transaction_date,
  CASE WHEN st.first_transaction_date IS NOT NULL THEN datediff(st.first_transaction_date, st.go_live_date) END AS first_transaction_lag_days,
  CASE WHEN st.go_live_date IS NOT NULL
       THEN coalesce(datediff(st.first_transaction_date, st.go_live_date) <= th.first_txn_days, false) END AS transacted_within_60d,
  CASE WHEN k.status = 'Open' THEN datediff(DATE'${as_of_date}', k.request_date) END       AS days_open,
  CASE WHEN k.status = 'Open' THEN CAST(st.cur.days AS INT) END                            AS days_in_current_stage,
  CASE WHEN k.status = 'Open' THEN CAST(st.cur.sla AS INT) END                             AS current_stage_sla_days,
  k.status = 'Open' AND coalesce(st.cur.days > st.cur.sla, false)                          AS is_over_sla,
  CAST(st.kyc_docs_days AS INT)                                                            AS kyc_docs_days,
  k.status = 'Open' AND k.current_stage = 'KYC Docs' AND coalesce(st.cur.days, 0) > 15     AS is_stuck_in_kyc_docs_15d,
  CAST(st.n_stages_over_sla AS INT)                                                        AS n_stages_over_sla,
  CAST(st.n_stages_entered AS INT)                                                         AS n_stages_entered,
  CAST(fb.f.score AS INT)                                                                  AS satisfaction_score,
  fb.f.theme                                                                               AS feedback_theme
FROM k
LEFT JOIN st ON st.case_id = k.case_id
LEFT JOIN grp g ON g.client_group_id = k.client_group_id
LEFT JOIN fb ON fb.case_id = k.case_id
LEFT JOIN stages cs ON cs.stage_name = k.current_stage
CROSS JOIN golive_sla gs
CROSS JOIN th
LEFT JOIN ${catalog}.gold.dim_date rd ON rd.date = k.request_date
LEFT JOIN ${catalog}.gold.dim_date gd ON gd.date = st.go_live_date
LEFT JOIN ${catalog}.gold.dim_product p ON p.product_name = k.primary_product
LEFT JOIN ${catalog}.gold.dim_employee rm ON rm.rm_code = k.rm_code
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = k.golden_client_id
  AND k.request_date <= dc.valid_to
  AND (k.request_date >= dc.valid_from OR dc.version_no = 1)
