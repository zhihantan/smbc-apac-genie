-- mvb_trade_finance: record_type union (D13) of instrument issuance (gold.fact_trade_finance_transaction, on the
-- issue date) and month-end outstanding (gold.fact_trade_outstanding_monthly) with one shared set of dimensions.
SELECT
  'Issuance' AS record_type, t.txn_id, t.txn_date AS record_date, t.month, t.golden_client_sk, t.golden_client_id,
  t.client_group_id, t.product_type, t.product_id, t.direction, t.origin_country, t.destination_country, t.corridor,
  t.commodity, t.hs_chapter, t.is_carbon_intensive_commodity, t.beneficiary_country, t.counterparty_bank_country,
  t.booking_country, t.tenor_bucket, t.maturity_date, t.expiry_fiscal_quarter_label, t.is_sustainable_trade,
  t.is_legacy_book, false AS is_latest_month_end, false AS is_fiscal_quarter_end,
  t.amount_usd AS issuance_usd, 1 AS transactions, t.fee_usd, t.tenor_days AS tenor_days_issued,
  CAST(0.0 AS DOUBLE) AS outstanding_usd
FROM ${catalog}.gold.fact_trade_finance_transaction t
UNION ALL
SELECT
  'Outstanding', o.txn_id, o.month_end_date, o.month, o.golden_client_sk, o.golden_client_id,
  o.client_group_id, o.product_type, o.product_id, o.direction, o.origin_country, o.destination_country, o.corridor,
  o.commodity, o.hs_chapter, o.is_carbon_intensive_commodity, o.beneficiary_country, o.counterparty_bank_country,
  o.booking_country, o.tenor_bucket, o.maturity_date, o.expiry_fiscal_quarter_label, o.is_sustainable_trade,
  o.txn_date < DATE'2024-04-01', o.is_latest_month_end, o.is_fiscal_quarter_end,
  CAST(0.0 AS DOUBLE), 0, CAST(0.0 AS DOUBLE), 0, o.outstanding_usd
FROM ${catalog}.gold.fact_trade_outstanding_monthly o
