-- fact_client_coverage_monthly (WP8c): dense golden client x month-end (Apr-2024 .. Sep-2026, from the month the
-- relationship started) with the primary RM and tier in force (dim_client as-was) and the RM engagement measured
-- from gold.fact_rm_activity: activities in the month, in the trailing 90 days, the last contact and whether the
-- client was touched in the last 90 days - so "no RM contact in 90 days" includes clients with no activity at all.
WITH months AS (
  SELECT month_start_date AS month, date AS month_end_date, fiscal_year, fiscal_year_label, fiscal_quarter_label,
         fiscal_half_label, is_latest_month
  FROM ${catalog}.gold.dim_date
  WHERE is_month_end AND date <= DATE'${as_of_date}'
    AND month_start_date >= (SELECT min(activity_month) FROM ${catalog}.gold.fact_rm_activity)
),
clients AS (      -- coverage starts at the relationship start, or the first RM contact if that came earlier
  SELECT c.golden_client_id,
         CASE WHEN f.first_activity < c.relationship_start_date THEN f.first_activity ELSE c.relationship_start_date END
           AS relationship_start_date
  FROM ${catalog}.gold.dim_client c
  LEFT JOIN (SELECT golden_client_id, min(activity_date) AS first_activity FROM ${catalog}.gold.fact_rm_activity GROUP BY 1) f
    ON f.golden_client_id = c.golden_client_id
  WHERE c.is_current
),
grid AS (
  SELECT c.golden_client_id, m.*
  FROM clients c
  JOIN months m ON c.relationship_start_date IS NULL OR m.month_end_date >= c.relationship_start_date
),
agg AS (
  SELECT g.golden_client_id, g.month,
         count_if(a.activity_date >= g.month) AS activities_in_month,
         count_if(a.activity_date >= g.month AND a.is_in_person) AS in_person_in_month,
         count_if(a.activity_date > date_sub(g.month_end_date, 90)) AS activities_90d,
         count(DISTINCT CASE WHEN a.activity_date > date_sub(g.month_end_date, 90) THEN a.contact_id END) AS contacts_touched_90d,
         count(DISTINCT CASE WHEN a.activity_date > date_sub(g.month_end_date, 90) THEN a.rm_employee_id END) AS rms_engaged_90d,
         sum(CASE WHEN a.activity_date > date_sub(g.month_end_date, 90) THEN a.sentiment_score END) AS sentiment_sum_90d,
         count(CASE WHEN a.activity_date > date_sub(g.month_end_date, 90) THEN a.sentiment_score END) AS sentiment_count_90d,
         max(a.activity_date) AS last_activity_date,
         count_if(a.is_next_action_open) AS open_next_actions
  FROM grid g
  LEFT JOIN ${catalog}.gold.fact_rm_activity a
    ON a.golden_client_id = g.golden_client_id AND a.activity_date <= g.month_end_date
  GROUP BY 1, 2
)
SELECT
  g.month, g.month_end_date, g.fiscal_year, g.fiscal_year_label, g.fiscal_quarter_label, g.fiscal_half_label,
  g.is_latest_month,
  dc.golden_client_sk, g.golden_client_id, dc.client_group_id,
  dc.primary_rm_id, dc.primary_rm_code, dc.primary_rm_office, dc.relationship_tier,
  coalesce(dc.relationship_tier = 'Strategic', false) AS is_strategic,
  array_contains(dc.source_systems_present, 'crm_account') AS has_crm_account,
  a.activities_in_month, a.in_person_in_month, a.activities_90d, a.contacts_touched_90d, a.rms_engaged_90d,
  a.sentiment_sum_90d, a.sentiment_count_90d, try_divide(a.sentiment_sum_90d, a.sentiment_count_90d) AS avg_sentiment_90d,
  a.last_activity_date, datediff(g.month_end_date, a.last_activity_date) AS days_since_last_activity,
  a.activities_90d > 0 AS is_touched_90d,
  a.activities_90d = 0 AS is_not_touched_90d,
  coalesce(dc.relationship_tier = 'Strategic', false) AND a.activities_90d = 0 AS is_strategic_not_touched_90d,
  a.open_next_actions
FROM grid g
JOIN agg a ON a.golden_client_id = g.golden_client_id AND a.month = g.month
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = g.golden_client_id
  AND g.month_end_date <= dc.valid_to
  AND (g.month_end_date >= dc.valid_from OR dc.version_no = 1)
