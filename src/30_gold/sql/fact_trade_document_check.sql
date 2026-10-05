-- fact_trade_document_check: silver.trade_presentation + the LC (gold.fact_trade_finance_transaction), amendments
-- before the presentation (silver.trade_event), the as-was golden_client_sk (D11) and each client's discrepancy-rate
-- trend H2 FY2025 (Oct-2025..Mar-2026) -> H1 FY2026 (Apr..Sep-2026) over examined presentations.
WITH p AS (
  SELECT x.*, to_date(x.received_ts) AS received_date, x.decision <> 'Pending Examination' AS is_examined
  FROM ${catalog}.silver.trade_presentation x
), amend AS (
  SELECT p.presentation_id, count(e.event_id) AS n_before
  FROM p LEFT JOIN ${catalog}.silver.trade_event e
    ON e.txn_id = p.txn_id AND e.event_type = 'Amend' AND e.event_date <= p.received_date
  GROUP BY p.presentation_id
), trend AS (
  SELECT golden_client_id,
         count_if(received_date BETWEEN DATE'2026-04-01' AND DATE'${as_of_date}')   AS n_now,
         avg(CASE WHEN received_date BETWEEN DATE'2026-04-01' AND DATE'${as_of_date}'
                  THEN CASE WHEN is_discrepant THEN 1.0 ELSE 0.0 END END)            AS r_now,
         count_if(received_date BETWEEN DATE'2025-10-01' AND DATE'2026-03-31')      AS n_prev,
         avg(CASE WHEN received_date BETWEEN DATE'2025-10-01' AND DATE'2026-03-31'
                  THEN CASE WHEN is_discrepant THEN 1.0 ELSE 0.0 END END)            AS r_prev
  FROM p WHERE is_examined
  GROUP BY golden_client_id
)
SELECT
  p.presentation_id,
  p.txn_id,
  p.presentation_no,
  p.presentation_no = 1                                                         AS is_first_presentation,
  coalesce(p.is_re_presentation, false)                                         AS is_re_presentation,
  p.received_ts,
  p.received_date,
  trunc(p.received_date, 'MM')                                                  AS month,
  p.checked_ts,
  dc.golden_client_sk,
  p.golden_client_id,
  coalesce(p.client_group_id, dc.client_group_id)                               AS client_group_id,
  t.product_type,
  t.product_id,
  t.booking_country,
  p.ops_team,
  p.amount_usd,
  p.turnaround_hours,
  p.sla_hours,
  coalesce(p.is_over_sla, false)                                                AS is_over_sla,
  p.is_examined,
  coalesce(p.is_discrepant, false)                                              AS is_discrepant,
  CAST(coalesce(p.discrepancy_count, 0) AS INT)                                 AS discrepancy_count,
  nullif(p.discrepancy_types, '')                                               AS discrepancy_types,
  CASE WHEN NOT p.is_examined THEN NULL
       ELSE coalesce(nullif(p.primary_discrepancy_type, ''), 'None') END        AS primary_discrepancy_type,
  p.decision,
  p.decision = 'Discrepant - Waived'                                            AS is_waived,
  p.decision = 'Discrepant - Refused'                                           AS is_refused,
  CAST(coalesce(a.n_before, 0) AS INT)                                          AS amendments_before_presentation,
  t.amendment_count                                                             AS txn_amendment_count,
  CASE WHEN tr.n_now >= 3 THEN tr.r_now END                                     AS client_discrepancy_rate_h1_fy2026,
  CASE WHEN tr.n_prev >= 3 THEN tr.r_prev END                                   AS client_discrepancy_rate_h2_fy2025,
  coalesce(tr.n_now >= 3 AND tr.n_prev >= 3 AND tr.r_now - tr.r_prev >= 0.10, false) AS is_client_discrepancy_rising
FROM p
JOIN ${catalog}.gold.fact_trade_finance_transaction t ON t.txn_id = p.txn_id
LEFT JOIN amend a ON a.presentation_id = p.presentation_id
LEFT JOIN trend tr ON tr.golden_client_id = p.golden_client_id
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = p.golden_client_id
  AND p.received_date <= dc.valid_to
  AND (p.received_date >= dc.valid_from OR dc.version_no = 1)
