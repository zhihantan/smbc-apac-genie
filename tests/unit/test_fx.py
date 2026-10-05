"""FX relationship-truth tests (pure Python; the Spark deal fan-out is verified in-workspace)."""
import pytest

from smbc_genie_lib import fx, fragment, truth
from smbc_genie_lib.config import load_config


@pytest.fixture(scope="module")
def built():
    cfg = load_config()
    groups = truth.build_groups(cfg)
    entities = truth.build_entities(cfg, groups)
    gids = [g["group_id"] for g in groups]
    _, xref = fragment.fragment_all(cfg, entities, gids)
    cpty = {}
    for x in xref:
        if x["source_system"] == "tsy_counterparty" and not x["is_within_source_dup"]:
            cpty.setdefault(x["entity_id"], x["source_id"])
    rels = fx.build_fx_relationships(cfg, entities, cpty)
    return cfg, entities, cpty, rels


def test_products_and_margins_consistent():
    assert abs(sum(w for _, w in fx.FX_PRODUCTS) - 1.0) < 1e-9
    assert set(p for p, _ in fx.FX_PRODUCTS) == set(fx.MARGIN_BPS)


def test_primary_pair_and_em():
    assert fx.primary_pair("SG") == "USD/SGD"
    assert fx.primary_pair("JP") == "USD/JPY"
    assert fx.primary_pair("ZZ") == "EUR/USD"  # fallback
    assert fx.is_em_pair("USD/IDR") and fx.is_em_pair("USD/CNY")
    assert not fx.is_em_pair("EUR/USD") and not fx.is_em_pair("USD/JPY")


def test_relationships_only_for_treasury_clients(built):
    _, _, cpty, rels = built
    assert all(r["entity_id"] in cpty for r in rels)
    assert len(rels) == len({r["entity_id"] for r in rels})  # one per entity
    # treasury presence is ~18% of the book -> a few hundred relationships
    assert 250 <= len(rels) <= 700, len(rels)


def test_relationship_params_in_range(built):
    _, _, _, rels = built
    assert all(0.30 <= r["smbc_wallet_share"] <= 0.85 for r in rels)
    assert all(0.0 <= r["hedge_ratio"] <= 0.95 for r in rels)
    assert all(0.0 <= r["trading_share"] <= 0.60 for r in rels)
    assert all(r["fx_annual_turnover_usd"] >= 1_000_000.0 for r in rels)
    assert all(r["primary_ccy_pair"] == fx.primary_pair(r["booking_country"]) for r in rels)


def test_fis_trade_more_corporates_hedge_more(built):
    _, _, _, rels = built
    fi = [r for r in rels if r["is_fi"]]
    corp = [r for r in rels if not r["is_fi"]]
    if fi and corp:  # FIs exist in the universe
        assert sum(r["trading_share"] for r in fi) / len(fi) > sum(r["trading_share"] for r in corp) / len(corp)
        assert sum(r["hedge_ratio"] for r in corp) / len(corp) > sum(r["hedge_ratio"] for r in fi) / len(fi)


def test_deterministic(built):
    cfg, entities, cpty, rels = built
    again = fx.build_fx_relationships(cfg, entities, cpty)
    assert [r["cpty_id"] for r in again] == [r["cpty_id"] for r in rels]
    assert [r["fx_annual_turnover_usd"] for r in again] == [r["fx_annual_turnover_usd"] for r in rels]
