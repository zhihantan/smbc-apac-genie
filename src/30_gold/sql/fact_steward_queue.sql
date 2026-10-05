-- fact_steward_queue: data-steward review items raised by the ER runs (silver.steward_queue: candidate pair in
-- the 0.75-0.90 score band, simulated decision). The item is attached to the current golden client of its left
-- record (silver.xref_client_source, latest run) so the standard client block works; aging buckets are on
-- days_open (open items: age at the as-of date; decided items: days to decision).
WITH q AS (
  SELECT * FROM ${catalog}.silver.steward_queue WHERE created_date <= DATE'${as_of_date}'
), x AS (
  SELECT source_system, source_id, golden_client_id FROM ${catalog}.silver.xref_client_source
)
SELECT
  q.steward_item_id,
  q.run_id,
  q.run_date,
  q.rule_version,
  q.created_date,
  trunc(q.created_date, 'MM')                                                  AS month,
  dc.golden_client_sk,
  xl.golden_client_id,
  dc.client_group_id,
  xr.golden_client_id                                                          AS right_golden_client_id,
  coalesce(xl.golden_client_id = xr.golden_client_id, false)                   AS is_same_golden_now,
  q.left_source_system,
  q.left_source_id,
  q.left_source_name,
  q.right_source_system,
  q.right_source_id,
  q.right_source_name,
  q.match_score,
  q.candidate_pairs,
  q.left_cluster_records,
  q.right_cluster_records,
  q.assigned_to                                                                AS assigned_to_employee_id,
  q.decided_date,
  CASE q.decision WHEN 'match' THEN 'Match' WHEN 'no_match' THEN 'No Match' END AS decision,
  q.status,
  q.status = 'Open'                                                            AS is_open,
  coalesce(q.is_open_at_as_of, false)                                          AS is_open_at_as_of,
  q.status = 'Decided'                                                         AS is_decided,
  coalesce(q.decision = 'match', false)                                        AS is_match_decision,
  q.days_open,
  CASE WHEN q.days_open <= 7 THEN '0-7 days' WHEN q.days_open <= 30 THEN '8-30 days'
       WHEN q.days_open <= 90 THEN '31-90 days' WHEN q.days_open <= 180 THEN '91-180 days'
       ELSE 'Over 180 days' END                                                AS aging_bucket,
  CASE WHEN q.days_open <= 7 THEN 1 WHEN q.days_open <= 30 THEN 2 WHEN q.days_open <= 90 THEN 3
       WHEN q.days_open <= 180 THEN 4 ELSE 5 END                               AS aging_bucket_order,
  datediff(q.decided_date, q.created_date)                                     AS days_to_decision,
  q.applied_in_run_id,
  run_date = max(q.run_date) OVER ()                                           AS is_latest_run
FROM q
LEFT JOIN x xl ON xl.source_system = q.left_source_system AND xl.source_id = q.left_source_id
LEFT JOIN x xr ON xr.source_system = q.right_source_system AND xr.source_id = q.right_source_id
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = xl.golden_client_id
  AND q.created_date <= dc.valid_to
  AND (q.created_date >= dc.valid_from OR dc.version_no = 1)
