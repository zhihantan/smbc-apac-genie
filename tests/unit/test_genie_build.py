"""Unit tests for the Genie space builder (genie/<slug>/space.yaml -> serialized_space v2)."""
import json
import textwrap
from pathlib import Path

import pytest

from smbc_genie_lib import genie as g
from smbc_genie_lib.metrics import SPACES

SLUG = "early_warning_monitoring"
MV = "smbc_genie.metrics.mv_ews_scores"
DIM = "smbc_genie.gold.dim_ews_trigger"

SPACE_YAML = textwrap.dedent("""\
    slug: early_warning_monitoring
    title: APAC Genie - Early Warning Monitoring
    description: Ask about early-warning scores. Data is synthetic and as of 30 Sep 2026.
    data_sources:
      - identifier: smbc_genie.metrics.mv_ews_scores
        columns:
          Daily EWS Band: {entity_matching: true}
          Client Days: {exclude: true}
          Client: {synonyms: [obligor]}
      - identifier: ${catalog}.gold.dim_ews_trigger
        include_columns: [trigger_code, description]
        columns:
          trigger_code: {entity_matching: true}
    instructions:
      domain:
        - "- Red >= 70, Amber 40-69."
    example_sqls:
      - question: Score for :client_group since :from_month
        usage_guidance: EWS history questions.
        parameters:
          - {name: client_group, type: STRING, description: Group name, default: Sunda}
          - {name: from_month, type: DATE, description: First month, default: "2026-01-01"}
        sql: |
          SELECT `Month`, MEASURE(`Latest Score`) AS s
          FROM smbc_genie.metrics.mv_ews_scores
          WHERE `Client Group` LIKE CONCAT('%', :client_group, '%') AND `Date` >= :from_month
          GROUP BY ALL
    sql_functions:
      - smbc_genie.gold.fn_ews_timeline
      - smbc_genie.gold.fn_as_of_date
    sample_questions:
      - Which clients are Red today?
      - Second question
    benchmarks:
      - key: Q1
        question: Which clients are Red today?
        answer: Q1
      - key: Q1-casual
        question: who is red rn
        variant_of: Q1
      - key: X1
        question: How many clients are Red today?
        expected_sql: |
          SELECT MEASURE(`Clients Red`) AS clients_red FROM smbc_genie.metrics.mv_ews_scores
          WHERE `Date` = DATE'2026-09-30'
        checks: {rows: 1, must_contain_columns: clients_red}
""")

FUNCTIONS_SQL = textwrap.dedent("""\
    -- test: SELECT * FROM smbc_genie.gold.fn_ews_timeline('Sunda', '2026-01-01')
    CREATE OR REPLACE FUNCTION ${catalog}.gold.fn_ews_timeline(
      client_group STRING COMMENT 'Group name fragment',
      from_month STRING COMMENT 'First month yyyy-MM-dd')
    RETURNS TABLE (month DATE COMMENT 'Month', score DOUBLE COMMENT 'Score')
    COMMENT 'EWS score by month for a client group.'
    RETURN SELECT `Month`, MEASURE(`Latest Score`) FROM smbc_genie.metrics.mv_ews_scores
      WHERE `Client Group` LIKE '%' || client_group || '%' AND `Date` >= to_date(from_month) GROUP BY ALL;
""")

ANSWERS_SQL = textwrap.dedent("""\
    -- Q1: Which clients are Red today?
    -- views: mv_ews_scores
    -- rows: 8
    SELECT `Client`, MEASURE(`Latest Score`) AS ews_score FROM smbc_genie.metrics.mv_ews_scores
    WHERE `Date` = DATE'2026-09-30' AND `Daily EWS Band` = 'Red' GROUP BY ALL;
""")


def _digest(ident, cols):
    return {"identifier": ident, "type": "METRIC_VIEW", "comment": "c",
            "columns": [{"name": n, "type": t, "is_measure": m, "comment": "x"} for n, t, m in cols]}


@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    root = tmp_path_factory.mktemp("genie")
    (root / SLUG).mkdir()
    (root / SLUG / "space.yaml").write_text(SPACE_YAML)
    (root / SLUG / "functions.sql").write_text(FUNCTIONS_SQL)
    (root / "answers").mkdir()
    (root / "answers" / f"{SLUG}.sql").write_text(ANSWERS_SQL)
    schema = {
        MV: _digest(MV, [("Date", "date", False), ("Client", "string", False), ("Daily EWS Band", "string", False),
                         ("Rating Grade", "int", False), ("Latest Score", "double", True), ("Client Days", "bigint", True)]),
        DIM: _digest(DIM, [("trigger_code", "string", False), ("description", "string", False),
                           ("signals_fired", "bigint", False)]),
    }
    return root, schema


@pytest.fixture(scope="module")
def built(tree):
    root, schema = tree
    src = g.load_space(SLUG, root)
    return g.build_space(src, g.load_shared(), schema, g.load_answers(SLUG, root / "answers"), SPACES)


def test_build_ok(built):
    assert built.errors == [], built.errors
    assert built.title == "APAC Genie - Early Warning Monitoring"
    assert built.assets == [MV, DIM]
    c = built.counts()
    assert c["example_sqls"] == 1 and c["sql_functions"] == 2 and c["text_instructions"] == 1
    assert c["instruction_lines"] == 13            # 10 base + as-of + 1 domain + response
    assert c["benchmarks"] == 3 and c["sample_questions"] == 2


def test_tables_sorted_and_metric_views_in_tables(built):
    ds = built.serialized["data_sources"]
    assert "metric_views" not in ds
    assert [t["identifier"] for t in ds["tables"]] == sorted([MV, DIM])


def test_column_configs(built):
    t = {x["identifier"]: x for x in built.serialized["data_sources"]["tables"]}
    mv = {c["column_name"]: c for c in t[MV]["column_configs"]}
    assert list(mv) == sorted(mv)                                   # sorted by column_name
    assert mv["Daily EWS Band"] == {"column_name": "Daily EWS Band", "enable_format_assistance": True,
                                    "enable_entity_matching": True}
    assert mv["Client Days"] == {"column_name": "Client Days", "exclude": True}
    assert mv["Client"]["synonyms"] == ["obligor"] and mv["Client"]["enable_format_assistance"] is True
    assert "Rating Grade" not in mv and "Latest Score" not in mv     # non-string defaults: no entry
    dim = {c["column_name"]: c for c in t[DIM]["column_configs"]}
    assert dim["signals_fired"] == {"column_name": "signals_fired", "exclude": True}   # not in include_columns
    assert dim["trigger_code"]["enable_entity_matching"] is True
    assert built.entity_matching == [f"{MV}.Daily EWS Band", f"{DIM}.trigger_code"]


def test_ids_ordered_and_stable(built, tree):
    root, schema = tree
    again = g.build_space(g.load_space(SLUG, root), g.load_shared(), schema,
                          g.load_answers(SLUG, root / "answers"), SPACES)
    assert json.dumps(again.serialized) == json.dumps(built.serialized)
    bq = built.serialized["benchmarks"]["questions"]
    assert [q["question"][0] for q in bq] == ["Which clients are Red today?", "who is red rn",
                                               "How many clients are Red today?"]   # authoring order kept
    assert all(len(q["id"]) == 32 for q in bq) and [q["id"] for q in bq] == sorted(q["id"] for q in bq)


def test_ordered_id():
    a, b = g.ordered_id("s", "benchmark", 1), g.ordered_id("s", "benchmark", 2)
    assert len(a) == 32 and a < b and a[:24] == b[:24]
    assert g.ordered_id("s", "sample_question", 1)[:24] != a[:24]


def test_text_instruction_block(built):
    ti = built.serialized["instructions"]["text_instructions"]
    assert len(ti) == 1
    content = ti[0]["content"]
    assert all(x.endswith("\n") for x in content[:-1]) and not content[-1].endswith("\n")
    assert content[0].startswith("- Fiscal year runs 1 April")
    assert any("Today = 30 Sep 2026" in x for x in content)
    assert "- Red >= 70, Amber 40-69.\n" in content


def test_example_sql_parameters(built):
    ex = built.serialized["instructions"]["example_question_sqls"][0]
    assert ex["parameters"][0] == {"name": "client_group", "type_hint": "STRING", "description": ["Group name"],
                                   "default_value": {"values": ["Sunda"]}}
    assert ex["parameters"][1]["type_hint"] == "DATE"
    assert ex["usage_guidance"] == ["EWS history questions."]
    assert ex["sql"][0].endswith("\n")


def test_benchmarks_answers_and_variants(built):
    by_key = {b["key"]: b for b in built.benchmarks}
    assert by_key["Q1"]["expected_checks"] == {"rows": 8}
    assert by_key["Q1-casual"]["expected_sql"] == by_key["Q1"]["expected_sql"]
    assert by_key["Q1-casual"]["variant_of_id"] == by_key["Q1"]["id"]
    assert by_key["X1"]["expected_checks"] == {"rows": 1, "must_contain_columns": ["clients_red"]}
    ans = built.serialized["benchmarks"]["questions"][0]["answer"]
    assert ans[0]["format"] == "SQL" and len(ans) == 1


def test_functions_parsed(built):
    assert [f.identifier for f in built.functions] == ["smbc_genie.gold.fn_ews_timeline"]
    assert built.functions[0].has_comment
    assert built.function_tests == ["SELECT * FROM smbc_genie.gold.fn_ews_timeline('Sunda', '2026-01-01')"]
    assert built.functions[0].args == [("client_group", "STRING"), ("from_month", "STRING")]


def _build_with(tree, mutate):
    root, schema = tree
    src = g.load_space(SLUG, root)
    src.raw = mutate(json.loads(json.dumps(src.raw)))
    return g.build_space(src, g.load_shared(), schema, g.load_answers(SLUG, root / "answers"), SPACES)


def test_errors_are_reported(tree):
    def bad(raw):
        raw["title"] = "Wrong"
        raw["data_sources"][0]["columns"]["Clinet"] = {"synonyms": ["x"]}
        raw["data_sources"][0]["columns"]["Rating Grade"] = {"entity_matching": True}
        raw["example_sqls"][0]["parameters"][1]["type"] = "TIMESTAMP"
        raw["example_sqls"][0]["sql"] += " AND x = :undeclared"
        raw["benchmarks"][2]["expected_sql"] = "SELECT * FROM smbc_genie.gold.dim_client WHERE d = current_date()"
        raw["instructions"]["domain"] = ["- x"] * 9
        raw["bogus"] = 1
        return raw
    errs = " | ".join(_build_with(tree, bad).errors)
    for needle in ["title 'Wrong'", "'Clinet' is not a column", "entity_matching needs a STRING", "TIMESTAMP",
                   ":undeclared used", "dim_client, which is not an asset", "CURRENT_DATE", "9 lines > 8",
                   "unknown key 'bogus'"]:
        assert needle in errs, needle


def test_function_comment_detection():
    with_param_comment_only = ("CREATE OR REPLACE FUNCTION c.gold.fn_x(a STRING COMMENT 'p') "
                               "RETURNS TABLE (b INT COMMENT 'c') RETURN SELECT 1")
    ok = ("CREATE OR REPLACE FUNCTION c.gold.fn_x(a STRING COMMENT 'p') RETURNS TABLE (b INT) "
          "COMMENT 'Function comment, with (parens).' RETURN SELECT 1")
    assert not g._function_has_comment(with_param_comment_only)
    assert g._function_has_comment(ok)
    prose = ("CREATE OR REPLACE FUNCTION c.gold.fn_x(a STRING COMMENT 'p') RETURNS TABLE (b INT) "
             "COMMENT 'Lists the cases; the views return counts, open now (as of 30-Sep).' RETURN SELECT 1")
    assert g._function_has_comment(prose)   # 'return' / 'now (' inside the COMMENT are prose, not code


def test_forbidden_functions_ignore_comment_prose():
    ok = ("CREATE OR REPLACE FUNCTION c.gold.fn_x(a STRING COMMENT 'p') RETURNS TABLE (b INT) "
          "COMMENT 'Cases open now (at the as-of date).' RETURN SELECT 1")
    assert len(g.parse_functions_sql(ok, {}).functions) == 1
    bad = ok.replace("SELECT 1", "SELECT datediff(now(), DATE'2026-01-01')")
    with pytest.raises(g.GenieSpecError):
        g.parse_functions_sql(bad, {})


def test_sql_parameters_and_references():
    sql = "SELECT x::date, ':notparam' FROM smbc_genie.metrics.mv_a JOIN `smbc_genie`.`gold`.`dim_b` -- :c\n" \
          "WHERE y = :p1 AND smbc_genie.gold.fn_z(:p2) > 0"
    assert g.sql_parameters(sql) == {"p1", "p2"}
    objs, fns = g.sql_references(sql, "smbc_genie")
    assert objs == {"smbc_genie.metrics.mv_a", "smbc_genie.gold.dim_b"} and fns == {"smbc_genie.gold.fn_z"}


def test_diff_json():
    assert g.diff_json({"a": [1, {"b": 2}]}, {"a": [1, {"b": 2}]}) == []
    d = g.diff_json({"a": [1, {"b": 2}], "c": 1}, {"a": [1, {"b": 3}], "d": 1})
    assert any(x.startswith("a[1].b") for x in d) and any(x.startswith("c: missing") for x in d) \
        and any(x.startswith("d: added") for x in d)


def test_metadata_gaps():
    d = _digest("c.m.v", [("A", "string", False), ("M", "double", True)])
    assert g.metadata_gaps(d) == []
    d["columns"][1]["comment"] = ""
    assert g.metadata_gaps(d) == ["c.m.v.M: measure comment"]


def test_function_date_argument_rejected(tree):
    root, schema = tree
    src = g.load_space(SLUG, root)
    src.functions_text = src.functions_text.replace("from_month STRING", "from_month DATE")
    b = g.build_space(src, g.load_shared(), schema, g.load_answers(SLUG, root / "answers"), SPACES)
    assert any("Genie rejects DATE function arguments" in e for e in b.errors)
    assert g.function_args("CREATE FUNCTION c.gold.fn_x(a DECIMAL(10, 2) COMMENT 'x, y', b ARRAY<STRING>) RETURNS INT RETURN 1") \
        == [("a", "DECIMAL"), ("b", "ARRAY")]
