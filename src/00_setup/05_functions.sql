-- Phase 2 — shared SQL functions in ${catalog}.gold (brief Appendix; DECISIONS D16, D17).
-- Fiscal functions mirror smbc_genie_lib.fiscal exactly (asserted in tests/test_fiscal_sql.py).
-- fn_usd depends on gold.fx_rate_daily and is created in Phase 5, not here.

CREATE OR REPLACE FUNCTION ${catalog}.gold.fn_fiscal_year(d DATE)
  RETURNS INT
  COMMENT 'Japanese fiscal year starting-calendar-year (Apr-Mar). FY2026 = Apr 2026-Mar 2027.'
  RETURN CASE WHEN month(d) >= 4 THEN year(d) ELSE year(d) - 1 END;

CREATE OR REPLACE FUNCTION ${catalog}.gold.fn_fiscal_year_label(d DATE)
  RETURNS STRING
  COMMENT 'Fiscal year label, e.g. FY2026.'
  RETURN concat('FY', cast(${catalog}.gold.fn_fiscal_year(d) AS STRING));

CREATE OR REPLACE FUNCTION ${catalog}.gold.fn_fiscal_quarter(d DATE)
  RETURNS INT
  COMMENT 'Fiscal quarter 1-4 (Q1 Apr-Jun, Q2 Jul-Sep, Q3 Oct-Dec, Q4 Jan-Mar).'
  RETURN cast((pmod(month(d) - 4, 12)) / 3 AS INT) + 1;

CREATE OR REPLACE FUNCTION ${catalog}.gold.fn_fiscal_half(d DATE)
  RETURNS STRING
  COMMENT 'Fiscal half: H1 (Apr-Sep) or H2 (Oct-Mar).'
  RETURN CASE WHEN ${catalog}.gold.fn_fiscal_quarter(d) <= 2 THEN 'H1' ELSE 'H2' END;

CREATE OR REPLACE FUNCTION ${catalog}.gold.fn_fiscal_month_no(d DATE)
  RETURNS INT
  COMMENT 'Fiscal month number 1-12 with April = 1, March = 12.'
  RETURN pmod(month(d) - 4, 12) + 1;

CREATE OR REPLACE FUNCTION ${catalog}.gold.fn_as_of_date()
  RETURNS DATE
  COMMENT 'The demo "today" (H1 FY2026 close). Genie must use this, never CURRENT_DATE().'
  RETURN DATE'${as_of_date}';

CREATE OR REPLACE FUNCTION ${catalog}.gold.fn_latest_closed_month()
  RETURNS DATE
  COMMENT 'Last calendar day of the latest closed month (= month-end of fn_as_of_date()).'
  RETURN last_day(${catalog}.gold.fn_as_of_date());
