-- fact_rm_note_sentiment: one row per RM call note (silver.crm_activity.note_text) with the synthetic sentiment
-- score and its label (D18) plus the ai_analyze_sentiment label stored alongside (D36; the AI function works on
-- this warehouse - one batch, repartitioned so the calls run in parallel), keyword-extracted topics, the follow-up
-- action status and the external (news) sentiment context of the client group for the internal-vs-external
-- divergence / blind-spot questions. Cut-offs: Negative <= -0.2, Positive >= 0.2 (same as fact_news_item).
WITH a AS (
  SELECT * FROM ${catalog}.silver.crm_activity
  WHERE activity_date BETWEEN DATE'${history_start}' AND DATE'${as_of_date}'
    AND note_text IS NOT NULL AND golden_client_id IS NOT NULL
), ai AS (
  SELECT activity_id, initcap(ai_analyze_sentiment(note_text)) AS ai_label
  FROM (SELECT /*+ REPARTITION(96) */ activity_id, note_text FROM a)
), news AS (
  SELECT client_group_id, published_date, raw_sentiment FROM ${catalog}.silver.ext_news
  WHERE client_group_id IS NOT NULL AND published_date <= DATE'${as_of_date}'
), ctx AS (          -- the group's news sentiment in the 90 days up to the note date
  SELECT a.activity_id, avg(n.raw_sentiment) AS s90, count(n.raw_sentiment) AS n90
  FROM a JOIN news n ON n.client_group_id = a.client_group_id
   AND n.published_date BETWEEN date_sub(a.activity_date, 89) AND a.activity_date
  GROUP BY a.activity_id
), gnews AS (        -- as of 30-Sep-2026: group news sentiment, last 90 days vs the 90 days before
  SELECT client_group_id,
         avg(CASE WHEN published_date > date_sub(DATE'${as_of_date}', 90) THEN raw_sentiment END) AS s_last,
         avg(CASE WHEN published_date > date_sub(DATE'${as_of_date}', 180)
                   AND published_date <= date_sub(DATE'${as_of_date}', 90) THEN raw_sentiment END) AS s_prior
  FROM news GROUP BY client_group_id
), gnote AS (        -- as of 30-Sep-2026: group RM-note sentiment in the last 90 days
  SELECT client_group_id, avg(CASE WHEN activity_date > date_sub(DATE'${as_of_date}', 90) THEN sentiment_score END) AS r_last
  FROM a WHERE client_group_id IS NOT NULL GROUP BY client_group_id
), t AS (            -- keyword topic extraction from the note text (first match = primary topic)
  SELECT activity_id,
         CASE WHEN x RLIKE 'liquidity|covenant|ebitda|leverage|weaker trading|financial statements' THEN 'Credit & Liquidity' END AS t1,
         CASE WHEN x RLIKE 'pricing|\\bmargins?\\b|\\bfees?\\b' THEN 'Pricing' END AS t2,
         CASE WHEN x RLIKE 'another bank|other bank|existing provider|current bank|other offers|consolidating banks|banking relationships|banking costs|share is at risk' THEN 'Competition' END AS t3,
         CASE WHEN x RLIKE 'repair|quer(y|ies)|straight-through|portal|cut-off|operations' THEN 'Service Quality' END AS t4,
         CASE WHEN x RLIKE 'market update|market call|outlook|hedging|volatility|fx scenarios|funding cost' THEN 'Market & FX' END AS t5,
         CASE WHEN x RLIKE 'courtesy|relationship review|account plan|coverage team|priorities|annual review|review with' THEN 'Relationship' END AS t6,
         CASE WHEN x RLIKE 'opportunit|indicative terms|term sheet|strong interest|mandate|proposal|open to more|follow-up|interest in|positioned|pitched|introduced|discussed' THEN 'New Business' END AS t7
  FROM (SELECT activity_id, lower(note_text) AS x FROM a)
)
SELECT
  a.activity_id                                                        AS note_id,
  a.activity_date                                                      AS note_date,
  trunc(a.activity_date, 'MM')                                         AS month,
  dc.golden_client_sk,
  a.golden_client_id,
  a.client_group_id,
  a.crm_account_id,
  a.rm_code,
  e.employee_id,
  a.contact_id,
  a.activity_type,
  a.purpose,
  a.subject,
  a.product_discussed,
  a.product_family,
  a.note_text,
  a.raw_tone,
  a.sentiment_score,
  CASE WHEN a.sentiment_score <= -0.2 THEN 'Negative' WHEN a.sentiment_score >= 0.2 THEN 'Positive'
       ELSE 'Neutral' END                                              AS sentiment_label,
  ai.ai_label                                                          AS ai_sentiment_label,
  CASE WHEN ai.ai_label IS NOT NULL THEN 'ai_analyze_sentiment' ELSE 'not returned' END AS ai_label_source,
  CASE WHEN ai.ai_label IS NULL THEN NULL
       ELSE ai.ai_label = CASE WHEN a.sentiment_score <= -0.2 THEN 'Negative' WHEN a.sentiment_score >= 0.2 THEN 'Positive'
                               ELSE 'Neutral' END END                  AS ai_agrees_with_score,
  a.sentiment_score <= -0.2                                            AS is_negative,
  a.sentiment_score >= 0.2                                             AS is_positive,
  coalesce(t.t1, t.t2, t.t3, t.t4, t.t5, t.t6, t.t7, 'General')         AS note_topic,
  coalesce(nullif(concat_ws(', ', t.t1, t.t2, t.t3, t.t4, t.t5, t.t6, t.t7), ''), 'General') AS topics_text,
  coalesce(a.action_required, false)                                   AS action_required,
  a.next_action,
  a.next_action_due_date,
  a.next_action_status,
  coalesce(a.next_action_status IN ('Open', 'Overdue'), false)         AS is_action_open,
  coalesce(a.next_action_status = 'Overdue', false)                    AS is_action_overdue,
  a.related_signal_id,
  a.related_opportunity_id,
  a.duration_minutes,
  datediff(DATE'${as_of_date}', a.activity_date)                       AS days_before_as_of,
  datediff(DATE'${as_of_date}', a.activity_date) < 90                  AS is_last_90d,
  round(c.s90, 4)                                                      AS group_news_sentiment_90d,
  CAST(coalesce(c.n90, 0) AS INT)                                      AS group_news_items_90d,
  round(a.sentiment_score - c.s90, 4)                                  AS sentiment_divergence,
  coalesce(a.sentiment_score >= 0.2 AND c.s90 <= -0.2, false)          AS is_blind_spot_note,
  round(gn.s_last, 4)                                                  AS group_news_sentiment_last_90d,
  round(gn.s_prior, 4)                                                 AS group_news_sentiment_prior_90d,
  round(gr.r_last, 4)                                                  AS group_note_sentiment_last_90d,
  coalesce(gn.s_last <= -0.2 AND (gn.s_prior IS NULL OR gn.s_prior > -0.2) AND gr.r_last >= 0.2, false) AS is_blind_spot_group
FROM a
LEFT JOIN ai ON ai.activity_id = a.activity_id
LEFT JOIN t ON t.activity_id = a.activity_id
LEFT JOIN ctx c ON c.activity_id = a.activity_id
LEFT JOIN gnews gn ON gn.client_group_id = a.client_group_id
LEFT JOIN gnote gr ON gr.client_group_id = a.client_group_id
LEFT JOIN ${catalog}.gold.dim_employee e ON e.rm_code = a.rm_code
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = a.golden_client_id
  AND a.activity_date <= dc.valid_to
  AND (a.activity_date >= dc.valid_from OR dc.version_no = 1)
