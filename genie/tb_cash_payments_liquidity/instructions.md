# APAC Genie - Transactional Banking: Cash, Payments & Liquidity - general instructions

Generated from genie/tb_cash_payments_liquidity/space.yaml + genie/_shared.yaml (20 lines; the space's single text instruction). Do not edit.

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
- Views: mv_tb_deposits = deposit balances, CASA / time deposits (TD), CASA ratio, depositors, top-10 concentration, TD maturities; mv_tb_payments = payment flows, STP and repairs, purpose, counterparty bank (other-bank leakage); mv_tb_liquidity_structures = cash pools and pooling candidates (group level); mv_tb_channel_adoption = digital channels and manual instructions.
- Point-in-time measures (balances, CASA, CASA ratio, depositors, TD maturing, pooled balance, group deposits, pooling candidates) need ONE `Month` or `Is Latest Month` (today = Sep 2026); CASA Movement, Deposit Movement, Transferred to TD, payments, fees and channel counts are flows that add up over months.
- 'CASA vs last month' = `CASA Balance USD`, `CASA Balance Prior Month USD` and `CASA Change vs Prior Month USD` on one `Month` (September 2026 = DATE'2026-09-01'). CASA outflow over a quarter = `CASA Movement USD` with `Fiscal Quarter` (Q2 FY2026 = 'FY2026-Q2'); compare it with `Transferred to TD USD` and `Deposit Movement USD` for 'moved to TD or left the bank'.
- Booking Country = Coverage Office for deposits and payments (use Booking Country). Payment `Corridor` is the money flow payer->payee, e.g. CN->VN = paid from China to Vietnam.
- 'Supplier payments to other banks' = `Share Paid via Other Banks %` with `Direction` = 'Outbound' AND `Payment Purpose` = 'Supplier'. Repairs = `Repairs` by `Repair Reason` and `Channel`; STP = `STP Rate %`. Ratios and shares are fractions (0.35 = 35%).
- Pooling candidates = `Is Pooling Candidate` on `Is Latest Month` (groups with deposit accounts in >= 3 APAC countries and no active structure), ranked by `Unpooled Group Deposits USD`; counts: `Clients Without Pooling`, `Multi-Country Groups`, `Pooling Penetration %`.
- `Client Group` has a value dictionary: filter it with = on the exact group name. Questions about entities, depositors or 'legal entities' list `Client`; the largest depositors of a country = `Depositor Rank in Country` <= 10 on `Is Latest Month`. Only add LIMIT when the user asks for a top N.
- Functions (STRING arguments): payment volume down > 30% YoY while deposits held steady = smbc_genie.gold.fn_tb_payment_drop_steady_deposits('FY2026-H1'); CASA by country vs the prior month-end = fn_casa_movement_by_country('2026-09'); one group's cash profile by entity = fn_tb_group_cash_profile('<exact group name>').
- Answer with one summary sentence that states the period and filters used, then the table. Keep measure values unscaled and unrounded in the SQL; name output columns plainly.
