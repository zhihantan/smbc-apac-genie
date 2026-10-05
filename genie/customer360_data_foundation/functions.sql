-- APAC Genie - Customer 360 Data Foundation: UC SQL table functions (trusted assets, format: genie/README.md).
-- Owner: Genie content agent G4. Each reads the space's metric views with MEASURE(), "today" = fn_as_of_date()
-- (30-Sep-2026), never CURRENT_DATE(). Output columns match the must-answer queries in
-- metrics/_answers/customer360_data_foundation.sql (Q1, Q7, Q5), so a function call and the metric-view SQL agree.
-- Rules (README): arguments are STRING (cast inside) and are referenced only in the outermost query. Argument
-- names avoid the views' column names (identifiers are case-insensitive: `Month` = month).
-- test: SELECT * FROM smbc_genie.gold.fn_er_run_summary()
-- test: SELECT * FROM smbc_genie.gold.fn_er_run_summary('ER-20260331')
-- test: SELECT * FROM smbc_genie.gold.fn_er_exposure_changes('2026-03-31', '0.2')
-- test: SELECT * FROM smbc_genie.gold.fn_dq_score_changes('2026-09-01', '10')

CREATE OR REPLACE FUNCTION smbc_genie.gold.fn_er_run_summary(
  run_date STRING DEFAULT NULL COMMENT 'Entity-resolution run as its date (2025-06-30, 2025-09-30, 2025-12-31, 2026-03-31, 2026-06-30 or 2026-09-30 - any day of that month works) or its id (ER-20260331). NULL or omitted = the latest run ER-20260930'
)
RETURNS TABLE (
  source_system STRING COMMENT 'Identity source system: core_customer, crm_account, kyc_customer, credit_obligor, trade_party, tsy_counterparty or ext_company_master',
  records_in BIGINT COMMENT 'Source identity records in the run',
  matched_records BIGINT COMMENT 'Records linked to at least one other record (deterministic + fuzzy + steward)',
  match_rate DOUBLE COMMENT 'Matched records / records in (fraction, 0.99 = 99%)',
  fuzzy_share DOUBLE COMMENT 'Fuzzy matches / matched records (fraction)',
  pairwise_precision DOUBLE COMMENT 'Pairwise precision of the source vs the synthetic truth (fraction, gate >= 0.97)',
  pairwise_recall DOUBLE COMMENT 'Pairwise recall of the source vs the synthetic truth (fraction, gate >= 0.95)'
)
COMMENT 'Entity-resolution scorecard of one quarterly ER run by identity source system: records in, matched records, match rate, fuzzy share and pairwise precision / recall against the synthetic truth (one row per source system, the whole-run ALL row excluded). Default = the latest run ER-20260930. Use for "ER match rate and precision by source", "how did each source resolve in the <date> run". Reads metrics.mv_entity_resolution.'
RETURN
  SELECT `Source System`,
         MEASURE(`Records In`), MEASURE(`Matched Records`), MEASURE(`Match Rate %`), MEASURE(`Fuzzy Share %`),
         MEASURE(`Precision %`), MEASURE(`Recall %`)
  FROM smbc_genie.metrics.mv_entity_resolution
  WHERE `Month` = TRUNC(COALESCE(TRY_TO_DATE(run_date), TRY_TO_DATE(REPLACE(run_date, 'ER-', ''), 'yyyyMMdd'),
                                 smbc_genie.gold.fn_as_of_date()), 'MM')
    AND `Source System` <> 'ALL'
  GROUP BY ALL;

CREATE OR REPLACE FUNCTION smbc_genie.gold.fn_er_exposure_changes(
  run_date STRING COMMENT 'Entity-resolution run as its date (e.g. 2026-03-31 for the March 2026 resolution, the first v2 run - any day of that month works) or its id (ER-20260331)',
  min_abs_change STRING DEFAULT '0.2' COMMENT 'Minimum absolute exposure change caused by re-resolution, as text: 0.2, 20 or 20% all mean more than 20% (the ER_EXPOSURE_CHANGE threshold)'
)
RETURNS TABLE (
  client_group STRING COMMENT 'Client group, or (unattributed) for exposure the run could not attribute to a group',
  attribution_status STRING COMMENT 'Attributed or Unattributed',
  exposure_before_usd DOUBLE COMMENT 'The same run-date exposure attributed with the resolution of the previous run, USD',
  exposure_after_usd DOUBLE COMMENT 'Exposure under the resolution of this run (drawn lending + trade finance outstanding), USD',
  resolution_change_usd DOUBLE COMMENT 'Exposure after minus before re-resolution, USD (the effect of entity resolution alone)',
  resolution_change DOUBLE COMMENT 'Resolution change / exposure before (fraction, 0.38 = +38%)',
  crossed_500m_threshold BIGINT COMMENT '1 when re-resolution moved the group across the USD 500m single-borrower attention threshold'
)
COMMENT 'Group exposures that changed by more than a threshold (default 20%) through re-resolution in one quarterly ER run: exposure under the previous resolution vs under this run, the change in USD and %, and whether the group crossed the USD 500m attention threshold (e.g. Meridian Agri Holdings +38% at the March 2026 run). Use for "which exposures changed by more than 20% after the <date> resolution" across all groups - it lists every changed group and has no group filter, so for one named group (its exposure before and after, the attention threshold, whether it crossed) query mv_er_exposure_impact directly. Reads metrics.mv_er_exposure_impact.'
RETURN
  SELECT COALESCE(`Client Group`, '(unattributed)'), `Attribution Status`,
         MEASURE(`Exposure Before Resolution USD`), MEASURE(`Exposure After Resolution USD`),
         MEASURE(`Resolution Change USD`), MEASURE(`Resolution Change %`), MEASURE(`Groups Crossing Threshold`)
  FROM smbc_genie.metrics.mv_er_exposure_impact
  WHERE `Month` = TRUNC(COALESCE(TRY_TO_DATE(run_date), TRY_TO_DATE(REPLACE(run_date, 'ER-', ''), 'yyyyMMdd')), 'MM')
  GROUP BY ALL
  HAVING ABS(MEASURE(`Resolution Change %`)) >
         CASE WHEN CAST(REPLACE(COALESCE(min_abs_change, '0.2'), '%', '') AS DOUBLE) > 1
              THEN CAST(REPLACE(COALESCE(min_abs_change, '0.2'), '%', '') AS DOUBLE) / 100
              ELSE CAST(REPLACE(COALESCE(min_abs_change, '0.2'), '%', '') AS DOUBLE) END;

CREATE OR REPLACE FUNCTION smbc_genie.gold.fn_dq_score_changes(
  score_month STRING DEFAULT '2026-09-01' COMMENT 'Month to compare with the month before, as text (any day of it, e.g. 2026-09-01 = September 2026 = this month)',
  top_n STRING DEFAULT '10' COMMENT 'How many table x DQ-dimension pairs to return, largest absolute change first, as text (default 10)'
)
RETURNS TABLE (
  table_name STRING COMMENT 'Layer-qualified table, e.g. silver.kyc_review, bronze.pay_payment_message',
  dq_dimension STRING COMMENT 'DQ dimension: completeness, validity, uniqueness, timeliness or consistency',
  dq_score DOUBLE COMMENT 'DQ score of the month (fraction of rows passing, 0.978 = 97.8%)',
  dq_score_prior_month DOUBLE COMMENT 'DQ score of the previous month (fraction)',
  dq_score_change DOUBLE COMMENT 'Score minus prior-month score (fraction points, negative = deterioration)'
)
COMMENT 'Data-quality movers: the table x DQ-dimension pairs with the largest month-on-month change in DQ score (absolute, either direction) for a month vs the month before - default September vs August 2026, top 10. Use for "DQ score by table and dimension this month vs last", "biggest DQ changes". Reads metrics.mv_data_quality (DQ Score %, DQ Score Prior Month %).'
RETURN
  WITH s AS (
    SELECT `Month` AS m, `Table` AS tbl, `DQ Dimension` AS dim,
           MEASURE(`DQ Score %`) AS dq_score, MEASURE(`DQ Score Prior Month %`) AS dq_score_prior_month,
           MEASURE(`DQ Score Change vs Prior Month pts`) AS dq_score_change
    FROM smbc_genie.metrics.mv_data_quality
    GROUP BY ALL
  ), ranked AS (
    SELECT s.*, ROW_NUMBER() OVER (PARTITION BY m ORDER BY ABS(dq_score_change) DESC, tbl, dim) AS rn
    FROM s
    WHERE dq_score_change IS NOT NULL
  )
  SELECT tbl, dim, dq_score, dq_score_prior_month, dq_score_change
  FROM ranked
  WHERE m = TRUNC(TO_DATE(score_month), 'MM') AND rn <= CAST(top_n AS INT);
