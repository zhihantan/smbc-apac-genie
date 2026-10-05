-- fact_pipeline_opportunity (WP8c): one row per CRM pipeline opportunity (silver.crm_opportunity) with stage,
-- product, amount, probability-weighted value, expected / actual close, cycle time, win / loss and - for
-- signal-sourced opportunities - the source signal (type, detection date, trade corridor) from silver.crm_signal.
-- Fiscal labels of the expected close come from gold.dim_date; close dates after its end (31-Mar-2027) get the same
-- Japanese fiscal rule (FY = year of the date minus 3 months).
SELECT
  o.opportunity_id, o.created_date, trunc(o.created_date, 'MM') AS created_month,
  dcr.fiscal_year AS created_fiscal_year, dcr.fiscal_year_label AS created_fiscal_year_label,
  dcr.fiscal_quarter_label AS created_fiscal_quarter_label,
  dcr.fiscal_year = (SELECT fiscal_year FROM ${catalog}.gold.dim_date WHERE date = DATE'${as_of_date}') AS is_created_current_fiscal_year,
  dc.golden_client_sk, o.golden_client_id, dc.client_group_id, o.crm_account_id,
  o.owner_rm AS owner_rm_code, e.employee_id AS owner_rm_id,
  o.product, dp.product_id, o.product_family,
  o.stage, CASE o.stage WHEN 'Prospecting' THEN 1 WHEN 'Qualification' THEN 2 WHEN 'Proposal' THEN 3
                        WHEN 'Negotiation' THEN 4 WHEN 'Won' THEN 5 WHEN 'Lost' THEN 6 END AS stage_order,
  o.status, o.stage NOT IN ('Won', 'Lost') AS is_open, o.stage IN ('Won', 'Lost') AS is_closed,
  o.stage = 'Won' AS is_won, o.stage = 'Lost' AS is_lost,
  o.win_probability, o.amount_usd, o.amount_usd * o.win_probability AS weighted_amount_usd,
  o.expected_revenue_usd, o.expected_revenue_usd * o.win_probability AS weighted_expected_revenue_usd,
  o.expected_close_date,
  coalesce(dx.fiscal_year, year(add_months(o.expected_close_date, -3))) AS expected_close_fiscal_year,
  coalesce(dx.fiscal_quarter_label,
           concat('FY', year(add_months(o.expected_close_date, -3)), '-Q', quarter(add_months(o.expected_close_date, -3))))
    AS expected_close_fiscal_quarter_label,
  coalesce(dx.fiscal_half_label,
           concat('FY', year(add_months(o.expected_close_date, -3)), '-H',
                  CASE WHEN quarter(add_months(o.expected_close_date, -3)) <= 2 THEN 1 ELSE 2 END))
    AS expected_close_fiscal_half_label,
  o.actual_close_date, dac.fiscal_quarter_label AS actual_close_fiscal_quarter_label,
  datediff(o.actual_close_date, o.created_date) AS cycle_days,
  CASE WHEN o.stage NOT IN ('Won', 'Lost') THEN datediff(DATE'${as_of_date}', o.created_date) END AS open_age_days,
  o.stage NOT IN ('Won', 'Lost') AND o.expected_close_date < DATE'${as_of_date}' AS is_past_expected_close,
  o.lost_reason, o.source_type, o.source_signal_id IS NOT NULL AS is_signal_sourced, o.source_signal_id,
  s.signal_code AS source_signal_code, s.signal_name AS source_signal_name, s.detected_date AS source_signal_detected_date,
  s.corridor_origin AS source_corridor_origin, s.corridor_destination AS source_corridor_destination,
  datediff(o.created_date, s.detected_date) AS signal_to_opportunity_days,
  o.segment AS crm_segment, o.relationship_tier AS crm_relationship_tier, o.booking_country
FROM ${catalog}.silver.crm_opportunity o
JOIN ${catalog}.gold.dim_date dcr ON dcr.date = o.created_date
LEFT JOIN ${catalog}.gold.dim_date dx ON dx.date = o.expected_close_date
LEFT JOIN ${catalog}.gold.dim_date dac ON dac.date = o.actual_close_date
LEFT JOIN ${catalog}.silver.crm_signal s ON s.signal_id = o.source_signal_id
LEFT JOIN ${catalog}.gold.dim_product dp ON dp.product_name = o.product
LEFT JOIN ${catalog}.gold.dim_employee e ON e.rm_code = o.owner_rm
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = o.golden_client_id
  AND o.created_date <= dc.valid_to
  AND (o.created_date >= dc.valid_from OR dc.version_no = 1)
