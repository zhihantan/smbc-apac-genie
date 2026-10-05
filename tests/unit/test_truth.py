"""Truth-universe generator tests (pure Python, no Spark)."""
import pytest

from smbc_genie_lib import names, truth
from smbc_genie_lib.config import load_config


@pytest.fixture(scope="module")
def cfg():
    return load_config()


def test_allocate_counts_sums_and_min_one():
    counts = truth.allocate_counts(20260930, 374, 2479, salt="entities")
    assert sum(counts) == 2479
    assert len(counts) == 374
    assert all(c >= 1 for c in counts)


def test_allocate_counts_deterministic():
    a = truth.allocate_counts(1, 50, 300)
    b = truth.allocate_counts(1, 50, 300)
    assert a == b


def test_groups_count_and_segments(cfg):
    groups = truth.build_groups(cfg)
    assert len(groups) == 380
    seg = {}
    for g in groups:
        seg[g["segment"]] = seg.get(g["segment"], 0) + 1
    assert seg["Japanese Corporate"] == 190
    assert seg["Non-Japanese Large Corporate"] == 110
    assert seg["Financial Institution"] == 35
    assert seg["Sponsor & Structured Finance"] == 25
    assert seg["Public Sector"] == 20


def test_storyline_groups_present_with_reserved_brands(cfg):
    groups = truth.build_groups(cfg)
    by_name = {g["group_name"]: g for g in groups}
    for spec in names.STORYLINE_GROUPS.values():
        assert spec["group_name"] in by_name
        assert by_name[spec["group_name"]]["segment"] == spec["segment"]
    # Kinokawa must be a Japanese Corporate HQ'd in JP
    assert by_name["Kinokawa Precision"]["hq_country"] == "JP"


def test_entities_exact_count_and_integrity(cfg):
    groups = truth.build_groups(cfg)
    entities = truth.build_entities(cfg, groups)
    assert len(entities) == 2500
    gids = {g["group_id"] for g in groups}
    assert all(e["group_id"] in gids for e in entities)
    # every group has at least one entity, exactly one lead
    from collections import Counter
    per_group = Counter(e["group_id"] for e in entities)
    assert all(per_group[g["group_id"]] >= 1 for g in groups)
    assert sum(1 for e in entities if e["is_group_lead"]) == 380


def test_entities_deterministic(cfg):
    g1 = truth.build_groups(cfg)
    g2 = truth.build_groups(cfg)
    e1 = truth.build_entities(cfg, g1)
    e2 = truth.build_entities(cfg, g2)
    assert [e["legal_name"] for e in e1] == [e["legal_name"] for e in e2]


def test_all_names_blocklist_clean(cfg):
    groups = truth.build_groups(cfg)
    entities = truth.build_entities(cfg, groups)
    for g in groups:
        names.assert_clean(g["group_name"])
    for e in entities:
        names.assert_clean(e["legal_name"])


def test_entity_legal_names_unique(cfg):
    groups = truth.build_groups(cfg)
    entities = truth.build_entities(cfg, groups)
    legal_names = [e["legal_name"] for e in entities]
    assert len(legal_names) == len(set(legal_names)), "entity legal names must be globally unique"


def test_kinokawa_has_apac_entities(cfg):
    groups = truth.build_groups(cfg)
    entities = truth.build_entities(cfg, groups)
    kg = next(g for g in groups if g["group_name"] == "Kinokawa Precision")
    kent = [e for e in entities if e["group_id"] == kg["group_id"]]
    assert len(kent) >= 1
    assert all(e["short_name"] == "Kinokawa Precision" for e in kent)


def test_people_and_products(cfg):
    people = truth.build_people(cfg)
    assert len(people) == 245  # 120+60+30+25+10
    roles = {p["role"] for p in people}
    assert "RM" in roles and "Credit Analyst" in roles
    products = truth.build_products()
    assert len(products) >= 30
    fams = {p["product_family"] for p in products}
    assert {"Trade Finance", "Cash", "Liquidity"} <= fams
