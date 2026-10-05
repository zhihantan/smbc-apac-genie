-- Space 11: APAC Genie - Customer 360 Data Foundation (brief §5.11, PLAN §10). One MEASURE() query per must-answer
-- question (these become the Genie benchmarks; expected SQL names its output columns), plus the storyline checks
-- of the space (Q9-Q13). Views: mv_entity_resolution, mv_golden_record_coverage, mv_data_quality,
-- mv_delta_sharing_freshness, mv_er_exposure_impact. Run- and month-level measures are snapshots: filter a Run /
-- Month (latest run ER-20260930 = Is Latest Month). `-- rows:` lines are written by run_metrics.py.

-- Q1: Entity-resolution match rate and precision by source system for the latest run.
-- views: mv_entity_resolution
-- rows: 7
SELECT `Source System`,
       MEASURE(`Records In`)      AS records_in,
       MEASURE(`Matched Records`) AS matched_records,
       MEASURE(`Match Rate %`)    AS match_rate,
       MEASURE(`Fuzzy Share %`)   AS fuzzy_share,
       MEASURE(`Precision %`)     AS pairwise_precision,
       MEASURE(`Recall %`)        AS pairwise_recall
FROM smbc_genie.metrics.mv_entity_resolution
WHERE `Is Latest Month` AND `Source System` <> 'ALL'
GROUP BY ALL
ORDER BY `Source System`;

-- Q1b: ... and for the whole latest run (all source systems together).
-- views: mv_entity_resolution
-- rows: 1
SELECT MEASURE(`Records In`) AS records_in, MEASURE(`Match Rate %`) AS match_rate,
       MEASURE(`Precision %`) AS pairwise_precision, MEASURE(`Recall %`) AS pairwise_recall
FROM smbc_genie.metrics.mv_entity_resolution
WHERE `Is Latest Month`;

-- Q2: How many client records collapsed into how many golden records, run by run?
-- views: mv_entity_resolution
-- rows: 6
SELECT `Run`, `Rule Version`,
       MEASURE(`Records In`)                AS records_in,
       MEASURE(`Golden Records`)            AS golden_records,
       MEASURE(`Records per Golden Record`) AS records_per_golden_record,
       MEASURE(`Duplicates Merged`)         AS duplicates_merged
FROM smbc_genie.metrics.mv_entity_resolution
GROUP BY ALL
ORDER BY `Run`;

-- Q3: Steward queue aging: items open at 30-Sep-2026 by aging bucket.
-- views: mv_entity_resolution
-- rows: 3
SELECT `Aging Bucket`,
       MEASURE(`Steward Queue Open`) AS open_items,
       MEASURE(`Average Days Open`)  AS average_days_open
FROM smbc_genie.metrics.mv_entity_resolution
GROUP BY ALL
HAVING MEASURE(`Steward Queue Open`) > 0
ORDER BY average_days_open;

-- Q3b: ... and steward decisions per raising run.
-- views: mv_entity_resolution
-- rows: 6
SELECT `Run`, MEASURE(`Steward Decisions`) AS steward_decisions, MEASURE(`Steward Queue Open`) AS still_open
FROM smbc_genie.metrics.mv_entity_resolution
GROUP BY ALL
ORDER BY `Run`;

-- Q4: Golden clients missing from the KYC or credit sources, by relationship tier (today).
-- views: mv_golden_record_coverage
-- rows: 4
SELECT `Relationship Tier`,
       MEASURE(`Golden Clients`)              AS golden_clients,
       MEASURE(`Clients Missing KYC Data`)    AS missing_kyc,
       MEASURE(`Clients Missing Credit Data`) AS missing_credit
FROM smbc_genie.metrics.mv_golden_record_coverage
WHERE `Is Latest Month`
GROUP BY ALL
ORDER BY `Relationship Tier`;

-- Q5: DQ score by table and dimension this month vs last (the largest month-on-month changes).
-- views: mv_data_quality
-- rows: 10
SELECT `Table`, `DQ Dimension`,
       MEASURE(`DQ Score %`)                         AS dq_score_sep_2026,
       MEASURE(`DQ Score Prior Month %`)             AS dq_score_aug_2026,
       MEASURE(`DQ Score Change vs Prior Month pts`) AS change
FROM smbc_genie.metrics.mv_data_quality
WHERE `Month` = DATE'2026-09-01'
GROUP BY ALL
ORDER BY abs(change) DESC NULLS LAST
LIMIT 10;

-- Q5b: ... by layer and DQ dimension, this month vs last.
-- views: mv_data_quality
-- rows: 14
SELECT `Layer`, `DQ Dimension`,
       MEASURE(`DQ Score %`)             AS dq_score_sep_2026,
       MEASURE(`DQ Score Prior Month %`) AS dq_score_aug_2026,
       MEASURE(`Row Pass Rate %`)        AS row_pass_rate_sep_2026,
       MEASURE(`Tables Below Target`)    AS tables_below_95
FROM smbc_genie.metrics.mv_data_quality
WHERE `Month` = DATE'2026-09-01'
GROUP BY ALL
ORDER BY `Layer`, `DQ Dimension`;

-- Q6: Delta Share freshness from Japan over the last 30 days, and any stale tables.
-- views: mv_delta_sharing_freshness
-- rows: 5
SELECT `Shared Table`,
       MEASURE(`Average Lag Hours`)   AS avg_lag_hours,
       MEASURE(`Max Lag Hours`)       AS max_lag_hours,
       MEASURE(`Stale Table-Days`)    AS stale_days,
       MEASURE(`Freshness SLA Met %`) AS sla_met
FROM smbc_genie.metrics.mv_delta_sharing_freshness
WHERE `Provider Region` = 'JP' AND `Date` >= DATE'2026-09-01'
GROUP BY ALL
ORDER BY `Shared Table`;

-- Q6b: ... the August 2026 Japan share outage (storyline 13 context): stale days and the longest run per JP table.
-- views: mv_delta_sharing_freshness
-- rows: 5
SELECT `Shared Table`, MEASURE(`Stale Table-Days`) AS stale_days, MEASURE(`Max Lag Hours`) AS max_lag_hours,
       MEASURE(`Longest Stale Run Days`) AS longest_stale_run_days
FROM smbc_genie.metrics.mv_delta_sharing_freshness
WHERE `Provider Region` = 'JP' AND `Month` = DATE'2026-08-01'
GROUP BY ALL
ORDER BY stale_days DESC, `Shared Table`;

-- Q7: Which exposures changed by more than 20% after the March 2026 resolution (Meridian Agri Holdings)?
-- views: mv_er_exposure_impact
-- rows: 5
SELECT coalesce(`Client Group`, '(unattributed)') AS client_group, `Attribution Status`,
       MEASURE(`Exposure Before Resolution USD`) AS exposure_before_usd,
       MEASURE(`Exposure After Resolution USD`)  AS exposure_after_usd,
       MEASURE(`Resolution Change USD`)          AS resolution_change_usd,
       MEASURE(`Resolution Change %`)            AS resolution_change,
       MEASURE(`Groups Crossing Threshold`)      AS crossed_500m_threshold
FROM smbc_genie.metrics.mv_er_exposure_impact
WHERE `Month` = DATE'2026-03-01' AND `Material Resolution Change`
GROUP BY ALL
ORDER BY abs(resolution_change_usd) DESC;

-- Q8: Attribute completeness of the golden records of Strategic clients (today), with other tiers for comparison.
-- views: mv_golden_record_coverage
-- rows: 4
SELECT `Relationship Tier`,
       MEASURE(`Golden Clients`)                   AS golden_clients,
       MEASURE(`Average Attribute Completeness %`) AS avg_attribute_completeness,
       MEASURE(`Fully Complete Golden Records`)    AS fully_complete
FROM smbc_genie.metrics.mv_golden_record_coverage
WHERE `Is Latest Month`
GROUP BY ALL
ORDER BY `Relationship Tier`;

-- Q9: Storyline 3 (Meridian): golden records holding today's Meridian (Singapore) golden client's source records, per run.
-- views: mv_entity_resolution
-- rows: 2
SELECT `Run`, MEASURE(`Records In`) AS records, MEASURE(`Golden Records`) AS golden_records
FROM smbc_genie.metrics.mv_entity_resolution
WHERE `Client` LIKE 'Meridian Agri Holdings (Singapore)%' AND `Run` IN ('ER-20251231', 'ER-20260331')
GROUP BY ALL
ORDER BY `Run`;

-- Q10: Storyline 3 (Meridian): group exposure change on the March 2026 resolution (expect +35..+45%) and the USD 500m threshold.
-- views: mv_er_exposure_impact
-- rows: 1
SELECT `Client Group`,
       MEASURE(`Exposure Before Resolution USD`) AS exposure_before_usd,
       MEASURE(`Exposure After Resolution USD`)  AS exposure_after_usd,
       MEASURE(`Resolution Change %`)            AS resolution_change,
       MEASURE(`Attention Threshold USD`)        AS attention_threshold_usd,
       MEASURE(`Groups Crossing Threshold`)      AS crossed_threshold
FROM smbc_genie.metrics.mv_er_exposure_impact
WHERE `Client Group` = 'Meridian Agri Holdings' AND `Month` = DATE'2026-03-01'
GROUP BY ALL;

-- Q11: Storyline 13 (Delta Share staleness): every stale day of share_jp_parent_rating (expect exactly 11-15 Aug 2026, lag > 24h).
-- views: mv_delta_sharing_freshness
-- rows: 5
SELECT `Date`, MEASURE(`Max Lag Hours`) AS lag_hours, MEASURE(`Longest Stale Run Days`) AS consecutive_stale_days
FROM smbc_genie.metrics.mv_delta_sharing_freshness
WHERE `Shared Table` = 'share_jp_parent_rating' AND `Is Stale`
GROUP BY ALL
ORDER BY `Date`;

-- Q12: Storyline 12 (payments replay): the uniqueness rule on pay_payment_message (expect 3,966 de-duplicated on bronze) and the Jun-2026 dip.
-- views: mv_data_quality
-- rows: 2
SELECT `Layer`, `Rule`, MEASURE(`Rule Failed Rows`) AS failed_rows, MEASURE(`Rows De-duplicated`) AS rows_deduplicated
FROM smbc_genie.metrics.mv_data_quality
WHERE `Rule` = 'pay_payment_message:key_unique'
GROUP BY ALL
ORDER BY `Layer`;

-- Q12b: ... bronze payments uniqueness score by month, Apr-Sep 2026 (the Jun-2026 dip).
-- views: mv_data_quality
-- rows: 6
SELECT `Month`, MEASURE(`DQ Score %`) AS uniqueness_score, MEASURE(`Rows Failed`) AS rows_failed
FROM smbc_genie.metrics.mv_data_quality
WHERE `Table` = 'bronze.pay_payment_message' AND `DQ Dimension` = 'uniqueness' AND `Month` >= DATE'2026-04-01'
GROUP BY ALL
ORDER BY `Month`;

-- Q13: Storylines 7 and 13: the KYC-migration timeliness dip (Feb-May 2026) and the Japan share outage (Aug-2026) in the DQ scores.
-- views: mv_data_quality
-- rows: 9
SELECT `Incident`, `Table`, `DQ Dimension`, `Month`, MEASURE(`DQ Score %`) AS dq_score, MEASURE(`DQ Score Prior Month %`) AS prior_month
FROM smbc_genie.metrics.mv_data_quality
WHERE `Incident` IN ('kyc_migration_backlog', 'jp_share_outage')
GROUP BY ALL
ORDER BY `Incident`, `Table`, `Month`;
