-- fact_opportunity_signal (WP8c): one row per CRM opportunity signal (silver.crm_signal: wallet leakage, deposit
-- surplus, corridor growth, SCF anchor, SLL eligibility, maturing facilities, product gaps, capex / M&A news) with
-- its lifecycle (Open / In Pipeline / Dismissed), RM action lag, the linked pipeline opportunity and precomputed
-- flags for the standard questions (detected in the last 60 days, client holds a time deposit / investment product,
-- client has an open lending opportunity).
WITH opp AS (SELECT * FROM ${catalog}.silver.crm_opportunity),
liq_now AS (      -- clients holding a liquidity (time deposit / money market / investment) product at the as-of month
  SELECT DISTINCT golden_client_id FROM ${catalog}.gold.fact_product_holding_monthly
  WHERE month_end_date = DATE'${as_of_date}' AND is_active AND product_family = 'Liquidity'
),
open_lending AS (
  SELECT DISTINCT golden_client_id FROM opp
  WHERE stage NOT IN ('Won', 'Lost') AND product_family IN ('Corporate Lending', 'Sustainable Finance')
)
SELECT
  s.signal_id, s.detected_date, trunc(s.detected_date, 'MM') AS signal_month, d.fiscal_year, d.fiscal_year_label,
  d.fiscal_quarter_label, d.fiscal_half_label,
  dc.golden_client_sk, s.golden_client_id, dc.client_group_id, s.crm_account_id,
  s.signal_code, s.signal_name, s.signal_category, s.polarity, s.source_quadrant, s.detection_engine,
  s.strength, s.confidence, s.estimated_revenue_usd, s.observed_amount_usd, s.metric_value,
  s.currency_pair, s.corridor_origin, s.corridor_destination, s.related_ref,
  s.recommended_product, dp.product_id AS recommended_product_id, dp.product_family AS recommended_product_family,
  s.signal_detail, s.owner_rm AS owner_rm_code, e.employee_id AS owner_rm_id,
  s.status AS source_status,
  CASE WHEN s.status = 'Converted' THEN 'In Pipeline' WHEN s.status = 'Dismissed' THEN 'Dismissed' ELSE 'Open' END AS signal_status,
  s.status IN ('New', 'Actioned') AS is_open,
  s.actioned_date IS NOT NULL AS is_actioned, s.status_date, s.actioned_date, s.actioned_by,
  datediff(s.actioned_date, s.detected_date) AS action_lag_days,
  s.dismissed_reason, s.linked_opportunity_id, s.linked_opportunity_id IS NOT NULL AS is_converted_to_pipeline,
  o.stage AS linked_opportunity_stage, o.stage = 'Won' AS is_linked_opportunity_won,
  CASE WHEN s.status IN ('New', 'Actioned') THEN datediff(DATE'${as_of_date}', s.detected_date)
       ELSE datediff(coalesce(s.status_date, s.actioned_date, DATE'${as_of_date}'), s.detected_date) END AS days_open,
  datediff(DATE'${as_of_date}', s.detected_date) AS days_since_detected,
  datediff(DATE'${as_of_date}', s.detected_date) <=
    (SELECT CAST(threshold_value AS INT) FROM ${catalog}.gold.dim_threshold WHERE threshold_code = 'DEPOSIT_SURPLUS_LOOKBACK_DAYS')
    AS is_last_60_days,
  l.golden_client_id IS NOT NULL AS client_holds_td_or_investment,
  ol.golden_client_id IS NOT NULL AS client_has_open_lending_opportunity
FROM ${catalog}.silver.crm_signal s
JOIN ${catalog}.gold.dim_date d ON d.date = s.detected_date
LEFT JOIN opp o ON o.opportunity_id = s.linked_opportunity_id
LEFT JOIN liq_now l ON l.golden_client_id = s.golden_client_id
LEFT JOIN open_lending ol ON ol.golden_client_id = s.golden_client_id
LEFT JOIN ${catalog}.gold.dim_product dp ON dp.product_name = s.recommended_product
LEFT JOIN ${catalog}.gold.dim_employee e ON e.rm_code = s.owner_rm
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = s.golden_client_id
  AND s.detected_date <= dc.valid_to
  AND (s.detected_date >= dc.valid_from OR dc.version_no = 1)
