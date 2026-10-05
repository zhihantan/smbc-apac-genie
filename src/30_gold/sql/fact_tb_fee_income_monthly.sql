-- fact_tb_fee_income_monthly: the finance engine's TB fee ledger (silver.fin_tb_fee: fees not derivable from the
-- transaction feeds) + fees derived from the transactions with the finance engine's tariff: payment processing
-- (USD 3 per payment message) and cross-border commission (10 bps of cross-border value) from
-- silver.pay_payment_message, trade commissions (silver.trade_finance_txn.commission_usd, at issuance) and FX margin
-- (silver.tsy_fx_deal.revenue_usd). Everything from Apr-2024 (the fee / payments history start); golden-client grain.
WITH ledger AS (
  SELECT f.golden_client_id, max(f.client_group_id) AS client_group_id, f.fee_month,
         c.coverage_office AS booking_country, f.fee_type,
         'finance engine fee ledger' AS fee_source,
         CASE f.fee_type WHEN 'Channel - Host-to-Host' THEN 'Host-to-Host' WHEN 'Channel - API' THEN 'Payments API' END AS product_name,
         f.product_family, upper(f.fee_currency) AS fee_currency,
         sum(f.fee_amount_lcy) AS fee_lcy, sum(f.fee_amount_usd) AS fee_usd,
         CAST(NULL AS BIGINT) AS activity_count, CAST(NULL AS DOUBLE) AS activity_volume_usd
  FROM ${catalog}.silver.fin_tb_fee f
  JOIN ${catalog}.gold.dim_client c ON c.golden_client_id = f.golden_client_id AND c.is_current
  GROUP BY f.golden_client_id, f.fee_month, c.coverage_office, f.fee_type, f.product_family, upper(f.fee_currency)
), pay AS (
  SELECT p.golden_client_id, max(p.client_group_id) AS client_group_id, trunc(p.payment_date, 'MM') AS fee_month,
         a.booking_country,
         CASE WHEN p.is_cross_border THEN 'Cross-Border Payments' ELSE 'Domestic Payments' END AS product_name,
         count(*) AS n, sum(p.amount_usd) AS v,
         sum(CASE WHEN p.is_cross_border THEN p.amount_usd ELSE 0.0 END) AS xb
  FROM ${catalog}.silver.pay_payment_message p
  JOIN ${catalog}.gold.dim_account a ON a.account_id = p.account_id
  WHERE p.payment_date >= DATE'2024-04-01'
  GROUP BY ALL
), derived AS (
  SELECT golden_client_id, client_group_id, fee_month, booking_country, 'Payment Processing' AS fee_type,
         'derived from payments (USD 3 per message)' AS fee_source, product_name, 'Payments' AS product_family,
         'USD' AS fee_currency, 3.0 * n AS fee_lcy, 3.0 * n AS fee_usd, CAST(n AS BIGINT) AS activity_count,
         v AS activity_volume_usd
  FROM pay
  UNION ALL
  SELECT golden_client_id, client_group_id, fee_month, booking_country, 'Cross-Border Payment Commission',
         'derived from payments (10 bps of cross-border value)', product_name, 'Payments', 'USD',
         xb * 10.0 / 10000.0, xb * 10.0 / 10000.0, CAST(n AS BIGINT), xb
  FROM pay WHERE product_name = 'Cross-Border Payments'
  UNION ALL
  SELECT t.golden_client_id, max(t.client_group_id), trunc(t.txn_date, 'MM'), upper(t.booking_location), 'Trade Commission',
         'derived from trade instruments (commission at issuance)', t.product_type, 'Trade Finance', 'USD',
         sum(t.commission_usd), sum(t.commission_usd), count(*), sum(t.amount_usd)
  FROM ${catalog}.silver.trade_finance_txn t
  WHERE t.txn_date >= DATE'2024-04-01'
  GROUP BY t.golden_client_id, trunc(t.txn_date, 'MM'), upper(t.booking_location), t.product_type
  UNION ALL
  SELECT x.golden_client_id, max(x.client_group_id), trunc(x.deal_date, 'MM'), upper(x.booking_location), 'FX Margin',
         'derived from FX deals (margin revenue)', x.product_type, 'FX', 'USD',
         sum(x.revenue_usd), sum(x.revenue_usd), count(*), sum(x.notional_usd)
  FROM ${catalog}.silver.tsy_fx_deal x
  WHERE x.deal_date >= DATE'2024-04-01'
  GROUP BY x.golden_client_id, trunc(x.deal_date, 'MM'), upper(x.booking_location), x.product_type
), lines AS (
  SELECT * FROM ledger UNION ALL SELECT * FROM derived
)
SELECT
  concat_ws('|', l.golden_client_id, date_format(l.fee_month, 'yyyy-MM'), l.fee_type, coalesce(l.product_name, '-'),
            l.booking_country, l.fee_currency)                                  AS fee_line_id,
  l.fee_month,
  dc.golden_client_sk,
  l.golden_client_id,
  coalesce(l.client_group_id, dc.client_group_id)                               AS client_group_id,
  l.booking_country,
  l.fee_type,
  l.fee_source,
  pr.product_id,
  l.product_family,
  CASE WHEN l.product_family = 'FX' THEN 'Markets' ELSE 'Transaction Banking' END AS business_line,
  l.product_family <> 'FX'                                                      AS is_tb_fee,
  l.fee_currency,
  l.fee_lcy,
  l.fee_usd,
  l.activity_count,
  l.activity_volume_usd
FROM lines l
LEFT JOIN ${catalog}.gold.dim_product pr ON pr.product_name = l.product_name
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = l.golden_client_id
  AND l.fee_month <= dc.valid_to
  AND (l.fee_month >= dc.valid_from OR dc.version_no = 1)
