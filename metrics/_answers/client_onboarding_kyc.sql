-- Space 6: APAC Genie - Client Onboarding & KYC (brief §5.6, PLAN §10).
-- One MEASURE() query per must-answer question (variants as Q<n>b ...); these become the Genie benchmarks. Written by
-- WP9b from the gold answers (scratch/wp8d_answers.sql). `-- rows:` lines are written by src/40_metrics/run_metrics.py.
-- Requests of applicants that never went live have no client record: use Booking Country / Request Segment.

-- Q1: Median and P90 days to go live by booking country, by request month since April 2025.
-- views: mv_onboarding_cycle_time
-- rows: 102
SELECT `Booking Country`, `Month` AS request_month,
       MEASURE(`Live Cases`)          AS live_cases,
       MEASURE(`Median Days to Live`) AS median_days_to_live,
       MEASURE(`P90 Days to Live`)    AS p90_days_to_live
FROM smbc_genie.metrics.mv_onboarding_cycle_time
WHERE `Date` >= DATE'2025-04-01' AND `Case Status` = 'Live'
GROUP BY ALL
ORDER BY `Booking Country`, request_month;

-- Q1b: Median and P90 days to go live before vs during the KYC-migration backlog (storyline 7: ~21 -> ~38 days).
-- views: mv_onboarding_cycle_time
-- rows: 4
SELECT `KYC Migration Period`,
       MEASURE(`Live Cases`)          AS live_cases,
       MEASURE(`Median Days to Live`) AS median_days_to_live,
       MEASURE(`P90 Days to Live`)    AS p90_days_to_live
FROM smbc_genie.metrics.mv_onboarding_cycle_time
WHERE `Case Status` = 'Live'
GROUP BY ALL
ORDER BY median_days_to_live;

-- Q2: Which stage adds the most days for Financial Institution clients, and did it change after February 2026?
-- views: mv_onboarding_cycle_time
-- rows: 6
WITH st AS (
  SELECT `Stage Number`, `Stage`, `Is After KYC Migration`, MEASURE(`Average Days in Stage`) AS avg_days_in_stage
  FROM smbc_genie.metrics.mv_onboarding_cycle_time
  WHERE `Request Segment` = 'Financial Institution' AND `Stage Number` <= 6
  GROUP BY ALL
)
SELECT `Stage Number`, `Stage`,
       max(CASE WHEN NOT `Is After KYC Migration` THEN avg_days_in_stage END) AS avg_days_before_feb_2026,
       max(CASE WHEN `Is After KYC Migration` THEN avg_days_in_stage END)     AS avg_days_from_feb_2026
FROM st
GROUP BY `Stage Number`, `Stage`
ORDER BY `Stage Number`;

-- Q3: Open onboarding cases over SLA right now, by owner and blocker reason.
-- views: mv_onboarding_funnel
-- rows: 28
SELECT `Owner`, `Blocker Reason`, MEASURE(`Cases Over SLA`) AS cases_over_sla
FROM smbc_genie.metrics.mv_onboarding_funnel
WHERE `Case Status` = 'Open'
GROUP BY ALL
HAVING MEASURE(`Cases Over SLA`) > 0
ORDER BY cases_over_sla DESC, `Owner`;

-- Q4: Documents outstanding by type for cases stuck in KYC Docs more than 15 days.
-- views: mv_onboarding_funnel
-- rows: 11
SELECT `Document Type`, MEASURE(`Documents Outstanding`) AS outstanding_documents
FROM smbc_genie.metrics.mv_onboarding_funnel
WHERE `Stuck in KYC Docs 15D`
GROUP BY ALL
HAVING MEASURE(`Documents Outstanding`) > 0
ORDER BY outstanding_documents DESC;

-- Q5: Share of new onboardings that were matched to an existing client group.
-- views: mv_onboarding_funnel
-- rows: 3
WITH m AS (
  SELECT `Match Timing`, MEASURE(`Cases Opened`) AS cases, MEASURE(`Cases Live`) AS live_cases
  FROM smbc_genie.metrics.mv_onboarding_funnel
  GROUP BY ALL
)
SELECT `Match Timing`, cases, cases / SUM(cases) OVER () AS share_of_cases,
       live_cases, live_cases / SUM(live_cases) OVER () AS share_of_live_cases
FROM m
ORDER BY cases DESC;

-- Q5b: Cases where the group match was found only after account opening.
-- views: mv_onboarding_funnel
-- rows: 39
SELECT `Client`, `Client Group`, `Booking Country`, `Date` AS request_date,
       MEASURE(`Matched After Account Opening`) AS cases
FROM smbc_genie.metrics.mv_onboarding_funnel
WHERE `Match Timing` = 'After Account Opening'
GROUP BY ALL
HAVING MEASURE(`Matched After Account Opening`) > 0
ORDER BY request_date;

-- Q6: Periodic KYC reviews overdue by risk rating and country, trend since January 2026 (month-ends).
-- views: mv_kyc_health
-- rows: 138
SELECT `Month`, `Client Risk Rating`, `Booking Country`, MEASURE(`Overdue`) AS overdue_reviews
FROM smbc_genie.metrics.mv_kyc_health
WHERE `Review Type` = 'Periodic' AND `Month` >= DATE'2026-01-01'
GROUP BY ALL
HAVING MEASURE(`Overdue`) > 0
ORDER BY `Month`, `Client Risk Rating`, `Booking Country`;

-- Q6b: High-risk periodic reviews overdue at each month-end since April 2025 (storyline 7: May-2026 peak ~3x normal).
-- views: mv_kyc_health
-- rows: 18
SELECT `Month`, MEASURE(`High-Risk Overdue`) AS high_risk_overdue, MEASURE(`Overdue`) AS all_overdue
FROM smbc_genie.metrics.mv_kyc_health
WHERE `Review Type` = 'Periodic' AND `Month` >= DATE'2025-04-01'
GROUP BY ALL
ORDER BY `Month`;

-- Q7: Onboarding requests by segment and product requested this fiscal year vs last (same period Apr-Sep, and full FY2025).
-- views: mv_onboarding_funnel
-- rows: 32
WITH p AS (
  SELECT `Request Segment`, `Product Requested`, `Fiscal Half`, MEASURE(`Product Requests`) AS requests
  FROM smbc_genie.metrics.mv_onboarding_funnel
  WHERE `Fiscal Year` IN ('FY2025', 'FY2026')
  GROUP BY ALL
)
SELECT `Request Segment`, `Product Requested`,
       sum(CASE WHEN `Fiscal Half` = 'FY2026-H1' THEN requests ELSE 0 END)           AS fy2026_h1_requests,
       sum(CASE WHEN `Fiscal Half` = 'FY2025-H1' THEN requests ELSE 0 END)           AS fy2025_h1_requests,
       sum(CASE WHEN `Fiscal Half` LIKE 'FY2025-%' THEN requests ELSE 0 END)         AS fy2025_full_year_requests
FROM p
WHERE `Product Requested` IS NOT NULL
GROUP BY ALL
ORDER BY fy2026_h1_requests DESC, fy2025_full_year_requests DESC;

-- Q7b: Onboarding requests (cases) by segment and primary product, FY2026 to date vs the same period of FY2025.
-- views: mv_onboarding_funnel
-- rows: 12
SELECT `Request Segment`, `Primary Product`, `Fiscal Half`, MEASURE(`Cases Opened`) AS requests
FROM smbc_genie.metrics.mv_onboarding_funnel
WHERE `Fiscal Half` IN ('FY2025-H1', 'FY2026-H1')
GROUP BY ALL
ORDER BY `Request Segment`, `Primary Product`, `Fiscal Half`;

-- Q8: Clients onboarded in FY2026 that have not transacted within 60 days of go-live.
-- views: mv_onboarding_funnel
-- rows: 4
SELECT `Client`, `Booking Country`,
       MEASURE(`Not Transacted Within 60D`)          AS not_transacted_within_60d,
       MEASURE(`Average First Transaction Lag Days`) AS days_to_first_transaction
FROM smbc_genie.metrics.mv_onboarding_funnel
WHERE `Go-Live Fiscal Year` = 'FY2026'
GROUP BY ALL
HAVING MEASURE(`Not Transacted Within 60D`) > 0
ORDER BY days_to_first_transaction DESC;

-- Q9: Screening true-match rate and average resolution hours by list.
-- views: mv_kyc_health
-- rows: 8
SELECT `List`,
       MEASURE(`Screening Alerts`)            AS alerts,
       MEASURE(`True Matches`)                AS true_matches,
       MEASURE(`Screening True Match Rate %`) AS true_match_rate,
       MEASURE(`Average Resolution Hours`)    AS avg_resolution_hours
FROM smbc_genie.metrics.mv_kyc_health
WHERE `Record Type` = 'Screening'
GROUP BY ALL
ORDER BY true_match_rate DESC;

-- Q10: Post-onboarding satisfaction by country.
-- views: mv_onboarding_funnel
-- rows: 13
SELECT `Booking Country`,
       MEASURE(`Survey Responses`)     AS responses,
       MEASURE(`Average Satisfaction`) AS avg_satisfaction,
       MEASURE(`Negative Responses`)   AS negative_responses
FROM smbc_genie.metrics.mv_onboarding_funnel
GROUP BY ALL
HAVING MEASURE(`Survey Responses`) > 0
ORDER BY avg_satisfaction;

-- Q10b: The most common negative comment themes after onboarding.
-- views: mv_onboarding_funnel
-- rows: 3
SELECT `Feedback Theme`, MEASURE(`Negative Responses`) AS negative_responses
FROM smbc_genie.metrics.mv_onboarding_funnel
GROUP BY ALL
HAVING MEASURE(`Negative Responses`) > 0
ORDER BY negative_responses DESC;
