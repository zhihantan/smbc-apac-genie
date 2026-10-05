-- dim_country: silver.ref_country (booking locations + JP: name, city, regulator, currency) plus a static ISO-2
-- list covering every other code seen in the feeds and common trade partners (names / regions for those).
WITH iso (country_code, country_name, region, sub_region, is_asean) AS (
  VALUES
  ('SG', 'Singapore', 'APAC', 'Southeast Asia', true), ('HK', 'Hong Kong', 'APAC', 'Greater China', false),
  ('CN', 'China', 'APAC', 'Greater China', false), ('TW', 'Taiwan', 'APAC', 'Greater China', false),
  ('MO', 'Macau', 'APAC', 'Greater China', false), ('KR', 'South Korea', 'APAC', 'North Asia', false),
  ('MN', 'Mongolia', 'APAC', 'North Asia', false), ('JP', 'Japan', 'JP', 'North Asia', false),
  ('TH', 'Thailand', 'APAC', 'Southeast Asia', true), ('ID', 'Indonesia', 'APAC', 'Southeast Asia', true),
  ('VN', 'Vietnam', 'APAC', 'Southeast Asia', true), ('MY', 'Malaysia', 'APAC', 'Southeast Asia', true),
  ('PH', 'Philippines', 'APAC', 'Southeast Asia', true), ('KH', 'Cambodia', 'APAC', 'Southeast Asia', true),
  ('LA', 'Laos', 'APAC', 'Southeast Asia', true), ('MM', 'Myanmar', 'APAC', 'Southeast Asia', true),
  ('BN', 'Brunei', 'APAC', 'Southeast Asia', true), ('IN', 'India', 'APAC', 'South Asia', false),
  ('BD', 'Bangladesh', 'APAC', 'South Asia', false), ('LK', 'Sri Lanka', 'APAC', 'South Asia', false),
  ('PK', 'Pakistan', 'APAC', 'South Asia', false), ('NP', 'Nepal', 'APAC', 'South Asia', false),
  ('AU', 'Australia', 'APAC', 'Oceania', false), ('NZ', 'New Zealand', 'APAC', 'Oceania', false),
  ('FJ', 'Fiji', 'APAC', 'Oceania', false), ('PG', 'Papua New Guinea', 'APAC', 'Oceania', false),
  ('GB', 'United Kingdom', 'EMEA', 'Europe', false), ('IE', 'Ireland', 'EMEA', 'Europe', false),
  ('DE', 'Germany', 'EMEA', 'Europe', false), ('FR', 'France', 'EMEA', 'Europe', false),
  ('NL', 'Netherlands', 'EMEA', 'Europe', false), ('BE', 'Belgium', 'EMEA', 'Europe', false),
  ('LU', 'Luxembourg', 'EMEA', 'Europe', false), ('CH', 'Switzerland', 'EMEA', 'Europe', false),
  ('AT', 'Austria', 'EMEA', 'Europe', false), ('IT', 'Italy', 'EMEA', 'Europe', false),
  ('ES', 'Spain', 'EMEA', 'Europe', false), ('PT', 'Portugal', 'EMEA', 'Europe', false),
  ('GR', 'Greece', 'EMEA', 'Europe', false), ('SE', 'Sweden', 'EMEA', 'Europe', false),
  ('NO', 'Norway', 'EMEA', 'Europe', false), ('DK', 'Denmark', 'EMEA', 'Europe', false),
  ('FI', 'Finland', 'EMEA', 'Europe', false), ('PL', 'Poland', 'EMEA', 'Europe', false),
  ('CZ', 'Czechia', 'EMEA', 'Europe', false), ('HU', 'Hungary', 'EMEA', 'Europe', false),
  ('RO', 'Romania', 'EMEA', 'Europe', false), ('TR', 'Turkey', 'EMEA', 'Europe', false),
  ('AE', 'United Arab Emirates', 'EMEA', 'Middle East', false), ('SA', 'Saudi Arabia', 'EMEA', 'Middle East', false),
  ('QA', 'Qatar', 'EMEA', 'Middle East', false), ('KW', 'Kuwait', 'EMEA', 'Middle East', false),
  ('BH', 'Bahrain', 'EMEA', 'Middle East', false), ('OM', 'Oman', 'EMEA', 'Middle East', false),
  ('IL', 'Israel', 'EMEA', 'Middle East', false), ('EG', 'Egypt', 'EMEA', 'Africa', false),
  ('ZA', 'South Africa', 'EMEA', 'Africa', false), ('NG', 'Nigeria', 'EMEA', 'Africa', false),
  ('KE', 'Kenya', 'EMEA', 'Africa', false), ('MA', 'Morocco', 'EMEA', 'Africa', false),
  ('US', 'United States', 'AMER', 'North America', false), ('CA', 'Canada', 'AMER', 'North America', false),
  ('MX', 'Mexico', 'AMER', 'Latin America', false), ('BR', 'Brazil', 'AMER', 'Latin America', false),
  ('CL', 'Chile', 'AMER', 'Latin America', false), ('PA', 'Panama', 'AMER', 'Latin America', false),
  ('AR', 'Argentina', 'AMER', 'Latin America', false), ('CO', 'Colombia', 'AMER', 'Latin America', false),
  ('PE', 'Peru', 'AMER', 'Latin America', false)
), ref AS (
  SELECT upper(country_code) AS country_code, nullif(country_name, '') AS country_name, city, regulator,
         local_currency, is_apac, region_cluster
  FROM ${catalog}.silver.ref_country
), be AS (
  SELECT upper(booking_entity_id) AS code, is_apac, is_hub FROM ${catalog}.silver.ref_booking_entity
), codes AS (
  SELECT country_code FROM iso UNION SELECT country_code FROM ref
)
SELECT
  c.country_code,
  coalesce(r.country_name, i.country_name, c.country_code)             AS country_name,
  coalesce(i.region, CASE WHEN r.region_cluster IN ('APAC', 'JP', 'EMEA', 'AMER') THEN r.region_cluster END,
           'Other')                                                    AS region,
  coalesce(i.sub_region, 'Other')                                      AS sub_region,
  coalesce(i.region, r.region_cluster) = 'APAC'                        AS is_apac,
  coalesce(i.is_asean, false)                                          AS is_asean,
  coalesce(b.is_apac, false)                                           AS is_booking_location,
  coalesce(b.is_hub, false)                                            AS is_hub,
  r.city                                                               AS booking_city,
  r.regulator,
  r.local_currency
FROM codes c
LEFT JOIN iso i ON i.country_code = c.country_code
LEFT JOIN ref r ON r.country_code = c.country_code
LEFT JOIN be b ON b.code = c.country_code
