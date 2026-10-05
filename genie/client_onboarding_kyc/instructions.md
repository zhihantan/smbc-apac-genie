# APAC Genie - Client Onboarding & KYC - general instructions

Generated from genie/client_onboarding_kyc/space.yaml + genie/_shared.yaml (19 lines; the space's single text instruction). Do not edit.

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
- Onboarding requests: use Booking Country and Request Segment (applicants that never went live have no client record); "FI" = Request Segment 'Financial Institution'. Date = the request date (request cohorts); statuses (open, live, over SLA, outstanding) are as of 30-Sep-2026.
- Cases, documents and KYC reviews are per legal entity: list every matching row (no top-20 cut) with Client and Booking Country. When counting by a label (owner, blocker, document type, risk rating, country, theme), keep only groups whose count is > 0 (HAVING MEASURE(...) > 0).
- mv_onboarding_funnel: "open cases over SLA right now" = Case Status 'Open' with Cases Over SLA; "stuck in KYC Docs > 15 days" = Stuck in KYC Docs 15D; Documents Outstanding by Document Type; Product Requests by Product Requested; group matches by Match Timing (At Intake / After Account Opening / New Group); Average Satisfaction and Negative Responses by Feedback Theme.
- mv_onboarding_cycle_time: Median / P90 / Average Days to Live are per live case - add Case Status = 'Live' when grouping by month or country; Average Days in Stage by Stage Number and Stage (1-6 = request to account open, 7 = first transaction); "after February 2026" = Is After KYC Migration; migration periods = KYC Migration Period.
- mv_kyc_health: Overdue and High-Risk Overdue are month-end point-in-time - filter one Month or group by Month (Is Latest Month = today); "high-risk" = Client Risk Rating 'High' (KYC master); screening = Record Type 'Screening' by List (Screening Alerts, True Matches, Screening True Match Rate %, Average Resolution Hours).
- "This fiscal year vs last" for requests = Fiscal Half 'FY2026-H1' vs 'FY2025-H1' (same months), plus full Fiscal Year 'FY2025' when asked; "onboarded in FY2026" = Go-Live Fiscal Year 'FY2026'.
- Case-level detail of open cases (case id, applicant, days in stage vs SLA, owner) = fn_onboarding_open_cases(booking_country or 'ALL'); the list of KYC reviews overdue today = fn_kyc_overdue_reviews(booking_country or 'ALL').
- Answer with one summary sentence that states the period and filters used, then the table. Keep measure values unscaled and unrounded in the SQL; name output columns plainly.
