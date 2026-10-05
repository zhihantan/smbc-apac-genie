"""Gold framework tests (pure Python; smbc_genie_lib.gold, src/30_gold specs + SQL; D11, D25, D38)."""
import datetime as _dt
import re
from pathlib import Path

import pytest
import yaml

from smbc_genie_lib import gold
from smbc_genie_lib.config import load_config

D = _dt.date
HUB = ["dim_date", "dim_client", "dim_client_group", "xref_client_source", "dim_contact", "dim_booking_entity",
       "dim_product", "dim_employee", "dim_industry", "dim_signal_type", "dim_ews_trigger", "dim_onboarding_stage",
       "dim_currency", "fx_rate_daily", "dim_country", "dim_peer_group", "dim_account", "dim_competitor_bank",
       "dim_threshold", "dim_scf_programme", "dim_account_plan_initiative", "fn_usd"]
CLIENT_BLOCK = ["client_group_id", "group_name", "display_name", "short_name", "segment", "relationship_tier",
                "is_japanese_corporate", "industry_sector", "industry_subsector", "coverage_office", "primary_rm_id",
                "primary_rm_name", "internal_rating_grade", "ifrs9_stage", "watchlist_flag", "kyc_risk_rating", "ews_band"]
# ops tables gold may read: operational run logs / catalogues (ER run quality, DQ rules), not synthetic truth
OPS_RUN_LOGS = {("ops", "entity_resolution_runs"), ("ops", "dq_rules")}


def _spec(**over):
    base = {"table": "fact_x", "domain": "test", "comment": "A test fact.", "grain": "one row per thing",
            "primary_key": ["id"], "cluster_by": ["d"],
            "columns": {"id": {"type": "STRING", "comment": "Row id."}, "d": "DATE",
                        "amount_usd": {"type": "DOUBLE", "comment": "It's in USD."},
                        "golden_client_sk": "BIGINT"}}
    base.update(over)
    return gold.parse_spec(base)


# Every valid spec in src/30_gold (hub + the domain facts other packages add); load errors are collected so
# one half-written spec fails only its own test below, never the hub tests.
_SPEC_ERRORS: list = []
_ALL = gold.load_specs(errors=_SPEC_ERRORS)
_DICTS = gold.load_dictionaries()


@pytest.fixture(scope="module")
def specs():
    return _ALL


@pytest.fixture(scope="module")
def dicts():
    return _DICTS


# ---- spec parsing ---------------------------------------------------------------------------------
def test_parse_spec_basics():
    s = _spec()
    assert s.column_names == ["id", "d", "amount_usd", "golden_client_sk"]
    assert s.column("id").not_null and not s.column("d").not_null      # PK columns become NOT NULL
    assert s.full_comment == "A test fact. Grain: one row per thing."
    assert s.column("d").type == "DATE"


@pytest.mark.parametrize("over, msg", [
    ({"primary_key": ["nope"]}, "primary key"),
    ({"primary_key": []}, "primary_key"),
    ({"columns": {"id": "VARCHAR"}}, "unsupported type"),
    ({"cluster_by": ["id", "d", "amount_usd", "golden_client_sk", "id"]}, "cluster_by"),
    ({"foreign_keys": [{"columns": ["nope"], "references": "dim_client"}]}, "FK columns"),
    ({"bogus_key": 1}, "unknown spec keys"),
    ({"checks": [{"name": "x"}]}, "check needs"),
    ({"domain": ""}, "domain"),
])
def test_parse_spec_rejects(over, msg):
    with pytest.raises(gold.SpecError, match=msg):
        _spec(**over)


def test_unquoted_comma_in_flow_mapping_is_caught():
    data = yaml.safe_load("table: t\ndomain: x\ncomment: c\nprimary_key: [a]\n"
                          "columns:\n  a: {type: STRING, comment: True for Singapore, the hub.}\n")
    with pytest.raises(gold.SpecError, match="quote comments"):
        gold.parse_spec(data)


def test_decimal_and_array_types_normalised():
    s = _spec(columns={"id": "string", "amt": "decimal(18, 2)", "tags": "array<string>"}, cluster_by=[])
    assert [c.type for c in s.columns] == ["STRING", "DECIMAL(18,2)", "ARRAY<STRING>"]


def test_fk_must_target_primary_key():
    dim = gold.parse_spec({"table": "dim_a", "domain": "x", "comment": "c", "primary_key": ["k"],
                           "columns": {"k": "STRING", "other": "STRING"}})
    fact = _spec(columns={"id": "STRING", "d": "DATE", "other": "STRING"},
                 foreign_keys=[{"columns": ["other"], "references": "dim_a(other)"}])
    with pytest.raises(gold.SpecError, match="primary key"):
        gold.validate_specs({"dim_a": dim, "fact_x": fact})
    ok = _spec(columns={"id": "STRING", "d": "DATE", "k": "STRING"},
               foreign_keys=[{"columns": ["k"], "references": "dim_a"}])
    gold.validate_specs({"dim_a": dim, "fact_x": ok})
    assert gold.fk_ref_columns(ok.foreign_keys[0], {"dim_a": dim}) == ["k"]


# ---- comments (D25) -------------------------------------------------------------------------------
def test_comment_resolution_priority():
    s = _spec(columns={"id": {"type": "STRING", "comment": "Spec wins."}, "d": "DATE", "golden_client_sk": "BIGINT",
                       "mystery": "STRING"})
    gold_d, cfg_d = {"id": "gold dict", "d": "Gold dict date."}, {"d": "cfg", "golden_client_sk": "Config dict sk."}
    missing = gold.resolve_comments(s, [gold_d, cfg_d])
    assert missing == ["mystery"]
    assert [s.column(c).comment for c in ("id", "d", "golden_client_sk")] == ["Spec wins.", "Gold dict date.", "Config dict sk."]


def test_parse_dictionary_shapes():
    assert gold.parse_dictionary({"columns": {"a": "x", "b": {"comment": "y"}}}) == {"a": "x", "b": "y"}
    assert gold.parse_dictionary({"a": {"description": " z "}, "version": 1, "tables": {}}) == {"a": "z"}
    assert gold.parse_dictionary(None) == {}


# ---- rendering ------------------------------------------------------------------------------------
def test_render_create_table():
    dim = gold.parse_spec({"table": "dim_client", "domain": "hub", "comment": "c", "primary_key": ["golden_client_sk"],
                           "columns": {"golden_client_sk": {"type": "BIGINT", "comment": "sk"}}})
    s = _spec(foreign_keys=[{"columns": ["golden_client_sk"], "references": "dim_client"}])
    gold.resolve_comments(s, [{"d": "Date.", "golden_client_sk": "SK."}])
    ddl = gold.render_create(s, "cat", {"fact_x": s, "dim_client": dim})
    assert ddl.startswith("CREATE OR REPLACE TABLE cat.gold.fact_x (")
    assert "`id` STRING NOT NULL COMMENT 'Row id.'" in ddl
    assert "COMMENT 'It\\'s in USD.'" in ddl                         # backslash escape, never ''
    assert "CONSTRAINT pk_fact_x PRIMARY KEY (`id`) RELY" in ddl
    assert ("CONSTRAINT fk_fact_x_golden_client_sk FOREIGN KEY (`golden_client_sk`) REFERENCES "
            "cat.gold.dim_client (`golden_client_sk`) NOT ENFORCED RELY") in ddl
    assert "CLUSTER BY (`d`)" in ddl and ddl.endswith("COMMENT 'A test fact. Grain: one row per thing.'")
    skipped = gold.render_create(s, "cat", {"fact_x": s, "dim_client": dim}, skip_fks=["fk_fact_x_golden_client_sk"])
    assert "FOREIGN KEY" not in skipped
    assert gold.add_fk_sql(s, s.foreign_keys[0], "cat", {"fact_x": s, "dim_client": dim}).startswith(
        "ALTER TABLE cat.gold.fact_x ADD CONSTRAINT fk_fact_x_golden_client_sk FOREIGN KEY")


def test_render_insert_selects_by_name():
    s = _spec()
    s.body = "SELECT 1 AS golden_client_sk, 'a' AS id, DATE'2026-09-30' AS d, 2.0 AS amount_usd -- trailing comment"
    ins = gold.render_insert(s, "cat")
    assert ins.splitlines()[1] == "SELECT `id`, `d`, `amount_usd`, `golden_client_sk` FROM ("
    assert ins.splitlines()[-1] == ") AS __src"                          # a trailing -- comment cannot eat it


def test_sql_str_escapes():
    assert gold.sql_str("it's a \\ test") == "'it\\'s a \\\\ test'"


def test_render_placeholders():
    assert gold.render("SELECT ${a}, '${b}'", {"a": 1, "b": "x"}) == "SELECT 1, 'x'"
    with pytest.raises(gold.SpecError, match="unknown placeholder"):
        gold.render("SELECT ${nope}", {})


def test_lint_body():
    with pytest.raises(gold.SpecError, match="doubled quote"):
        gold.lint_body("SELECT 'it''s'")
    with pytest.raises(gold.SpecError, match="CURRENT_DATE"):
        gold.lint_body("SELECT current_date() AS d")
    gold.lint_body("SELECT '' AS empty -- never use CURRENT_DATE() here")    # empty literal + comment are fine


def test_clean_body_single_statement():
    assert gold.clean_body("SELECT 1;\n") == "SELECT 1"
    with pytest.raises(gold.SpecError, match="exactly one statement"):
        gold.clean_body("SELECT 1; SELECT 2;")


# ---- dependencies ---------------------------------------------------------------------------------
def test_sql_refs_separate_tables_and_functions():
    body = ("SELECT ${catalog}.gold.fn_usd(x, 'SGD', d) FROM ${catalog}.silver.core_account a "
            "JOIN ${catalog}.gold.dim_client c ON 1=1")
    assert gold.sql_refs(body) == {("silver", "core_account"), ("gold", "dim_client")}
    assert gold.sql_fn_refs(body) == {("gold", "fn_usd")}


def _mini(name, body="SELECT 1", fks=(), deps=()):
    s = gold.parse_spec({"table": name, "domain": "t", "comment": "c", "primary_key": ["k"],
                         "columns": {"k": "STRING", "r": "STRING"}, "depends_on": list(deps),
                         "foreign_keys": [{"columns": ["r"], "references": f} for f in fks]})
    s.body = body
    return s


def test_build_order_and_levels():
    specs = {"dim_a": _mini("dim_a"),
             "dim_b": _mini("dim_b", "SELECT * FROM ${catalog}.gold.dim_a"),
             "fact_c": _mini("fact_c", "SELECT ${catalog}.gold.fn_x(1) FROM ${catalog}.silver.s", fks=["dim_b"]),
             "fn_x": _mini("fn_x", "SELECT * FROM ${catalog}.gold.dim_a")}
    order = gold.build_order(specs)
    assert order.index("dim_a") < order.index("dim_b") < order.index("fact_c")
    assert order.index("fn_x") < order.index("fact_c")
    assert gold.levels(specs, order) == [["dim_a"], ["dim_b", "fn_x"], ["fact_c"]]
    assert gold.select(specs, only=["fact_c"], with_deps=True) == ["dim_a", "fn_x", "fact_c"]   # data deps, not FKs
    assert gold.select(specs, only=["dim_b"]) == ["dim_b"]


def test_fk_cycle_falls_back_to_data_dependencies():
    specs = {"dim_client": _mini("dim_client", fks=["dim_client_group"]),
             "dim_client_group": _mini("dim_client_group", "SELECT * FROM ${catalog}.gold.dim_client", fks=["dim_client"])}
    assert gold.build_order(specs) == ["dim_client", "dim_client_group"]
    specs["dim_client"].body = "SELECT * FROM ${catalog}.gold.dim_client_group"
    with pytest.raises(gold.SpecError, match="cycle"):
        gold.build_order(specs)


# ---- verification SQL -----------------------------------------------------------------------------
def test_verification_sql_shapes():
    s = _spec(primary_key=["id", "d"])
    pk = gold.pk_check_sql(s, "cat")
    assert "GROUP BY `id`, `d` HAVING count(*) > 1" in pk and "`id` IS NULL OR `d` IS NULL" in pk
    dim = _mini("dim_client")
    fk = gold.ForeignKey(columns=["golden_client_sk"], ref_table="dim_client")
    sql = gold.fk_check_sql(s, fk, "cat", {"dim_client": dim})
    assert "LEFT JOIN (SELECT DISTINCT `k` FROM cat.gold.dim_client) r ON r.`k` = f.`golden_client_sk`" in sql
    assert "n_orphans" in sql
    gate = gold.comment_gate_sql("cat", ["dim_a"])
    assert "information_schema.columns" in gate and "'dim_a'" in gate


def test_scd2_checks_cover_integrity_rules(specs):
    names = [c["name"] for c in gold.scd2_checks(specs["dim_client"])]
    assert names == ["scd2_no_overlap", "scd2_no_gap", "scd2_from_le_to", "scd2_version_sequence",
                     "scd2_one_current_per_key", "scd2_current_is_open_last"]
    assert all("${table}" in c["sql"] for c in gold.scd2_checks(specs["dim_client"]))
    assert gold.scd2_checks(specs["dim_date"]) == []


def test_client_sk_join_snippet():
    j = gold.client_sk_join("f.balance_date")
    assert "LEFT JOIN ${catalog}.gold.dim_client dc" in j
    assert "dc.golden_client_id = f.golden_client_id" in j
    assert "f.balance_date <= dc.valid_to" in j
    assert "(f.balance_date >= dc.valid_from OR dc.version_no = 1)" in j
    g = gold.group_lead_sk_join("f.month")
    assert "gold.dim_client_group dg ON dg.client_group_id = f.client_group_id" in g
    assert "dl.golden_client_id = dg.lead_golden_client_id" in g


# ---- SCD2 reference collapse ------------------------------------------------------------------------
def _months(start, n):
    out, d = [], start
    for _ in range(n):
        out.append(d)
        d = gold.month_end(d) + _dt.timedelta(days=1)
    return out


def test_month_helpers():
    assert gold.month_end(D(2024, 2, 10)) == D(2024, 2, 29)
    assert gold.month_end(D(2026, 12, 5)) == D(2026, 12, 31)
    assert gold.month_range(D(2026, 7, 15), D(2026, 9, 30)) == [D(2026, 7, 1), D(2026, 8, 1), D(2026, 9, 1)]


def test_collapse_versions_contiguous_one_current():
    ms = _months(D(2025, 1, 1), 6)
    bands = ["Green", "Green", "Amber", "Amber", "Green", "Green"]
    states = [{"k": "GC-1", "m": m, "band": b, "rm": "RM001"} for m, b in zip(ms, bands)]
    states += [{"k": "GC-2", "m": m, "band": None, "rm": "RM002"} for m in ms[3:]]
    v = gold.collapse_versions(states, "k", "m", ["band", "rm"])
    one = [x for x in v if x["k"] == "GC-1"]
    assert [(x["valid_from"], x["valid_to"], x["band"]) for x in one] == [
        (D(2025, 1, 1), D(2025, 2, 28), "Green"), (D(2025, 3, 1), D(2025, 4, 30), "Amber"),
        (D(2025, 5, 1), D(9999, 12, 31), "Green")]                   # A -> B -> A gives three versions
    assert [x["version_no"] for x in one] == [1, 2, 3] and [x["is_current"] for x in one] == [False, False, True]
    two = [x for x in v if x["k"] == "GC-2"]
    assert len(two) == 1 and two[0]["valid_from"] == D(2025, 4, 1) and two[0]["is_current"]
    for k in ("GC-1", "GC-2"):                                       # contiguous, non-overlapping
        vs = [x for x in v if x["k"] == k]
        for a, b in zip(vs, vs[1:]):
            assert b["valid_from"] == a["valid_to"] + _dt.timedelta(days=1)


def test_collapse_versions_static_client_is_one_version():
    states = [{"k": "GC-9", "m": m, "g": 5} for m in _months(D(2023, 4, 1), 42)]
    v = gold.collapse_versions(states, "k", "m", ["g"])
    assert len(v) == 1 and v[0]["valid_from"] == D(2023, 4, 1) and v[0]["valid_to"] == D(9999, 12, 31)


def test_load_specs_collects_errors(tmp_path):
    spec_dir, sql_dir = tmp_path / "specs", tmp_path / "sql"
    spec_dir.mkdir(), sql_dir.mkdir()
    good = "table: dim_a\ndomain: t\ncomment: c\nprimary_key: [k]\ncolumns: {k: {type: STRING, comment: key}}\n"
    (spec_dir / "dim_a.yaml").write_text(good)
    (sql_dir / "dim_a.sql").write_text("SELECT 'x' AS k;\n")
    (spec_dir / "fact_bad.yaml").write_text(good.replace("dim_a", "fact_bad").replace("STRING", "VARCHAR"))
    (spec_dir / "fact_nosql.yaml").write_text(good.replace("dim_a", "fact_nosql"))
    (spec_dir / "fact_child.yaml").write_text(good.replace("dim_a", "fact_child") + "depends_on: [fact_bad]\n")
    (sql_dir / "fact_child.sql").write_text("SELECT 'x' AS k")
    (spec_dir / "_columns.yaml").write_text("columns: {k: key}\n")
    with pytest.raises(gold.SpecError):
        gold.load_specs(spec_dir, sql_dir)
    errors = []
    specs = gold.load_specs(spec_dir, sql_dir, errors=errors)
    assert list(specs) == ["dim_a"] and len(errors) == 3                 # bad type, missing SQL, orphan depends_on
    assert specs["dim_a"].body == "SELECT 'x' AS k"


# ---- the repo's specs ---------------------------------------------------------------------------------
def test_repo_specs_load_and_cover_the_hub(specs):
    missing = [t for t in HUB if t not in specs]
    assert not missing, missing
    for name in HUB:
        s = specs[name]
        assert s.sql_path and s.sql_path.exists() and s.spec_path.stem == name
        if s.kind == "table":
            assert s.primary_key and s.min_rows >= 1


def test_all_repo_specs_load_cleanly():
    """Fails (listing them) while any package's spec / SQL is invalid; other tests skip only that spec."""
    assert not _SPEC_ERRORS, _SPEC_ERRORS


@pytest.mark.parametrize("name", sorted(_ALL))
def test_spec_conventions(name, dicts):
    """Gold conventions per spec: fully commented (D25), reads silver / gold only, known placeholders."""
    s = _ALL[name]
    assert gold.resolve_comments(s, dicts) == [], "columns without a comment"
    assert "${" not in s.full_comment and all("${" not in (c.comment or "") for c in s.columns)
    refs = set(gold.sql_refs(s.body)) - OPS_RUN_LOGS
    schemas = {sch for sch, _ in refs}
    assert schemas <= {"silver", "gold"}, schemas
    body = s.body.replace("${catalog}.silver.share_", "")
    for sch, t in OPS_RUN_LOGS:
        body = re.sub(rf"(\$\{{catalog\}}\.)?\b{sch}\.{t}\b", "", body)
    assert not re.search(r"\b(bronze|ops|shared)\.", body)
    params = gold.build_params(load_config())
    texts = [s.body] + [c["sql"] for c in s.checks] + [r["silver"] for r in s.reconcile]
    unknown = set().union(*(gold.placeholders(t) for t in texts)) - set(params) - {"table"}
    assert not unknown, unknown


def test_repo_specs_build_order(specs):
    order = gold.build_order(specs)
    assert order.index("dim_employee") < order.index("dim_client") < order.index("dim_client_group")
    assert order.index("fx_rate_daily") < order.index("fn_usd")
    assert order.index("dim_client_group") < order.index("dim_account_plan_initiative")


def test_dim_client_contract(specs):
    s = specs["dim_client"]
    assert s.primary_key == ["golden_client_sk"] and s.scd2["key"] == "golden_client_id"
    for col in ["golden_client_sk", "golden_client_id", "version_no", "valid_from", "valid_to", "is_current",
                "aliases", "aliases_text", "source_systems_present", "source_systems_text", *CLIENT_BLOCK]:
        assert col in s.column_names, col
    assert s.column("aliases").type == "ARRAY<STRING>"
    refs = {fk.ref_table for fk in s.foreign_keys}
    assert {"dim_client_group", "dim_employee", "dim_booking_entity", "dim_industry"} <= refs
    group = specs["dim_client_group"]
    for col in ["group_name", "group_segment", "group_relationship_tier", "lead_office", "lead_rm_id",
                "worst_internal_rating_grade", "worst_ews_band", "jp_parent_legal_name", "is_japanese_group",
                "lead_golden_client_sk"]:
        assert col in group.column_names, col


def test_threshold_params_come_from_config(specs):
    body, cfg = specs["dim_threshold"].body, load_config()
    for key in ("rorwa_hurdle", "raroc_hurdle", "roe_hurdle", "single_borrower_attention_usd",
                "ews_amber_min", "ews_red_min", "covenant_headroom_warn"):
        assert f"${{{key}}}" in body and key in cfg.thresholds
    assert float(cfg.thresholds["rorwa_hurdle"]) == pytest.approx(0.012)
