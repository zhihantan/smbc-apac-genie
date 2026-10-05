-- mvb_facility_risk (WP8d, metric-view base for mv_facilities_covenants_collateral, D13): record_type union of
--   'Facility'      one row per facility (gold.fact_facility_terms): limit, drawn, undrawn, margin  [is_primary_row]
--   'Covenant Test' one row per covenant test (gold.fact_covenant_test): headroom, breach, waiver, latest flag
--   'Collateral'    one row per collateral item (gold.fact_collateral): value, LTV, valuation age
-- Facility-level descriptors repeat on every row (dimensions); every amount / flag lives only on the row type that
-- owns it (null elsewhere), so SUM / MIN / COUNT_IF over the whole base never double counts.
WITH f AS (SELECT * FROM ${catalog}.gold.fact_facility_terms),
fac AS (
  SELECT facility_id, facility_type, facility_status, is_active, security_type, guarantor_type, has_parent_support,
         product_id, currency, jp_parent_id, parent_rating_grade, parent_rating_equivalent, parent_rating_outlook
  FROM f
)
SELECT
  'Facility'                          AS record_type,
  f.facility_id                       AS record_id,
  true                                AS is_primary_row,
  f.as_of_date                        AS record_date,
  f.facility_id, f.obligor_id, f.golden_client_sk, f.golden_client_id, f.client_group_id,
  f.facility_type, f.facility_status, f.is_active, f.security_type, f.guarantor_type, f.has_parent_support,
  f.product_id, f.currency, f.jp_parent_id, f.parent_rating_grade, f.parent_rating_equivalent, f.parent_rating_outlook,
  f.limit_usd, f.drawn_usd, f.undrawn_usd, f.utilisation_pct, CAST(f.margin_bps AS DOUBLE) AS margin_bps,
  f.margin_bps * f.limit_usd          AS margin_x_limit_usd,
  f.maturity_date, f.matures_within_12m, f.has_waiver_current_fy,
  CAST(NULL AS STRING) AS covenant_type, CAST(NULL AS STRING) AS covenant_category, CAST(NULL AS DATE) AS test_date,
  CAST(NULL AS STRING) AS test_basis, CAST(NULL AS DOUBLE) AS covenant_threshold, CAST(NULL AS DOUBLE) AS covenant_actual,
  CAST(NULL AS DOUBLE) AS headroom_pct, CAST(NULL AS STRING) AS headroom_bucket, CAST(NULL AS BOOLEAN) AS is_breached,
  CAST(NULL AS BOOLEAN) AS is_tight_headroom, CAST(NULL AS BOOLEAN) AS is_waived, CAST(NULL AS BOOLEAN) AS is_latest_test,
  CAST(NULL AS BOOLEAN) AS is_current_fy_test, CAST(NULL AS DOUBLE) AS exposure_at_test_usd,
  CAST(NULL AS BOOLEAN) AS client_on_watchlist_as_of,
  CAST(NULL AS STRING) AS collateral_id, CAST(NULL AS STRING) AS collateral_type, CAST(NULL AS DOUBLE) AS collateral_value_usd,
  CAST(NULL AS DOUBLE) AS ltv_pct, CAST(NULL AS DATE) AS last_valuation_date, CAST(NULL AS INT) AS valuation_age_months,
  CAST(NULL AS BOOLEAN) AS is_high_ltv, CAST(NULL AS BOOLEAN) AS is_valuation_stale
FROM f
UNION ALL
SELECT
  'Covenant Test', concat(t.facility_id, '|', t.covenant_type, '|', CAST(t.test_date AS STRING)), false, t.test_date,
  t.facility_id, t.obligor_id, t.golden_client_sk, t.golden_client_id, t.client_group_id,
  x.facility_type, x.facility_status, x.is_active, x.security_type, x.guarantor_type, x.has_parent_support,
  x.product_id, x.currency, x.jp_parent_id, x.parent_rating_grade, x.parent_rating_equivalent, x.parent_rating_outlook,
  NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
  t.covenant_type, t.covenant_category, t.test_date, t.test_basis, t.threshold, t.actual, t.headroom_pct, t.headroom_bucket,
  t.is_breached, t.is_tight_headroom, t.is_waived, t.is_latest_test, t.is_current_fy_test, t.drawn_usd_at_test,
  t.client_on_watchlist_as_of,
  NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
FROM ${catalog}.gold.fact_covenant_test t
LEFT JOIN fac x ON x.facility_id = t.facility_id
UNION ALL
SELECT
  'Collateral', c.collateral_id, false, c.last_valuation_date,
  c.facility_id, c.obligor_id, c.golden_client_sk, c.golden_client_id, c.client_group_id,
  x.facility_type, x.facility_status, x.is_active, x.security_type, x.guarantor_type, x.has_parent_support,
  x.product_id, x.currency, x.jp_parent_id, x.parent_rating_grade, x.parent_rating_equivalent, x.parent_rating_outlook,
  NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
  NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
  c.collateral_id, c.collateral_type, c.appraised_value_usd, c.ltv_pct, c.last_valuation_date, c.valuation_age_months,
  c.is_high_ltv, c.is_valuation_stale
FROM ${catalog}.gold.fact_collateral c
LEFT JOIN fac x ON x.facility_id = c.facility_id
