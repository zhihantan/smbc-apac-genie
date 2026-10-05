# APAC Genie - Credit Memo & Financial Spreading - general instructions

Generated from genie/credit_memo_financial_spreading/space.yaml + genie/_shared.yaml (19 lines; the space's single text instruction). Do not edit.

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
- Credit work is per legal entity: for spreads, ratios, facilities, covenants, collateral and reviews, "clients" means Client (legal entity) - list every matching row (no top-20 cut). Sunda Energi Nusantara = `Client` LIKE 'Sunda Energi Nusantara (Jakarta)%' (the lead obligor; the group also has a Singapore entity).
- Spreads and ratios are annual: filter or group by Fiscal Year (FY2023-FY2025; the latest spread year is FY2025, FY2026 has none), "last 3 years" = all three; always filter Statement Line or Ratio Name. "vs peers" = Peer Median, "peer percentile" = Percentile Rank in Peer Group %, "worse than P25" = Clients Below Peer P25.
- mv_facilities_covenants_collateral: each measure reads its own Record Type (Facility, Covenant Test, Collateral); filter Record Type only when listing covenant tests or collateral items. Date = record date; "at the latest test" = Is Latest Test; "leverage covenant" = Covenant Type 'Net Debt/EBITDA'; "breached or < 10% headroom" = Headroom Bucket IN ('Breached', '0-10%'); live book = Facility Status 'Active'; this fiscal year's tests = Fiscal Year 'FY2026'. Covenant waivers are always 0 in this data.
- Collateral needing review = LTV above 70% or a valuation older than 24 months (keep rows with MEASURE(`Collateral Needing Review`) > 0); parent support = Guarantor Type IN ('Parent Guarantee', 'Keepwell'); the parent's external rating (Japan share) = Parent Rating.
- "Indonesian mining" = Coverage Office 'ID' and Subsector IN ('Mining', 'Minerals', 'Natural Resources', 'Oil, Gas & Coal') (coal names sit under Energy); "electronics" = Subsector IN ('Electronics', 'Electronic Devices').
- mv_credit_review_workflow (statuses as of 30-Sep-2026): "FY2025 financials not yet spread" = Review Type 'Financial Spreading' + Review Fiscal Year 'FY2025' + Is Not Yet Spread; "due in Q3 FY2026" = Fiscal Quarter 'FY2026-Q3' (Date = due date); "approved in H1 FY2026" = Approval Fiscal Half 'FY2026-H1' + Outcome IN ('Approved', 'Approved with Conditions').
- Memo pack of one entity: spread P&L and balance sheet = fn_credit_memo_financials, facilities with limits and collateral = fn_credit_memo_facilities, current exposure (drawn, EAD, RWA, ECL, Stage 3, overdue) = fn_credit_memo_exposure; ratios and covenant tests come from the metric views.
- Answer with one summary sentence that states the period and filters used, then the table. Keep measure values unscaled and unrounded in the SQL; name output columns plainly.
