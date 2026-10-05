-- Space 1: APAC Genie - Account Planning (brief §5.1, PLAN §10).
-- One MEASURE() query per must-answer question; these become the Genie benchmarks (expected SQL names its
-- output columns). `-- rows:` lines are written by src/40_metrics/run_metrics.py. Written by WP8c/WP9b.

-- Q1: Account-plan status for the Kinokawa Precision group - FY2026 target vs H1 actual by product family, and the full-year run-rate.
-- views: mv_account_plan_progress
-- rows: 7
SELECT `Product Family`,
       MEASURE(`Revenue Target USD`)     AS original_target_usd,
       MEASURE(`Revised Target USD`)     AS revised_target_usd,
       MEASURE(`Effective Target USD`)   AS target_in_force_usd,
       MEASURE(`Revenue Actual YTD USD`) AS h1_actual_usd,
       MEASURE(`Run-Rate Full-Year USD`) AS full_year_run_rate_usd,
       MEASURE(`Attainment %`)           AS attainment
FROM smbc_genie.metrics.mv_account_plan_progress
WHERE `Client Group` = 'Kinokawa Precision' AND `Fiscal Year` = 'FY2026'
GROUP BY ALL
ORDER BY `Product Family`;

-- Q2: Which Strategic-tier groups are below 40% plan attainment at H1, by coverage office and RM?
-- views: mv_account_plan_progress
-- rows: 6
SELECT `Coverage Office`, `Plan Owner RM`, `Client Group`,
       MEASURE(`Attainment %`)              AS attainment_h1,
       MEASURE(`Revenue Actual YTD USD`)    AS revenue_h1_usd,
       MEASURE(`Effective Target USD`)      AS target_usd
FROM smbc_genie.metrics.mv_account_plan_progress
WHERE `Fiscal Year` = 'FY2026' AND `Relationship Tier` = 'Strategic'
GROUP BY ALL
HAVING MEASURE(`Attainment %`) < 0.40
ORDER BY attainment_h1;

-- Q3: What products does the Tanaka Chemical group hold with us across APAC, and which entities hold nothing but deposits?
-- views: mv_relationship_footprint
-- rows: 2
SELECT `Client`, `Coverage Office`,
       MEASURE(`Products Held`)         AS products_held,
       MEASURE(`Deposit-Only Entities`) AS deposit_only_entity,
       MEASURE(`Deposits USD`)          AS deposits_usd,
       MEASURE(`Lending Drawn USD`)     AS lending_drawn_usd
FROM smbc_genie.metrics.mv_relationship_footprint
WHERE `Client Group` = 'Tanaka Chemical' AND `Is Latest Month`
GROUP BY ALL
ORDER BY `Client`;

-- Q3b: The Tanaka Chemical group's products held across APAC today, by product family and product.
-- views: mv_relationship_footprint
-- rows: 16
SELECT `Product Family`, `Product`,
       MEASURE(`Product Holdings`) AS entities_holding,
       MEASURE(`Deposits USD`)     AS deposits_usd,
       MEASURE(`Lending Drawn USD`) AS lending_drawn_usd,
       MEASURE(`TB Volume 12M USD`) AS tb_volume_12m_usd
FROM smbc_genie.metrics.mv_relationship_footprint
WHERE `Client Group` = 'Tanaka Chemical' AND `Is Latest Month` AND `Is Active Product`
GROUP BY ALL
ORDER BY `Product Family`, `Product`;

-- Q4: Show the global relationship with Hayashi Marine Logistics - exposure, deposits and revenue by region including Japan, EMEA and Americas.
-- views: mv_global_group_relationship
-- rows: 4
SELECT `Region`,
       MEASURE(`Entity Count`)           AS entities,
       MEASURE(`Global Committed USD`)   AS committed_usd,
       MEASURE(`Global Drawn USD`)       AS drawn_usd,
       MEASURE(`Global Deposits USD`)    AS deposits_usd,
       MEASURE(`Global Revenue 12M USD`) AS revenue_12m_usd
FROM smbc_genie.metrics.mv_global_group_relationship
WHERE `Client Group` = 'Hayashi Marine Logistics' AND `Is Latest Month`
GROUP BY ALL
ORDER BY `Region`;

-- Q5: Our share of wallet by segment and product family in FY2025 - where is it lowest?
-- views: mv_account_plan_progress
-- rows: 10
SELECT `Segment`, `Product Family`,
       MEASURE(`Estimated Wallet USD`) AS wallet_usd,
       MEASURE(`Share of Wallet %`)    AS share_of_wallet
FROM smbc_genie.metrics.mv_account_plan_progress
WHERE `Fiscal Year` = 'FY2025'
GROUP BY ALL
HAVING MEASURE(`Estimated Wallet USD`) > 0
ORDER BY share_of_wallet ASC
LIMIT 10;

-- Q6: Which Strategic clients have had no RM contact in the last 90 days?
-- views: mv_rm_engagement
-- rows: 20
SELECT `Client`, `Client Group`, `Primary RM`,
       MEASURE(`Average Days Since Last Contact`) AS days_since_last_contact
FROM smbc_genie.metrics.mv_rm_engagement
WHERE `Is Latest Month`
GROUP BY ALL
HAVING MEASURE(`Strategic Clients Not Touched 90D`) > 0
ORDER BY days_since_last_contact DESC;

-- Q6b: How many Strategic clients have had no RM contact in 90 days, and how many Strategic-tier entities are not covered in CRM at all?
-- views: mv_rm_engagement
-- rows: 1
SELECT MEASURE(`Strategic Clients Not Touched 90D`) AS strategic_crm_clients_not_touched_90d,
       MEASURE(`Strategic Entities Without CRM`)    AS strategic_entities_without_crm_account,
       MEASURE(`Clients Not Touched 90D`)           AS crm_clients_not_touched_90d
FROM smbc_genie.metrics.mv_rm_engagement
WHERE `Is Latest Month`;

-- Q7: RM book summary - for each RM in Singapore, number of groups, revenue YTD and attainment.
-- views: mv_account_plan_progress
-- rows: 6
SELECT `Primary RM`,
       MEASURE(`Groups`)                 AS groups,
       MEASURE(`Revenue Actual YTD USD`) AS revenue_ytd_usd,
       MEASURE(`Attainment %`)           AS attainment
FROM smbc_genie.metrics.mv_account_plan_progress
WHERE `Fiscal Year` = 'FY2026' AND `Primary RM Office` = 'SG'
GROUP BY ALL
ORDER BY revenue_ytd_usd DESC;

-- Q8: Which Japanese-corporate groups grew revenue more than 20% YoY and which fell more than 15%? Break down by product.
-- views: mv_account_plan_progress
-- rows: 426
WITH g AS (
  SELECT `Client Group`, MEASURE(`Revenue YoY %`) AS group_yoy
  FROM smbc_genie.metrics.mv_account_plan_progress
  WHERE `Fiscal Year` = 'FY2026' AND `Segment` = 'Japanese Corporate'
  GROUP BY ALL
  HAVING MEASURE(`Revenue YoY %`) > 0.20 OR MEASURE(`Revenue YoY %`) < -0.15
), f AS (
  SELECT `Client Group`, `Product Family`,
         MEASURE(`Revenue YTD Comparable USD`) AS revenue_h1_fy2026_usd,
         MEASURE(`Revenue Prior YTD USD`)      AS revenue_h1_fy2025_usd,
         MEASURE(`Revenue YoY %`)              AS family_yoy
  FROM smbc_genie.metrics.mv_account_plan_progress
  WHERE `Fiscal Year` = 'FY2026' AND `Segment` = 'Japanese Corporate'
  GROUP BY ALL
)
SELECT CASE WHEN g.group_yoy > 0.20 THEN 'Grew > 20%' ELSE 'Fell > 15%' END AS bucket,
       g.`Client Group` AS client_group, g.group_yoy, f.`Product Family` AS product_family,
       f.revenue_h1_fy2026_usd, f.revenue_h1_fy2025_usd, f.family_yoy
FROM g JOIN f ON f.`Client Group` = g.`Client Group`
WHERE f.revenue_h1_fy2025_usd IS NOT NULL
ORDER BY bucket, g.group_yoy, product_family;

-- Q9: For groups where APAC is less than 20% of global group revenue, what is the APAC product footprint vs the rest of the group?
-- views: mv_global_group_relationship
-- rows: 16
WITH s AS (
  SELECT `Client Group`, MEASURE(`APAC Share of Group Revenue %`) AS apac_share
  FROM smbc_genie.metrics.mv_global_group_relationship
  WHERE `Is Latest Month`
  GROUP BY ALL
  HAVING MEASURE(`APAC Share of Group Revenue %`) < 0.20
), f AS (
  SELECT `Client Group`, `Region`, `Product Family`,
         MEASURE(`Global Revenue 12M USD`) AS revenue_12m_usd,
         MEASURE(`Global Drawn USD`)       AS drawn_usd,
         MEASURE(`Global Deposits USD`)    AS deposits_usd
  FROM smbc_genie.metrics.mv_global_group_relationship
  WHERE `Is Latest Month`
  GROUP BY ALL
)
SELECT CASE WHEN f.`Region` = 'APAC' THEN 'APAC' ELSE 'Rest of group' END AS scope, f.`Product Family` AS product_family,
       count(DISTINCT f.`Client Group`) AS groups, sum(f.revenue_12m_usd) AS revenue_12m_usd,
       sum(f.drawn_usd) AS drawn_usd, sum(f.deposits_usd) AS deposits_usd
FROM f JOIN s ON s.`Client Group` = f.`Client Group`
GROUP BY ALL
ORDER BY scope, revenue_12m_usd DESC;

-- Q10: List account-plan initiatives still open for Q3 FY2026 by RM (detail from gold.dim_account_plan_initiative, D15).
-- views: mv_account_plan_progress
-- rows: 49
WITH d AS (
  SELECT owner_rm_name, count(*) AS open_initiatives_due_q3, sum(target_revenue_usd) AS target_revenue_usd
  FROM smbc_genie.gold.dim_account_plan_initiative
  WHERE is_open AND due_fiscal_quarter_label = 'FY2026-Q3'
  GROUP BY 1
), v AS (
  SELECT `Plan Owner RM`, MEASURE(`Initiatives Open`) AS open_initiatives_fy2026, MEASURE(`Initiatives Overdue`) AS overdue_initiatives_fy2026
  FROM smbc_genie.metrics.mv_account_plan_progress
  WHERE `Fiscal Year` = 'FY2026'
  GROUP BY ALL
)
SELECT d.owner_rm_name AS rm, d.open_initiatives_due_q3, d.target_revenue_usd, v.open_initiatives_fy2026, v.overdue_initiatives_fy2026
FROM d LEFT JOIN v ON v.`Plan Owner RM` = d.owner_rm_name
ORDER BY d.open_initiatives_due_q3 DESC, rm;
