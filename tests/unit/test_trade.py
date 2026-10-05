"""Trade-finance & SCF truth tests (pure Python; brief §5.8, storyline 6 VN/IN import-LC surge)."""
import datetime as _dt
from collections import Counter, defaultdict

import pytest

from smbc_genie_lib import fiscal, fragment, health, names, storylines, trade, truth
from smbc_genie_lib.config import load_config

D = _dt.date


@pytest.fixture(scope="module")
def built():
    cfg = load_config()
    groups = truth.build_groups(cfg)
    entities = truth.build_entities(cfg, groups)
    gids = [g["group_id"] for g in groups]
    _, xref = fragment.fragment_all(cfg, entities, gids)
    party = {}
    for x in xref:
        if x["source_system"] == "trade_party" and not x["is_within_source_dup"]:
            party.setdefault(x["entity_id"], x["source_id"])
    rels = trade.build_trade_relationships(cfg, entities, party)
    progs = trade.build_scf_programmes(cfg, rels)
    sups = trade.build_scf_suppliers(cfg, progs)
    return cfg, entities, party, rels, progs, sups


@pytest.fixture(scope="module")
def txns(built):
    cfg, entities, _, rels, _, _ = built
    hl = trade.health_lookup(health.build_health_monthly(cfg, entities))
    return trade.build_trade_txns(cfg, entities, rels, hl)


def _half(d):
    return fiscal.fiscal_half_label(d)


def test_products_statuses_sum_to_one():
    assert abs(sum(w for _, w in trade.TRADE_PRODUCTS) - 1.0) < 1e-9
    assert abs(sum(w for _, w in trade.TRADE_STATUS) - 1.0) < 1e-9
    assert set(p for p, _ in trade.TRADE_PRODUCTS) == set(trade.FEE_BPS)


def test_relationships_only_for_trade_parties(built):
    _, _, party, rels, _, _ = built
    assert all(r["entity_id"] in party for r in rels)
    assert all(0.30 <= r["import_share"] <= 0.70 for r in rels)
    assert all(r["trade_annual_turnover_usd"] >= 1_000_000.0 for r in rels)
    assert 300 <= len(rels) <= 800, len(rels)  # ~22% treasury/trade presence


def test_vn_in_corridors_weighted_up(built):
    # Vietnam & India are elevated vs smaller corridors in the base distribution
    w = dict(trade.CORRIDORS)
    assert w["VN"] > w["AU"] and w["IN"] > w["TH"]


def test_scf_programmes_anchored_by_corporates(built):
    cfg, _, _, rels, progs, _ = built
    assert len(progs) >= 1
    anchor_ids = {r["party_id"] for r in rels if r["is_corporate"]}
    assert all(p["anchor_party_id"] in anchor_ids for p in progs)
    assert all(p["drawn_usd"] <= p["limit_usd"] for p in progs)
    assert all(0.28 <= p["utilisation"] <= 0.94 for p in progs)
    # brief §5.8 Q3: some programmes run above 85% and some below 40% utilisation at as-of
    assert any(p["utilisation"] > 0.85 for p in progs) and any(p["utilisation"] < 0.40 for p in progs)
    # anchors are the largest by turnover
    by_turn = sorted((r for r in rels if r["is_corporate"]),
                     key=lambda r: r["trade_annual_turnover_usd"], reverse=True)
    assert set(p["anchor_entity_id"] for p in progs) <= {r["entity_id"] for r in by_turn[:len(progs)]}


def test_scf_suppliers_reference_programmes_and_clean_names(built):
    _, _, _, _, progs, sups = built
    pids = {p["programme_id"] for p in progs}
    assert all(s["programme_id"] in pids for s in sups)
    assert all(s["supplier_country"] in trade.SUPPLIER_COUNTRIES for s in sups)
    # onboarded suppliers carry financing, prospective ones do not
    assert all(s["financed_amount_usd"] > 0 for s in sups if s["is_onboarded"])
    assert all(s["financed_amount_usd"] == 0 for s in sups if not s["is_onboarded"])
    for s in sups[:50]:
        names.assert_clean(s["supplier_name"])  # no real-company names


def test_onboarding_leaves_a_pipeline(built):
    _, _, _, _, _, sups = built
    onb = sum(s["is_onboarded"] for s in sups) / len(sups)
    assert 0.45 <= onb <= 0.72, onb  # ~60% onboarded, the rest a growth pipeline


def test_deterministic(built):
    cfg, entities, party, rels, progs, sups = built
    r2 = trade.build_trade_relationships(cfg, entities, party)
    p2 = trade.build_scf_programmes(cfg, r2)
    s2 = trade.build_scf_suppliers(cfg, p2)
    assert [r["party_id"] for r in r2] == [r["party_id"] for r in rels]
    assert [p["programme_id"] for p in p2] == [p["programme_id"] for p in progs]
    assert [s["supplier_name"] for s in s2] == [s["supplier_name"] for s in sups]


def test_supplier_financing_reconciles_to_programmes(built):
    cfg, _, _, _, progs, sups = built
    as_of = D.fromisoformat(cfg.as_of_date)
    for p in progs:  # financed outstanding per supplier sums to the programme's drawn amount
        fin = sum(s["financed_amount_usd"] for s in sups if s["programme_id"] == p["programme_id"])
        assert abs(fin - p["drawn_usd"]) < 1.0, p["programme_id"]
    for s in sups:
        assert (s["onboarded_date"] is not None) == s["is_onboarded"]
        if s["is_onboarded"]:
            assert s["onboarded_date"] <= as_of - _dt.timedelta(days=90)


# ---- instruments (bronze.trade_finance_txn) -------------------------------------------------
def test_txns_volume_columns_and_keys(built, txns):
    cfg, _, party, _, _, _ = built
    window = [t for t in txns if t["txn_date"] >= trade.TXN_START]
    assert len(window) == max(2000, round(trade.TXNS_AT_SCALE_1 * cfg.scale))
    assert all(list(t) == trade.TXN_COLUMNS for t in txns[:20])  # no truth ids in bronze
    assert len({t["txn_id"] for t in txns}) == len(txns)
    assert {t["party_id"] for t in txns} <= set(party.values())
    as_of = D.fromisoformat(cfg.as_of_date)
    assert all(trade.LEGACY_START <= t["txn_date"] < as_of for t in txns)
    legacy = [t for t in txns if t["txn_date"] < trade.TXN_START]  # FY2023 book still open on TXN_START
    assert 0.05 <= len(legacy) / len(window) <= 0.30
    assert all(t["closed_date"] is None or t["closed_date"] >= trade.TXN_START for t in legacy)
    assert all(t["hs_chapter"] == trade.HS_CHAPTER[t["commodity"]] for t in txns)
    assert all(t["origin_country"] != t["destination_country"] for t in txns)


def test_lifecycle_status_is_consistent(built, txns):
    cfg = built[0]
    as_of = D.fromisoformat(cfg.as_of_date)
    mix = Counter(t["status"] for t in txns)
    assert set(mix) == {s for s, _ in trade.TRADE_STATUS}
    for s, w in trade.TRADE_STATUS:  # the documented as-of mix holds
        assert abs(mix[s] / len(txns) - w) < 0.06, (s, mix[s] / len(txns))
    for t in txns:
        closed = t["status"] in ("Settled", "Expired")
        assert (t["closed_date"] is not None) == closed
        assert (t["outstanding_usd"] == 0.0) == closed or t["outstanding_usd"] < 0.02
        if closed:
            assert t["txn_date"] <= t["closed_date"] <= as_of


def test_japanese_corporates_price_tighter(built, txns):
    _, entities, party, _, _, _ = built
    seg = {party[e["entity_id"]]: e["segment"] for e in entities if e["entity_id"] in party}
    by = defaultdict(list)
    for t in txns:
        if t["product_type"] == "Import LC":
            by[seg[t["party_id"]] == "Japanese Corporate"].append(t["fee_bps"])
    assert sum(by[True]) / len(by[True]) < 0.9 * sum(by[False]) / len(by[False])


def test_vn_in_surge_is_exact(txns):
    m = trade.surge_measures(txns)
    assert abs(m["yoy_count"] - storylines.VN_IN["yoy_growth"]) < 0.01
    assert abs(m["yoy_usd"] - storylines.VN_IN["yoy_growth"]) < 0.001
    assert 0.42 <= m["sg_share_count"] <= 0.48 and abs(m["sg_share_usd"] - 0.45) < 0.001
    # SG-booked LCs ship to the group's VN / IN entities: destination differs from booking
    sel = [t for t in txns if trade._is_surge(t["product_type"], t["destination_country"], t["origin_country"],
                                              t["commodity"])]
    assert all(t["booking_location"] in ("SG", t["destination_country"]) for t in sel)


def test_surge_corridors_lead_import_lc_growth(txns):
    cells, clients = defaultdict(lambda: [0.0, 0.0]), defaultdict(set)
    for t in txns:
        if t["product_type"] != "Import LC":
            continue
        h = _half(t["txn_date"])
        if h in ("FY2025-H1", "FY2026-H1"):
            k = (t["origin_country"], t["destination_country"])
            cells[k][h == "FY2026-H1"] += t["amount_usd"]
            if h == "FY2025-H1":
                clients[k].add(t["party_id"])
    # broad-based corridors only (>= 10 clients); one- or two-client corridors are noise
    growth = {k: v[1] / v[0] - 1 for k, v in cells.items() if v[0] > 0 and len(clients[k]) >= 10}
    story = {(o, d) for o in storylines.VN_IN["source_countries"] for d in storylines.VN_IN["import_countries"]}
    top = sorted(growth, key=growth.get, reverse=True)[:len(story)]
    assert set(top) == story, top
    # every storyline corridor grows ~40% (CN->VN carries Kinokawa VN's +50% book): PLAN §5.4 band 35-45%
    assert all(abs(growth[k] - storylines.VN_IN["yoy_growth"]) <= 0.05 for k in story), growth


def test_kinokawa_cn_vn_corridor_grows_50pct(built, txns):
    _, entities, party, _, _, _ = built
    kv = party[storylines.entity_in(entities, "kinokawa", "VN")["entity_id"]]
    usd = defaultdict(float)
    for t in txns:
        if t["party_id"] == kv and (t["origin_country"], t["destination_country"]) == ("CN", "VN"):
            usd[_half(t["txn_date"])] += t["amount_usd"]
    assert abs(usd["FY2026-H1"] / usd["FY2025-H1"] - 1 - storylines.KINOKAWA_SCRIPT["corridor_growth"]) < 0.001


def test_base_book_is_steady(txns):
    n = Counter(_half(t["txn_date"]) for t in txns)
    assert 0.0 <= n["FY2026-H1"] / n["FY2025-H1"] - 1 < 0.10  # the surge stands out against the base


def test_txns_deterministic(built, txns):
    cfg, entities, _, rels, _, _ = built
    again = trade.build_trade_txns(cfg, entities, rels, trade.health_lookup(health.build_health_monthly(cfg, entities)))
    assert again == txns
