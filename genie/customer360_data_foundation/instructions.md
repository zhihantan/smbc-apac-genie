# APAC Genie - Customer 360 Data Foundation - general instructions

Generated from genie/customer360_data_foundation/space.yaml + genie/_shared.yaml (20 lines; the space's single text instruction). Do not edit.

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
- Views: mv_entity_resolution = quarterly ER runs (records in, matches, golden records, precision / recall, steward queue); mv_golden_record_coverage = golden clients by source system, missing KYC / credit, attribute completeness; mv_data_quality = DQ scores by layer / table / DQ dimension plus the latest rule results; mv_delta_sharing_freshness = JP / EMEA / AMER share lag and staleness; mv_er_exposure_impact = group exposure before vs after each run's re-resolution.
- ER runs ER-20250630 .. ER-20260930 are quarter-end snapshots (rule set v1 to Dec-2025, v2 from Mar-2026): latest run = `Is Latest Month`; compare runs by grouping by `Run`, never add runs; "the March 2026 resolution" = `Month` = DATE'2026-03-01' (ER-20260331).
- ER per source system: group by `Source System` and exclude the technical ALL value (or use fn_er_run_summary). Whole-run ER figures (records in, match rate, golden records, precision, recall of a run): put no Source System condition in the query at all. Precision % and Recall % are never summed across sources.
- Steward queue aging = items open at 30-Sep-2026: MEASURE(`Steward Queue Open`) and MEASURE(`Average Days Open`) by `Aging Bucket` with HAVING MEASURE(`Steward Queue Open`) > 0.
- Coverage and DQ measures are month-end snapshots: today = `Is Latest Month` (`Month` = DATE'2026-09-01'); "this month vs last" = `Month` = DATE'2026-09-01' with DQ Score % and DQ Score Prior Month %. Attribute completeness exists for Sep-2026 only.
- Delta Share freshness: "from Japan" = `Provider Region` = 'JP'; last 30 days = `Date` >= DATE'2026-09-01'; stale = lag > 24h (Stale Table-Days = stale days, Tables Stale = tables); lag and stale counts add up over the selected days.
- Exposures changed by more than 20% after a resolution (all groups) = `Material Resolution Change` in mv_er_exposure_impact or fn_er_exposure_changes; one named group = the example query (before / after, attention threshold, crossed). Unattributed exposure has no Client Group: show COALESCE(`Client Group`, '(unattributed)') AS client_group with `Attribution Status`.
- Golden clients missing KYC / credit = Clients Missing KYC Data / Clients Missing Credit Data; "by tier" = Relationship Tier, keeping the null tier (clients without a CRM tier). Client = legal entity, matched with LIKE, e.g. `Client` LIKE 'Meridian Agri Holdings (Singapore)%'.
- Answer with one summary sentence that states the period and filters used, then the table. Keep measure values unscaled and unrounded in the SQL; name output columns plainly.
