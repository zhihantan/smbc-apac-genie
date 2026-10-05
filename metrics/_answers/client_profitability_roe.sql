-- Space 5: APAC Genie - Client Profitability & ROE (brief §5.5, PLAN §10).
-- One MEASURE() query per must-answer question; these become the Genie benchmarks (expected SQL names its
-- output columns). `-- rows:` lines are written by src/40_metrics/run_metrics.py.
-- WP9a wrote the mv_client_profitability questions (Q2, Q4, Q6, Q7, Q9, Q10); WP8c/WP9b added Q1, Q3, Q5, Q8 (mv_roe_waterfall,
-- mv_deal_pricing), the cross-sell half of Q2 (Q2b: a CTE on mv_next_best_product joined on Client, D35) and the
-- storyline checks QS*.

-- Q2: Which Japanese-corporate relationships are single-product lending with RoRWA below the 8% hurdle (= the configured RoRWA hurdle, D20)?
-- views: mv_client_profitability
-- rows: 38
SELECT `Client`, `Client Group`, `Coverage Office`, `Primary RM`,
       MEASURE(`RoRWA %`)             AS rorwa_latest_quarter,
       MEASURE(`RoRWA Hurdle %`)      AS rorwa_hurdle,
       MEASURE(`Average RWA USD`)     AS rwa_usd,
       MEASURE(`Total Revenue USD`)   AS revenue_sep_2026_usd
FROM smbc_genie.metrics.mv_client_profitability
WHERE `Is Latest Month` AND `Is Japanese Corporate` AND `Is Single-Product Lending` AND `Below RoRWA Hurdle`
GROUP BY ALL
ORDER BY rwa_usd DESC;

-- Q4: Revenue mix by product family for Strategic clients vs Core clients (this fiscal year, FY2026 to date).
-- views: mv_client_profitability
-- rows: 14
WITH r AS (
  SELECT `Relationship Tier`, `Product Family`, MEASURE(`Total Revenue USD`) AS revenue_usd
  FROM smbc_genie.metrics.mv_client_profitability
  WHERE `Fiscal Year` = 'FY2026' AND `Relationship Tier` IN ('Strategic', 'Core')
  GROUP BY ALL
)
SELECT `Relationship Tier`, `Product Family`, revenue_usd,
       revenue_usd / SUM(revenue_usd) OVER (PARTITION BY `Relationship Tier`) AS revenue_mix_share
FROM r
ORDER BY `Relationship Tier`, revenue_usd DESC;

-- Q6: Net contribution by coverage office and segment, H1 FY2026 vs H1 FY2025.
-- views: mv_client_profitability
-- rows: 75
WITH n AS (
  SELECT `Coverage Office`, `Segment`, `Fiscal Half`, MEASURE(`Net Contribution USD`) AS net_contribution_usd
  FROM smbc_genie.metrics.mv_client_profitability
  WHERE `Fiscal Half` IN ('FY2026-H1', 'FY2025-H1')
  GROUP BY ALL
)
SELECT `Coverage Office`, `Segment`,
       SUM(CASE WHEN `Fiscal Half` = 'FY2026-H1' THEN net_contribution_usd END) AS net_contribution_h1_fy2026_usd,
       SUM(CASE WHEN `Fiscal Half` = 'FY2025-H1' THEN net_contribution_usd END) AS net_contribution_h1_fy2025_usd,
       SUM(CASE WHEN `Fiscal Half` = 'FY2026-H1' THEN net_contribution_usd ELSE -net_contribution_usd END) AS change_usd
FROM n
GROUP BY ALL
ORDER BY `Coverage Office`, `Segment`;

-- Q7: Which groups consume the most capital relative to revenue (lowest revenue per unit of RWA), FY2025?
-- views: mv_client_profitability
-- rows: 20
SELECT * FROM (
  SELECT `Client Group`,
         MEASURE(`Revenue per RWA %`) AS revenue_per_rwa,
         MEASURE(`Average RWA USD`)   AS average_rwa_usd,
         MEASURE(`Total Revenue USD`) AS revenue_usd,
         MEASURE(`RoRWA %`)           AS rorwa
  FROM smbc_genie.metrics.mv_client_profitability
  WHERE `Fiscal Year` = 'FY2025'
  GROUP BY ALL
) WHERE average_rwa_usd > 0
ORDER BY revenue_per_rwa ASC
LIMIT 20;

-- Q9a: Products per client by segment, with the typical (median) RoRWA - latest month-end.
-- views: mv_client_profitability
-- rows: 6
SELECT `Segment`,
       MEASURE(`Products per Client`)   AS products_per_client,
       MEASURE(`Median Client RoRWA %`) AS median_client_rorwa,
       MEASURE(`Clients`)               AS clients
FROM smbc_genie.metrics.mv_client_profitability
WHERE `Is Latest Month`
GROUP BY ALL
ORDER BY products_per_client DESC;

-- Q9b: Relationship between products held (bucketed) and RoRWA - latest month-end.
-- views: mv_client_profitability
-- rows: 6
SELECT `Product Count Bucket`,
       MEASURE(`Clients`)                    AS clients,
       MEASURE(`Median Client RoRWA %`)      AS median_client_rorwa,
       MEASURE(`RoRWA %`)                    AS portfolio_rorwa,
       MEASURE(`Clients Below RoRWA Hurdle`) AS clients_below_hurdle
FROM smbc_genie.metrics.mv_client_profitability
WHERE `Is Latest Month`
GROUP BY ALL
ORDER BY `Product Count Bucket`;

-- Q10: RM-level profitability: revenue, RWA and RoRWA per RM in Hong Kong (RMs based in HK, FY2026 to date).
-- views: mv_client_profitability
-- rows: 9
SELECT `Primary RM`,
       MEASURE(`Total Revenue USD`) AS revenue_fytd_usd,
       MEASURE(`Average RWA USD`)   AS average_rwa_usd,
       MEASURE(`RoRWA %`)           AS rorwa,
       MEASURE(`Clients`)           AS clients
FROM smbc_genie.metrics.mv_client_profitability
WHERE `Primary RM Office` = 'HK' AND `Fiscal Year` = 'FY2026'
GROUP BY ALL
ORDER BY revenue_fytd_usd DESC;

-- Q1: Top and bottom 20 client groups by ROE in FY2025, with revenue, capital and credit cost.
-- views: mv_roe_waterfall
-- rows: 40
WITH g AS (
  SELECT `Client Group`,
         MEASURE(`ROE %`)                 AS roe,
         MEASURE(`Median Client ROE %`)   AS median_client_roe,
         MEASURE(`Revenue USD`)           AS revenue_usd,
         MEASURE(`Allocated Capital USD`) AS allocated_capital_usd,
         MEASURE(`Credit Cost USD`)       AS credit_cost_usd
  FROM smbc_genie.metrics.mv_roe_waterfall
  WHERE `Fiscal Year` = 'FY2025' AND `Client Group` IS NOT NULL
  GROUP BY ALL
  HAVING MEASURE(`Allocated Capital USD`) > 0
), r AS (
  SELECT g.*, rank() OVER (ORDER BY roe DESC) AS top_rank, rank() OVER (ORDER BY roe ASC) AS bottom_rank FROM g
)
SELECT CASE WHEN top_rank <= 20 THEN 'Top 20' ELSE 'Bottom 20' END AS list, `Client Group` AS client_group, roe, median_client_roe,
       revenue_usd, allocated_capital_usd, credit_cost_usd
FROM r
WHERE top_rank <= 20 OR bottom_rank <= 20
ORDER BY roe DESC;

-- Q1b: Same ranking for groups with at least USD 1m of allocated capital (deposit-rich groups with little capital have extreme ROE).
-- views: mv_roe_waterfall
-- rows: 40
WITH g AS (
  SELECT `Client Group`, MEASURE(`ROE %`) AS roe, MEASURE(`Median Client ROE %`) AS median_client_roe,
         MEASURE(`Revenue USD`) AS revenue_usd, MEASURE(`Allocated Capital USD`) AS allocated_capital_usd, MEASURE(`Credit Cost USD`) AS credit_cost_usd
  FROM smbc_genie.metrics.mv_roe_waterfall
  WHERE `Fiscal Year` = 'FY2025' AND `Client Group` IS NOT NULL
  GROUP BY ALL
  HAVING MEASURE(`Allocated Capital USD`) >= 1000000
), r AS (
  SELECT g.*, rank() OVER (ORDER BY roe DESC) AS top_rank, rank() OVER (ORDER BY roe ASC) AS bottom_rank FROM g
)
SELECT CASE WHEN top_rank <= 20 THEN 'Top 20' ELSE 'Bottom 20' END AS list, `Client Group` AS client_group, roe, median_client_roe,
       revenue_usd, allocated_capital_usd, credit_cost_usd
FROM r
WHERE top_rank <= 20 OR bottom_rank <= 20
ORDER BY roe DESC;

-- Q2b: Cross-sell that would lift the Japanese-corporate single-product lending relationships below the RoRWA hurdle (top cross-sell product from the latest next-best-product scores).
-- views: mv_client_profitability, mv_next_best_product
-- rows: 38
WITH b AS (
  SELECT `Client`, MEASURE(`RoRWA %`) AS rorwa_latest_quarter
  FROM smbc_genie.metrics.mv_client_profitability
  WHERE `Is Latest Month` AND `Is Japanese Corporate` AND `Is Single-Product Lending` AND `Below RoRWA Hurdle`
  GROUP BY ALL
), n AS (
  SELECT `Client`, `Product`, MEASURE(`Max Propensity`) AS propensity
  FROM smbc_genie.metrics.mv_next_best_product
  WHERE `Is Latest Month` AND `Is Cross-Sell`
  GROUP BY ALL
)
SELECT b.`Client` AS client, b.rorwa_latest_quarter, max_by(n.`Product`, n.propensity) AS top_cross_sell_product,
       max(n.propensity) AS propensity
FROM b LEFT JOIN n ON n.`Client` = b.`Client`
GROUP BY ALL
ORDER BY b.rorwa_latest_quarter;

-- Q3: ROE waterfall for the Hayashi Marine Logistics group, FY2024 vs FY2025.
-- views: mv_roe_waterfall
-- rows: 2
SELECT `Fiscal Year`,
       MEASURE(`Revenue USD`)           AS revenue_usd,
       MEASURE(`Opex USD`)              AS opex_usd,
       MEASURE(`Credit Cost USD`)       AS credit_cost_usd,
       MEASURE(`Pre-Tax Profit USD`)    AS pre_tax_profit_usd,
       MEASURE(`Tax USD`)               AS tax_usd,
       MEASURE(`Net Income USD`)        AS net_income_usd,
       MEASURE(`Allocated Capital USD`) AS allocated_capital_usd,
       MEASURE(`ROE %`)                 AS roe,
       MEASURE(`Hurdle Gap pts`)        AS gap_to_roe_hurdle
FROM smbc_genie.metrics.mv_roe_waterfall
WHERE `Client Group` = 'Hayashi Marine Logistics' AND `Fiscal Year` IN ('FY2024', 'FY2025')
GROUP BY ALL
ORDER BY `Fiscal Year`;

-- Q5: Deals approved below hurdle in the last 4 quarters - by exception reason and whether realised RAROC caught up (last 4 seasoned quarters = FY2025).
-- views: mv_deal_pricing
-- rows: 4
SELECT `Exception Reason`,
       MEASURE(`Deals`)                       AS deals,
       MEASURE(`Amount USD`)                  AS amount_usd,
       MEASURE(`Deals Caught Up`)             AS caught_up,
       MEASURE(`Caught-Up Rate %`)            AS caught_up_rate,
       MEASURE(`Average Origination RAROC %`) AS avg_origination_raroc,
       MEASURE(`Average Realised RAROC %`)    AS avg_realised_raroc
FROM smbc_genie.metrics.mv_deal_pricing
WHERE `Approved Below Hurdle` AND `Signed in Last 4 Seasoned Quarters`
GROUP BY ALL
ORDER BY deals DESC;

-- Q5b: Deals approved below hurdle in the rolling last 4 quarters to 30-Sep-2026 by exception reason and realised status (FY2026 deals not yet seasoned).
-- views: mv_deal_pricing
-- rows: 8
SELECT `Exception Reason`, `Realised Status`, MEASURE(`Deals`) AS deals, MEASURE(`Amount USD`) AS amount_usd
FROM smbc_genie.metrics.mv_deal_pricing
WHERE `Approved Below Hurdle` AND `Signed in Last 4 Quarters`
GROUP BY ALL
ORDER BY `Exception Reason`, `Realised Status`;

-- Q8: Clients where credit cost exceeded 30% of revenue in FY2025.
-- views: mv_roe_waterfall
-- rows: 41
SELECT `Client`, `Client Group`,
       MEASURE(`Revenue USD`)              AS revenue_usd,
       MEASURE(`Credit Cost USD`)          AS credit_cost_usd,
       MEASURE(`Credit Cost to Revenue %`) AS credit_cost_to_revenue
FROM smbc_genie.metrics.mv_roe_waterfall
WHERE `Fiscal Year` = 'FY2025' AND `Credit Cost Above 30%`
GROUP BY ALL
ORDER BY credit_cost_to_revenue DESC;

-- QS8a: Storyline 8 - FY2025 deals approved below hurdle on Relationship exceptions (expect 9) and how many caught up (expect 3).
-- views: mv_deal_pricing
-- rows: 1
SELECT MEASURE(`Deals`) AS relationship_exceptions, MEASURE(`Deals Caught Up`) AS caught_up
FROM smbc_genie.metrics.mv_deal_pricing
WHERE `Fiscal Year` = 'FY2025' AND `Exception Reason` = 'Relationship';

-- QS8b: Storyline 8 - Japanese-corporate lending-only relationships below the RoRWA hurdle in FY2026-Q2 (band 36..44).
-- views: mv_client_profitability
-- rows: 1
SELECT MEASURE(`Clients Below RoRWA Hurdle`) AS jc_lending_only_below_hurdle
FROM smbc_genie.metrics.mv_client_profitability
WHERE `Fiscal Quarter` = 'FY2026-Q2' AND `Is Japanese Corporate` AND `Is Lending-Only Relationship`;

-- QS8c: Storyline 8 - RAROC by segment in FY2026-Q2 (Sponsor & Structured Finance band 13%..15%).
-- views: mv_client_profitability
-- rows: 5
SELECT `Segment`, MEASURE(`RAROC %`) AS raroc, MEASURE(`RoRWA %`) AS rorwa, MEASURE(`Clients Below RoRWA Hurdle`) AS clients_below_hurdle
FROM smbc_genie.metrics.mv_client_profitability
WHERE `Fiscal Quarter` = 'FY2026-Q2' AND `Segment` IS NOT NULL
GROUP BY ALL
ORDER BY raroc DESC;

-- QS2: Storyline 2 - Kinokawa Precision group revenue H1 FY2026 vs H1 FY2025 (band -25%..-19%).
-- views: mv_client_profitability
-- rows: 1
SELECT MEASURE(`Total Revenue USD`) AS revenue_h1_fy2026_usd, MEASURE(`Revenue Prior Year USD`) AS revenue_h1_fy2025_usd,
       MEASURE(`Revenue YoY %`) AS revenue_yoy
FROM smbc_genie.metrics.mv_client_profitability
WHERE `Client Group` = 'Kinokawa Precision' AND `Fiscal Half` = 'FY2026-H1';
