-- Phase 2 — ops tables (brief §3.1). Idempotent. Params: ${catalog}.
-- Operational metadata for the build: config, run log, DQ rules, ER runs, storylines, benchmarks.

CREATE TABLE IF NOT EXISTS ${catalog}.ops.build_config (
  key        STRING  COMMENT 'Config key (e.g. as_of_date, scale, random_seed, history_start)',
  value      STRING  COMMENT 'Config value as text',
  updated_at TIMESTAMP COMMENT 'When this key was last written'
) COMMENT 'Single source of build parameters read by functions, generators and tests.';

CREATE TABLE IF NOT EXISTS ${catalog}.ops.build_run_log (
  run_id       STRING  COMMENT 'Build run identifier (one per build_all invocation)',
  phase        STRING  COMMENT 'Phase code, e.g. p02_setup, p03_bronze',
  step         STRING  COMMENT 'Step within the phase',
  object_name  STRING  COMMENT 'Fully-qualified object created or validated, if any',
  row_count    BIGINT  COMMENT 'Row count written, if applicable',
  status       STRING  COMMENT 'ok | warn | error | skipped',
  message      STRING  COMMENT 'Free-text detail or error message',
  scale        DOUBLE  COMMENT 'SCALE in effect for this run',
  started_at   TIMESTAMP COMMENT 'Step start',
  finished_at  TIMESTAMP COMMENT 'Step end',
  duration_sec DOUBLE  COMMENT 'Elapsed seconds'
) COMMENT 'Append-only log of every build step with row counts and timings.';

CREATE TABLE IF NOT EXISTS ${catalog}.ops.dq_rules (
  rule_id      STRING  COMMENT 'Unique rule id',
  layer        STRING  COMMENT 'bronze | silver | gold',
  table_name   STRING  COMMENT 'Target table (schema.table)',
  column_name  STRING  COMMENT 'Target column, or * for row-level rules',
  dq_dimension STRING  COMMENT 'completeness | validity | uniqueness | timeliness | consistency',
  rule_expr    STRING  COMMENT 'SQL boolean expression that must hold (true = pass)',
  severity     STRING  COMMENT 'info | warn | error',
  threshold    DOUBLE  COMMENT 'Max allowed failure fraction (0 = none allowed)',
  description  STRING  COMMENT 'Human-readable rule description'
) COMMENT 'Catalogue of data-quality rules evaluated in silver (results -> silver.dq_results).';

CREATE TABLE IF NOT EXISTS ${catalog}.ops.entity_resolution_runs (
  run_id               STRING COMMENT 'ER run id',
  run_date             DATE   COMMENT 'As-of date the run was executed against',
  rule_version         STRING COMMENT 'Resolution rule set version (v1 | v2)',
  source_system        STRING COMMENT 'Source system evaluated, or ALL for the combined run',
  records_in           BIGINT COMMENT 'Source identity records considered',
  matched_deterministic BIGINT COMMENT 'Matched on LEI-like / tax id',
  matched_fuzzy        BIGINT COMMENT 'Matched by fuzzy score >= auto threshold',
  steward_resolved     BIGINT COMMENT 'Resolved via (simulated) steward decision',
  unmatched            BIGINT COMMENT 'Left unmatched (new golden records)',
  duplicates_merged    BIGINT COMMENT 'Within-source duplicates collapsed',
  golden_records       BIGINT COMMENT 'Distinct golden clients after the run',
  precision            DOUBLE COMMENT 'Pairwise precision vs synthetic truth',
  recall               DOUBLE COMMENT 'Pairwise recall vs synthetic truth',
  started_at           TIMESTAMP COMMENT 'Run start'
) COMMENT 'One row per ER run per source (6 quarterly runs; v1 then v2). Brief §5.11.';

CREATE TABLE IF NOT EXISTS ${catalog}.ops.storyline_assertions (
  storyline_id   INT     COMMENT 'Storyline number 1-13 (brief §6.4)',
  storyline_name STRING  COMMENT 'Short storyline name',
  assertion_key  STRING  COMMENT 'Assertion identifier within the storyline',
  description    STRING  COMMENT 'What this assertion checks',
  expected       STRING  COMMENT 'Expected value or range as text',
  actual         STRING  COMMENT 'Observed value as text',
  passed         BOOLEAN COMMENT 'Whether the assertion held',
  scale          DOUBLE  COMMENT 'SCALE in effect',
  checked_at     TIMESTAMP COMMENT 'When checked'
) COMMENT 'Results of the embedded-storyline assertions (also enforced in tests/test_storylines.py).';

CREATE TABLE IF NOT EXISTS ${catalog}.ops.genie_benchmarks (
  space_slug           STRING  COMMENT 'Genie space slug (Appendix D)',
  benchmark_id         STRING  COMMENT '32-hex benchmark id, stable across rebuilds',
  question             STRING  COMMENT 'Benchmark question text',
  expected_sql         STRING  COMMENT 'Minimal expected SQL (names output columns explicitly)',
  min_rows             INT     COMMENT 'Expected minimum row count',
  must_contain_columns STRING  COMMENT 'Comma-separated columns the result must contain',
  top_row_contains     STRING  COMMENT 'Substring/JSON the top row must contain',
  variant_of           STRING  COMMENT 'Base must-answer question id this rephrases, or NULL',
  last_sql_pass        BOOLEAN COMMENT 'Did the expected SQL last pass its checks?',
  last_eval_assessment STRING  COMMENT 'Last Genie eval assessment: GOOD | BAD | NEEDS_REVIEW',
  checked_at           TIMESTAMP COMMENT 'When last evaluated',
  -- Phase 7 additions (src/50_genie/run_benchmarks.py OPS_NEW_COLUMNS adds them to an existing table)
  benchmark_key        STRING  COMMENT 'Human key of the benchmark in genie/<slug>/space.yaml (Q1, Q1-casual, X1)',
  space_id             STRING  COMMENT 'Genie space id that was evaluated',
  genie_sql            STRING  COMMENT 'SQL Genie generated in the last evaluation (NULL when it answered without SQL)',
  last_eval_pass       BOOLEAN COMMENT 'Final verdict of the last evaluation: eval-runs GOOD, or a match under the comparison rule',
  eval_reason          STRING  COMMENT 'Why: eval assessment, comparison-rule detail or error',
  eval_method          STRING  COMMENT 'eval_runs | conversation',
  eval_run_id          STRING  COMMENT 'Genie eval-run id (eval_runs method)',
  space_pass_rate      DOUBLE  COMMENT 'Pass rate of the space in that evaluation (passes / benchmarks)'
) COMMENT 'Benchmark catalogue + latest SQL/eval results per Genie space. Brief §3.4, §8.';
