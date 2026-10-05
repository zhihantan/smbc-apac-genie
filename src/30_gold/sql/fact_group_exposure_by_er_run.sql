-- fact_group_exposure_by_er_run: client group x ER run credit exposure (silver.group_exposure_by_er_run) =
-- drawn lending (credit obligor records) + trade finance outstanding (trade party records) at the run date,
-- attributed to groups through that run's resolution; prior_resolution_exposure re-attributes the same
-- facts with the previous run's resolution, so resolution_change isolates the effect of re-resolution
-- (Meridian: +38% at the Mar-2026 v2 run, crossing the USD 500m attention threshold). Exposure of records no
-- group could be attributed to is kept as one UNATTRIBUTED row per run (client_group_id null).
WITH g AS (
  SELECT e.*, coalesce(e.client_group_id, 'UNATTRIBUTED') AS group_key,
         dense_rank() OVER (ORDER BY e.run_date) AS run_seq, max(e.run_date) OVER () AS last_run_date,
         lag(e.rule_version) OVER (PARTITION BY coalesce(e.client_group_id, 'UNATTRIBUTED') ORDER BY e.run_date) AS prev_rule_version
  FROM ${catalog}.silver.group_exposure_by_er_run e
  WHERE e.run_date <= DATE'${as_of_date}'
), thr AS (
  SELECT max(CASE WHEN threshold_code = 'ER_EXPOSURE_CHANGE' THEN threshold_value END) AS chg,
         max(CASE WHEN threshold_code = 'SINGLE_BORROWER_ATTENTION_USD' THEN threshold_value END) AS attn
  FROM ${catalog}.gold.dim_threshold
)
SELECT
  g.run_id,
  g.group_key,
  g.run_date,
  g.rule_version,
  CAST(g.run_seq AS INT)                                                       AS run_seq,
  g.run_date = g.last_run_date                                                 AS is_latest_run,
  coalesce(g.rule_version <> g.prev_rule_version, false)                       AS is_rule_version_change,
  g.client_group_id,
  dl.golden_client_sk                                                          AS lead_golden_client_sk,
  g.attribution_status,
  g.n_golden_clients,
  g.n_obligor_records,
  g.n_trade_party_records,
  g.lending_drawn_usd,
  g.trade_outstanding_usd,
  g.total_exposure_usd,
  g.prior_resolution_exposure_usd,
  g.resolution_change_usd,
  g.resolution_change_pct,
  g.prev_run_exposure_usd,
  round(g.total_exposure_usd - g.prev_run_exposure_usd, 2)                     AS change_vs_prev_run_usd,
  g.change_vs_prev_run_pct,
  coalesce(g.attention_threshold_usd, thr.attn)                                AS attention_threshold_usd,
  g.is_above_threshold,
  coalesce(g.prior_resolution_exposure_usd >= coalesce(g.attention_threshold_usd, thr.attn), false) AS was_above_threshold_before_resolution,
  g.crossed_threshold_on_resolution,
  coalesce(abs(g.resolution_change_pct) > thr.chg, false)                      AS is_material_resolution_change
FROM g
CROSS JOIN thr
LEFT JOIN ${catalog}.gold.dim_client_group dg ON dg.client_group_id = g.client_group_id
LEFT JOIN ${catalog}.gold.dim_client dl
  ON  dl.golden_client_id = dg.lead_golden_client_id
  AND g.run_date <= dl.valid_to
  AND (g.run_date >= dl.valid_from OR dl.version_no = 1)
