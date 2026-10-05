-- APAC Genie - Signals & Sentiment: UC SQL table functions (trusted assets, format: genie/README.md).
-- Owner: Genie content agent G4. Each reads the space's metric views with MEASURE(), "today" = fn_as_of_date()
-- (30-Sep-2026), never CURRENT_DATE(). Output columns match the must-answer queries in
-- metrics/_answers/signals_sentiment.sql (Q1, Q1b, Q2), so a function call and the metric-view SQL agree.
-- Rules (README): arguments are STRING (cast inside) and are referenced only in the outermost query - the
-- metric-view CTEs are computed unfiltered (news / notes by group and day, re-aggregated exactly outside).
-- test: SELECT * FROM smbc_genie.gold.fn_group_signal_digest('Kinokawa Precision', '90')
-- test: SELECT * FROM smbc_genie.gold.fn_group_sentiment_snapshot('Kinokawa Precision', '90')
-- test: SELECT * FROM smbc_genie.gold.fn_sentiment_blind_spots()

CREATE OR REPLACE FUNCTION smbc_genie.gold.fn_group_signal_digest(
  client_group STRING COMMENT 'Client group name, matched case-insensitively as a substring (pass the full group name, e.g. Kinokawa Precision - a fragment such as Kinokawa also matches Kinokawa Heavy Industries)',
  days STRING DEFAULT '90' COMMENT 'Look-back window in days ending today 30-Sep-2026, as text: 90 = 3-Jul-2026 to 30-Sep-2026'
)
RETURNS TABLE (
  source_feed STRING COMMENT 'Feed the signals came from: crm_signal, ews_signal, news, market, ext_rating, jp_parent_rating or rm_note',
  signal_type STRING COMMENT 'Signal code, e.g. TRADE_CORRIDOR_GROWTH, GRADE_DOWNGRADE, NEWS_EXPANSION, PARENT_RATING_UPGRADE, RM_NOTE_POSITIVE',
  signals BIGINT COMMENT 'Signal events of the group in the window',
  risk_signals BIGINT COMMENT 'Signals with polarity Risk (early-warning side)',
  opportunity_signals BIGINT COMMENT 'Signals with polarity Opportunity (sales side)',
  unacknowledged_signals BIGINT COMMENT 'Signals no RM has acknowledged'
)
COMMENT 'Signal digest of one client group over the last N days (default 90) to 30-Sep-2026: every signal of the unified feed (CRM opportunity signals, EWS triggers, news, market moves, agency and JP-parent rating actions, RM notes) counted by source feed and signal type, with risk, opportunity and unacknowledged counts. Use for "everything about / what is happening around <group> in the last 90 days". Reads metrics.mv_signal_feed.'
RETURN
  SELECT `Source Feed`, `Signal Type`,
         MEASURE(`Signals`), MEASURE(`Risk Signals`), MEASURE(`Opportunity Signals`), MEASURE(`Unacknowledged Signals`)
  FROM smbc_genie.metrics.mv_signal_feed
  WHERE lower(`Client Group`) LIKE lower(CONCAT('%', client_group, '%'))
    AND `Date` BETWEEN DATE_SUB(smbc_genie.gold.fn_as_of_date(), CAST(days AS INT) - 1) AND smbc_genie.gold.fn_as_of_date()
  GROUP BY ALL;

CREATE OR REPLACE FUNCTION smbc_genie.gold.fn_group_sentiment_snapshot(
  client_group STRING COMMENT 'Client group name, matched case-insensitively as a substring (pass the full group name, e.g. Kinokawa Precision)',
  days STRING DEFAULT '90' COMMENT 'Look-back window in days ending today 30-Sep-2026 for the news and RM-note figures, as text: 90 = 3-Jul-2026 to 30-Sep-2026'
)
RETURNS TABLE (
  client_group STRING COMMENT 'Client group (short brand)',
  news_items BIGINT COMMENT 'News items on the group in the window',
  news_sentiment DOUBLE COMMENT 'Average news sentiment in the window, -1..+1 (<= -0.2 negative, >= 0.2 positive)',
  rm_notes BIGINT COMMENT 'RM call notes on the group in the window',
  rm_note_sentiment DOUBLE COMMENT 'Average RM-note sentiment in the window, -1..+1',
  external_rating STRING COMMENT 'Agency rating of the listed parent in force today (null = not listed)',
  outlook STRING COMMENT 'Rating outlook in force today: Stable, Positive or Negative',
  share_price_change_30d DOUBLE COMMENT 'Listed parent share-price change over the last 30 days, today (fraction, -0.05 = -5%)',
  cds_change_30d_bps DOUBLE COMMENT 'Change of the parent CDS spread over the last 30 days, bps (positive = widening)',
  at_52_week_low BIGINT COMMENT '1 when the listed parent closed at its 52-week low today, else 0',
  apac_exposure_usd DOUBLE COMMENT 'Group APAC credit exposure (drawn lending + trade finance outstanding), USD, today'
)
COMMENT 'One-row sentiment and market snapshot of a client group: news items and average news sentiment plus RM notes and average RM-note sentiment over the last N days (default 90) to 30-Sep-2026, and the listed parent today (agency rating, outlook, 30-day share-price and CDS change, 52-week-low flag, APAC exposure). Use for "news, RM notes and market picture of <group>". Joins CTEs over metrics.mv_news_sentiment, mv_internal_sentiment and mv_market_signals (D35) - market columns are null for unlisted groups.'
RETURN
  WITH news AS (
    SELECT `Client Group` AS grp, `Date` AS d, MEASURE(`News Items`) AS n, MEASURE(`Average Sentiment`) AS s
    FROM smbc_genie.metrics.mv_news_sentiment
    GROUP BY ALL
  ), notes AS (
    SELECT `Client Group` AS grp, `Date` AS d, MEASURE(`Notes`) AS n, MEASURE(`Average Note Sentiment`) AS s
    FROM smbc_genie.metrics.mv_internal_sentiment
    GROUP BY ALL
  ), mkt AS (
    SELECT `Client Group` AS grp, `External Rating` AS external_rating, `Outlook` AS outlook,
           MEASURE(`Average Share Price Change 30D %`) AS share_price_change_30d,
           MEASURE(`Average CDS Change 30D bps`) AS cds_change_30d_bps,
           MEASURE(`Groups at 52-Week Low`) AS at_52_week_low,
           MEASURE(`APAC Exposure USD`) AS apac_exposure_usd
    FROM smbc_genie.metrics.mv_market_signals
    WHERE `Date` = smbc_genie.gold.fn_as_of_date()
    GROUP BY ALL
  )
  SELECT nw.grp, nw.news_items, nw.news_sentiment, nt.rm_notes, nt.rm_note_sentiment,
         m.external_rating, m.outlook, m.share_price_change_30d, m.cds_change_30d_bps, m.at_52_week_low,
         m.apac_exposure_usd
  FROM (SELECT grp, SUM(n) AS news_items, SUM(n * s) / SUM(n) AS news_sentiment
        FROM news
        WHERE lower(grp) LIKE lower(CONCAT('%', client_group, '%'))
          AND d BETWEEN DATE_SUB(smbc_genie.gold.fn_as_of_date(), CAST(days AS INT) - 1) AND smbc_genie.gold.fn_as_of_date()
        GROUP BY grp) nw
  LEFT JOIN (SELECT grp, SUM(n) AS rm_notes, SUM(n * s) / SUM(n) AS rm_note_sentiment
             FROM notes
             WHERE lower(grp) LIKE lower(CONCAT('%', client_group, '%'))
               AND d BETWEEN DATE_SUB(smbc_genie.gold.fn_as_of_date(), CAST(days AS INT) - 1) AND smbc_genie.gold.fn_as_of_date()
             GROUP BY grp) nt ON nt.grp = nw.grp
  LEFT JOIN mkt m ON m.grp = nw.grp;

CREATE OR REPLACE FUNCTION smbc_genie.gold.fn_sentiment_blind_spots()
RETURNS TABLE (
  client_group STRING COMMENT 'Client group (short brand) flagged as a blind spot',
  news_sentiment_last_90d DOUBLE COMMENT 'Average news sentiment of the group, 3-Jul to 30-Sep-2026 (<= -0.2 = negative)',
  news_sentiment_change_90d DOUBLE COMMENT 'News sentiment of the last 90 days minus the prior 90 days (negative = deteriorating, null without prior news)',
  rm_note_sentiment_last_90d DOUBLE COMMENT 'Average RM-note sentiment of the group, 3-Jul to 30-Sep-2026 (>= 0.2 = positive)'
)
COMMENT 'Sentiment blind spots as of 30-Sep-2026: client groups whose external news sentiment turned negative over the last 90 days (average <= -0.2, prior 90 days not) while their RM call notes stayed positive (average >= 0.2), with the 90-day news-sentiment change. Use for "blind spots", "news turned negative but RM notes still positive", "where are RMs missing bad news". Reads metrics.mv_internal_sentiment (Blind Spot Groups) and mv_news_sentiment (Sentiment Change 90D).'
RETURN
  WITH blind AS (
    SELECT `Client Group` AS grp,
           MEASURE(`Group News Sentiment Last 90D`) AS news_sentiment_last_90d,
           MEASURE(`Group Note Sentiment Last 90D`) AS rm_note_sentiment_last_90d
    FROM smbc_genie.metrics.mv_internal_sentiment
    GROUP BY ALL
    HAVING MEASURE(`Blind Spot Groups`) > 0
  ), trend AS (
    SELECT `Client Group` AS grp, MEASURE(`Sentiment Change 90D`) AS news_sentiment_change_90d
    FROM smbc_genie.metrics.mv_news_sentiment
    GROUP BY ALL
  )
  SELECT b.grp, b.news_sentiment_last_90d, t.news_sentiment_change_90d, b.rm_note_sentiment_last_90d
  FROM blind b LEFT JOIN trend t ON t.grp = b.grp;
