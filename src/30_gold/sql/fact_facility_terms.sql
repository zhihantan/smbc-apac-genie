-- fact_facility_terms (WP8d, brief 5.3): one row per credit facility with its terms (silver.credit_facility_terms),
-- the drawn balance at the as-of month-end (silver.core_facility_balance_monthly), a covenant summary at the
-- latest test on or before the as-of date (silver.credit_covenant_test), the collateral held
-- (silver.credit_collateral) and, for parent-supported facilities, the JP-share support letter and the Japanese
-- parent's current rating (silver.share_jp_support_letters / silver.share_jp_parent_rating).
-- Current snapshot: golden_client_sk = the client's current dim_client version.
WITH f AS (
  SELECT * FROM ${catalog}.silver.credit_facility_terms
),
asof AS (
  SELECT date AS as_of_date, fiscal_year_start_date FROM ${catalog}.gold.dim_date WHERE date = DATE'${as_of_date}'
),
bal_now AS (    -- balance at the as-of month-end (facilities repaid / matured earlier have none -> drawn 0)
  SELECT facility_id, drawn_usd, drawn_lcy, limit_usd AS balance_limit_usd, utilisation_pct
  FROM ${catalog}.silver.core_facility_balance_monthly WHERE balance_date = last_day(DATE'${as_of_date}')
),
bal_last AS (
  SELECT facility_id, max(balance_date) AS last_balance_date
  FROM ${catalog}.silver.core_facility_balance_monthly WHERE balance_date <= DATE'${as_of_date}' GROUP BY facility_id
),
tests AS (
  SELECT t.*, row_number() OVER (PARTITION BY facility_id, covenant_type ORDER BY test_date DESC) = 1 AS is_latest
  FROM ${catalog}.silver.credit_covenant_test t WHERE test_date <= DATE'${as_of_date}'
),
cov AS (
  SELECT t.facility_id,
         concat_ws(', ', sort_array(collect_set(t.covenant_type)))                            AS covenant_types,
         count(DISTINCT t.covenant_type)                                                       AS n_covenants,
         max(t.test_date)                                                                      AS last_test_date,
         min(t.headroom_pct) FILTER (WHERE t.is_latest)                                        AS min_headroom_latest,
         min(t.headroom_pct) FILTER (WHERE t.is_latest AND t.covenant_type = 'Net Debt/EBITDA') AS leverage_headroom_latest,
         max(t.actual) FILTER (WHERE t.is_latest AND t.covenant_type = 'Net Debt/EBITDA')      AS leverage_actual_latest,
         max(t.threshold) FILTER (WHERE t.is_latest AND t.covenant_type = 'Net Debt/EBITDA')   AS leverage_threshold_latest,
         coalesce(bool_or(t.breached) FILTER (WHERE t.is_latest), false)                       AS breached_latest,
         count_if(t.waiver)                                                                    AS n_waivers,
         count_if(t.waiver AND t.test_date >= a.fiscal_year_start_date)                        AS n_waivers_current_fy
  FROM tests t CROSS JOIN asof a
  GROUP BY t.facility_id
),
col AS (
  SELECT facility_id, count(*) AS n_collateral, sum(appraised_value_usd) AS collateral_value_usd,
         max(ltv_pct) AS max_ltv_pct, min(last_valuation_date) AS oldest_valuation_date
  FROM ${catalog}.silver.credit_collateral GROUP BY facility_id
),
letters AS (    -- JP-share support letters: parent guarantees cover a facility, keepwells cover the subsidiary
  SELECT letter_id, support_type, status, issue_date, expiry_date, last_confirmed_date, support_amount_usd,
         covered_facility_id, golden_client_id, jp_parent_id,
         CASE status WHEN 'Active' THEN 1 ELSE 0 END AS is_active
  FROM ${catalog}.silver.share_jp_support_letters
),
pg_letter AS (
  SELECT covered_facility_id AS facility_id,
         max_by(named_struct('id', letter_id, 'type', support_type, 'status', status, 'confirmed', last_confirmed_date,
                             'amount', support_amount_usd, 'parent', jp_parent_id), struct(is_active, issue_date, letter_id)) AS l
  FROM letters WHERE support_type = 'Parent Guarantee' AND covered_facility_id IS NOT NULL GROUP BY covered_facility_id
),
kw_letter AS (
  SELECT golden_client_id,
         max_by(named_struct('id', letter_id, 'type', support_type, 'status', status, 'confirmed', last_confirmed_date,
                             'amount', support_amount_usd, 'parent', jp_parent_id), struct(is_active, issue_date, letter_id)) AS l
  FROM letters WHERE support_type = 'Keepwell' AND golden_client_id IS NOT NULL GROUP BY golden_client_id
),
parent AS (     -- the Japanese parent's rating in force at the as-of date (Tokyo HO share)
  SELECT client_group_id,
         max_by(named_struct('parent', jp_parent_id, 'grade', grade_to, 'equiv', rating_equivalent, 'outlook', outlook,
                             'action', rating_action, 'date', rating_date), struct(rating_date, rating_id)) AS p
  FROM ${catalog}.silver.share_jp_parent_rating
  WHERE rating_date <= DATE'${as_of_date}' AND client_group_id IS NOT NULL
  GROUP BY client_group_id
)
SELECT
  f.facility_id,
  f.obligor_id,
  dc.golden_client_sk,
  f.golden_client_id,
  f.client_group_id,
  a.as_of_date,
  f.facility_type,
  p.product_id,
  f.status                                                                AS facility_status,
  f.status = 'Active'                                                     AS is_active,
  f.currency,
  f.limit_usd,
  coalesce(b.drawn_usd, 0D)                                               AS drawn_usd,
  coalesce(b.drawn_lcy, 0D)                                               AS drawn_lcy,
  CASE WHEN f.status = 'Active' THEN greatest(f.limit_usd - coalesce(b.drawn_usd, 0D), 0D) ELSE 0D END AS undrawn_usd,
  CASE WHEN f.limit_usd > 0 THEN coalesce(b.drawn_usd, 0D) / f.limit_usd END AS utilisation_pct,
  bl.last_balance_date,
  CAST(f.margin_bps AS INT)                                               AS margin_bps,
  f.origination_date,
  f.maturity_date,
  f.closed_date,
  CASE WHEN f.status = 'Active' THEN datediff(f.maturity_date, a.as_of_date) END AS days_to_maturity,
  CASE WHEN f.status = 'Active' THEN round(months_between(f.maturity_date, a.as_of_date) / 12, 2) END AS remaining_tenor_years,
  f.status = 'Active' AND f.maturity_date <= add_months(a.as_of_date, 12)  AS matures_within_12m,
  f.security_type,
  f.security_type <> 'Unsecured'                                          AS is_secured,
  f.guarantor_type,
  f.guarantor_type IN ('Parent Guarantee', 'Keepwell')                    AS has_parent_support,
  coalesce(f.has_covenant, false)                                         AS has_covenant,
  cv.covenant_types,
  CAST(coalesce(cv.n_covenants, 0) AS INT)                                AS n_covenants,
  cv.last_test_date,
  cv.min_headroom_latest,
  cv.leverage_headroom_latest,
  cv.leverage_actual_latest,
  cv.leverage_threshold_latest,
  coalesce(cv.breached_latest, false)                                     AS covenant_breached_latest,
  CAST(coalesce(cv.n_waivers, 0) AS INT)                                  AS n_covenant_waivers,
  coalesce(cv.n_waivers_current_fy, 0) > 0                                AS has_waiver_current_fy,
  CAST(coalesce(cl.n_collateral, 0) AS INT)                               AS n_collateral,
  coalesce(cl.collateral_value_usd, 0D)                                   AS collateral_value_usd,
  cl.max_ltv_pct,
  cl.oldest_valuation_date,
  coalesce(pl.l, kl.l).id                                                 AS jp_support_letter_id,
  coalesce(pl.l, kl.l).status                                             AS jp_support_letter_status,
  coalesce(pl.l, kl.l).confirmed                                          AS jp_support_last_confirmed_date,
  coalesce(pl.l, kl.l).amount                                             AS jp_support_amount_usd,
  CASE WHEN f.guarantor_type IN ('Parent Guarantee', 'Keepwell') THEN coalesce(coalesce(pl.l, kl.l).parent, pr.p.parent) END AS jp_parent_id,
  CASE WHEN f.guarantor_type IN ('Parent Guarantee', 'Keepwell') THEN CAST(pr.p.grade AS INT) END AS parent_rating_grade,
  CASE WHEN f.guarantor_type IN ('Parent Guarantee', 'Keepwell') THEN pr.p.equiv END   AS parent_rating_equivalent,
  CASE WHEN f.guarantor_type IN ('Parent Guarantee', 'Keepwell') THEN pr.p.outlook END AS parent_rating_outlook,
  CASE WHEN f.guarantor_type IN ('Parent Guarantee', 'Keepwell') THEN pr.p.action END  AS parent_rating_last_action,
  CASE WHEN f.guarantor_type IN ('Parent Guarantee', 'Keepwell') THEN pr.p.date END    AS parent_rating_date
FROM f
CROSS JOIN asof a
LEFT JOIN ${catalog}.gold.dim_product p ON p.product_name = f.facility_type
LEFT JOIN bal_now b ON b.facility_id = f.facility_id
LEFT JOIN bal_last bl ON bl.facility_id = f.facility_id
LEFT JOIN cov cv ON cv.facility_id = f.facility_id
LEFT JOIN col cl ON cl.facility_id = f.facility_id
LEFT JOIN pg_letter pl ON pl.facility_id = f.facility_id AND f.guarantor_type = 'Parent Guarantee'
LEFT JOIN kw_letter kl ON kl.golden_client_id = f.golden_client_id AND f.guarantor_type = 'Keepwell'
LEFT JOIN parent pr ON pr.client_group_id = f.client_group_id
LEFT JOIN ${catalog}.gold.dim_client dc ON dc.golden_client_id = f.golden_client_id AND dc.is_current
