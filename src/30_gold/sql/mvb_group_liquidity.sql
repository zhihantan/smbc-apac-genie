-- mvb_group_liquidity: record_type union (D13) of the cash-pool member rows (gold.fact_liquidity_structure_monthly)
-- and one row per client group x month-end with its deposits (gold.fact_deposit_balance_monthly), APAC country
-- footprint and pooling status. Group rows carry the lead entity's as-was dim_client version (D12).
WITH grp AS (
  SELECT client_group_id, balance_date AS month_end_date,
         sum(balance_usd)                    AS dep_usd,
         sum(casa_balance_usd)               AS casa_usd,
         sum(td_balance_usd)                 AS td_usd,
         count(DISTINCT booking_country)     AS n_countries,
         count(DISTINCT golden_client_id)    AS n_clients
  FROM ${catalog}.gold.fact_deposit_balance_monthly
  WHERE client_group_id IS NOT NULL
  GROUP BY client_group_id, balance_date
), pools AS (
  SELECT client_group_id, month_end_date, count(DISTINCT structure_id) AS n_structures
  FROM ${catalog}.gold.fact_liquidity_structure_monthly
  WHERE client_group_id IS NOT NULL
  GROUP BY client_group_id, month_end_date
), members AS (
  SELECT
    'Structure member'                                 AS record_type,
    concat(f.structure_id, '|', f.member_account_id)   AS base_row_id,
    f.month_end_date, f.month, f.golden_client_sk, f.golden_client_id, f.client_group_id,
    f.structure_id, f.structure_type, f.product_id, f.header_country, f.member_country, f.member_currency,
    f.pooling_currency, f.member_role, f.is_header, f.member_role = 'Participant' AS is_participant,
    coalesce(f.member_balance_usd, 0.0) AS member_balance_usd, coalesce(f.interest_saved_usd, 0.0) AS interest_saved_usd,
    f.monthly_fee_usd,
    0.0 AS group_deposits_usd, 0.0 AS group_casa_balance_usd, 0.0 AS group_td_balance_usd,
    CAST(NULL AS INT) AS group_apac_countries, CAST(NULL AS INT) AS group_clients_with_accounts,
    CAST(NULL AS INT) AS group_active_structures, CAST(NULL AS BOOLEAN) AS group_has_active_structure,
    false AS is_pooling_candidate, f.is_latest_month_end
  FROM ${catalog}.gold.fact_liquidity_structure_monthly f
), groups AS (
  SELECT
    'Group', g.client_group_id, g.month_end_date, trunc(g.month_end_date, 'MM'), CAST(NULL AS BIGINT),
    dg.lead_golden_client_id, g.client_group_id,
    CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING),
    CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING), false, false,
    0.0, 0.0, 0.0,
    g.dep_usd, g.casa_usd, g.td_usd,
    CAST(g.n_countries AS INT), CAST(g.n_clients AS INT), CAST(coalesce(p.n_structures, 0) AS INT),
    coalesce(p.n_structures, 0) > 0,
    g.n_countries >= th.threshold_value AND coalesce(p.n_structures, 0) = 0,
    g.month_end_date = last_day(DATE'${as_of_date}')
  FROM grp g
  JOIN ${catalog}.gold.dim_threshold th ON th.threshold_code = 'POOLING_MIN_COUNTRIES'
  LEFT JOIN pools p ON p.client_group_id = g.client_group_id AND p.month_end_date = g.month_end_date
  LEFT JOIN ${catalog}.gold.dim_client_group dg ON dg.client_group_id = g.client_group_id
), u AS (
  SELECT * FROM members UNION ALL SELECT * FROM groups
)
SELECT
  u.record_type, u.base_row_id, u.month_end_date, u.month,
  coalesce(u.golden_client_sk, dl.golden_client_sk)      AS golden_client_sk,
  u.golden_client_id,
  dl.golden_client_sk                                    AS lead_golden_client_sk,
  u.client_group_id, u.structure_id, u.structure_type, u.product_id, u.header_country, u.member_country,
  u.member_currency, u.pooling_currency, u.member_role, u.is_header, u.is_participant,
  u.member_balance_usd, u.interest_saved_usd, u.monthly_fee_usd,
  u.group_deposits_usd, u.group_casa_balance_usd, u.group_td_balance_usd, u.group_apac_countries,
  u.group_clients_with_accounts, u.group_active_structures, u.group_has_active_structure,
  u.is_pooling_candidate, u.is_latest_month_end
FROM u
LEFT JOIN ${catalog}.gold.dim_client_group dg2 ON dg2.client_group_id = u.client_group_id
LEFT JOIN ${catalog}.gold.dim_client dl
  ON  dl.golden_client_id = dg2.lead_golden_client_id
  AND u.month_end_date <= dl.valid_to
  AND (u.month_end_date >= dl.valid_from OR dl.version_no = 1)
