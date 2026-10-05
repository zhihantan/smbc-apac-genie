-- dim_threshold: configured thresholds (config/smbc_genie.yaml -> ${...} params), the fixed business rules the
-- must-answer questions use, and the onboarding stage SLAs (gold.dim_onboarding_stage <- silver.kyc_case_stage).
WITH cfg (threshold_code, threshold_name, category, threshold_value, unit, comparison, applies_to, description, source) AS (
  VALUES
  ('RORWA_HURDLE', 'RoRWA hurdle', 'Profitability', CAST(${rorwa_hurdle} AS DOUBLE), 'ratio', 'below', 'RoRWA (annualised net contribution / average RWA)', 'Relationships or deals with RoRWA below this are below hurdle (D20: the 8% hurdle wording of the brief means this configured hurdle).', 'config/smbc_genie.yaml'),
  ('RAROC_HURDLE', 'RAROC hurdle', 'Profitability', CAST(${raroc_hurdle} AS DOUBLE), 'ratio', 'below', 'RAROC (risk-adjusted return on economic capital)', 'Deals approved with origination RAROC below this are approved below hurdle.', 'config/smbc_genie.yaml'),
  ('ROE_HURDLE', 'ROE hurdle', 'Profitability', CAST(${roe_hurdle} AS DOUBLE), 'ratio', 'below', 'ROE (net income / allocated capital)', 'Clients with ROE below this are below the cost-of-equity hurdle.', 'config/smbc_genie.yaml'),
  ('SINGLE_BORROWER_ATTENTION_USD', 'Single-borrower attention threshold', 'Credit', CAST(${single_borrower_attention_usd} AS DOUBLE), 'usd', 'at_or_above', 'Group credit exposure (USD)', 'Group exposure at or above this needs senior credit attention (Meridian crosses it after entity resolution).', 'config/smbc_genie.yaml'),
  ('EWS_AMBER_MIN', 'EWS Amber band minimum score', 'Early Warning', CAST(${ews_amber_min} AS DOUBLE), 'score', 'at_or_above', 'EWS composite score 0-100', 'Score at or above this (and below the Red minimum) is Amber; below it is Green.', 'config/smbc_genie.yaml'),
  ('EWS_RED_MIN', 'EWS Red band minimum score', 'Early Warning', CAST(${ews_red_min} AS DOUBLE), 'score', 'at_or_above', 'EWS composite score 0-100', 'Score at or above this is Red.', 'config/smbc_genie.yaml'),
  ('COVENANT_HEADROOM_WARN', 'Covenant headroom warning', 'Credit', CAST(${covenant_headroom_warn} AS DOUBLE), 'ratio', 'below', 'Covenant headroom % at the latest test', 'Headroom below this (and not breached) is a tight covenant / early-warning trigger.', 'config/smbc_genie.yaml')
), rules (threshold_code, threshold_name, category, threshold_value, unit, comparison, applies_to, description, source) AS (
  VALUES
  ('LTV_HIGH', 'High loan-to-value', 'Credit', 0.70D, 'ratio', 'above', 'Collateral LTV', 'Facilities with LTV above 70% need collateral review.', 'business rule (gold)'),
  ('VALUATION_STALE_MONTHS', 'Stale collateral valuation', 'Credit', 24.0D, 'months', 'above', 'Months since last collateral valuation', 'Valuations older than 24 months are stale.', 'business rule (gold)'),
  ('DPD_30_PLUS', '30+ days past due', 'Early Warning', 30.0D, 'days', 'at_or_above', 'Days past due', 'Facilities 30 or more days past due count in the 30+ DPD rate.', 'business rule (gold)'),
  ('DPD_DEFAULT', 'Default (Stage 3) days past due', 'Early Warning', 90.0D, 'days', 'above', 'Days past due', 'More than 90 days past due is default / IFRS 9 Stage 3.', 'business rule (gold)'),
  ('PLAN_ATTAINMENT_LOW', 'Low plan attainment at H1', 'Relationship', 0.40D, 'ratio', 'below', 'Revenue actual YTD / revenue target', 'Groups below 40% attainment at H1 are behind plan.', 'business rule (gold)'),
  ('NO_RM_CONTACT_DAYS', 'No RM contact window', 'Relationship', 90.0D, 'days', 'above', 'Days since the last RM activity', 'Clients without an RM activity in the last 90 days are not touched.', 'business rule (gold)'),
  ('REVENUE_GROWTH_HIGH', 'High revenue growth YoY', 'Relationship', 0.20D, 'ratio', 'above', 'Revenue YoY %', 'Revenue growth above 20% YoY.', 'business rule (gold)'),
  ('REVENUE_DECLINE_HIGH', 'Large revenue decline YoY', 'Relationship', -0.15D, 'ratio', 'below', 'Revenue YoY %', 'Revenue falling more than 15% YoY.', 'business rule (gold)'),
  ('APAC_SHARE_LOW', 'Low APAC share of group revenue', 'Relationship', 0.20D, 'ratio', 'below', 'APAC revenue / global group revenue', 'Groups where APAC earns less than 20% of the global relationship revenue.', 'business rule (gold)'),
  ('CREDIT_COST_SHARE_HIGH', 'High credit cost share', 'Profitability', 0.30D, 'ratio', 'above', 'Credit cost / revenue', 'Clients where credit cost exceeds 30% of revenue.', 'business rule (gold)'),
  ('NBP_HIGH_PROPENSITY', 'High next-best-product propensity', 'Opportunity', 0.70D, 'ratio', 'at_or_above', 'NBP propensity 0-1', 'Clients scoring at or above 0.7 are strong candidates.', 'business rule (gold)'),
  ('DEPOSIT_SURPLUS_LOOKBACK_DAYS', 'Recent deposit-surplus signal window', 'Opportunity', 60.0D, 'days', 'at_most', 'Days since the signal was detected', 'Deposit-surplus signals detected in the last 60 days are recent.', 'business rule (gold)'),
  ('PAYMENT_VOLUME_DROP_YOY', 'Payment volume drop YoY', 'Cash & Liquidity', -0.30D, 'ratio', 'below', 'Payment volume YoY %', 'Payment volume through SMBC down more than 30% YoY.', 'business rule (gold)'),
  ('POOLING_MIN_COUNTRIES', 'Multi-country group for pooling', 'Cash & Liquidity', 3.0D, 'count', 'at_or_above', 'APAC countries with entities', 'Groups present in 3 or more APAC countries are pooling candidates.', 'business rule (gold)'),
  ('TD_MATURITY_WINDOW_DAYS', 'Time-deposit maturity window', 'Cash & Liquidity', 90.0D, 'days', 'at_most', 'Days to TD maturity', 'Time deposits maturing in the next 90 days.', 'business rule (gold)'),
  ('SCF_UTILISATION_HIGH', 'High SCF programme utilisation', 'Trade & SCF', 0.85D, 'ratio', 'above', 'SCF utilisation (financed / limit)', 'Programmes above 85% utilisation need limit review.', 'business rule (gold)'),
  ('SCF_UTILISATION_LOW', 'Low SCF programme utilisation', 'Trade & SCF', 0.40D, 'ratio', 'below', 'SCF utilisation (financed / limit)', 'Programmes below 40% utilisation are under-used.', 'business rule (gold)'),
  ('SCF_ANCHOR_MIN_SUPPLIERS', 'SCF anchor candidate supplier count', 'Trade & SCF', 50.0D, 'count', 'at_or_above', 'Distinct suppliers paid through SMBC', 'Buyers paying 50 or more distinct suppliers through us are SCF anchor candidates.', 'business rule (gold)'),
  ('SURPLUS_ATTENTION_USD', 'Large predicted surplus', 'Cashflow', 20000000.0D, 'usd', 'above', 'Predicted surplus amount (USD)', 'Predicted surpluses above USD 20m not yet placed are deposit opportunities.', 'business rule (gold)'),
  ('RCF_FOLLOW_UP_DAYS', 'RCF drawdown follow-up window', 'Cashflow', 10.0D, 'days', 'at_most', 'Days from predicted shortfall to RCF drawdown', 'A shortfall followed by an RCF drawdown within 10 days counts as actioned by RCF.', 'business rule (gold)'),
  ('MAPE_GOOD', 'Accurate forecast', 'Cashflow', 0.10D, 'ratio', 'at_most', 'Forecast MAPE', 'Clients within 10% MAPE are well forecast.', 'business rule (gold)'),
  ('MARKET_APAC_EXPOSURE_USD', 'Listed-parent APAC exposure of interest', 'Signals', 100000000.0D, 'usd', 'above', 'Group APAC exposure (USD)', 'Listed parents at 52-week lows with APAC exposure above USD 100m.', 'business rule (gold)'),
  ('KYC_DOCS_STUCK_DAYS', 'Stuck in KYC Docs', 'Onboarding', 15.0D, 'days', 'above', 'Days in the KYC Docs stage', 'Cases more than 15 days in KYC Docs are stuck.', 'business rule (gold)'),
  ('FIRST_TRANSACTION_DAYS', 'First transaction after go-live', 'Onboarding', 60.0D, 'days', 'at_most', 'Days from go-live to first transaction', 'Clients transacting within 60 days of go-live are activated.', 'business rule (gold)'),
  ('SHARE_STALE_HOURS', 'Delta Share staleness', 'Data Foundation', 24.0D, 'hours', 'above', 'Hours since the last provider refresh', 'Shared tables not refreshed for more than 24 hours are stale.', 'business rule (gold)'),
  ('ER_EXPOSURE_CHANGE', 'Material exposure change after entity resolution', 'Data Foundation', 0.20D, 'ratio', 'above', 'Group exposure change % between ER runs', 'Exposure changes above 20% after a resolution run need review.', 'business rule (gold)')
), sla AS (
  SELECT concat('SLA_STAGE_', stage_no, '_', upper(regexp_replace(stage_name, '[^A-Za-z]+', '_'))) AS threshold_code,
         concat('Onboarding SLA - ', stage_name)                       AS threshold_name,
         'Onboarding'                                                  AS category,
         CAST(sla_days AS DOUBLE)                                      AS threshold_value,
         'days'                                                        AS unit,
         'at_most'                                                     AS comparison,
         concat('Days in stage ', stage_no, ' (', stage_name, ')')     AS applies_to,
         concat('Cases longer than ', sla_days, ' days in ', stage_name, ' are over SLA.') AS description,
         'silver.kyc_case_stage'                                       AS source
  FROM ${catalog}.gold.dim_onboarding_stage
)
SELECT * FROM cfg UNION ALL SELECT * FROM rules UNION ALL SELECT * FROM sla
