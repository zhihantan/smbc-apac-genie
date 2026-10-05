-- Phase 2 — grants. Consumer group gets gold + metrics only (brief §3.1, DECISIONS D34).
-- Schema-level SELECT is inherited by views and metric views, surviving CREATE OR REPLACE.
-- TODO(phase2): resolve ${consumer_group}; skip if it is not an account-level group.
-- GRANT USE CATALOG ON CATALOG ${catalog} TO `${consumer_group}`;
-- GRANT USE SCHEMA, SELECT, EXECUTE ON SCHEMA ${catalog}.gold    TO `${consumer_group}`;
-- GRANT USE SCHEMA, SELECT, EXECUTE ON SCHEMA ${catalog}.metrics TO `${consumer_group}`;
SELECT 'grants: implemented in phase 2' AS status;
