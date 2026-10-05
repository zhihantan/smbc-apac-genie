-- UC SQL table functions for APAC Genie - Opportunity Identification (G1 / p07g1). Deployed by the Genie runner
-- (CREATE OR REPLACE). Signals are entity-level events dated by detection; "open opportunities" = open signals.
-- Arguments are STRING only (genie/README.md); the `-- test:` probes must return rows.
-- test: SELECT * FROM smbc_genie.gold.fn_opportunity_top_open_signals('25')
-- test: SELECT * FROM smbc_genie.gold.fn_opportunity_fx_leakage('USD/JPY,USD/VND')
-- test: SELECT * FROM smbc_genie.gold.fn_opportunity_penetration_gaps('Japanese Corporate', 'Auto Parts', 'FY2026')

CREATE OR REPLACE FUNCTION smbc_genie.gold.fn_opportunity_top_open_signals(
  top_n STRING DEFAULT '25' COMMENT 'How many signals to return, ranked by estimated revenue, as text (default 25).'
)
RETURNS TABLE (
  `Client` STRING COMMENT 'Legal entity (golden client) the signal was raised on.',
  `Signal Type` STRING COMMENT 'Signal code, e.g. CAPEX_NEWS, DEPOSIT_SURPLUS, FACILITY_MATURING_12M, LOAN_SERVICE_TO_OTHER_BANK.',
  `Primary RM` STRING COMMENT 'Primary RM of the client (fictional name).',
  detected_date DATE COMMENT 'Date the signal was detected.',
  estimated_revenue_usd DOUBLE COMMENT 'Estimated annual revenue in USD if the opportunity is won.',
  days_open DOUBLE COMMENT 'Days the signal has been open to 30-Sep-2026.'
)
COMMENT 'Top open opportunity signals across SMBC APAC ranked by estimated annual revenue (default top 25): client, signal type, primary RM, detection date, estimated revenue and days open. "Open opportunities" means open signals (not yet converted to the pipeline, not dismissed). Use for "top 25 open opportunities", "biggest open signals", "largest untapped opportunities across APAC". USD.'
RETURN
  SELECT `Client`, `Signal Type`, `Primary RM`, detected_date, estimated_revenue_usd, days_open
  FROM (
    SELECT s.*, row_number() OVER (ORDER BY s.estimated_revenue_usd DESC) AS revenue_rank
    FROM (
      SELECT `Client`, `Signal Type`, `Primary RM`, `Date` AS detected_date,
             MEASURE(`Open Estimated Revenue USD`) AS estimated_revenue_usd,
             MEASURE(`Days Open Average`)          AS days_open
      FROM smbc_genie.metrics.mv_opportunity_signals
      WHERE `Status` = 'Open'
      GROUP BY ALL
    ) s
  ) r
  WHERE r.revenue_rank <= CAST(top_n AS INT);

CREATE OR REPLACE FUNCTION smbc_genie.gold.fn_opportunity_fx_leakage(
  currency_pairs STRING DEFAULT 'USD/JPY,USD/VND' COMMENT 'Comma-separated currency pairs, e.g. USD/JPY,USD/VND (default). Use ALL for every pair.'
)
RETURNS TABLE (
  `Client` STRING COMMENT 'Legal entity paying FX flow through other banks.',
  `Currency Pair` STRING COMMENT 'Currency pair of the flow routed via other banks, e.g. USD/JPY.',
  fx_flow_via_other_banks_12m_usd DOUBLE COMMENT 'Latest observed 12-month FX flow the client pays via other banks, USD (not summed over signals).',
  signals BIGINT COMMENT 'FX_FLOW_VIA_OTHER_BANK signals raised on the client for the pair.'
)
COMMENT 'FX flow SMBC does not capture: clients paying given currency pairs (default USD/JPY and USD/VND) through other banks, with the latest observed 12-month flow and the number of FX_FLOW_VIA_OTHER_BANK signals. Use for "which clients pay USD/JPY or USD/VND through other banks", "FX leakage", "FX flow we do not capture", "FX wallet leakage by currency pair". USD.'
RETURN
  SELECT `Client`, `Currency Pair`,
         MEASURE(`Latest Observed Amount USD`) AS fx_flow_via_other_banks_12m_usd,
         MEASURE(`Signals`)                    AS signals
  FROM smbc_genie.metrics.mv_opportunity_signals
  WHERE `Signal Type` = 'FX_FLOW_VIA_OTHER_BANK'
    AND (upper(trim(fn_opportunity_fx_leakage.currency_pairs)) = 'ALL'
         OR array_contains(transform(split(fn_opportunity_fx_leakage.currency_pairs, ','), p -> upper(trim(p))),
                           upper(`Currency Pair`)))
  GROUP BY ALL;

CREATE OR REPLACE FUNCTION smbc_genie.gold.fn_opportunity_penetration_gaps(
  segment STRING COMMENT 'Client segment, e.g. Japanese Corporate.',
  subsector STRING COMMENT 'Industry subsector (peer group), e.g. Auto Parts (automotive parts), Semiconductors, Shipping.',
  fiscal_year STRING DEFAULT 'FY2026' COMMENT 'Fiscal-year label, e.g. FY2026 (to date). Default FY2026.'
)
RETURNS TABLE (
  `Product` STRING COMMENT 'Core product, e.g. Term Loan, Import LC, Time Deposit.',
  clients BIGINT COMMENT 'Clients of the segment and subsector (the penetration base).',
  penetration DOUBLE COMMENT 'Share of these clients holding the product (fraction).',
  peer_penetration DOUBLE COMMENT 'Share of their peer group (same subsector) holding it (fraction).',
  clients_with_gap_vs_peers BIGINT COMMENT 'Clients without the product while at least half of their peers hold it.',
  all_apac_penetration DOUBLE COMMENT 'Share of all SMBC APAC clients holding the product (fraction).',
  gap_vs_all_apac_pts DOUBLE COMMENT 'All-APAC penetration minus own penetration (fraction; positive = under-penetrated).'
)
COMMENT 'Product penetration gaps for one segment and industry subsector (e.g. Japanese Corporate automotive-parts subsidiaries) in a fiscal year: per product the clients, own penetration, peer penetration, clients with a gap vs peers, all-APAC penetration and the gap vs all APAC clients; only under-penetrated products (clients with a peer gap, or more than 5 pts below the APAC book). Use for "product penetration gap vs peers", "which products are under-penetrated", "white space by product". When the selection is a whole peer group the peer gap is 0 by construction, so read Clients With Gap and the all-APAC gap.'
RETURN
  SELECT `Product`,
         MEASURE(`Clients`)                  AS clients,
         MEASURE(`Penetration %`)            AS penetration,
         MEASURE(`Peer Penetration %`)       AS peer_penetration,
         MEASURE(`Clients With Gap`)         AS clients_with_gap_vs_peers,
         MEASURE(`All-Client Penetration %`) AS all_apac_penetration,
         MEASURE(`Gap vs All Clients pts`)   AS gap_vs_all_apac_pts
  FROM smbc_genie.metrics.mv_product_penetration
  WHERE `Fiscal Year` = fn_opportunity_penetration_gaps.fiscal_year
    AND (lower(`Segment`) = lower(trim(fn_opportunity_penetration_gaps.segment))
         OR lower(trim(fn_opportunity_penetration_gaps.segment)) LIKE concat('%', lower(`Segment`), '%')
         OR lower(`Segment`) LIKE concat(lower(trim(fn_opportunity_penetration_gaps.segment)), '%'))
    AND `Subsector` IN (
      SELECT i.industry_subsector FROM smbc_genie.gold.dim_industry i
      WHERE lower(i.industry_subsector) = regexp_replace(regexp_replace(lower(trim(fn_opportunity_penetration_gaps.subsector)), 'automotive[- ]?', 'auto '), '-', ' ')
         OR (lower(i.industry_subsector) LIKE concat('%', regexp_replace(regexp_replace(lower(trim(fn_opportunity_penetration_gaps.subsector)), 'automotive[- ]?', 'auto '), '-', ' '), '%')
             AND NOT EXISTS (SELECT 1 FROM smbc_genie.gold.dim_industry x
                             WHERE lower(x.industry_subsector) = regexp_replace(regexp_replace(lower(trim(fn_opportunity_penetration_gaps.subsector)), 'automotive[- ]?', 'auto '), '-', ' '))))
  GROUP BY ALL
  HAVING MEASURE(`Clients With Gap`) > 0 OR MEASURE(`Gap vs All Clients pts`) > 0.05;
