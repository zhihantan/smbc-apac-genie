-- fact_signal_event: the unified signal feed - every signal from every source, keyed to gold.dim_signal_type:
--   CRM opportunity signals (silver.crm_signal), EWS trigger signals (silver.ews_signal), news items
--   (gold.fact_news_item), market moves (gold.fact_market_signal_daily entry events), external agency rating
--   actions (silver.ext_rating), JP parent rating actions from the Japan share (silver.share_jp_parent_rating)
--   and non-neutral RM notes (gold.fact_rm_note_sentiment).
-- Group-level signals (market, agency, JP parent) are attached to the group's lead entity. Acknowledged = CRM
-- status moved beyond New; RM notes are raised by the RM; any other signal is acknowledged when an RM logged a
-- CRM activity with the client group within 30 days on or after the signal date.
WITH crm AS (
  SELECT signal_id AS signal_event_id, 'crm_signal' AS source_feed, signal_id AS source_record_id,
         detected_date AS signal_date, signal_code, golden_client_id, client_group_id, 'Client' AS signal_level,
         CAST(strength AS DOUBLE) AS strength, CAST(NULL AS DOUBLE) AS sentiment_score, signal_detail AS signal_text,
         polarity, status AS source_status, true AS is_new_occurrence, linked_opportunity_id, owner_rm AS owner_rm_code,
         status <> 'New' AS src_ack, coalesce(actioned_date, status_date) AS src_ack_date, 'APAC' AS source_region
  FROM ${catalog}.silver.crm_signal
  WHERE golden_client_id IS NOT NULL AND detected_date BETWEEN DATE'${history_start}' AND DATE'${as_of_date}'
), ews AS (
  SELECT concat('EWS-', obligor_id, '-', date_format(signal_month, 'yyyyMM'), '-', trigger_code) AS signal_event_id,
         'ews_signal' AS source_feed, concat(obligor_id, '|', CAST(signal_month AS STRING), '|', trigger_code) AS source_record_id,
         signal_month AS signal_date, trigger_code AS signal_code, golden_client_id, client_group_id, 'Client' AS signal_level,
         CASE severity WHEN 'High' THEN 1.0D WHEN 'Medium' THEN 0.6D ELSE 0.3D END AS strength,
         CAST(NULL AS DOUBLE) AS sentiment_score, detail AS signal_text, 'Risk' AS polarity, CAST(NULL AS STRING) AS source_status,
         coalesce(lag(signal_month) OVER (PARTITION BY obligor_id, trigger_code ORDER BY signal_month) < add_months(signal_month, -1), true) AS is_new_occurrence,
         CAST(NULL AS STRING) AS linked_opportunity_id, CAST(NULL AS STRING) AS owner_rm_code,
         CAST(NULL AS BOOLEAN) AS src_ack, CAST(NULL AS DATE) AS src_ack_date, 'APAC' AS source_region
  FROM ${catalog}.silver.ews_signal
  WHERE golden_client_id IS NOT NULL AND signal_month BETWEEN DATE'${history_start}' AND DATE'${as_of_date}'
), news AS (
  SELECT concat('NEWS-EVT-', news_id) AS signal_event_id, 'news' AS source_feed, news_id AS source_record_id,
         published_date AS signal_date, signal_code, golden_client_id, client_group_id, 'Client' AS signal_level,
         round(0.5 * relevance + 0.5 * abs(sentiment_score), 3) AS strength, sentiment_score, headline AS signal_text,
         signal_polarity AS polarity, CAST(NULL AS STRING) AS source_status, true AS is_new_occurrence,
         CAST(NULL AS STRING) AS linked_opportunity_id, CAST(NULL AS STRING) AS owner_rm_code,
         CAST(NULL AS BOOLEAN) AS src_ack, CAST(NULL AS DATE) AS src_ack_date, 'APAC' AS source_region
  FROM ${catalog}.gold.fact_news_item
), mkt_rows AS (
  SELECT m.*, dg.lead_golden_client_id
  FROM ${catalog}.gold.fact_market_signal_daily m
  JOIN ${catalog}.gold.dim_client_group dg ON dg.client_group_id = m.client_group_id
  WHERE (m.is_52w_low_event OR m.is_price_drop_event OR m.is_cds_widening_event) AND dg.lead_golden_client_id IS NOT NULL
), mkt AS (
  SELECT concat('MKT-', issuer_id, '-', date_format(trade_date, 'yyyyMMdd'), '-', code) AS signal_event_id, 'market' AS source_feed,
         concat(issuer_id, '|', CAST(trade_date AS STRING)) AS source_record_id, trade_date AS signal_date, code AS signal_code,
         lead_golden_client_id AS golden_client_id, client_group_id, 'Group' AS signal_level, strength,
         CAST(NULL AS DOUBLE) AS sentiment_score, txt AS signal_text, 'Risk' AS polarity, CAST(NULL AS STRING) AS source_status,
         true AS is_new_occurrence, CAST(NULL AS STRING) AS linked_opportunity_id, CAST(NULL AS STRING) AS owner_rm_code,
         CAST(NULL AS BOOLEAN) AS src_ack, CAST(NULL AS DATE) AS src_ack_date, 'APAC' AS source_region
  FROM (
    SELECT issuer_id, trade_date, client_group_id, lead_golden_client_id, 'MARKET_52W_LOW' AS code,
           round(least(1.0, 0.5 + greatest(-coalesce(price_change_30d_pct, 0), 0)), 3) AS strength,
           concat(issuer_name, ' closes at its 52-week low (', CAST(close_price AS STRING), ' ', currency, ')') AS txt
    FROM mkt_rows WHERE is_52w_low_event
    UNION ALL
    SELECT issuer_id, trade_date, client_group_id, lead_golden_client_id, 'MARKET_PRICE_DROP_30D',
           round(least(1.0, abs(price_change_30d_pct) / 0.4), 3),
           concat(issuer_name, ' shares down ', CAST(round(-100 * price_change_30d_pct, 1) AS STRING), '% in 30 days')
    FROM mkt_rows WHERE is_price_drop_event
    UNION ALL
    SELECT issuer_id, trade_date, client_group_id, lead_golden_client_id, 'MARKET_CDS_WIDENING',
           round(least(1.0, cds_change_30d_bps / 100), 3),
           concat(issuer_name, ' CDS spread +', CAST(round(cds_change_30d_bps, 0) AS STRING), ' bps in 30 days to ',
                  CAST(round(cds_spread_bps, 0) AS STRING), ' bps')
    FROM mkt_rows WHERE is_cds_widening_event)
), xr AS (
  SELECT concat('XR-', r.rating_id) AS signal_event_id, 'ext_rating' AS source_feed, r.rating_id AS source_record_id,
         r.action_date AS signal_date,
         CASE WHEN r.action = 'Downgrade' THEN 'EXT_RATING_DOWNGRADE' WHEN r.action = 'Upgrade' THEN 'EXT_RATING_UPGRADE'
              ELSE 'EXT_OUTLOOK_NEGATIVE' END AS signal_code,
         dg.lead_golden_client_id AS golden_client_id, r.client_group_id, 'Group' AS signal_level,
         CASE WHEN r.action IN ('Downgrade', 'Upgrade') THEN round(least(1.0, 0.4 + 0.2 * abs(coalesce(r.notch_change, 1))), 3)
              ELSE 0.5D END AS strength,
         CAST(NULL AS DOUBLE) AS sentiment_score,
         concat(r.agency, ' ', lower(r.action), ': ', coalesce(r.previous_rating, '-'), ' -> ', r.rating, ', outlook ',
                coalesce(r.outlook, '-'), '. ', coalesce(r.rationale, '')) AS signal_text,
         CASE WHEN r.action = 'Upgrade' THEN 'Opportunity' ELSE 'Risk' END AS polarity, CAST(NULL AS STRING) AS source_status,
         true AS is_new_occurrence, CAST(NULL AS STRING) AS linked_opportunity_id, CAST(NULL AS STRING) AS owner_rm_code,
         CAST(NULL AS BOOLEAN) AS src_ack, CAST(NULL AS DATE) AS src_ack_date, 'APAC' AS source_region
  FROM ${catalog}.silver.ext_rating r
  JOIN ${catalog}.gold.dim_client_group dg ON dg.client_group_id = r.client_group_id
  WHERE (r.action IN ('Downgrade', 'Upgrade') OR (r.action = 'Outlook Revised' AND r.outlook = 'Negative'))
    AND r.action_date BETWEEN DATE'${history_start}' AND DATE'${as_of_date}' AND dg.lead_golden_client_id IS NOT NULL
), jpr AS (
  SELECT concat('JPR-EVT-', p.rating_id) AS signal_event_id, 'jp_parent_rating' AS source_feed, p.rating_id AS source_record_id,
         p.rating_date AS signal_date,
         CASE p.rating_action WHEN 'Upgrade' THEN 'PARENT_RATING_UPGRADE' ELSE 'PARENT_RATING_DOWNGRADE' END AS signal_code,
         dg.lead_golden_client_id AS golden_client_id, p.client_group_id, 'Group' AS signal_level,
         round(least(1.0, 0.4 + 0.2 * abs(coalesce(p.grade_to - p.grade_from, 1))), 3) AS strength,
         CAST(NULL AS DOUBLE) AS sentiment_score,
         concat('Tokyo HO ', lower(p.rating_action), ' of the JP parent (', dg.jp_parent_legal_name, '): grade ',
                coalesce(CAST(p.grade_from AS STRING), '-'), ' -> ', CAST(p.grade_to AS STRING), ' (', p.rating_equivalent,
                ', outlook ', coalesce(p.outlook, '-'), '). ', coalesce(p.rating_reason, '')) AS signal_text,
         CASE p.rating_action WHEN 'Upgrade' THEN 'Opportunity' ELSE 'Risk' END AS polarity, CAST(NULL AS STRING) AS source_status,
         true AS is_new_occurrence, CAST(NULL AS STRING) AS linked_opportunity_id, CAST(NULL AS STRING) AS owner_rm_code,
         CAST(NULL AS BOOLEAN) AS src_ack, CAST(NULL AS DATE) AS src_ack_date, 'JP' AS source_region
  FROM ${catalog}.silver.share_jp_parent_rating p
  JOIN ${catalog}.gold.dim_client_group dg ON dg.client_group_id = p.client_group_id
  WHERE p.rating_action IN ('Upgrade', 'Downgrade') AND dg.lead_golden_client_id IS NOT NULL
    AND p.rating_date BETWEEN DATE'${history_start}' AND DATE'${as_of_date}'
), notes AS (
  SELECT concat('NOTE-', note_id) AS signal_event_id, 'rm_note' AS source_feed, note_id AS source_record_id,
         note_date AS signal_date, CASE sentiment_label WHEN 'Negative' THEN 'RM_NOTE_NEGATIVE' ELSE 'RM_NOTE_POSITIVE' END AS signal_code,
         golden_client_id, client_group_id, 'Client' AS signal_level, round(abs(sentiment_score), 3) AS strength,
         sentiment_score, note_text AS signal_text,
         CASE sentiment_label WHEN 'Negative' THEN 'Risk' ELSE 'Opportunity' END AS polarity, CAST(NULL AS STRING) AS source_status,
         true AS is_new_occurrence, CAST(NULL AS STRING) AS linked_opportunity_id, rm_code AS owner_rm_code,
         true AS src_ack, note_date AS src_ack_date, 'APAC' AS source_region
  FROM ${catalog}.gold.fact_rm_note_sentiment WHERE sentiment_label IN ('Negative', 'Positive')
), u AS (
  SELECT * FROM crm UNION ALL SELECT * FROM ews UNION ALL SELECT * FROM news UNION ALL SELECT * FROM mkt
  UNION ALL SELECT * FROM xr UNION ALL SELECT * FROM jpr UNION ALL SELECT * FROM notes
), act AS (
  SELECT DISTINCT client_group_id, activity_date FROM ${catalog}.silver.crm_activity
  WHERE client_group_id IS NOT NULL AND activity_date <= DATE'${as_of_date}'
), ack AS (       -- first RM activity with the client group within 30 days on / after the signal
  SELECT u.signal_event_id, min(a.activity_date) AS ack_date
  FROM u JOIN act a ON a.client_group_id = u.client_group_id
   AND a.activity_date BETWEEN u.signal_date AND date_add(u.signal_date, 30)
  WHERE u.src_ack IS NULL
  GROUP BY u.signal_event_id
), opp AS (
  SELECT golden_client_id, client_group_id, created_date, status FROM ${catalog}.silver.crm_opportunity
  WHERE golden_client_id IS NOT NULL AND created_date <= DATE'${as_of_date}'
), pipe AS (      -- client-level signals look at the client, group-level signals at the whole group
  SELECT u.signal_event_id,
         max(CASE WHEN o.status = 'Open' THEN 1 ELSE 0 END) = 1                    AS has_open,
         max(CASE WHEN o.created_date >= u.signal_date THEN 1 ELSE 0 END) = 1     AS has_since
  FROM u JOIN opp o
    ON (u.signal_level = 'Client' AND o.golden_client_id = u.golden_client_id)
    OR (u.signal_level = 'Group' AND o.client_group_id = u.client_group_id)
  GROUP BY u.signal_event_id
)
SELECT
  u.signal_event_id,
  u.signal_date,
  trunc(u.signal_date, 'MM')                                                    AS month,
  u.signal_code,
  st.signal_name,
  st.signal_category,
  u.polarity,
  u.polarity = 'Opportunity'                                                    AS is_opportunity_signal,
  u.polarity = 'Risk'                                                           AS is_risk_signal,
  st.source_quadrant,
  st.consumed_by,
  u.source_feed,
  st.source_system,
  u.source_region,
  u.source_record_id,
  u.signal_level,
  dc.golden_client_sk,
  u.golden_client_id,
  u.client_group_id,
  coalesce(eo.employee_id, dc.primary_rm_id)                                    AS owner_employee_id,
  u.strength,
  u.sentiment_score,
  u.signal_text,
  u.source_status,
  u.is_new_occurrence,
  coalesce(u.src_ack, k.ack_date IS NOT NULL)                                   AS is_acknowledged,
  CASE WHEN u.src_ack IS NULL THEN k.ack_date WHEN u.src_ack THEN u.src_ack_date END AS acknowledged_date,
  datediff(CASE WHEN u.src_ack IS NULL THEN k.ack_date WHEN u.src_ack THEN u.src_ack_date END, u.signal_date) AS days_to_acknowledge,
  u.linked_opportunity_id,
  coalesce(p.has_open, false)                                                   AS has_open_pipeline,
  coalesce(p.has_since, false) OR u.linked_opportunity_id IS NOT NULL           AS has_opportunity_since_signal,
  coalesce(p.has_open, false) OR coalesce(p.has_since, false) OR u.linked_opportunity_id IS NOT NULL AS has_pipeline,
  datediff(DATE'${as_of_date}', u.signal_date)                                  AS days_before_as_of,
  datediff(DATE'${as_of_date}', u.signal_date) < 30                             AS is_last_30d,
  datediff(DATE'${as_of_date}', u.signal_date) < 90                             AS is_last_90d
FROM u
LEFT JOIN ${catalog}.gold.dim_signal_type st ON st.signal_code = u.signal_code
LEFT JOIN ack k ON k.signal_event_id = u.signal_event_id
LEFT JOIN pipe p ON p.signal_event_id = u.signal_event_id
LEFT JOIN ${catalog}.gold.dim_employee eo ON eo.rm_code = u.owner_rm_code
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = u.golden_client_id
  AND u.signal_date <= dc.valid_to
  AND (u.signal_date >= dc.valid_from OR dc.version_no = 1)
