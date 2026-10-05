-- dim_booking_entity: silver.ref_booking_entity, with the JP head-office attributes (and any missing country
-- name) filled from dim_country.
SELECT
  upper(b.booking_entity_id)                                                   AS booking_entity_id,
  CASE WHEN b.is_apac THEN concat(coalesce(nullif(b.country_name, ''), c.country_name), ' (', upper(b.booking_entity_id), CASE WHEN b.is_hub THEN ' hub' ELSE '' END, ')')
       WHEN upper(b.booking_entity_id) = 'JP' THEN 'Japan head office (JP provider region)'
       ELSE concat(upper(b.booking_entity_id), ' provider region') END       AS booking_entity_name,
  c.country_code,
  coalesce(nullif(b.country_name, ''), c.country_name)                         AS country_name,
  coalesce(b.city, c.booking_city)                                             AS city,
  b.region_cluster,
  coalesce(b.regulator, c.regulator)                                           AS regulator,
  coalesce(b.local_currency, c.local_currency)                                 AS local_currency,
  coalesce(b.is_hub, false)                                                    AS is_hub,
  coalesce(b.is_apac, false)                                                   AS is_apac,
  NOT coalesce(b.is_apac, false)                                               AS is_provider_region
FROM ${catalog}.silver.ref_booking_entity b
LEFT JOIN ${catalog}.gold.dim_country c ON c.country_code = upper(b.booking_entity_id)
