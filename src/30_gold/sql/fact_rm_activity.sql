-- fact_rm_activity (WP8c): one row per CRM activity (call, meeting, email, video call, site visit) with the RM,
-- the client contact and role, purpose, product discussed, short note, sentiment and the follow-up action
-- (silver.crm_activity + crm_contact; RM codes -> gold.dim_employee).
SELECT
  a.activity_id, a.activity_date, trunc(a.activity_date, 'MM') AS activity_month, d.fiscal_year, d.fiscal_year_label,
  d.fiscal_quarter_label, d.fiscal_half_label,
  dc.golden_client_sk, a.golden_client_id, dc.client_group_id, a.crm_account_id,
  e.employee_id AS rm_employee_id, a.rm_code, e.coverage_office AS rm_office,
  a.contact_id, coalesce(c.contact_role, 'Unknown') AS contact_role, c.seniority AS contact_seniority,
  a.activity_type, a.activity_type IN ('Meeting', 'Site Visit') AS is_in_person,
  a.purpose, a.subject, a.product_discussed, dp.product_id AS product_discussed_id, a.product_family,
  a.note_text, a.raw_tone AS note_tone, a.sentiment_score,
  a.action_required, a.next_action, a.next_action_due_date, a.next_action_status,
  coalesce(a.next_action_status IN ('Open', 'Overdue'), false) AS is_next_action_open,
  coalesce(a.next_action_status = 'Overdue', false) AS is_next_action_overdue,
  a.related_signal_id, a.related_opportunity_id, a.duration_minutes,
  datediff(DATE'${as_of_date}', a.activity_date) AS days_before_as_of,
  a.activity_date > date_sub(DATE'${as_of_date}', 90) AS is_last_90_days
FROM ${catalog}.silver.crm_activity a
JOIN ${catalog}.gold.dim_date d ON d.date = a.activity_date
LEFT JOIN ${catalog}.silver.crm_contact c ON c.contact_id = a.contact_id
LEFT JOIN ${catalog}.gold.dim_employee e ON e.rm_code = a.rm_code
LEFT JOIN ${catalog}.gold.dim_product dp ON dp.product_name = a.product_discussed
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = a.golden_client_id
  AND a.activity_date <= dc.valid_to
  AND (a.activity_date >= dc.valid_from OR dc.version_no = 1)
