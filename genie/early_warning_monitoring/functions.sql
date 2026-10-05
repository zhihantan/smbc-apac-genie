-- UC SQL table functions of APAC Genie - Early Warning Monitoring (genie/README.md "functions.sql rules").
-- Deployed by src/50_genie/run_genie.py --create (CREATE OR REPLACE); the `-- test:` probes must return rows.
-- Name matching: a fragment of the legal-entity or client-group name, LIKE '%fragment%', case-insensitive.

-- test: SELECT * FROM smbc_genie.gold.fn_ews_timeline('Sunda Energi Nusantara', '2026-01-01')
-- test: SELECT * FROM smbc_genie.gold.fn_ews_client_snapshot('Hoshioka')
-- test: SELECT * FROM smbc_genie.gold.fn_watchlist_overdue_actions('')

CREATE OR REPLACE FUNCTION ${catalog}.gold.fn_ews_timeline(
  client_name STRING COMMENT 'Fragment of the client (legal entity) or client-group name, matched with LIKE %fragment% ignoring case, e.g. Sunda Energi Nusantara',
  from_month  STRING COMMENT 'First month to show as yyyy-MM-dd, e.g. 2026-01-01 for since January 2026 (DATE arguments are not supported by Genie)')
RETURNS TABLE (
  client              STRING COMMENT 'Client (legal entity)',
  client_group        STRING COMMENT 'Client group (global parent)',
  month               DATE   COMMENT 'Month (first day of the month)',
  month_end_band      STRING COMMENT 'Final EWS band on the last day of the month (Green < 40, Amber 40-69, Red >= 70)',
  month_end_score     DOUBLE COMMENT 'Composite EWS score 0-100 on the last day of the month',
  average_score       DOUBLE COMMENT 'Average daily EWS score over the month',
  signals_fired       STRING COMMENT 'EWS triggers fired in the month, comma-separated in alphabetical order (null = none)',
  signal_score_points DOUBLE COMMENT 'Composite-score points contributed by the signals of the month')
COMMENT 'EWS timeline of a client or client group, one row per legal entity and month since from_month: month-end band and score, average score and the EWS triggers (signals) fired in that month. Use for "walk me through the EWS score of <client> month by month" and "which signals fired when". Today is 30-Sep-2026.'
RETURN
  -- Parameters may only be referenced in the outermost query: a SQL UDF cannot use them inside CTEs over metric
  -- views (UNSUPPORTED_SUBQUERY_EXPRESSION_CATEGORY.ACCESSING_OUTER_QUERY_COLUMN_IS_NOT_ALLOWED).
  WITH scores AS (
    SELECT `Client` AS client, `Client Group` AS client_group, `Month` AS month,
           MEASURE(`Latest Score`) AS month_end_score, MEASURE(`Average Score`) AS average_score
    FROM ${catalog}.metrics.mv_ews_scores
    GROUP BY ALL
  ), bands AS (
    SELECT `Client` AS client, `Month` AS month, `Daily EWS Band` AS month_end_band
    FROM ${catalog}.metrics.mv_ews_scores
    WHERE `Is Month End`
    GROUP BY ALL
  ), sig AS (
    SELECT `Client` AS client, `Month` AS month, `Trigger` AS trigger_code, MEASURE(`Score Points Total`) AS points
    FROM ${catalog}.metrics.mv_ews_signals
    GROUP BY ALL
  ), sig_month AS (
    SELECT client, month, concat_ws(', ', sort_array(collect_set(trigger_code))) AS signals_fired, sum(points) AS signal_score_points
    FROM sig GROUP BY client, month
  )
  SELECT s.client, s.client_group, s.month, b.month_end_band, s.month_end_score, s.average_score,
         g.signals_fired, g.signal_score_points
  FROM scores s
  LEFT JOIN bands b ON b.client = s.client AND b.month = s.month
  LEFT JOIN sig_month g ON g.client = s.client AND g.month = s.month
  WHERE (lower(s.client) LIKE lower(concat('%', client_name, '%')) OR lower(s.client_group) LIKE lower(concat('%', client_name, '%')))
    AND s.month >= trunc(to_date(from_month), 'MM');

CREATE OR REPLACE FUNCTION ${catalog}.gold.fn_ews_client_snapshot(
  client_name STRING COMMENT 'Fragment of the client (legal entity) or client-group name, matched with LIKE %fragment% ignoring case')
RETURNS TABLE (
  client          STRING COMMENT 'Client (legal entity)',
  client_group    STRING COMMENT 'Client group (global parent)',
  coverage_office STRING COMMENT 'Coverage office / booking country (ISO-2)',
  primary_rm      STRING COMMENT 'Primary relationship manager',
  ews_band        STRING COMMENT 'Final EWS band today (after overrides)',
  model_band      STRING COMMENT 'EWS band computed by the model, before overrides',
  ews_score       DOUBLE COMMENT 'Composite EWS score 0-100 today',
  days_in_band    DOUBLE COMMENT 'Consecutive days in the current final band',
  top_triggers    STRING COMMENT 'Top three EWS triggers of the month',
  override_reason STRING COMMENT 'Reason of the steward override in force (null = none)',
  on_watchlist    BOOLEAN COMMENT 'On the credit watchlist today',
  exposure_usd    DOUBLE COMMENT 'Drawn lending exposure, USD')
COMMENT 'Early-warning position today (30-Sep-2026) of the legal entities matching a client or client-group name: final and model band, score, days in band, top three triggers, override reason, watchlist status and drawn exposure. Use for "why is <client> Red / Amber" and "EWS status of <client>".'
RETURN
  SELECT `Client`, `Client Group`, `Coverage Office`, `Primary RM`, `Daily EWS Band`, `Model Band`,
         MEASURE(`Latest Score`), MEASURE(`Average Days in Current Band`), `Top Triggers`, `Override Reason`,
         `Watchlisted Daily`, MEASURE(`Exposure Scored USD`)
  FROM ${catalog}.metrics.mv_ews_scores
  WHERE `Date` = DATE'2026-09-30'
    AND (lower(`Client`) LIKE lower(concat('%', client_name, '%')) OR lower(`Client Group`) LIKE lower(concat('%', client_name, '%')))
  GROUP BY ALL;

CREATE OR REPLACE FUNCTION ${catalog}.gold.fn_watchlist_overdue_actions(
  owner_name STRING COMMENT 'Fragment of the owner RM name, matched with LIKE %fragment% ignoring case; empty string = all owners')
RETURNS TABLE (
  owner        STRING COMMENT 'RM owning the watchlist action',
  client       STRING COMMENT 'Client (legal entity) on the watchlist',
  client_group STRING COMMENT 'Client group (global parent)',
  event_month  DATE   COMMENT 'Month of the watchlist event that created the action',
  event_type   STRING COMMENT 'Watchlist event: Added, Escalated or De-escalated',
  action_type  STRING COMMENT 'Action required: Watchlist credit review, Recovery / exit plan or Monitoring update',
  reason       STRING COMMENT 'Lead reason of the watchlist event',
  days_overdue DOUBLE COMMENT 'Days past the action due date at 30-Sep-2026')
COMMENT 'Overdue watchlist action plans at 30-Sep-2026, one row per action, for one owner RM or all owners (empty string): client, event, action, reason and days overdue. Use for "which watchlist actions are overdue for <RM>" and drill-down of overdue actions by owner.'
RETURN
  SELECT `Owner`, `Client`, `Client Group`, `Month`, `Event Type`, `Action Type`, `Reason`,
         MEASURE(`Average Action Days Overdue`)
  FROM ${catalog}.metrics.mv_watchlist
  WHERE `Record Type` = 'Event' AND `Action Status` = 'Overdue'
    AND lower(coalesce(`Owner`, '')) LIKE lower(concat('%', coalesce(owner_name, ''), '%'))
  GROUP BY ALL;
