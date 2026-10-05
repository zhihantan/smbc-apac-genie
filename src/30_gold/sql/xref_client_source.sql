-- xref_client_source: silver.xref_client_source (latest ER run) + recorded attributes (silver.client_source_record)
-- + the current dim_client version and group.
SELECT
  x.source_system,
  x.source_id,
  x.golden_client_id,
  c.golden_client_sk,
  c.client_group_id,
  x.source_name_as_recorded,
  r.country_as_recorded,
  r.lei_as_recorded,
  x.match_method,
  x.match_rule,
  x.match_score,
  x.steward_decision,
  count(*) OVER (PARTITION BY x.source_system, x.golden_client_id) > 1 AS is_within_source_duplicate,
  r.record_available_from,
  x.resolved_in_run_id,
  x.resolved_at,
  x.run_id                                                             AS er_run_id,
  x.run_date                                                           AS er_run_date
FROM ${catalog}.silver.xref_client_source x
LEFT JOIN ${catalog}.silver.client_source_record r ON r.source_system = x.source_system AND r.source_id = x.source_id
LEFT JOIN ${catalog}.gold.dim_client c ON c.golden_client_id = x.golden_client_id AND c.is_current
