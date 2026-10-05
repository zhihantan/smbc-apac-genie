"""Unit tests for the SQL-expression builders."""
import pytest

from smbc_genie_lib.sqlgen import pick_from_array, weighted_case


def test_weighted_case_structure():
    sql = weighted_case("u", [("A", 2), ("B", 1), ("C", 1)])
    assert sql.startswith("CASE WHEN u < 0.500000 THEN 'A'")
    assert "WHEN u < 0.750000 THEN 'B'" in sql
    assert sql.endswith("ELSE 'C' END")


def test_weighted_case_single():
    assert weighted_case("u", [("Only", 1)]) == "CASE ELSE 'Only' END"


def test_weighted_case_rejects_empty():
    with pytest.raises(ValueError):
        weighted_case("u", [])


def test_pick_from_array():
    sql = pick_from_array("u", ["SWIFT", "ISO", "API"])
    assert "array('SWIFT', 'ISO', 'API')" in sql
    assert "CAST(u * 3 AS INT) + 1" in sql
