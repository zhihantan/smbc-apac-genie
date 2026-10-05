"""Identity-fragmentation tests (pure Python, no Spark)."""
import pytest

from smbc_genie_lib import fragment, truth
from smbc_genie_lib.config import load_config


@pytest.fixture(scope="module")
def built():
    cfg = load_config()
    groups = truth.build_groups(cfg)
    entities = truth.build_entities(cfg, groups)
    gids = [g["group_id"] for g in groups]
    records, xref = fragment.fragment_all(cfg, entities, gids)
    return cfg, groups, entities, records, xref


def test_synthetic_id_formats():
    assert fragment.make_lei("SYN-E-00001").startswith("SYNLEI")
    assert fragment.make_tax_id("SYN-E-00001", "SG").startswith("SYNTX-SG-")


def test_record_count_in_band(built):
    _, _, entities, records, xref = built
    assert len(records) == len(xref)
    # ~8-9k records (core presence doubles as deposit penetration); generous band
    assert 7000 <= len(records) <= 9800, len(records)
    # average 2-4 source records per entity
    assert 2.0 <= len(records) / len(entities) <= 4.0


def test_every_entity_has_a_record(built):
    _, _, entities, _, xref = built
    covered = {x["entity_id"] for x in xref}
    assert covered == {e["entity_id"] for e in entities}


def test_noise_rates_within_tolerance(built):
    _, _, _, _, xref = built
    n = len(xref)
    wrong_cc = sum(1 for x in xref if not x["country_correct"]) / n
    stale = sum(1 for x in xref if not x["parent_correct"]) / n
    dups = sum(1 for x in xref if x["is_within_source_dup"]) / n
    assert 0.01 <= wrong_cc <= 0.035, wrong_cc
    assert 0.015 <= stale <= 0.05, stale
    assert 0.03 <= dups <= 0.09, dups


def test_most_entities_recoverable_via_lei(built):
    # Deterministic matching needs most entities to share a LEI across >=2 records.
    from collections import defaultdict
    by_entity = defaultdict(list)
    _, _, _, _, xref = built
    for x in xref:
        by_entity[x["entity_id"]].append(x)
    with_lei = sum(1 for recs in by_entity.values() if sum(r["has_lei"] for r in recs) >= 1)
    assert with_lei / len(by_entity) >= 0.90


def test_ext_lei_rate_near_policy(built):
    _, _, _, _, xref = built
    ext = [x for x in xref if x["source_system"] == "ext_company_master" and not x["is_within_source_dup"]]
    rate = sum(x["has_lei"] for x in ext) / len(ext)
    assert 0.90 <= rate <= 0.99, rate


def test_deterministic(built):
    cfg, groups, entities, records, _ = built
    gids = [g["group_id"] for g in groups]
    r2, _ = fragment.fragment_all(cfg, entities, gids)
    assert [r["source_id"] for r in records] == [r["source_id"] for r in r2]
    assert [r["name_recorded"] for r in records] == [r["name_recorded"] for r in r2]


def test_name_variant_helpers():
    assert fragment.strip_legal_suffix("Kinokawa Precision (Singapore) Pte Ltd") == "Kinokawa Precision (Singapore)"
    assert fragment.strip_parenthetical("Kinokawa Precision (Singapore) Pte Ltd") == "Kinokawa Precision Pte Ltd"
    assert fragment.abbreviate("Acme International Manufacturing") == "Acme Intl Mfg"
    assert len(fragment.truncate("A" * 50, 35)) == 35
