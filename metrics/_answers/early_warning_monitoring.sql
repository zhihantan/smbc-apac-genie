-- Space 4: APAC Genie - Early Warning Monitoring (brief §5.4, PLAN §10).
-- One MEASURE() query per must-answer question (variants as Q<n>b ...); these become the Genie benchmarks. Written by
-- WP9b from the gold answers (scratch/wp8d_answers.sql). `-- rows:` lines are written by src/40_metrics/run_metrics.py.
-- Point-in-time measures (scores, bands, exposure, watchlist) need a Date / Month filter; today = DATE'2026-09-30'.

-- Q1: Which clients are Red today, what exposure do they carry, and what are the top three triggers for each?
-- views: mv_ews_scores
-- rows: 8
SELECT `Client`, `Top Triggers`,
       MEASURE(`Latest Score`)        AS ews_score,
       MEASURE(`Exposure in Red USD`) AS exposure_usd
FROM smbc_genie.metrics.mv_ews_scores
WHERE `Date` = DATE'2026-09-30' AND `Daily EWS Band` = 'Red'
GROUP BY ALL
ORDER BY exposure_usd DESC;

-- Q2: Walk me through Sunda Energi Nusantara's EWS score month by month since January 2026 and which signals fired when.
-- views: mv_ews_scores, mv_ews_signals (D35: one CTE per view, joined on Month)
-- rows: 9
WITH s AS (
  SELECT `Month`, MEASURE(`Average Score`) AS avg_score, MEASURE(`Latest Score`) AS month_end_score
  FROM smbc_genie.metrics.mv_ews_scores
  WHERE `Client` LIKE 'Sunda Energi Nusantara (Jakarta)%' AND `Date` >= DATE'2026-01-01'
  GROUP BY ALL
), g AS (
  SELECT `Month`, `Trigger`, `Preceded Downgrade 90D`, MEASURE(`Signals Fired`) AS fired
  FROM smbc_genie.metrics.mv_ews_signals
  WHERE `Client` LIKE 'Sunda Energi Nusantara (Jakarta)%' AND `Date` >= DATE'2026-01-01'
  GROUP BY ALL
), gm AS (
  SELECT `Month`, concat_ws(', ', sort_array(collect_list(`Trigger`))) AS signals_fired,
         bool_or(`Preceded Downgrade 90D`) AS preceded_downgrade_90d
  FROM g GROUP BY `Month`
)
SELECT s.`Month`, s.avg_score, s.month_end_score, gm.signals_fired, gm.preceded_downgrade_90d
FROM s LEFT JOIN gm ON gm.`Month` = s.`Month`
ORDER BY s.`Month`;

-- Q2b: Sunda Energi Nusantara - final EWS band at each month-end since January 2026.
-- views: mv_ews_scores
-- rows: 9
SELECT `Month`, `Daily EWS Band`, MEASURE(`Latest Score`) AS month_end_score
FROM smbc_genie.metrics.mv_ews_scores
WHERE `Client` LIKE 'Sunda Energi Nusantara (Jakarta)%' AND `Date` >= DATE'2026-01-01' AND `Is Month End`
GROUP BY ALL
ORDER BY `Month`;

-- Q3: Clients that moved from Green to Amber in the last 30 days, by coverage office and industry.
-- views: mv_ews_scores
-- rows: 10
SELECT `Coverage Office`, `Industry`, MEASURE(`Moved Green to Amber 30D`) AS clients_green_to_amber
FROM smbc_genie.metrics.mv_ews_scores
WHERE `Date` = DATE'2026-09-30'
GROUP BY ALL
HAVING MEASURE(`Moved Green to Amber 30D`) > 0
ORDER BY clients_green_to_amber DESC, `Coverage Office`, `Industry`;

-- Q4: Exposure in Amber and Red by industry sector at each month-end since April 2025.
-- views: mv_ews_scores
-- rows: 288
SELECT `Month`, `Industry`,
       MEASURE(`Exposure in Amber USD`) AS amber_exposure_usd,
       MEASURE(`Exposure in Red USD`)   AS red_exposure_usd
FROM smbc_genie.metrics.mv_ews_scores
WHERE `Month` >= DATE'2025-04-01'
GROUP BY ALL
ORDER BY `Month`, `Industry`;

-- Q5: Which signals most often precede a downgrade within 90 days? (trigger -> downgrade pairs, ranked by hit rate)
-- views: mv_ews_signals
-- rows: 12
SELECT `Trigger`,
       MEASURE(`Signals Preceding Downgrade`) AS signal_downgrade_pairs,
       MEASURE(`Signals Fired`)               AS signals_fired,
       MEASURE(`Downgrade Hit Rate %`)        AS downgrade_hit_rate
FROM smbc_genie.metrics.mv_ews_signals
GROUP BY ALL
ORDER BY downgrade_hit_rate DESC;

-- Q6: Clients with a deposit-outflow signal AND a utilisation spike in the same month this fiscal year.
-- views: mv_ews_signals
-- rows: 3
SELECT `Client`, `Month`, MEASURE(`Signals Fired`) AS outflow_and_utilisation_signals
FROM smbc_genie.metrics.mv_ews_signals
WHERE `Is Current FYTD` AND `Outflow and Utilisation Same Month` AND `Trigger Family` IN ('Deposit Outflow', 'Utilisation')
GROUP BY ALL
ORDER BY `Month`, `Client`;

-- Q7: EWS overrides this year and the reasons - how many cite parent support from Japan?
-- views: mv_ews_scores
-- rows: 4
SELECT `Override Direction`, `Override Reason`,
       MEASURE(`Override Events`)          AS override_events,
       MEASURE(`Parent Support Overrides`) AS citing_parent_support_jp,
       MEASURE(`Overrides on Stale Data`)  AS on_stale_jp_data
FROM smbc_genie.metrics.mv_ews_scores
WHERE `Fiscal Year` = 'FY2026'
GROUP BY ALL
HAVING MEASURE(`Override Events`) > 0
ORDER BY override_events DESC, `Override Direction`;

-- Q8: Watchlist actions overdue by owner.
-- views: mv_watchlist
-- rows: 22
SELECT `Owner`,
       MEASURE(`Actions Overdue`)             AS overdue_actions,
       MEASURE(`Average Action Days Overdue`) AS avg_days_overdue
FROM smbc_genie.metrics.mv_watchlist
WHERE `Record Type` = 'Event'
GROUP BY ALL
HAVING MEASURE(`Actions Overdue`) > 0
ORDER BY overdue_actions DESC, avg_days_overdue DESC;

-- Q9: 30+ DPD rate by booking country and segment, trend by month (cells with 30+ DPD exposure).
-- views: mv_delinquency
-- rows: 78
SELECT `Month`, `Coverage Office` AS booking_country, `Segment`,
       MEASURE(`30+ DPD Rate %`)         AS dpd30_rate,
       MEASURE(`Facilities 30 Plus DPD`) AS facilities_30_plus
FROM smbc_genie.metrics.mv_delinquency
GROUP BY ALL
HAVING MEASURE(`Facilities 30 Plus DPD`) > 0
ORDER BY `Month`, booking_country, `Segment`;

-- Q9b: 30+ DPD rate by booking country, latest month-end (Sep 2026).
-- views: mv_delinquency
-- rows: 13
SELECT `Coverage Office` AS booking_country, MEASURE(`30+ DPD Rate %`) AS dpd30_rate, MEASURE(`Drawn USD`) AS drawn_usd
FROM smbc_genie.metrics.mv_delinquency
WHERE `Is Latest Month`
GROUP BY ALL
ORDER BY dpd30_rate DESC;

-- Q10: Clients with covenant headroom below 10% that are NOT on the watchlist.
-- views: mv_ews_signals
-- rows: 3
SELECT `Client`, MEASURE(`Signals Fired`) AS headroom_low_signals
FROM smbc_genie.metrics.mv_ews_signals
WHERE `Trigger` = 'COV_HEADROOM_LOW' AND `Is Latest Month` AND NOT `Watchlist`
GROUP BY ALL
ORDER BY `Client`;

-- Q10b: Same question from the covenant tests (latest test, headroom 0-10%, client not on the watchlist today).
-- views: mv_facilities_covenants_collateral
-- rows: 3
SELECT `Client`, `Covenant Type`, MEASURE(`Min Covenant Headroom %`) AS headroom
FROM smbc_genie.metrics.mv_facilities_covenants_collateral
WHERE `Record Type` = 'Covenant Test' AND `Is Latest Test` AND `Headroom Bucket` = '0-10%' AND NOT `Watchlist`
GROUP BY ALL
ORDER BY headroom;
