"""Deposit accounts (brief §6.2: ~9,000 accounts). Deterministic, pure Python.

An account requires a core-banking customer record, so accounts are created only for entities
that have a core_customer identity (the deposit relationship). Balances are lognormal and
size-weighted so a few groups dominate (Pareto concentration, checked after load). CASA
(current/savings) vs time-deposit split supports the CASA-ratio metrics and migration storylines.

Every account carries `active_from`: no balances or payments before it. Long-standing accounts
are active from their open date; a new client's (onboarding cohort, D43) accounts open at
go-live or later and are active from the client's first transaction.
"""
from __future__ import annotations

import datetime as _dt
from typing import Dict, List, Optional

from . import rng

MIN_OPEN_DAYS, OPEN_DAYS_SPAN = 200, 2400  # long-standing accounts open 200-2,600 days before as-of
ACCOUNT_TYPES = [
    ("Current Account", True, 0.42),
    ("Savings Account", True, 0.18),
    ("Time Deposit", False, 0.28),
    ("Money Market Deposit", False, 0.12),
]
CCY_BY_CC = {"SG": "SGD", "HK": "HKD", "CN": "CNY", "TH": "THB", "ID": "IDR", "IN": "INR",
             "AU": "AUD", "VN": "VND", "MY": "MYR", "TW": "TWD", "KR": "KRW", "PH": "PHP",
             "NZ": "NZD", "JP": "JPY"}


def _n_accounts(seed: int, entity: Dict) -> int:
    if entity["is_group_lead"]:
        opts, weights = [3, 4, 5, 6, 7, 8], [2, 3, 4, 4, 3, 2]   # avg ~5.5
    else:
        opts, weights = [2, 3, 4, 5, 6], [3, 5, 5, 3, 2]          # avg ~3.8
    return rng.weighted_choice(seed, opts, weights, "nacct", entity["entity_id"])


def build_accounts(cfg, entities: List[Dict], entity_core_custno: Dict[str, str],
                   cohort: Optional[Dict[str, Dict]] = None) -> List[Dict]:
    """`cohort` is onboarding.new_client_cohort(): those clients' first account opens at go-live."""
    seed = cfg.random_seed
    as_of = _dt.date.fromisoformat(cfg.as_of_date)
    cohort = cohort or {}
    rows: List[Dict] = []
    aidx = 0
    for e in entities:
        custno = entity_core_custno.get(e["entity_id"])
        if not custno:
            continue  # no core-banking relationship -> no deposit accounts
        local_ccy = CCY_BY_CC.get(e["booking_country"], "USD")
        # account balance scales with GROUP wealth (concentration) + lead + health, lognormal tail
        wealth = e.get("group_wealth", 1.0)
        lead_mult = 1.6 if e["is_group_lead"] else 1.0
        sched = cohort.get(e["entity_id"])
        for j in range(_n_accounts(seed, e)):
            aidx += 1
            atype, is_casa, _ = rng.weighted_choice(
                seed, ACCOUNT_TYPES, [w for _, _, w in ACCOUNT_TYPES], "atype", e["entity_id"], j)
            # mostly local currency, some USD
            ccy = "USD" if rng.unit(seed, "acctccy", e["entity_id"], j) < 0.25 else local_ccy
            base_usd = round(rng.lognormal(seed, 13.3, 1.0, "bal", e["entity_id"], j)
                             * wealth * lead_mult * (0.6 + 0.8 * e["health_base"]), 2)
            u_open = rng.unit(seed, "openacct", e["entity_id"], j)
            if sched:  # new client: first account at go-live, later ones front-loaded after it
                go_live = sched["go_live_date"]
                open_date = go_live if j == 0 else go_live + _dt.timedelta(
                    days=int((as_of - go_live).days * u_open ** 2))
                active_from = max(open_date, sched["first_txn_date"])
            else:
                open_date = as_of - _dt.timedelta(days=int(MIN_OPEN_DAYS + OPEN_DAYS_SPAN * u_open))
                active_from = open_date
            rows.append({
                "account_id": f"ACC-{aidx:07d}",
                "cust_no": custno,
                "entity_id": e["entity_id"],
                "account_type": atype,
                "is_casa": is_casa,
                "currency": ccy,
                "open_date": open_date,
                "active_from": active_from,
                "base_balance_usd": base_usd,
            })
    return rows
