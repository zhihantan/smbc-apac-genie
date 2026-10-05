-- UC SQL table functions for APAC Genie - Account Planning (G1 / p07g1). Deployed by the Genie runner
-- (CREATE OR REPLACE). Client-group parameters match the exact group name first (e.g. 'Tanaka Chemical' is not
-- 'Tanaka Chemical Group'); only when no group has that exact name is the text matched as a LIKE fragment.
-- Arguments are STRING only (genie/README.md); the `-- test:` probes must return rows.
-- test: SELECT * FROM smbc_genie.gold.fn_account_plan_status('Kinokawa Precision', 'FY2026')
-- test: SELECT * FROM smbc_genie.gold.fn_account_plan_footprint('Tanaka Chemical')
-- test: SELECT * FROM smbc_genie.gold.fn_account_plan_rm_book('Singapore', 'FY2026')

CREATE OR REPLACE FUNCTION smbc_genie.gold.fn_account_plan_status(
  client_group STRING COMMENT 'Client group (global parent) name, e.g. Kinokawa Precision. Exact group name first; otherwise a name fragment matched with LIKE.',
  fiscal_year STRING DEFAULT 'FY2026' COMMENT 'Japanese fiscal-year label, e.g. FY2026 (Apr 2026 - Mar 2027; H1 closed 30-Sep-2026). Default FY2026.'
)
RETURNS TABLE (
  `Product Family` STRING COMMENT 'Plan product family (Corporate Lending, Cash, Liquidity, Payments, Trade Finance, Supply Chain Finance, FX, Sustainable Finance).',
  original_target_usd DOUBLE COMMENT 'Original full-year revenue target of the plan line, USD.',
  revised_target_usd DOUBLE COMMENT 'Mid-year revised full-year target, USD (null when the line was not revised).',
  target_in_force_usd DOUBLE COMMENT 'Target in force, USD = the revised target where revised, else the original target.',
  h1_actual_usd DOUBLE COMMENT 'Actual revenue year to date, USD (FY2026 = H1, Apr-Sep 2026; past years = full year).',
  full_year_run_rate_usd DOUBLE COMMENT 'Actual year to date annualised, USD (FY2026 H1 x 2).',
  attainment DOUBLE COMMENT 'Actual YTD on planned lines / target in force (fraction; 0.40 = 40%; about 0.50 is on track at H1).'
)
COMMENT 'Account-plan status of ONE client group for a fiscal year, by product family: original target, mid-year revised target, target in force, actual revenue year to date (FY2026 = H1 Apr-Sep 2026), full-year run-rate and plan attainment. Use for "account plan status for <group>", "plan vs actual", "target vs H1 actual and run-rate", "how is <group> tracking against plan". USD. Shows the Kinokawa Precision FY2026 mid-year plan reset (revised vs original targets).'
RETURN
  SELECT `Product Family`,
         MEASURE(`Revenue Target USD`)     AS original_target_usd,
         MEASURE(`Revised Target USD`)     AS revised_target_usd,
         MEASURE(`Effective Target USD`)   AS target_in_force_usd,
         MEASURE(`Revenue Actual YTD USD`) AS h1_actual_usd,
         MEASURE(`Run-Rate Full-Year USD`) AS full_year_run_rate_usd,
         MEASURE(`Attainment %`)           AS attainment
  FROM smbc_genie.metrics.mv_account_plan_progress
  WHERE `Fiscal Year` = fn_account_plan_status.fiscal_year
    AND `Client Group` IN (
      SELECT g.group_name FROM smbc_genie.gold.dim_client_group g
      WHERE lower(g.group_name) = lower(trim(fn_account_plan_status.client_group))
         OR (lower(g.group_name) LIKE concat('%', lower(trim(fn_account_plan_status.client_group)), '%')
             AND NOT EXISTS (SELECT 1 FROM smbc_genie.gold.dim_client_group x
                             WHERE lower(x.group_name) = lower(trim(fn_account_plan_status.client_group)))))
  GROUP BY ALL;

CREATE OR REPLACE FUNCTION smbc_genie.gold.fn_account_plan_footprint(
  client_group STRING COMMENT 'Client group (global parent) name, e.g. Tanaka Chemical. Exact group name first; otherwise a name fragment matched with LIKE.'
)
RETURNS TABLE (
  `Client` STRING COMMENT 'Legal entity of the group (golden client display name).',
  `Coverage Office` STRING COMMENT 'APAC booking location covering the entity (ISO-2, e.g. SG, HK).',
  products_held BIGINT COMMENT 'Distinct products the entity holds or used in the last 12 months at Sep-2026.',
  deposit_only_entity BIGINT COMMENT '1 when every product the entity holds is a deposit product (nothing but deposits), else 0.',
  deposits_usd DOUBLE COMMENT 'Deposit balances at the Sep-2026 month-end, USD.',
  lending_drawn_usd DOUBLE COMMENT 'Drawn credit-facility balances at the Sep-2026 month-end, USD.'
)
COMMENT 'Relationship footprint TODAY (Sep-2026 month-end) of every APAC legal entity of ONE client group: products held, whether the entity holds nothing but deposits (deposit-only = 1), deposits and drawn lending. Use for "which entities of <group> hold only deposits", "what does each <group> entity hold with us", "entity footprint of <group>". USD.'
RETURN
  SELECT `Client`, `Coverage Office`,
         MEASURE(`Products Held`)         AS products_held,
         MEASURE(`Deposit-Only Entities`) AS deposit_only_entity,
         MEASURE(`Deposits USD`)          AS deposits_usd,
         MEASURE(`Lending Drawn USD`)     AS lending_drawn_usd
  FROM smbc_genie.metrics.mv_relationship_footprint
  WHERE `Is Latest Month`
    AND `Client Group` IN (
      SELECT g.group_name FROM smbc_genie.gold.dim_client_group g
      WHERE lower(g.group_name) = lower(trim(fn_account_plan_footprint.client_group))
         OR (lower(g.group_name) LIKE concat('%', lower(trim(fn_account_plan_footprint.client_group)), '%')
             AND NOT EXISTS (SELECT 1 FROM smbc_genie.gold.dim_client_group x
                             WHERE lower(x.group_name) = lower(trim(fn_account_plan_footprint.client_group)))))
  GROUP BY ALL;

CREATE OR REPLACE FUNCTION smbc_genie.gold.fn_account_plan_rm_book(
  rm_office STRING COMMENT 'Office where the RMs sit: ISO-2 code (SG, HK, CN, TH, ID, IN, AU, VN, MY, KR, TW, PH, NZ) or the country name, e.g. Singapore.',
  fiscal_year STRING DEFAULT 'FY2026' COMMENT 'Japanese fiscal-year label, e.g. FY2026 (Apr 2026 - Mar 2027; H1 closed 30-Sep-2026). Default FY2026.'
)
RETURNS TABLE (
  `Primary RM` STRING COMMENT 'Group lead RM (primary RM of the group lead entity; fictional name).',
  groups BIGINT COMMENT 'Client groups led by the RM.',
  revenue_ytd_usd DOUBLE COMMENT 'Revenue year to date of those groups, USD (FY2026 = H1, Apr-Sep 2026).',
  attainment DOUBLE COMMENT 'Revenue YTD on planned lines / target in force (fraction; about 0.50 is on track at H1).'
)
COMMENT 'RM book summary for the RMs based in one office: for each group lead RM, the number of client groups, revenue year to date and account-plan attainment for the fiscal year. Use for "RM book summary", "for each RM in Singapore / Hong Kong: groups, revenue YTD and attainment", "RM league table". USD; RM office = Primary RM Office (where the RM sits).'
RETURN
  SELECT `Primary RM`,
         MEASURE(`Groups`)                 AS groups,
         MEASURE(`Revenue Actual YTD USD`) AS revenue_ytd_usd,
         MEASURE(`Attainment %`)           AS attainment
  FROM smbc_genie.metrics.mv_account_plan_progress
  WHERE `Fiscal Year` = fn_account_plan_rm_book.fiscal_year
    AND `Primary RM Office` IN (
      SELECT c.country_code FROM smbc_genie.gold.dim_country c
      WHERE upper(c.country_code) = upper(trim(fn_account_plan_rm_book.rm_office))
         OR upper(c.country_name) = upper(trim(fn_account_plan_rm_book.rm_office)))
  GROUP BY ALL;
