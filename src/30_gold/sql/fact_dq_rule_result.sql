-- fact_dq_rule_result: rule-level results of the DQ runs (silver.dq_results; currently one run, DQ-20260930)
-- with the rule definition from the DQ rule catalogue (ops.dq_rules - operational catalogue, WP8f exception).
-- The run date is the as-of date in the run id (the run's wall-clock timestamp is not used, D16).
WITH r AS (
  SELECT r.*, to_date(regexp_extract(r.run_id, '([0-9]{8})$', 1), 'yyyyMMdd') AS run_date,
         regexp_extract(r.table_name, '^[a-z]+\\.(.+)$', 1) AS base_table_name
  FROM ${catalog}.silver.dq_results r
)
SELECT
  r.run_id,
  r.rule_id,
  r.layer,
  r.run_date,
  trunc(r.run_date, 'MM')                                                      AS month,
  r.table_name,
  r.base_table_name,
  r.column_name,
  r.dimension,
  r.rule_kind,
  d.description                                                                AS rule_description,
  d.rule_expr,
  d.origin                                                                     AS rule_origin,
  d.rule_grain,
  r.severity,
  r.is_blocking,
  r.action,
  r.total_rows,
  r.failed_rows,
  r.total_rows - r.failed_rows                                                 AS passed_rows,
  CASE WHEN r.action = 'deduplicate' THEN r.failed_rows ELSE 0 END             AS deduplicated_rows,
  r.pass_rate,
  r.threshold,
  r.status,
  r.status <> 'pass'                                                           AS is_failed,
  r.status = 'fail'                                                            AS is_hard_fail,
  array_join(r.sample_keys, ', ')                                              AS sample_keys_text,
  r.detail,
  r.storyline                                                                  AS storyline_no,
  r.run_date = max(r.run_date) OVER ()                                         AS is_latest_run
FROM r
LEFT JOIN ${catalog}.ops.dq_rules d ON d.rule_id = r.rule_id
