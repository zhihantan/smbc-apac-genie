"""Cash-flow forecast model tests (pure Python; the Spark aggregation is verified in-workspace)."""
from statistics import mean

import pytest

from smbc_genie_lib import cashflow as cf
from smbc_genie_lib.config import load_config


@pytest.fixture(scope="module")
def cfg():
    return load_config()


def test_model_version_split(cfg):
    v2_from = cfg.realism["forecast_v2_from"]
    assert cf.model_version("2026-05-01", v2_from) == "v1"
    assert cf.model_version("2026-06-01", v2_from) == "v2"
    assert cf.model_version("2026-09-01", v2_from) == "v2"
    assert cf.model_version("2024-04-01", v2_from) == "v1"


def test_mape_for_versions(cfg):
    assert cf.mape_for("v1", cfg) == cfg.realism["forecast_mape_v1"]
    assert cf.mape_for("v2", cfg) == cfg.realism["forecast_mape_v2"]
    assert cf.mape_for("v2", cfg) < cf.mape_for("v1", cfg)  # the upgrade improves accuracy


def test_forecast_error_matches_mape(cfg):
    # over many uniforms, mean absolute pct error ~ the configured MAPE
    for mape in (0.18, 0.11):
        us = [(i + 0.5) / 2000 for i in range(2000)]
        errs = [cf.abs_pct_error(cf.forecast_value(100.0, u, mape), 100.0) for u in us]
        assert abs(mean(errs) - mape) < 0.01, (mape, mean(errs))


def test_forecast_value_unbiased_center(cfg):
    # at u=0.5 the forecast equals the actual (zero error)
    assert cf.forecast_value(500.0, 0.5, 0.18) == 500.0
    assert cf.abs_pct_error(500.0, 500.0) == 0.0


def test_v2_tighter_than_v1(cfg):
    us = [(i + 0.5) / 500 for i in range(500)]
    e1 = mean(cf.abs_pct_error(cf.forecast_value(100.0, u, 0.18), 100.0) for u in us)
    e2 = mean(cf.abs_pct_error(cf.forecast_value(100.0, u, 0.11), 100.0) for u in us)
    assert e2 < e1
