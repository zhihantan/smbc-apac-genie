-- APAC Genie - Credit Memo & Financial Spreading: UC SQL table functions (trusted assets, genie/README.md).
-- The three parts of the credit memo pack for ONE legal entity (brief 5.3 must-answer 1). Each returns exactly
-- the columns of the matching must-answer query (metrics/_answers/credit_memo_financial_spreading.sql Q1, Q1c,
-- Q1e), so a function call and the metric-view query give the same answer. Entity filter: LIKE '%name%' on the
-- client display name ("Sunda Energi Nusantara (Jakarta)" = the storyline lead obligor; the group has a second
-- entity in Singapore). A fragment matching several entities adds their figures together.
-- test: SELECT * FROM ${catalog}.gold.fn_credit_memo_financials('Sunda Energi Nusantara (Jakarta)')
-- test: SELECT * FROM ${catalog}.gold.fn_credit_memo_facilities('Sunda Energi Nusantara (Jakarta)')
-- test: SELECT * FROM ${catalog}.gold.fn_credit_memo_exposure('Sunda Energi Nusantara (Jakarta)')

CREATE OR REPLACE FUNCTION ${catalog}.gold.fn_credit_memo_financials(
  client_name STRING COMMENT 'Legal-entity name or a fragment of it, matched with LIKE on the client display name, e.g. Sunda Energi Nusantara (Jakarta) or Kinokawa Precision (Singapore)'
)
RETURNS TABLE (
  fiscal_year STRING COMMENT 'Statement fiscal year label FY2023-FY2025 (FY2025 = the year ending Dec-2025 or Mar-2026, the latest spread year)',
  statement_type STRING COMMENT 'P&L or Balance Sheet',
  statement_line STRING COMMENT 'Standard spread line, e.g. Revenue, COGS, EBITDA, Net Income, Cash, Total Debt, Net Debt, Equity',
  line_order INT COMMENT 'Display order of the line in the spread (P&L 10-110, Balance Sheet 210-310) - order by it',
  amount_usd DOUBLE COMMENT 'Statement amount in USD: annual flow for P&L lines, year-end balance for balance-sheet lines'
)
COMMENT 'Credit memo pack part 1, spread financials: the spread P&L and balance sheet of one legal entity for its last three spread years (FY2023-FY2025) in USD, one row per fiscal year and statement line. Use for credit memo pack, spread financials, last 3 years of P&L and balance sheet of a client. Pass the legal-entity name (for Sunda Energi Nusantara pass Sunda Energi Nusantara (Jakarta), the lead obligor); a fragment matching several entities adds their amounts. Ratios vs peers are in mv_financial_ratios_vs_peers.'
RETURN
  SELECT `Fiscal Year`, `Statement Type`, `Statement Line`, CAST(`Line Order` AS INT), MEASURE(`Amount USD`)
  FROM ${catalog}.metrics.mv_financial_spreads
  WHERE `Client` LIKE CONCAT('%', fn_credit_memo_financials.client_name, '%')
    AND `Statement Type` IN ('P&L', 'Balance Sheet')
  GROUP BY ALL;

CREATE OR REPLACE FUNCTION ${catalog}.gold.fn_credit_memo_facilities(
  client_name STRING COMMENT 'Legal-entity name or a fragment of it, matched with LIKE on the client display name, e.g. Sunda Energi Nusantara (Jakarta)'
)
RETURNS TABLE (
  facility STRING COMMENT 'Credit facility id (FAC-nnnnnn)',
  facility_type STRING COMMENT 'Term Loan, Revolving Credit Facility, Syndicated Loan, Bilateral Loan, Overdraft or Trade Loan',
  facility_status STRING COMMENT 'Facility status at 30-Sep-2026: Active, Matured or Prepaid',
  security_type STRING COMMENT 'Security of the facility: Unsecured, Cash, Receivables, Inventory, Fixed Assets or Real Estate',
  guarantor_type STRING COMMENT 'Credit support: Parent Guarantee, Keepwell, Corporate Guarantee, Sponsor Support or None',
  limit_usd DOUBLE COMMENT 'Committed limit in USD at 30-Sep-2026',
  drawn_usd DOUBLE COMMENT 'Drawn balance in USD at 30-Sep-2026 (current funded exposure of the facility)',
  margin_bps DOUBLE COMMENT 'Lending margin over the base rate in basis points',
  collateral_value_usd DOUBLE COMMENT 'Appraised value of the collateral pledged to the facility in USD (null when none)',
  max_ltv DOUBLE COMMENT 'Highest loan-to-value of the facility collateral as a fraction (0.72 = 72%); null when none'
)
COMMENT 'Credit memo pack part 2, facilities: every credit facility of one legal entity at 30-Sep-2026 with facility type, status, security type, guarantor type, committed limit, drawn, margin (bps), collateral value and highest LTV. Use for the facilities / limits / collateral section of a credit memo pack. Pass the legal-entity name (for Sunda Energi Nusantara pass Sunda Energi Nusantara (Jakarta)). Covenant tests are in mv_facilities_covenants_collateral (Record Type Covenant Test).'
RETURN
  SELECT `Facility`, `Facility Type`, `Facility Status`, `Security Type`, `Guarantor Type`,
         MEASURE(`Limit USD`), MEASURE(`Drawn USD`), MEASURE(`Weighted Margin bps`),
         MEASURE(`Collateral Value USD`), MEASURE(`Max LTV %`)
  FROM ${catalog}.metrics.mv_facilities_covenants_collateral
  WHERE `Client` LIKE CONCAT('%', fn_credit_memo_facilities.client_name, '%')
  GROUP BY ALL;

CREATE OR REPLACE FUNCTION ${catalog}.gold.fn_credit_memo_exposure(
  client_name STRING COMMENT 'Legal-entity name or a fragment of it, matched with LIKE on the client display name, e.g. Sunda Energi Nusantara (Jakarta)'
)
RETURNS TABLE (
  drawn_usd DOUBLE COMMENT 'Drawn credit at the 30-Sep-2026 month-end in USD',
  limit_usd DOUBLE COMMENT 'Committed credit limits at the 30-Sep-2026 month-end in USD',
  ead_usd DOUBLE COMMENT 'Exposure at default (EAD) at 30-Sep-2026 in USD',
  rwa_usd DOUBLE COMMENT 'Risk-weighted assets (RWA) at 30-Sep-2026 in USD',
  ecl_usd DOUBLE COMMENT 'IFRS 9 expected credit loss provision (ECL) at 30-Sep-2026 in USD',
  stage3_exposure_usd DOUBLE COMMENT 'Drawn exposure in IFRS 9 Stage 3 (credit-impaired) at 30-Sep-2026 in USD',
  overdue_usd DOUBLE COMMENT 'Amount past due (arrears) at 30-Sep-2026 in USD'
)
COMMENT 'Credit memo pack part 3, current exposure: drawn, limit, EAD, RWA, ECL, Stage 3 exposure and overdue (arrears) amount of one legal entity at the 30-Sep-2026 month-end (USD, one row). Use for current exposure, EAD, RWA, ECL, impairment or arrears of a client. Pass the legal-entity name (for Sunda Energi Nusantara pass Sunda Energi Nusantara (Jakarta)); a group-name fragment returns the total of its entities.'
RETURN
  SELECT MEASURE(`Drawn USD`), MEASURE(`Limit USD`), MEASURE(`EAD USD`), MEASURE(`RWA USD`),
         MEASURE(`ECL USD`), MEASURE(`Stage 3 Exposure USD`), MEASURE(`Overdue Amount USD`)
  FROM ${catalog}.metrics.mv_delinquency
  WHERE `Client` LIKE CONCAT('%', fn_credit_memo_exposure.client_name, '%') AND `Is Latest Month`;
