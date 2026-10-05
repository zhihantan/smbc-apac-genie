-- fact_ews_signal_daily (WP8d, brief 5.4): EWS trigger firings per golden client x evaluation date x trigger
-- (silver.ews_signal). The EWS engine evaluates each credit obligor monthly on month-end data (utilisation,
-- latest covenant test, deposit change, DPD, news, health, grade), so signal_date = the month-end of the signal
-- month (the as-of date for the current month) - no look-ahead into the month. Duplicate credit records of one
-- golden client are collapsed to the strongest firing. preceded_downgrade_90d (D13 precedence flag) = an internal
-- rating downgrade of the client followed within 90 days after the signal date (silver.core_rating_history).
WITH s AS (
  SELECT golden_client_id, client_group_id, obligor_id, signal_month,
         least(last_day(signal_month), DATE'${as_of_date}') AS signal_date,
         trigger_code, category, signal_value, severity, points_contributed, detail,
         CASE severity WHEN 'High' THEN 3 WHEN 'Medium' THEN 2 ELSE 1 END AS severity_rank
  FROM ${catalog}.silver.ews_signal
  WHERE golden_client_id IS NOT NULL AND signal_month <= DATE'${as_of_date}'
),
agg AS (
  SELECT golden_client_id, signal_date, trigger_code,
         max(client_group_id) AS client_group_id, min(signal_month) AS signal_month,
         max_by(named_struct('obligor_id', obligor_id, 'value', signal_value, 'severity', severity, 'points', points_contributed,
                             'detail', detail, 'category', category), struct(points_contributed, severity_rank, obligor_id)) AS top,
         count(*) AS n_obligors_fired
  FROM s GROUP BY golden_client_id, signal_date, trigger_code
),
downgrades AS (
  SELECT golden_client_id, effective_date, notch_change
  FROM ${catalog}.silver.core_rating_history
  WHERE golden_client_id IS NOT NULL AND rating_action = 'Downgrade' AND effective_date <= DATE'${as_of_date}'
),
next_dg AS (
  SELECT a.golden_client_id, a.signal_date, a.trigger_code,
         min_by(named_struct('d', d.effective_date, 'notches', d.notch_change), d.effective_date) AS dg
  FROM agg a JOIN downgrades d ON d.golden_client_id = a.golden_client_id AND d.effective_date > a.signal_date
  GROUP BY a.golden_client_id, a.signal_date, a.trigger_code
),
families AS (   -- trigger family per client-month, for 'deposit outflow AND utilisation spike in the same month'
  SELECT golden_client_id, signal_date,
         bool_or(trigger_code IN ('DEPOSIT_OUTFLOW', 'DEPOSIT_OUTFLOW_SEVERE')) AS has_outflow,
         bool_or(trigger_code IN ('UTIL_HIGH', 'UTIL_ELEVATED'))               AS has_util,
         count(*)                                                               AS n_triggers_client_month
  FROM agg GROUP BY golden_client_id, signal_date
),
expo AS (       -- client drawn exposure at the month-end of the signal
  SELECT golden_client_id, balance_date, sum(drawn_usd) AS drawn_usd, sum(limit_usd) AS limit_usd
  FROM ${catalog}.silver.core_facility_balance_monthly WHERE golden_client_id IS NOT NULL
  GROUP BY golden_client_id, balance_date
)
SELECT
  a.golden_client_id,
  a.signal_date,
  a.trigger_code,
  dc.golden_client_sk,
  a.client_group_id,
  a.top.obligor_id                                                                 AS obligor_id,
  CAST(a.n_obligors_fired AS INT)                                                  AS n_obligors_fired,
  a.signal_month,
  t.trigger_name,
  coalesce(t.category, a.top.category)                                             AS trigger_category,
  CASE WHEN a.trigger_code IN ('UTIL_HIGH', 'UTIL_ELEVATED') THEN 'Utilisation'
       WHEN a.trigger_code IN ('COV_HEADROOM_LOW', 'COV_BREACH') THEN 'Covenant'
       WHEN a.trigger_code IN ('DEPOSIT_OUTFLOW', 'DEPOSIT_OUTFLOW_SEVERE') THEN 'Deposit Outflow'
       WHEN a.trigger_code = 'WALLET_LEAKAGE' THEN 'Wallet Leakage'
       WHEN a.trigger_code = 'HEALTH_DECLINE' THEN 'Credit Health'
       WHEN a.trigger_code = 'GRADE_DOWNGRADE' THEN 'Internal Rating'
       WHEN a.trigger_code = 'NEWS_NEGATIVE' THEN 'Adverse News'
       WHEN a.trigger_code IN ('DPD_15', 'DPD_30') THEN 'Payment Arrears'
       ELSE 'Other' END                                                            AS trigger_family,
  a.top.severity                                                                   AS severity,
  coalesce(t.source_quadrant, 'Internal Quantitative')                             AS source_quadrant,
  a.top.value                                                                      AS signal_value,
  a.top.points                                                                     AS score_points,
  a.trigger_code IN ('DPD_15', 'DPD_30', 'NEWS_NEGATIVE')                          AS is_informational,
  a.top.detail                                                                     AS evidence_text,
  coalesce(e.drawn_usd, 0D)                                                        AS exposure_drawn_usd,
  coalesce(e.limit_usd, 0D)                                                        AS exposure_limit_usd,
  CAST(f.n_triggers_client_month AS INT)                                           AS n_triggers_client_month,
  f.has_outflow AND f.has_util                                                     AS outflow_and_util_same_month,
  coalesce(n.dg.d <= date_add(a.signal_date, 90), false)                           AS preceded_downgrade_90d,
  n.dg.d                                                                           AS next_downgrade_date,
  datediff(n.dg.d, a.signal_date)                                                  AS days_to_next_downgrade,
  CAST(n.dg.notches AS INT)                                                        AS next_downgrade_notches,
  d.fiscal_year                                                                    AS signal_fiscal_year,
  d.is_fytd                                                                        AS is_current_fytd
FROM agg a
JOIN ${catalog}.gold.dim_date d ON d.date = a.signal_date
LEFT JOIN ${catalog}.gold.dim_ews_trigger t ON t.trigger_code = a.trigger_code
LEFT JOIN next_dg n ON n.golden_client_id = a.golden_client_id AND n.signal_date = a.signal_date AND n.trigger_code = a.trigger_code
LEFT JOIN families f ON f.golden_client_id = a.golden_client_id AND f.signal_date = a.signal_date
LEFT JOIN expo e ON e.golden_client_id = a.golden_client_id AND e.balance_date = a.signal_date
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = a.golden_client_id
  AND a.signal_date <= dc.valid_to
  AND (a.signal_date >= dc.valid_from OR dc.version_no = 1)
