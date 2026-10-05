-- fact_scf_programme_drawdown: silver.trade_scf_invoice + supplier attributes (silver.scf_supplier), programme
-- type / product, anchor coverage office, a supplier spend segment (rank of the supplier's invoice value in the last
-- 12 months within its programme) and the anchor's as-was golden_client_sk (D11).
WITH spend AS (
  SELECT programme_id, supplier_id, sum(invoice_amount_usd) AS usd
  FROM ${catalog}.silver.trade_scf_invoice
  WHERE invoice_date > add_months(DATE'${as_of_date}', -12) AND invoice_date <= DATE'${as_of_date}'
  GROUP BY programme_id, supplier_id
), seg AS (
  SELECT s.programme_id, s.supplier_id,
         percent_rank() OVER (PARTITION BY s.programme_id ORDER BY coalesce(sp.usd, 0.0) DESC, s.supplier_id) AS pr
  FROM ${catalog}.silver.scf_supplier s
  LEFT JOIN spend sp ON sp.programme_id = s.programme_id AND sp.supplier_id = s.supplier_id
)
SELECT
  i.invoice_id,
  i.programme_id,
  pg.programme_type,
  pd.product_id,
  dc.golden_client_sk,
  i.golden_client_id,
  coalesce(i.client_group_id, pg.client_group_id, dc.client_group_id)          AS client_group_id,
  i.anchor_party_id,
  ca.coverage_office                                                            AS anchor_country,
  i.supplier_id,
  s.supplier_name,
  upper(s.supplier_country)                                                     AS supplier_country,
  CASE WHEN sg.pr < 0.2 THEN 'Strategic (top 20%)' WHEN sg.pr < 0.5 THEN 'Core (next 30%)'
       ELSE 'Long tail (bottom 50%)' END                                        AS supplier_segment,
  coalesce(s.is_onboarded, false)                                               AS is_supplier_onboarded,
  s.onboarded_date                                                              AS supplier_onboarded_date,
  coalesce(s.routes_to_other_bank, false)                                       AS supplier_routes_to_other_bank,
  i.supplier_invoice_no,
  i.invoice_date,
  trunc(i.invoice_date, 'MM')                                                   AS invoice_month,
  i.approval_date,
  i.due_date,
  upper(i.currency)                                                             AS currency,
  i.invoice_amount_usd,
  coalesce(i.is_financed, false)                                                AS is_financed,
  i.financing_date,
  trunc(i.financing_date, 'MM')                                                 AS financing_month,
  coalesce(i.financed_amount_usd, 0.0)                                          AS financed_amount_usd,
  i.advance_rate,
  i.discount_rate_pct,
  i.days_financed,
  coalesce(i.financed_amount_usd, 0.0) * coalesce(i.days_financed, 0)           AS financed_usd_days,
  coalesce(i.discount_amount_usd, 0.0)                                          AS discount_amount_usd,
  i.net_proceeds_usd,
  i.status,
  coalesce(i.is_financed, false) AND i.financing_date <= DATE'${as_of_date}'
    AND i.due_date > DATE'${as_of_date}'                                        AS is_outstanding_at_as_of
FROM ${catalog}.silver.trade_scf_invoice i
JOIN ${catalog}.silver.scf_programme pg ON pg.programme_id = i.programme_id
LEFT JOIN ${catalog}.silver.scf_supplier s ON s.supplier_id = i.supplier_id
LEFT JOIN seg sg ON sg.programme_id = i.programme_id AND sg.supplier_id = i.supplier_id
LEFT JOIN ${catalog}.gold.dim_product pd ON pd.product_name = pg.programme_type
LEFT JOIN ${catalog}.gold.dim_client ca ON ca.golden_client_id = i.golden_client_id AND ca.is_current
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = i.golden_client_id
  AND i.invoice_date <= dc.valid_to
  AND (i.invoice_date >= dc.valid_from OR dc.version_no = 1)
