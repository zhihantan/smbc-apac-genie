-- dim_client_group: Tokyo HO group master + the APAC roll-up of the current dim_client versions (D12), the
-- group issuer rating (silver.ext_rating), the JP parent rating (Japan share) and EMEA / AMER entity counts.
WITH cur AS (
  SELECT * FROM ${catalog}.gold.dim_client WHERE is_current
), m AS (
  SELECT * FROM ${catalog}.silver.share_jp_group_master
), groups AS (
  SELECT client_group_id FROM m WHERE client_group_id IS NOT NULL
  UNION SELECT client_group_id FROM cur WHERE client_group_id IS NOT NULL
), mode_attrs AS (   -- most common segment / tier / industry among the group's clients (ties alphabetical)
  SELECT client_group_id,
         max_by(segment, struct(n_seg, segment)) AS group_segment,
         max_by(relationship_tier, struct(n_tier, relationship_tier)) AS group_relationship_tier,
         max_by(named_struct('s', industry_sector, 'ss', industry_subsector), struct(n_ind, industry_sector, industry_subsector)) AS ind
  FROM (SELECT client_group_id, segment, relationship_tier, industry_sector, industry_subsector,
               count(*) OVER (PARTITION BY client_group_id, segment) AS n_seg,
               count(*) OVER (PARTITION BY client_group_id, relationship_tier) AS n_tier,
               count(*) OVER (PARTITION BY client_group_id, industry_sector, industry_subsector) AS n_ind
        FROM cur WHERE client_group_id IS NOT NULL)
  GROUP BY client_group_id
), risk AS (
  SELECT client_group_id,
         max(internal_rating_grade) AS worst_grade,
         max_by(rating_equivalent, internal_rating_grade) AS worst_equiv,
         max(ifrs9_stage) AS worst_stage,
         CASE max(CASE ews_band WHEN 'Red' THEN 3 WHEN 'Amber' THEN 2 WHEN 'Green' THEN 1 END)
              WHEN 3 THEN 'Red' WHEN 2 THEN 'Amber' WHEN 1 THEN 'Green' END AS worst_band,
         count_if(ews_band = 'Red') AS n_red, count_if(ews_band = 'Amber') AS n_amber, count_if(watchlist_flag) AS n_wl,
         count(*) AS n_apac, count(DISTINCT coverage_office) AS n_countries,
         array_join(array_sort(collect_set(coverage_office)), ', ') AS countries
  FROM cur WHERE client_group_id IS NOT NULL GROUP BY client_group_id
), plan_lead AS (    -- entity the group's latest account plan is held on
  SELECT client_group_id, max_by(golden_client_id, struct(fiscal_year, n, golden_client_id)) AS golden_client_id
  FROM (SELECT client_group_id, golden_client_id, fiscal_year, count(*) AS n FROM ${catalog}.silver.crm_account_plan
        WHERE client_group_id IS NOT NULL AND golden_client_id IS NOT NULL GROUP BY ALL)
  GROUP BY client_group_id
), footprint_lead AS (   -- else: most source records, Singapore hub first, then the oldest golden id
  SELECT client_group_id,
         max_by(golden_client_id, struct(n_source_records, coverage_office = 'SG', -CAST(regexp_extract(golden_client_id, '([0-9]+)$', 1) AS BIGINT))) AS golden_client_id
  FROM cur WHERE client_group_id IS NOT NULL GROUP BY client_group_id
), lead AS (
  SELECT g.client_group_id,
         CASE WHEN pc.golden_client_id IS NOT NULL THEN pl.golden_client_id ELSE fl.golden_client_id END AS golden_client_id,
         CASE WHEN pc.golden_client_id IS NOT NULL THEN 'account_plan' WHEN fl.golden_client_id IS NOT NULL THEN 'largest_footprint' END AS basis
  FROM groups g
  LEFT JOIN plan_lead pl ON pl.client_group_id = g.client_group_id
  LEFT JOIN cur pc ON pc.golden_client_id = pl.golden_client_id AND pc.client_group_id = g.client_group_id
  LEFT JOIN footprint_lead fl ON fl.client_group_id = g.client_group_id
), agency AS (
  SELECT agency, row_number() OVER (ORDER BY count(DISTINCT issuer_id) DESC, agency) AS arank
  FROM ${catalog}.silver.ext_rating GROUP BY agency
), xr AS (
  SELECT x.client_group_id,
         max_by(named_struct('rating', x.rating, 'agency', x.agency, 'outlook', x.outlook, 'd', x.action_date),
                struct(-a.arank, x.action_date, x.rating_id)) AS r
  FROM ${catalog}.silver.ext_rating x JOIN agency a ON a.agency = x.agency
  WHERE x.client_group_id IS NOT NULL AND x.rating IS NOT NULL AND x.action_date <= DATE'${as_of_date}'
  GROUP BY x.client_group_id
), jpr AS (
  SELECT client_group_id,
         max_by(named_struct('grade', grade_to, 'equiv', rating_equivalent, 'outlook', outlook, 'd', rating_date),
                struct(rating_date, rating_id)) AS r
  FROM ${catalog}.silver.share_jp_parent_rating
  WHERE client_group_id IS NOT NULL AND rating_date <= DATE'${as_of_date}'
  GROUP BY client_group_id
), emea AS (
  SELECT client_group_id, count(DISTINCT provider_entity_id) AS n FROM ${catalog}.silver.share_emea_entity_master
  WHERE client_group_id IS NOT NULL GROUP BY client_group_id
), amer AS (
  SELECT client_group_id, count(DISTINCT provider_entity_id) AS n FROM ${catalog}.silver.share_amer_entity_master
  WHERE client_group_id IS NOT NULL GROUP BY client_group_id
), listed AS (
  SELECT client_group_id, bool_or(coalesce(is_listed, false)) AS is_listed FROM ${catalog}.silver.ext_issuer
  WHERE client_group_id IS NOT NULL GROUP BY client_group_id
), plan_fy AS (
  SELECT DISTINCT client_group_id FROM ${catalog}.silver.crm_account_plan
  WHERE fiscal_year = CASE WHEN month(DATE'${as_of_date}') >= ${fy_start_month} THEN year(DATE'${as_of_date}')
                           ELSE year(DATE'${as_of_date}') - 1 END
)
SELECT
  g.client_group_id,
  coalesce(m.group_name_global, lc.group_name)                                AS group_name,
  m.hq_country,
  hc.country_name                                                             AS hq_country_name,
  m.hq_region,
  coalesce(m.hq_country = 'JP', false)                                        AS is_japanese_group,
  m.jp_parent_id,
  m.jp_parent_legal_name,
  ma.group_segment,
  ma.group_relationship_tier,
  coalesce(ma.group_relationship_tier = 'Strategic', false)                   AS is_strategic_group,
  ma.ind.s                                                                    AS group_industry_sector,
  ma.ind.ss                                                                   AS group_industry_subsector,
  m.global_segment, m.global_tier, m.global_industry, m.global_relationship_owner_region,
  m.global_rm_code, m.global_rm_name, m.global_coverage_unit, m.relationship_since,
  l.golden_client_id                                                          AS lead_golden_client_id,
  lc.golden_client_sk                                                         AS lead_golden_client_sk,
  lc.display_name                                                             AS lead_client_name,
  l.basis                                                                     AS lead_basis,
  lc.coverage_office                                                          AS lead_office,
  lc.primary_rm_id                                                            AS lead_rm_id,
  lc.primary_rm_code                                                          AS lead_rm_code,
  lc.primary_rm_name                                                          AS lead_rm_name,
  CAST(r.worst_grade AS INT)                                                  AS worst_internal_rating_grade,
  r.worst_equiv                                                               AS worst_rating_equivalent,
  CAST(r.worst_stage AS INT)                                                  AS worst_ifrs9_stage,
  r.worst_band                                                                AS worst_ews_band,
  CAST(coalesce(r.n_red, 0) AS BIGINT)                                        AS clients_red,
  CAST(coalesce(r.n_amber, 0) AS BIGINT)                                      AS clients_amber,
  CAST(coalesce(r.n_wl, 0) AS BIGINT)                                         AS clients_on_watchlist,
  xr.r.rating                                                                 AS group_external_rating,
  xr.r.agency                                                                 AS group_external_rating_agency,
  xr.r.outlook                                                                AS group_external_outlook,
  xr.r.d                                                                      AS group_external_rating_date,
  CAST(jpr.r.grade AS INT)                                                    AS jp_parent_grade,
  jpr.r.equiv                                                                 AS jp_parent_rating_equivalent,
  jpr.r.outlook                                                               AS jp_parent_outlook,
  jpr.r.d                                                                     AS jp_parent_rating_date,
  coalesce(li.is_listed, false)                                               AS is_listed_group,
  CAST(coalesce(r.n_apac, 0) AS BIGINT)                                       AS n_entities_apac,
  CAST(coalesce(r.n_countries, 0) AS BIGINT)                                  AS n_apac_countries,
  r.countries                                                                 AS apac_countries_text,
  CAST(coalesce(e.n, 0) AS BIGINT)                                            AS n_entities_emea,
  CAST(coalesce(a.n, 0) AS BIGINT)                                            AS n_entities_amer,
  CAST(coalesce(r.n_apac, 0) + coalesce(e.n, 0) + coalesce(a.n, 0) + CASE WHEN m.jp_parent_id IS NOT NULL THEN 1 ELSE 0 END AS BIGINT) AS n_entities_global,
  pf.client_group_id IS NOT NULL                                              AS has_account_plan_current_fy,
  m.master_status
FROM groups g
LEFT JOIN m ON m.client_group_id = g.client_group_id
LEFT JOIN ${catalog}.gold.dim_country hc ON hc.country_code = m.hq_country
LEFT JOIN mode_attrs ma ON ma.client_group_id = g.client_group_id
LEFT JOIN risk r ON r.client_group_id = g.client_group_id
LEFT JOIN lead l ON l.client_group_id = g.client_group_id
LEFT JOIN cur lc ON lc.golden_client_id = l.golden_client_id
LEFT JOIN xr ON xr.client_group_id = g.client_group_id
LEFT JOIN jpr ON jpr.client_group_id = g.client_group_id
LEFT JOIN emea e ON e.client_group_id = g.client_group_id
LEFT JOIN amer a ON a.client_group_id = g.client_group_id
LEFT JOIN listed li ON li.client_group_id = g.client_group_id
LEFT JOIN plan_fy pf ON pf.client_group_id = g.client_group_id
