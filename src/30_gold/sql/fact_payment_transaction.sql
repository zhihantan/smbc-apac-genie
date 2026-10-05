-- fact_payment_transaction: silver.pay_payment_message (already one row per payment_id: silver kept the earliest
-- copy and dropped the Jun-2026 replay batch) + the account's currency / booking country (dim_account), the as-was
-- golden_client_sk (D11), payer / payee countries and the money corridor, the competitor-bank key and the fee from
-- the finance engine's TB tariff (USD 3 per payment message + 10 bps of cross-border value).
SELECT
  p.payment_id,
  p.payment_date,
  trunc(p.payment_date, 'MM')                                                     AS month,
  dc.golden_client_sk,
  p.golden_client_id,
  coalesce(p.client_group_id, dc.client_group_id)                                 AS client_group_id,
  p.cust_no,
  p.account_id,
  a.booking_country,
  a.currency,
  p.amount_usd,
  CASE WHEN a.currency = 'USD' THEN p.amount_usd ELSE p.amount_usd * fx.rate_per_usd END AS amount_lcy,
  p.direction,
  p.channel,
  CASE WHEN p.channel IN ('Portal', 'Payments API', 'Host-to-Host') THEN 'Digital' ELSE 'Network rail' END AS channel_type,
  p.payment_purpose,
  p.payment_purpose = 'Trade Settlement'                                          AS is_trade_settlement,
  upper(p.debtor_country)                                                         AS client_country,
  upper(p.counterparty_country)                                                   AS counterparty_country,
  upper(CASE WHEN p.direction = 'Outbound' THEN p.debtor_country ELSE p.counterparty_country END) AS payer_country,
  upper(CASE WHEN p.direction = 'Outbound' THEN p.counterparty_country ELSE p.debtor_country END) AS payee_country,
  concat(upper(CASE WHEN p.direction = 'Outbound' THEN p.debtor_country ELSE p.counterparty_country END), '->',
         upper(CASE WHEN p.direction = 'Outbound' THEN p.counterparty_country ELSE p.debtor_country END)) AS corridor,
  p.is_cross_border,
  p.counterparty_bank_type,
  p.counterparty_bank_name,
  bk.bank_id                                                                      AS counterparty_bank_id,
  p.counterparty_bank_type = 'Other Bank'                                         AS is_other_bank_counterparty,
  p.counterparty_ref,
  p.stp_flag,
  NOT p.stp_flag                                                                  AS is_repaired,
  p.repair_reason,
  p.processing_seconds,
  pr.product_id,
  3.0 + CASE WHEN p.is_cross_border THEN p.amount_usd * 10.0 / 10000.0 ELSE 0.0 END AS fee_usd
FROM ${catalog}.silver.pay_payment_message p
LEFT JOIN ${catalog}.gold.dim_account a ON a.account_id = p.account_id
LEFT JOIN ${catalog}.gold.fx_rate_daily fx ON fx.currency_code = a.currency AND fx.date = p.payment_date
LEFT JOIN ${catalog}.gold.dim_competitor_bank bk ON bk.bank_name = p.counterparty_bank_name
LEFT JOIN ${catalog}.gold.dim_product pr
  ON pr.product_name = CASE WHEN p.is_cross_border THEN 'Cross-Border Payments' ELSE 'Domestic Payments' END
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = p.golden_client_id
  AND p.payment_date <= dc.valid_to
  AND (p.payment_date >= dc.valid_from OR dc.version_no = 1)
