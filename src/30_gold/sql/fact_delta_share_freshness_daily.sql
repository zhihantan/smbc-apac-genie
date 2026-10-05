-- fact_delta_share_freshness_daily: daily freshness of every table received over Delta Sharing from the JP / EMEA /
-- AMER lakehouses (silver.dq_share_refresh_log): last refresh, lag in hours vs the 24h SLA, stale flag, row count,
-- schema drift; plus the running count of consecutive stale days and the as-of windows.
WITH s AS (
  SELECT s.*,
         lag(s.row_count) OVER (PARTITION BY s.table_name ORDER BY s.log_date) AS prev_row_count,
         max(s.log_date) OVER (PARTITION BY s.table_name) AS last_log_date,
         -- island id for runs of consecutive stale / fresh days
         row_number() OVER (PARTITION BY s.table_name ORDER BY s.log_date)
           - row_number() OVER (PARTITION BY s.table_name, s.is_stale ORDER BY s.log_date) AS grp
  FROM ${catalog}.silver.dq_share_refresh_log s
  WHERE s.log_date BETWEEN DATE'${history_start}' AND DATE'${as_of_date}'
), thr AS (
  SELECT max(CASE WHEN threshold_code = 'SHARE_STALE_HOURS' THEN threshold_value END) AS stale_h FROM ${catalog}.gold.dim_threshold
)
SELECT
  s.log_date,
  s.table_name,
  trunc(s.log_date, 'MM')                                                      AS month,
  s.provider_region,
  s.share_name,
  s.checked_at,
  s.scheduled_refresh_at,
  s.refresh_status,
  s.refreshed_at                                                               AS last_refreshed_at,
  s.provider_version,
  s.lag_hours,
  s.sla_hours,
  s.is_stale,
  s.lag_hours > thr.stale_h                                                    AS is_over_24h,
  round(greatest(s.lag_hours - s.sla_hours, 0), 2)                             AS hours_over_sla,
  CASE WHEN s.is_stale THEN CAST(row_number() OVER (PARTITION BY s.table_name, s.is_stale, s.grp ORDER BY s.log_date) AS INT)
       ELSE 0 END                                                              AS consecutive_stale_days,
  s.row_count,
  s.row_count - s.prev_row_count                                               AS row_count_change,
  s.schema_version,
  s.schema_drift_flag,
  s.schema_change,
  s.error_message,
  s.log_date = s.last_log_date                                                 AS is_latest_day,
  datediff(DATE'${as_of_date}', s.log_date)                                    AS days_before_as_of,
  datediff(DATE'${as_of_date}', s.log_date) < 30                               AS is_last_30d
FROM s CROSS JOIN thr
