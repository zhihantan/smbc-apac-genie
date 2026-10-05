"""Unit tests for the templated SQL runner's pure helpers (no workspace)."""
from smbc_genie_lib.sql_runner import render, split_statements


def test_render_substitutes_and_leaves_unknown():
    out = render("USE CATALOG ${catalog}; SELECT '${missing}'", {"catalog": "smbc_genie"})
    assert "smbc_genie" in out
    assert "${missing}" in out  # unknown placeholders are left intact (should fail loudly later)


def test_split_basic():
    stmts = split_statements("SELECT 1; SELECT 2 ;SELECT 3")
    assert stmts == ["SELECT 1", "SELECT 2", "SELECT 3"]


def test_split_skips_comment_only_statements():
    sql = """-- header comment
CREATE SCHEMA s;
-- trailing todo with no statement after it
"""
    stmts = split_statements(sql)
    assert len(stmts) == 1
    assert stmts[0].strip().endswith("CREATE SCHEMA s")  # header comment stays attached, is fine


def test_split_keeps_semicolon_inside_metric_view_yaml():
    sql = """CREATE OR REPLACE VIEW c.m.v WITH METRICS LANGUAGE YAML AS $$
version: 1.1
source: c.g.f
dimensions:
  - name: a
    expr: a
measures:
  - name: n
    expr: COUNT(1)
$$;
SELECT 1"""
    stmts = split_statements(sql)
    assert len(stmts) == 2
    assert "version: 1.1" in stmts[0]
    assert stmts[1] == "SELECT 1"


def test_split_ignores_semicolon_in_string_literal():
    stmts = split_statements("SELECT 'a;b' AS x; SELECT 2")
    assert len(stmts) == 2
    assert stmts[0] == "SELECT 'a;b' AS x"
