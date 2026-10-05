-- fact_trade_corridor_monthly: client x goods corridor x HS chapter x month from SMBC trade finance
-- (silver.trade_finance_txn: origin / destination / HS chapter of each instrument) and cross-border trade-settlement
-- payments (silver.pay_payment_message: outbound = the client imports -> counterparty country to client country,
-- inbound = exports; HS chapter = the main traded goods of the client's industry - the trade platform's industry ->
-- goods mix, top weight - else the chapter it trades most in its own instruments, else 00), plus the vendor trade
-- statistics (silver.ext_trade_statistics) allocated over the active clients by activity, with one market residual
-- row per corridor-month without an active SMBC client (so the allocated market sums to the vendor total); the
-- prior-year vendor value of the same corridor x HS chapter is allocated with the same keys (additive YoY base).
WITH com (commodity, hs_chapter, is_carbon) AS (
  VALUES ('Electronics', '85', false), ('Machinery', '84', false), ('Auto Parts', '87', false),
         ('Precision Instruments', '90', false), ('Chemicals', '29', true), ('Plastics', '39', true),
         ('Iron & Steel', '72', true), ('Mineral Fuels', '27', true), ('Ores & Metals', '26', true),
         ('Palm Oil & Fats', '15', true), ('Food & Agri', '10', false), ('Textiles & Apparel', '62', false)
), ind (industry_subsector, commodity) AS (
  VALUES ('Electronics', 'Electronics'), ('Electronic Devices', 'Electronics'), ('Semiconductors', 'Electronics'),
         ('Telecommunications', 'Electronics'), ('Auto Parts', 'Auto Parts'), ('Vehicles', 'Auto Parts'),
         ('Precision Components', 'Precision Instruments'), ('Instruments', 'Precision Instruments'),
         ('Machinery', 'Machinery'), ('Heavy Industry', 'Machinery'), ('Chemicals', 'Chemicals'),
         ('Petrochemicals', 'Chemicals'), ('Steel', 'Iron & Steel'), ('Advanced Materials', 'Plastics'),
         ('Fibre', 'Textiles & Apparel'), ('Oil, Gas & Coal', 'Mineral Fuels'), ('Power Generation', 'Mineral Fuels'),
         ('State Power', 'Mineral Fuels'), ('Mining', 'Ores & Metals'), ('Minerals', 'Ores & Metals'),
         ('Natural Resources', 'Ores & Metals'), ('Agribusiness', 'Food & Agri'), ('Palm Oil', 'Palm Oil & Fats'),
         ('Food & Beverage', 'Food & Agri'), ('Consumer Goods', 'Textiles & Apparel'), ('Infrastructure', 'Machinery'),
         ('Construction', 'Iron & Steel'), ('Property', 'Iron & Steel'), ('Renewables', 'Electronics')
), txn AS (
  SELECT golden_client_id, txn_date, closed_date, upper(origin_country) AS o, upper(destination_country) AS d,
         hs_chapter, amount_usd, commission_usd
  FROM ${catalog}.silver.trade_finance_txn
), traded AS (   -- the HS chapter a client trades most in its own instruments
  SELECT golden_client_id, max_by(hs_chapter, struct(n, hs_chapter)) AS hs_chapter
  FROM (SELECT golden_client_id, hs_chapter, count(*) AS n FROM txn GROUP BY golden_client_id, hs_chapter)
  GROUP BY golden_client_id
), cli_main AS (
  SELECT c.golden_client_id, c.client_group_id, coalesce(ic.hs_chapter, tr.hs_chapter, '00') AS hs_chapter
  FROM ${catalog}.gold.dim_client c
  LEFT JOIN ind i ON i.industry_subsector = c.industry_subsector
  LEFT JOIN com ic ON ic.commodity = i.commodity
  LEFT JOIN traded tr ON tr.golden_client_id = c.golden_client_id
  WHERE c.is_current
), tf AS (
  SELECT golden_client_id, trunc(txn_date, 'MM') AS month, o, d, hs_chapter AS hs,
         count(*) AS n, sum(amount_usd) AS usd, sum(commission_usd) AS fee
  FROM txn WHERE o <> d
  GROUP BY golden_client_id, trunc(txn_date, 'MM'), o, d, hs_chapter
), ts AS (
  SELECT p.golden_client_id, trunc(p.payment_date, 'MM') AS month,
         upper(CASE WHEN p.direction = 'Outbound' THEN p.counterparty_country ELSE p.debtor_country END) AS o,
         upper(CASE WHEN p.direction = 'Outbound' THEN p.debtor_country ELSE p.counterparty_country END) AS d,
         coalesce(cm.hs_chapter, '00') AS hs, count(*) AS n, sum(p.amount_usd) AS usd
  FROM ${catalog}.silver.pay_payment_message p
  LEFT JOIN cli_main cm ON cm.golden_client_id = p.golden_client_id
  WHERE p.payment_purpose = 'Trade Settlement' AND p.debtor_country <> p.counterparty_country
  GROUP BY ALL
), act AS (
  SELECT coalesce(tf.golden_client_id, ts.golden_client_id) AS golden_client_id,
         coalesce(tf.month, ts.month) AS month, coalesce(tf.o, ts.o) AS o, coalesce(tf.d, ts.d) AS d,
         coalesce(tf.hs, ts.hs) AS hs,
         coalesce(tf.n, 0) AS tf_n, coalesce(tf.usd, 0.0) AS tf_usd, coalesce(tf.fee, 0.0) AS tf_fee,
         coalesce(ts.n, 0) AS ts_n, coalesce(ts.usd, 0.0) AS ts_usd
  FROM tf FULL OUTER JOIN ts
    ON ts.golden_client_id = tf.golden_client_id AND ts.month = tf.month AND ts.o = tf.o AND ts.d = tf.d AND ts.hs = tf.hs
), mkt AS (
  SELECT m.period_month AS month, upper(m.origin_country) AS o, upper(m.destination_country) AS d, m.hs_chapter AS hs,
         m.trade_value_usd AS v, m.data_status, py.trade_value_usd AS v_py
  FROM ${catalog}.silver.ext_trade_statistics m
  LEFT JOIN ${catalog}.silver.ext_trade_statistics py
    ON py.origin_country = m.origin_country AND py.destination_country = m.destination_country
   AND py.hs_chapter = m.hs_chapter AND py.period_month = add_months(m.period_month, -12)
), tot AS (
  SELECT month, o, d, hs, sum(tf_usd + ts_usd) AS a FROM act GROUP BY month, o, d, hs
), am AS (   -- active client-months: trade product held in the trailing 12 months / live at the month-end
  SELECT a.golden_client_id, a.month,
         count_if(t.txn_date <= last_day(a.month)
                  AND (t.txn_date > last_day(add_months(a.month, -12)) OR t.closed_date IS NULL OR t.closed_date > last_day(a.month))) > 0 AS has_tp
  FROM (SELECT DISTINCT golden_client_id, month FROM act) a
  LEFT JOIN txn t ON t.golden_client_id = a.golden_client_id
  GROUP BY a.golden_client_id, a.month
), tp_now AS (
  SELECT DISTINCT golden_client_id FROM txn
  WHERE closed_date IS NULL OR txn_date > add_months(DATE'${as_of_date}', -12)
), rws AS (
  SELECT 'Client' AS record_type, a.month, a.o, a.d, a.hs, a.golden_client_id,
         a.tf_n, a.tf_usd, a.tf_fee, a.ts_n, a.ts_usd,
         coalesce(m.v * (a.tf_usd + a.ts_usd) / nullif(t.a, 0), 0.0) AS v_alloc,
         m.v_py * (a.tf_usd + a.ts_usd) / nullif(t.a, 0) AS v_py_alloc, m.v, m.v_py, m.data_status
  FROM act a
  JOIN tot t ON t.month = a.month AND t.o = a.o AND t.d = a.d AND t.hs = a.hs
  LEFT JOIN mkt m ON m.month = a.month AND m.o = a.o AND m.d = a.d AND m.hs = a.hs
  UNION ALL
  SELECT 'Market residual', m.month, m.o, m.d, m.hs, CAST(NULL AS STRING),
         0, 0.0, 0.0, 0, 0.0, m.v, m.v_py, m.v, m.v_py, m.data_status
  FROM mkt m LEFT ANTI JOIN tot t ON t.month = m.month AND t.o = m.o AND t.d = m.d AND t.hs = m.hs
)
SELECT
  concat_ws('|', date_format(r.month, 'yyyy-MM'), r.o, r.d, r.hs, coalesce(r.golden_client_id, 'MARKET')) AS corridor_row_id,
  r.record_type,
  r.month,
  r.o                                                                  AS origin_country,
  r.d                                                                  AS destination_country,
  concat(r.o, '->', r.d)                                               AS corridor,
  r.hs                                                                 AS hs_chapter,
  coalesce(c.commodity, 'Unclassified')                                AS commodity,
  coalesce(c.is_carbon, false)                                         AS is_carbon_intensive_commodity,
  dc.golden_client_sk,
  r.golden_client_id,
  cm.client_group_id,
  CAST(r.tf_n AS INT)                                                  AS tf_transactions,
  r.tf_usd                                                             AS tf_issuance_usd,
  r.tf_fee                                                             AS tf_fee_usd,
  CAST(r.ts_n AS INT)                                                  AS trade_settlement_payments,
  r.ts_usd                                                             AS trade_settlement_usd,
  r.tf_usd + r.ts_usd                                                  AS activity_usd,
  r.ts_n > 0                                                           AS is_payment_visible,
  CASE WHEN r.golden_client_id IS NULL THEN NULL ELSE coalesce(am.has_tp, false) END AS has_trade_product_12m,
  CASE WHEN r.golden_client_id IS NULL THEN NULL ELSE tn.golden_client_id IS NOT NULL END AS has_trade_product_as_of,
  r.v_alloc                                                            AS market_value_allocated_usd,
  r.v_py_alloc                                                         AS market_value_prior_year_allocated_usd,
  r.v                                                                  AS market_value_corridor_usd,
  r.v_py                                                               AS market_value_prior_year_corridor_usd,
  r.v / nullif(r.v_py, 0) - 1                                          AS market_yoy_growth_corridor,
  r.data_status                                                        AS market_data_status
FROM rws r
LEFT JOIN com c ON c.hs_chapter = r.hs
LEFT JOIN cli_main cm ON cm.golden_client_id = r.golden_client_id
LEFT JOIN am ON am.golden_client_id = r.golden_client_id AND am.month = r.month
LEFT JOIN tp_now tn ON tn.golden_client_id = r.golden_client_id
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = r.golden_client_id
  AND r.month <= dc.valid_to
  AND (r.month >= dc.valid_from OR dc.version_no = 1)
