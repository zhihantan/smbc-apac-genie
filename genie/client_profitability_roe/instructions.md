# APAC Genie - Client Profitability & ROE - general instructions

Generated from genie/client_profitability_roe/space.yaml + genie/_shared.yaml (20 lines; the space's single text instruction). Do not edit.

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
- Hurdles (gold.dim_threshold): RoRWA 1.2%, RAROC 12%, ROE 10%. 'Below the (8%) hurdle' for relationships = `Below RoRWA Hurdle` (the configured 1.2% hurdle, D20), a quarter-level flag: filter one `Fiscal Quarter` or `Is Latest Month` (FY2026-Q2).
- Monthly revenue, cost, net contribution, RWA, RoRWA / RAROC and revenue per RWA: mv_client_profitability (flows add up over months; returns are annualised ratios of sums). ROE and the P&L waterfall (revenue, opex, credit cost, tax, net income, allocated capital): mv_roe_waterfall, filter ONE `Fiscal Year`. Deal pricing vs hurdle: mv_deal_pricing (signing date).
- Group `ROE %` (ratio of sums) is extreme for deposit-rich groups with little capital: rank groups with `Allocated Capital USD` > 0 (or the capital floor asked for) and show `Median Client ROE %` beside it.
- 'Deals in the last 4 quarters' = `Signed in Last 4 Quarters` (Oct-2025..Sep-2026, mostly not yet seasoned); 'did realised RAROC catch up' = `Signed in Last 4 Seasoned Quarters` (FY2025 deals) with `Deals Caught Up` and `Caught-Up Rate %`.
- Growth = the `Revenue YoY %` measure (a fraction; never compute it by hand or x100): H1 FY2026 vs H1 FY2025 in one row = `Fiscal Half` = 'FY2026-H1' with `Total Revenue USD`, `Revenue Prior Year USD`, `Revenue YoY %`; side by side = `Fiscal Half` IN ('FY2026-H1', 'FY2025-H1'). 'Lowest revenue per unit of RWA' = `Revenue per RWA %` ascending over groups with `Average RWA USD` > 0; 'credit cost above 30% of revenue' = `Credit Cost Above 30%`.
- 'RM' / 'per RM' = `Primary RM` (the RM name); 'RMs in Hong Kong' = filter `Primary RM Office` = 'HK'. Products per client by segment = `Products per Client` by `Segment`; RoRWA vs number of products = `Product Count Bucket` with `Median Client RoRWA %`, both on `Is Latest Month`.
- `Client Group` has a value dictionary (= on the exact name, e.g. 'Hayashi Marine Logistics'); match entity names (`Client`) with LIKE. Relationship-level flags (below hurdle, single-product lending, credit cost > 30%) list `Client` rows with their `Client Group`.
- Keep NULL groups: clients without a segment (blank `Segment`) or without an RM are real rows, so never add IS NOT NULL filters the user did not ask for.
- Answer with one summary sentence that states the period and filters used, then the table. Keep measure values unscaled and unrounded in the SQL; name output columns plainly.
