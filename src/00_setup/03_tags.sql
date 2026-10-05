-- Phase 2 — UC tags (DECISIONS D37). Pre-check for governed-tag clashes first; fall back to smbc_ prefix.
-- ALTER SCHEMA ${catalog}.gold    SET TAGS ('domain'='customer360','layer'='gold','synthetic'='true','pii'='false');
-- ALTER SCHEMA ${catalog}.metrics SET TAGS ('domain'='customer360','layer'='metrics','synthetic'='true','pii'='false');
SELECT 'tags: implemented in phase 2' AS status;
