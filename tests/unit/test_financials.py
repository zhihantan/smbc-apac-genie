"""Spread-financials generator tests (coherence + ratios)."""
from collections import defaultdict

import pytest

from smbc_genie_lib import financials, fragment, health, reference, truth
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
    hlut = {(r["entity_id"], r["month"]): r["health"] for r in health.build_health_monthly(cfg, entities)}
    lines, ratios = financials.build_statements(cfg, entities, hlut, obligor)
    return cfg, entities, obligor, lines, ratios


def test_lines_per_borrower(built):
    _, _, obligor, lines, _ = built
    per = defaultdict(set)
    for ln in lines:
        per[ln["obligor_id"]].add((ln["fiscal_year"], ln["statement_type"], ln["line_item"]))
    # 3 FYs x (11 P&L + 11 BS + 4 CF) = 78 distinct lines
    assert all(len(v) == 78 for v in per.values())
    assert set(per.keys()) == set(obligor.values())


def test_balance_sheet_balances(built):
    _, _, _, lines, _ = built
    bs = defaultdict(dict)
    for ln in lines:
        if ln["statement_type"] == "Balance Sheet":
            bs[(ln["obligor_id"], ln["fiscal_year"])][ln["line_item"]] = ln["amount_usd"]
    for key, d in list(bs.items())[:500]:
        lhs = d["Total Assets"]
        rhs = d["Total Debt"] + d["Payables"] + d["Equity"]
        assert abs(lhs - rhs) <= 1.0, (key, lhs, rhs)


def test_leverage_and_margins_sane(built):
    _, _, _, _, ratios = built
    lev = [r["ratio_value"] for r in ratios if r["ratio_name"] == "Net Debt/EBITDA"]
    # base book stays 1.3-4.2x; the only name above is Sunda's scripted FY2025 4.6x (storyline 1)
    assert all(1.3 <= v <= 4.2 for v in lev if v != 4.6)
    assert sum(v == 4.6 for v in lev) == 1
    nm = [r["ratio_value"] for r in ratios if r["ratio_name"] == "Net Margin"]
    assert all(-0.5 <= v <= 0.6 for v in nm)
    roe = [r["ratio_value"] for r in ratios if r["ratio_name"] == "ROE"]
    assert all(-0.4 <= v <= 0.45 for v in roe), (min(roe), max(roe))  # realistic, not ~50%


def test_some_fy2025_unspread(built):
    _, _, obligor, lines, _ = built
    unspread = {ln["obligor_id"] for ln in lines if ln["fiscal_year"] == 2025 and not ln["is_spread"]}
    frac = len(unspread) / len(obligor)
    assert 0.03 <= frac <= 0.20, frac  # ~10% spreading backlog


def test_yoy_growth_null_first_year(built):
    _, _, _, _, ratios = built
    g = [r for r in ratios if r["ratio_name"] == "Revenue YoY Growth"]
    assert all(r["ratio_value"] is None for r in g if r["fiscal_year"] == 2023)
    assert all(r["ratio_value"] is not None for r in g if r["fiscal_year"] == 2025)


def test_peer_group_matches_reference(built):
    cfg, entities, obligor, _, ratios = built
    peers = {p["industry_subsector"]: p["peer_group_id"] for p in reference.build_industry_peers(cfg)}
    e = next(e for e in entities if e["entity_id"] in obligor)
    rr = next(r for r in ratios if r["obligor_id"] == obligor[e["entity_id"]])
    assert rr["peer_group_id"] == peers[e["industry_subsector"]]


def test_jc_fiscal_year_end_is_march(built):
    _, entities, _, lines, _ = built
    jc = next(e for e in entities if e["segment"] == "Japanese Corporate")
    # not all JC are borrowers; just assert the fye helper
    assert financials.fye(jc, 2025).month == 3 and financials.fye(jc, 2025).year == 2026
