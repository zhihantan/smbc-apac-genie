-- dim_contact: silver.crm_contact + last interaction from silver.crm_activity (to the as-of date).
WITH act AS (
  SELECT contact_id, max(activity_date) AS last_date,
         count_if(activity_date > date_sub(DATE'${as_of_date}', 365)) AS n_12m
  FROM ${catalog}.silver.crm_activity
  WHERE contact_id IS NOT NULL AND activity_date <= DATE'${as_of_date}'
  GROUP BY contact_id
)
SELECT
  k.contact_id, k.crm_account_id, k.golden_client_id, c.golden_client_sk,
  coalesce(k.client_group_id, c.client_group_id)            AS client_group_id,
  k.full_name, k.first_name, k.last_name, k.job_title,
  k.function                                                AS contact_function,
  k.contact_role, k.seniority, k.is_primary, k.email, k.preferred_channel, k.relationship_strength,
  k.is_active, k.created_date, k.inactive_since,
  a.last_date                                               AS last_interaction_date,
  datediff(DATE'${as_of_date}', a.last_date)                AS days_since_last_interaction,
  CAST(coalesce(a.n_12m, 0) AS BIGINT)                      AS interactions_12m
FROM ${catalog}.silver.crm_contact k
LEFT JOIN ${catalog}.gold.dim_client c ON c.golden_client_id = k.golden_client_id AND c.is_current
LEFT JOIN act a ON a.contact_id = k.contact_id
