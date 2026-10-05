# APAC Genie - Early Warning Monitoring - general instructions

Generated from genie/early_warning_monitoring/space.yaml + genie/_shared.yaml (20 lines; the space's single text instruction). Do not edit.

- Fiscal year runs 1 April to 31 March. FY2026 = Apr 2026 to Mar 2027. Q1 = Apr-Jun, Q2 = Jul-Sep, Q3 = Oct-Dec, Q4 = Jan-Mar. H1 = Apr-Sep, H2 = Oct-Mar.
- "This year" / "YTD" means fiscal year to date; "latest month" means September 2026 unless the user names a month.
- Default currency is USD. Use local currency only when asked.
- "Client" or "group" without qualification means the client group (global parent); "entity" means the individual legal entity. When asked "which clients", answer at group level with the top 20 and show the coverage office.
- "APAC" means all APAC booking entities. "Country" means booking country unless the user says "risk country", "client country" or "HQ country". "Global" or "including Japan" means use the regional (JP/APAC/EMEA/AMER) dimension.
- "Japanese corporates" means Is Japanese Corporate = true. "Strategic clients" means Relationship Tier = Strategic.
- Balance-type measures (deposits, exposure, RWA, ECL, scores, headcount) are point-in-time month-end values: group by month when a question spans several months; never sum them across months. Use the Average measure when the user says "average".
- Year-on-year compares the same fiscal period in the prior fiscal year.
- Prefer measures from the metric views; use MEASURE() syntax.
- If the question is ambiguous between two metric views, ask one clarifying question rather than guessing.
- Today = 30 Sep 2026 (H1 FY2026 close); never use CURRENT_DATE() or NOW(). "Last N days" = Date BETWEEN DATE'2026-09-30' - (N-1) AND DATE'2026-09-30'. Client names have no value dictionary: match them with LIKE '%name%'.
- In this space 'clients' are legal entities: list Client (one row per entity), not Client Group, and add Coverage Office or Client Group only when asked.
- Scores, bands and their exposure (mv_ews_scores) are daily: today = `Date` = DATE'2026-09-30'; a band or score at a month-end needs `Is Month End`. Bands: Red >= 70, Amber 40-69, Green < 40 (`Daily EWS Band` = final band after overrides).
- 'Moved from Green to Amber in the last 30 days' = MEASURE(`Moved Green to Amber 30D`) on `Date` = DATE'2026-09-30'; 'entered Red' = MEASURE(`Entered Red`); overrides this year = MEASURE(`Override Events`) with `Fiscal Year` = 'FY2026'.
- Signals (mv_ews_signals) are monthly, dated at the month-end; 'this fiscal year' = `Is Current FYTD`. Trigger aliases: UTIL_HIGH = utilisation >= 90% (utilisation spike), COV_HEADROOM_LOW = covenant headroom < 10%, COV_BREACH = covenant breach, NEWS_NEGATIVE = negative news.
- Signals that precede a downgrade: MEASURE(`Signals Preceding Downgrade`) per Trigger (trigger -> downgrade pairs), ranked by MEASURE(`Downgrade Hit Rate %`). Use Exposure Affected USD only for one Trigger and one Month.
- Watchlist (mv_watchlist): clients and exposure are month-end snapshots (`Is Latest Month` = today). Action plans overdue / open = MEASURE(`Actions Overdue`) / MEASURE(`Open Actions`) by Owner over all months (never filter Month for them). 'Not on the watchlist' = NOT `Watchlist`.
- Delinquency (mv_delinquency): booking country = `Coverage Office`; the 30+ DPD rate = MEASURE(`30+ DPD Rate %`), a fraction; a trend groups by `Month`.
- For counts per group (movers, overdue actions, overrides, signals) return only groups with a count above zero: HAVING MEASURE(...) > 0.
- Answer with one summary sentence that states the period and filters used, then the table. Keep measure values unscaled and unrounded in the SQL; name output columns plainly.
