"""Latent credit-health backbone (DECISIONS D08; brief §6.3 realism).

Per-entity monthly health in [0,1] from an AR(1) process plus a shared industry-cycle signal:
coal/oil & shipping turn negative through 2025-26, renewables/semis turn positive, others drift
mildly. Health drives balances, utilisation, deposit behaviour, covenant headroom, DPD, ratings
and news tone downstream, so the correlations the brief asks for ("signals precede downgrades")
are real. Pure Python/deterministic; written to ops.synthetic_entity_health_monthly.
"""
from __future__ import annotations

import datetime as _dt
import math
from typing import Dict, List

from . import rng, storyline_injectors

NEGATIVE_SUBSECTORS = {"Oil, Gas & Coal", "Shipping", "Petrochemicals", "Mining", "Coal"}
POSITIVE_SUBSECTORS = {"Renewables", "Semiconductors", "Power Generation", "Electronic Devices"}


def month_starts(history_start: str, end_year: int = 2027, end_month: int = 3) -> List[_dt.date]:
    start = _dt.date.fromisoformat(history_start).replace(day=1)
    out = []
    y, m = start.year, start.month
    while (y, m) <= (end_year, end_month):
        out.append(_dt.date(y, m, 1))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def _ramp(month: _dt.date) -> float:
    """0 before 2025, ramping to 1 by mid-2026 (the stress build-up window)."""
    idx = (month.year - 2025) * 12 + (month.month - 1)  # months since Jan 2025
    if idx <= 0:
        return 0.0
    return min(1.0, idx / 18.0)


def sector_cycle(subsector: str, month: _dt.date, seed: int) -> float:
    """Shared monthly cycle for a subsector in [-1, 1].

    A smooth directional trend (negative sectors decline, positive sectors improve through
    2025-26) dominates a smaller seasonal wobble; the trend is deliberately strong so the
    sector downturn is visible in health, ratings and EWS by the as-of date — see HEALTH_COEFF.
    """
    idx = (month.year - 2023) * 12 + month.month
    phase = rng.unit(seed, "cycphase", subsector) * 2 * math.pi
    seasonal = 0.10 * math.sin(idx / 6.0 + phase)
    trend = 0.0
    if subsector in NEGATIVE_SUBSECTORS:
        trend = -0.85 * _ramp(month)
    elif subsector in POSITIVE_SUBSECTORS:
        trend = 0.65 * _ramp(month)
    val = trend + seasonal + (rng.unit(seed, "cycnoise", subsector, idx) - 0.5) * 0.06
    return max(-1.0, min(1.0, val))


# How strongly the sector cycle moves latent health. At 0.40, a mid-beta name in a declining
# sector loses ~0.25-0.35 of health by 2026 (clearly stressed), a high-beta name more; positive
# sectors gain similarly. Kept below the base-health spread so names don't all converge.
HEALTH_COEFF = 0.40


def build_health_monthly(cfg, entities: List[Dict]) -> List[Dict]:
    seed = cfg.random_seed
    months = month_starts(cfg.history_start)
    # cache sector cycles
    cyc_cache: Dict[tuple, float] = {}
    rows: List[Dict] = []
    for e in entities:
        eid = e["entity_id"]
        subsector = e["industry_subsector"]
        base = e["health_base"]
        vol = e["health_vol"]
        beta = e["industry_cycle_beta"]
        prev = 0.0
        for i, m in enumerate(months):
            key = (subsector, m)
            cyc = cyc_cache.get(key)
            if cyc is None:
                cyc = sector_cycle(subsector, m, seed)
                cyc_cache[key] = cyc
            shock = rng.normal(seed, 0.0, vol, "hshock", eid, i)
            prev = 0.6 * prev + 0.4 * shock
            raw = base + HEALTH_COEFF * beta * cyc + prev
            health = max(0.02, min(0.99, raw))
            grade = int(max(1, min(10, round(1 + (1 - health) * 9))))
            scripted = storyline_injectors.health_override(cfg, e, m)   # Sunda cascade, Tanaka dip
            if scripted:
                health, grade = scripted
            rows.append({
                "entity_id": eid,
                "month": m,
                "health": round(health, 4),
                "industry_cycle": round(cyc, 4),
                "grade_effective": grade,
            })
    return rows
