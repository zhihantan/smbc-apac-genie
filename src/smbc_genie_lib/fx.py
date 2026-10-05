"""FX & treasury deal flow (brief §5; Markets / Treasury Genie space; Kinokawa wallet-recapture).

Per client with a treasury dealing relationship (a tsy_counterparty identity), the FX wallet and
the deals SMBC books against it: spot / forward / swap flow, treasury-sales margin, hedge vs
trading mix, and — the Kinokawa storyline hook — how much of the client's total FX wallet SMBC
captures versus competitors. The relationship truth (turnover, SMBC wallet share, hedge ratio) is
pure Python and deterministic (unit-tested here); the entry script Spark-expands the deals and
rolls up the quarterly wallet estimate. Deal notionals tie back to each client's captured wallet
so revenue concentrates (Pareto) and the recapture opportunity is a real number.
"""
from __future__ import annotations

from typing import Dict, List

from . import rng

FX_PRODUCTS = [("FX Spot", 0.55), ("FX Forward", 0.30), ("FX Swap", 0.15)]
MARGIN_BPS = {"FX Spot": 3.0, "FX Forward": 8.0, "FX Swap": 5.0}
EM_CCYS = ["IDR", "VND", "INR", "PHP", "THB", "MYR", "CNY", "TWD", "KRW"]  # wider spreads
MAJOR_PAIRS = ["EUR/USD", "USD/JPY", "GBP/USD", "AUD/USD"]
PAIR_BY_CC = {"SG": "USD/SGD", "HK": "USD/HKD", "CN": "USD/CNY", "TH": "USD/THB", "ID": "USD/IDR",
              "IN": "USD/INR", "AU": "AUD/USD", "VN": "USD/VND", "MY": "USD/MYR", "TW": "USD/TWD",
              "KR": "USD/KRW", "PH": "USD/PHP", "NZ": "NZD/USD", "JP": "USD/JPY"}


def primary_pair(booking_country: str) -> str:
    return PAIR_BY_CC.get(booking_country, "EUR/USD")


def build_fx_relationships(cfg, entities: List[Dict], cpty_map: Dict[str, str]) -> List[Dict]:
    """One FX relationship per entity that has a treasury-counterparty identity (~18% of the book).

    turnover scales with group wealth; FIs trade more (higher trading share, lower hedge ratio);
    corporates mostly hedge. SMBC wallet share is 0.30-0.85 (the rest leaks to competitors).
    """
    seed = cfg.random_seed
    out: List[Dict] = []
    for e in entities:
        cpty = cpty_map.get(e["entity_id"])
        if not cpty:
            continue
        wealth = e.get("group_wealth", 1.0)
        is_fi = e["segment"] == "Financial Institution"
        turnover = round(rng.lognormal(seed, 17.2, 0.9, "fxturn", e["entity_id"]) * wealth, 2)
        share = round(0.30 + 0.55 * rng.unit(seed, "fxshare", e["entity_id"]), 4)
        hedge = round((0.30 if is_fi else 0.60) + 0.30 * rng.unit(seed, "fxhedge", e["entity_id"]), 4)
        trading = round((0.30 if is_fi else 0.05) + (0.25 if is_fi else 0.10) * rng.unit(seed, "fxtrade", e["entity_id"]), 4)
        out.append({
            "entity_id": e["entity_id"], "cpty_id": cpty, "booking_country": e["booking_country"],
            "fx_annual_turnover_usd": max(turnover, 1_000_000.0), "smbc_wallet_share": share,
            "hedge_ratio": hedge, "trading_share": trading,
            "primary_ccy_pair": primary_pair(e["booking_country"]), "is_fi": is_fi,
        })
    return out


def is_em_pair(ccy_pair: str) -> bool:
    return any(cc in ccy_pair for cc in EM_CCYS)
