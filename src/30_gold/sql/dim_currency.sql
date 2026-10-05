-- dim_currency: silver.ref_currency + names (static ISO 4217 list) + the as-of market rate from silver.fx_rate_daily.
WITH names (currency_code, currency_name, home_country_code) AS (
  VALUES ('USD', 'US Dollar', 'US'), ('SGD', 'Singapore Dollar', 'SG'), ('JPY', 'Japanese Yen', 'JP'),
         ('HKD', 'Hong Kong Dollar', 'HK'), ('AUD', 'Australian Dollar', 'AU'), ('INR', 'Indian Rupee', 'IN'),
         ('IDR', 'Indonesian Rupiah', 'ID'), ('THB', 'Thai Baht', 'TH'), ('MYR', 'Malaysian Ringgit', 'MY'),
         ('VND', 'Vietnamese Dong', 'VN'), ('CNY', 'Chinese Yuan Renminbi', 'CN'), ('PHP', 'Philippine Peso', 'PH'),
         ('KRW', 'South Korean Won', 'KR'), ('TWD', 'New Taiwan Dollar', 'TW'), ('NZD', 'New Zealand Dollar', 'NZ'),
         ('EUR', 'Euro', CAST(NULL AS STRING)), ('GBP', 'Pound Sterling', 'GB')
), asof AS (   -- latest market rate on or before the as-of date
  SELECT upper(currency_code) AS currency_code,
         max_by(rate_per_usd, date) AS rate_per_usd, max_by(usd_per_unit, date) AS usd_per_unit
  FROM ${catalog}.silver.fx_rate_daily
  WHERE date <= DATE'${as_of_date}'
  GROUP BY upper(currency_code)
), apac AS (
  SELECT DISTINCT upper(local_currency) AS currency_code FROM ${catalog}.silver.ref_booking_entity
  WHERE is_apac AND local_currency IS NOT NULL
)
SELECT
  upper(r.currency_code)                                       AS currency_code,
  coalesce(n.currency_name, upper(r.currency_code))            AS currency_name,
  n.home_country_code,
  coalesce(r.is_base, upper(r.currency_code) = 'USD')          AS is_base,
  p.currency_code IS NOT NULL                                  AS is_apac_local_currency,
  r.fx_base_per_usd                                            AS reference_rate_per_usd,
  CASE WHEN upper(r.currency_code) = 'USD' THEN 1.0 ELSE a.rate_per_usd END AS rate_per_usd_as_of,
  CASE WHEN upper(r.currency_code) = 'USD' THEN 1.0
       ELSE coalesce(a.usd_per_unit, 1.0 / a.rate_per_usd) END AS usd_per_unit_as_of
FROM ${catalog}.silver.ref_currency r
LEFT JOIN names n ON n.currency_code = upper(r.currency_code)
LEFT JOIN asof a ON a.currency_code = upper(r.currency_code)
LEFT JOIN apac p ON p.currency_code = upper(r.currency_code)
