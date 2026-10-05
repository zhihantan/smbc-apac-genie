"""Unit tests for the benchmark checks and the result-comparison rule (genie/README.md)."""
from smbc_genie_lib.genie import check_result, compare_results, normalize_value


def test_normalize_value():
    assert normalize_value("123.456789") == normalize_value(123.4567)          # 4 significant digits
    assert normalize_value("123.4") != normalize_value(123.5)
    assert normalize_value("true") == normalize_value(True)
    assert normalize_value("2026-09-01T00:00:00.000Z") == normalize_value("2026-09-01")
    assert normalize_value(None) == (0, "")
    assert normalize_value("0") == normalize_value(0.0)
    assert normalize_value("Red") == (3, "Red")


EXP_COLS = ["client", "exposure_usd"]
EXP = [["A", "100.0"], ["B", "250.123"]]


def test_compare_exact_any_order_any_names():
    act_cols = ["Exposure in Red", "Client"]
    act = [["250.1234", "B"], ["100", "A"]]
    assert compare_results(EXP_COLS, EXP, act_cols, act) == (True, "match")


def test_compare_extra_rows_fail():
    ok, why = compare_results(EXP_COLS, EXP, EXP_COLS, EXP + [["C", "1"]])
    assert not ok and "row count" in why


def test_compare_extra_columns_only_when_allowed():
    act_cols = ["client", "exposure", "score"]
    act = [["A", "100", "71"], ["B", "250.12", "80"]]
    ok, why = compare_results(EXP_COLS, EXP, act_cols, act)
    assert not ok and "extra column" in why
    ok, why = compare_results(EXP_COLS, EXP, act_cols, act, allow_extra_columns=True)
    assert ok and "extra" in why


def test_compare_wrong_values_or_pairing():
    ok, _ = compare_results(EXP_COLS, EXP, EXP_COLS, [["A", "100"], ["B", "251"]])
    assert not ok
    # same column value sets but swapped pairing -> rows differ
    ok, why = compare_results(EXP_COLS, EXP, EXP_COLS, [["A", "250.123"], ["B", "100"]])
    assert not ok and "rows differ" in why


def test_compare_duplicate_value_columns_backtracks():
    exp_cols, exp = ["a", "b"], [["1", "1"], ["2", "1"]]
    act_cols, act = ["x", "y", "z"], [["1", "1", "1"], ["1", "2", "1"]]
    ok, _ = compare_results(exp_cols, exp, act_cols, act, allow_extra_columns=True)
    assert ok


def test_check_result():
    cols, rows = ["Client", "exposure_usd"], [["Sunda Energi", 10], ["B", 5]]
    assert check_result(cols, rows, {"rows": 2, "must_contain_columns": ["exposure_usd"],
                                     "top_row_contains": ["sunda"]}) == []
    fails = check_result(cols, rows, {"min_rows": 3, "must_contain_columns": ["score"], "top_row_contains": ["x"]})
    assert len(fails) == 3


def test_typed_result():
    from smbc_genie_lib.genie import typed_result
    ser = {"status": {"state": "SUCCEEDED"},
           "manifest": {"schema": {"columns": [{"name": "a"}, {"name": "b"}]}, "total_row_count": 2, "truncated": False},
           "result": {"data_typed_array": [{"values": [{"str": "x"}, {"str": "1"}]}, {"values": [{"str": "y"}, {}]}]}}
    assert typed_result(ser) == (["a", "b"], [["x", "1"], ["y", None]])
    ser["manifest"]["total_row_count"] = 3            # partial result (more chunks) -> None
    assert typed_result(ser) is None
    assert typed_result({"status": {"state": "FAILED"}}) is None
