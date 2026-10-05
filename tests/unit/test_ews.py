"""EWS scoring + trigger tests (pure Python; the Spark fan-out is verified in-workspace)."""
import pytest

from smbc_genie_lib import ews
from smbc_genie_lib.config import load_config


@pytest.fixture(scope="module")
def cfg():
    return load_config()


# a comfortable, healthy obligor-month
STRONG = dict(health=0.85, util_max=0.55, cov_headroom_min=0.35, cov_breached=False,
              deposit_mom=0.02, leakage=0.15)
# a severely deteriorating obligor-month (Sunda-like)
WEAK = dict(health=0.15, util_max=1.0, cov_headroom_min=-0.05, cov_breached=True,
            deposit_mom=-0.30, leakage=0.65)


def test_band_thresholds(cfg):
    assert ews.band(cfg, 10.0) == "Green"
    assert ews.band(cfg, 39.9) == "Green"
    assert ews.band(cfg, 40.0) == "Amber"
    assert ews.band(cfg, 69.9) == "Amber"
    assert ews.band(cfg, 70.0) == "Red"
    assert ews.band(cfg, 100.0) == "Red"


def test_strong_is_green_weak_is_red(cfg):
    strong = ews.score_components(cfg, **STRONG)
    weak = ews.score_components(cfg, **WEAK)
    assert strong["band"] == "Green", strong
    assert weak["band"] == "Red", weak
    assert weak["composite_score"] > strong["composite_score"]


def test_components_bounded(cfg):
    for metrics in (STRONG, WEAK, dict(health=0.5, util_max=0.85, cov_headroom_min=0.08,
                                       cov_breached=False, deposit_mom=-0.12, leakage=0.5)):
        sc = ews.score_components(cfg, **metrics)
        for k in ("composite_score", "credit_component", "liquidity_component",
                  "behavioural_component", "external_component"):
            assert 0.0 <= sc[k] <= 100.0, (k, sc[k])


def test_composite_monotonic_in_health(cfg):
    base = dict(util_max=0.6, cov_headroom_min=0.3, cov_breached=False, deposit_mom=0.0, leakage=0.2)
    scores = [ews.score_components(cfg, health=h, **base)["composite_score"]
              for h in (0.9, 0.7, 0.5, 0.3, 0.1)]
    assert scores == sorted(scores), scores  # lower health -> higher (worse) score


def test_external_component_zero_until_news(cfg):
    assert ews.score_components(cfg, **WEAK)["external_component"] == 0.0


def test_signals_fire_on_weak(cfg):
    m = dict(WEAK, grade_effective=8, health_3m_ago=0.40, grade_3m_ago=5)
    fired = {s["trigger_code"] for s in ews.signals_for(cfg, m)}
    assert {"UTIL_HIGH", "COV_BREACH", "DEPOSIT_OUTFLOW_SEVERE", "WALLET_LEAKAGE",
            "HEALTH_DECLINE", "GRADE_DOWNGRADE"} <= fired, fired


def test_signals_quiet_on_strong(cfg):
    m = dict(STRONG, grade_effective=3, health_3m_ago=0.84, grade_3m_ago=3)
    assert ews.signals_for(cfg, m) == []


def test_util_elevated_vs_high(cfg):
    base = dict(cov_headroom_min=0.3, cov_breached=False, deposit_mom=0.0, leakage=0.1,
                grade_effective=4, health=0.6, health_3m_ago=0.6, grade_3m_ago=4)
    hi = {s["trigger_code"] for s in ews.signals_for(cfg, dict(base, util_max=0.95))}
    el = {s["trigger_code"] for s in ews.signals_for(cfg, dict(base, util_max=0.84))}
    assert "UTIL_HIGH" in hi and "UTIL_ELEVATED" not in hi
    assert "UTIL_ELEVATED" in el and "UTIL_HIGH" not in el


def test_signal_values_rounded_and_categorised(cfg):
    m = dict(WEAK, grade_effective=8, health_3m_ago=0.40, grade_3m_ago=5)
    cats = {t[0]: t[2] for t in ews.TRIGGERS}
    for s in ews.signals_for(cfg, m):
        assert s["category"] == cats[s["trigger_code"]]
        assert s["points_contributed"] >= 0.0


def test_trigger_catalog_shape(cfg):
    cat = ews.build_trigger_catalog(cfg)
    codes = {t["trigger_code"] for t in cat}
    assert {"UTIL_HIGH", "COV_BREACH", "DEPOSIT_OUTFLOW", "WALLET_LEAKAGE", "HEALTH_DECLINE",
            "GRADE_DOWNGRADE", "NEWS_NEGATIVE"} <= codes
    assert {t["category"] for t in cat} == {"Credit", "Liquidity", "Behavioural", "External"}
    # the news feed now drives NEWS_NEGATIVE and core_dpd the DPD triggers; PAYMENT_DELAY stays catalogued only
    inactive = {t["trigger_code"] for t in cat if not t["is_active"]}
    assert inactive == {"PAYMENT_DELAY"}
    assert {"DPD_15", "DPD_30"} <= codes


def test_news_external_component_and_dpd_signals(cfg):
    base = dict(health=0.7, util_max=0.5, cov_headroom_min=0.3, cov_breached=False, deposit_mom=0.0, leakage=0.1)
    quiet = ews.score_components(cfg, **base)
    noisy = ews.score_components(cfg, **base, news_sentiment=-0.6)
    assert quiet["external_component"] == 0.0 and noisy["external_component"] == 100.0
    assert quiet["composite_score"] == noisy["composite_score"]   # informational: calibration unchanged
    m = {**base, "grade_effective": 4, "health_3m_ago": None, "grade_3m_ago": None,
         "dpd_max": 31, "news_sentiment": -0.45, "news_items": 3}
    codes = {s["trigger_code"] for s in ews.signals_for(cfg, m)}
    assert {"DPD_30", "NEWS_NEGATIVE"} <= codes and "DPD_15" not in codes


def test_parent_support_overrides(cfg):
    import datetime as dt
    rows = [{"month": dt.date(2026, m, 1), "band": "Amber" if m >= 7 else "Green"} for m in range(4, 10)]
    out = ews.parent_support_overrides(cfg, "CRD-X", rows, {"2026-08-13": True}, "JPSL-1")
    assert [o["override_date"] for o in out] == ["2026-07-06", "2026-08-13"]
    assert all(o["override_band"] == "Green" and o["rationale"].startswith("Parent support confirmed (JP data)")
               for o in out)
    assert [o["source_data_stale"] for o in out] == [False, True]
    assert ews.parent_support_overrides(cfg, "CRD-X", [r for r in rows if r["band"] == "Green"], {}, None) == []


def test_lead_driver_picks_dominant(cfg):
    row = dict(credit_component=70.0, liquidity_component=10.0, behavioural_component=5.0,
               external_component=0.0)
    assert ews.lead_driver(row)[0] == "Credit"
    row2 = dict(credit_component=10.0, liquidity_component=55.0, behavioural_component=5.0,
                external_component=0.0)
    assert ews.lead_driver(row2)[0] == "Liquidity"


def test_rm_code_format(cfg):
    code = ews.rm_code(cfg.random_seed, "OBL-000123")
    assert code.startswith("RM") and len(code) == 5 and 1 <= int(code[2:]) <= 120


def test_overrides_deterministic_and_rated(cfg):
    rows = [dict(obligor_id=f"OBL-{i:05d}", band="Amber" if i % 3 else "Green",
                 composite_score=45.0 if i % 3 else 12.0, credit_component=50.0 if i % 3 else 12.0,
                 liquidity_component=10.0, behavioural_component=5.0, external_component=0.0)
            for i in range(400)]
    a = ews.build_overrides(cfg, rows)
    b = ews.build_overrides(cfg, rows)
    assert [o["override_id"] for o in a] == [o["override_id"] for o in b]  # deterministic
    assert 0.0 < len(a) / len(rows) < 0.08  # ~3% rate
    assert all(o["direction"] in ("Affirm", "Soften", "Escalate") for o in a)
    # a Green system band can only produce an Escalate override
    greens = [o for o in a if o["system_band"] == "Green"]
    assert all(o["direction"] == "Escalate" and o["override_band"] == "Amber" for o in greens)
