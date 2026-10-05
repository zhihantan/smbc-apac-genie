"""Unit tests for Genie serialized_space helpers."""
from smbc_genie_lib.genie import new_space, sort_serialized_space, stable_id


def test_stable_id_is_32hex_and_deterministic():
    a = stable_id("account_planning", "benchmark", 3)
    b = stable_id("account_planning", "benchmark", 3)
    assert a == b and len(a) == 32
    assert all(ch in "0123456789abcdef" for ch in a)
    assert stable_id("x", 1) != stable_id("x", 2)


def test_new_space_merges_and_sorts_data_sources():
    ss = new_space("T", "d", metric_views=["c.m.z_mv", "c.m.a_mv"], tables=["c.g.z", "c.g.a"])
    # metric views + tables are merged into data_sources.tables, sorted by identifier.
    assert [t["identifier"] for t in ss["data_sources"]["tables"]] == \
        ["c.g.a", "c.g.z", "c.m.a_mv", "c.m.z_mv"]
    assert "metric_views" not in ss["data_sources"]


def test_sort_orders_all_lists():
    ss = new_space("T", "d", tables=["c.g.z", "c.g.a"])
    ss["config"]["sample_questions"] = [{"id": "ff"}, {"id": "11"}]
    ss["instructions"]["example_question_sqls"] = [{"id": "ee"}, {"id": "22"}]
    ss["benchmarks"]["questions"] = [{"id": "dd"}, {"id": "33"}]
    # column_configs on the 'c.g.z' table should sort by column_name.
    tbl_z = next(t for t in ss["data_sources"]["tables"] if t["identifier"] == "c.g.z")
    tbl_z["column_configs"] = [{"column_name": "z"}, {"column_name": "a"}]

    sort_serialized_space(ss)

    assert [t["identifier"] for t in ss["data_sources"]["tables"]] == ["c.g.a", "c.g.z"]
    assert [q["id"] for q in ss["config"]["sample_questions"]] == ["11", "ff"]
    assert [q["id"] for q in ss["instructions"]["example_question_sqls"]] == ["22", "ee"]
    assert [q["id"] for q in ss["benchmarks"]["questions"]] == ["33", "dd"]
    tbl_z = next(t for t in ss["data_sources"]["tables"] if t["identifier"] == "c.g.z")
    assert [c["column_name"] for c in tbl_z["column_configs"]] == ["a", "z"]


def test_new_space_shape():
    ss = new_space("T", "d", metric_views=["c.m.mv"])
    assert ss["version"] == 2
    assert ss["data_sources"]["tables"][0]["identifier"] == "c.m.mv"
