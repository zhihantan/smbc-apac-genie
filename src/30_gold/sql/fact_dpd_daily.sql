-- fact_dpd_daily (WP8d, brief 5.4): daily days-past-due per facility in arrears (silver.core_dpd: one row per
-- facility x day the facility is overdue), with the arrears episode, the missed due date and its cure. A cure
-- dated after the as-of date is not known yet and is shown as null / not cured.
SELECT
  d.facility_id,
  d.dpd_date,
  d.obligor_id,
  dc.golden_client_sk,
  d.golden_client_id,
  d.client_group_id,
  CAST(d.days_past_due AS INT)                                                    AS days_past_due,
  d.dpd_bucket,
  d.days_past_due >= 30                                                           AS is_30_plus_dpd,
  d.days_past_due > 90                                                            AS is_90_plus_dpd,
  d.overdue_amount_usd,
  d.overdue_amount_lcy,
  d.currency,
  d.arrears_episode_id,
  d.due_date                                                                      AS missed_due_date,
  CASE WHEN d.cure_date <= DATE'${as_of_date}' THEN d.cure_date END               AS cure_date,
  coalesce(d.cure_date <= DATE'${as_of_date}', false)                             AS is_episode_cured,
  f.facility_type,
  p.product_id,
  dd.is_month_end
FROM ${catalog}.silver.core_dpd d
JOIN ${catalog}.gold.dim_date dd ON dd.date = d.dpd_date
LEFT JOIN ${catalog}.silver.credit_facility_terms f ON f.facility_id = d.facility_id
LEFT JOIN ${catalog}.gold.dim_product p ON p.product_name = f.facility_type
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = d.golden_client_id
  AND d.dpd_date <= dc.valid_to
  AND (d.dpd_date >= dc.valid_from OR dc.version_no = 1)
WHERE d.dpd_date <= DATE'${as_of_date}'
