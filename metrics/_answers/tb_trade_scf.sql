-- Space 8: APAC Genie - Transactional Banking: Trade & Supply Chain Finance (brief §5.8, PLAN §10).
-- One MEASURE() query per must-answer question; these become the Genie benchmarks (expected SQL names its
-- output columns). `-- rows:` lines are written by src/40_metrics/run_metrics.py. Written by WP8e (WP9b).
-- Point-in-time measures (Outstanding, programme limit / utilisation, supplier counts) need one Month
-- (Is Latest Month = Sep 2026) or a period whose last month-end is meant; issuance / fees / payments are flows.

-- Q1: Trade finance issuance by corridor, H1 FY2026 vs H1 FY2025 - the fastest-growing corridors (at least USD 20m in H1 FY2025).
-- views: mv_tb_trade_finance
-- rows: 10
WITH h AS (
  SELECT `Corridor`, `Fiscal Half`, MEASURE(`Issuance USD`) AS issuance_usd
  FROM smbc_genie.metrics.mv_tb_trade_finance
  WHERE `Fiscal Half` IN ('FY2025-H1', 'FY2026-H1')
  GROUP BY ALL
), c AS (
  SELECT `Corridor`,
         sum(CASE WHEN `Fiscal Half` = 'FY2025-H1' THEN issuance_usd END) AS issuance_h1_fy2025_usd,
         sum(CASE WHEN `Fiscal Half` = 'FY2026-H1' THEN issuance_usd END) AS issuance_h1_fy2026_usd
  FROM h GROUP BY `Corridor`
)
SELECT `Corridor`, issuance_h1_fy2025_usd, issuance_h1_fy2026_usd,
       issuance_h1_fy2026_usd / issuance_h1_fy2025_usd - 1 AS issuance_growth_yoy
FROM c
WHERE issuance_h1_fy2025_usd >= 20000000
ORDER BY issuance_growth_yoy DESC
LIMIT 10;

-- Q1b: Trade finance issuance by product, H1 FY2026 vs H1 FY2025.
-- views: mv_tb_trade_finance
-- rows: 6
WITH h AS (
  SELECT `Product`, `Fiscal Half`, MEASURE(`Issuance USD`) AS issuance_usd
  FROM smbc_genie.metrics.mv_tb_trade_finance
  WHERE `Fiscal Half` IN ('FY2025-H1', 'FY2026-H1')
  GROUP BY ALL
)
SELECT `Product`,
       sum(CASE WHEN `Fiscal Half` = 'FY2025-H1' THEN issuance_usd END) AS issuance_h1_fy2025_usd,
       sum(CASE WHEN `Fiscal Half` = 'FY2026-H1' THEN issuance_usd END) AS issuance_h1_fy2026_usd,
       sum(CASE WHEN `Fiscal Half` = 'FY2026-H1' THEN issuance_usd END)
         / sum(CASE WHEN `Fiscal Half` = 'FY2025-H1' THEN issuance_usd END) - 1 AS issuance_growth_yoy
FROM h GROUP BY `Product`
ORDER BY issuance_growth_yoy DESC;

-- Q2: Import LC volume into Vietnam and India by commodity and client segment this fiscal year.
-- views: mv_tb_trade_finance
-- rows: 78
SELECT `Corridor To`, `Commodity`, `Segment`,
       MEASURE(`Transactions`) AS import_lcs,
       MEASURE(`Issuance USD`) AS import_lc_volume_usd
FROM smbc_genie.metrics.mv_tb_trade_finance
WHERE `Fiscal Year` = 'FY2026' AND `Product` = 'Import LC' AND `Corridor To` IN ('VN', 'IN')
GROUP BY ALL
ORDER BY import_lc_volume_usd DESC;

-- Q2b: VN / IN trade surge (storyline) - import LCs into VN / IN of electronics and auto parts from CN / KR / JP, H1 FY2026 vs H1 FY2025, and the share booked in Singapore.
-- views: mv_tb_trade_finance
-- rows: 1
WITH h AS (
  SELECT `Fiscal Half`, `Booking Country`, MEASURE(`Transactions`) AS import_lcs
  FROM smbc_genie.metrics.mv_tb_trade_finance
  WHERE `Fiscal Half` IN ('FY2025-H1', 'FY2026-H1') AND `Product` = 'Import LC' AND `Corridor To` IN ('VN', 'IN')
    AND `Corridor From` IN ('CN', 'KR', 'JP') AND `Commodity` IN ('Electronics', 'Auto Parts')
  GROUP BY ALL
)
SELECT sum(CASE WHEN `Fiscal Half` = 'FY2026-H1' THEN import_lcs END) AS import_lcs_h1_fy2026,
       sum(CASE WHEN `Fiscal Half` = 'FY2025-H1' THEN import_lcs END) AS import_lcs_h1_fy2025,
       sum(CASE WHEN `Fiscal Half` = 'FY2026-H1' THEN import_lcs END)
         / sum(CASE WHEN `Fiscal Half` = 'FY2025-H1' THEN import_lcs END) - 1 AS import_lc_growth_yoy,
       sum(CASE WHEN `Fiscal Half` = 'FY2026-H1' AND `Booking Country` = 'SG' THEN import_lcs END)
         / sum(CASE WHEN `Fiscal Half` = 'FY2026-H1' THEN import_lcs END) AS share_booked_in_singapore
FROM h;

-- Q3: SCF programme utilisation and supplier activation by anchor today - programmes above 85% or below 40% are flagged in the band.
-- views: mv_tb_scf
-- rows: 8
SELECT `Programme`, `Client` AS anchor, `Anchor Country`, `Utilisation Band`,
       MEASURE(`Programme Limit USD`)       AS programme_limit_usd,
       MEASURE(`Financed Outstanding USD`)  AS financed_outstanding_usd,
       MEASURE(`Utilisation %`)             AS utilisation,
       MEASURE(`Suppliers in Register`)     AS suppliers_in_register,
       MEASURE(`Suppliers Onboarded`)       AS suppliers_onboarded,
       MEASURE(`Suppliers Active`)          AS suppliers_active,
       MEASURE(`Supplier Activation %`)     AS supplier_activation_rate
FROM smbc_genie.metrics.mv_tb_scf
WHERE `Is Latest Month`
GROUP BY ALL
ORDER BY utilisation DESC;

-- Q4: Document discrepancy rate by booking country this fiscal year.
-- views: mv_trade_operations
-- rows: 13
SELECT `Booking Country`,
       MEASURE(`Examined Presentations`) AS examined_presentations,
       MEASURE(`Discrepancy Rate %`)     AS discrepancy_rate
FROM smbc_genie.metrics.mv_trade_operations
WHERE `Fiscal Year` = 'FY2026'
GROUP BY ALL
ORDER BY discrepancy_rate DESC;

-- Q4b: Discrepant presentations by discrepancy type this fiscal year.
-- views: mv_trade_operations
-- rows: 9
SELECT `Discrepancy Type`,
       MEASURE(`Discrepant Presentations`) AS discrepant_presentations
FROM smbc_genie.metrics.mv_trade_operations
WHERE `Fiscal Year` = 'FY2026' AND `Is Discrepant`
GROUP BY ALL
ORDER BY discrepant_presentations DESC;

-- Q4c: Clients with a rising discrepancy rate (H2 FY2025 -> H1 FY2026, +10 points or more) - an early-warning input.
-- views: mv_trade_operations
-- rows: 15
WITH r AS (
  SELECT `Client`, `Fiscal Half`, MEASURE(`Discrepancy Rate %`) AS discrepancy_rate
  FROM smbc_genie.metrics.mv_trade_operations
  WHERE `Is Client Discrepancy Rising` AND `Fiscal Half` IN ('FY2025-H2', 'FY2026-H1')
  GROUP BY ALL
)
SELECT `Client`,
       max(CASE WHEN `Fiscal Half` = 'FY2025-H2' THEN discrepancy_rate END) AS discrepancy_rate_h2_fy2025,
       max(CASE WHEN `Fiscal Half` = 'FY2026-H1' THEN discrepancy_rate END) AS discrepancy_rate_h1_fy2026
FROM r GROUP BY `Client`
ORDER BY discrepancy_rate_h1_fy2026 - discrepancy_rate_h2_fy2025 DESC
LIMIT 15;

-- Q5: Trade ops turnaround this fiscal year - median examination hours by product and country vs the 24-hour SLA.
-- views: mv_trade_operations
-- rows: 26
SELECT `Product`, `Booking Country`,
       MEASURE(`Examined Presentations`)  AS examined_presentations,
       MEASURE(`Median Turnaround Hours`) AS median_turnaround_hours,
       MEASURE(`SLA Hours`)               AS sla_hours,
       MEASURE(`Over SLA %`)              AS share_over_sla
FROM smbc_genie.metrics.mv_trade_operations
WHERE `Fiscal Year` = 'FY2026'
GROUP BY ALL
ORDER BY median_turnaround_hours DESC;

-- Q6: Clients active in the CN->VN electronics corridor (visible from their trade-settlement payments) that have no trade product with us.
-- views: mv_trade_corridors
-- rows: 4
SELECT `Client`, `Segment`, `Coverage Office`,
       MEASURE(`Trade Settlement Payments USD`) AS trade_settlement_payments_usd
FROM smbc_genie.metrics.mv_trade_corridors
WHERE `Corridor` = 'CN->VN' AND `Commodity` = 'Electronics' AND `Is Payment Visible` AND NOT `Has Trade Product Today`
GROUP BY ALL
ORDER BY trade_settlement_payments_usd DESC;

-- Q6b: The CN->VN electronics corridor - market size, SMBC share and clients without a trade product (FY2025 and FY2026 to date).
-- views: mv_trade_corridors
-- rows: 2
SELECT `Fiscal Year`,
       MEASURE(`Corridor Volume USD`)                       AS corridor_market_volume_usd,
       MEASURE(`SMBC Trade Finance USD`)                    AS smbc_trade_finance_usd,
       MEASURE(`SMBC Share Estimate %`)                     AS smbc_share_estimate,
       MEASURE(`Clients Active in Corridor`)                AS clients_active,
       MEASURE(`Clients in Corridor Without Trade Product`) AS clients_without_trade_product
FROM smbc_genie.metrics.mv_trade_corridors
WHERE `Corridor` = 'CN->VN' AND `Commodity` = 'Electronics' AND `Fiscal Year` IN ('FY2025', 'FY2026')
GROUP BY ALL
ORDER BY `Fiscal Year`;

-- Q6c: External trade statistics (storyline) - the VN / IN import corridors from CN / KR / JP for electronics and auto parts grow too, H1 FY2026 vs H1 FY2025.
-- views: mv_trade_corridors
-- rows: 1
SELECT MEASURE(`Corridor Volume USD`)            AS corridor_market_volume_h1_fy2026_usd,
       MEASURE(`Corridor Volume Prior Year USD`) AS corridor_market_volume_h1_fy2025_usd,
       MEASURE(`Corridor Growth YoY %`)          AS corridor_growth_yoy
FROM smbc_genie.metrics.mv_trade_corridors
WHERE `Fiscal Half` = 'FY2026-H1' AND `Corridor To` IN ('VN', 'IN') AND `Corridor From` IN ('CN', 'KR', 'JP')
  AND `Commodity` IN ('Electronics', 'Auto Parts');

-- Q7: Guarantees and SBLCs outstanding by beneficiary country and expiry quarter (30-Sep-2026).
-- views: mv_tb_trade_finance
-- rows: 101
SELECT `Beneficiary Country`, `Expiry Quarter`,
       MEASURE(`Live Instruments`) AS live_guarantees,
       MEASURE(`Outstanding USD`)  AS outstanding_usd
FROM smbc_genie.metrics.mv_tb_trade_finance
WHERE `Is Latest Month` AND `Product` = 'Guarantee / SBLC'
GROUP BY ALL
HAVING MEASURE(`Outstanding USD`) > 0
ORDER BY outstanding_usd DESC;

-- Q8: Fee yield on trade products by segment - are Japanese-corporate trade fees below non-Japanese (FY2025 and FY2026 to date)?
-- views: mv_tb_trade_finance
-- rows: 6
SELECT `Fiscal Year`, `Is Japanese Corporate`,
       MEASURE(`Fee Yield bps`)  AS fee_yield_bps,
       MEASURE(`Fee Income USD`) AS fee_income_usd,
       MEASURE(`Transactions`)   AS instruments
FROM smbc_genie.metrics.mv_tb_trade_finance
WHERE `Fiscal Year` IN ('FY2025', 'FY2026')
GROUP BY ALL
ORDER BY `Fiscal Year`, `Is Japanese Corporate`;

-- Q8b: Fee yield by segment and product (FY2025).
-- views: mv_tb_trade_finance
-- rows: 12
SELECT `Segment`, `Product`, MEASURE(`Fee Yield bps`) AS fee_yield_bps
FROM smbc_genie.metrics.mv_tb_trade_finance
WHERE `Fiscal Year` = 'FY2025' AND `Segment` IN ('Japanese Corporate', 'Non-Japanese Large Corporate')
GROUP BY ALL
ORDER BY `Product`, `Segment`;

-- Q9: Anchor-buyer candidates for new SCF programmes - buyers paying 50 or more distinct suppliers through us in the last 12 months, not already an SCF anchor.
-- views: mv_tb_payments, mv_tb_scf
-- rows: 8
WITH sup AS (
  SELECT `Client`, `Segment`, `Coverage Office`,
         MEASURE(`Distinct Suppliers Paid`) AS distinct_suppliers_paid,
         MEASURE(`Payment Volume USD`)      AS supplier_payments_usd
  FROM smbc_genie.metrics.mv_tb_payments
  WHERE `Month` >= DATE'2025-10-01' AND `Direction` = 'Outbound' AND `Payment Purpose` = 'Supplier'
  GROUP BY ALL
), anchors AS (
  SELECT `Client`, MEASURE(`Programmes`) AS programmes
  FROM smbc_genie.metrics.mv_tb_scf
  WHERE `Is Latest Month`
  GROUP BY ALL
)
SELECT s.`Client`, s.`Segment`, s.`Coverage Office`, s.distinct_suppliers_paid, s.supplier_payments_usd
FROM sup s LEFT ANTI JOIN anchors a ON a.`Client` = s.`Client`
WHERE s.distinct_suppliers_paid >= 50
ORDER BY s.supplier_payments_usd DESC;

-- Q10: Trade exposure to carbon-intensive commodities, trend by fiscal quarter (outstanding at each quarter-end, from FY2024).
-- views: mv_tb_trade_finance
-- rows: 10
WITH q AS (
  SELECT `Fiscal Quarter`, `Is Carbon-Intensive Commodity`, MEASURE(`Outstanding USD`) AS outstanding_usd
  FROM smbc_genie.metrics.mv_tb_trade_finance
  WHERE `Fiscal Quarter` >= 'FY2024-Q1'
  GROUP BY ALL
)
SELECT `Fiscal Quarter`,
       sum(CASE WHEN `Is Carbon-Intensive Commodity` THEN outstanding_usd END) AS carbon_intensive_outstanding_usd,
       sum(outstanding_usd) AS total_outstanding_usd,
       sum(CASE WHEN `Is Carbon-Intensive Commodity` THEN outstanding_usd END) / sum(outstanding_usd) AS carbon_intensive_share
FROM q GROUP BY `Fiscal Quarter`
ORDER BY `Fiscal Quarter`;
