"""Lending book: facilities, covenants, collateral (brief §5.3; DECISIONS D29).

Borrowers are entities with a credit_obligor identity (~35% lending penetration). Each gets 1-3
facilities keyed by obligor_id (credit workflow is the system of record for terms). Facility
limits scale with group wealth; margins tighten with health and for Japanese corporates; many
Japanese subsidiaries carry a parent guarantee / keepwell (the Tanaka storyline). Covenant
headroom and utilisation derive from latent health, and the base generators deliberately keep
non-storyline borrowers comfortably inside covenants (headroom >= ~15%) so the storyline
injectors can place the exact breach/blind-spot cases later. Pure Python; monthly utilisation is
Spark-expanded in the entry script.
"""
from __future__ import annotations

import datetime as _dt
from typing import Dict, List

from . import rng, storyline_injectors

FACILITY_TYPES = [("Term Loan", 0.30), ("Revolving Credit Facility", 0.28), ("Bilateral Loan", 0.14),
                  ("Syndicated Loan", 0.12), ("Overdraft", 0.09), ("Trade Loan", 0.07)]
REVOLVING = {"Revolving Credit Facility", "Overdraft"}
SECURITY_TYPES = ["Unsecured", "Real Estate", "Receivables", "Cash", "Fixed Assets", "Inventory"]
GUARANTOR_JC = [("Parent Guarantee", 0.34), ("Keepwell", 0.22), ("None", 0.34), ("Corporate Guarantee", 0.10)]
GUARANTOR_OTHER = [("None", 0.55), ("Corporate Guarantee", 0.25), ("Parent Guarantee", 0.15), ("Sponsor Support", 0.05)]


def _n_facilities(seed: int, eid: str) -> int:
    return rng.weighted_choice(seed, [1, 2, 3], [5, 3, 2], "nfac", eid)


def build_facilities(cfg, entities: List[Dict], entity_obligor: Dict[str, str]) -> List[Dict]:
    seed = cfg.random_seed
    as_of = _dt.date.fromisoformat(cfg.as_of_date)
    rows: List[Dict] = []
    fi = 0
    for e in entities:
        obligor = entity_obligor.get(e["entity_id"])
        if not obligor:
            continue
        wealth = e.get("group_wealth", 1.0)
        is_jc = e["segment"] == "Japanese Corporate"
        for j in range(_n_facilities(seed, e["entity_id"])):
            fi += 1
            ftype, _ = rng.weighted_choice(seed, FACILITY_TYPES, [w for _, w in FACILITY_TYPES], "ftype", e["entity_id"], j)
            limit_usd = round(rng.lognormal(seed, 15.0, 1.0, "flim", e["entity_id"], j) * wealth, 2)
            margin = int(70 + (1 - e["health_base"]) * 240 + rng.unit(seed, "marg", e["entity_id"], j) * 60
                         - (25 if is_jc else 0))
            gtable = GUARANTOR_JC if is_jc else GUARANTOR_OTHER
            guarantor, _ = rng.weighted_choice(seed, gtable, [w for _, w in gtable], "guar", e["entity_id"], j)
            security = rng.choice(seed, SECURITY_TYPES, "sec", e["entity_id"], j)
            is_secured = security != "Unsecured"
            orig_days = int(120 + 2400 * rng.unit(seed, "forig", e["entity_id"], j))
            orig = as_of - _dt.timedelta(days=orig_days)
            tenor_days = rng.choice(seed, [365, 730, 1095, 1825, 2555], "ften", e["entity_id"], j)
            maturity = orig + _dt.timedelta(days=tenor_days)
            rows.append({
                "facility_id": f"FAC-{fi:06d}", "entity_id": e["entity_id"], "obligor_id": obligor,
                "facility_type": ftype, "limit_usd": max(limit_usd, 250000.0),
                "currency": "USD" if rng.unit(seed, "fccy", e["entity_id"], j) < 0.5 else _ccy(e["booking_country"]),
                "margin_bps": max(35, margin), "security_type": security, "is_secured": is_secured,
                "guarantor_type": guarantor, "origination_date": orig, "maturity_date": maturity,
                "is_revolving": ftype in REVOLVING,
                "base_utilisation": round(0.30 + 0.45 * rng.unit(seed, "futil", e["entity_id"], j), 4),
                "has_covenant": ftype in ("Term Loan", "Syndicated Loan", "Revolving Credit Facility")
                                and rng.unit(seed, "fcov", e["entity_id"], j) < 0.8,
                "closed_date": None,
            })
    # storylines: scripted facilities (ids continue the sequence) and no new money for Sunda mid-cascade
    rows += storyline_injectors.scripted_facilities(cfg, entities, entity_obligor, fi + 1)
    storyline_injectors.redate_storyline_facilities(cfg, entities, rows)
    return rows


def facility_status(f: Dict, as_of: _dt.date) -> str:
    if f.get("closed_date"):
        return "Prepaid"
    return "Matured" if f["maturity_date"] < as_of else "Active"


def _ccy(cc: str) -> str:
    from .accounts import CCY_BY_CC
    return CCY_BY_CC.get(cc, "USD")


def _quarter_ends(start: _dt.date, end: _dt.date) -> List[_dt.date]:
    out = []
    for y in range(start.year, end.year + 1):
        for m in (3, 6, 9, 12):
            d = _dt.date(y, m, 1)
            d = (d.replace(day=28) + _dt.timedelta(days=4)).replace(day=1) - _dt.timedelta(days=1)
            if start <= d <= end:
                out.append(d)
    return out


def build_covenant_tests(cfg, facilities: List[Dict], health_lookup: Dict) -> List[Dict]:
    """Leverage (Net Debt/EBITDA) + some ICR covenants, tested quarterly. Base stays >=~15% headroom."""
    seed = cfg.random_seed
    as_of = _dt.date.fromisoformat(cfg.as_of_date)
    rows: List[Dict] = []
    for f in facilities:
        if not f["has_covenant"]:
            continue
        covs = ["Net Debt/EBITDA"]
        if rng.unit(seed, "icr", f["facility_id"]) < 0.4:
            covs.append("ICR")
        for cov in covs:
            thr = (4.0 + rng.choice(seed, [-0.5, 0.0, 0.5], "thr", f["facility_id"]) if cov == "Net Debt/EBITDA"
                   else 2.5)
            last = min(as_of, f["closed_date"] or f["maturity_date"])
            for td in _quarter_ends(f["origination_date"], last):
                mkey = (f["entity_id"], _dt.date(td.year, td.month, 1))
                hlth = health_lookup.get(mkey, 0.6)
                if cov == "Net Debt/EBITDA":
                    actual = round(thr * (0.40 + (1 - hlth) * 0.40), 2)  # higher ratio = worse
                    headroom = round((thr - actual) / thr, 4)
                    breached = actual > thr
                else:  # ICR: higher = better. Coeff keeps base headroom >= ~18% even at the
                    # latent-health floor (the stronger sector cycle now drives health lower),
                    # so non-storyline borrowers stay comfortably inside — storylines inject breaches.
                    actual = round(thr * (1.6 - (1 - hlth) * 0.42), 2)
                    headroom = round((actual - thr) / thr, 4)
                    breached = actual < thr
                rows.append({
                    "facility_id": f["facility_id"], "obligor_id": f["obligor_id"],
                    "covenant_type": cov, "test_date": td, "threshold": thr, "actual": actual,
                    "headroom_pct": headroom, "breached": breached, "waiver": False,
                    "is_latest_test": False, "test_basis": "Quarterly",
                })
    # mark latest test per (facility, covenant)
    latest: Dict = {}
    for r in rows:
        k = (r["facility_id"], r["covenant_type"])
        if k not in latest or r["test_date"] > latest[k]["test_date"]:
            latest[k] = r
    for r in latest.values():
        r["is_latest_test"] = True
    return rows


def build_collateral(cfg, facilities: List[Dict]) -> List[Dict]:
    seed = cfg.random_seed
    as_of = _dt.date.fromisoformat(cfg.as_of_date)
    rows: List[Dict] = []
    ci = 0
    for f in facilities:
        if not f["is_secured"]:
            continue
        ci += 1
        ltv = round(0.35 + 0.5 * rng.unit(seed, "ltv", f["facility_id"]), 4)  # ~0.35-0.85
        drawn = f["limit_usd"] * f["base_utilisation"]
        appraised = round(drawn / max(ltv, 0.1), 2)
        val_days = int(30 + 1000 * rng.unit(seed, "valdays", f["facility_id"]))  # some > 24 months
        rows.append({
            "collateral_id": f"COL-{ci:06d}", "facility_id": f["facility_id"],
            "collateral_type": f["security_type"], "appraised_value_usd": appraised,
            "ltv_pct": ltv, "last_valuation_date": as_of - _dt.timedelta(days=val_days),
        })
    return rows
