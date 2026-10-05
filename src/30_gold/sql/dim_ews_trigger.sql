-- dim_ews_trigger: silver.ews_trigger_catalog + firing statistics from silver.ews_signal (to the as-of date).
WITH fired AS (
  SELECT trigger_code,
         avg(points_contributed) AS avg_points, max(points_contributed) AS max_points,
         count(*) AS n, count(DISTINCT golden_client_id) AS clients,
         min(signal_month) AS first_month, max(signal_month) AS last_month
  FROM ${catalog}.silver.ews_signal
  WHERE signal_month <= DATE'${as_of_date}'
  GROUP BY trigger_code
)
SELECT
  c.trigger_code,
  c.trigger_name,
  c.category,
  c.default_severity                                                        AS severity,
  c.threshold_desc                                                          AS threshold_description,
  c.description,
  coalesce(c.is_active, true)                                               AS is_active,
  CASE WHEN c.category = 'External' THEN 'External Qualitative' ELSE 'Internal Quantitative' END AS source_quadrant,
  round(coalesce(f.avg_points, 0), 1)                                       AS score_points,
  round(coalesce(f.max_points, 0), 1)                                       AS max_score_points,
  c.default_severity = 'High'                                               AS auto_watchlist_flag,
  CAST(coalesce(f.n, 0) AS BIGINT)                                          AS signals_fired,
  CAST(coalesce(f.clients, 0) AS BIGINT)                                    AS clients_fired,
  f.first_month                                                             AS first_fired_month,
  f.last_month                                                              AS last_fired_month
FROM ${catalog}.silver.ews_trigger_catalog c
LEFT JOIN fired f ON f.trigger_code = c.trigger_code
