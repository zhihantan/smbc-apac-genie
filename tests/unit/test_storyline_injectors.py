"""Storyline injectors (pure Python): each scripted override lands on the right entity and value."""
import datetime as _dt

import pytest

from smbc_genie_lib import accounts, credit, fragment, health, onboarding, storylines as S, truth
from smbc_genie_lib import storyline_injectors as inj
from smbc_genie_lib.config import load_config

D = _dt.date


@pytest.fixture(scope="module")
def built():
    cfg = load_config()
    groups = truth.build_groups(cfg)
    entities = truth.build_entities(cfg, groups)
    _, xref = fragment.fragment_all(cfg, entities, [g["group_id"] for g in groups])

    def nondup(src):
        m = {}
        for x in xref:
            if x["source_system"] == src and not x["is_within_source_dup"]:
                m.setdefault(x["entity_id"], x["source_id"])
        return m

    obligor, custno = nondup("credit_obligor"), nondup("core_customer")
    facs = credit.build_facilities(cfg, entities, obligor)   # includes the scripted facilities
    hlut = {(r["entity_id"], r["month"]): r["health"] for r in health.build_health_monthly(cfg, entities)}
    accts = accounts.build_accounts(cfg, entities, custno, onboarding.new_client_cohort(cfg, entities, xref))
    return cfg, entities, obligor, facs, hlut, accts


def test_sunda_health_path_and_downgrade(built):
    cfg, entities, *_ = built
    lead = S.lead_entity(entities, "sunda")
    assert lead["booking_country"] == "ID"
    h25, g25 = inj.health_override(cfg, lead, D(2025, 6, 1))
    assert 0.6 <= h25 <= 0.64 and g25 == 7
    assert inj.health_override(cfg, lead, D(2026, 5, 1)) == (0.24, 7)
    assert inj.health_override(cfg, lead, D(2026, 6, 1)) == (0.19, 9)       # the June downgrade 7 -> 9
    sib = [e for e in S.group_entities(entities, "sunda") if not e["is_group_lead"]][0]
    assert inj.health_override(cfg, sib, D(2026, 6, 1))[1] == 7
    other = next(e for e in entities if not e["storyline_key"])
    assert inj.health_override(cfg, other, D(2026, 6, 1)) is None


def test_tanaka_dip_and_switch_off(built):
    cfg, entities, *_ = built
    tanaka = S.lead_entity(entities, "tanaka")
    assert inj.health_override(cfg, tanaka, D(2026, 6, 1)) is None
    assert inj.health_override(cfg, tanaka, D(2026, 8, 1)) == (0.42, 4)
    off = load_config()
    off.raw["storylines"] = {**off.raw["storylines"], S.TANAKA: False, S.SUNDA_EWS: False}
    assert inj.health_override(off, tanaka, D(2026, 8, 1)) is None
    assert inj.health_override(off, S.lead_entity(entities, "sunda"), D(2026, 6, 1)) is None


def test_statement_overrides(built):
    cfg, entities, *_ = built
    assert inj.statement_override(cfg, S.lead_entity(entities, "sunda"), 2025)["leverage"] == 4.6
    assert inj.statement_override(cfg, S.lead_entity(entities, "tanaka"), 2025) == {"leverage": 3.0, "ebitda_margin": 0.07}
    assert inj.statement_override(cfg, S.lead_entity(entities, "tanaka"), 2024) == {}


def test_scripted_facilities(built):
    cfg, entities, obligor, facs, *_ = built
    scripted = [f for f in facs if f.get("closed_date") is not None or f["facility_type"] == "Syndicated Loan"
                and f["entity_id"] in (S.lead_entity(entities, "kinokawa")["entity_id"], S.lead_entity(entities, "sunda")["entity_id"])]
    kin = [f for f in facs if f.get("closed_date")]
    assert len(kin) == 1 and kin[0]["limit_usd"] == 400_000_000 and kin[0]["closed_date"] == D(2026, 2, 16)
    ids = [f["facility_id"] for f in facs]
    assert len(set(ids)) == len(ids)
    tanaka_rcf = [f for f in facs if f["entity_id"] == S.lead_entity(entities, "tanaka")["entity_id"]
                  and f["guarantor_type"] == "Keepwell"]
    assert tanaka_rcf and tanaka_rcf[0]["maturity_date"] > D(2026, 9, 30)
    assert scripted


def test_utilisation_pins(built):
    cfg, entities, _, facs, *_ = built
    ov = inj.utilisation_overrides(cfg, entities, facs)
    sunda = [f for f in facs if f["entity_id"] == S.lead_entity(entities, "sunda")["entity_id"]]
    assert all(ov[(f["facility_id"], D(2026, 3, 31))] >= 0.95 for f in sunda if f["is_revolving"])
    assert all(ov[(f["facility_id"], D(2026, 3, 31))] == 0.78 for f in sunda if not f["is_revolving"])
    kin = next(f for f in facs if f.get("closed_date"))
    assert ov[(kin["facility_id"], D(2026, 2, 28))] == 0.0


def test_covenants_sunda_breach_and_blind_spot(built):
    cfg, entities, _, facs, hlut, _ = built
    tests = credit.build_covenant_tests(cfg, facs, hlut)
    adj, blind = inj.adjust_covenants(cfg, entities, facs, tests, hlut)
    sunda = S.lead_entity(entities, "sunda")["entity_id"]
    sfac = {f["facility_id"] for f in facs if f["entity_id"] == sunda}
    fy25 = [t for t in adj if t["facility_id"] in sfac and t["test_basis"] == "FY2025 Annual"]
    assert len(fy25) == 1 and fy25[0]["actual"] == 4.6 and fy25[0]["threshold"] == 4.0 and fy25[0]["breached"]
    assert fy25[0]["test_date"] == D(2026, 5, 15)
    latest = [t for t in adj if t["is_latest_test"] and t["covenant_type"] == "Net Debt/EBITDA"]
    low = {t["obligor_id"] for t in latest if 0.0 <= t["headroom_pct"] < 0.10 and not t["breached"]}
    breached = {t["obligor_id"] for t in latest if t["breached"]}
    assert len(blind) == 5 and low == set(blind)
    assert breached == {next(f["obligor_id"] for f in facs if f["entity_id"] == sunda)}
    # exactly one latest test per facility x covenant
    keys = [(t["facility_id"], t["covenant_type"]) for t in adj if t["is_latest_test"]]
    assert len(keys) == len(set(keys))


def test_hk_casa_calibration(built):
    cfg, entities, _, _, hlut, accts = built
    story = inj.deposit_story(cfg, entities, accts, hlut)
    trio = {e["entity_id"] for e in S.group_entities(entities, "hk_casa")}
    assert len(trio) == 3
    trio_accts = [a for a in accts if a["entity_id"] in trio]
    assert all(a["account_id"] in story for a in trio_accts)
    by_id = {e["entity_id"]: e for e in entities}

    def may(a, mult=1.0):
        if a["active_from"] > D(2026, 5, 31):
            return 0.0
        return a["base_balance_usd"] * mult * (0.4 + 0.8 * hlut.get((a["entity_id"], D(2026, 5, 1)), 0.6))

    hk = [a for a in accts if by_id[a["entity_id"]]["booking_country"] == "HK"]
    tot = sum(may(a, story.get(a["account_id"], {}).get("deposit_mult", 1.0)) for a in hk)
    casa = sum(may(a, story.get(a["account_id"], {}).get("deposit_mult", 1.0)) for a in hk if a["is_casa"])
    assert abs(tot - 900e6 / 0.13) / tot < 0.01 and abs(casa / tot - 0.62) < 0.005
    moved = sum(may(a, story[a["account_id"]]["deposit_mult"]) * story[a["account_id"]]["story_rate"]
                for a in trio_accts if a["is_casa"])
    assert abs(moved - 900e6) < 1e6
    sunda = S.lead_entity(entities, "sunda")["entity_id"]
    assert all(story[a["account_id"]]["story_kind"] == "sunda_outflow" for a in accts if a["entity_id"] == sunda)


def test_kinokawa_payments(built):
    cfg, entities, _, _, _, accts = built
    rows = inj.kinokawa_payments(cfg, entities, accts, 10_000_000)
    svc = [r for r in rows if r["payment_purpose"] == "Loan Service"]
    assert len(svc) == 9 and all(r["counterparty_bank_type"] == "Other Bank" for r in svc)
    corr = [r for r in rows if r["payment_purpose"] == "Trade Settlement"]
    h1_25 = sum(r["amount_usd"] for r in corr if D(2025, 4, 1) <= r["payment_date"] <= D(2025, 9, 30))
    h1_26 = sum(r["amount_usd"] for r in corr if D(2026, 4, 1) <= r["payment_date"] <= D(2026, 9, 30))
    assert 0.40 <= h1_26 / h1_25 - 1 <= 0.60
    assert all(r["counterparty_country"] == "VN" for r in corr)
    assert len({r["payment_id"] for r in rows}) == len(rows)
