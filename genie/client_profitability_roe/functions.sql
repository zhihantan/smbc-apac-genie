-- UC SQL table functions for APAC Genie - Client Profitability & ROE (G1 / p07g1). Deployed by the Genie runner
-- (CREATE OR REPLACE). Hurdles come from gold.dim_threshold: RoRWA 1.2% ("below the 8% hurdle" means this
-- configured flag, DECISIONS D20), RAROC 12%, ROE 10%. Arguments are STRING only; the `-- test:` probes must return rows.
-- test: SELECT * FROM smbc_genie.gold.fn_profitability_below_hurdle_lending('ALL')
-- test: SELECT * FROM smbc_genie.gold.fn_profitability_deal_exceptions('FY2025')
-- test: SELECT * FROM smbc_genie.gold.fn_profitability_deal_exceptions('FY2025', 'Relationship')
-- test: SELECT * FROM smbc_genie.gold.fn_profitability_rm_summary('Hong Kong', 'FY2026')

CREATE OR REPLACE FUNCTION smbc_genie.gold.fn_profitability_below_hurdle_lending(
  coverage_office STRING DEFAULT 'ALL' COMMENT 'APAC coverage office (ISO-2, e.g. SG, HK) or its country name; ALL (default) = every office.'
)
RETURNS TABLE (
  `Client` STRING COMMENT 'Legal entity (golden client).',
  `Client Group` STRING COMMENT 'Client group (global parent).',
  `Coverage Office` STRING COMMENT 'APAC booking location covering the client (ISO-2).',
  `Primary RM` STRING COMMENT 'Primary RM of the client (fictional name).',
  rorwa_latest_quarter DOUBLE COMMENT 'Annualised RoRWA of the latest fiscal quarter FY2026-Q2 (fraction; 0.012 = 1.2%).',
  rorwa_hurdle DOUBLE COMMENT 'Configured RoRWA hurdle from gold.dim_threshold (0.012 = 1.2%).',
  rwa_usd DOUBLE COMMENT 'Risk-weighted assets at the Sep-2026 month-end, USD.',
  revenue_sep_2026_usd DOUBLE COMMENT 'Total revenue in September 2026, USD.'
)
COMMENT 'Japanese-corporate single-product lending relationships (clients whose only products are credit facilities) whose RoRWA is below the hurdle in the latest quarter (FY2026-Q2, Sep-2026 month-end), largest RWA first: client, group, coverage office, RM, RoRWA, hurdle, RWA and September revenue. "Below the 8% hurdle" means below the configured RoRWA hurdle (1.2%). Use for "Japanese corporates that are single-product lending below hurdle", "lending-only JC names under the hurdle", "mono-line credit relationships under the RoRWA hurdle". USD.'
RETURN
  SELECT `Client`, `Client Group`, `Coverage Office`, `Primary RM`,
         MEASURE(`RoRWA %`)           AS rorwa_latest_quarter,
         MEASURE(`RoRWA Hurdle %`)    AS rorwa_hurdle,
         MEASURE(`Average RWA USD`)   AS rwa_usd,
         MEASURE(`Total Revenue USD`) AS revenue_sep_2026_usd
  FROM smbc_genie.metrics.mv_client_profitability
  WHERE `Is Latest Month` AND `Is Japanese Corporate` AND `Is Single-Product Lending` AND `Below RoRWA Hurdle`
    AND (upper(trim(fn_profitability_below_hurdle_lending.coverage_office)) = 'ALL'
         OR `Coverage Office` IN (
           SELECT c.country_code FROM smbc_genie.gold.dim_country c
           WHERE upper(c.country_code) = upper(trim(fn_profitability_below_hurdle_lending.coverage_office))
              OR upper(c.country_name) = upper(trim(fn_profitability_below_hurdle_lending.coverage_office))))
  GROUP BY ALL;

CREATE OR REPLACE FUNCTION smbc_genie.gold.fn_profitability_deal_exceptions(
  fiscal_year STRING DEFAULT 'FY2025' COMMENT 'Fiscal year of signing, e.g. FY2025 (default) = the last four SEASONED quarters (Apr-2025 to Mar-2026), the latest deals with a realised RAROC.',
  exception_reason STRING DEFAULT 'ALL' COMMENT 'One exception reason to keep (Relationship, Strategic Client, Cross-sell Commitment or Competitive Pricing); ALL (default) = one row per reason.'
)
RETURNS TABLE (
  `Exception Reason` STRING COMMENT 'Reason the deal was approved below hurdle: Relationship, Strategic Client, Cross-sell Commitment or Competitive Pricing.',
  deals BIGINT COMMENT 'Deals approved below the 12% RAROC hurdle.',
  amount_usd DOUBLE COMMENT 'Deal (facility limit) amount, USD.',
  caught_up BIGINT COMMENT 'Below-hurdle deals whose realised RAROC is back at or above the 12% hurdle.',
  caught_up_rate DOUBLE COMMENT 'Caught up / seasoned below-hurdle deals (fraction).',
  avg_origination_raroc DOUBLE COMMENT 'Average RAROC at origination (fraction).',
  avg_realised_raroc DOUBLE COMMENT 'Average realised RAROC of the seasoned deals (fraction).'
)
COMMENT 'Deals approved below the 12% RAROC hurdle, signed in one fiscal year (default FY2025 = the last four seasoned quarters), by exception reason (optionally one reason, e.g. Relationship): deals, amount, how many caught up with the hurdle on realised RAROC, the caught-up rate and average origination vs realised RAROC. Use for "deals approved below hurdle in the last 4 quarters by exception reason and whether realised RAROC caught up", "pricing exceptions and their payback", "did the relationship exceptions pay off". FY2026 deals are not yet seasoned. USD.'
RETURN
  SELECT `Exception Reason`,
         MEASURE(`Deals`)                       AS deals,
         MEASURE(`Amount USD`)                  AS amount_usd,
         MEASURE(`Deals Caught Up`)             AS caught_up,
         MEASURE(`Caught-Up Rate %`)            AS caught_up_rate,
         MEASURE(`Average Origination RAROC %`) AS avg_origination_raroc,
         MEASURE(`Average Realised RAROC %`)    AS avg_realised_raroc
  FROM smbc_genie.metrics.mv_deal_pricing
  WHERE `Approved Below Hurdle` AND `Fiscal Year` = fn_profitability_deal_exceptions.fiscal_year
    AND (upper(trim(fn_profitability_deal_exceptions.exception_reason)) = 'ALL'
         OR lower(`Exception Reason`) = lower(trim(fn_profitability_deal_exceptions.exception_reason)))
  GROUP BY ALL;

CREATE OR REPLACE FUNCTION smbc_genie.gold.fn_profitability_rm_summary(
  rm_office STRING COMMENT 'Office where the RMs sit: ISO-2 code (SG, HK, CN, TH, ID, IN, AU, VN, MY, KR, TW, PH, NZ) or the country name, e.g. Hong Kong.',
  fiscal_year STRING DEFAULT 'FY2026' COMMENT 'Fiscal-year label, e.g. FY2026 (to date, Apr-Sep 2026). Default FY2026.'
)
RETURNS TABLE (
  `Primary RM` STRING COMMENT 'Primary RM (fictional name) based in the office.',
  revenue_fytd_usd DOUBLE COMMENT 'Total revenue of the RM clients in the fiscal year (to date), USD.',
  average_rwa_usd DOUBLE COMMENT 'Average monthly RWA of the RM clients, USD.',
  rorwa DOUBLE COMMENT 'Annualised RoRWA = 12 x relationship net profit / sum of monthly RWA (fraction; hurdle 0.012).',
  clients BIGINT COMMENT 'Clients with revenue activity in the period.'
)
COMMENT 'RM-level profitability for the RMs based in one office (Primary RM Office), for a fiscal year (default FY2026 to date): revenue, average RWA, annualised RoRWA and number of clients per RM, highest revenue first. Use for "RM-level profitability in Hong Kong", "revenue, RWA and RoRWA per RM", "RM league table by RoRWA". USD.'
RETURN
  SELECT `Primary RM`,
         MEASURE(`Total Revenue USD`) AS revenue_fytd_usd,
         MEASURE(`Average RWA USD`)   AS average_rwa_usd,
         MEASURE(`RoRWA %`)           AS rorwa,
         MEASURE(`Clients`)           AS clients
  FROM smbc_genie.metrics.mv_client_profitability
  WHERE `Fiscal Year` = fn_profitability_rm_summary.fiscal_year
    AND `Primary RM Office` IN (
      SELECT c.country_code FROM smbc_genie.gold.dim_country c
      WHERE upper(c.country_code) = upper(trim(fn_profitability_rm_summary.rm_office))
         OR upper(c.country_name) = upper(trim(fn_profitability_rm_summary.rm_office)))
  GROUP BY ALL;
