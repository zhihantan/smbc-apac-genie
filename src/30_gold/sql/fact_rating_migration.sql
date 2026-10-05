-- fact_rating_migration (WP8d, brief 5.4): internal rating actions per credit obligor (silver.core_rating_history):
-- grade and IFRS 9 stage from -> to, notches, reason, approver, linked review, with migration flags and the EWS
-- signals that fired in the 90 days before the action (silver.ews_signal, evaluated at month-ends). Rating
-- events dated before the calendar window (opening ratings before 1-Apr-2023) are excluded.
WITH r AS (
  SELECT * FROM ${catalog}.silver.core_rating_history
  WHERE effective_date BETWEEN DATE'${history_start}' AND DATE'${as_of_date}'
),
sig AS (
  SELECT golden_client_id, least(last_day(signal_month), DATE'${as_of_date}') AS signal_date, trigger_code
  FROM ${catalog}.silver.ews_signal WHERE golden_client_id IS NOT NULL
),
prior_sig AS (
  SELECT r.rating_event_id,
         count(DISTINCT s.trigger_code)                                        AS n_triggers_prior_90d,
         concat_ws(', ', sort_array(collect_set(s.trigger_code)))              AS triggers_prior_90d
  FROM r JOIN sig s ON s.golden_client_id = r.golden_client_id
   AND s.signal_date < r.effective_date AND s.signal_date >= date_sub(r.effective_date, 90)
  GROUP BY r.rating_event_id
)
SELECT
  r.rating_event_id,
  r.obligor_id,
  dc.golden_client_sk,
  r.golden_client_id,
  r.client_group_id,
  r.effective_date,
  r.rating_action,
  CAST(r.previous_grade AS INT)                                                 AS grade_from,
  CAST(r.internal_grade AS INT)                                                 AS grade_to,
  CAST(r.notch_change AS INT)                                                   AS notch_change,
  r.rating_equivalent                                                           AS rating_equivalent_to,
  CAST(r.previous_stage AS INT)                                                 AS stage_from,
  CAST(r.ifrs9_stage AS INT)                                                    AS stage_to,
  CASE WHEN r.previous_grade IS NOT NULL THEN concat(r.previous_grade, ' -> ', r.internal_grade) END AS grade_migration,
  CASE WHEN r.previous_stage IS NOT NULL THEN concat('Stage ', r.previous_stage, ' -> Stage ', r.ifrs9_stage) END AS stage_migration,
  r.rating_action = 'Downgrade'                                                 AS is_downgrade,
  r.rating_action = 'Upgrade'                                                   AS is_upgrade,
  coalesce(r.previous_stage <> r.ifrs9_stage, false)                            AS is_stage_change,
  coalesce(r.ifrs9_stage = 3 AND r.previous_stage < 3, false)                   AS is_stage3_entry,
  coalesce(r.ifrs9_stage > r.previous_stage, false)                             AS is_stage_deterioration,
  CAST(r.origination_grade AS INT)                                              AS origination_grade,
  CAST(r.internal_grade - r.origination_grade AS INT)                           AS notches_vs_origination,
  r.action_reason,
  r.rating_model,
  r.approver_id,
  r.review_id,
  CAST(coalesce(p.n_triggers_prior_90d, 0) AS INT)                              AS n_triggers_prior_90d,
  p.triggers_prior_90d
FROM r
LEFT JOIN prior_sig p ON p.rating_event_id = r.rating_event_id
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = r.golden_client_id
  AND r.effective_date <= dc.valid_to
  AND (r.effective_date >= dc.valid_from OR dc.version_no = 1)
