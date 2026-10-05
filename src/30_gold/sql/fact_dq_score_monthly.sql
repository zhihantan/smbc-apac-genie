-- fact_dq_score_monthly: monthly DQ score per layer-qualified table x DQ dimension (silver.dq_score_history:
-- 42 months, simulated + explainable incidents, latest month = the current DQ run's actual results), with the
-- previous month's score for "this month vs last", a below-95% flag and the incident behind each dip.
WITH h AS (
  SELECT h.*, regexp_extract(h.table_name, '^[a-z]+\\.(.+)$', 1) AS base_table_name,
         max(h.month) OVER () AS last_month,
         lag(h.score) OVER (PARTITION BY h.table_name, h.dimension ORDER BY h.month) AS prev_score,
         lag(h.month) OVER (PARTITION BY h.table_name, h.dimension ORDER BY h.month) AS prev_month
  FROM ${catalog}.silver.dq_score_history h
  WHERE h.month BETWEEN DATE'${history_start}' AND DATE'${as_of_date}'
)
SELECT
  h.month,
  h.table_name,
  h.dimension,
  h.layer,
  h.base_table_name,
  h.score,
  h.rules_evaluated,
  h.rules_failed,
  h.rows_evaluated,
  h.rows_failed,
  h.rows_evaluated - h.rows_failed                                             AS rows_passed,
  h.basis,
  h.incident_key,
  h.incident_note,
  h.storyline                                                                  AS storyline_no,
  h.run_id                                                                     AS dq_run_id,
  h.month = h.last_month                                                       AS is_latest_month,
  CAST(months_between(h.month, h.last_month) AS INT)                           AS month_offset,
  CASE WHEN h.prev_month = add_months(h.month, -1) THEN h.prev_score END       AS prev_month_score,
  CASE WHEN h.prev_month = add_months(h.month, -1) THEN round(h.score - h.prev_score, 6) END AS score_change_vs_prev_month,
  h.score < 0.95                                                               AS is_below_95,
  h.incident_key IS NOT NULL                                                   AS is_incident
FROM h
