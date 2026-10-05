"""Latent-health and accounts generator tests (pure Python)."""
from collections import defaultdict
from statistics import mean

import pytest

from smbc_genie_lib import accounts, fragment, health, truth
from smbc_genie_lib.config import load_config


@pytest.fixture(scope="module")
def built():
    cfg = load_config()
    groups = truth.build_groups(cfg)
    entities = truth.build_entities(cfg, groups)
    return cfg, groups, entities


def test_health_monthly_shape_and_range(built):
    cfg, _, entities = built
    hm = health.build_health_monthly(cfg, entities)
    months = len(health.month_starts(cfg.history_start))
    assert months == 48  # 2023-04 .. 2027-03
    assert len(hm) == len(entities) * months
    assert all(0.02 <= r["health"] <= 0.99 for r in hm)
    assert all(1 <= r["grade_effective"] <= 10 for r in hm)


def test_health_deterministic(built):
    cfg, _, entities = built
    a = health.build_health_monthly(cfg, entities[:50])
    b = health.build_health_monthly(cfg, entities[:50])
    assert [r["health"] for r in a] == [r["health"] for r in b]


def test_coal_sector_declines_into_2026(built):
    cfg, _, entities = built
    hm = health.build_health_monthly(cfg, entities)
    sunda = next(e for e in entities if e["short_name"] == "Sunda Energi Nusantara")
    rows = [r for r in hm if r["entity_id"] == sunda["entity_id"]]
    h2024 = mean(r["health"] for r in rows if r["month"].year == 2024)
    h2026 = mean(r["health"] for r in rows if r["month"].year == 2026)
    assert h2026 < h2024, (h2024, h2026)  # negative coal/energy cycle bites by 2026


def _sector_mean_health(hm, entities, subsectors, year):
    eids = {e["entity_id"] for e in entities if e["industry_subsector"] in subsectors}
    vals = [r["health"] for r in hm if r["entity_id"] in eids and r["month"].year == year]
    return mean(vals) if vals else None


def test_negative_sectors_visibly_decline(built):
    """The negative-sector downturn must be material at the sector level by 2026, not just a nudge."""
    cfg, _, entities = built
    hm = health.build_health_monthly(cfg, entities)
    h2023 = _sector_mean_health(hm, entities, health.NEGATIVE_SUBSECTORS, 2023)
    h2026 = _sector_mean_health(hm, entities, health.NEGATIVE_SUBSECTORS, 2026)
    assert h2023 is not None and h2026 is not None
    assert h2023 - h2026 >= 0.12, (h2023, h2026)  # a clearly visible decline


def test_positive_sectors_improve(built):
    cfg, _, entities = built
    hm = health.build_health_monthly(cfg, entities)
    h2023 = _sector_mean_health(hm, entities, health.POSITIVE_SUBSECTORS, 2023)
    h2026 = _sector_mean_health(hm, entities, health.POSITIVE_SUBSECTORS, 2026)
    if h2023 is not None and h2026 is not None:  # positive subsectors exist in the universe
        assert h2026 > h2023, (h2023, h2026)


def test_accounts_only_for_core_entities(built):
    cfg, groups, entities = built
    gids = [g["group_id"] for g in groups]
    _, xref = fragment.fragment_all(cfg, entities, gids)
    custno = {}
    for x in xref:
        if x["source_system"] == "core_customer" and not x["is_within_source_dup"]:
            custno.setdefault(x["entity_id"], x["source_id"])
    accts = accounts.build_accounts(cfg, entities, custno)
    assert 7000 <= len(accts) <= 10500, len(accts)  # ~9,000 target (brief §6.2, approximate)
    assert all(a["entity_id"] in custno for a in accts)
    # both CASA and time deposits exist
    assert any(a["is_casa"] for a in accts) and any(not a["is_casa"] for a in accts)
    assert all(a["base_balance_usd"] > 0 for a in accts)


def test_accounts_deterministic(built):
    cfg, groups, entities = built
    gids = [g["group_id"] for g in groups]
    _, xref = fragment.fragment_all(cfg, entities, gids)
    custno = {x["entity_id"]: x["source_id"] for x in xref
              if x["source_system"] == "core_customer" and not x["is_within_source_dup"]}
    a1 = accounts.build_accounts(cfg, entities, custno)
    a2 = accounts.build_accounts(cfg, entities, custno)
    assert [a["account_id"] for a in a1] == [a["account_id"] for a in a2]
