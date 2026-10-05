-- dim_employee: every staff id in the silver feeds. Role from the CRM team record (else the system it appears
-- in), name / office / RM code from the CRM team record; RM team from the CRM segment of the accounts covered.
WITH team AS (
  SELECT employee_id, nullif(rm_code, '') AS rm_code, member_name, member_office, team_role, crm_account_id,
         golden_client_id, client_group_id, coalesce(is_current, valid_to IS NULL) AS is_current
  FROM ${catalog}.silver.crm_account_team_history WHERE employee_id IS NOT NULL
), seen AS (   -- (employee, role, priority): team record first, then the workflow systems
  SELECT employee_id, CASE WHEN team_role IN ('Primary RM', 'Secondary RM') THEN 'RM' ELSE team_role END AS role,
         team_role AS role_label, 1 AS prio FROM team
  UNION ALL SELECT analyst_id, 'Credit Analyst', 'Credit Analyst', 2 FROM ${catalog}.silver.credit_review WHERE analyst_id IS NOT NULL
  UNION ALL SELECT approver_id, 'Credit Approver', 'Credit Approver', 2 FROM ${catalog}.silver.credit_review WHERE approver_id IS NOT NULL
  UNION ALL SELECT approver_id, 'Credit Approver', 'Credit Approver', 3 FROM ${catalog}.silver.core_rating_history WHERE approver_id IS NOT NULL
  UNION ALL SELECT analyst_id, 'Credit Analyst', 'Credit Analyst', 3 FROM ${catalog}.silver.credit_spreading_task WHERE analyst_id IS NOT NULL
  UNION ALL SELECT checker_id, 'Credit Analyst', 'Credit Analyst (checker)', 3 FROM ${catalog}.silver.credit_spreading_task WHERE checker_id IS NOT NULL
  UNION ALL SELECT owner_id, 'Onboarding Officer', 'Onboarding Officer', 2 FROM ${catalog}.silver.kyc_case WHERE owner_id IS NOT NULL
  UNION ALL SELECT reviewer_id, 'Onboarding Officer', 'KYC Reviewer', 3 FROM ${catalog}.silver.kyc_review WHERE reviewer_id IS NOT NULL
  UNION ALL SELECT resolved_by, 'Onboarding Officer', 'Screening Analyst', 3 FROM ${catalog}.silver.kyc_screening WHERE resolved_by LIKE 'SYN-P-%'
  UNION ALL SELECT owner_employee_id, CAST(NULL AS STRING), 'Initiative Owner', 9 FROM ${catalog}.silver.crm_account_plan_initiative
    WHERE owner_employee_id IS NOT NULL
), role_pick AS (
  SELECT employee_id, max_by(role, struct(-prio, n, role)) AS role, array_join(array_sort(collect_set(role_label)), ', ') AS roles_seen
  FROM (SELECT employee_id, role, role_label, prio, count(*) OVER (PARTITION BY employee_id, role) AS n FROM seen)
  GROUP BY employee_id
), attrs AS (
  SELECT employee_id,   -- one name / office / RM code per person in CRM; min() keeps it deterministic
         min(member_name) AS employee_name, min(member_office) AS office, min(rm_code) AS rm_code,
         count_if(is_current) AS assignments_current,
         count(DISTINCT CASE WHEN is_current AND team_role = 'Primary RM' THEN golden_client_id END) AS clients_current,
         count(DISTINCT CASE WHEN is_current AND team_role = 'Primary RM' THEN client_group_id END) AS groups_current
  FROM team GROUP BY employee_id
), rm_segment AS (   -- segment (as labelled in CRM) most of an RM's accounts belong to; ties -> alphabetical
  SELECT employee_id, max_by(segment, struct(n, segment)) AS segment
  FROM (SELECT t.employee_id, r.segment_label AS segment, count(*) AS n
        FROM team t JOIN ${catalog}.silver.client_source_record r ON r.source_system = 'crm_account' AND r.source_id = t.crm_account_id
        WHERE t.team_role IN ('Primary RM', 'Secondary RM') AND nullif(r.segment_label, '') IS NOT NULL
        GROUP BY t.employee_id, r.segment_label)
  GROUP BY employee_id
), onb_office AS (
  SELECT employee_id, max_by(office, struct(n, office)) AS office
  FROM (SELECT owner_id AS employee_id, booking_country AS office, count(*) AS n FROM ${catalog}.silver.kyc_case
        WHERE owner_id IS NOT NULL AND booking_country IS NOT NULL GROUP BY owner_id, booking_country)
  GROUP BY employee_id
)
SELECT
  p.employee_id,
  coalesce(a.employee_name, concat(coalesce(p.role, 'Staff'), ' ', p.employee_id))              AS employee_name,
  coalesce(p.role, 'Staff')                                                                      AS role,
  p.roles_seen,
  CASE p.role WHEN 'RM' THEN concat(coalesce(s.segment, 'Corporate'), ' Coverage')
              WHEN 'Credit Analyst' THEN 'Credit Risk Management'
              WHEN 'Credit Approver' THEN 'Credit Approval'
              WHEN 'TB Sales' THEN 'Transaction Banking Sales'
              WHEN 'Onboarding Officer' THEN 'Client Onboarding & KYC'
              ELSE 'Coverage Support' END                                                        AS team,
  coalesce(a.office, o.office, 'SG')                                                             AS coverage_office,
  a.rm_code,
  p.role = 'RM'                                                                                  AS is_relationship_manager,
  a.employee_name IS NOT NULL                                                                    AS has_recorded_name,
  CAST(coalesce(a.clients_current, 0) AS BIGINT)                                                 AS primary_clients_current,
  CAST(coalesce(a.groups_current, 0) AS BIGINT)                                                  AS primary_groups_current,
  CAST(coalesce(a.assignments_current, 0) AS BIGINT)                                             AS team_assignments_current
FROM role_pick p
LEFT JOIN attrs a ON a.employee_id = p.employee_id
LEFT JOIN rm_segment s ON s.employee_id = p.employee_id
LEFT JOIN onb_office o ON o.employee_id = p.employee_id
