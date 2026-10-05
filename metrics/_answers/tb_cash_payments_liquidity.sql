-- Space 7: APAC Genie - Transactional Banking: Cash, Payments & Liquidity (brief §5.7, PLAN §10).
-- One MEASURE() query per must-answer question; these become the Genie benchmarks (expected SQL names its
-- output columns). `-- rows:` lines are written by src/40_metrics/run_metrics.py.
-- WP9a wrote the mv_tb_deposits questions (Q1, Q2, Q3, Q7, Q10 and the deposits half of Q8); WP8e (WP9b) added Q4,
-- Q5, Q6, Q8 (payments YoY CTE joined to a deposits CTE, D35) and Q9 on mv_tb_payments / mv_tb_liquidity_structures /
-- mv_tb_channel_adoption.

-- Q1: Total CASA balance by booking country at end September 2026 and the movement versus August.
-- views: mv_tb_deposits
-- rows: 13
SELECT `Booking Country`,
       MEASURE(`CASA Balance USD`)               AS casa_balance_sep_2026_usd,
       MEASURE(`CASA Balance Prior Month USD`)   AS casa_balance_aug_2026_usd,
       MEASURE(`CASA Change vs Prior Month USD`) AS casa_change_vs_aug_usd
FROM smbc_genie.metrics.mv_tb_deposits
WHERE `Month` = DATE'2026-09-01'
GROUP BY ALL
ORDER BY casa_balance_sep_2026_usd DESC;

-- Q2: Which 20 client groups drove the largest CASA outflow in Q2 FY2026, and did it move to time deposits or leave the bank?
-- views: mv_tb_deposits
-- rows: 20
WITH g AS (
  SELECT `Client Group`,
         MEASURE(`CASA Movement USD`)     AS casa_movement_usd,
         MEASURE(`Transferred to TD USD`) AS casa_moved_to_td_usd,
         MEASURE(`Deposit Movement USD`)  AS total_deposit_movement_usd
  FROM smbc_genie.metrics.mv_tb_deposits
  WHERE `Fiscal Quarter` = 'FY2026-Q2'
  GROUP BY ALL
)
SELECT `Client Group`, casa_movement_usd, casa_moved_to_td_usd, total_deposit_movement_usd,
       CASE WHEN total_deposit_movement_usd >= casa_movement_usd / 2 THEN 'Mostly moved to time deposits'
            ELSE 'Mostly left the bank' END AS where_it_went
FROM g
WHERE casa_movement_usd < 0
ORDER BY casa_movement_usd ASC
LIMIT 20;

-- Q2b: HK CASA migration (storyline): CASA movement and CASA-to-TD transfers in Hong Kong, Jun-Aug 2026, by month.
-- views: mv_tb_deposits
-- rows: 3
SELECT `Month`,
       MEASURE(`CASA Movement USD`)     AS casa_movement_usd,
       MEASURE(`Transferred to TD USD`) AS casa_moved_to_td_usd,
       MEASURE(`CASA Ratio %`)          AS casa_ratio
FROM smbc_genie.metrics.mv_tb_deposits
WHERE `Booking Country` = 'HK' AND `Month` BETWEEN DATE'2026-06-01' AND DATE'2026-08-01'
GROUP BY ALL
ORDER BY `Month`;

-- Q3: CASA ratio trend, monthly, Japanese vs non-Japanese corporates since FY2025.
-- views: mv_tb_deposits
-- rows: 36
SELECT `Month`,
       CASE WHEN `Is Japanese Corporate` THEN 'Japanese corporate' ELSE 'Non-Japanese' END AS client_type,
       MEASURE(`CASA Ratio %`) AS casa_ratio
FROM smbc_genie.metrics.mv_tb_deposits
WHERE `Month` >= DATE'2025-04-01'
GROUP BY ALL
ORDER BY `Month`, client_type;

-- Q7: Top 10 depositor concentration by booking country.
-- views: mv_tb_deposits
-- rows: 13
SELECT `Booking Country`,
       MEASURE(`Top-10 Depositor Concentration %`) AS top10_depositor_share,
       MEASURE(`End of Month Balance USD`)         AS total_deposits_usd,
       MEASURE(`Depositors`)                       AS depositors
FROM smbc_genie.metrics.mv_tb_deposits
WHERE `Is Latest Month`
GROUP BY ALL
ORDER BY top10_depositor_share DESC;

-- Q7b: The ten largest depositors in Singapore today (drill-down of Q7).
-- views: mv_tb_deposits
-- rows: 10
SELECT `Client`, `Client Group`, `Depositor Rank in Country`,
       MEASURE(`End of Month Balance USD`) AS deposits_usd
FROM smbc_genie.metrics.mv_tb_deposits
WHERE `Is Latest Month` AND `Booking Country` = 'SG' AND `Depositor Rank in Country` <= 10
GROUP BY ALL
ORDER BY `Depositor Rank in Country`;

-- Q8a: Deposits half of Q8 - clients whose deposit balances held steady year on year (within +/-10%, Sep 2026 vs Sep 2025).
-- views: mv_tb_deposits
-- rows: 1177
SELECT * FROM (
  SELECT `Client`, `Client Group`, `Booking Country`,
         MEASURE(`End of Month Balance USD`)            AS deposits_sep_2026_usd,
         MEASURE(`End of Month Balance Prior Year USD`) AS deposits_sep_2025_usd,
         MEASURE(`Balance YoY %`)                       AS deposits_yoy
  FROM smbc_genie.metrics.mv_tb_deposits
  WHERE `Is Latest Month`
  GROUP BY ALL
) WHERE deposits_yoy BETWEEN -0.10 AND 0.10
ORDER BY deposits_sep_2026_usd DESC;

-- Q10: Time-deposit maturities in the next 90 days by country and currency.
-- views: mv_tb_deposits
-- rows: 31
SELECT * FROM (
  SELECT `Booking Country`, `Currency`,
         MEASURE(`TD Maturing Next 90D USD`) AS td_maturing_next_90d_usd
  FROM smbc_genie.metrics.mv_tb_deposits
  WHERE `Is Latest Month`
  GROUP BY ALL
) WHERE td_maturing_next_90d_usd > 0
ORDER BY `Booking Country`, td_maturing_next_90d_usd DESC;

-- Q4: Payment volume by purpose and counterparty bank type (this fiscal year, outbound) - how much goes to accounts at other banks?
-- views: mv_tb_payments
-- rows: 13
SELECT `Payment Purpose`, `Counterparty Bank Type`,
       MEASURE(`Payment Volume USD`) AS payment_volume_usd,
       MEASURE(`Payments`)           AS payments
FROM smbc_genie.metrics.mv_tb_payments
WHERE `Fiscal Year` = 'FY2026' AND `Direction` = 'Outbound'
GROUP BY ALL
ORDER BY `Payment Purpose`, payment_volume_usd DESC;

-- Q4b: Share of clients' outbound supplier payments (FY2026 to date) paid to accounts at other banks.
-- views: mv_tb_payments
-- rows: 1
SELECT MEASURE(`Share Paid via Other Banks %`) AS supplier_share_paid_to_other_banks,
       MEASURE(`Volume via Other Banks USD`)   AS supplier_payments_to_other_banks_usd,
       MEASURE(`Payment Volume USD`)           AS supplier_payments_usd
FROM smbc_genie.metrics.mv_tb_payments
WHERE `Fiscal Year` = 'FY2026' AND `Direction` = 'Outbound' AND `Payment Purpose` = 'Supplier';

-- Q5: STP rate by channel and currency this fiscal year (lowest first).
-- views: mv_tb_payments
-- rows: 12
SELECT * FROM (
  SELECT `Channel`, `Currency`,
         MEASURE(`Payments`)   AS payments,
         MEASURE(`STP Rate %`) AS stp_rate,
         MEASURE(`Repairs`)    AS repairs
  FROM smbc_genie.metrics.mv_tb_payments
  WHERE `Fiscal Year` = 'FY2026'
  GROUP BY ALL
) WHERE payments >= 200
ORDER BY stp_rate
LIMIT 12;

-- Q5b: Where repairs are concentrated this fiscal year - repair reason by channel.
-- views: mv_tb_payments
-- rows: 10
SELECT `Repair Reason`, `Channel`,
       MEASURE(`Repairs`)                  AS repairs,
       MEASURE(`Average Processing Hours`) AS average_processing_hours
FROM smbc_genie.metrics.mv_tb_payments
WHERE `Fiscal Year` = 'FY2026' AND `Repair Reason` IS NOT NULL
GROUP BY ALL
ORDER BY repairs DESC
LIMIT 10;

-- Q6: Multi-country groups (3 or more APAC countries) with no liquidity pooling structure, ranked by total deposits.
-- views: mv_tb_liquidity_structures
-- rows: 20
SELECT `Client Group`, `Group APAC Countries`,
       MEASURE(`Unpooled Group Deposits USD`) AS group_deposits_usd
FROM smbc_genie.metrics.mv_tb_liquidity_structures
WHERE `Is Latest Month` AND `Is Pooling Candidate`
GROUP BY ALL
ORDER BY group_deposits_usd DESC
LIMIT 20;

-- Q6b: How many multi-country groups have no pooling today, and the pooling penetration of multi-country groups.
-- views: mv_tb_liquidity_structures
-- rows: 1
SELECT MEASURE(`Clients Without Pooling`)     AS multi_country_groups_without_pooling,
       MEASURE(`Multi-Country Groups`)        AS multi_country_groups,
       MEASURE(`Pooling Penetration %`)       AS pooling_penetration,
       MEASURE(`Unpooled Group Deposits USD`) AS unpooled_group_deposits_usd
FROM smbc_genie.metrics.mv_tb_liquidity_structures
WHERE `Is Latest Month`;

-- Q8: Clients whose payment volume through us dropped more than 30% YoY (H1 FY2026 vs H1 FY2025) while their average month-end deposits held steady (+/-10%).
-- views: mv_tb_payments, mv_tb_deposits
-- rows: 79
WITH pay AS (
  SELECT `Client`, `Fiscal Half`, MEASURE(`Payment Volume USD`) AS payment_volume_usd
  FROM smbc_genie.metrics.mv_tb_payments
  WHERE `Fiscal Half` IN ('FY2025-H1', 'FY2026-H1')
  GROUP BY ALL
), p AS (
  SELECT `Client`,
         sum(CASE WHEN `Fiscal Half` = 'FY2025-H1' THEN payment_volume_usd END) AS payments_h1_fy2025_usd,
         sum(CASE WHEN `Fiscal Half` = 'FY2026-H1' THEN payment_volume_usd END) AS payments_h1_fy2026_usd
  FROM pay GROUP BY `Client`
), dep AS (
  SELECT `Client`, `Fiscal Half`, `Month`, MEASURE(`End of Month Balance USD`) AS deposits_usd
  FROM smbc_genie.metrics.mv_tb_deposits
  WHERE `Fiscal Half` IN ('FY2025-H1', 'FY2026-H1')
  GROUP BY ALL
), d AS (
  SELECT `Client`,
         sum(CASE WHEN `Fiscal Half` = 'FY2025-H1' THEN deposits_usd END) / 6 AS avg_deposits_h1_fy2025_usd,
         sum(CASE WHEN `Fiscal Half` = 'FY2026-H1' THEN deposits_usd END) / 6 AS avg_deposits_h1_fy2026_usd
  FROM dep GROUP BY `Client`
)
SELECT p.`Client`, p.payments_h1_fy2025_usd, coalesce(p.payments_h1_fy2026_usd, 0) AS payments_h1_fy2026_usd,
       coalesce(p.payments_h1_fy2026_usd, 0) / p.payments_h1_fy2025_usd - 1 AS payment_volume_yoy,
       d.avg_deposits_h1_fy2026_usd / d.avg_deposits_h1_fy2025_usd - 1 AS avg_deposits_change
FROM p JOIN d ON d.`Client` = p.`Client`
WHERE p.payments_h1_fy2025_usd >= 1000000
  AND coalesce(p.payments_h1_fy2026_usd, 0) / p.payments_h1_fy2025_usd - 1 < -0.30
  AND abs(d.avg_deposits_h1_fy2026_usd / d.avg_deposits_h1_fy2025_usd - 1) <= 0.10
ORDER BY p.payments_h1_fy2025_usd - coalesce(p.payments_h1_fy2026_usd, 0) DESC;

-- Q9: Digital channel adoption by segment this fiscal year - who still sends manual instructions?
-- views: mv_tb_channel_adoption
-- rows: 5
SELECT `Segment`,
       MEASURE(`Active Clients`)                      AS active_clients,
       MEASURE(`Digital Share of Payments %`)         AS digital_share,
       MEASURE(`Manual Instruction Share %`)          AS manual_instruction_share,
       MEASURE(`Clients Sending Manual Instructions`) AS clients_sending_manual_instructions
FROM smbc_genie.metrics.mv_tb_channel_adoption
WHERE `Fiscal Year` = 'FY2026'
GROUP BY ALL
ORDER BY manual_instruction_share DESC;

-- Q9b: The clients still sending the most manual instructions this fiscal year.
-- views: mv_tb_channel_adoption
-- rows: 10
SELECT `Client`, `Segment`, `Relationship Tier`,
       MEASURE(`Manual Instructions`)        AS manual_instructions,
       MEASURE(`Manual Instruction Share %`) AS manual_instruction_share
FROM smbc_genie.metrics.mv_tb_channel_adoption
WHERE `Fiscal Year` = 'FY2026'
GROUP BY ALL
HAVING MEASURE(`Manual Instructions`) > 0
ORDER BY manual_instructions DESC
LIMIT 10;
