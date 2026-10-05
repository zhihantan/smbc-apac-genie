-- fact_scf_programme_monthly: SCF programmes (silver.scf_programme, gold.dim_scf_programme) at every month-end from
-- Apr-2024 (or the launch month) to the as-of month, as a record_type union (D13): one Programme row per programme and
-- month-end (limit, financed outstanding, utilisation, supplier counts) and one Supplier row per programme supplier
-- (silver.scf_supplier register) and month-end (the supplier's financed outstanding, onboarded / active state and the
-- month's invoice, drawdown, discount and USD-day flows from gold.fact_scf_programme_drawdown). Programme amounts live on
-- Programme rows only and supplier amounts / flows on Supplier rows only; the anchor's version valid at the month-end (D11).
WITH me AS (
  SELECT date AS month_end_date FROM ${catalog}.gold.dim_date
  WHERE is_month_end AND date BETWEEN DATE'2024-04-30' AND DATE'${as_of_date}'
), pm AS (
  SELECT p.programme_id, p.programme_type, upper(p.currency) AS currency, p.limit_usd,
         p.golden_client_id, p.client_group_id, me.month_end_date
  FROM ${catalog}.silver.scf_programme p
  JOIN me ON me.month_end_date >= last_day(p.launch_date)
), seg AS (     -- supplier spend segment (attribute of the drawdown fact)
  SELECT supplier_id, max(supplier_segment) AS supplier_segment FROM ${catalog}.gold.fact_scf_programme_drawdown GROUP BY supplier_id
), sm AS (      -- supplier x month-end state
  SELECT pm.programme_id, pm.month_end_date, s.supplier_id, s.supplier_name, upper(s.supplier_country) AS supplier_country,
         coalesce(sg.supplier_segment, 'Long tail (bottom 50%)') AS supplier_segment,
         coalesce(s.is_onboarded AND s.onboarded_date <= pm.month_end_date, false) AS is_onboarded,
         coalesce(s.routes_to_other_bank, false) AS elsewhere
  FROM pm JOIN ${catalog}.silver.scf_supplier s ON s.programme_id = pm.programme_id
  LEFT JOIN seg sg ON sg.supplier_id = s.supplier_id
), s_os AS (    -- financed and not yet due at the month-end
  SELECT sm.programme_id, sm.month_end_date, sm.supplier_id, sum(d.financed_amount_usd) AS outstanding
  FROM sm JOIN ${catalog}.gold.fact_scf_programme_drawdown d
    ON d.programme_id = sm.programme_id AND d.supplier_id = sm.supplier_id AND d.is_financed
   AND d.financing_date <= sm.month_end_date AND d.due_date > sm.month_end_date
  GROUP BY sm.programme_id, sm.month_end_date, sm.supplier_id
), s_act AS (   -- drew at least once in the 3 months to the month-end
  SELECT DISTINCT sm.programme_id, sm.month_end_date, sm.supplier_id
  FROM sm JOIN ${catalog}.gold.fact_scf_programme_drawdown d
    ON d.programme_id = sm.programme_id AND d.supplier_id = sm.supplier_id AND d.is_financed
   AND d.financing_date <= sm.month_end_date AND d.financing_date > last_day(add_months(sm.month_end_date, -3))
), s_inv12 AS ( -- invoices approved in the 12 months to the month-end
  SELECT DISTINCT sm.programme_id, sm.month_end_date, sm.supplier_id
  FROM sm JOIN ${catalog}.gold.fact_scf_programme_drawdown d
    ON d.programme_id = sm.programme_id AND d.supplier_id = sm.supplier_id
   AND d.approval_date <= sm.month_end_date AND d.approval_date > last_day(add_months(sm.month_end_date, -12))
), appr AS (
  SELECT programme_id, supplier_id, last_day(approval_date) AS month_end_date, count(*) AS n, sum(invoice_amount_usd) AS usd
  FROM ${catalog}.gold.fact_scf_programme_drawdown
  WHERE approval_date <= DATE'${as_of_date}'
  GROUP BY programme_id, supplier_id, last_day(approval_date)
), fin AS (
  SELECT programme_id, supplier_id, last_day(financing_date) AS month_end_date, count(*) AS n, sum(financed_amount_usd) AS usd,
         sum(discount_amount_usd) AS disc, sum(days_financed) AS days, sum(financed_usd_days) AS usd_days
  FROM ${catalog}.gold.fact_scf_programme_drawdown
  WHERE is_financed AND financing_date <= DATE'${as_of_date}'
  GROUP BY programme_id, supplier_id, last_day(financing_date)
), srow AS (
  SELECT sm.*, coalesce(o.outstanding, 0.0) AS outstanding, a.supplier_id IS NOT NULL AS is_active,
         i.supplier_id IS NOT NULL AS is_invoicing,
         coalesce(ap.n, 0) AS appr_n, coalesce(ap.usd, 0.0) AS appr_usd, coalesce(f.n, 0) AS fin_n,
         coalesce(f.usd, 0.0) AS fin_usd, coalesce(f.disc, 0.0) AS disc, coalesce(f.days, 0) AS days,
         coalesce(f.usd_days, 0.0) AS usd_days
  FROM sm
  LEFT JOIN s_os o ON o.programme_id = sm.programme_id AND o.month_end_date = sm.month_end_date AND o.supplier_id = sm.supplier_id
  LEFT JOIN s_act a ON a.programme_id = sm.programme_id AND a.month_end_date = sm.month_end_date AND a.supplier_id = sm.supplier_id
  LEFT JOIN s_inv12 i ON i.programme_id = sm.programme_id AND i.month_end_date = sm.month_end_date AND i.supplier_id = sm.supplier_id
  LEFT JOIN appr ap ON ap.programme_id = sm.programme_id AND ap.month_end_date = sm.month_end_date AND ap.supplier_id = sm.supplier_id
  LEFT JOIN fin f ON f.programme_id = sm.programme_id AND f.month_end_date = sm.month_end_date AND f.supplier_id = sm.supplier_id
), prog AS (    -- programme x month-end totals over its suppliers
  SELECT programme_id, month_end_date, sum(outstanding) AS outstanding, count(*) AS n_register,
         count_if(is_onboarded) AS n_onboarded, count_if(is_active) AS n_active, count_if(is_invoicing) AS n_invoicing
  FROM srow GROUP BY programme_id, month_end_date
), pstate AS (
  SELECT pm.*, coalesce(pr.outstanding, 0.0) AS outstanding, coalesce(pr.n_register, 0) AS n_register,
         coalesce(pr.n_onboarded, 0) AS n_onboarded, coalesce(pr.n_active, 0) AS n_active,
         coalesce(pr.n_invoicing, 0) AS n_invoicing,
         coalesce(pr.outstanding, 0.0) / nullif(pm.limit_usd, 0) AS util
  FROM pm LEFT JOIN prog pr ON pr.programme_id = pm.programme_id AND pr.month_end_date = pm.month_end_date
), th AS (
  SELECT max(CASE WHEN threshold_code = 'SCF_UTILISATION_HIGH' THEN threshold_value END) AS hi,
         max(CASE WHEN threshold_code = 'SCF_UTILISATION_LOW' THEN threshold_value END) AS lo
  FROM ${catalog}.gold.dim_threshold
), u AS (
  SELECT 'Programme' AS record_type, 'PROGRAMME' AS record_key, p.programme_id, p.month_end_date,
         p.limit_usd, p.outstanding AS financed_outstanding_usd, p.util AS utilisation,
         CAST(p.n_register AS INT) AS suppliers_in_register, CAST(p.n_invoicing AS INT) AS suppliers_invoicing_12m,
         CAST(p.n_onboarded AS INT) AS suppliers_onboarded, CAST(p.n_active AS INT) AS suppliers_active_3m,
         p.n_active / nullif(p.n_register, 0) AS supplier_activation_rate,
         CAST(NULL AS STRING) AS supplier_id, CAST(NULL AS STRING) AS supplier_name, CAST(NULL AS STRING) AS supplier_country,
         CAST(NULL AS STRING) AS supplier_segment, CAST(NULL AS BOOLEAN) AS is_supplier_onboarded,
         CAST(NULL AS BOOLEAN) AS is_supplier_active_3m, CAST(NULL AS BOOLEAN) AS supplier_routes_to_other_bank,
         CAST(NULL AS DOUBLE) AS supplier_financed_outstanding_usd,
         0 AS appr_n, 0.0 AS appr_usd, 0 AS fin_n, 0.0 AS fin_usd, 0.0 AS disc, CAST(0 AS BIGINT) AS days, 0.0 AS usd_days
  FROM pstate p
  UNION ALL
  SELECT 'Supplier', s.supplier_id, s.programme_id, s.month_end_date,
         CAST(NULL AS DOUBLE), CAST(NULL AS DOUBLE), CAST(NULL AS DOUBLE),
         CAST(NULL AS INT), CAST(NULL AS INT), CAST(NULL AS INT), CAST(NULL AS INT), CAST(NULL AS DOUBLE),
         s.supplier_id, s.supplier_name, s.supplier_country, s.supplier_segment, s.is_onboarded, s.is_active, s.elsewhere,
         s.outstanding, s.appr_n, s.appr_usd, s.fin_n, s.fin_usd, s.disc, CAST(s.days AS BIGINT), s.usd_days
  FROM srow s
)
SELECT
  u.programme_id,
  u.month_end_date,
  u.record_key,
  u.record_type,
  trunc(u.month_end_date, 'MM')                                               AS month,
  dc.golden_client_sk,
  pm.golden_client_id,
  coalesce(pm.client_group_id, dp.client_group_id, dc.client_group_id)       AS client_group_id,
  dp.programme_name,
  pm.programme_type,
  pd.product_id,
  dp.anchor_country,
  pm.currency,
  u.limit_usd,
  u.financed_outstanding_usd,
  u.utilisation,
  CASE WHEN ps.util > th.hi THEN 'Above 85%' WHEN ps.util < th.lo THEN 'Below 40%' ELSE '40-85%' END AS utilisation_band,
  coalesce(ps.util > th.hi, false)                                            AS is_above_85pct,
  coalesce(ps.util < th.lo, false)                                            AS is_below_40pct,
  u.suppliers_in_register,
  u.suppliers_invoicing_12m,
  u.suppliers_onboarded,
  u.suppliers_active_3m,
  u.supplier_activation_rate,
  u.supplier_id,
  u.supplier_name,
  u.supplier_country,
  u.supplier_segment,
  u.is_supplier_onboarded,
  u.is_supplier_active_3m,
  u.supplier_routes_to_other_bank,
  u.supplier_financed_outstanding_usd,
  CAST(u.appr_n AS INT)                                                       AS invoices_approved_in_month,
  u.appr_usd                                                                  AS invoice_value_approved_in_month_usd,
  CAST(u.fin_n AS INT)                                                        AS drawdowns_in_month,
  u.fin_usd                                                                   AS financed_in_month_usd,
  u.disc                                                                      AS discount_income_in_month_usd,
  u.days                                                                      AS days_financed_in_month,
  u.usd_days                                                                  AS financed_usd_days_in_month,
  u.month_end_date = last_day(DATE'${as_of_date}')                            AS is_latest_month_end
FROM u
CROSS JOIN th
JOIN pm ON pm.programme_id = u.programme_id AND pm.month_end_date = u.month_end_date
JOIN pstate ps ON ps.programme_id = u.programme_id AND ps.month_end_date = u.month_end_date
LEFT JOIN ${catalog}.gold.dim_scf_programme dp ON dp.programme_id = u.programme_id
LEFT JOIN ${catalog}.gold.dim_product pd ON pd.product_name = pm.programme_type
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = pm.golden_client_id
  AND u.month_end_date <= dc.valid_to
  AND (u.month_end_date >= dc.valid_from OR dc.version_no = 1)
