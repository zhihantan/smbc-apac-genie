-- fact_entity_resolution_run: the six quarterly ER runs x identity source system (plus one ALL row per run) from
-- the ER run log ops.entity_resolution_runs (an operational run log, not synthetic truth - WP8f exception; its
-- precision / recall / purity are the run's own evaluation against the synthetic truth, stored by the ER job).
-- Record counts reconcile with silver.er_cluster_membership (checks in the spec).
WITH r AS (
  SELECT *, dense_rank() OVER (ORDER BY run_date) AS run_seq, max(run_date) OVER () AS last_run_date
  FROM ${catalog}.ops.entity_resolution_runs
  WHERE run_date <= DATE'${as_of_date}'
), p AS (
  SELECT r.*,
         lag(precision) OVER (PARTITION BY source_system ORDER BY run_date)    AS prev_precision,
         lag(recall) OVER (PARTITION BY source_system ORDER BY run_date)       AS prev_recall,
         lag(rule_version) OVER (PARTITION BY source_system ORDER BY run_date) AS prev_rule_version
  FROM r
)
SELECT
  run_id,
  run_date,
  rule_version,
  rule_set_note,
  CAST(run_seq AS INT)                                                         AS run_seq,
  run_date = last_run_date                                                     AS is_latest_run,
  coalesce(rule_version <> prev_rule_version, false)                           AS is_rule_version_change,
  source_system,
  source_system = 'ALL'                                                        AS is_all_sources,
  records_in,
  records_new,
  matched_deterministic,
  matched_fuzzy,
  steward_resolved,
  matched_deterministic + matched_fuzzy + steward_resolved                     AS matched_total,
  unmatched,
  round((matched_deterministic + matched_fuzzy + steward_resolved) / nullif(records_in, 0), 6) AS match_rate,
  round(matched_fuzzy / nullif(matched_deterministic + matched_fuzzy + steward_resolved, 0), 6)  AS fuzzy_share,
  round(steward_resolved / nullif(matched_deterministic + matched_fuzzy + steward_resolved, 0), 6) AS steward_share,
  duplicates_merged,
  golden_records,
  records_in - golden_records                                                  AS records_collapsed,
  round(records_in / nullif(golden_records, 0), 4)                             AS records_per_golden,
  golden_new,
  golden_merged,
  golden_split,
  candidate_pairs,
  deterministic_pairs,
  auto_match_pairs,
  steward_band_pairs,
  steward_items,
  steward_items_open,
  links_accepted,
  true_pairs,
  predicted_pairs,
  tp_pairs,
  precision,
  recall,
  purity,
  round(precision - prev_precision, 6)                                         AS precision_change_vs_prev_run,
  round(recall - prev_recall, 6)                                               AS recall_change_vs_prev_run,
  started_at,
  finished_at,
  round((unix_timestamp(finished_at) - unix_timestamp(started_at)) / 60.0, 1)  AS duration_minutes
FROM p
