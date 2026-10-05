# APAC Genie - Opportunity Identification - general instructions

Generated from genie/opportunity_identification/space.yaml + genie/_shared.yaml (20 lines; the space's single text instruction). Do not edit.

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
- 'Open opportunities' = open SIGNALS in mv_opportunity_signals (`Status` = 'Open'; `Open Signals`, `Open Estimated Revenue USD`) unless the user says pipeline, deals or CRM opportunities (mv_pipeline).
- Signals are entity-level events dated by detection (`Date`); 'last 60 days' = `Date` BETWEEN DATE'2026-08-02' AND DATE'2026-09-30'. `Signal Type` codes: FX_FLOW_VIA_OTHER_BANK, DEPOSIT_SURPLUS, FACILITY_MATURING_12M, TRADE_CORRIDOR_GROWTH, CAPEX_NEWS (capex / expansion news), MA_NEWS, SLL_ELIGIBLE, SCF_ANCHOR_CANDIDATE, LOAN_SERVICE_TO_OTHER_BANK, PRODUCT_GAP_VS_PEERS.
- FX flow via other banks = `Latest Observed Amount USD` (the current 12-month flow; never sum `Observed Amount USD` for it). 'Not actioned by an RM' = NOT `Is Actioned`; 'no time-deposit or investment product' = NOT `Holds TD or Investment`; 'no refinancing in the pipeline' = NOT `Has Open Lending Opportunity` on `Status` = 'Open' signals.
- mv_pipeline calendar fields (`Fiscal Year`, `Month`, `Date`) are the CREATION date ('created this fiscal year' = `Fiscal Year` = 'FY2026'); forecasts use `Expected Close Quarter` / `Expected Close Half` (e.g. 'FY2026-H2') with `Is Open`. `Win Rate %` = won / (won + lost).
- Next-best-product scores are monthly snapshots: filter `Is Latest Month`; 'top N clients for product X' = filter `Product Family` (e.g. 'Supply Chain Finance') and order by `Max Propensity`, showing `Propensity Rank` and `Top Drivers`.
- Penetration is per `Fiscal Year` (FY2026 to date); 'automotive parts' = `Subsector` 'Auto Parts' (all Japanese Corporate, so the peer gap nets to 0). 'Under-penetrated products' = HAVING MEASURE(`Clients With Gap`) > 0 OR MEASURE(`Gap vs All Clients pts`) > 0.05.
- Countries are ISO-2 codes in `Coverage Office` ('in Australia' = 'AU', Vietnam = 'VN', India = 'IN'); never search `Client` names for a country. `Client Group` has a value dictionary (= on the exact name); match entity names (`Client`) with LIKE. Questions about clients list `Client` (legal entity) rows unless the user asks for groups.
- Keep NULL groups: a blank `Source Signal Type` (RM-originated, referral or client-request deals) is a real row, so never add IS NOT NULL filters the user did not ask for; 'win rate by source' groups by `Source Type` and `Source Signal Type`.
- Answer with one summary sentence that states the period and filters used, then the table. Keep measure values unscaled and unrounded in the SQL; name output columns plainly.
