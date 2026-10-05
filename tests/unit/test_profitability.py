"""Relationship-economics tests (pure Python; the Spark aggregation is verified in-workspace)."""
import pytest

from smbc_genie_lib import profitability as prof
from smbc_genie_lib.config import load_config


@pytest.fixture(scope="module")
def cfg():
    return load_config()


def _agg(**over):
    base = dict(avg_drawn=35e6, avg_limit=50e6, wgt_margin_bps=220.0, lgd_blended=0.40,
                avg_dep_bal=40e6, casa_share=0.6, n_pay=400, xb_amt=20e6, n_acct=4,
                relationship_tier="Core", grade_avg=5.0)
    base.update(over)
    return base


def test_risk_model_monotonic(cfg):
    assert prof.risk_weight(1) < prof.risk_weight(5) < prof.risk_weight(10)
    assert prof.pd_of(1) < prof.pd_of(5) < prof.pd_of(10)
    assert prof.econ_capital_rate(1) < prof.econ_capital_rate(10)
    assert prof.risk_weight(1) == prof.RW_MIN and abs(prof.risk_weight(10) - prof.RW_MAX) < 1e-9


def test_grade_clamped(cfg):
    assert prof.pd_of(0) == prof.pd_of(1)
    assert prof.pd_of(99) == prof.pd_of(10)
    assert prof.risk_weight(7.4) == prof.risk_weight(7)  # rounds


def test_pnl_identity_and_bounds(cfg):
    p = prof.relationship_pnl(cfg, _agg())
    # revenue adds up across product lines
    assert abs((p["rev_lending"] + p["rev_deposits"] + p["rev_payments"] + p["rev_other"])
               - p["revenue_total"]) < 1.0
    # net profit = revenue - cost - EL
    assert abs(p["net_profit"] - (p["revenue_total"] - p["cost_to_serve"] - p["expected_loss"])) < 1.0
    assert abs(p["net_profit_annualised"] - p["net_profit"] * 4.0) < 1.0
    # returns are the annualised profit over the right denominators (stored ratios rounded to 4dp)
    assert abs(p["rorwa"] - p["net_profit_annualised"] / p["rwa"]) < 1e-3
    assert abs(p["raroc"] - p["net_profit_annualised"] / p["economic_capital"]) < 1e-3
    assert abs(p["roe"] - p["net_profit_annualised"] / p["book_equity"]) < 1e-3


def test_ead_uses_ccf_on_undrawn(cfg):
    p = prof.relationship_pnl(cfg, _agg(avg_drawn=20e6, avg_limit=100e6))
    assert abs(p["ead"] - (20e6 + prof.UNDRAWN_CCF * 80e6)) < 1.0


def test_weaker_grade_hurts_returns(cfg):
    strong = prof.relationship_pnl(cfg, _agg(grade_avg=2.0))
    weak = prof.relationship_pnl(cfg, _agg(grade_avg=9.0))
    assert weak["expected_loss"] > strong["expected_loss"]
    assert weak["rwa"] > strong["rwa"]
    assert weak["rorwa"] < strong["rorwa"]  # higher RWA + EL, same revenue -> lower return


def test_returns_winsorised(cfg):
    # a sub-scale, lightly-lent relationship with heavy cost -> deeply negative raw ratio, floored
    tiny = prof.relationship_pnl(cfg, _agg(avg_drawn=0.4e6, avg_limit=0.5e6, avg_dep_bal=0.2e6,
                                           n_pay=5, xb_amt=0, n_acct=2, relationship_tier="Strategic",
                                           grade_avg=8.0))
    assert prof.RETURN_FLOOR <= tiny["rorwa"] <= prof.RETURN_CAP
    assert tiny["below_rorwa_hurdle"]  # flag still fires from the raw (uncapped) ratio
    # a deposit-flooded, barely-lent relationship -> huge raw ratio, capped
    flood = prof.relationship_pnl(cfg, _agg(avg_drawn=0.5e6, avg_limit=1e6, avg_dep_bal=400e6, grade_avg=2.0))
    assert flood["rorwa"] <= prof.RETURN_CAP


def test_deposit_rich_relationship_clears_hurdle(cfg):
    # a deposit-heavy, lightly-drawn relationship should clear the RORWA hurdle
    p = prof.relationship_pnl(cfg, _agg(avg_drawn=10e6, avg_limit=20e6, avg_dep_bal=120e6, grade_avg=3.0))
    assert not p["below_rorwa_hurdle"], p["rorwa"]


def test_hurdle_flags_track_thresholds(cfg):
    p = prof.relationship_pnl(cfg, _agg())
    assert p["below_rorwa_hurdle"] == (p["rorwa"] < cfg.thresholds["rorwa_hurdle"])
    assert p["below_raroc_hurdle"] == (p["raroc"] < cfg.thresholds["raroc_hurdle"])
    assert p["below_roe_hurdle"] == (p["roe"] < cfg.thresholds["roe_hurdle"])


def test_deal_pricing_hurdle_margin(cfg):
    dp = prof.deal_pricing(cfg, facility_id="FAC-1", obligor_id="OBL-1", limit_usd=50e6,
                           base_utilisation=0.6, priced_margin_bps=200.0, security_type="Unsecured",
                           grade=6)
    assert dp["hurdle_margin_bps"] > 0
    assert dp["meets_standalone_hurdle"] == (dp["priced_margin_bps"] >= dp["hurdle_margin_bps"])
    assert dp["pricing_status"] in ("Above hurdle", "At hurdle", "Below hurdle")
    # a very high priced margin clears the standalone hurdle
    dp2 = prof.deal_pricing(cfg, facility_id="FAC-2", obligor_id="OBL-1", limit_usd=50e6,
                            base_utilisation=0.6, priced_margin_bps=2000.0, security_type="Cash", grade=2)
    assert dp2["meets_standalone_hurdle"] and dp2["pricing_status"] == "Above hurdle"


def test_deal_pricing_secured_lower_lgd(cfg):
    uns = prof.deal_pricing(cfg, facility_id="F", obligor_id="O", limit_usd=10e6, base_utilisation=0.5,
                            priced_margin_bps=150, security_type="Unsecured", grade=5)
    cash = prof.deal_pricing(cfg, facility_id="F", obligor_id="O", limit_usd=10e6, base_utilisation=0.5,
                             priced_margin_bps=150, security_type="Cash", grade=5)
    assert cash["lgd"] < uns["lgd"]
