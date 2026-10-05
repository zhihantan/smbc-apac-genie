-- fact_ews_score_daily (WP8d, brief 5.4): daily composite EWS score per golden client (silver.ews_score,
-- Apr-2025..Sep-2026). Composite = 1.12 x 100 x (0.45 health + 0.20 utilisation + 0.15 covenant + 0.12 deposit
-- + 0.08 wallet-leakage stress terms), banded Green < 40 <= Amber < 70 <= Red; the external (news) component is
-- informational. computed_band = model band, final_band = after steward overrides (silver.ews_override; the
-- latest override in force wins). source_data_stale = the override in force was confirmed on a day the JP share
-- feed it relies on was stale (silver.dq_share_refresh_log). Duplicate credit records of one golden client are
-- collapsed to the worst record of the day (final band, then score). Precomputed (D13): previous band, band
-- change, band 30 days ago, days in band, top-3 triggers of the month, month-end exposure / EAD / ECL.
WITH sc AS (
  SELECT golden_client_id, client_group_id, obligor_id, score_date, composite_score, band, final_band, is_overridden,
         credit_component, liquidity_component, behavioural_component, external_component, is_watchlisted,
         CASE final_band WHEN 'Red' THEN 3 WHEN 'Amber' THEN 2 WHEN 'Green' THEN 1 ELSE 0 END AS band_rank
  FROM ${catalog}.silver.ews_score
  WHERE golden_client_id IS NOT NULL AND score_date <= DATE'${as_of_date}'
),
pick AS (
  SELECT golden_client_id, score_date,
         max_by(named_struct('obligor_id', obligor_id, 'client_group_id', client_group_id, 'score', composite_score,
                             'band', band, 'final_band', final_band, 'is_overridden', is_overridden,
                             'credit', credit_component, 'liquidity', liquidity_component,
                             'behavioural', behavioural_component, 'external', external_component),
                struct(band_rank, composite_score, obligor_id))       AS r,
         bool_or(is_watchlisted)                                     AS is_watchlisted,
         count(*)                                                    AS n_obligors_scored
  FROM sc GROUP BY golden_client_id, score_date
),
jp_stale AS (   -- days a JP share feed used as override evidence was stale (lag > 24h)
  SELECT log_date, bool_or(is_stale) AS any_stale
  FROM ${catalog}.silver.dq_share_refresh_log
  WHERE provider_region = 'JP' AND table_name IN ('share_jp_parent_rating', 'share_jp_support_letters')
  GROUP BY log_date
),
ovr AS (
  SELECT o.override_id, o.obligor_id, o.override_date, o.expiry_date, o.system_band, o.override_band, o.direction,
         o.rationale, o.analyst, o.evidence_ref,
         o.rationale LIKE 'Parent support confirmed (JP data)%'                                     AS is_parent_support,
         (o.rationale LIKE '%(JP data)%' OR o.evidence_ref IS NOT NULL) AND coalesce(j.any_stale, false) AS is_stale
  FROM ${catalog}.silver.ews_override o
  LEFT JOIN jp_stale j ON j.log_date = o.override_date
),
cover AS (
  SELECT p.golden_client_id, p.score_date,
         max_by(named_struct('id', o.override_id, 'odate', o.override_date, 'system_band', o.system_band, 'band', o.override_band,
                             'direction', o.direction, 'reason', o.rationale, 'analyst', o.analyst, 'evidence', o.evidence_ref,
                             'parent', o.is_parent_support, 'stale', o.is_stale), struct(o.override_date, o.override_id)) AS o
  FROM pick p
  JOIN ovr o ON o.obligor_id = p.r.obligor_id AND p.score_date BETWEEN o.override_date AND o.expiry_date
  GROUP BY p.golden_client_id, p.score_date
),
trig AS (       -- top 3 triggers of the client's month: score points, then severity, then code
  SELECT golden_client_id, signal_month,
         max(CASE WHEN rn = 1 THEN trigger_code END) AS t1, max(CASE WHEN rn = 2 THEN trigger_code END) AS t2,
         max(CASE WHEN rn = 3 THEN trigger_code END) AS t3, count(*) AS n_triggers
  FROM (SELECT golden_client_id, signal_month, trigger_code,
               row_number() OVER (PARTITION BY golden_client_id, signal_month
                                  ORDER BY max(points_contributed) DESC,
                                           max(CASE severity WHEN 'High' THEN 3 WHEN 'Medium' THEN 2 ELSE 1 END) DESC,
                                           trigger_code) AS rn
        FROM ${catalog}.silver.ews_signal WHERE golden_client_id IS NOT NULL
        GROUP BY golden_client_id, signal_month, trigger_code)
  GROUP BY golden_client_id, signal_month
),
expo AS (
  SELECT golden_client_id, balance_date, sum(drawn_usd) AS drawn_usd, sum(limit_usd) AS limit_usd
  FROM ${catalog}.silver.core_facility_balance_monthly WHERE golden_client_id IS NOT NULL
  GROUP BY golden_client_id, balance_date
),
cap AS (
  SELECT golden_client_id, month, sum(ead_usd) AS ead_usd, sum(ecl_usd) AS ecl_usd
  FROM ${catalog}.silver.fin_capital_allocation WHERE golden_client_id IS NOT NULL
  GROUP BY golden_client_id, month
),
base AS (
  SELECT p.golden_client_id, p.score_date, p.r.client_group_id AS client_group_id, p.r.obligor_id AS obligor_id,
         p.n_obligors_scored, p.r.score AS composite_score, p.r.band AS computed_band, p.r.final_band AS final_band,
         coalesce(p.r.is_overridden, false) AS is_overridden, p.is_watchlisted,
         p.r.credit AS credit_component, p.r.liquidity AS liquidity_component,
         p.r.behavioural AS behavioural_component, p.r.external AS external_component, c.o
  FROM pick p LEFT JOIN cover c ON c.golden_client_id = p.golden_client_id AND c.score_date = p.score_date
),
w1 AS (
  SELECT b.*,
         lag(final_band) OVER w    AS prev_final_band,
         lag(computed_band) OVER w AS prev_computed_band,
         CASE WHEN lag(score_date, 30) OVER w = date_sub(score_date, 30) THEN lag(final_band, 30) OVER w END      AS final_band_30d_ago,
         CASE WHEN lag(score_date, 30) OVER w = date_sub(score_date, 30) THEN lag(composite_score, 30) OVER w END AS score_30d_ago
  FROM base b
  WINDOW w AS (PARTITION BY golden_client_id ORDER BY score_date)
),
w2 AS (
  SELECT w1.*,
         sum(CASE WHEN prev_final_band IS NULL OR prev_final_band <> final_band THEN 1 ELSE 0 END)
           OVER (PARTITION BY golden_client_id ORDER BY score_date ROWS UNBOUNDED PRECEDING) AS band_island
  FROM w1
),
w3 AS (
  SELECT w2.*, row_number() OVER (PARTITION BY golden_client_id, band_island ORDER BY score_date) AS days_in_band
  FROM w2
)
SELECT
  s.golden_client_id,
  s.score_date,
  dc.golden_client_sk,
  s.client_group_id,
  s.obligor_id,
  CAST(s.n_obligors_scored AS INT)                                                      AS n_obligors_scored,
  s.composite_score,
  s.computed_band,
  s.final_band,
  s.prev_final_band,
  s.prev_computed_band,
  s.prev_final_band IS NOT NULL AND s.prev_final_band <> s.final_band                   AS final_band_changed,
  CASE WHEN s.prev_final_band IS NOT NULL AND s.prev_final_band <> s.final_band
       THEN concat(s.prev_final_band, ' to ', s.final_band) END                         AS band_change,
  coalesce(s.prev_final_band = 'Green' AND s.final_band = 'Amber', false)               AS moved_green_to_amber,
  coalesce(s.prev_final_band <> 'Red' AND s.final_band = 'Red', false)                  AS entered_red,
  s.final_band_30d_ago,
  coalesce(s.final_band_30d_ago = 'Green' AND s.final_band = 'Amber', false)          AS moved_green_to_amber_30d,
  s.score_30d_ago,
  round(s.composite_score - s.score_30d_ago, 1)                                         AS score_change_30d,
  CAST(s.days_in_band AS INT)                                                           AS days_in_final_band,
  s.computed_band <> s.final_band                                                       AS is_band_overridden,
  s.is_overridden,
  coalesce(s.o.odate = s.score_date, false)                                              AS override_event_flag,
  s.o.id                                                                                AS override_id,
  s.o.odate                                                                             AS override_date,
  s.o.direction                                                                         AS override_direction,
  s.o.system_band                                                                       AS override_model_band,
  s.o.reason                                                                            AS override_reason,
  s.o.analyst                                                                           AS override_analyst_name,
  s.o.evidence                                                                          AS override_evidence_ref,
  coalesce(s.o.parent, false)                                                           AS is_parent_support_override,
  coalesce(s.o.stale, false)                                                            AS source_data_stale,
  s.credit_component,
  s.liquidity_component,
  s.behavioural_component,
  s.external_component,
  t.t1                                                                                  AS top_trigger_1,
  t.t2                                                                                  AS top_trigger_2,
  t.t3                                                                                  AS top_trigger_3,
  nullif(concat_ws(', ', t.t1, t.t2, t.t3), '')                                         AS top_triggers_text,
  CAST(coalesce(t.n_triggers, 0) AS INT)                                                AS n_triggers_month,
  s.is_watchlisted,
  coalesce(e.drawn_usd, 0D)                                                             AS exposure_drawn_usd,
  coalesce(e.limit_usd, 0D)                                                             AS exposure_limit_usd,
  cp.ead_usd,
  cp.ecl_usd,
  d.is_month_end,
  s.score_date = DATE'${as_of_date}'                                                    AS is_as_of_date
FROM w3 s
JOIN ${catalog}.gold.dim_date d ON d.date = s.score_date
LEFT JOIN trig t ON t.golden_client_id = s.golden_client_id AND t.signal_month = trunc(s.score_date, 'MM')
LEFT JOIN expo e ON e.golden_client_id = s.golden_client_id AND e.balance_date = last_day(s.score_date)
LEFT JOIN cap cp ON cp.golden_client_id = s.golden_client_id AND cp.month = trunc(s.score_date, 'MM')
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = s.golden_client_id
  AND s.score_date <= dc.valid_to
  AND (s.score_date >= dc.valid_from OR dc.version_no = 1)
