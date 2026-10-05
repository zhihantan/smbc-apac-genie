-- fact_trade_finance_transaction: silver.trade_finance_txn + lifecycle counts (silver.trade_event,
-- silver.trade_presentation), the commodity reference (HS chapter description, carbon-intensive goods - the trade
-- platform's commodity list), product key, tenor bucket, expiry fiscal quarter (dim_date; same JP fiscal rule for
-- maturities after Mar-2027) and the as-was golden_client_sk (D11).
WITH com (commodity, hs_description, is_carbon) AS (
  VALUES ('Electronics', 'Electrical machinery and equipment', false),
         ('Machinery', 'Machinery and mechanical appliances', false),
         ('Auto Parts', 'Vehicles, parts and accessories', false),
         ('Precision Instruments', 'Optical, measuring and precision instruments', false),
         ('Chemicals', 'Organic chemicals', true),
         ('Plastics', 'Plastics and articles thereof', true),
         ('Iron & Steel', 'Iron and steel', true),
         ('Mineral Fuels', 'Mineral fuels and oils (coal, crude, LNG)', true),
         ('Ores & Metals', 'Ores, slag and ash', true),
         ('Palm Oil & Fats', 'Animal and vegetable fats and oils', true),
         ('Food & Agri', 'Cereals and food products', false),
         ('Textiles & Apparel', 'Apparel and clothing', false)
), ev AS (
  SELECT txn_id, count_if(event_type = 'Amend') AS n_amend FROM ${catalog}.silver.trade_event GROUP BY txn_id
), pr AS (
  SELECT txn_id, count(*) AS n_pres, count_if(is_discrepant) AS n_disc FROM ${catalog}.silver.trade_presentation GROUP BY txn_id
)
SELECT
  t.txn_id,
  t.txn_date,
  trunc(t.txn_date, 'MM')                                                         AS month,
  dc.golden_client_sk,
  t.golden_client_id,
  coalesce(t.client_group_id, dc.client_group_id)                                 AS client_group_id,
  t.party_id,
  t.product_type,
  p.product_id,
  t.direction,
  upper(t.origin_country)                                                         AS origin_country,
  upper(t.destination_country)                                                    AS destination_country,
  concat(upper(t.origin_country), '->', upper(t.destination_country))             AS corridor,
  upper(t.counterparty_country)                                                   AS counterparty_country,
  upper(t.counterparty_bank_country)                                              AS counterparty_bank_country,
  upper(t.beneficiary_country)                                                    AS beneficiary_country,
  t.commodity,
  t.hs_chapter,
  c.hs_description,
  coalesce(c.is_carbon, false)                                                    AS is_carbon_intensive_commodity,
  upper(t.currency)                                                               AS currency,
  t.amount_ccy                                                                    AS amount_lcy,
  t.amount_usd,
  t.tenor_days,
  CASE WHEN t.tenor_days <= 30 THEN '0-30 days' WHEN t.tenor_days <= 90 THEN '31-90 days'
       WHEN t.tenor_days <= 180 THEN '91-180 days' WHEN t.tenor_days <= 365 THEN '181-365 days'
       ELSE 'Over 365 days' END                                                   AS tenor_bucket,
  t.maturity_date,
  coalesce(dm.fiscal_quarter_label,
           concat('FY', year(t.maturity_date) - CASE WHEN month(t.maturity_date) < 4 THEN 1 ELSE 0 END, '-Q',
                  CASE WHEN month(t.maturity_date) BETWEEN 4 AND 6 THEN 1 WHEN month(t.maturity_date) BETWEEN 7 AND 9 THEN 2
                       WHEN month(t.maturity_date) BETWEEN 10 AND 12 THEN 3 ELSE 4 END)) AS expiry_fiscal_quarter_label,
  t.status,
  t.closed_date,
  t.status IN ('Issued', 'Negotiated')                                            AS is_live_at_as_of,
  coalesce(t.outstanding_usd, 0.0)                                                AS outstanding_at_as_of_usd,
  t.fee_bps,
  t.commission_usd                                                                AS fee_usd,
  coalesce(t.is_sustainable_trade, false)                                         AS is_sustainable_trade,
  upper(t.booking_location)                                                       AS booking_country,
  upper(t.booking_location) = 'SG'                                                AS is_booked_in_sg_hub,
  CAST(coalesce(ev.n_amend, 0) AS INT)                                            AS amendment_count,
  CAST(coalesce(pr.n_pres, 0) AS INT)                                             AS presentation_count,
  CAST(coalesce(pr.n_disc, 0) AS INT)                                             AS discrepant_presentation_count,
  t.txn_date < DATE'2024-04-01'                                                   AS is_legacy_book
FROM ${catalog}.silver.trade_finance_txn t
LEFT JOIN com c ON c.commodity = t.commodity
LEFT JOIN ev ON ev.txn_id = t.txn_id
LEFT JOIN pr ON pr.txn_id = t.txn_id
LEFT JOIN ${catalog}.gold.dim_product p ON p.product_name = t.product_type
LEFT JOIN ${catalog}.gold.dim_date dm ON dm.date = t.maturity_date
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = t.golden_client_id
  AND t.txn_date <= dc.valid_to
  AND (t.txn_date >= dc.valid_from OR dc.version_no = 1)
