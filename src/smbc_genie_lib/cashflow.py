"""Cash-flow actuals + forecasting (brief §5; Cash-flow Genie space; model-upgrade storyline).

Monthly operating cash-flow actuals are derived from the payments hub (inbound collections vs
outbound disbursements). On top of them sits a treasury collections forecast whose accuracy
improves at a model upgrade: a v1 model (MAPE ~0.18) is replaced by v2 (~0.11) from
realism.forecast_v2_from (2026-06-01) — the visible before/after of the cash-flow-model-upgrade
storyline. The error model and version split are pure Python (unit-tested); the heavy monthly
aggregation and the forecast fan-out run in Spark. Forecasts target gross inflows (always
positive) so MAPE is well-defined.
"""
from __future__ import annotations

import datetime as _dt


def _as_date(x) -> _dt.date:
    return x if isinstance(x, _dt.date) else _dt.date.fromisoformat(str(x)[:10])


def model_version(target_month, v2_from) -> str:
    """'v2' from the upgrade date onwards, else 'v1'."""
    return "v2" if _as_date(target_month) >= _as_date(v2_from) else "v1"


def mape_for(version: str, cfg) -> float:
    r = cfg.realism
    return float(r.get("forecast_mape_v2", 0.11)) if version == "v2" else float(r.get("forecast_mape_v1", 0.18))


def forecast_value(actual: float, u: float, mape: float) -> float:
    """Deterministic backtest forecast: actual scaled by a signed error whose expected magnitude
    equals `mape` (u is a [0,1) uniform; E|4*mape*(u-0.5)| = mape)."""
    return actual * (1.0 + 4.0 * mape * (u - 0.5))


def abs_pct_error(forecast: float, actual: float) -> float:
    return abs(forecast - actual) / abs(actual) if actual else 0.0
