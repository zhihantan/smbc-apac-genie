-- UC SQL table functions for APAC Genie - Transactional Banking: Cash, Payments & Liquidity (G3 / p07g3).
-- Deployed by the Genie runner (CREATE OR REPLACE). Client-group parameters match the exact group name first
-- (e.g. 'Kinokawa Precision'); only when no group has that exact name is the text matched as a LIKE fragment.
-- Every function reads the space's metric views (MEASURE()) or gold as of 30-Sep-2026 - never CURRENT_DATE.

CREATE OR REPLACE FUNCTION smbc_genie.gold.fn_casa_movement_by_country(
  report_month STRING COMMENT 'Month-end to report as text YYYY-MM or YYYY-MM-DD: ''2026-09'' (or ''2026-09-01'') = end-September 2026, the latest month. It is compared with the previous month-end.'
)
RETURNS TABLE (
  `Booking Country` STRING COMMENT 'APAC booking country of the deposit accounts (ISO-2: SG, HK, CN, TH, ID, IN, AU, VN, MY, KR, TW, PH, NZ).',
  casa_balance_usd DOUBLE COMMENT 'CASA (current + savings account) balance at the month-end, USD.',
  casa_balance_prior_month_usd DOUBLE COMMENT 'CASA balance at the previous month-end, USD (e.g. 31-Aug-2026 for September 2026).',
  casa_change_vs_prior_month_usd DOUBLE COMMENT 'CASA balance minus the previous month-end, USD (negative = CASA outflow).'
)
COMMENT 'CASA movement by booking country for one month: the CASA (current and savings account) balance at the month-end, the balance at the previous month-end and the change, per APAC booking country. Use for "CASA by country end of September vs August", "CASA movement by booking country for <month>", "month-on-month CASA change by country". USD; pass the month as text, e.g. ''2026-09'' (the latest month, September 2026).'
RETURN
  SELECT `Booking Country`,
         MEASURE(`CASA Balance USD`)               AS casa_balance_usd,
         MEASURE(`CASA Balance Prior Month USD`)   AS casa_balance_prior_month_usd,
         MEASURE(`CASA Change vs Prior Month USD`) AS casa_change_vs_prior_month_usd
  FROM smbc_genie.metrics.mv_tb_deposits
  WHERE `Month` = try_cast(concat(substr(trim(fn_casa_movement_by_country.report_month), 1, 7), '-01') AS DATE)
  GROUP BY ALL;

CREATE OR REPLACE FUNCTION smbc_genie.gold.fn_tb_group_cash_profile(
  client_group STRING COMMENT 'Client group (global parent) name, e.g. Kinokawa Precision. Exact group name first; otherwise a name fragment matched with LIKE.'
)
RETURNS TABLE (
  `Client` STRING COMMENT 'Legal entity of the group (golden client display name).',
  `Coverage Office` STRING COMMENT 'APAC booking location covering the entity (ISO-2, e.g. SG, HK); deposits and payments are booked there.',
  deposits_usd DOUBLE COMMENT 'Total deposits (CASA + time deposits) at 30-Sep-2026, USD.',
  casa_usd DOUBLE COMMENT 'Current and savings account (CASA) balances at 30-Sep-2026, USD.',
  time_deposits_usd DOUBLE COMMENT 'Time-deposit balances at 30-Sep-2026, USD.',
  casa_ratio DOUBLE COMMENT 'CASA / total deposits at 30-Sep-2026 (fraction; 0.60 = 60%).',
  td_maturing_90d_usd DOUBLE COMMENT 'Time-deposit principal maturing in the next 90 days (to 29-Dec-2026), USD.',
  payments_last_90d_usd DOUBLE COMMENT 'Value of the entity''s payments (in and out) in the last 90 days to 30-Sep-2026, USD.',
  payments_other_bank_share_90d DOUBLE COMMENT 'Share of that payment value with counterparties at other banks (fraction) - wallet leakage.',
  in_liquidity_structure BOOLEAN COMMENT 'True when the entity holds an account in an active liquidity structure (cash pool) at 30-Sep-2026.'
)
COMMENT 'Cash-management profile TODAY (30-Sep-2026) of every APAC legal entity of ONE client group: deposits, CASA, time deposits, CASA ratio, time deposits maturing in the next 90 days, payments of the last 90 days, the share paid to / received from other banks, and whether the entity sits in a cash pool. Use for "cash profile of <group>", "<group> deposits and payments by entity", "transaction banking snapshot of <group>". USD.'
RETURN
  WITH grp AS (
    SELECT g.group_name FROM smbc_genie.gold.dim_client_group g
    WHERE lower(g.group_name) = lower(trim(fn_tb_group_cash_profile.client_group))
       OR (lower(g.group_name) LIKE concat('%', lower(trim(fn_tb_group_cash_profile.client_group)), '%')
           AND NOT EXISTS (SELECT 1 FROM smbc_genie.gold.dim_client_group x
                           WHERE lower(x.group_name) = lower(trim(fn_tb_group_cash_profile.client_group))))
  ), pooled AS (
    SELECT DISTINCT golden_client_id FROM smbc_genie.gold.mvb_group_liquidity
    WHERE record_type = 'Structure member' AND is_latest_month_end
  )
  SELECT c.display_name AS `Client`, c.coverage_office AS `Coverage Office`,
         c.deposits_usd, c.casa_usd, c.time_deposits_usd, c.casa_ratio, c.td_maturing_90d_usd,
         c.payments_last_90d_usd, c.payments_other_bank_share_90d,
         p.golden_client_id IS NOT NULL AS in_liquidity_structure
  FROM smbc_genie.gold.vw_client_360 c
  JOIN grp ON grp.group_name = c.group_name
  LEFT JOIN pooled p ON p.golden_client_id = c.golden_client_id;

CREATE OR REPLACE FUNCTION smbc_genie.gold.fn_tb_payment_drop_steady_deposits(
  fiscal_half STRING DEFAULT 'FY2026-H1' COMMENT 'Fiscal half to test, e.g. FY2026-H1 (Apr-Sep 2026, the latest closed half). It is compared with the same half one year earlier (FY2025-H1).'
)
RETURNS TABLE (
  `Client` STRING COMMENT 'Legal entity (golden client display name).',
  payments_prior_half_usd DOUBLE COMMENT 'Payment volume through SMBC in the same half one year earlier (e.g. H1 FY2025), USD (at least USD 1m).',
  payments_half_usd DOUBLE COMMENT 'Payment volume through SMBC in the fiscal half (e.g. H1 FY2026), USD (0 when none).',
  payment_volume_yoy DOUBLE COMMENT 'Year-on-year change of the payment volume (fraction; -0.40 = down 40%).',
  avg_deposits_change DOUBLE COMMENT 'Change of the average month-end deposits between the two halves (fraction).'
)
COMMENT 'Payment-attrition watch list: legal entities whose payment volume through SMBC fell by more than 30% year on year (the fiscal half vs the same half a year earlier, dim_threshold PAYMENT_VOLUME_DROP_YOY; at least USD 1m of payments in the earlier half) while their average month-end deposits held steady (within +/-10%) - transaction flows leaving while balances stay. Use for "payments down more than 30% YoY but balances steady", "clients moving payments to other banks", "payment attrition with stable deposits". USD; default fiscal_half FY2026-H1 = H1 FY2026 vs H1 FY2025.'
RETURN
  WITH pay AS (
    SELECT `Client`, `Fiscal Half`, MEASURE(`Payment Volume USD`) AS payment_volume_usd
    FROM smbc_genie.metrics.mv_tb_payments
    WHERE `Fiscal Half` IN (fn_tb_payment_drop_steady_deposits.fiscal_half,
                            concat('FY', CAST(CAST(substr(fn_tb_payment_drop_steady_deposits.fiscal_half, 3, 4) AS INT) - 1 AS STRING),
                                   substr(fn_tb_payment_drop_steady_deposits.fiscal_half, 7)))
    GROUP BY ALL
  ), pay_tagged AS (
    SELECT `Client`, `Fiscal Half` = fn_tb_payment_drop_steady_deposits.fiscal_half AS is_half, payment_volume_usd
    FROM pay
  ), p AS (
    SELECT `Client`,
           sum(CASE WHEN NOT is_half THEN payment_volume_usd END) AS payments_prior_half_usd,
           sum(CASE WHEN is_half THEN payment_volume_usd END)     AS payments_half_usd
    FROM pay_tagged GROUP BY `Client`
  ), dep AS (
    SELECT `Client`, `Fiscal Half`, `Month`, MEASURE(`End of Month Balance USD`) AS deposits_usd
    FROM smbc_genie.metrics.mv_tb_deposits
    WHERE `Fiscal Half` IN (fn_tb_payment_drop_steady_deposits.fiscal_half,
                            concat('FY', CAST(CAST(substr(fn_tb_payment_drop_steady_deposits.fiscal_half, 3, 4) AS INT) - 1 AS STRING),
                                   substr(fn_tb_payment_drop_steady_deposits.fiscal_half, 7)))
    GROUP BY ALL
  ), dep_tagged AS (
    SELECT `Client`, `Fiscal Half` = fn_tb_payment_drop_steady_deposits.fiscal_half AS is_half, deposits_usd
    FROM dep
  ), d AS (
    SELECT `Client`,
           sum(CASE WHEN NOT is_half THEN deposits_usd END) / 6 AS avg_deposits_prior_usd,
           sum(CASE WHEN is_half THEN deposits_usd END) / 6     AS avg_deposits_usd
    FROM dep_tagged GROUP BY `Client`
  )
  SELECT p.`Client`, p.payments_prior_half_usd, coalesce(p.payments_half_usd, 0) AS payments_half_usd,
         coalesce(p.payments_half_usd, 0) / p.payments_prior_half_usd - 1 AS payment_volume_yoy,
         d.avg_deposits_usd / d.avg_deposits_prior_usd - 1 AS avg_deposits_change
  FROM p JOIN d ON d.`Client` = p.`Client`
  WHERE p.payments_prior_half_usd >= 1000000
    AND coalesce(p.payments_half_usd, 0) / p.payments_prior_half_usd - 1 < -0.30
    AND abs(d.avg_deposits_usd / d.avg_deposits_prior_usd - 1) <= 0.10;

-- test: SELECT * FROM smbc_genie.gold.fn_casa_movement_by_country('2026-09')
-- test: SELECT * FROM smbc_genie.gold.fn_tb_group_cash_profile('Kinokawa Precision')
-- test: SELECT * FROM smbc_genie.gold.fn_tb_payment_drop_steady_deposits('FY2026-H1')
