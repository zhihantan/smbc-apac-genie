-- Phase 2 — catalog + medallion schemas. Idempotent. Params: ${catalog}, ${storage_root}.
-- Never DROP; teardown is a separate confirmed script (scripts/teardown.py, docs/TEARDOWN.md).
CREATE CATALOG IF NOT EXISTS ${catalog}
  COMMENT 'SMBC APAC Genie — synthetic Customer 360 (no real clients, people or figures).';

CREATE SCHEMA IF NOT EXISTS ${catalog}.bronze  COMMENT 'Raw landings per APAC source system; deliberate DQ noise and identity fragmentation.';
CREATE SCHEMA IF NOT EXISTS ${catalog}.shared  COMMENT 'Tables as they arrive via Delta Sharing from JP / EMEA / AMER (simulated locally).';
CREATE SCHEMA IF NOT EXISTS ${catalog}.silver  COMMENT 'Cleansed, conformed, deduplicated; entity resolution and SCD2 golden client.';
CREATE SCHEMA IF NOT EXISTS ${catalog}.gold    COMMENT 'Customer 360 star schema: golden client hub + fact_* + dim_*.';
CREATE SCHEMA IF NOT EXISTS ${catalog}.metrics COMMENT 'Unity Catalog metric views (mv_*) — the primary Genie answer surface.';
CREATE SCHEMA IF NOT EXISTS ${catalog}.ops     COMMENT 'Build run log, DQ rules, ER runs, storyline assertions, Genie benchmarks.';
-- TODO(phase2): ALTER CATALOG OWNER TO owner_group when an account group is confirmed.
