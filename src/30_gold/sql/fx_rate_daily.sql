-- fx_rate_daily: dense currency x day grid over dim_date; gaps carry the last published silver rate forward
-- (leading gaps take the first published rate). USD is pinned to 1.
WITH grid AS (
  SELECT d.date, c.currency_code FROM ${catalog}.gold.dim_date d CROSS JOIN ${catalog}.gold.dim_currency c
), src AS (
  SELECT upper(currency_code) AS currency_code, date, max(rate_per_usd) AS rate_per_usd
  FROM ${catalog}.silver.fx_rate_daily
  WHERE rate_per_usd > 0
  GROUP BY upper(currency_code), date
), filled AS (
  SELECT g.date, g.currency_code, s.rate_per_usd AS published,
         coalesce(last_value(s.rate_per_usd, true) OVER (PARTITION BY g.currency_code ORDER BY g.date
                                                         ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW),
                  first_value(s.rate_per_usd, true) OVER (PARTITION BY g.currency_code ORDER BY g.date
                                                          ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING)) AS rate
  FROM grid g LEFT JOIN src s ON s.currency_code = g.currency_code AND s.date = g.date
)
SELECT
  date,
  currency_code,
  CASE WHEN currency_code = 'USD' THEN 1.0 ELSE rate END                         AS rate_per_usd,
  CASE WHEN currency_code = 'USD' THEN 1.0 ELSE 1.0 / rate END                   AS usd_per_unit,
  avg(CASE WHEN currency_code = 'USD' THEN 1.0 ELSE 1.0 / rate END)
    OVER (PARTITION BY currency_code, trunc(date, 'MM'))                         AS month_avg_usd_per_unit,
  date = last_day(date)                                                          AS is_month_end,
  currency_code <> 'USD' AND published IS NULL                                   AS is_carried_forward
FROM filled
