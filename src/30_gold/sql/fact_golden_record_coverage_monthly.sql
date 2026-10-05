-- fact_golden_record_coverage_monthly: golden client x month x identity source system (silver.golden_record_
-- coverage_monthly, dense: 7 rows per client-month) - is the client present in the source, how many records,
-- when last updated, source field completeness - plus the golden record's attribute completeness at the as-of
-- date (silver.golden_attribute_completeness; current snapshot, filled on the latest month only).
WITH c AS (
  SELECT c.*,
         row_number() OVER (PARTITION BY c.month, c.golden_client_id ORDER BY c.source_system) = 1 AS is_client_primary_row,
         max(c.month) OVER () AS last_month
  FROM ${catalog}.silver.golden_record_coverage_monthly c
  WHERE c.month BETWEEN DATE'${history_start}' AND DATE'${as_of_date}'
), a AS (
  SELECT golden_client_id, CAST(attribute_completeness_pct AS DOUBLE) AS attribute_completeness_pct,
         n_attributes_populated, n_attributes, array_join(missing_attributes, ', ') AS missing_attributes_text
  FROM ${catalog}.silver.golden_attribute_completeness
)
SELECT
  c.month,
  c.golden_client_id,
  c.source_system,
  c.month_end_date,
  dc.golden_client_sk,
  c.client_group_id,
  c.is_present,
  NOT c.is_present                                                             AS is_missing,
  c.n_records,
  c.first_available_date,
  c.last_record_added_date,
  CAST(c.source_field_completeness AS DOUBLE)                                  AS source_field_completeness,
  c.is_core_source,
  c.n_sources_present,
  c.in_all_core_sources,
  c.months_present,
  c.is_client_primary_row,
  c.month = c.last_month                                                       AS is_latest_month,
  CASE WHEN c.month = c.last_month THEN a.attribute_completeness_pct END       AS attribute_completeness_pct,
  CASE WHEN c.month = c.last_month THEN a.n_attributes_populated END           AS n_attributes_populated,
  CASE WHEN c.month = c.last_month THEN a.n_attributes END                     AS n_attributes,
  CASE WHEN c.month = c.last_month THEN a.missing_attributes_text END          AS missing_attributes_text
FROM c
LEFT JOIN a ON a.golden_client_id = c.golden_client_id
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = c.golden_client_id
  AND c.month_end_date <= dc.valid_to
  AND (c.month_end_date >= dc.valid_from OR dc.version_no = 1)
