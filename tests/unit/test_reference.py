"""Reference-dimension tests (calendar, FX, booking entities)."""
import datetime as dt

import pytest

from smbc_genie_lib import reference
from smbc_genie_lib.config import load_config


@pytest.fixture(scope="module")
def cfg():
    return load_config()


def test_calendar_span_and_fiscal(cfg):
    cal = reference.build_calendar(cfg)
    assert cal[0]["date"] == dt.date(2023, 4, 1)
    assert cal[-1]["date"] == dt.date(2027, 3, 31)
    # latest closed month = Sep 2026 -> 30 days flagged
    assert sum(1 for r in cal if r["is_latest_closed_month"]) == 30
    sep30 = next(r for r in cal if r["date"] == dt.date(2026, 9, 30))
    assert sep30["fiscal_year_label"] == "FY2026" and sep30["fiscal_quarter"] == 2
    assert sep30["is_month_end"] and sep30["days_before_as_of"] == 0


def test_fx_usd_is_one_and_deterministic(cfg):
    fx = reference.build_fx_daily(cfg)
    usd = [r for r in fx if r["currency_code"] == "USD"]
    assert usd and all(r["rate_per_usd"] == 1.0 for r in usd)
    # deterministic
    fx2 = reference.build_fx_daily(cfg)
    assert [r["rate_per_usd"] for r in fx[:50]] == [r["rate_per_usd"] for r in fx2[:50]]
    # a non-USD rate stays near its base (within +/-15%)
    sgd = [r["rate_per_usd"] for r in fx if r["currency_code"] == "SGD"]
    assert all(1.35 * 0.85 <= r <= 1.35 * 1.15 for r in sgd)


def test_booking_entities_count(cfg):
    be = reference.build_booking_entities(cfg)
    assert sum(1 for r in be if r["is_apac"]) == 13
    assert {"JP", "EMEA", "AMER"} <= {r["booking_entity_id"] for r in be}
    assert sum(1 for r in be if r["is_hub"]) == 1  # Singapore


def test_peer_groups(cfg):
    pg = reference.build_industry_peers(cfg)
    assert 30 <= len(pg) <= 50
    assert any(p["is_carbon_intensive"] for p in pg)
