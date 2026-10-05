-- Space 3: APAC Genie - Credit Memo & Financial Spreading (brief §5.3, PLAN §10).
-- One MEASURE() query per must-answer question (variants as Q<n>b ...); these become the Genie benchmarks. Written by
-- WP9b from the gold answers (scratch/wp8d_answers.sql). `-- rows:` lines are written by src/40_metrics/run_metrics.py.
-- Client names: LIKE on `Client`; "Sunda Energi Nusantara" has two entities - the memo pack uses the Jakarta lead.

-- Q1: Credit memo pack for Sunda Energi Nusantara - last 3 years of spread P&L and balance sheet (USD).
-- views: mv_financial_spreads
-- rows: 69
SELECT `Fiscal Year`, `Statement Type`, `Statement Line`, `Line Order`, MEASURE(`Amount USD`) AS amount_usd
FROM smbc_genie.metrics.mv_financial_spreads
WHERE `Client` LIKE 'Sunda Energi Nusantara (Jakarta)%' AND `Statement Type` IN ('P&L', 'Balance Sheet')
GROUP BY ALL
ORDER BY `Line Order`, `Fiscal Year`;

-- Q1b: Credit memo pack for Sunda Energi Nusantara - key ratios vs peer median and peer percentile, FY2023-FY2025.
-- views: mv_financial_ratios_vs_peers
-- rows: 15
SELECT `Ratio Name`, `Fiscal Year`,
       MEASURE(`Ratio Value`)                     AS ratio_value,
       MEASURE(`Peer Median`)                     AS peer_median,
       MEASURE(`Percentile Rank in Peer Group %`) AS peer_percentile_rank,
       MEASURE(`Clients Below Peer P25`)          AS worse_than_p25
FROM smbc_genie.metrics.mv_financial_ratios_vs_peers
WHERE `Client` LIKE 'Sunda Energi Nusantara (Jakarta)%'
  AND `Ratio Name` IN ('Net Debt/EBITDA', 'ICR', 'DSCR', 'Current Ratio', 'EBITDA Margin')
GROUP BY ALL
ORDER BY `Ratio Name`, `Fiscal Year`;

-- Q1c: Credit memo pack for Sunda Energi Nusantara - facilities with limit, drawn, margin and collateral (30-Sep-2026).
-- views: mv_facilities_covenants_collateral
-- rows: 4
SELECT `Facility`, `Facility Type`, `Facility Status`, `Security Type`, `Guarantor Type`,
       MEASURE(`Limit USD`)            AS limit_usd,
       MEASURE(`Drawn USD`)            AS drawn_usd,
       MEASURE(`Weighted Margin bps`)  AS margin_bps,
       MEASURE(`Collateral Value USD`) AS collateral_value_usd,
       MEASURE(`Max LTV %`)            AS max_ltv
FROM smbc_genie.metrics.mv_facilities_covenants_collateral
WHERE `Client` LIKE 'Sunda Energi Nusantara (Jakarta)%'
GROUP BY ALL
ORDER BY drawn_usd DESC;

-- Q1d: Credit memo pack for Sunda Energi Nusantara - covenants with headroom (leverage path since Dec-2025; latest test flagged).
-- views: mv_facilities_covenants_collateral
-- rows: 13
SELECT `Facility`, `Covenant Type`, `Date` AS test_date, `Is Latest Test`,
       MEASURE(`Covenant Actual`)         AS actual,
       MEASURE(`Covenant Threshold`)      AS threshold,
       MEASURE(`Min Covenant Headroom %`) AS headroom,
       MEASURE(`Breaches`)                AS breaches
FROM smbc_genie.metrics.mv_facilities_covenants_collateral
WHERE `Client` LIKE 'Sunda Energi Nusantara (Jakarta)%' AND `Record Type` = 'Covenant Test' AND `Date` >= DATE'2025-12-31'
GROUP BY ALL
ORDER BY `Facility`, `Covenant Type`, test_date;

-- Q1e: Credit memo pack for Sunda Energi Nusantara - current exposure (30-Sep-2026): drawn, EAD, RWA, ECL, arrears.
-- views: mv_delinquency
-- rows: 1
SELECT MEASURE(`Drawn USD`)           AS drawn_usd,
       MEASURE(`Limit USD`)           AS limit_usd,
       MEASURE(`EAD USD`)             AS ead_usd,
       MEASURE(`RWA USD`)             AS rwa_usd,
       MEASURE(`ECL USD`)             AS ecl_usd,
       MEASURE(`Stage 3 Exposure USD`) AS stage3_exposure_usd,
       MEASURE(`Overdue Amount USD`)  AS overdue_usd
FROM smbc_genie.metrics.mv_delinquency
WHERE `Client` LIKE 'Sunda Energi Nusantara (Jakarta)%' AND `Is Latest Month`;

-- Q2: Which clients breached or have < 10% headroom on a leverage covenant at the latest test?
-- views: mv_facilities_covenants_collateral
-- rows: 6
SELECT `Client`, `Facility`, `Headroom Bucket`, `Watchlist`,
       MEASURE(`Covenant Actual`)         AS actual,
       MEASURE(`Covenant Threshold`)      AS threshold,
       MEASURE(`Min Covenant Headroom %`) AS headroom
FROM smbc_genie.metrics.mv_facilities_covenants_collateral
WHERE `Record Type` = 'Covenant Test' AND `Is Latest Test` AND `Covenant Type` = 'Net Debt/EBITDA'
  AND `Headroom Bucket` IN ('Breached', '0-10%')
GROUP BY ALL
ORDER BY headroom;

-- Q3: Net Debt/EBITDA and ICR for all Indonesian mining clients vs their peer median, FY2023-FY2025 ("mining" incl. coal: Energy - Oil, Gas & Coal).
-- views: mv_financial_ratios_vs_peers
-- rows: 18
SELECT `Client`, `Subsector`, `Ratio Name`, `Fiscal Year`,
       MEASURE(`Ratio Value`)        AS ratio_value,
       MEASURE(`Peer Median`)        AS peer_median,
       MEASURE(`Gap to Peer Median`) AS gap_to_peer_median
FROM smbc_genie.metrics.mv_financial_ratios_vs_peers
WHERE `Coverage Office` = 'ID' AND `Subsector` IN ('Mining', 'Minerals', 'Natural Resources', 'Oil, Gas & Coal')
  AND `Ratio Name` IN ('Net Debt/EBITDA', 'ICR')
GROUP BY ALL
ORDER BY `Client`, `Ratio Name`, `Fiscal Year`;

-- Q4: Clients whose FY2025 financials are not yet spread, by analyst and days overdue.
-- views: mv_credit_review_workflow
-- rows: 43
SELECT `Analyst`,
       MEASURE(`Not Yet Spread`)       AS statements_not_spread,
       MEASURE(`Average Days Overdue`) AS avg_days_overdue,
       MEASURE(`Max Days Overdue`)     AS max_days_overdue
FROM smbc_genie.metrics.mv_credit_review_workflow
WHERE `Review Type` = 'Financial Spreading' AND `Review Fiscal Year` = 'FY2025' AND `Is Not Yet Spread`
GROUP BY ALL
ORDER BY statements_not_spread DESC, max_days_overdue DESC;

-- Q4b: FY2025 financials not yet spread - client list with analyst and days overdue.
-- views: mv_credit_review_workflow
-- rows: 128
SELECT `Client`, `Analyst`, `Status`, MEASURE(`Max Days Overdue`) AS days_overdue
FROM smbc_genie.metrics.mv_credit_review_workflow
WHERE `Review Type` = 'Financial Spreading' AND `Review Fiscal Year` = 'FY2025' AND `Is Not Yet Spread`
GROUP BY ALL
ORDER BY days_overdue DESC;

-- Q5: Annual reviews due in Q3 FY2026 by analyst, with the current status and days in preparation.
-- views: mv_credit_review_workflow
-- rows: 65
SELECT `Analyst`, `Status`,
       MEASURE(`Reviews Due`)                 AS reviews_due,
       MEASURE(`Average Days in Preparation`) AS avg_days_in_preparation
FROM smbc_genie.metrics.mv_credit_review_workflow
WHERE `Review Type` = 'Annual' AND `Fiscal Quarter` = 'FY2026-Q3'
GROUP BY ALL
ORDER BY `Analyst`, `Status`;

-- Q6: Collateral coverage - facilities with LTV above 70% or a valuation older than 24 months (active facilities).
-- views: mv_facilities_covenants_collateral
-- rows: 308
SELECT `Client`, `Facility`, `Collateral Type`,
       MEASURE(`Collateral Value USD`)     AS collateral_value_usd,
       MEASURE(`Max LTV %`)                AS ltv,
       MEASURE(`Max Valuation Age Months`) AS valuation_age_months
FROM smbc_genie.metrics.mv_facilities_covenants_collateral
WHERE `Record Type` = 'Collateral' AND `Facility Status` = 'Active'
GROUP BY ALL
HAVING MEASURE(`Collateral Needing Review`) > 0
ORDER BY ltv DESC;

-- Q6b: Collateral needing review on active facilities - summary by collateral type.
-- views: mv_facilities_covenants_collateral
-- rows: 5
SELECT `Collateral Type`,
       MEASURE(`Collateral Needing Review`) AS items_needing_review,
       MEASURE(`High LTV Items`)            AS ltv_over_70,
       MEASURE(`Stale Valuations`)          AS valuation_over_24m
FROM smbc_genie.metrics.mv_facilities_covenants_collateral
WHERE `Record Type` = 'Collateral' AND `Facility Status` = 'Active'
GROUP BY ALL
ORDER BY items_needing_review DESC;

-- Q7: Which Japanese-corporate subsidiaries rely on a parent guarantee or keepwell, and what is the parent's external rating (from the Japan share)?
-- views: mv_facilities_covenants_collateral
-- rows: 159
SELECT `Client`, `Client Group`, `Guarantor Type`, `Parent Rating`,
       MEASURE(`Facilities`) AS facilities,
       MEASURE(`Drawn USD`)  AS drawn_usd
FROM smbc_genie.metrics.mv_facilities_covenants_collateral
WHERE `Record Type` = 'Facility' AND `Facility Status` = 'Active' AND `Is Japanese Corporate`
  AND `Guarantor Type` IN ('Parent Guarantee', 'Keepwell')
GROUP BY ALL
ORDER BY drawn_usd DESC;

-- Q8: Working-capital cycle (DSO / DIO / DPO) trend for electronics-sector clients, FY2023-FY2025.
-- views: mv_financial_ratios_vs_peers
-- rows: 12
SELECT `Ratio Name`, `Fiscal Year`,
       MEASURE(`Clients`)     AS clients,
       MEASURE(`Ratio Value`) AS client_average_days,
       MEASURE(`Peer Median`) AS peer_median_days
FROM smbc_genie.metrics.mv_financial_ratios_vs_peers
WHERE `Subsector` IN ('Electronics', 'Electronic Devices') AND `Ratio Name` IN ('DSO', 'DIO', 'DPO', 'Cash Conversion Cycle')
GROUP BY ALL
ORDER BY `Ratio Name`, `Fiscal Year`;

-- Q9: Covenant waivers granted this fiscal year and the associated exposure.
-- views: mv_facilities_covenants_collateral
-- rows: 1
SELECT MEASURE(`Waivers`)                   AS waivers,
       MEASURE(`Exposure with Waivers USD`) AS exposure_with_waivers_usd,
       MEASURE(`Covenant Tests`)            AS covenant_tests_fy2026
FROM smbc_genie.metrics.mv_facilities_covenants_collateral
WHERE `Record Type` = 'Covenant Test' AND `Fiscal Year` = 'FY2026';

-- Q10: New-money requests approved in H1 FY2026 - amount, margin and time to approval by coverage office.
-- views: mv_credit_review_workflow
-- rows: 9
SELECT `Coverage Office`,
       MEASURE(`Approved`)                 AS approvals,
       MEASURE(`Requested Amount USD`)     AS amount_usd,
       MEASURE(`Weighted Margin bps`)      AS weighted_margin_bps,
       MEASURE(`Average Days to Approval`) AS avg_days_to_approval
FROM smbc_genie.metrics.mv_credit_review_workflow
WHERE `Review Type` = 'New Money' AND `Approval Fiscal Half` = 'FY2026-H1' AND `Outcome` IN ('Approved', 'Approved with Conditions')
GROUP BY ALL
ORDER BY amount_usd DESC;
