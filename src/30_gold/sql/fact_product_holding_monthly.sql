-- fact_product_holding_monthly (WP8c): golden client x product x month, Apr-2023 .. Sep-2026, dense per
-- client-product from the month of first use. Held products: deposit accounts (month-end balance), credit
-- facilities (drawn / limit), live trade instruments, liquidity structures (header or participant) and SCF
-- programmes (anchor). Flow products (trade, FX, payments) count as used when they had a transaction in the
-- trailing 12 months. Client-month attributes (products held, deposit-only, single-product lending, peer median)
-- are carried on every row; family revenue 12M sits on one row per client x family x month.
WITH months AS (
  SELECT month_start_date AS month, date AS month_end_date, fiscal_year, fiscal_year_label, fiscal_quarter_label
  FROM ${catalog}.gold.dim_date
  WHERE is_month_end AND date BETWEEN DATE'${history_start}' AND DATE'${as_of_date}'
),
-- ---- held at month-end ---------------------------------------------------------------------------------
dep AS (
  SELECT b.golden_client_id, trunc(b.balance_date, 'MM') AS month, a.account_type AS product_name,
         sum(b.balance_usd) AS balance_usd, CAST(NULL AS DOUBLE) AS limit_usd, 0D AS drawn_usd, count(DISTINCT b.account_id) AS n_instruments,
         'Account balance' AS basis
  FROM ${catalog}.silver.core_deposit_balance_monthly b
  JOIN ${catalog}.silver.core_account a ON a.account_id = b.account_id
  GROUP BY 1, 2, 3
),
fac AS (
  SELECT f.golden_client_id, trunc(f.balance_date, 'MM') AS month, t.facility_type AS product_name,
         sum(f.drawn_usd) AS balance_usd, sum(f.limit_usd) AS limit_usd, sum(f.drawn_usd) AS drawn_usd, count(DISTINCT f.facility_id) AS n_instruments,
         'Facility limit' AS basis
  FROM ${catalog}.silver.core_facility_balance_monthly f
  JOIN ${catalog}.silver.credit_facility_terms t ON t.facility_id = f.facility_id
  GROUP BY 1, 2, 3
),
trade_live AS (   -- same rule as silver.group_exposure_by_er_run: issued on or before, maturing after the date
  SELECT t.golden_client_id, m.month, t.product_type AS product_name,
         sum(t.amount_usd) AS balance_usd, CAST(NULL AS DOUBLE) AS limit_usd, 0D AS drawn_usd, count(*) AS n_instruments,
         'Live instrument' AS basis
  FROM ${catalog}.silver.trade_finance_txn t
  JOIN months m ON t.txn_date <= m.month_end_date AND t.maturity_date > m.month_end_date
  GROUP BY 1, 2, 3
),
liq_members AS (  -- header client and participant clients of each cash-management structure
  SELECT golden_client_id, structure_id, structure_type, start_date, end_date FROM ${catalog}.silver.core_liquidity_structure
  UNION
  SELECT p.golden_client_id, s.structure_id, s.structure_type, greatest(s.start_date, p.join_date),
         CASE WHEN p.leave_date IS NULL THEN s.end_date WHEN s.end_date IS NULL THEN p.leave_date
              ELSE least(s.end_date, p.leave_date) END
  FROM ${catalog}.silver.core_liquidity_structure_participant p
  JOIN ${catalog}.silver.core_liquidity_structure s ON s.structure_id = p.structure_id
),
liq AS (
  SELECT x.golden_client_id, m.month, x.structure_type AS product_name, CAST(NULL AS DOUBLE) AS balance_usd,
         CAST(NULL AS DOUBLE) AS limit_usd, 0D AS drawn_usd, count(DISTINCT x.structure_id) AS n_instruments, 'Structure membership' AS basis
  FROM liq_members x
  JOIN months m ON x.start_date <= m.month_end_date AND (x.end_date IS NULL OR x.end_date > m.month_end_date)
  GROUP BY 1, 2, 3
),
scf AS (
  SELECT s.golden_client_id, m.month, s.programme_type AS product_name, CAST(NULL AS DOUBLE) AS balance_usd,
         sum(s.limit_usd) AS limit_usd, 0D AS drawn_usd, count(*) AS n_instruments, 'Programme' AS basis
  FROM ${catalog}.silver.scf_programme s
  JOIN months m ON s.launch_date <= m.month_end_date
  GROUP BY 1, 2, 3
),
held AS (
  SELECT golden_client_id, month, product_name, sum(balance_usd) AS balance_usd, sum(limit_usd) AS limit_usd,
         sum(drawn_usd) AS drawn_usd, sum(n_instruments) AS n_instruments, min(basis) AS basis
  FROM (SELECT * FROM dep UNION ALL SELECT * FROM fac UNION ALL SELECT * FROM trade_live
        UNION ALL SELECT * FROM liq UNION ALL SELECT * FROM scf) u
  GROUP BY 1, 2, 3
),
-- ---- flows: trade, FX and payments by month ----------------------------------------------------------------
flow_m AS (
  SELECT golden_client_id, month, product_name, sum(vol) AS vol, sum(n) AS n, max(last_d) AS last_d
  FROM (
    SELECT golden_client_id, trunc(txn_date, 'MM') AS month, product_type AS product_name,
           amount_usd AS vol, 1 AS n, txn_date AS last_d
    FROM ${catalog}.silver.trade_finance_txn
    UNION ALL
    SELECT golden_client_id, trunc(deal_date, 'MM'), product_type, notional_usd, 1, deal_date
    FROM ${catalog}.silver.tsy_fx_deal
    UNION ALL
    SELECT golden_client_id, trunc(payment_date, 'MM'),
           CASE WHEN channel = 'Host-to-Host' THEN 'Host-to-Host' WHEN channel = 'Payments API' THEN 'Payments API'
                WHEN is_cross_border THEN 'Cross-Border Payments' ELSE 'Domestic Payments' END,
           amount_usd, 1, payment_date
    FROM ${catalog}.silver.pay_payment_message
  ) u
  WHERE month BETWEEN DATE'${history_start}' AND DATE'${as_of_date}'
  GROUP BY 1, 2, 3
),
-- ---- first use per client x product (all history, incl. before the gold window) ------------------------
first_use AS (
  SELECT golden_client_id, product_name, min(d) AS first_use_date
  FROM (
    SELECT golden_client_id, account_type AS product_name, open_date AS d FROM ${catalog}.silver.core_account
    UNION ALL SELECT golden_client_id, facility_type, origination_date FROM ${catalog}.silver.credit_facility_terms
    UNION ALL SELECT golden_client_id, product_type, txn_date FROM ${catalog}.silver.trade_finance_txn
    UNION ALL SELECT golden_client_id, product_type, deal_date FROM ${catalog}.silver.tsy_fx_deal
    UNION ALL SELECT golden_client_id, product_name, min(last_d) FROM flow_m GROUP BY 1, 2
    UNION ALL SELECT golden_client_id, structure_type, start_date FROM liq_members
    UNION ALL SELECT golden_client_id, programme_type, launch_date FROM ${catalog}.silver.scf_programme
  ) u
  GROUP BY 1, 2
),
-- ---- dense spine per client x product from the first month with activity in the window ---------------------
pairs AS (
  SELECT golden_client_id, product_name, min(month) AS first_month
  FROM (SELECT golden_client_id, product_name, month FROM held UNION ALL SELECT golden_client_id, product_name, month FROM flow_m) u
  GROUP BY 1, 2
),
spine AS (
  SELECT p.golden_client_id, p.product_name, m.month, m.month_end_date, m.fiscal_year, m.fiscal_year_label, m.fiscal_quarter_label,
         h.balance_usd, h.limit_usd, h.drawn_usd, h.n_instruments, h.basis, h.golden_client_id IS NOT NULL AS is_held_at_month_end,
         coalesce(f.vol, 0) AS volume_month_usd, coalesce(f.n, 0) AS txn_count_month, f.last_d
  FROM pairs p
  JOIN months m ON m.month >= p.first_month
  LEFT JOIN held h ON h.golden_client_id = p.golden_client_id AND h.product_name = p.product_name AND h.month = m.month
  LEFT JOIN flow_m f ON f.golden_client_id = p.golden_client_id AND f.product_name = p.product_name AND f.month = m.month
),
rolled AS (
  SELECT s.*,
         sum(volume_month_usd) OVER w12 AS volume_12m_usd,
         sum(txn_count_month) OVER w12 AS txn_count_12m,
         max(last_d) OVER wall AS last_txn_date,
         max(CASE WHEN is_held_at_month_end THEN month_end_date END) OVER wall AS last_held_date
  FROM spine s
  WINDOW w12 AS (PARTITION BY golden_client_id, product_name ORDER BY month ROWS BETWEEN 11 PRECEDING AND CURRENT ROW),
         wall AS (PARTITION BY golden_client_id, product_name ORDER BY month ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
),
-- ---- family revenue, trailing 12 months (product feeds) -------------------------------------------------------
rev_m AS (
  SELECT golden_client_id, product_family, month, sum(total_revenue_usd) AS rev
  FROM ${catalog}.silver.fin_client_revenue GROUP BY 1, 2, 3
),
rev_pairs AS (SELECT golden_client_id, product_family, min(month) AS first_month FROM rev_m GROUP BY 1, 2),
rev12 AS (
  SELECT p.golden_client_id, p.product_family, m.month,
         sum(coalesce(r.rev, 0)) OVER (PARTITION BY p.golden_client_id, p.product_family ORDER BY m.month
                                       ROWS BETWEEN 11 PRECEDING AND CURRENT ROW) AS revenue_12m_usd
  FROM rev_pairs p
  JOIN months m ON m.month >= p.first_month
  LEFT JOIN rev_m r ON r.golden_client_id = p.golden_client_id AND r.product_family = p.product_family AND r.month = m.month
),
rows_ AS (
  SELECT r.*, dp.product_id, dp.product_family, dp.business_line, dp.is_deposit_product, dp.is_credit_product,
         dp.is_tb_product,
         (r.is_held_at_month_end OR r.txn_count_12m > 0) AS is_active,
         -- lending facilities (any credit-system facility type, incl. trade loans) vs everything else
         coalesce(r.basis = 'Facility limit', false) OR (r.basis IS NULL AND dp.business_line = 'Lending') AS is_lending_product,
         fu.first_use_date
  FROM rolled r
  JOIN ${catalog}.gold.dim_product dp ON dp.product_name = r.product_name
  LEFT JOIN first_use fu ON fu.golden_client_id = r.golden_client_id AND fu.product_name = r.product_name
),
cm AS (           -- client-month summary over active products
  SELECT golden_client_id, month,
         count_if(is_active) AS products_held_count,
         count(DISTINCT CASE WHEN is_active THEN product_family END) AS product_families_held_count,
         count_if(is_active AND is_deposit_product) > 0 AS holds_deposits,
         count_if(is_active AND is_lending_product AND is_held_at_month_end) > 0 AS holds_lending,
         count_if(is_active AND business_line = 'Transaction Banking' AND NOT is_deposit_product) > 0 AS holds_tb_services,
         count_if(is_active AND product_family = 'FX') > 0 AS holds_fx,
         count_if(is_active AND product_family = 'Trade Finance') > 0 AS holds_trade_finance,
         count_if(is_active) > 0 AND count_if(is_active AND NOT is_deposit_product) = 0 AS is_deposit_only,
         count_if(is_active) > 0 AND count_if(is_active AND NOT is_lending_product) = 0 AS is_single_product_lending
  FROM rows_ GROUP BY 1, 2
),
cm_dc AS (
  SELECT cm.*, dc.golden_client_sk, dc.client_group_id, dc.peer_group_id, dc.coverage_office
  FROM cm
  JOIN months m ON m.month = cm.month
  LEFT JOIN ${catalog}.gold.dim_client dc
    ON  dc.golden_client_id = cm.golden_client_id
    AND m.month_end_date <= dc.valid_to
    AND (m.month_end_date >= dc.valid_from OR dc.version_no = 1)
),
peer AS (
  SELECT peer_group_id, month, percentile(products_held_count, 0.5) AS peer_median_products_held
  FROM cm_dc WHERE products_held_count > 0 AND peer_group_id IS NOT NULL
  GROUP BY 1, 2
),
fam_rank AS (
  SELECT r.*,
         row_number() OVER (PARTITION BY r.golden_client_id, r.month, r.product_family
                            ORDER BY CASE WHEN r.is_active THEN 0 ELSE 1 END,
                                     coalesce(r.balance_usd, 0) + coalesce(r.limit_usd, 0) + coalesce(r.volume_12m_usd, 0) DESC,
                                     r.product_name) AS fam_rn,
         row_number() OVER (PARTITION BY r.golden_client_id, r.month
                            ORDER BY CASE WHEN r.is_active THEN 0 ELSE 1 END, r.product_family, r.product_name) AS cm_rn
  FROM rows_ r
)
SELECT
  f.month, f.month_end_date, f.fiscal_year, f.fiscal_year_label, f.fiscal_quarter_label,
  c.golden_client_sk, f.golden_client_id, c.client_group_id, c.coverage_office AS booking_country,
  f.product_id, f.product_name, f.product_family, f.business_line,
  f.is_active, f.is_held_at_month_end, NOT f.is_active AS is_lapsed,
  coalesce(f.basis, '12M usage') AS holding_basis,
  f.balance_usd, f.limit_usd,
  CASE WHEN f.is_deposit_product THEN coalesce(f.balance_usd, 0) ELSE 0D END AS deposit_balance_usd,
  coalesce(f.drawn_usd, 0) AS lending_drawn_usd,
  f.volume_month_usd, f.txn_count_month, f.volume_12m_usd, f.txn_count_12m,
  CASE WHEN f.business_line = 'Transaction Banking' AND f.product_family IN ('Payments', 'Trade Finance')
       THEN f.volume_12m_usd ELSE 0D END AS tb_volume_12m_usd,
  CASE WHEN f.product_family = 'FX' THEN f.volume_12m_usd ELSE 0D END AS fx_notional_12m_usd,
  coalesce(f.n_instruments, 0) AS n_instruments,
  f.first_use_date,
  CASE WHEN f.is_held_at_month_end THEN f.month_end_date
       ELSE greatest(f.last_txn_date, f.last_held_date) END AS last_use_date,
  CASE WHEN f.is_held_at_month_end THEN 0
       ELSE CAST(months_between(f.month_end_date, greatest(f.last_txn_date, f.last_held_date)) AS INT) END AS months_since_last_use,
  f.fam_rn = 1 AS is_family_primary_row,
  CASE WHEN f.fam_rn = 1 THEN coalesce(r12.revenue_12m_usd, 0) END AS revenue_12m_usd,
  f.cm_rn = 1 AS is_client_month_primary_row,
  c.products_held_count, c.product_families_held_count,
  c.holds_deposits, c.holds_lending, c.holds_tb_services, c.holds_fx, c.holds_trade_finance,
  c.is_deposit_only, c.is_single_product_lending,
  c.peer_group_id, p.peer_median_products_held,
  c.products_held_count - p.peer_median_products_held AS products_held_vs_peer_median
FROM fam_rank f
JOIN cm_dc c ON c.golden_client_id = f.golden_client_id AND c.month = f.month
LEFT JOIN peer p ON p.peer_group_id = c.peer_group_id AND p.month = f.month
LEFT JOIN rev12 r12 ON r12.golden_client_id = f.golden_client_id AND r12.product_family = f.product_family AND r12.month = f.month
