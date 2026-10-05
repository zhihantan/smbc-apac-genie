-- fn_usd(amount, currency, d): USD value of a local-currency amount at the daily gold.fx_rate_daily rate.
CREATE OR REPLACE FUNCTION ${catalog}.gold.fn_usd(
    amount   DOUBLE COMMENT 'Amount in the local currency',
    currency STRING COMMENT 'ISO 4217 code of the amount (case-insensitive)',
    d        DATE   COMMENT 'Conversion date; clamped to the gold calendar window ${history_start}..${calendar_end}')
  RETURNS DOUBLE
  COMMENT 'USD value of amount (currency) at the gold.fx_rate_daily rate of date d. USD passes through; an unknown currency returns NULL. Never uses CURRENT_DATE.'
  RETURN CASE
    WHEN amount IS NULL THEN NULL
    WHEN upper(currency) = 'USD' THEN amount
    ELSE amount * (SELECT max(r.usd_per_unit) FROM ${catalog}.gold.fx_rate_daily r
                   WHERE r.currency_code = upper(currency)
                     AND r.date = least(greatest(d, DATE'${history_start}'), DATE'${calendar_end}'))
  END
