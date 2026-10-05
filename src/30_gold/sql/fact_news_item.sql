-- fact_news_item: external news items about APAC clients (silver.ext_news, golden-keyed), with the vendor
-- sentiment score (-1..1, synthetic per D18), score label (Negative <= -0.2 / Positive >= 0.2, the vendor's own
-- item cut-offs), the unified-feed signal code + event polarity, as-of window flags and the group's 90-day trend.
WITH n AS (
  SELECT * FROM ${catalog}.silver.ext_news
  WHERE published_date BETWEEN DATE'${history_start}' AND DATE'${as_of_date}' AND golden_client_id IS NOT NULL
), grp AS (     -- group sentiment in the last 90 days vs the 90 days before (as of 30-Sep-2026)
  SELECT client_group_id,
         avg(CASE WHEN published_date > date_sub(DATE'${as_of_date}', 90) THEN raw_sentiment END) AS s_last_90d,
         avg(CASE WHEN published_date > date_sub(DATE'${as_of_date}', 180)
                   AND published_date <= date_sub(DATE'${as_of_date}', 90) THEN raw_sentiment END) AS s_prior_90d,
         count(CASE WHEN published_date > date_sub(DATE'${as_of_date}', 90) THEN 1 END) AS n_last_90d
  FROM n WHERE client_group_id IS NOT NULL GROUP BY client_group_id
), coded AS (
  SELECT n.*,
         CASE n.topic WHEN 'Expansion' THEN 'NEWS_EXPANSION' WHEN 'M&A' THEN 'NEWS_MA' WHEN 'Earnings' THEN 'NEWS_EARNINGS'
                      WHEN 'Management Change' THEN 'NEWS_MANAGEMENT_CHANGE' WHEN 'Rating Action' THEN 'NEWS_RATING_ACTION'
                      WHEN 'Regulatory' THEN 'NEWS_REGULATORY' WHEN 'Litigation' THEN 'NEWS_LITIGATION'
                      WHEN 'ESG Controversy' THEN 'NEWS_ESG_CONTROVERSY'
                      WHEN 'Supply-Chain Disruption' THEN 'NEWS_SUPPLY_CHAIN_DISRUPTION' END AS signal_code,
         datediff(DATE'${as_of_date}', n.published_date) AS days_before_as_of
  FROM n
)
SELECT
  c.news_id,
  c.published_date,
  c.published_ts,
  trunc(c.published_date, 'MM')                                        AS month,
  dc.golden_client_sk,
  c.golden_client_id,
  c.client_group_id,
  c.company_id,
  c.issuer_id,
  c.outlet,
  c.source_type,
  c.headline,
  c.summary,
  c.topic,
  c.subtopic,
  c.signal_code,
  CASE WHEN c.raw_sentiment >= 0.2 THEN 'Opportunity' WHEN c.raw_sentiment <= -0.2 THEN 'Risk'
       ELSE st.polarity END                                             AS signal_polarity,
  c.raw_sentiment                                                       AS sentiment_score,
  CASE WHEN c.raw_sentiment <= -0.2 THEN 'Negative' WHEN c.raw_sentiment >= 0.2 THEN 'Positive'
       ELSE 'Neutral' END                                               AS sentiment_label,
  c.raw_sentiment <= -0.2                                               AS is_negative,
  c.raw_sentiment >= 0.2                                                AS is_positive,
  c.relevance,
  c.region,
  c.language,
  CASE WHEN dc.industry_subsector IN ('Oil, Gas & Coal', 'Mining') THEN 'Coal'
       WHEN dc.industry_subsector = 'Shipping' THEN 'Shipping'
       WHEN dc.industry_subsector IN ('Renewables', 'Power Generation') THEN 'Renewables'
       WHEN dc.industry_subsector IN ('Telecommunications', 'Infrastructure', 'Infrastructure Fund',
                                      'Semiconductors', 'Electronic Devices') THEN 'Data Centres & Digital'
       ELSE 'Other' END                                                 AS sector_theme,
  c.days_before_as_of,
  c.days_before_as_of < 30                                              AS is_last_30d,
  c.days_before_as_of < 90                                              AS is_last_90d,
  c.days_before_as_of BETWEEN 90 AND 179                                AS is_prior_90d,
  round(g.s_last_90d, 4)                                                AS group_sentiment_last_90d,
  round(g.s_prior_90d, 4)                                               AS group_sentiment_prior_90d,
  coalesce(g.s_last_90d < 0 AND (g.s_prior_90d IS NULL OR g.s_last_90d < g.s_prior_90d), false) AS is_group_negative_trend
FROM coded c
LEFT JOIN ${catalog}.gold.dim_signal_type st ON st.signal_code = c.signal_code
LEFT JOIN grp g ON g.client_group_id = c.client_group_id
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = c.golden_client_id
  AND c.published_date <= dc.valid_to
  AND (c.published_date >= dc.valid_from OR dc.version_no = 1)
