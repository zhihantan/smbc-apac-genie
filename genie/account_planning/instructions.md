# APAC Genie - Account Planning - general instructions

Generated from genie/account_planning/space.yaml + genie/_shared.yaml (20 lines; the space's single text instruction). Do not edit.

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
- Plan targets, attainment, run-rate, revenue YoY, wallet / share of wallet and initiative counts: mv_account_plan_progress (group x fiscal year x product family). Filter ONE `Fiscal Year`; FY2026 actuals are H1 (Apr-Sep 2026), so attainment near 50% is on track and `Attainment %` < 0.40 is behind plan.
- Products held, deposit-only entities, drawn lending, deposits: mv_relationship_footprint. Exposure, deposits and revenue by region (APAC, JP, EMEA, AMER): mv_global_group_relationship. RM activities and contact gaps: mv_rm_engagement. These are point-in-time: filter `Is Latest Month` for today (Sep-2026).
- `Client Group` has a value dictionary: filter it with = on the exact group name ('Tanaka Chemical' and 'Tanaka Chemical Group' are different groups). Use LIKE only for entity (`Client`) names.
- Questions about entities or subsidiaries (which entities hold only deposits, which clients were not contacted) list `Client` (legal entity) with its `Client Group`; a group question lists `Client Group`.
- RMs: 'by RM' = `Primary RM` (the group lead RM; `Plan Owner RM` in plan lists); 'RMs in Singapore / Hong Kong' = `Primary RM Office` = 'SG' / 'HK' (where the RM sits, not the client's `Coverage Office`).
- 'No RM contact in 90 days' = `Strategic Clients Not Touched 90D` (Strategic) or `Clients Not Touched 90D` on `Is Latest Month`: CRM-covered clients only; `Strategic Entities Without CRM` counts Strategic-tier entities with no CRM account at all.
- Initiative detail (name, status, due quarter, owner RM): gold.dim_account_plan_initiative with is_open, is_overdue and due_fiscal_quarter_label (e.g. 'FY2026-Q3'); per-group / per-RM counts: `Initiatives Open`, `Initiatives Overdue`.
- `Revenue YoY %` is like for like: FY2026 = H1 FY2026 vs H1 FY2025. Share of wallet: `Share of Wallet %` with `Estimated Wallet USD` > 0; 'where is it lowest' = ascending order.
- Answer with one summary sentence that states the period and filters used, then the table. Keep measure values unscaled and unrounded in the SQL; name output columns plainly.
