-- fact_channel_usage_monthly: silver.pay_channel_usage (core customer x channel x month) summed to the golden client
-- (within-source duplicate customers of one client are combined), with the as-was golden_client_sk (D11), the
-- client's coverage office and the channel type.
WITH u AS (
  SELECT golden_client_id, usage_month, channel,
         max(client_group_id)                         AS client_group_id,
         sum(active_users)                            AS active_users,
         sum(logins)                                  AS logins,
         sum(payments_initiated)                      AS payments_initiated,
         sum(payments_value_usd)                      AS payments_value_usd,
         sum(stp_payments)                            AS stp_payments,
         sum(manual_instructions)                     AS manual_instructions,
         sum(digital_payments)                        AS digital_payments,
         sum(api_calls)                               AS api_calls,
         sum(files_transmitted)                       AS files_transmitted,
         count(*)                                     AS n_src
  FROM ${catalog}.silver.pay_channel_usage
  GROUP BY golden_client_id, usage_month, channel
)
SELECT
  u.golden_client_id,
  u.usage_month,
  u.channel,
  dc.golden_client_sk,
  coalesce(u.client_group_id, dc.client_group_id)                                   AS client_group_id,
  dc.coverage_office                                                                AS booking_country,
  CASE WHEN u.channel IN ('Portal', 'Payments API', 'Host-to-Host') THEN 'Digital' ELSE 'Network rail' END AS channel_type,
  pr.product_id,
  CAST(u.active_users AS INT)                                                       AS active_users,
  CAST(u.logins AS BIGINT)                                                          AS logins,
  CAST(u.payments_initiated AS BIGINT)                                              AS payments_initiated,
  u.payments_value_usd,
  CAST(u.stp_payments AS BIGINT)                                                    AS stp_payments,
  CAST(u.manual_instructions AS BIGINT)                                             AS manual_instructions,
  CAST(u.digital_payments AS BIGINT)                                                AS digital_payments,
  CAST(u.api_calls AS BIGINT)                                                       AS api_calls,
  CAST(u.files_transmitted AS BIGINT)                                               AS files_transmitted,
  (u.active_users + u.logins + u.payments_initiated + u.api_calls + u.files_transmitted) > 0 AS is_active,
  CAST(u.n_src AS INT)                                                              AS source_customers
FROM u
LEFT JOIN ${catalog}.gold.dim_product pr
  ON pr.product_name = u.channel AND u.channel IN ('Payments API', 'Host-to-Host')
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = u.golden_client_id
  AND u.usage_month <= dc.valid_to
  AND (u.usage_month >= dc.valid_from OR dc.version_no = 1)
