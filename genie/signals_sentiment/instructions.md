# APAC Genie - Signals & Sentiment - general instructions

Generated from genie/signals_sentiment/space.yaml + genie/_shared.yaml (20 lines; the space's single text instruction). Do not edit.

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
- Views: mv_signal_feed = every signal (CRM opportunity, EWS, news, market, agency and JP-parent ratings, RM notes) by Source Feed / Signal Type / Polarity / Acknowledged / Has Pipeline; mv_news_sentiment = news items and sentiment by Topic / Subtopic / Sector Theme; mv_internal_sentiment = RM call notes, divergence and blind spots; mv_market_signals = listed parents (share price, CDS, 52-week lows, ratings, APAC exposure).
- "Everything about <group> in the last N days" = mv_signal_feed by Source Feed and Signal Type (or fn_group_signal_digest); its news, RM-note and market picture = the CTE example or fn_group_sentiment_snapshot. Match the group with `Client Group` LIKE '%<full group name>%'.
- Sentiment runs -1..+1 (negative <= -0.2, positive >= 0.2) and is always averaged, never summed. Sentiment Last 30D / 90D, Sentiment Change 30D / 90D, Groups With Negative Trend, Blind Spot Groups and Group News / Note Sentiment Last 90D are fixed to 30-Sep-2026: use them without a Date filter (a Date filter that cuts their window returns null).
- Market measures (share price and CDS changes, Groups at 52-Week Low, APAC Exposure USD, rating and outlook in force) are point-in-time: filter `Date` = DATE'2026-09-30' for today. "At a 52-week low with APAC exposure above USD 100m" = `At 52-Week Low` AND `APAC Exposure Above 100m`.
- Blind spots = groups with MEASURE(`Blind Spot Groups`) > 0 in mv_internal_sentiment (news turned negative while RM notes stayed positive), see fn_sentiment_blind_spots. "Coal and shipping sectors" = `Sector Theme` IN ('Coal', 'Shipping').
- "Rating actions from JP-shared parent data" = mv_signal_feed `Source Region` = 'JP' (PARENT_RATING_UPGRADE / _DOWNGRADE). Acknowledgement of non-CRM signals is a proxy: an RM activity with the group within 30 days.
- Unacknowledged risk signals by RM: filter `Polarity` = 'Risk' AND NOT `Acknowledged`, group by COALESCE(`Primary RM`, '(no RM coverage)') AS rm and `Primary RM Office` (RM names repeat across offices).
- Expansion signals = `Signal Type` IN ('NEWS_EXPANSION', 'CAPEX_NEWS') with `Polarity` = 'Opportunity'; "no pipeline" = NOT `Has Pipeline`; Australia = `Coverage Office` 'AU', India = 'IN'; list them per legal entity (`Client`).
- Answer with one summary sentence that states the period and filters used, then the table. Keep measure values unscaled and unrounded in the SQL; name output columns plainly.
