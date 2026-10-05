-- fact_liquidity_structure_monthly: silver.core_liquidity_structure (+ _participant, which also lists the header
-- account) expanded to every month-end the structure is active (start on / before, not ended by the month-end; from
-- the history start Apr-2023), one row per member account active at the month-end, with its month-end balance
-- (silver.core_deposit_balance_monthly), the as-was golden_client_sk of the member's holder and the estimated
-- interest saved (member balance x interest benefit bps / 12).
WITH me AS (
  SELECT date AS month_end_date FROM ${catalog}.gold.dim_date
  WHERE is_month_end AND date <= DATE'${as_of_date}'
), s AS (
  SELECT st.*, me.month_end_date
  FROM ${catalog}.silver.core_liquidity_structure st
  JOIN me ON st.start_date <= me.month_end_date AND (st.end_date IS NULL OR st.end_date > me.month_end_date)
), m AS (
  SELECT s.structure_id, s.month_end_date, s.structure_name, s.structure_type, s.header_account_id,
         s.golden_client_id AS header_golden_client_id, upper(s.header_country) AS header_country,
         upper(s.pooling_currency) AS pooling_currency, s.sweep_frequency, s.is_cross_border,
         s.interest_benefit_bps, s.monthly_fee_usd, s.start_date, s.client_group_id AS structure_group_id,
         p.participant_account_id AS member_account_id, p.role, upper(p.participant_country) AS member_country,
         upper(p.participant_currency) AS member_currency, p.sweep_direction, p.target_balance_lcy, p.join_date,
         p.golden_client_id, p.client_group_id
  FROM s JOIN ${catalog}.silver.core_liquidity_structure_participant p
    ON p.structure_id = s.structure_id AND p.join_date <= s.month_end_date
   AND (p.leave_date IS NULL OR p.leave_date > s.month_end_date)
), agg AS (
  SELECT structure_id, month_end_date, count(*) AS n_members, count(DISTINCT member_country) AS n_countries,
         array_join(array_sort(collect_set(member_country)), ', ') AS countries_text
  FROM m GROUP BY structure_id, month_end_date
)
SELECT
  m.structure_id,
  m.member_account_id,
  m.month_end_date,
  trunc(m.month_end_date, 'MM')                                        AS month,
  dc.golden_client_sk,
  m.golden_client_id,
  coalesce(m.structure_group_id, m.client_group_id, dc.client_group_id) AS client_group_id,
  m.structure_name,
  m.structure_type,
  pr.product_id,
  m.header_account_id,
  m.header_golden_client_id,
  m.header_country,
  m.pooling_currency,
  m.sweep_frequency,
  m.is_cross_border,
  m.interest_benefit_bps,
  m.start_date                                                         AS structure_start_date,
  m.role                                                               AS member_role,
  m.role = 'Header'                                                    AS is_header,
  m.member_country,
  m.member_currency,
  m.sweep_direction,
  m.target_balance_lcy,
  m.join_date                                                          AS member_join_date,
  coalesce(b.balance_usd, 0.0)                                         AS member_balance_usd,   -- 0 = not funded yet
  coalesce(b.balance_lcy, 0.0)                                         AS member_balance_lcy,
  coalesce(b.balance_usd, 0.0) * m.interest_benefit_bps / 10000.0 / 12.0 AS interest_saved_usd,
  CASE WHEN m.role = 'Header' THEN m.monthly_fee_usd ELSE 0.0 END      AS monthly_fee_usd,
  CAST(a.n_members AS INT)                                             AS structure_members,
  CAST(a.n_countries AS INT)                                           AS structure_countries,
  a.countries_text                                                     AS structure_countries_text,
  m.month_end_date = last_day(DATE'${as_of_date}')                     AS is_latest_month_end
FROM m
JOIN agg a ON a.structure_id = m.structure_id AND a.month_end_date = m.month_end_date
LEFT JOIN ${catalog}.silver.core_deposit_balance_monthly b
  ON b.account_id = m.member_account_id AND b.balance_date = m.month_end_date
LEFT JOIN ${catalog}.gold.dim_product pr ON pr.product_name = m.structure_type
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = m.golden_client_id
  AND m.month_end_date <= dc.valid_to
  AND (m.month_end_date >= dc.valid_from OR dc.version_no = 1)
