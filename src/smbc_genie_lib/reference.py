"""Reference dimensions (brief §4, §6.1 ref_* ). Deterministic, pure Python.

Calendar (Japanese fiscal attributes), currencies + daily FX to USD, countries, booking entities
(13 APAC + 3 provider regions) and industry / peer groups. Monetary realism (random-walk FX) is
smooth and reproducible from the seed. dim_date / fx_rate_daily are promoted to gold in Phase 5;
here they land as the bronze/ref source.
"""
from __future__ import annotations

import datetime as _dt
import math
from typing import Dict, List

from . import fiscal, rng
from .names import CITY_BY_CC
from .truth import INDUSTRY_MAP

# Base FX: units of local currency per 1 USD (illustrative).
FX_BASE: Dict[str, float] = {
    "USD": 1.0, "SGD": 1.35, "JPY": 150.0, "HKD": 7.8, "AUD": 1.5, "INR": 83.0, "IDR": 15800.0,
    "THB": 36.0, "MYR": 4.7, "VND": 25000.0, "CNY": 7.2, "PHP": 58.0, "KRW": 1350.0, "TWD": 32.0,
    "NZD": 1.65, "EUR": 0.92, "GBP": 0.79,
}
REGION_BY_CC: Dict[str, str] = {cc: "APAC" for cc in CITY_BY_CC if cc != "JP"}
REGION_BY_CC["JP"] = "JP"


def _daterange(start: _dt.date, end: _dt.date):
    d = start
    while d <= end:
        yield d
        d += _dt.timedelta(days=1)


def build_calendar(cfg) -> List[Dict]:
    start = _dt.date.fromisoformat(cfg.history_start)
    end = _dt.date(2027, 3, 31)
    as_of = _dt.date.fromisoformat(cfg.as_of_date)
    latest_month = (as_of.year, as_of.month)
    rows: List[Dict] = []
    for d in _daterange(start, end):
        nxt = d + _dt.timedelta(days=1)
        is_month_end = nxt.month != d.month
        rows.append({
            "date": d,
            "year": d.year,
            "month": d.month,
            "day": d.day,
            "fiscal_year": fiscal.fiscal_year(d),
            "fiscal_year_label": fiscal.fiscal_year_label(d),
            "fiscal_quarter": fiscal.fiscal_quarter(d),
            "fiscal_quarter_label": fiscal.fiscal_quarter_label(d),
            "fiscal_half": fiscal.fiscal_half(d),
            "fiscal_month_no": fiscal.fiscal_month_no(d),
            "is_month_end": is_month_end,
            "is_business_day_sg": d.weekday() < 5,
            "is_latest_closed_month": (d.year, d.month) == latest_month,
            "is_fytd": fiscal.fiscal_year(d) == fiscal.fiscal_year(as_of) and d <= as_of,
            "days_before_as_of": (as_of - d).days,
        })
    return rows


def build_currencies(cfg) -> List[Dict]:
    return [{"currency_code": c, "fx_base_per_usd": r, "is_base": c == "USD"}
            for c, r in FX_BASE.items()]


def build_fx_daily(cfg) -> List[Dict]:
    """Daily FX to USD as a smooth deterministic random walk around the base rate."""
    seed = cfg.random_seed
    start = _dt.date.fromisoformat(cfg.history_start)
    end = _dt.date(2027, 3, 31)
    rows: List[Dict] = []
    for ccy, base in FX_BASE.items():
        phase = rng.unit(seed, "fxphase", ccy) * 2 * math.pi
        amp = 0.0 if ccy == "USD" else 0.06 + 0.04 * rng.unit(seed, "fxamp", ccy)
        for i, d in enumerate(_daterange(start, end)):
            if ccy == "USD":
                rate = 1.0
            else:
                drift = amp * math.sin(i / 55.0 + phase)
                noise = (rng.unit(seed, "fxnoise", ccy, i) - 0.5) * 0.004
                rate = base * (1.0 + drift + noise)
            rows.append({"date": d, "currency_code": ccy,
                         "rate_per_usd": round(rate, 6),
                         "usd_per_unit": round(1.0 / rate, 10)})
    return rows


COUNTRY_NAMES = {"SG": "Singapore", "HK": "Hong Kong", "CN": "China", "TH": "Thailand", "ID": "Indonesia",
                 "IN": "India", "AU": "Australia", "VN": "Vietnam", "MY": "Malaysia", "TW": "Taiwan",
                 "KR": "South Korea", "PH": "Philippines", "NZ": "New Zealand", "JP": "Japan"}


def build_countries(cfg) -> List[Dict]:
    rows = []
    for l in cfg.booking_locations:
        rows.append({"country_code": l["code"], "country_name": COUNTRY_NAMES.get(l["code"], l["code"]),
                     "city": l["city"], "region_cluster": "APAC",
                     "local_currency": l["currency"], "regulator": l.get("regulator"),
                     "is_apac": True})
    rows.append({"country_code": "JP", "country_name": "Japan", "city": "Tokyo", "region_cluster": "JP",
                 "local_currency": "JPY", "regulator": "FSA", "is_apac": False})
    return rows


def build_booking_entities(cfg) -> List[Dict]:
    rows = []
    for l in cfg.booking_locations:
        rows.append({
            "booking_entity_id": l["code"], "city": l["city"],
            "country_name": COUNTRY_NAMES.get(l["code"], l["code"]), "region_cluster": "APAC",
            "regulator": l.get("regulator"), "local_currency": l["currency"],
            "is_hub": bool(l.get("is_hub")), "is_apac": True,
        })
    for region in cfg.raw.get("provider_regions", ["JP", "EMEA", "AMER"]):
        rows.append({
            "booking_entity_id": region, "city": None, "country_name": None,
            "region_cluster": region, "regulator": None, "local_currency": None,
            "is_hub": False, "is_apac": False,
        })
    return rows


def build_industry_peers(cfg) -> List[Dict]:
    """One peer group per distinct industry subsector (~40 peer groups)."""
    seed = cfg.random_seed
    seen: Dict[str, Dict] = {}
    for word, (sector, subsector, carbon) in INDUSTRY_MAP.items():
        if subsector in seen:
            continue
        pid = f"PG-{len(seen) + 1:03d}"
        # sector limit between USD 2bn and 10bn (illustrative)
        limit = int((2 + 8 * rng.unit(seed, "sectlimit", subsector)) * 1_000_000_000)
        seen[subsector] = {"peer_group_id": pid, "industry_sector": sector,
                           "industry_subsector": subsector, "is_carbon_intensive": carbon,
                           "sector_limit_usd": limit}
    return list(seen.values())
