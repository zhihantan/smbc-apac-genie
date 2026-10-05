-- fact_group_global_exposure_monthly (WP8c): the global relationship of each client group, entity x region x
-- product family x month-end (Apr-2023 .. Sep-2026). APAC = golden clients from silver facts (facility limits / drawn,
-- trade instruments live at the month-end, deposit balances) and gold.fact_client_revenue_monthly revenue; JP / EMEA /
-- AMER = the silver copies of the Delta-Shared tables (share_jp_parent_exposure_monthly, share_emea_*, share_amer_*).
-- Dense per entity x family from the first month to 11 months after the last activity so revenue_12m stays complete.
WITH months AS (
  SELECT month_start_date AS month, date AS month_end_date, fiscal_year, fiscal_year_label, fiscal_quarter_label,
         is_latest_month, row_number() OVER (ORDER BY date) AS month_no
  FROM ${catalog}.gold.dim_date
  WHERE is_month_end AND date BETWEEN DATE'${history_start}' AND DATE'${as_of_date}'
),
apac_parts AS (
  SELECT f.golden_client_id AS entity_id, trunc(f.balance_date, 'MM') AS month, dp.product_family,
         f.limit_usd AS committed_usd, f.drawn_usd, 0D AS deposits_usd, 0D AS revenue_usd
  FROM ${catalog}.silver.core_facility_balance_monthly f
  JOIN ${catalog}.silver.credit_facility_terms t ON t.facility_id = f.facility_id
  JOIN ${catalog}.gold.dim_product dp ON dp.product_name = t.facility_type
  UNION ALL
  SELECT t.golden_client_id, m.month, 'Trade Finance', t.amount_usd, t.amount_usd, 0D, 0D
  FROM ${catalog}.silver.trade_finance_txn t
  JOIN months m ON t.txn_date <= m.month_end_date AND t.maturity_date > m.month_end_date
  UNION ALL
  SELECT b.golden_client_id, trunc(b.balance_date, 'MM'), dp.product_family, 0D, 0D, b.balance_usd, 0D
  FROM ${catalog}.silver.core_deposit_balance_monthly b
  JOIN ${catalog}.silver.core_account a ON a.account_id = b.account_id
  JOIN ${catalog}.gold.dim_product dp ON dp.product_name = a.account_type
  UNION ALL
  SELECT golden_client_id, month, product_family, 0D, 0D, 0D, total_revenue_usd
  FROM ${catalog}.gold.fact_client_revenue_monthly WHERE NOT is_prior_year_only
),
apac AS (
  SELECT 'APAC' AS region, entity_id, month, product_family, CAST(NULL AS STRING) AS currency,
         sum(committed_usd) AS committed_usd, sum(drawn_usd) AS drawn_usd, sum(deposits_usd) AS deposits_usd,
         sum(revenue_usd) AS revenue_usd, CAST(NULL AS DOUBLE) AS committed_lcy, CAST(NULL AS DOUBLE) AS drawn_lcy,
         CAST(NULL AS DOUBLE) AS deposits_lcy, CAST(NULL AS DOUBLE) AS revenue_lcy,
         CAST(NULL AS STRING) AS share_entity_name, CAST(NULL AS STRING) AS share_entity_type,
         CAST(NULL AS STRING) AS share_entity_country, CAST(NULL AS STRING) AS booking_office,
         CAST(NULL AS STRING) AS share_group_id, CAST(NULL AS INT) AS provider_version, CAST(NULL AS TIMESTAMP) AS shared_at
  FROM apac_parts GROUP BY 2, 3, 4
),
jp AS (
  SELECT 'JP' AS region, jp_counterparty_id AS entity_id, trunc(month_end_date, 'MM') AS month, product_family, currency,
         committed_usd, drawn_usd, deposits_usd, revenue_usd, committed_lcy, drawn_lcy, deposits_lcy, revenue_lcy,
         jp_counterparty_name, counterparty_type, 'JP', booking_office, client_group_id, _provider_version, _shared_at
  FROM ${catalog}.silver.share_jp_parent_exposure_monthly
),
emea AS (
  SELECT 'EMEA' AS region, coalesce(x.provider_entity_id, r.provider_entity_id) AS entity_id,
         trunc(coalesce(x.month_end_date, r.month_end_date), 'MM') AS month, coalesce(x.product_family, r.product_family) AS product_family,
         coalesce(x.currency, r.currency) AS currency,
         coalesce(x.committed_usd, 0) AS committed_usd, coalesce(x.drawn_usd, 0) AS drawn_usd, coalesce(x.deposits_usd, 0) AS deposits_usd,
         coalesce(r.revenue_usd, 0) AS revenue_usd, x.committed_lcy, x.drawn_lcy, x.deposits_lcy, r.revenue_lcy,
         em.legal_name, 'EMEA subsidiary', em.country_code, coalesce(x.booking_office, em.booking_office), em.client_group_id,
         greatest(x._provider_version, r._provider_version), greatest(x._shared_at, r._shared_at)
  FROM ${catalog}.silver.share_emea_exposure_monthly x
  FULL OUTER JOIN ${catalog}.silver.share_emea_revenue_monthly r
    ON r.provider_entity_id = x.provider_entity_id AND r.month_end_date = x.month_end_date AND r.product_family = x.product_family
  LEFT JOIN ${catalog}.silver.share_emea_entity_master em ON em.provider_entity_id = coalesce(x.provider_entity_id, r.provider_entity_id)
),
amer AS (
  SELECT 'AMER' AS region, coalesce(x.provider_entity_id, r.provider_entity_id) AS entity_id,
         trunc(coalesce(x.month_end_date, r.month_end_date), 'MM') AS month, coalesce(x.product_family, r.product_family) AS product_family,
         coalesce(x.currency, r.currency) AS currency,
         coalesce(x.committed_usd, 0) AS committed_usd, coalesce(x.drawn_usd, 0) AS drawn_usd, coalesce(x.deposits_usd, 0) AS deposits_usd,
         coalesce(r.revenue_usd, 0) AS revenue_usd, x.committed_lcy, x.drawn_lcy, x.deposits_lcy, r.revenue_lcy,
         am.legal_name, 'AMER subsidiary', am.country_code, coalesce(x.booking_office, am.booking_office), am.client_group_id,
         greatest(x._provider_version, r._provider_version), greatest(x._shared_at, r._shared_at)
  FROM ${catalog}.silver.share_amer_exposure_monthly x
  FULL OUTER JOIN ${catalog}.silver.share_amer_revenue_monthly r
    ON r.provider_entity_id = x.provider_entity_id AND r.month_end_date = x.month_end_date AND r.product_family = x.product_family
  LEFT JOIN ${catalog}.silver.share_amer_entity_master am ON am.provider_entity_id = coalesce(x.provider_entity_id, r.provider_entity_id)
),
allr AS (SELECT * FROM apac UNION ALL SELECT * FROM jp UNION ALL SELECT * FROM emea UNION ALL SELECT * FROM amer),
span AS (         -- dense months per entity x family: first month .. 11 months after the last one
  SELECT a.region, a.entity_id, a.product_family, min(m.month_no) AS first_no, max(m.month_no) + 11 AS last_no,
         max(a.share_entity_name) AS share_entity_name, max(a.share_entity_type) AS share_entity_type,
         max(a.share_entity_country) AS share_entity_country, max(a.share_group_id) AS share_group_id,
         max(a.booking_office) AS share_booking_office
  FROM allr a JOIN months m ON m.month = a.month
  GROUP BY 1, 2, 3
),
dense AS (
  SELECT s.region, s.entity_id, s.product_family, s.share_entity_name, s.share_entity_type, s.share_entity_country,
         s.share_group_id, m.month, m.month_end_date, m.fiscal_year, m.fiscal_year_label, m.fiscal_quarter_label,
         m.is_latest_month,
         a.month IS NOT NULL AS has_data, a.currency, coalesce(a.committed_usd, 0) AS committed_usd,
         coalesce(a.drawn_usd, 0) AS drawn_usd, coalesce(a.deposits_usd, 0) AS deposits_usd,
         coalesce(a.revenue_usd, 0) AS revenue_usd, a.committed_lcy, a.drawn_lcy, a.deposits_lcy, a.revenue_lcy,
         coalesce(a.booking_office, s.share_booking_office) AS booking_office, a.provider_version, a.shared_at,
         sum(coalesce(a.revenue_usd, 0)) OVER (PARTITION BY s.region, s.entity_id, s.product_family ORDER BY m.month_no
                                               ROWS BETWEEN 11 PRECEDING AND CURRENT ROW) AS revenue_12m_usd
  FROM span s
  JOIN months m ON m.month_no BETWEEN s.first_no AND s.last_no
  LEFT JOIN allr a ON a.region = s.region AND a.entity_id = s.entity_id AND a.product_family = s.product_family AND a.month = m.month
)
SELECT
  d.month, d.month_end_date, d.fiscal_year, d.fiscal_year_label, d.fiscal_quarter_label, d.is_latest_month,
  d.region, d.entity_id,
  CASE WHEN d.region = 'APAC' THEN dc.display_name ELSE d.share_entity_name END AS entity_name,
  CASE WHEN d.region = 'APAC' THEN 'APAC legal entity' ELSE d.share_entity_type END AS entity_type,
  CASE WHEN d.region = 'APAC' THEN dc.country_of_incorporation ELSE d.share_entity_country END AS entity_country,
  CASE WHEN d.region = 'APAC' THEN dc.coverage_office ELSE d.region END AS booking_entity_id,
  CASE WHEN d.region = 'APAC' THEN dc.coverage_office ELSE d.booking_office END AS booking_office,
  CASE WHEN d.region = 'APAC' THEN dc.client_group_id ELSE d.share_group_id END AS client_group_id,
  dl.golden_client_sk AS lead_golden_client_sk,
  CASE WHEN d.region = 'APAC' THEN dc.golden_client_sk END AS golden_client_sk,
  CASE WHEN d.region = 'APAC' THEN d.entity_id END AS golden_client_id,
  d.region = 'APAC' AS is_apac, d.product_family, d.currency,
  d.committed_usd, d.drawn_usd, d.deposits_usd, d.revenue_usd, d.revenue_12m_usd,
  d.committed_lcy, d.drawn_lcy, d.deposits_lcy, d.revenue_lcy,
  CASE WHEN d.region = 'APAC' THEN 'gold APAC facts' WHEN d.region = 'JP' THEN 'share_jp_parent_exposure_monthly'
       ELSE concat('share_', lower(d.region), '_exposure_monthly / revenue_monthly') END AS source_table,
  d.provider_version AS share_provider_version, d.shared_at AS share_shared_at, d.has_data
FROM dense d
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  d.region = 'APAC' AND dc.golden_client_id = d.entity_id
  AND d.month_end_date <= dc.valid_to
  AND (d.month_end_date >= dc.valid_from OR dc.version_no = 1)
LEFT JOIN ${catalog}.gold.dim_client_group dg
  ON dg.client_group_id = CASE WHEN d.region = 'APAC' THEN dc.client_group_id ELSE d.share_group_id END
LEFT JOIN ${catalog}.gold.dim_client dl
  ON  dl.golden_client_id = dg.lead_golden_client_id
  AND d.month_end_date <= dl.valid_to
  AND (d.month_end_date >= dl.valid_from OR dl.version_no = 1)
WHERE d.has_data OR d.revenue_12m_usd <> 0
