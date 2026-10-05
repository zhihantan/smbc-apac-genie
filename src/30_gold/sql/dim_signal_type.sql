-- dim_signal_type: CRM signal codes (silver.crm_signal), EWS triggers (silver.ews_trigger_catalog) and the
-- signal types the unified feed (fact_signal_event) derives in gold from news / market / rating / RM notes.
WITH crm AS (
  SELECT signal_code,
         max_by(signal_name, n) AS signal_name, max_by(signal_category, n) AS signal_category,
         max_by(polarity, n) AS polarity, max_by(source_quadrant, n) AS source_quadrant,
         max_by(detection_engine, n) AS engine, sum(n) AS n, sum(strength_sum) / sum(n) AS avg_strength
  FROM (SELECT signal_code, signal_name, signal_category, polarity, source_quadrant, detection_engine,
               count(*) AS n, sum(strength) AS strength_sum
        FROM ${catalog}.silver.crm_signal WHERE signal_code IS NOT NULL AND detected_date <= DATE'${as_of_date}'
        GROUP BY ALL)
  GROUP BY signal_code
), ews AS (
  SELECT c.trigger_code, c.trigger_name, c.category, c.default_severity, c.threshold_desc, c.description,
         count(s.trigger_code) AS n
  FROM ${catalog}.silver.ews_trigger_catalog c
  LEFT JOIN ${catalog}.silver.ews_signal s ON s.trigger_code = c.trigger_code AND s.signal_month <= DATE'${as_of_date}'
  GROUP BY ALL
), derived (signal_code, signal_name, signal_category, polarity, source_quadrant, source_system, consumed_by, default_weight, description) AS (
  VALUES
  ('NEWS_EXPANSION', 'Expansion / capex news', 'News', 'Opportunity', 'External Qualitative', 'news_vendor', 'Opportunity Identification', 0.6D, 'News item with topic Expansion about the client or its group.'),
  ('NEWS_MA', 'M&A news', 'News', 'Opportunity', 'External Qualitative', 'news_vendor', 'Both', 0.6D, 'News item with topic M&A about the client or its group.'),
  ('NEWS_EARNINGS', 'Earnings news', 'News', 'Neutral', 'External Qualitative', 'news_vendor', 'Both', 0.3D, 'Earnings news; direction from the item sentiment.'),
  ('NEWS_MANAGEMENT_CHANGE', 'Management change news', 'News', 'Neutral', 'External Qualitative', 'news_vendor', 'Early Warning', 0.4D, 'Key management change reported in the news.'),
  ('NEWS_RATING_ACTION', 'Rating action news', 'News', 'Neutral', 'External Qualitative', 'news_vendor', 'Early Warning', 0.4D, 'News of a rating action on the client or its group.'),
  ('NEWS_REGULATORY', 'Regulatory news', 'News', 'Risk', 'External Qualitative', 'news_vendor', 'Early Warning', 0.5D, 'Regulatory change or action affecting the client (e.g. coal regulation).'),
  ('NEWS_LITIGATION', 'Litigation news', 'News', 'Risk', 'External Qualitative', 'news_vendor', 'Early Warning', 0.7D, 'Litigation involving the client or its group.'),
  ('NEWS_ESG_CONTROVERSY', 'ESG controversy news', 'News', 'Risk', 'External Qualitative', 'news_vendor', 'Early Warning', 0.7D, 'Environmental, social or governance controversy.'),
  ('NEWS_SUPPLY_CHAIN_DISRUPTION', 'Supply-chain disruption news', 'News', 'Risk', 'External Qualitative', 'news_vendor', 'Early Warning', 0.6D, 'Supply-chain disruption affecting the client.'),
  ('MARKET_52W_LOW', 'Share price at 52-week low', 'Market', 'Risk', 'External Quantitative', 'market_data', 'Early Warning', 0.6D, 'Listed parent closes at or below its 52-week low.'),
  ('MARKET_PRICE_DROP_30D', 'Share price down 20% in 30 days', 'Market', 'Risk', 'External Quantitative', 'market_data', 'Early Warning', 0.6D, 'Listed parent share price falls 20% or more over 30 days.'),
  ('MARKET_CDS_WIDENING', 'CDS spread widening', 'Market', 'Risk', 'External Quantitative', 'market_data', 'Early Warning', 0.6D, 'CDS-like spread widens 50 bps or more over 30 days.'),
  ('EXT_RATING_DOWNGRADE', 'External rating downgrade', 'Rating', 'Risk', 'External Quantitative', 'rating_agency', 'Early Warning', 0.8D, 'Agency downgrade of the group issuer.'),
  ('EXT_RATING_UPGRADE', 'External rating upgrade', 'Rating', 'Opportunity', 'External Quantitative', 'rating_agency', 'Both', 0.5D, 'Agency upgrade of the group issuer.'),
  ('EXT_OUTLOOK_NEGATIVE', 'Outlook changed to negative', 'Rating', 'Risk', 'External Quantitative', 'rating_agency', 'Early Warning', 0.5D, 'Agency outlook revised to Negative.'),
  ('PARENT_RATING_DOWNGRADE', 'JP parent downgrade (Japan share)', 'Rating', 'Risk', 'External Quantitative', 'jp_share', 'Early Warning', 0.8D, 'Tokyo HO downgrade of the Japanese parent (share_jp_parent_rating).'),
  ('PARENT_RATING_UPGRADE', 'JP parent upgrade (Japan share)', 'Rating', 'Opportunity', 'External Quantitative', 'jp_share', 'Both', 0.5D, 'Tokyo HO upgrade of the Japanese parent (share_jp_parent_rating).'),
  ('RM_NOTE_NEGATIVE', 'Negative RM call note', 'Internal Sentiment', 'Risk', 'Internal Qualitative', 'crm_notes', 'Early Warning', 0.4D, 'RM activity note with negative sentiment.'),
  ('RM_NOTE_POSITIVE', 'Positive RM call note', 'Internal Sentiment', 'Opportunity', 'Internal Qualitative', 'crm_notes', 'Opportunity Identification', 0.3D, 'RM activity note with positive sentiment.')
)
SELECT signal_code, signal_name, signal_category, polarity, source_quadrant,
       CASE WHEN engine = 'signal_engine' THEN 'crm_signal_engine' ELSE coalesce(engine, 'crm_signal_engine') END AS source_system,
       'Opportunity Identification' AS consumed_by, round(avg_strength, 2) AS default_weight,
       concat(signal_name, ' - opportunity signal raised by the CRM ', coalesce(engine, 'signal engine'), '.') AS description,
       false AS is_derived_in_gold, CAST(n AS BIGINT) AS signals_recorded
FROM crm
UNION ALL
SELECT trigger_code, trigger_name, category, 'Risk',
       CASE WHEN category = 'External' THEN 'External Qualitative' ELSE 'Internal Quantitative' END,
       'ews_engine', 'Early Warning',
       CASE default_severity WHEN 'High' THEN 1.0 WHEN 'Medium' THEN 0.6 ELSE 0.3 END,
       concat(coalesce(description, trigger_name), ' Fires when ', coalesce(threshold_desc, 'its rule is met'), '.'),
       false, CAST(n AS BIGINT)
FROM ews WHERE trigger_code NOT IN (SELECT signal_code FROM crm)
UNION ALL
SELECT signal_code, signal_name, signal_category, polarity, source_quadrant, source_system, consumed_by, default_weight,
       description, true, CAST(0 AS BIGINT)
FROM derived WHERE signal_code NOT IN (SELECT signal_code FROM crm UNION SELECT trigger_code FROM ews)
