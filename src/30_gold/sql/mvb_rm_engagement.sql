-- mvb_rm_engagement (WP8c, D13): metric-view base for mv_rm_engagement - a record_type union of
--   'activity' rows (one per CRM activity, from gold.fact_rm_activity) and
--   'coverage' rows (one per golden client x month-end, from gold.fact_client_coverage_monthly)
-- so activity measures and the "not touched in 90 days" coverage measures share one source without double counting.
SELECT
  'activity' AS record_type, a.activity_id AS record_id,
  a.activity_month AS month, a.activity_date, last_day(a.activity_month) AS month_end_date,
  a.fiscal_year, a.fiscal_year_label, a.fiscal_quarter_label, a.fiscal_half_label,
  last_day(a.activity_month) = last_day(DATE'${as_of_date}') AS is_latest_month,
  a.golden_client_sk, a.golden_client_id, a.client_group_id,
  a.rm_employee_id, a.rm_office, dc.relationship_tier, coalesce(dc.relationship_tier = 'Strategic', false) AS is_strategic,
  true AS has_crm_account,
  a.activity_type, a.contact_role, a.purpose, a.product_family, a.is_in_person,
  1 AS activity_count, a.sentiment_score, a.is_next_action_open, a.is_next_action_overdue,
  0 AS client_month_count, CAST(NULL AS BOOLEAN) AS is_touched_90d, CAST(NULL AS BOOLEAN) AS is_not_touched_90d,
  CAST(NULL AS BOOLEAN) AS is_strategic_not_touched_90d, CAST(NULL AS INT) AS days_since_last_activity,
  CAST(NULL AS BIGINT) AS activities_90d
FROM ${catalog}.gold.fact_rm_activity a
LEFT JOIN ${catalog}.gold.dim_client dc ON dc.golden_client_sk = a.golden_client_sk
UNION ALL
SELECT
  'coverage', concat(c.golden_client_id, '|', CAST(c.month AS STRING)),
  c.month, CAST(NULL AS DATE), c.month_end_date,
  c.fiscal_year, c.fiscal_year_label, c.fiscal_quarter_label, c.fiscal_half_label,
  c.is_latest_month,
  c.golden_client_sk, c.golden_client_id, c.client_group_id,
  c.primary_rm_id, c.primary_rm_office, c.relationship_tier, c.is_strategic,
  c.has_crm_account,
  CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS BOOLEAN),
  0, CAST(NULL AS DOUBLE), CAST(NULL AS BOOLEAN), CAST(NULL AS BOOLEAN),
  1, c.is_touched_90d, c.is_not_touched_90d, c.is_strategic_not_touched_90d, c.days_since_last_activity,
  c.activities_90d
FROM ${catalog}.gold.fact_client_coverage_monthly c
