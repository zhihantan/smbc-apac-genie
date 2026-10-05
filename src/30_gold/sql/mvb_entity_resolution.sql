-- mvb_entity_resolution: metric-view base for mv_entity_resolution (D13 record_type union, no double counting):
--   source_record - one row per ER run x source record (silver.er_cluster_membership): match method / rule /
--                   score, 0/1 counters, golden-record primary row; attached to the record's CURRENT golden client
--                   (latest-run xref) so segment / coverage office work for every run;
--   run_source    - one row per run x source system + ALL (gold.fact_entity_resolution_run): pairwise quality vs
--                   the synthetic truth (tp / predicted / true pairs, precision, recall, purity);
--   steward_item  - one row per steward review item (gold.fact_steward_queue): decision, status, aging.
-- Counter columns are 1 only on their own record type (0 elsewhere), so plain SUMs never mix record types.
WITH x AS (
  SELECT source_system, source_id, golden_client_id FROM ${catalog}.silver.xref_client_source
), runs AS (
  SELECT run_id, run_seq, is_latest_run FROM ${catalog}.gold.fact_entity_resolution_run WHERE source_system = 'ALL'
), mem AS (
  SELECT m.*, x.golden_client_id AS current_golden_client_id,
         row_number() OVER (PARTITION BY m.run_id, m.golden_client_id ORDER BY m.source_system, m.source_id) = 1 AS is_golden_primary,
         row_number() OVER (PARTITION BY m.run_id, m.golden_client_id, m.source_system ORDER BY m.source_id) > 1 AS is_dup
  FROM ${catalog}.silver.er_cluster_membership m
  LEFT JOIN x ON x.source_system = m.source_system AND x.source_id = m.source_id
  WHERE m.run_date <= DATE'${as_of_date}'
), u AS (
  SELECT concat('REC|', m.run_id, '|', m.source_system, '|', m.source_id) AS mvb_row_id, 'source_record' AS record_type,
         m.run_id, m.run_date, m.rule_version, m.source_system, false AS is_all_sources_row,
         m.current_golden_client_id AS golden_client_id, m.golden_client_id AS run_golden_client_id, m.source_id,
         m.match_method, m.match_rule, m.match_score, m.cluster_size,
         1 AS record_count,
         CASE WHEN m.match_method <> 'unmatched' THEN 1 ELSE 0 END AS matched_count,
         CASE WHEN m.match_method = 'deterministic' THEN 1 ELSE 0 END AS deterministic_count,
         CASE WHEN m.match_method = 'fuzzy' THEN 1 ELSE 0 END AS fuzzy_count,
         CASE WHEN m.match_method = 'steward' THEN 1 ELSE 0 END AS steward_resolved_count,
         CASE WHEN m.match_method = 'unmatched' THEN 1 ELSE 0 END AS unmatched_count,
         CASE WHEN m.is_dup THEN 1 ELSE 0 END AS duplicate_merged_count,
         CASE WHEN m.is_new_record THEN 1 ELSE 0 END AS new_record_count,
         CASE WHEN m.is_golden_primary THEN 1 ELSE 0 END AS golden_record_count,
         0 AS steward_item_count, 0 AS steward_decided_count, 0 AS steward_open_count, 0 AS steward_match_count,
         CAST(NULL AS STRING) AS steward_item_id, CAST(NULL AS STRING) AS steward_decision, CAST(NULL AS STRING) AS steward_status,
         CAST(NULL AS INT) AS days_open, CAST(NULL AS STRING) AS aging_bucket, CAST(NULL AS INT) AS aging_bucket_order,
         CAST(NULL AS STRING) AS assigned_to_employee_id,
         CAST(NULL AS BIGINT) AS true_pairs, CAST(NULL AS BIGINT) AS predicted_pairs, CAST(NULL AS BIGINT) AS tp_pairs,
         CAST(NULL AS BIGINT) AS candidate_pairs, CAST(NULL AS BIGINT) AS run_golden_records,
         CAST(NULL AS DOUBLE) AS precision, CAST(NULL AS DOUBLE) AS recall, CAST(NULL AS DOUBLE) AS purity,
         m.run_date AS attr_date
  FROM mem m
  UNION ALL
  SELECT concat('RUN|', r.run_id, '|', r.source_system), 'run_source',
         r.run_id, r.run_date, r.rule_version, r.source_system, r.is_all_sources,
         CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING),
         CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS DOUBLE), CAST(NULL AS INT),
         0, 0, 0, 0, 0, 0, 0, 0, 0,
         0, 0, 0, 0,
         CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING),
         CAST(NULL AS INT), CAST(NULL AS STRING), CAST(NULL AS INT), CAST(NULL AS STRING),
         r.true_pairs, r.predicted_pairs, r.tp_pairs, r.candidate_pairs, r.golden_records,
         r.precision, r.recall, r.purity,
         r.run_date
  FROM ${catalog}.gold.fact_entity_resolution_run r
  UNION ALL
  SELECT concat('STW|', s.steward_item_id), 'steward_item',
         s.run_id, s.run_date, s.rule_version, s.left_source_system, false,
         s.golden_client_id, CAST(NULL AS STRING), s.left_source_id,
         CAST(NULL AS STRING), CAST(NULL AS STRING), s.match_score, CAST(NULL AS INT),
         0, 0, 0, 0, 0, 0, 0, 0, 0,
         1, CASE WHEN s.is_decided THEN 1 ELSE 0 END, CASE WHEN s.is_open_at_as_of THEN 1 ELSE 0 END,
         CASE WHEN s.is_match_decision THEN 1 ELSE 0 END,
         s.steward_item_id, s.decision, s.status,
         s.days_open, s.aging_bucket, s.aging_bucket_order, s.assigned_to_employee_id,
         CAST(NULL AS BIGINT), CAST(NULL AS BIGINT), CAST(NULL AS BIGINT), CAST(NULL AS BIGINT), CAST(NULL AS BIGINT),
         CAST(NULL AS DOUBLE), CAST(NULL AS DOUBLE), CAST(NULL AS DOUBLE),
         s.created_date
  FROM ${catalog}.gold.fact_steward_queue s
)
SELECT
  u.*,
  rs.run_seq,
  rs.is_latest_run,
  dc.golden_client_sk,
  dc.client_group_id
FROM u
LEFT JOIN runs rs ON rs.run_id = u.run_id
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = u.golden_client_id
  AND u.attr_date <= dc.valid_to
  AND (u.attr_date >= dc.valid_from OR dc.version_no = 1)
