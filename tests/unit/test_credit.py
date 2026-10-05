"""Lending-book generator tests (facilities, covenants, collateral)."""
import pytest

from smbc_genie_lib import credit, fragment, health, truth
from smbc_genie_lib.config import load_config


@pytest.fixture(scope="module")
def built():
    cfg = load_config()
    groups = truth.build_groups(cfg)
    entities = truth.build_entities(cfg, groups)
    gids = [g["group_id"] for g in groups]
    _, xref = fragment.fragment_all(cfg, entities, gids)
    obligor = {}
    for x in xref:
        if x["source_system"] == "credit_obligor" and not x["is_within_source_dup"]:
            obligor.setdefault(x["entity_id"], x["source_id"])
    facs = credit.build_facilities(cfg, entities, obligor)
    hlut = {(r["entity_id"], r["month"]): r["health"] for r in health.build_health_monthly(cfg, entities)}
    covs = credit.build_covenant_tests(cfg, facs, hlut)
    coll = credit.build_collateral(cfg, facs)
    return cfg, entities, obligor, facs, covs, coll


def test_facility_count_and_integrity(built):
    _, _, obligor, facs, _, _ = built
    assert 1200 <= len(facs) <= 2900, len(facs)
    assert all(f["entity_id"] in obligor for f in facs)
    assert all(f["limit_usd"] >= 250000 for f in facs)
    assert all(f["maturity_date"] > f["origination_date"] for f in facs)
    assert all(30 <= f["margin_bps"] <= 500 for f in facs)  # 30-35 bps: parent-guaranteed JC deals (Kinokawa)


def test_japanese_corporates_more_parent_support(built):
    _, entities, _, facs, _, _ = built
    seg = {e["entity_id"]: e["segment"] for e in entities}
    jc = [f for f in facs if seg[f["entity_id"]] == "Japanese Corporate"]
    other = [f for f in facs if seg[f["entity_id"]] != "Japanese Corporate"]
    jc_support = sum(1 for f in jc if f["guarantor_type"] in ("Parent Guarantee", "Keepwell")) / max(len(jc), 1)
    oth_support = sum(1 for f in other if f["guarantor_type"] in ("Parent Guarantee", "Keepwell")) / max(len(other), 1)
    assert jc_support > oth_support


def test_covenants_base_has_comfortable_headroom(built):
    _, _, _, _, covs, _ = built
    # base generators keep non-storyline borrowers inside covenants
    assert all(not r["breached"] for r in covs)
    assert min(r["headroom_pct"] for r in covs) >= 0.12
    # exactly one latest test per (facility, covenant)
    latest = [r for r in covs if r["is_latest_test"]]
    keys = {(r["facility_id"], r["covenant_type"]) for r in covs}
    assert len(latest) == len(keys)


def test_collateral_only_secured_and_ltv_range(built):
    _, _, _, facs, _, coll = built
    secured = {f["facility_id"] for f in facs if f["is_secured"]}
    assert {c["facility_id"] for c in coll} <= secured
    assert all(0.3 <= c["ltv_pct"] <= 0.9 for c in coll)


def test_deterministic(built):
    cfg, entities, obligor, facs, _, _ = built
    f2 = credit.build_facilities(cfg, entities, obligor)
    assert [f["facility_id"] for f in facs] == [f["facility_id"] for f in f2]
    assert [f["limit_usd"] for f in facs] == [f["limit_usd"] for f in f2]
