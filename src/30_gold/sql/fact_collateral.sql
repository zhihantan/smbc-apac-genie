-- fact_collateral (WP8d, brief 5.3): one row per collateral item (silver.credit_collateral) with LTV, the age of
-- the last valuation at the as-of date and the review flags of the credit memo (LTV above 70%, valuation older
-- than 24 months: dim_threshold LTV_HIGH / VALUATION_STALE_MONTHS), plus the secured facility's terms and
-- drawn balance at the as-of month-end. Current snapshot: golden_client_sk = current dim_client version.
WITH th AS (
  SELECT max(CASE WHEN threshold_code = 'LTV_HIGH' THEN threshold_value END)               AS ltv_high,
         max(CASE WHEN threshold_code = 'VALUATION_STALE_MONTHS' THEN threshold_value END) AS stale_months
  FROM ${catalog}.gold.dim_threshold
),
bal AS (
  SELECT facility_id, drawn_usd FROM ${catalog}.silver.core_facility_balance_monthly
  WHERE balance_date = last_day(DATE'${as_of_date}')
)
SELECT
  c.collateral_id,
  c.facility_id,
  c.obligor_id,
  dc.golden_client_sk,
  c.golden_client_id,
  c.client_group_id,
  DATE'${as_of_date}'                                                             AS as_of_date,
  c.collateral_type,
  c.appraised_value_usd,
  c.ltv_pct,
  c.last_valuation_date,
  CAST(floor(months_between(DATE'${as_of_date}', c.last_valuation_date)) AS INT)  AS valuation_age_months,
  months_between(DATE'${as_of_date}', c.last_valuation_date) > th.stale_months    AS is_valuation_stale,
  c.ltv_pct > th.ltv_high                                                         AS is_high_ltv,
  c.ltv_pct > th.ltv_high OR months_between(DATE'${as_of_date}', c.last_valuation_date) > th.stale_months AS needs_collateral_review,
  f.facility_type,
  f.status                                                                        AS facility_status,
  f.status = 'Active'                                                             AS is_facility_active,
  f.security_type,
  f.limit_usd                                                                     AS facility_limit_usd,
  coalesce(b.drawn_usd, 0D)                                                       AS facility_drawn_usd
FROM ${catalog}.silver.credit_collateral c
CROSS JOIN th
LEFT JOIN ${catalog}.silver.credit_facility_terms f ON f.facility_id = c.facility_id
LEFT JOIN bal b ON b.facility_id = c.facility_id
LEFT JOIN ${catalog}.gold.dim_client dc ON dc.golden_client_id = c.golden_client_id AND dc.is_current
