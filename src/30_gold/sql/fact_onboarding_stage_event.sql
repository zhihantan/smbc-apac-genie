-- fact_onboarding_stage_event (WP8d, brief 5.6): one row per onboarding case x stage entered
-- (silver.kyc_case_stage): entered / exited dates, days in stage (to the as-of date for the current stage), SLA
-- days and SLA met / over SLA, the stage actor, plus the case attributes the cycle-time questions slice by
-- (request date and month, booking country, segment, status, days to live, KYC-migration period) and a primary-row
-- flag (stage 1 row) so case-level measures such as median days to live are computed once per case (D13).
WITH s AS (
  SELECT * FROM ${catalog}.silver.kyc_case_stage
),
k AS (
  SELECT case_id, golden_client_id, client_group_id, intake_group_ref, booking_country, segment, status, request_date,
         primary_product
  FROM ${catalog}.silver.kyc_case
),
golive AS (
  SELECT case_id, max(CASE WHEN stage_no = 6 THEN exited_date END) AS go_live_date FROM s GROUP BY case_id
)
SELECT
  s.case_id,
  CAST(s.stage_no AS INT)                                                       AS stage_no,
  s.stage_name,
  dc.golden_client_sk,
  k.golden_client_id,
  coalesce(k.client_group_id, k.intake_group_ref)                               AS client_group_id,
  s.entered_date,
  s.exited_date,
  CAST(s.days_in_stage AS INT)                                                  AS days_in_stage,
  CAST(s.sla_days AS INT)                                                       AS sla_days,
  coalesce(s.is_sla_met, s.days_in_stage <= s.sla_days)                         AS is_sla_met,
  NOT coalesce(s.is_sla_met, s.days_in_stage <= s.sla_days)                     AS is_over_sla,
  greatest(CAST(s.days_in_stage AS INT) - CAST(s.sla_days AS INT), 0)           AS days_over_sla,
  coalesce(s.is_current, s.exited_date IS NULL)                                 AS is_current_stage,
  s.exited_date IS NOT NULL                                                     AS is_stage_completed,
  s.actor_id,
  s.stage_no = 1                                                                AS is_case_primary_row,
  k.request_date,
  trunc(k.request_date, 'MM')                                                   AS request_month,
  k.booking_country,
  k.segment,
  k.primary_product,
  k.status                                                                      AS case_status,
  CASE WHEN g.go_live_date IS NOT NULL THEN datediff(g.go_live_date, k.request_date) END AS case_days_to_live,
  CASE WHEN k.request_date < DATE'2026-02-01' THEN 'Pre-migration'
       WHEN k.request_date < DATE'2026-03-01' THEN 'Migration (Feb-2026)'
       WHEN k.request_date < DATE'2026-06-01' THEN 'Backlog peak (Mar-May 2026)'
       ELSE 'Recovery (from Jun-2026)' END                                     AS kyc_migration_period,
  k.request_date >= DATE'2026-02-01'                                            AS is_after_kyc_migration
FROM s
JOIN k ON k.case_id = s.case_id
LEFT JOIN golive g ON g.case_id = s.case_id
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = k.golden_client_id
  AND s.entered_date <= dc.valid_to
  AND (s.entered_date >= dc.valid_from OR dc.version_no = 1)
