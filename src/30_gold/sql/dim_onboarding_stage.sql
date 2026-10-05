-- dim_onboarding_stage: the funnel stages and SLA days as recorded on silver.kyc_case_stage (modal SLA per stage).
WITH s AS (
  SELECT stage_no, stage_name, sla_days, count(*) AS n, count(DISTINCT case_id) AS cases
  FROM ${catalog}.silver.kyc_case_stage
  WHERE stage_no IS NOT NULL
  GROUP BY stage_no, stage_name, sla_days
), pick AS (
  SELECT stage_no,
         max_by(stage_name, n) AS stage_name, max_by(sla_days, n) AS sla_days, sum(cases) AS cases_entered
  FROM s GROUP BY stage_no
), descr (stage_name, stage_description) AS (
  VALUES ('Request', 'RM submits the onboarding request with the products requested.'),
         ('KYC Docs', 'Collection and verification of KYC / CDD documents from the client.'),
         ('Screening', 'Sanctions, PEP and adverse-media screening of the entity and related parties.'),
         ('Risk Assessment', 'AML/CFT risk rating and enhanced due diligence where needed.'),
         ('Credit/Product Approval', 'Credit and product approvals for the requested facilities and services.'),
         ('Account Open', 'Accounts and channels set up in core banking; the client goes live.'),
         ('First Transaction', 'Activation: the first transaction through the new accounts.')
)
SELECT
  CAST(p.stage_no AS INT)                                                    AS stage_no,
  p.stage_name,
  CAST(p.sla_days AS INT)                                                    AS sla_days,
  CAST(sum(p.sla_days) OVER (ORDER BY p.stage_no ROWS UNBOUNDED PRECEDING) AS INT) AS cumulative_sla_days,
  p.stage_name = 'Account Open'                                              AS is_go_live_stage,
  p.stage_no = max(p.stage_no) OVER ()                                       AS is_final_stage,
  coalesce(d.stage_description, p.stage_name)                                AS stage_description,
  CAST(p.cases_entered AS BIGINT)                                            AS cases_entered
FROM pick p LEFT JOIN descr d ON d.stage_name = p.stage_name
