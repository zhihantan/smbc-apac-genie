-- dim_account_plan_initiative: silver.crm_account_plan_initiative + group, lead client, owners and due-date fiscal labels.
SELECT
  i.initiative_id,
  i.plan_id,
  coalesce(i.client_group_id, c.client_group_id)                                AS client_group_id,
  g.group_name,
  i.crm_account_id,
  i.golden_client_id                                                            AS lead_golden_client_id,
  c.golden_client_sk                                                            AS lead_golden_client_sk,
  i.fiscal_year,
  concat('FY', i.fiscal_year)                                                   AS fiscal_year_label,
  i.product_family,
  i.initiative_name,
  i.description                                                                 AS initiative_description,
  i.target_revenue_usd,
  i.status,
  i.completed_date IS NULL AND coalesce(i.status, '') NOT IN ('Completed', 'Cancelled', 'Not Achieved') AS is_open,
  i.completed_date IS NULL AND coalesce(i.status, '') NOT IN ('Completed', 'Cancelled', 'Not Achieved')
    AND i.due_date < DATE'${as_of_date}'                                        AS is_overdue,
  i.priority,
  i.due_date,
  d.fiscal_quarter_label                                                        AS due_fiscal_quarter_label,
  d.fiscal_half_label                                                           AS due_fiscal_half_label,
  i.completed_date,
  i.created_date,
  i.owner_employee_id,
  oe.employee_name                                                              AS owner_name,
  i.owner_rm                                                                    AS owner_rm_code,
  re.employee_id                                                                AS owner_rm_employee_id,
  re.employee_name                                                              AS owner_rm_name,
  i.linked_opportunity_id
FROM ${catalog}.silver.crm_account_plan_initiative i
LEFT JOIN ${catalog}.gold.dim_client c ON c.golden_client_id = i.golden_client_id AND c.is_current
LEFT JOIN ${catalog}.gold.dim_client_group g ON g.client_group_id = coalesce(i.client_group_id, c.client_group_id)
LEFT JOIN ${catalog}.gold.dim_date d ON d.date = i.due_date
LEFT JOIN ${catalog}.gold.dim_employee oe ON oe.employee_id = i.owner_employee_id
LEFT JOIN ${catalog}.gold.dim_employee re ON re.rm_code = i.owner_rm
