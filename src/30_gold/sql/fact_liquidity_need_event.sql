-- fact_liquidity_need_event: silver.cf_event + the linked RCF drawdown in silver.core_loan_schedule, action product,
-- flags for the must-answer questions (cut-offs from gold.dim_threshold, D29) and the as-was golden_client_sk (D11).
SELECT
  e.event_id,
  e.event_date,
  trunc(e.event_date, 'MM')                                                  AS event_month,
  e.event_type,
  dc.golden_client_sk,
  e.golden_client_id,
  coalesce(e.client_group_id, dc.client_group_id)                            AS client_group_id,
  e.cust_no,
  dc.coverage_office                                                         AS booking_country,
  e.model_version,
  e.forecast_run_date,
  e.forecast_target_month,
  e.horizon_days,
  '30D'                                                                      AS horizon_label,
  e.forecast_inflows_usd,
  e.expected_outflows_usd,
  e.predicted_net_usd,
  e.predicted_amount_usd,
  e.severity_ratio,
  coalesce(e.has_rcf, false)                                                 AS has_rcf,
  coalesce(e.undrawn_rcf_usd, 0.0)                                           AS undrawn_rcf_usd,
  coalesce(e.action_taken, 'None')                                           AS action_taken,
  pr.product_id                                                              AS action_product_id,
  e.action_date,
  e.days_to_action,
  e.action_amount_usd,
  e.linked_facility_id,
  e.linked_schedule_event_id,
  s.event_date                                                               AS linked_drawdown_date,
  s.amount_usd                                                               AS linked_drawdown_usd,
  e.est_action_revenue_usd,
  e.event_status,
  e.action_date IS NOT NULL                                                  AS is_actioned,
  e.event_type = 'Predicted Shortfall' AND e.action_taken = 'RCF Drawdown'
    AND e.linked_schedule_event_id IS NOT NULL AND e.days_to_action <= th.rcf_days AS is_rcf_drawdown_within_10d,
  e.event_type = 'Predicted Surplus' AND e.predicted_amount_usd > th.surplus_usd AS is_large_surplus,
  e.event_type = 'Predicted Surplus' AND coalesce(e.action_taken, 'None') <> 'TD Placed' AS is_surplus_not_placed
FROM ${catalog}.silver.cf_event e
CROSS JOIN (SELECT max(CASE WHEN threshold_code = 'RCF_FOLLOW_UP_DAYS' THEN threshold_value END) AS rcf_days,
                   max(CASE WHEN threshold_code = 'SURPLUS_ATTENTION_USD' THEN threshold_value END) AS surplus_usd
            FROM ${catalog}.gold.dim_threshold) th
LEFT JOIN ${catalog}.silver.core_loan_schedule s
  ON s.schedule_event_id = e.linked_schedule_event_id AND s.event_type = 'Drawdown'
LEFT JOIN ${catalog}.gold.dim_product pr
  ON pr.product_name = CASE e.action_taken WHEN 'RCF Drawdown' THEN 'Revolving Credit Facility'
                                           WHEN 'TD Placed' THEN 'Time Deposit' END
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = e.golden_client_id
  AND e.event_date <= dc.valid_to
  AND (e.event_date >= dc.valid_from OR dc.version_no = 1)
