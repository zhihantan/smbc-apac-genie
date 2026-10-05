-- Space 10: APAC Genie - Signals & Sentiment (brief §5.10, PLAN §10). One MEASURE() query per must-answer
-- question (these become the Genie benchmarks; expected SQL names its output columns), plus the storyline checks
-- of the space (Q9-Q11). Views: mv_news_sentiment, mv_internal_sentiment, mv_market_signals, mv_signal_feed.
-- As of 30-Sep-2026: "last 90 days" = Date >= DATE'2026-07-03'. `-- rows:` lines are written by run_metrics.py.

-- Q1: Everything about a named group (Kinokawa Precision) in the last 90 days: all signals by feed and type.
-- views: mv_signal_feed
-- rows: 9
SELECT `Source Feed`, `Signal Type`,
       MEASURE(`Signals`)                AS signals,
       MEASURE(`Risk Signals`)           AS risk_signals,
       MEASURE(`Opportunity Signals`)    AS opportunity_signals,
       MEASURE(`Unacknowledged Signals`) AS unacknowledged_signals
FROM smbc_genie.metrics.mv_signal_feed
WHERE `Client Group` LIKE '%Kinokawa Precision%' AND `Date` >= DATE'2026-07-03'
GROUP BY ALL
ORDER BY `Source Feed`, signals DESC;

-- Q1b: ... the same group's news and RM-note sentiment over the last 90 days and its listed parent today (D35 CTE join).
-- views: mv_news_sentiment, mv_internal_sentiment, mv_market_signals
-- rows: 1
WITH news AS (
  SELECT `Client Group`, MEASURE(`News Items`) AS news_items_90d, MEASURE(`Average Sentiment`) AS news_sentiment_90d
  FROM smbc_genie.metrics.mv_news_sentiment
  WHERE `Client Group` LIKE '%Kinokawa Precision%' AND `Date` >= DATE'2026-07-03' GROUP BY ALL
), notes AS (
  SELECT `Client Group`, MEASURE(`Notes`) AS rm_notes_90d, MEASURE(`Average Note Sentiment`) AS rm_note_sentiment_90d
  FROM smbc_genie.metrics.mv_internal_sentiment
  WHERE `Client Group` LIKE '%Kinokawa Precision%' AND `Date` >= DATE'2026-07-03' GROUP BY ALL
), mkt AS (
  SELECT `Client Group`, `External Rating`, `Outlook`,
         MEASURE(`Average Share Price Change 30D %`) AS share_price_change_30d,
         MEASURE(`Average CDS Change 30D bps`)       AS cds_change_30d_bps,
         MEASURE(`Groups at 52-Week Low`)            AS at_52_week_low,
         MEASURE(`APAC Exposure USD`)                AS apac_exposure_usd
  FROM smbc_genie.metrics.mv_market_signals
  WHERE `Client Group` LIKE '%Kinokawa Precision%' AND `Date` = DATE'2026-09-30' GROUP BY ALL
)
SELECT n.`Client Group`, n.news_items_90d, n.news_sentiment_90d, r.rm_notes_90d, r.rm_note_sentiment_90d,
       m.`External Rating` AS external_rating, m.`Outlook` AS outlook, m.share_price_change_30d, m.cds_change_30d_bps,
       m.at_52_week_low, m.apac_exposure_usd
FROM news n LEFT JOIN notes r ON r.`Client Group` = n.`Client Group` LEFT JOIN mkt m ON m.`Client Group` = n.`Client Group`;

-- Q2: Which groups' external sentiment turned negative while their RM notes stayed positive (blind spots, today)?
-- views: mv_internal_sentiment, mv_news_sentiment
-- rows: 8
WITH blind AS (
  SELECT `Client Group`,
         MEASURE(`Group News Sentiment Last 90D`) AS news_sentiment_last_90d,
         MEASURE(`Group Note Sentiment Last 90D`) AS rm_note_sentiment_last_90d,
         MEASURE(`Blind Spot Groups`)             AS is_blind_spot
  FROM smbc_genie.metrics.mv_internal_sentiment
  GROUP BY ALL
  HAVING MEASURE(`Blind Spot Groups`) > 0
), trend AS (
  SELECT `Client Group`, MEASURE(`Sentiment Change 90D`) AS news_sentiment_change_90d
  FROM smbc_genie.metrics.mv_news_sentiment GROUP BY ALL
)
SELECT b.`Client Group`, b.news_sentiment_last_90d, t.news_sentiment_change_90d, b.rm_note_sentiment_last_90d
FROM blind b LEFT JOIN trend t ON t.`Client Group` = b.`Client Group`
ORDER BY b.news_sentiment_last_90d;

-- Q3: What are the most common topics behind negative news sentiment, by industry (last 12 months)?
-- views: mv_news_sentiment
-- rows: 16
WITH t AS (
  SELECT `Industry`, `Topic`, MEASURE(`Negative Items`) AS negative_items, MEASURE(`Average Sentiment`) AS avg_sentiment
  FROM smbc_genie.metrics.mv_news_sentiment
  WHERE `Date` > DATE'2025-09-30' AND `Sentiment Polarity` = 'Negative'
  GROUP BY ALL
)
SELECT `Industry`, `Topic` AS top_negative_topic, negative_items, avg_sentiment,
       SUM(negative_items) OVER (PARTITION BY `Industry`) AS industry_negative_items
FROM t
QUALIFY row_number() OVER (PARTITION BY `Industry` ORDER BY negative_items DESC, `Topic`) = 1
ORDER BY industry_negative_items DESC;

-- Q4: Which listed parents are at 52-week lows with APAC exposure above USD 100m (today)?
-- views: mv_market_signals
-- rows: 1
SELECT `Client Group`, `Listed Parent`, `External Rating`, `Outlook`,
       MEASURE(`APAC Exposure USD`)                AS apac_exposure_usd,
       MEASURE(`Average Share Price Change 30D %`) AS share_price_change_30d
FROM smbc_genie.metrics.mv_market_signals
WHERE `Date` = DATE'2026-09-30' AND `At 52-Week Low` AND `APAC Exposure Above 100m`
GROUP BY ALL
ORDER BY apac_exposure_usd DESC;

-- Q5: Unacknowledged risk signals by RM over the last 90 days (top 10).
-- views: mv_signal_feed
-- rows: 10
SELECT coalesce(`Primary RM`, '(no RM coverage)') AS rm, `Primary RM Office` AS rm_office,
       MEASURE(`Unacknowledged Risk Signals`) AS unacknowledged_risk_signals,
       MEASURE(`Clients Signalled`)           AS clients_with_signals
FROM smbc_genie.metrics.mv_signal_feed
WHERE `Date` >= DATE'2026-07-03' AND `Polarity` = 'Risk' AND NOT `Acknowledged`
GROUP BY ALL
ORDER BY unacknowledged_risk_signals DESC
LIMIT 10;

-- Q6: Positive expansion signals in Australia and India with no pipeline (FY2026 to date).
-- views: mv_signal_feed
-- rows: 9
SELECT `Coverage Office`, `Client`, `Signal Type`, `Date`,
       MEASURE(`Signals`)          AS signals,
       MEASURE(`Average Strength`) AS strength
FROM smbc_genie.metrics.mv_signal_feed
WHERE `Signal Type` IN ('NEWS_EXPANSION', 'CAPEX_NEWS') AND `Polarity` = 'Opportunity'
  AND `Coverage Office` IN ('AU', 'IN') AND NOT `Has Pipeline` AND `Fiscal Year` = 'FY2026'
GROUP BY ALL
ORDER BY `Coverage Office`, `Date`;

-- Q7: Sentiment trend for the coal and shipping sectors by fiscal quarter (FY2024 onward).
-- views: mv_news_sentiment
-- rows: 20
SELECT `Sector Theme`, `Fiscal Quarter`,
       MEASURE(`News Items`)        AS news_items,
       MEASURE(`Average Sentiment`) AS avg_sentiment,
       MEASURE(`Negative Items`)    AS negative_items
FROM smbc_genie.metrics.mv_news_sentiment
WHERE `Sector Theme` IN ('Coal', 'Shipping') AND `Date` >= DATE'2024-04-01'
GROUP BY ALL
ORDER BY `Sector Theme`, `Fiscal Quarter`;

-- Q8: Rating-action signals from the JP-shared parent data (Japan Delta Share), FY2026 to date.
-- views: mv_signal_feed
-- rows: 42
SELECT `Date`, `Client Group`, `Signal Type`, MEASURE(`Signals`) AS signals, MEASURE(`Average Strength`) AS strength
FROM smbc_genie.metrics.mv_signal_feed
WHERE `Source Region` = 'JP' AND `Fiscal Year` = 'FY2026'
GROUP BY ALL
ORDER BY `Date`, `Client Group`;

-- Q9: Storyline 1 (Sunda): coal-regulation news in Jan-2026 - expect 8 items, sentiment between -0.7 and -0.5.
-- views: mv_news_sentiment
-- rows: 1
SELECT MEASURE(`News Items`) AS items, MEASURE(`Lowest Sentiment`) AS min_sentiment,
       MEASURE(`Highest Sentiment`) AS max_sentiment, MEASURE(`Average Sentiment`) AS avg_sentiment
FROM smbc_genie.metrics.mv_news_sentiment
WHERE `Client Group` = 'Sunda Energi Nusantara' AND `Subtopic` = 'Coal Regulation' AND `Month` = DATE'2026-01-01';

-- Q10: Storyline 10 (Banksia): average sentiment of the expansion news Apr-Jul 2026 - expect >= 0.6.
-- views: mv_news_sentiment
-- rows: 1
SELECT `Client Group`, MEASURE(`News Items`) AS items, MEASURE(`Average Sentiment`) AS avg_sentiment
FROM smbc_genie.metrics.mv_news_sentiment
WHERE `Client Group` = 'Banksia Renewables Partners' AND `Topic` = 'Expansion'
  AND `Date` BETWEEN DATE'2026-04-01' AND DATE'2026-07-31'
GROUP BY ALL;

-- Q11: Storyline 4 (Tanaka): the JP parent upgrade of 18-Jun-2026 in the unified feed (Japan share).
-- views: mv_signal_feed
-- rows: 1
SELECT `Date`, `Client Group`, `Client`, `Signal Type`, `Source Region`, MEASURE(`Signals`) AS signals
FROM smbc_genie.metrics.mv_signal_feed
WHERE `Client Group` = 'Tanaka Chemical' AND `Signal Type` LIKE 'PARENT_RATING_%'
GROUP BY ALL
ORDER BY `Date`;
