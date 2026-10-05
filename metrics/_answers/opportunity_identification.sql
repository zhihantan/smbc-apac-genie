-- Space 2: APAC Genie - Opportunity Identification (brief §5.2, PLAN §10).
-- One MEASURE() query per must-answer question; these become the Genie benchmarks (expected SQL names its
-- output columns). `-- rows:` lines are written by src/40_metrics/run_metrics.py. QS* = storyline checks. Written by WP8c/WP9b.

-- Q1: Top 25 open opportunities by estimated revenue across APAC, with signal type, RM and days open ("open opportunities" = open signals).
-- views: mv_opportunity_signals
-- rows: 25
SELECT `Client`, `Signal Type`, `Primary RM`, `Date` AS detected_date,
       MEASURE(`Open Estimated Revenue USD`) AS estimated_revenue_usd,
       MEASURE(`Days Open Average`)          AS days_open
FROM smbc_genie.metrics.mv_opportunity_signals
WHERE `Status` = 'Open'
GROUP BY ALL
ORDER BY estimated_revenue_usd DESC
LIMIT 25;

-- Q2: Which clients are paying USD/JPY or USD/VND through other banks (FX flow we do not capture), and how much flow?
-- views: mv_opportunity_signals
-- rows: 145
SELECT `Client`, `Currency Pair`,
       MEASURE(`Latest Observed Amount USD`) AS fx_flow_via_other_banks_12m_usd,
       MEASURE(`Signals`)                    AS signals
FROM smbc_genie.metrics.mv_opportunity_signals
WHERE `Signal Type` = 'FX_FLOW_VIA_OTHER_BANK' AND `Currency Pair` IN ('USD/JPY', 'USD/VND')
GROUP BY ALL
ORDER BY fx_flow_via_other_banks_12m_usd DESC;

-- Q3: Clients with a deposit surplus signal in the last 60 days that have no time-deposit or investment product with us.
-- views: mv_opportunity_signals
-- rows: 2
SELECT `Client`, `Date` AS detected_date, `Status`,
       MEASURE(`Observed Amount USD`) AS surplus_usd
FROM smbc_genie.metrics.mv_opportunity_signals
WHERE `Signal Type` = 'DEPOSIT_SURPLUS' AND `Date` BETWEEN DATE'2026-08-02' AND DATE'2026-09-30' AND NOT `Holds TD or Investment`
GROUP BY ALL
ORDER BY surplus_usd DESC;

-- Q4: Which groups have facilities maturing in the next 12 months with no refinancing opportunity in the pipeline?
-- views: mv_opportunity_signals
-- rows: 27
SELECT `Client Group`,
       MEASURE(`Open Signals`)        AS maturing_facility_signals,
       MEASURE(`Observed Amount USD`) AS maturing_amount_usd
FROM smbc_genie.metrics.mv_opportunity_signals
WHERE `Signal Type` = 'FACILITY_MATURING_12M' AND `Status` = 'Open' AND NOT `Has Open Lending Opportunity`
GROUP BY ALL
ORDER BY maturing_amount_usd DESC;

-- Q5: Product penetration gap vs peers for Japanese automotive-parts subsidiaries - which products are under-penetrated? (FY2026)
-- views: mv_product_penetration
-- rows: 11
SELECT `Product`,
       MEASURE(`Clients`)                         AS clients,
       MEASURE(`Penetration %`)                   AS penetration,
       MEASURE(`Peer Penetration %`)              AS peer_penetration,
       MEASURE(`Clients With Gap`)                AS clients_with_gap_vs_peers,
       MEASURE(`All-Client Penetration %`)        AS all_apac_penetration,
       MEASURE(`Gap vs All Clients pts`)          AS gap_vs_all_apac_pts
FROM smbc_genie.metrics.mv_product_penetration
WHERE `Fiscal Year` = 'FY2026' AND `Segment` = 'Japanese Corporate' AND `Subsector` = 'Auto Parts'
GROUP BY ALL
HAVING MEASURE(`Clients With Gap`) > 0 OR MEASURE(`Gap vs All Clients pts`) > 0.05
ORDER BY gap_vs_all_apac_pts DESC;

-- Q6: Next-best-product - top 20 clients by propensity for supply chain finance and the main drivers.
-- views: mv_next_best_product
-- rows: 20
SELECT `Client`, `Product`, `Propensity Rank`, `Top Drivers`,
       MEASURE(`Max Propensity`) AS propensity
FROM smbc_genie.metrics.mv_next_best_product
WHERE `Is Latest Month` AND `Product Family` = 'Supply Chain Finance'
GROUP BY ALL
ORDER BY propensity DESC
LIMIT 20;

-- Q7: Opportunities created from trade-corridor-growth signals into Vietnam and India this fiscal year, and their conversion rate.
-- views: mv_pipeline
-- rows: 2
SELECT `Corridor Destination`,
       MEASURE(`Opportunities`)        AS created,
       MEASURE(`Opportunities Closed`) AS closed,
       MEASURE(`Opportunities Won`)    AS won,
       MEASURE(`Win Rate %`)           AS win_rate
FROM smbc_genie.metrics.mv_pipeline
WHERE `Source Signal Type` = 'TRADE_CORRIDOR_GROWTH' AND `Corridor Destination` IN ('VN', 'IN') AND `Fiscal Year` = 'FY2026'
GROUP BY ALL
ORDER BY `Corridor Destination`;

-- Q7b: Total of Q7 - corridor-growth opportunities into VN and IN created in FY2026 (storyline 6 - 14 created, 10 closed, 6 won).
-- views: mv_pipeline
-- rows: 1
SELECT MEASURE(`Opportunities`)        AS created,
       MEASURE(`Opportunities Closed`) AS closed,
       MEASURE(`Opportunities Won`)    AS won,
       MEASURE(`Win Rate %`)           AS win_rate
FROM smbc_genie.metrics.mv_pipeline
WHERE `Source Signal Type` = 'TRADE_CORRIDOR_GROWTH' AND `Corridor Destination` IN ('VN', 'IN') AND `Fiscal Year` = 'FY2026';

-- Q8: Which capex / expansion news signals in Australia have not been actioned by an RM?
-- views: mv_opportunity_signals
-- rows: 7
SELECT `Client`, `Date` AS detected_date, `Status`,
       MEASURE(`Signals`)               AS signals,
       MEASURE(`Estimated Revenue USD`) AS estimated_revenue_usd
FROM smbc_genie.metrics.mv_opportunity_signals
WHERE `Signal Type` = 'CAPEX_NEWS' AND `Coverage Office` = 'AU' AND NOT `Is Actioned`
GROUP BY ALL
ORDER BY detected_date DESC;

-- Q9: Pipeline by expected close quarter and stage for H2 FY2026, weighted value by product.
-- views: mv_pipeline
-- rows: 62
SELECT `Expected Close Quarter`, `Stage`, `Product`,
       MEASURE(`Open Opportunities`)    AS open_opportunities,
       MEASURE(`Pipeline Amount USD`)   AS pipeline_amount_usd,
       MEASURE(`Weighted Pipeline USD`) AS weighted_pipeline_usd
FROM smbc_genie.metrics.mv_pipeline
WHERE `Expected Close Half` = 'FY2026-H2' AND `Is Open`
GROUP BY ALL
ORDER BY `Expected Close Quarter`, `Stage`, weighted_pipeline_usd DESC;

-- Q10: Win rate by source signal type - which signals actually convert?
-- views: mv_pipeline
-- rows: 13
SELECT `Source Type`, `Source Signal Type`,
       MEASURE(`Opportunities Closed`) AS closed,
       MEASURE(`Opportunities Won`)    AS won,
       MEASURE(`Win Rate %`)           AS win_rate
FROM smbc_genie.metrics.mv_pipeline
GROUP BY ALL
HAVING MEASURE(`Opportunities Closed`) > 0
ORDER BY win_rate DESC;

-- QS2a: Storyline 2 - Kinokawa Precision clients whose latest next best product (rank 1) is Supply Chain Finance (band 1..9).
-- views: mv_next_best_product
-- rows: 2
SELECT `Client`, MEASURE(`Rank-1 Recommendations`) AS rank1_scf, MEASURE(`Max Propensity`) AS propensity
FROM smbc_genie.metrics.mv_next_best_product
WHERE `Is Latest Month` AND `Client Group` = 'Kinokawa Precision' AND `Product Family` = 'Supply Chain Finance' AND `Propensity Rank` = 1
GROUP BY ALL;

-- QS2b: Storyline 2 - the USD 120m supply chain finance opportunity created for Kinokawa in Aug-2026 from a signal (expect exactly 1).
-- views: mv_pipeline
-- rows: 1
SELECT `Client`, `Date` AS created_date, `Product`, `Stage`, `Source Signal Type`,
       MEASURE(`Opportunities`) AS opportunities, MEASURE(`Pipeline Amount USD`) AS open_amount_usd
FROM smbc_genie.metrics.mv_pipeline
WHERE `Client Group` = 'Kinokawa Precision' AND `Month` = DATE'2026-08-01' AND `Product` = 'Supply Chain Finance'
  AND `Source Type` = 'Signal'
GROUP BY ALL;

-- QS10: Storyline 10 - Banksia Renewables Partners - open green-loan opportunities (expect 2) and SLL_ELIGIBLE signals (expect 1).
-- views: mv_pipeline, mv_opportunity_signals
-- rows: 1
WITH p AS (
  SELECT MEASURE(`Open Opportunities`) AS open_green_loans
  FROM smbc_genie.metrics.mv_pipeline
  WHERE `Client Group` = 'Banksia Renewables Partners' AND `Product` = 'Green Loan'
), s AS (
  SELECT MEASURE(`Signals`) AS sll_signals
  FROM smbc_genie.metrics.mv_opportunity_signals
  WHERE `Client Group` = 'Banksia Renewables Partners' AND `Signal Type` = 'SLL_ELIGIBLE'
)
SELECT p.open_green_loans, s.sll_signals FROM p CROSS JOIN s;

-- QS5: Storyline 5 - the HK CASA trio's May-2026 deposit-surplus signals: shortest RM action lag (band 40..120 days).
-- views: mv_opportunity_signals
-- rows: 1
SELECT MEASURE(`Signals`)                  AS signals,
       MEASURE(`Shortest Action Lag Days`) AS shortest_action_lag_days,
       MEASURE(`Average Action Lag Days`)  AS average_action_lag_days
FROM smbc_genie.metrics.mv_opportunity_signals
WHERE `Signal Type` = 'DEPOSIT_SURPLUS' AND `Month` = DATE'2026-05-01'
  AND `Client` IN ('Hoshioka Electronics Group (Hong Kong) Ltd', 'Oedo Electronics (Hong Kong) (HK) Ltd', 'Kazami Devices (Hong Kong) Ltd');
