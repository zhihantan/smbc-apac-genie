-- dim_account: silver.core_account + current dim_client version, product and CASA / TD class.
SELECT
  a.account_id,
  a.cust_no,
  a.golden_client_id,
  c.golden_client_sk,
  coalesce(a.client_group_id, c.client_group_id)                                   AS client_group_id,
  a.account_type,
  p.product_id,
  CASE WHEN a.account_type IN ('Current Account', 'Savings Account') THEN 'CASA' ELSE 'TD' END AS deposit_class,
  a.account_type IN ('Current Account', 'Savings Account')                          AS is_casa,
  upper(a.currency)                                                                 AS currency,
  c.coverage_office                                                                 AS booking_country,
  a.open_date,
  a.status                                                                          AS account_status,
  a.status = 'ACTIVE'                                                               AS is_active,
  CAST(floor(months_between(DATE'${as_of_date}', a.open_date)) AS INT)              AS account_age_months
FROM ${catalog}.silver.core_account a
LEFT JOIN ${catalog}.gold.dim_client c ON c.golden_client_id = a.golden_client_id AND c.is_current
LEFT JOIN ${catalog}.gold.dim_product p ON p.product_name = a.account_type
WHERE a.golden_client_id IS NOT NULL
