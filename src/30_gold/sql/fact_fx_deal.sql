-- fact_fx_deal: silver.tsy_fx_deal + product key, currency legs, tenor bucket and the as-was golden_client_sk (D11).
SELECT
  f.deal_id,
  f.deal_date,
  trunc(f.deal_date, 'MM')                                                    AS month,
  dc.golden_client_sk,
  f.golden_client_id,
  coalesce(f.client_group_id, dc.client_group_id)                             AS client_group_id,
  f.cpty_id,
  f.product_type,
  pr.product_id,
  upper(f.ccy_pair)                                                           AS ccy_pair,
  upper(split_part(f.ccy_pair, '/', 1))                                       AS base_currency,
  upper(split_part(f.ccy_pair, '/', 2))                                       AS quote_currency,
  CASE WHEN upper(split_part(f.ccy_pair, '/', 1)) = 'USD' THEN upper(split_part(f.ccy_pair, '/', 2))
       ELSE upper(split_part(f.ccy_pair, '/', 1)) END                         AS non_usd_currency,
  f.direction,
  f.notional_usd,
  f.margin_bps,
  f.revenue_usd,
  f.hedge_type,
  f.hedge_type = 'Hedge'                                                      AS is_hedge,
  f.tenor_days,
  CASE WHEN f.tenor_days <= 2 THEN 'Spot (T+2)'
       WHEN f.tenor_days <= 31 THEN 'Up to 1 month'
       WHEN f.tenor_days <= 92 THEN '1-3 months'
       WHEN f.tenor_days <= 183 THEN '3-6 months'
       ELSE 'Over 6 months' END                                               AS tenor_bucket,
  f.value_date,
  f.value_date > DATE'${as_of_date}'                                          AS is_unsettled_at_as_of,
  upper(f.booking_location)                                                   AS booking_country
FROM ${catalog}.silver.tsy_fx_deal f
LEFT JOIN ${catalog}.gold.dim_product pr ON pr.product_name = f.product_type
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = f.golden_client_id
  AND f.deal_date <= dc.valid_to
  AND (f.deal_date >= dc.valid_from OR dc.version_no = 1)
