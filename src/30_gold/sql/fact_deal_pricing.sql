-- fact_deal_pricing (WP8c): one row per credit deal (facility) signed from FY2023 (the gold calendar starts
-- 1-Apr-2023; 778 older silver deals signed 2019-FY2022 are outside dim_date and left out): pricing at origination
-- vs the hurdles, approval exceptions and the realised RAROC once seasoned (silver.fin_deal_pricing +
-- credit_facility_terms). Origination / realised RAROC are winsorised in the source; the below-hurdle flags come
-- from the unwinsorised ratios.
WITH seasoned AS (   -- latest fiscal quarter end with a realised RAROC (deals there are seasoned)
  SELECT last_day(add_months(trunc(max(signing_date), 'QUARTER'), 2)) AS q_end
  FROM ${catalog}.silver.fin_deal_pricing WHERE realised_raroc IS NOT NULL
)
SELECT
  p.facility_id, p.obligor_id, dc.golden_client_sk, p.golden_client_id, dc.client_group_id,
  p.signing_date, p.signing_quarter, d.fiscal_year AS signing_fiscal_year, d.fiscal_year_label AS signing_fiscal_year_label,
  d.fiscal_quarter_label AS signing_fiscal_quarter_label, d.fiscal_half_label AS signing_fiscal_half_label,
  p.product, dp.product_id, dp.product_family, t.status AS facility_status, t.maturity_date,
  p.amount_usd, p.internal_grade, p.ead AS ead_usd, p.rwa AS rwa_usd, p.lgd,
  p.priced_margin_bps, p.hurdle_margin_bps, p.margin_gap_bps, p.pricing_status,
  p.standalone_rorwa, p.meets_standalone_hurdle, p.standalone_rorwa < ${rorwa_hurdle} AS standalone_below_rorwa_hurdle,
  p.origination_raroc, p.approved_below_hurdle,
  p.exception_reason IS NOT NULL AS has_exception, p.exception_reason, p.exception_approver,
  p.realised_raroc, p.realised_raroc IS NOT NULL AS is_seasoned,
  CASE WHEN p.realised_raroc IS NOT NULL THEN p.caught_up END AS caught_up,
  CASE WHEN p.realised_raroc IS NULL THEN 'Not yet seasoned'
       WHEN NOT p.approved_below_hurdle THEN CASE WHEN p.realised_raroc >= ${raroc_hurdle} THEN 'Held above hurdle' ELSE 'Slipped below hurdle' END
       WHEN p.caught_up THEN 'Caught up' ELSE 'Still below hurdle' END AS realised_status,
  p.realised_raroc - p.origination_raroc AS raroc_slippage,
  p.realised_raroc < ${raroc_hurdle} AS realised_below_raroc_hurdle,
  CAST(months_between(DATE'${as_of_date}', p.signing_date) AS INT) AS months_since_signing,
  p.signing_date > add_months(DATE'${as_of_date}', -12) AS is_last_4_quarters,
  p.signing_date > add_months(s.q_end, -12) AND p.signing_date <= s.q_end AS is_last_4_seasoned_quarters
FROM ${catalog}.silver.fin_deal_pricing p
CROSS JOIN seasoned s
JOIN ${catalog}.gold.dim_date d ON d.date = p.signing_date
LEFT JOIN ${catalog}.silver.credit_facility_terms t ON t.facility_id = p.facility_id
LEFT JOIN ${catalog}.gold.dim_product dp ON dp.product_name = p.product
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = p.golden_client_id
  AND p.signing_date <= dc.valid_to
  AND (p.signing_date >= dc.valid_from OR dc.version_no = 1)
WHERE p.signing_date >= DATE'${history_start}'
