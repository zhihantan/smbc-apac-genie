-- dim_scf_programme: silver.scf_programme + supplier statistics (silver.scf_supplier) + the anchor's dim_client version.
WITH sup AS (
  SELECT programme_id,
         count(*) AS n_listed,
         count_if(coalesce(is_onboarded, false)) AS n_onboarded,
         count_if(coalesce(n_invoices_financed, 0) > 0) AS n_financing,
         count(DISTINCT supplier_country) AS n_countries,
         count_if(coalesce(routes_to_other_bank, false)) AS n_elsewhere
  FROM ${catalog}.silver.scf_supplier GROUP BY programme_id
)
SELECT
  p.programme_id,
  concat(coalesce(c.short_name, p.anchor_party_id), ' ', p.programme_type)        AS programme_name,
  p.programme_type,
  p.anchor_party_id,
  p.golden_client_id                                                              AS anchor_golden_client_id,
  c.golden_client_sk                                                              AS anchor_golden_client_sk,
  c.display_name                                                                  AS anchor_client_name,
  c.coverage_office                                                               AS anchor_country,
  coalesce(p.client_group_id, c.client_group_id)                                  AS client_group_id,
  p.launch_date,
  upper(p.currency)                                                               AS currency,
  p.limit_usd,
  p.drawn_usd,
  coalesce(p.utilisation, p.drawn_usd / nullif(p.limit_usd, 0))                   AS utilisation,
  CASE WHEN coalesce(p.utilisation, p.drawn_usd / nullif(p.limit_usd, 0)) > 0.85 THEN 'Above 85%'
       WHEN coalesce(p.utilisation, p.drawn_usd / nullif(p.limit_usd, 0)) < 0.40 THEN 'Below 40%'
       ELSE '40-85%' END                                                          AS utilisation_band,
  CAST(coalesce(s.n_listed, 0) AS INT)                                            AS n_suppliers_total,
  CAST(coalesce(s.n_onboarded, 0) AS BIGINT)                                      AS n_suppliers_onboarded,
  CAST(coalesce(s.n_financing, 0) AS BIGINT)                                      AS n_suppliers_financing,
  CAST(coalesce(s.n_countries, 0) AS BIGINT)                                      AS n_supplier_countries,
  CAST(coalesce(s.n_elsewhere, 0) AS BIGINT)                                      AS n_suppliers_banking_elsewhere,
  p.primary_corridor,
  CAST(floor(months_between(DATE'${as_of_date}', p.launch_date)) AS INT)          AS programme_age_months
FROM ${catalog}.silver.scf_programme p
LEFT JOIN ${catalog}.gold.dim_client c ON c.golden_client_id = p.golden_client_id AND c.is_current
LEFT JOIN sup s ON s.programme_id = p.programme_id
