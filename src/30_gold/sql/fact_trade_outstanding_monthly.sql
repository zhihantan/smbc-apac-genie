-- fact_trade_outstanding_monthly: replay of silver.trade_event (outstanding after each lifecycle event) at every
-- month-end from Apr-2024 to the as-of month: an instrument is on a month-end when issued on / before it and not
-- closed by it; its outstanding is that of its last event on / before the month-end. Instrument attributes come
-- from gold.fact_trade_finance_transaction; the client version is the one valid at the month-end (D11).
WITH me AS (
  SELECT date AS month_end_date, is_quarter_end FROM ${catalog}.gold.dim_date
  WHERE is_month_end AND date BETWEEN DATE'2024-04-30' AND DATE'${as_of_date}'
), live AS (
  SELECT t.txn_id, me.month_end_date, me.is_quarter_end
  FROM ${catalog}.gold.fact_trade_finance_transaction t
  JOIN me ON t.txn_date <= me.month_end_date AND (t.closed_date IS NULL OR t.closed_date > me.month_end_date)
), os AS (
  SELECT l.txn_id, l.month_end_date, l.is_quarter_end,
         max_by(e.outstanding_after_usd, struct(e.event_date, e.event_seq)) AS outstanding_usd
  FROM live l
  JOIN ${catalog}.silver.trade_event e ON e.txn_id = l.txn_id AND e.event_date <= l.month_end_date
  GROUP BY l.txn_id, l.month_end_date, l.is_quarter_end
)
SELECT
  o.txn_id,
  o.month_end_date,
  trunc(o.month_end_date, 'MM')                                                AS month,
  dc.golden_client_sk,
  t.golden_client_id,
  coalesce(t.client_group_id, dc.client_group_id)                              AS client_group_id,
  t.product_type,
  t.product_id,
  t.direction,
  t.origin_country,
  t.destination_country,
  t.corridor,
  t.commodity,
  t.hs_chapter,
  t.is_carbon_intensive_commodity,
  t.beneficiary_country,
  t.counterparty_bank_country,
  t.booking_country,
  t.currency,
  o.outstanding_usd,
  o.outstanding_usd * t.amount_lcy / nullif(t.amount_usd, 0)                   AS outstanding_lcy,
  t.txn_date,
  t.maturity_date,
  datediff(t.maturity_date, o.month_end_date)                                  AS days_to_maturity,
  t.expiry_fiscal_quarter_label,
  t.tenor_bucket,
  t.is_sustainable_trade,
  o.month_end_date = last_day(DATE'${as_of_date}')                             AS is_latest_month_end,
  o.is_quarter_end                                                             AS is_fiscal_quarter_end
FROM os o
JOIN ${catalog}.gold.fact_trade_finance_transaction t ON t.txn_id = o.txn_id
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = t.golden_client_id
  AND o.month_end_date <= dc.valid_to
  AND (o.month_end_date >= dc.valid_from OR dc.version_no = 1)
WHERE o.outstanding_usd > 0
