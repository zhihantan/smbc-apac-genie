-- vw_client_360: Customer 360 drill-through view - one row per current golden client (dim_client is_current) with
-- headline attributes and the latest KPI of every domain as of 30-Sep-2026. The one gold object that reads the
-- other packages' facts (RM / profitability, credit / EWS / KYC / onboarding, TB / trade / cash flow) plus the
-- signals and data-foundation facts. Point-in-time KPIs are taken at the as-of month-end / day; flows over the
-- stated window (FYTD = Apr-Sep 2026, last 90 days = 3-Jul .. 30-Sep-2026).
WITH c AS (
  SELECT * FROM ${catalog}.gold.dim_client WHERE is_current
), fy AS (
  SELECT fiscal_year AS fy FROM ${catalog}.gold.dim_date WHERE date = DATE'${as_of_date}'
), rev AS (            -- RM / profitability: FYTD revenue vs prior FYTD, net contribution, latest-quarter RoRWA
  SELECT r.golden_client_id,
         sum(r.total_revenue_usd) AS revenue_fytd_usd,
         sum(r.total_revenue_py_usd) AS revenue_prior_fytd_usd,
         sum(r.net_contribution_usd) AS net_contribution_fytd_usd,
         max_by(r.rorwa_fq, r.month) FILTER (WHERE r.is_primary_row) AS rorwa_latest_fq,
         max_by(r.below_rorwa_hurdle, r.month) FILTER (WHERE r.is_primary_row) AS below_rorwa_hurdle
  FROM ${catalog}.gold.fact_client_revenue_monthly r CROSS JOIN fy
  WHERE r.fiscal_year = fy.fy AND r.month <= DATE'${as_of_date}'
  GROUP BY r.golden_client_id
), hold AS (           -- product holding at the as-of month
  SELECT golden_client_id, max(products_held_count) AS products_held_count,
         max(product_families_held_count) AS product_families_held_count,
         bool_or(is_deposit_only) AS is_deposit_only, bool_or(is_single_product_lending) AS is_single_product_lending
  FROM ${catalog}.gold.fact_product_holding_monthly
  WHERE month = trunc(DATE'${as_of_date}', 'MM') AND is_client_month_primary_row
  GROUP BY golden_client_id
), opp AS (            -- pipeline: open opportunities at the as-of date and FY2026 wins (gold.fact_pipeline_opportunity)
  SELECT p.golden_client_id,
         count_if(p.is_open) AS open_n,
         sum(CASE WHEN p.is_open THEN p.amount_usd ELSE 0 END) AS open_usd,
         sum(CASE WHEN p.is_open THEN p.weighted_amount_usd ELSE 0 END) AS weighted_usd,
         sum(CASE WHEN p.is_won AND d.fiscal_year = fy.fy THEN p.amount_usd ELSE 0 END) AS won_fytd_usd
  FROM ${catalog}.gold.fact_pipeline_opportunity p CROSS JOIN fy
  LEFT JOIN ${catalog}.gold.dim_date d ON d.date = p.actual_close_date
  WHERE p.golden_client_id IS NOT NULL AND p.created_date <= DATE'${as_of_date}'
  GROUP BY p.golden_client_id
), nbp AS (            -- next-best product ranked 1 in the latest scoring month (gold.fact_next_best_product_score)
  SELECT golden_client_id, max_by(named_struct('p', product, 'pr', propensity), struct(propensity, product)) AS b
  FROM ${catalog}.gold.fact_next_best_product_score
  WHERE is_latest_score_month AND is_top_ranked AND golden_client_id IS NOT NULL
  GROUP BY golden_client_id
), crd AS (            -- credit exposure at the as-of month-end
  SELECT golden_client_id, count(*) AS facilities, sum(limit_usd) AS limit_usd, sum(drawn_usd) AS drawn_usd,
         sum(undrawn_usd) AS undrawn_usd, sum(ead_usd) AS ead_usd, sum(rwa_usd) AS rwa_usd, sum(ecl_usd) AS ecl_usd,
         max(days_past_due) AS max_dpd
  FROM ${catalog}.gold.fact_credit_exposure_monthly
  WHERE month_end_date = DATE'${as_of_date}'
  GROUP BY golden_client_id
), ews AS (            -- EWS score on the as-of day
  SELECT golden_client_id, max(composite_score) AS ews_score, max(computed_band) AS computed_band,
         max(score_change_30d) AS score_change_30d, bool_or(is_band_overridden) AS is_overridden,
         max(top_triggers_text) AS top_triggers
  FROM ${catalog}.gold.fact_ews_score_daily WHERE score_date = DATE'${as_of_date}'
  GROUP BY golden_client_id
), cov AS (            -- latest covenant tests
  SELECT golden_client_id, min(headroom_pct) AS min_headroom, bool_or(is_breached) AS breached,
         count_if(is_tight_headroom) AS tight
  FROM ${catalog}.gold.fact_covenant_test WHERE is_latest_test
  GROUP BY golden_client_id
), rvw AS (            -- annual credit review workflow
  SELECT golden_client_id,
         min(due_date) FILTER (WHERE is_open AND review_type = 'Annual') AS next_review_due,
         bool_or(is_overdue AND workflow_type = 'Credit Review') AS review_overdue,
         count_if(is_open AND is_not_yet_spread) AS spreads_pending
  FROM ${catalog}.gold.fact_credit_review_workflow
  GROUP BY golden_client_id
), kyc AS (            -- KYC reviews open / overdue at the as-of date
  SELECT golden_client_id, count_if(NOT is_completed) AS open_reviews, count_if(is_overdue) AS overdue_reviews
  FROM ${catalog}.gold.fact_kyc_review
  GROUP BY golden_client_id
), onb AS (            -- latest onboarding case
  SELECT golden_client_id,
         max_by(named_struct('id', case_id, 'st', status, 'live', go_live_date, 'mt', match_timing), struct(request_date, case_id)) AS o
  FROM ${catalog}.gold.fact_onboarding_case WHERE golden_client_id IS NOT NULL
  GROUP BY golden_client_id
), dep AS (            -- deposits at the as-of month-end
  SELECT golden_client_id, sum(balance_usd) AS deposits_usd, sum(casa_balance_usd) AS casa_usd,
         sum(td_balance_usd) AS td_usd, sum(td_principal_maturing_90d_usd) AS td_maturing_90d_usd
  FROM ${catalog}.gold.fact_deposit_balance_monthly WHERE balance_date = DATE'${as_of_date}'
  GROUP BY golden_client_id
), pay AS (            -- payments in the last 90 days
  SELECT golden_client_id, count(*) AS n, sum(amount_usd) AS usd,
         sum(CASE WHEN is_other_bank_counterparty THEN amount_usd ELSE 0 END) AS other_bank_usd
  FROM ${catalog}.gold.fact_payment_transaction
  WHERE payment_date > date_sub(DATE'${as_of_date}', 90) AND payment_date <= DATE'${as_of_date}'
  GROUP BY golden_client_id
), trd AS (            -- trade finance outstanding at the as-of month-end
  SELECT golden_client_id, sum(outstanding_usd) AS outstanding_usd, count(*) AS instruments
  FROM ${catalog}.gold.fact_trade_outstanding_monthly WHERE month_end_date = DATE'${as_of_date}'
  GROUP BY golden_client_id
), cff AS (            -- latest 30-day cash-flow forecast (forward forecast made on the as-of date)
  SELECT golden_client_id,
         max_by(named_struct('net', forecast_net_usd, 'p10', forecast_net_p10_usd, 'p90', forecast_net_p90_usd,
                             'neg', is_forecast_net_negative, 'rcf', undrawn_rcf_usd, 'd', forecast_date), struct(forecast_date, model_version)) AS f
  FROM ${catalog}.gold.fact_cashflow_forecast WHERE is_latest_forecast AND horizon_days = 30
  GROUP BY golden_client_id
), lne AS (            -- latest predicted liquidity need (shortfall / surplus) and how it was handled
  SELECT golden_client_id,
         max_by(named_struct('t', event_type, 'd', event_date, 'amt', predicted_amount_usd, 'st', event_status, 'act', action_taken),
                struct(event_date, event_id)) AS e
  FROM ${catalog}.gold.fact_liquidity_need_event WHERE event_date <= DATE'${as_of_date}'
  GROUP BY golden_client_id
), sig AS (            -- unified signal feed, last 90 days
  SELECT golden_client_id, count(*) AS n, count_if(is_risk_signal) AS risk, count_if(is_opportunity_signal) AS opp,
         count_if(is_risk_signal AND NOT is_acknowledged) AS risk_unack, max(signal_date) AS last_date
  FROM ${catalog}.gold.fact_signal_event WHERE is_last_90d
  GROUP BY golden_client_id
), nws AS (            -- news on the client, last 90 days
  SELECT golden_client_id, count(*) AS n, avg(sentiment_score) AS s, count_if(is_negative) AS neg
  FROM ${catalog}.gold.fact_news_item WHERE is_last_90d
  GROUP BY golden_client_id
), nte AS (            -- RM coverage and internal sentiment (every CRM activity carries a note)
  SELECT golden_client_id, max(note_date) AS last_contact, count_if(is_last_90d) AS n90,
         avg(CASE WHEN is_last_90d THEN sentiment_score END) AS s90, count_if(is_action_open) AS open_actions
  FROM ${catalog}.gold.fact_rm_note_sentiment
  GROUP BY golden_client_id
), grp_sent AS (       -- group-level sentiment status as of the as-of date
  SELECT client_group_id, max(group_news_sentiment_last_90d) AS news_90d, max(group_note_sentiment_last_90d) AS notes_90d,
         bool_or(is_blind_spot_group) AS blind_spot
  FROM ${catalog}.gold.fact_rm_note_sentiment
  GROUP BY client_group_id
), mkt AS (            -- listed parent on its latest trading day
  SELECT client_group_id, bool_or(is_52w_low) AS at_52w_low, max(price_change_30d_pct) AS chg30
  FROM ${catalog}.gold.fact_market_signal_daily WHERE is_latest_trade_date
  GROUP BY client_group_id
), cvg AS (            -- golden-record coverage and completeness at the latest month
  SELECT golden_client_id,
         max(n_sources_present) AS n_sources, bool_or(in_all_core_sources) AS all_core,
         bool_or(source_system = 'kyc_customer' AND is_missing) AS missing_kyc,
         bool_or(source_system = 'credit_obligor' AND is_missing) AS missing_credit,
         max(attribute_completeness_pct) AS completeness, max(missing_attributes_text) AS missing_attrs
  FROM ${catalog}.gold.fact_golden_record_coverage_monthly WHERE is_latest_month
  GROUP BY golden_client_id
)
SELECT
  -- identity / headline (current dim_client version)
  c.golden_client_id,
  c.golden_client_sk,
  c.display_name,
  c.legal_name,
  c.short_name,
  c.aliases_text,
  c.lei_like_id,
  c.country_of_incorporation,
  c.client_group_id,
  c.group_name,
  coalesce(c.golden_client_id = g.lead_golden_client_id, false)                AS is_group_lead,
  g.lead_client_name                                                           AS group_lead_client_name,
  g.jp_parent_legal_name,
  g.jp_parent_rating_equivalent,
  c.segment,
  c.is_japanese_corporate,
  c.relationship_tier,
  c.industry_sector,
  c.industry_subsector,
  c.is_carbon_intensive,
  c.is_listed,
  c.coverage_office,
  c.coverage_office_name,
  c.primary_rm_id,
  c.primary_rm_code,
  c.primary_rm_name,
  c.primary_rm_office,
  c.credit_analyst_name,
  c.internal_rating_grade,
  c.rating_equivalent,
  c.ifrs9_stage,
  c.external_rating,
  c.ews_band,
  c.watchlist_flag,
  c.kyc_risk_rating,
  c.kyc_next_review_date,
  c.has_account_plan,
  c.onboarding_date,
  c.relationship_start_date,
  c.esg_rating,
  c.source_systems_text,
  c.n_source_records,
  c.golden_record_confidence,
  -- RM / profitability
  round(rev.revenue_fytd_usd, 2)                                               AS revenue_fytd_usd,
  round(rev.revenue_prior_fytd_usd, 2)                                         AS revenue_prior_fytd_usd,
  round(rev.revenue_fytd_usd / nullif(rev.revenue_prior_fytd_usd, 0) - 1, 4)  AS revenue_yoy_pct,
  round(rev.net_contribution_fytd_usd, 2)                                      AS net_contribution_fytd_usd,
  round(rev.rorwa_latest_fq, 6)                                                AS rorwa_latest_quarter,
  rev.below_rorwa_hurdle,
  hold.products_held_count,
  hold.product_families_held_count,
  coalesce(hold.is_deposit_only, false)                                        AS is_deposit_only,
  coalesce(hold.is_single_product_lending, false)                              AS is_single_product_lending,
  CAST(coalesce(opp.open_n, 0) AS INT)                                         AS open_opportunities,
  round(coalesce(opp.open_usd, 0), 2)                                          AS open_pipeline_usd,
  round(coalesce(opp.weighted_usd, 0), 2)                                      AS weighted_pipeline_usd,
  round(coalesce(opp.won_fytd_usd, 0), 2)                                      AS won_fytd_usd,
  nbp.b.p                                                                      AS next_best_product,
  round(nbp.b.pr, 4)                                                           AS next_best_product_propensity,
  nte.last_contact                                                             AS last_rm_contact_date,
  datediff(DATE'${as_of_date}', nte.last_contact)                              AS days_since_last_rm_contact,
  CAST(coalesce(nte.n90, 0) AS INT)                                            AS rm_contacts_last_90d,
  CAST(coalesce(nte.open_actions, 0) AS INT)                                   AS open_rm_actions,
  -- credit / early warning
  CAST(coalesce(crd.facilities, 0) AS INT)                                     AS facilities,
  round(coalesce(crd.limit_usd, 0), 2)                                         AS credit_limit_usd,
  round(coalesce(crd.drawn_usd, 0), 2)                                         AS drawn_usd,
  round(coalesce(crd.undrawn_usd, 0), 2)                                       AS undrawn_usd,
  round(crd.drawn_usd / nullif(crd.limit_usd, 0), 4)                           AS utilisation_pct,
  round(coalesce(crd.ead_usd, 0), 2)                                           AS ead_usd,
  round(coalesce(crd.rwa_usd, 0), 2)                                           AS rwa_usd,
  round(coalesce(crd.ecl_usd, 0), 2)                                           AS ecl_usd,
  crd.max_dpd                                                                  AS max_days_past_due,
  ews.ews_score,
  ews.computed_band                                                            AS ews_computed_band,
  ews.score_change_30d                                                         AS ews_score_change_30d,
  coalesce(ews.is_overridden, false)                                           AS is_ews_overridden,
  ews.top_triggers                                                             AS ews_top_triggers,
  cov.min_headroom                                                             AS covenant_min_headroom_pct,
  coalesce(cov.breached, false)                                                AS has_covenant_breach,
  CAST(coalesce(cov.tight, 0) AS INT)                                          AS tight_covenants,
  rvw.next_review_due                                                          AS next_credit_review_due_date,
  coalesce(rvw.review_overdue, false)                                          AS is_credit_review_overdue,
  -- KYC / onboarding
  CAST(coalesce(kyc.open_reviews, 0) AS INT)                                   AS open_kyc_reviews,
  CAST(coalesce(kyc.overdue_reviews, 0) AS INT)                                AS overdue_kyc_reviews,
  onb.o.id                                                                     AS latest_onboarding_case_id,
  onb.o.st                                                                     AS latest_onboarding_status,
  onb.o.live                                                                   AS latest_onboarding_go_live_date,
  onb.o.mt                                                                     AS latest_onboarding_match_timing,
  -- transaction banking / trade
  round(coalesce(dep.deposits_usd, 0), 2)                                      AS deposits_usd,
  round(coalesce(dep.casa_usd, 0), 2)                                          AS casa_usd,
  round(coalesce(dep.td_usd, 0), 2)                                            AS time_deposits_usd,
  round(dep.casa_usd / nullif(dep.deposits_usd, 0), 4)                         AS casa_ratio,
  round(coalesce(dep.td_maturing_90d_usd, 0), 2)                               AS td_maturing_90d_usd,
  CAST(coalesce(pay.n, 0) AS INT)                                              AS payments_last_90d,
  round(coalesce(pay.usd, 0), 2)                                               AS payments_last_90d_usd,
  round(pay.other_bank_usd / nullif(pay.usd, 0), 4)                            AS payments_other_bank_share_90d,
  round(coalesce(trd.outstanding_usd, 0), 2)                                   AS trade_outstanding_usd,
  CAST(coalesce(trd.instruments, 0) AS INT)                                    AS trade_instruments_outstanding,
  -- cash flow
  round(cff.f.net, 2)                                                          AS forecast_net_30d_usd,
  round(cff.f.p10, 2)                                                          AS forecast_net_30d_p10_usd,
  round(cff.f.p90, 2)                                                          AS forecast_net_30d_p90_usd,
  coalesce(cff.f.neg, false)                                                   AS is_forecast_negative_30d,
  lne.e.t                                                                      AS latest_liquidity_event_type,
  lne.e.d                                                                      AS latest_liquidity_event_date,
  round(lne.e.amt, 2)                                                          AS latest_liquidity_event_usd,
  lne.e.st                                                                     AS latest_liquidity_event_status,
  lne.e.act                                                                    AS latest_liquidity_event_action,
  -- signals & sentiment
  CAST(coalesce(sig.n, 0) AS INT)                                              AS signals_last_90d,
  CAST(coalesce(sig.risk, 0) AS INT)                                           AS risk_signals_last_90d,
  CAST(coalesce(sig.opp, 0) AS INT)                                            AS opportunity_signals_last_90d,
  CAST(coalesce(sig.risk_unack, 0) AS INT)                                     AS unacknowledged_risk_signals_90d,
  sig.last_date                                                                AS last_signal_date,
  CAST(coalesce(nws.n, 0) AS INT)                                              AS news_items_last_90d,
  round(nws.s, 4)                                                              AS news_sentiment_last_90d,
  CAST(coalesce(nws.neg, 0) AS INT)                                            AS negative_news_last_90d,
  round(nte.s90, 4)                                                            AS rm_note_sentiment_last_90d,
  round(gs.news_90d, 4)                                                        AS group_news_sentiment_last_90d,
  coalesce(gs.blind_spot, false)                                               AS is_blind_spot_group,
  coalesce(mk.at_52w_low, false)                                               AS listed_parent_at_52w_low,
  round(mk.chg30, 4)                                                           AS listed_parent_price_change_30d_pct,
  -- data foundation
  CAST(cvg.n_sources AS INT)                                                   AS sources_present,
  coalesce(cvg.all_core, false)                                                AS in_all_core_sources,
  coalesce(cvg.missing_kyc, false)                                             AS is_missing_kyc_record,
  coalesce(cvg.missing_credit, false)                                          AS is_missing_credit_record,
  cvg.completeness                                                             AS attribute_completeness_pct,
  cvg.missing_attrs                                                            AS missing_attributes_text
FROM c
LEFT JOIN ${catalog}.gold.dim_client_group g ON g.client_group_id = c.client_group_id
LEFT JOIN rev ON rev.golden_client_id = c.golden_client_id
LEFT JOIN hold ON hold.golden_client_id = c.golden_client_id
LEFT JOIN opp ON opp.golden_client_id = c.golden_client_id
LEFT JOIN nbp ON nbp.golden_client_id = c.golden_client_id
LEFT JOIN crd ON crd.golden_client_id = c.golden_client_id
LEFT JOIN ews ON ews.golden_client_id = c.golden_client_id
LEFT JOIN cov ON cov.golden_client_id = c.golden_client_id
LEFT JOIN rvw ON rvw.golden_client_id = c.golden_client_id
LEFT JOIN kyc ON kyc.golden_client_id = c.golden_client_id
LEFT JOIN onb ON onb.golden_client_id = c.golden_client_id
LEFT JOIN dep ON dep.golden_client_id = c.golden_client_id
LEFT JOIN pay ON pay.golden_client_id = c.golden_client_id
LEFT JOIN trd ON trd.golden_client_id = c.golden_client_id
LEFT JOIN cff ON cff.golden_client_id = c.golden_client_id
LEFT JOIN lne ON lne.golden_client_id = c.golden_client_id
LEFT JOIN sig ON sig.golden_client_id = c.golden_client_id
LEFT JOIN nws ON nws.golden_client_id = c.golden_client_id
LEFT JOIN nte ON nte.golden_client_id = c.golden_client_id
LEFT JOIN grp_sent gs ON gs.client_group_id = c.client_group_id
LEFT JOIN mkt mk ON mk.client_group_id = c.client_group_id
LEFT JOIN cvg ON cvg.golden_client_id = c.golden_client_id
