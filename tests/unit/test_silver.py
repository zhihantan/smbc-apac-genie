"""Silver standardisation helpers (pure; Phase 4b): spec parsing, typed projection, conformed keys,
de-duplication order and the repo's config/table_specs.yaml + column dictionary."""
import re

import pytest

from smbc_genie_lib import silver

MINI = {
    "conform": {
        "xref": "silver.xref_client_source", "golden": "silver.client_golden_identity",
        "group_master": "shared.share_jp_group_master.global_group_id",
        "identity_keys": {"cust_no": "core_customer", "obligor_id": "credit_obligor"},
        "lookups": {"facility": {"table": "bronze.credit_facility_terms", "key": "facility_id",
                                 "identity": "obligor_id", "system": "credit_obligor"}},
        "reprocessed_batch_pattern": "_R$",
    },
    "tables": {
        "pay": {"source": "bronze.pay", "domain": "payments", "comment": "Payments.", "keys": ["payment_id"],
                "client": {"key": "cust_no"}, "date": "payment_date",
                "columns": {"payment_id": "string", "cust_no": "string", "payment_date": "date",
                            "currency": "code", "amount_usd": "double", "stp_flag": "boolean",
                            "_batch_id": "string", "_ingest_ts": "timestamp"}},
        "coll": {"source": "bronze.coll", "domain": "credit", "comment": "Collateral.", "keys": ["collateral_id"],
                 "client": {"key": "facility_id", "via": "facility"},
                 "columns": {"collateral_id": "string", "facility_id": "string", "ltv_pct": "double"}},
        "grp": {"source": "shared.grp", "domain": "shared", "comment": "Group rows.",
                "keys": ["global_group_id", "month_end_date"], "group": {"key": "global_group_id"},
                "columns": {"global_group_id": "string", "month_end_date": "date"}},
    },
}
PAY_SCHEMA = {"payment_id": "string", "cust_no": "string", "payment_date": "string", "currency": "string",
              "amount_usd": "double", "stp_flag": "string", "_batch_id": "string", "_ingest_ts": "string",
              "extra_note": "string"}


@pytest.fixture(scope="module")
def mini():
    return silver.parse_specs(MINI)


def test_cast_expressions():
    assert silver.cast_expr("d", "date", "string") == "try_cast(NULLIF(trim(`d`), '') AS DATE)"
    assert silver.cast_expr("c", "code", "string") == "upper(NULLIF(trim(`c`), ''))"
    assert silver.cast_expr("s", "string", "string") == "NULLIF(trim(`s`), '')"
    assert silver.cast_expr("d", "date", "date") == "`d`"            # already typed: untouched
    assert silver.cast_expr("n", "int", "bigint") == "try_cast(`n` AS INT)"
    assert silver.cast_expr("m", "decimal(30,2)", "decimal(30,2)") == "`m`"
    b = silver.cast_expr("f", "boolean", "string")
    assert "'true'" in b and "'0'" in b and b.endswith("END")


def test_conformance_only_for_parsed_strings():
    assert silver.needs_conformance("date", "string") and silver.needs_conformance("double", "string")
    assert not silver.needs_conformance("string", "string") and not silver.needs_conformance("code", "string")
    assert not silver.needs_conformance("date", "date")


def test_infer_type_for_undeclared_columns():
    assert silver.infer_type("payment_date", "string") == "date"
    assert silver.infer_type("_ingest_ts", "string") == "timestamp"
    assert silver.infer_type("checked_at", "string") == "timestamp"
    assert silver.infer_type("valid_from", "string") == "date"
    assert silver.infer_type("amount", "double") == "double"
    assert silver.infer_type("note", "string") == "string"


def test_sql_type_rejects_unknown():
    with pytest.raises(ValueError):
        silver.sql_type("varchar")
    assert silver.sql_type("decimal(30,2)") == "DECIMAL(30,2)"


def test_parse_specs_defaults(mini):
    pay = mini.tables["pay"]
    assert pay.client == {"key": "cust_no", "system": "core_customer", "required": True}
    assert pay.target == "silver.pay" and pay.quarantine == "silver.quarantine_pay" and pay.client_grain
    assert mini.tables["grp"].group == {"key": "global_group_id"} and not mini.tables["grp"].client_grain


def test_validate_spec_errors():
    bad = {**MINI, "tables": {"t": {"source": "bronze.t", "domain": "x", "comment": "c", "keys": ["nope"],
                                    "columns": {"a": "string"}}}}
    with pytest.raises(ValueError, match="key columns"):
        silver.parse_specs(bad)
    bad2 = {**MINI, "tables": {"t": {"source": "bronze.t", "domain": "x", "comment": "c", "keys": ["a"],
                                     "client": {"key": "a", "via": "missing"}, "columns": {"a": "string"}}}}
    with pytest.raises(ValueError, match="unknown lookup"):
        silver.parse_specs(bad2)
    bad3 = {**MINI, "tables": {"t": {"source": "bronze.t", "domain": "x", "comment": "c", "keys": ["a"],
                                     "client": {"key": "a"}, "columns": {"a": "string"}}}}
    with pytest.raises(ValueError, match="no identity system"):
        silver.parse_specs(bad3)


def test_resolve_columns_reports_drift(mini):
    cols, drift = silver.resolve_columns(mini.tables["pay"], PAY_SCHEMA)
    assert list(cols)[:3] == ["payment_id", "cust_no", "payment_date"]
    assert cols["extra_note"] == "string" and drift == {"missing": [], "undeclared": ["extra_note"]}
    with pytest.raises(ValueError, match="missing"):
        silver.resolve_columns(mini.tables["pay"], {k: v for k, v in PAY_SCHEMA.items() if k != "currency"})


def test_staged_sql_shape(mini):
    sql, cols, _ = silver.staged_sql(mini.tables["pay"], PAY_SCHEMA, "cat", mini.conform,
                                     [("pay:golden_key", "golden_client_id IS NOT NULL", (), ())])
    assert "FROM cat.bronze.pay" in sql
    assert "__x.source_system = 'core_customer' AND __x.source_id = CAST(t.`cust_no` AS STRING)" in sql
    assert "FROM cat.silver.xref_client_source GROUP BY source_system, source_id" in sql
    # raw value kept for the type-conformance check of parsed columns only
    assert "AS `_raw_payment_date`" in sql and "AS `_raw_stp_flag`" in sql and "_raw_currency" not in sql
    # replayed batches rank after originals, then earliest ingest
    order = re.search(r"ORDER BY (.*?)\) END AS _dedup_rank", sql).group(1)
    assert order.startswith("CASE WHEN `_batch_id` RLIKE '_R$' THEN 1 ELSE 0 END, _ingest_ts ASC, _batch_id ASC")
    assert "WHEN NOT coalesce(golden_client_id IS NOT NULL, true) THEN 'pay:golden_key'" in sql
    assert silver.silver_select(cols, mini.tables["pay"], mini.conform) == [
        "payment_id", "cust_no", "payment_date", "currency", "amount_usd", "stp_flag", "extra_note",
        "golden_client_id", "client_group_id", "_batch_id", "_ingest_ts"]


def test_lookup_and_group_joins(mini):
    schema = {"collateral_id": "string", "facility_id": "string", "ltv_pct": "double"}
    sql, _, _ = silver.staged_sql(mini.tables["coll"], schema, "cat", mini.conform)
    assert "FROM cat.bronze.credit_facility_terms GROUP BY" in sql
    assert "__x.source_system = 'credit_obligor' AND __x.source_id = __lk.__id" in sql
    gsql, gcols, _ = silver.staged_sql(mini.tables["grp"], {"global_group_id": "string", "month_end_date": "date"},
                                       "cat", mini.conform)
    assert "SELECT DISTINCT `global_group_id` AS client_group_id FROM cat.shared.share_jp_group_master" in gsql
    assert silver.silver_select(gcols, mini.tables["grp"], mini.conform) == [
        "global_group_id", "month_end_date", "client_group_id"]


def test_dedup_order_without_metadata(mini):
    assert silver.dedup_order(["a", "b"], mini.conform, ["a"]) == "xxhash64(`b`)"
    assert silver.dedup_order(["a"], mini.conform, ["a"]) == "1"


def test_reason_map_escapes_quotes():
    assert silver.reason_map_sql({"r1": "can't resolve"}) == "map('r1', 'can''t resolve')"
    assert silver.reason_map_sql({}) == "map()"


def test_via_lookup_adds_identity_column(mini):
    coll = mini.tables["coll"]
    assert silver.derived_key(coll, mini.conform) == "obligor_id"
    sql, cols, _ = silver.staged_sql(coll, {"collateral_id": "string", "facility_id": "string", "ltv_pct": "double"},
                                     "cat", mini.conform)
    assert "__lk.__id AS `obligor_id`" in sql
    assert silver.silver_select(cols, coll, mini.conform) == [
        "collateral_id", "facility_id", "ltv_pct", "obligor_id", "golden_client_id", "client_group_id"]


def test_client_scope_and_golden_pass():
    data = {**MINI, "tables": {
        "scr": {"source": "bronze.scr", "domain": "kyc", "comment": "c", "keys": ["id"],
                "client": {"key": "subject_ref", "system": "kyc_customer", "required": False,
                           "scope": "subject_ref LIKE 'KYC-%'"},
                "columns": {"id": "string", "subject_ref": "string"}},
        "req": {"source": "bronze.req", "domain": "x", "comment": "c", "keys": ["id"], "client": {"key": "cust_no"},
                "columns": {"id": "string", "cust_no": "string"}}}}
    sp = silver.parse_specs(data)
    scr, req = sp.tables["scr"], sp.tables["req"]
    assert silver.client_scope(scr, sp.conform) == "`subject_ref` IS NOT NULL AND (subject_ref LIKE 'KYC-%')"
    assert silver.golden_pass(scr, sp.conform).startswith("(NOT (`subject_ref` IS NOT NULL")
    assert silver.client_scope(req, sp.conform) is None
    assert silver.golden_pass(req, sp.conform) == "golden_client_id IS NOT NULL"
    sql, _, _ = silver.staged_sql(scr, {"id": "string", "subject_ref": "string"}, "cat", sp.conform)
    assert "AND (subject_ref LIKE 'KYC-%')" in sql   # out-of-scope rows never pick up a golden id


def test_system_column_resolution():
    data = {**MINI, "tables": {"rev": {"source": "bronze.rev", "domain": "fin", "comment": "c",
                                       "keys": ["client_source_system", "client_source_id"],
                                       "client": {"key": "client_source_id", "system_column": "client_source_system"},
                                       "columns": {"client_source_system": "string", "client_source_id": "string"}}}}
    sp = silver.parse_specs(data)
    sql, _, _ = silver.staged_sql(sp.tables["rev"], {"client_source_system": "string", "client_source_id": "string"},
                                  "cat", sp.conform)
    assert "__x.source_system = t.`client_source_system` AND __x.source_id = CAST(t.`client_source_id` AS STRING)" in sql


def test_blocking_fk_joins_reach_the_staging_sql(mini):
    fk = ("pay:parent_exists", "(`account_id` IS NULL OR __fk_account_id IS NOT NULL)",
          ("LEFT JOIN (SELECT DISTINCT CAST(`account_id` AS STRING) AS __v FROM {catalog}.silver.core_account) "
           "__fk_account_id_j ON __fk_account_id_j.__v = CAST(t.`account_id` AS STRING)",),
          ("__fk_account_id_j.__v AS __fk_account_id",))
    schema = {**PAY_SCHEMA, "account_id": "string"}
    spec = silver.parse_specs({**MINI, "tables": {"pay": {**MINI["tables"]["pay"], "columns": {
        **MINI["tables"]["pay"]["columns"], "account_id": "string"}}}}).tables["pay"]
    sql, _, _ = silver.staged_sql(spec, schema, "cat", mini.conform, [fk])
    assert "FROM cat.silver.core_account" in sql and "__fk_account_id_j.__v AS __fk_account_id" in sql
    assert "THEN 'pay:parent_exists' END" in sql


def test_table_tags():
    tags = {"fixed": {"smbc_layer": "silver", "smbc_pii": "false"},
            "quadrant_by_prefix": {"crm_": "internal_quantitative", "ext_": "external_quantitative"},
            "quadrant_overrides": {"crm_activity": "internal_qualitative"},
            "source_region_by_prefix": {"share_jp_": "JP"}, "source_region_default": "APAC"}
    assert silver.table_tags("crm_activity", tags) == {"smbc_layer": "silver", "smbc_pii": "false",
                                                       "smbc_quadrant": "internal_qualitative",
                                                       "smbc_source_region": "APAC"}
    assert silver.table_tags("share_jp_parent_rating", tags)["smbc_source_region"] == "JP"
    assert "smbc_quadrant" not in silver.table_tags("share_jp_parent_rating", tags)


def test_load_order_puts_parents_first():
    data = {**MINI, "tables": {
        "child": {"source": "bronze.child", "domain": "x", "comment": "c", "keys": ["id"],
                  "columns": {"id": "string", "p": "string"},
                  "checks": {"fk": {"p": "silver.parent.id"}, "fk_blocking": ["p"]}},
        "parent": {"source": "bronze.parent", "domain": "x", "comment": "c", "keys": ["id"], "columns": {"id": "string"}}}}
    assert silver.load_order(silver.parse_specs(data)) == [["parent"], ["child"]]
    soft = {**MINI, "tables": {**data["tables"], "child": {**data["tables"]["child"],
                                                           "checks": {"fk": {"p": "silver.parent.id"}}}}}
    assert silver.load_order(silver.parse_specs(soft)) == [["child", "parent"]]   # soft references don't order
    cyc = {**MINI, "tables": {"a": {**data["tables"]["child"], "checks": {"fk": {"p": "silver.a2.id"}, "fk_blocking": ["p"]}},
                              "a2": {**data["tables"]["child"], "checks": {"fk": {"p": "silver.a.id"}, "fk_blocking": ["p"]}}}}
    with pytest.raises(ValueError, match="cyclic"):
        silver.load_order(silver.parse_specs(cyc))


# ---- the repo's configs ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def repo_specs():
    return silver.load_specs()


def test_repo_specs_cover_every_landing_table(repo_specs):
    import yaml
    assert len(repo_specs.tables) == 85
    assert sum(t.source.startswith("shared.") for t in repo_specs.tables.values()) == 11
    identity = {"core_customer", "crm_account", "kyc_customer", "credit_obligor", "trade_party", "tsy_counterparty",
                "ext_company_master"}
    assert not identity & set(repo_specs.tables)     # identity masters are resolved by ER, not copied
    for t in repo_specs.tables.values():
        assert t.comment and t.keys and t.domain
        assert t.client or t.group or t.domain in ("reference", "governance", "external", "cashflow"), t.name
    order = [n for level in silver.load_order(repo_specs) for n in level]
    assert order.index("core_account") < order.index("pay_payment_message") < order.index("pay_repair_queue")
    assert order.index("ref_currency") < order.index("core_account")
    assert yaml.safe_load(open(silver.default_specs_path()))["conform"]["reprocessed_batch_pattern"] == "_R$"


def test_repo_specs_conform_keys(repo_specs):
    t = repo_specs.tables
    assert t["pay_payment_message"].client == {"key": "cust_no", "system": "core_customer", "required": True}
    assert t["credit_collateral"].client["via"] == "facility"
    assert t["fin_client_revenue"].client["system_column"] == "client_source_system"
    assert t["share_jp_parent_rating"].group == {"key": "global_group_id"}
    assert t["share_jp_support_letters"].client["system"] == "credit_obligor"
    assert t["core_deposit_balance_monthly"].checks["not_future"] == ["balance_date"]


def test_column_dictionary_covers_conformed_and_metadata_columns():
    import yaml
    d = yaml.safe_load(open(silver.default_specs_path().parent / "column_dictionary.yaml"))
    for c in ("golden_client_id", "client_group_id", "_ingest_ts", "_batch_id", "_source_system", "_dq_rule_id",
              "_dq_reason", "_shared_at", "_provider_version"):
        assert d["columns"][c]
    for t in ("dq_results", "dq_score_history", "dq_table_reconciliation", "golden_record_coverage_monthly",
              "golden_attribute_completeness"):
        assert d["table_comments"][t]
    assert all(len(v.split()) <= 40 for cols in d["tables"].values() for v in cols.values())


def test_coverage_and_attribute_sql(repo_specs):
    from smbc_genie_lib.er import records
    fields = {s: list(c) for s, c in records.SOURCE_COLUMNS.items()}
    cov = silver.coverage_sql("cat", repo_specs, records.SOURCE_PRIORITY, fields, "2023-04-01", "2026-09-01")
    assert "sequence(DATE'2023-04-01', DATE'2026-09-01', INTERVAL 1 MONTH)" in cov
    assert "r.avail <= last_day(m.month)" in cov and "in_all_core_sources" in cov
    assert "IN ('core_customer', 'crm_account', 'kyc_customer')" in cov
    # trade_party carries name, country, LEI only: completeness is over those three fields
    assert "WHEN 'trade_party' THEN (" in cov and cov.split("WHEN 'trade_party' THEN (")[1].split(")")[0].count("CASE") == 3
    att = silver.attribute_sql("cat", repo_specs, records.SOURCE_PRIORITY)
    for col in ("has_tier", "relationship_tier", "primary_rm_code", "kyc_risk_rating", "attribute_completeness_pct",
                "missing_attributes", "grp_tier", "f_rating_0"):
        assert col in att
    assert "FROM cat.silver.crm_opportunity WHERE golden_client_id IS NOT NULL" in att
