-- UC SQL table functions for APAC Genie - Transactional Banking: Trade & Supply Chain Finance (G3 / p07g3).
-- Deployed by the Genie runner (CREATE OR REPLACE). Client-group parameters match the exact group name first
-- (e.g. 'Kinokawa Precision'); only when no group has that exact name is the text matched as a LIKE fragment.
-- Every function reads the space's metric views (MEASURE()) or gold as of 30-Sep-2026 - never CURRENT_DATE.

CREATE OR REPLACE FUNCTION smbc_genie.gold.fn_scf_anchor_candidates(
  min_suppliers STRING DEFAULT '50' COMMENT 'Minimum number of distinct suppliers paid through SMBC in the last 12 months, as text (default ''50'', dim_threshold SCF_ANCHOR_MIN_SUPPLIERS).'
)
RETURNS TABLE (
  `Client` STRING COMMENT 'Legal entity (buyer) paying the suppliers (golden client display name).',
  `Segment` STRING COMMENT 'Client segment of the buyer (e.g. Japanese Corporate).',
  `Coverage Office` STRING COMMENT 'APAC booking location covering the buyer (ISO-2, e.g. SG).',
  distinct_suppliers_paid BIGINT COMMENT 'Distinct suppliers paid through SMBC (outbound supplier payments) Oct-2025 to Sep-2026.',
  supplier_payments_usd DOUBLE COMMENT 'Value of those outbound supplier payments Oct-2025 to Sep-2026, USD.'
)
COMMENT 'Anchor-buyer candidates for NEW supply chain finance (SCF) programmes: legal entities paying at least min_suppliers distinct suppliers through SMBC in the last 12 months (Oct-2025 to Sep-2026, outbound supplier payments) that are not already the anchor of a live SCF programme, with their supplier payment value. Use for "SCF anchor candidates", "buyers paying 50+ suppliers through us", "who could anchor a new supplier finance programme". USD; default min_suppliers = 50.'
RETURN
  WITH sup AS (
    SELECT `Client`, `Segment`, `Coverage Office`,
           MEASURE(`Distinct Suppliers Paid`) AS distinct_suppliers_paid,
           MEASURE(`Payment Volume USD`)      AS supplier_payments_usd
    FROM smbc_genie.metrics.mv_tb_payments
    WHERE `Month` >= DATE'2025-10-01' AND `Direction` = 'Outbound' AND `Payment Purpose` = 'Supplier'
    GROUP BY ALL
  ), anchors AS (
    SELECT `Client`, MEASURE(`Programmes`) AS programmes
    FROM smbc_genie.metrics.mv_tb_scf
    WHERE `Is Latest Month`
    GROUP BY ALL
  )
  SELECT s.`Client`, s.`Segment`, s.`Coverage Office`, s.distinct_suppliers_paid, s.supplier_payments_usd
  FROM sup s LEFT ANTI JOIN anchors a ON a.`Client` = s.`Client`
  WHERE s.distinct_suppliers_paid >= coalesce(try_cast(fn_scf_anchor_candidates.min_suppliers AS INT), 50);

CREATE OR REPLACE FUNCTION smbc_genie.gold.fn_trade_corridor_growth(
  fiscal_half STRING DEFAULT 'FY2026-H1' COMMENT 'Fiscal half to measure, e.g. FY2026-H1 (Apr-Sep 2026). It is compared with the same half one year earlier (FY2025-H1).',
  min_prior_issuance_usd STRING DEFAULT '20000000' COMMENT 'Minimum trade-finance issuance in the earlier half for a corridor to be ranked, USD as text (default ''20000000'' = USD 20m).',
  top_n STRING DEFAULT '10' COMMENT 'How many corridors to return, fastest-growing first, as text (default ''10''; ''1000'' = every qualifying corridor).'
)
RETURNS TABLE (
  `Corridor` STRING COMMENT 'Goods corridor origin->destination country (ISO-2), e.g. CN->VN.',
  issuance_prior_half_usd DOUBLE COMMENT 'Trade-finance issuance on the corridor in the same half one year earlier (e.g. H1 FY2025), USD.',
  issuance_half_usd DOUBLE COMMENT 'Trade-finance issuance on the corridor in the fiscal half (e.g. H1 FY2026), USD.',
  issuance_growth_yoy DOUBLE COMMENT 'Year-on-year growth of the issuance (fraction; 0.32 = +32%).'
)
COMMENT 'The top_n FASTEST-GROWING trade corridors (already ranked and limited - default the top 10): SMBC trade-finance issuance (LCs, guarantees, collections, trade loans, receivables purchase) per goods corridor (origin->destination) in a fiscal half versus the same half one year earlier, for corridors with at least min_prior_issuance_usd in the earlier half, highest growth first. Use for "the 10 fastest-growing trade corridors H1 FY2026 vs H1 FY2025", "corridor issuance growth ranking". USD; defaults FY2026-H1 vs FY2025-H1, USD 20m minimum, top 10.'
RETURN
  WITH h AS (
    SELECT `Corridor`, `Fiscal Half`, MEASURE(`Issuance USD`) AS issuance_usd
    FROM smbc_genie.metrics.mv_tb_trade_finance
    WHERE `Fiscal Half` IN (fn_trade_corridor_growth.fiscal_half,
                            concat('FY', CAST(CAST(substr(fn_trade_corridor_growth.fiscal_half, 3, 4) AS INT) - 1 AS STRING),
                                   substr(fn_trade_corridor_growth.fiscal_half, 7)))
    GROUP BY ALL
  ), tagged AS (
    SELECT `Corridor`, `Fiscal Half` = fn_trade_corridor_growth.fiscal_half AS is_half, issuance_usd FROM h
  ), c AS (
    SELECT `Corridor`,
           sum(CASE WHEN NOT is_half THEN issuance_usd END) AS issuance_prior_half_usd,
           sum(CASE WHEN is_half THEN issuance_usd END)     AS issuance_half_usd
    FROM tagged GROUP BY `Corridor`
  )
  , ranked AS (
    SELECT `Corridor`, issuance_prior_half_usd, issuance_half_usd,
           issuance_half_usd / issuance_prior_half_usd - 1 AS issuance_growth_yoy,
           row_number() OVER (ORDER BY issuance_half_usd / issuance_prior_half_usd - 1 DESC NULLS LAST, `Corridor`) AS rn
    FROM c
    WHERE issuance_prior_half_usd >= coalesce(try_cast(fn_trade_corridor_growth.min_prior_issuance_usd AS DOUBLE), 20000000)
  )
  SELECT `Corridor`, issuance_prior_half_usd, issuance_half_usd, issuance_growth_yoy
  FROM ranked
  WHERE rn <= coalesce(try_cast(fn_trade_corridor_growth.top_n AS INT), 10);

CREATE OR REPLACE FUNCTION smbc_genie.gold.fn_trade_group_profile(
  client_group STRING COMMENT 'Client group (global parent) name, e.g. Kinokawa Precision. Exact group name first; otherwise a name fragment matched with LIKE.'
)
RETURNS TABLE (
  `Client` STRING COMMENT 'Legal entity of the group (golden client display name).',
  `Coverage Office` STRING COMMENT 'APAC booking location covering the entity (ISO-2).',
  trade_outstanding_usd DOUBLE COMMENT 'Trade finance outstanding at 30-Sep-2026, USD.',
  live_instruments BIGINT COMMENT 'Trade instruments outstanding at 30-Sep-2026.',
  issuance_fytd_usd DOUBLE COMMENT 'Trade-finance issuance in FY2026 to date (Apr-Sep 2026), USD.',
  fee_yield_fytd_bps DOUBLE COMMENT 'Trade fee income / issuance in FY2026 to date, basis points.',
  discrepancy_rate_fytd DOUBLE COMMENT 'LC document discrepancy rate in FY2026 to date (discrepant / examined presentations; fraction).',
  scf_programmes_anchored BIGINT COMMENT 'Live SCF programmes the entity anchors at 30-Sep-2026 (0 = none).'
)
COMMENT 'Trade and supply chain finance profile of every APAC legal entity of ONE client group: trade finance outstanding and live instruments today (30-Sep-2026), issuance and fee yield in FY2026 to date, the LC document discrepancy rate in FY2026 to date and the SCF programmes it anchors. Entities with no trade activity show 0 / null. Use for "trade profile of <group>", "<group> trade finance by entity", "does <group> use trade finance or SCF with us". USD.'
RETURN
  WITH grp AS (
    SELECT g.group_name FROM smbc_genie.gold.dim_client_group g
    WHERE lower(g.group_name) = lower(trim(fn_trade_group_profile.client_group))
       OR (lower(g.group_name) LIKE concat('%', lower(trim(fn_trade_group_profile.client_group)), '%')
           AND NOT EXISTS (SELECT 1 FROM smbc_genie.gold.dim_client_group x
                           WHERE lower(x.group_name) = lower(trim(fn_trade_group_profile.client_group))))
  ), ents AS (
    SELECT c.display_name, c.coverage_office, c.trade_outstanding_usd, c.trade_instruments_outstanding
    FROM smbc_genie.gold.vw_client_360 c JOIN grp ON grp.group_name = c.group_name
  ), iss AS (
    SELECT `Client`, MEASURE(`Issuance USD`) AS issuance_usd, MEASURE(`Fee Yield bps`) AS fee_yield_bps
    FROM smbc_genie.metrics.mv_tb_trade_finance
    WHERE `Fiscal Year` = 'FY2026' AND `Client Group` IN (SELECT group_name FROM grp)
    GROUP BY ALL
  ), ops AS (
    SELECT `Client`, MEASURE(`Discrepancy Rate %`) AS discrepancy_rate
    FROM smbc_genie.metrics.mv_trade_operations
    WHERE `Fiscal Year` = 'FY2026' AND `Client Group` IN (SELECT group_name FROM grp)
    GROUP BY ALL
  ), scf AS (
    SELECT `Client`, MEASURE(`Programmes`) AS programmes
    FROM smbc_genie.metrics.mv_tb_scf
    WHERE `Is Latest Month`
    GROUP BY ALL
  )
  SELECT e.display_name AS `Client`, e.coverage_office AS `Coverage Office`,
         coalesce(e.trade_outstanding_usd, 0) AS trade_outstanding_usd,
         CAST(coalesce(e.trade_instruments_outstanding, 0) AS BIGINT) AS live_instruments,
         coalesce(i.issuance_usd, 0) AS issuance_fytd_usd, i.fee_yield_bps AS fee_yield_fytd_bps,
         o.discrepancy_rate AS discrepancy_rate_fytd,
         CAST(coalesce(s.programmes, 0) AS BIGINT) AS scf_programmes_anchored
  FROM ents e
  LEFT JOIN iss i ON i.`Client` = e.display_name
  LEFT JOIN ops o ON o.`Client` = e.display_name
  LEFT JOIN scf s ON s.`Client` = e.display_name;

-- test: SELECT * FROM smbc_genie.gold.fn_scf_anchor_candidates('50')
-- test: SELECT * FROM smbc_genie.gold.fn_trade_corridor_growth('FY2026-H1', '20000000', '10')
-- test: SELECT * FROM smbc_genie.gold.fn_trade_group_profile('Kinokawa Precision')
