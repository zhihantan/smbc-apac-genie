"""CRM generator tests (pure Python)."""
import pytest

from smbc_genie_lib import crm, fragment, truth
from smbc_genie_lib.config import load_config


@pytest.fixture(scope="module")
def built():
    cfg = load_config()
    groups = truth.build_groups(cfg)
    entities = truth.build_entities(cfg, groups)
    gids = [g["group_id"] for g in groups]
    _, xref = fragment.fragment_all(cfg, entities, gids)

    def nondup(src):
        m = {}
        for x in xref:
            if x["source_system"] == src and not x["is_within_source_dup"]:
                m.setdefault(x["entity_id"], x["source_id"])
        return m

    crm_map = nondup("crm_account")
    opps = crm.build_opportunities(cfg, entities, crm_map)
    plans = crm.build_account_plans(cfg, entities, crm_map)
    nbp = crm.build_nbp(cfg, entities, crm_map, set(nondup("credit_obligor")),
                        set(nondup("tsy_counterparty")), set(nondup("trade_party")))
    return cfg, entities, crm_map, opps, plans, nbp


def test_opportunities_reference_crm_and_valid_stage(built):
    _, _, crm_map, opps, _, _ = built
    crm_ids = set(crm_map.values())
    assert all(o["crm_account_id"] in crm_ids for o in opps)
    stages = {s for s, _ in crm.OPP_STAGES}
    assert all(o["stage"] in stages for o in opps)
    assert all(0.0 <= o["win_probability"] <= 1.0 for o in opps)
    assert all(o["owner_rm"].startswith("RM") for o in opps)


def test_won_lost_probabilities(built):
    _, _, _, opps, _, _ = built
    for o in opps:
        if o["stage"] == "Won":
            assert o["win_probability"] == 1.0 and o["status"] == "Won"
        if o["stage"] == "Lost":
            assert o["win_probability"] == 0.0 and o["status"] == "Lost"


def test_account_plans_optimism_in_band(built):
    cfg, _, crm_map, _, plans, _ = built
    lo, hi = cfg.realism["plan_optimism_min"], cfg.realism["plan_optimism_max"]
    assert all(lo <= p["plan_optimism"] <= hi for p in plans)
    # planned always above actual by the optimism factor
    assert all(p["planned_revenue_usd"] > p["actual_revenue_usd"] for p in plans)
    assert {p["fiscal_year"] for p in plans} == {2025, 2026}
    assert len(plans) == 2 * len(crm_map)


def test_nbp_ranked_and_grounded(built):
    _, _, crm_map, _, _, nbp = built
    crm_ids = set(crm_map.values())
    assert all(n["crm_account_id"] in crm_ids for n in nbp)
    assert all(1 <= n["rank"] <= 3 for n in nbp)
    assert all(0.0 <= n["propensity_score"] <= 1.0 for n in nbp)
    # rank 1 within an account has the highest propensity
    by_acct = {}
    for n in nbp:
        by_acct.setdefault(n["crm_account_id"], []).append(n)
    for recs in by_acct.values():
        recs_sorted = sorted(recs, key=lambda r: r["rank"])
        assert recs_sorted[0]["propensity_score"] == max(r["propensity_score"] for r in recs)


def test_deterministic(built):
    cfg, entities, crm_map, opps, _, _ = built
    again = crm.build_opportunities(cfg, entities, crm_map)
    assert [o["opportunity_id"] for o in again] == [o["opportunity_id"] for o in opps]
