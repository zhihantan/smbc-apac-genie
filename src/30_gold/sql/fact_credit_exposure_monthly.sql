-- fact_credit_exposure_monthly (WP8d, brief 5.4 / PLAN 7): month-end credit exposure per facility, Apr-2023..Sep-2026.
-- Limits and drawn balances come from silver.core_facility_balance_monthly; EAD, RWA, PD / LGD, ECL (12-month,
-- lifetime and the IFRS 9 stage-based provision) and stage / grade come from silver.fin_capital_allocation
-- (per obligor x month, from Apr-2024) and are allocated to the obligor's facilities pro rata to facility EAD
-- (drawn + CCF x undrawn; equal split when all are zero), so they add up exactly to the obligor totals.
-- Delinquency at the month-end and cures in the month come from silver.core_dpd (cures after the as-of date
-- are not known yet). D13 precomputed flags: dpd_bucket, is_30_plus_dpd, was_overdue_prior_month_end,
-- in_arrears_during_month, is_cured_in_month.
WITH bal AS (
  SELECT facility_id, obligor_id, golden_client_id, client_group_id, balance_date AS month_end_date, currency,
         limit_usd, drawn_usd, drawn_lcy, utilisation_pct
  FROM ${catalog}.silver.core_facility_balance_monthly
  WHERE balance_date BETWEEN DATE'${history_start}' AND DATE'${as_of_date}'
),
cap AS (
  SELECT obligor_id, last_day(month) AS month_end_date, ead_usd, rwa_usd, risk_weight, ccf, pd_12m, pd_lifetime, lgd,
         ecl_12m_usd, ecl_lifetime_usd, ecl_usd, ecl_change_usd, ifrs9_stage, internal_grade, rating_equivalent
  FROM ${catalog}.silver.fin_capital_allocation
),
w AS (
  SELECT b.*, c.ead_usd AS obl_ead_usd, c.rwa_usd AS obl_rwa_usd, c.risk_weight, c.ccf, c.pd_12m, c.pd_lifetime, c.lgd,
         c.ecl_12m_usd AS obl_ecl_12m_usd, c.ecl_lifetime_usd AS obl_ecl_lifetime_usd, c.ecl_usd AS obl_ecl_usd,
         c.ecl_change_usd AS obl_ecl_change_usd, c.ifrs9_stage, c.internal_grade, c.rating_equivalent,
         greatest(b.drawn_usd + coalesce(c.ccf, 0D) * greatest(b.limit_usd - b.drawn_usd, 0D), 0D) AS ead_proxy
  FROM bal b LEFT JOIN cap c ON c.obligor_id = b.obligor_id AND c.month_end_date = b.month_end_date
),
shares AS (
  SELECT w.*,
         CASE WHEN sum(ead_proxy) OVER o > 0 THEN ead_proxy / sum(ead_proxy) OVER o ELSE 1D / count(*) OVER o END AS alloc_share
  FROM w
  WINDOW o AS (PARTITION BY obligor_id, month_end_date)
),
dpd_m AS (      -- arrears seen in the month and the position on the month-end day
  SELECT facility_id, last_day(dpd_date) AS month_end_date,
         max(CASE WHEN dpd_date = last_day(dpd_date) OR dpd_date = DATE'${as_of_date}' THEN days_past_due END)      AS dpd_month_end,
         max(CASE WHEN dpd_date = last_day(dpd_date) OR dpd_date = DATE'${as_of_date}' THEN overdue_amount_usd END) AS overdue_month_end_usd,
         max(days_past_due)                                                                                         AS dpd_max_in_month
  FROM ${catalog}.silver.core_dpd WHERE dpd_date <= DATE'${as_of_date}'
  GROUP BY facility_id, last_day(dpd_date)
),
cures AS (
  SELECT facility_id, last_day(cure_date) AS month_end_date, count(DISTINCT arrears_episode_id) AS n_cures
  FROM ${catalog}.silver.core_dpd WHERE cure_date <= DATE'${as_of_date}'
  GROUP BY facility_id, last_day(cure_date)
),
joined AS (
  SELECT s.*, coalesce(d.dpd_month_end, 0) AS days_past_due, coalesce(d.overdue_month_end_usd, 0D) AS overdue_amount_usd,
         coalesce(d.dpd_max_in_month, 0) AS dpd_max_in_month, d.facility_id IS NOT NULL AS in_arrears_during_month,
         coalesce(cu.n_cures, 0) > 0 AS is_cured_in_month
  FROM shares s
  LEFT JOIN dpd_m d ON d.facility_id = s.facility_id AND d.month_end_date = s.month_end_date
  LEFT JOIN cures cu ON cu.facility_id = s.facility_id AND cu.month_end_date = s.month_end_date
)
SELECT
  j.facility_id,
  j.month_end_date,
  trunc(j.month_end_date, 'MM')                                                      AS month,
  j.obligor_id,
  dc.golden_client_sk,
  j.golden_client_id,
  j.client_group_id,
  f.facility_type,
  p.product_id,
  j.currency,
  j.limit_usd,
  j.drawn_usd,
  j.drawn_lcy,
  greatest(j.limit_usd - j.drawn_usd, 0D)                                            AS undrawn_usd,
  j.utilisation_pct,
  j.obl_ead_usd * j.alloc_share                                                      AS ead_usd,
  j.obl_rwa_usd * j.alloc_share                                                      AS rwa_usd,
  j.risk_weight,
  j.ccf,
  j.pd_12m,
  j.pd_lifetime,
  j.lgd,
  j.obl_ecl_12m_usd * j.alloc_share                                                  AS ecl_12m_usd,
  j.obl_ecl_lifetime_usd * j.alloc_share                                             AS ecl_lifetime_usd,
  j.obl_ecl_usd * j.alloc_share                                                      AS ecl_usd,
  j.obl_ecl_change_usd * j.alloc_share                                               AS ecl_change_usd,
  j.alloc_share                                                                      AS allocation_share,
  CAST(j.ifrs9_stage AS INT)                                                         AS ifrs9_stage,
  CAST(j.internal_grade AS INT)                                                      AS internal_grade,
  j.rating_equivalent,
  CAST(j.days_past_due AS INT)                                                       AS days_past_due,
  CAST(j.dpd_max_in_month AS INT)                                                    AS dpd_max_in_month,
  CASE WHEN j.days_past_due = 0 THEN 'Current' WHEN j.days_past_due < 30 THEN '1-29'
       WHEN j.days_past_due < 60 THEN '30-59' WHEN j.days_past_due < 90 THEN '60-89' ELSE '90+' END AS dpd_bucket,
  j.overdue_amount_usd,
  j.days_past_due > 0                                                                AS is_overdue,
  j.days_past_due >= 30                                                              AS is_30_plus_dpd,
  j.days_past_due > 90                                                               AS is_90_plus_dpd,
  coalesce(lag(j.days_past_due) OVER (PARTITION BY j.facility_id ORDER BY j.month_end_date) > 0, false) AS was_overdue_prior_month_end,
  j.in_arrears_during_month,
  j.is_cured_in_month,
  j.obl_ead_usd IS NOT NULL                                                          AS has_capital_measures
FROM joined j
LEFT JOIN ${catalog}.silver.credit_facility_terms f ON f.facility_id = j.facility_id
LEFT JOIN ${catalog}.gold.dim_product p ON p.product_name = f.facility_type
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = j.golden_client_id
  AND j.month_end_date <= dc.valid_to
  AND (j.month_end_date >= dc.valid_from OR dc.version_no = 1)
