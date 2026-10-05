-- dim_industry: silver.ref_industry_peer (one peer group per sector / subsector).
SELECT
  industry_sector,
  industry_subsector,
  peer_group_id,
  coalesce(is_carbon_intensive, false)                     AS is_carbon_intensive,
  CAST(sector_limit_usd AS DOUBLE)                         AS sector_limit_usd,
  concat(industry_sector, ' - ', industry_subsector)       AS industry_label
FROM ${catalog}.silver.ref_industry_peer
WHERE industry_sector IS NOT NULL AND industry_subsector IS NOT NULL
