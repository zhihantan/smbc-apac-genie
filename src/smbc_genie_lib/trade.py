"""Trade finance & supply-chain finance (brief §5.8; Trade/TB Genie space; storyline 6 VN/IN surge).

Two product families for clients with a trade identity (a trade_party record, ~22% of the book):

  * Trade finance instruments — import/export LCs, guarantees/SBLCs, documentary collections,
    trade loans and receivables purchase. Each carries a corridor (origin -> destination country),
    a commodity (HS chapter) from the client's industry, and a lifecycle (issue / amend / present /
    pay / expire) whose state at as-of sets its status. `instrument_lifecycle` is the single source
    of that lifecycle: the trade step takes status / closed date from it and the TB-depth step
    (tb_trade) expands it into bronze.trade_event and bronze.trade_presentation.
  * Supply-chain finance programmes — the largest corporate importers anchor payables/receivables
    programmes with a supplier network; un-onboarded suppliers are the growth opportunity.

Storyline 6 is fully scripted in `vn_in_surge_txns`: every import LC into VN/IN of electronics or
auto parts from CN/KR/JP comes from it, so H1 FY2026 vs H1 FY2025 growth (+40%, count and USD) and
the Singapore booking share (45%) are exact. Kinokawa's VN entity carries the +50% CN->VN corridor.
Pure Python and deterministic; the entry script only writes.
"""
from __future__ import annotations

import bisect
import datetime as _dt
import math
from collections import defaultdict
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import fiscal, names, reference, rng, storyline_injectors, storylines

D, TD = _dt.date, _dt.timedelta

# ---- instruments ---------------------------------------------------------------------------
TXN_START = D(2024, 4, 1)          # trade history starts with FY2024 (as payments) ...
LEGACY_START = D(2023, 4, 1)       # ... plus the FY2023-issued book still open on 1-Apr-2024
TXNS_AT_SCALE_1 = 180_000          # instruments issued from TXN_START (the legacy book comes on top)
TRADE_PRODUCTS = [("Import LC", 0.25), ("Guarantee / SBLC", 0.20), ("Export LC", 0.18),
                  ("Trade Loan", 0.17), ("Documentary Collection", 0.12), ("Receivables Purchase", 0.08)]
FEE_BPS = {"Import LC": 25.0, "Export LC": 22.0, "Guarantee / SBLC": 40.0,
           "Documentary Collection": 10.0, "Trade Loan": 150.0, "Receivables Purchase": 35.0}
PER_ANNUM_FEES = {"Trade Loan", "Guarantee / SBLC"}   # margin / commission pro-rated by tenor
JC_FEE_FACTOR = 0.85               # Japanese subsidiaries price tighter (brief §6.3)
TENOR_DAYS = {"Guarantee / SBLC": (180, 550), "Documentary Collection": (15, 75), "Trade Loan": (60, 300),
              "Receivables Purchase": (30, 90), "Import LC": (30, 150), "Export LC": (30, 150)}  # lo + u*span
# indicative as-of status mix (derived from the lifecycle, checked in tests)
TRADE_STATUS = [("Settled", 0.64), ("Issued", 0.17), ("Expired", 0.18), ("Negotiated", 0.01)]
# counterparty countries, Vietnam & India weighted up (base for the trade-surge storyline)
CORRIDORS = [("CN", 18), ("VN", 14), ("IN", 13), ("US", 10), ("JP", 8), ("KR", 7), ("DE", 6),
             ("GB", 5), ("SG", 5), ("HK", 4), ("TH", 4), ("AU", 3), ("ID", 3)]
CORRIDOR_SPLIT = (0.55, 0.27, 0.18)  # a client's txns: primary corridor + two secondary corridors
# products and corridors are allocated per client per fiscal half in exact proportion (largest
# remainder), direction and goods follow low-discrepancy sequences: client mixes hold steadily over
# time, so corridor volumes are smooth and the scripted VN/IN surge stands out
SEQ_STEP = (0.6180339887, 0.4142135624, 0.7320508076, 0.2360679775)
AMOUNT_SPREAD = 0.6                # per-txn ticket noise exp((u - 0.5) * spread) around the client ticket
CORRIDOR_TAILWIND = 0.30           # storyline 6 market: base flows into VN/IN from CN/KR/JP, USD
TAILWIND_RAMP = (D(2025, 10, 1), D(2026, 4, 1))  # ... ramp up over H2 FY2025 (matches ext statistics)
BANK_HUBS = ["SG", "HK", "US", "GB"]  # counterparty banks booked outside the counterparty's country
TXN_CCY = {"JP": ("JPY", 0.30), "CN": ("CNY", 0.20), "DE": ("EUR", 0.45), "GB": ("GBP", 0.35)}
MONTH_FACTOR = {2: 0.88, 3: 1.30, 6: 1.10, 9: 1.10, 12: 1.10}  # Lunar New Year dip, March FY-end
WEEKEND_FACTOR, TREND_PER_YEAR = 0.05, 0.02
LATE_STARTER_SHARE = 0.12          # smaller trade relationships (below-median turnover) begun in-window
SUSTAINABLE_SHARE = 0.08

# commodity = (name, HS chapter, HS description, carbon-intensive)
COMMODITIES = [
    ("Electronics", "85", "Electrical machinery and equipment", False),
    ("Machinery", "84", "Machinery and mechanical appliances", False),
    ("Auto Parts", "87", "Vehicles, parts and accessories", False),
    ("Precision Instruments", "90", "Optical, measuring and precision instruments", False),
    ("Chemicals", "29", "Organic chemicals", True),
    ("Plastics", "39", "Plastics and articles thereof", True),
    ("Iron & Steel", "72", "Iron and steel", True),
    ("Mineral Fuels", "27", "Mineral fuels and oils (coal, crude, LNG)", True),
    ("Ores & Metals", "26", "Ores, slag and ash", True),
    ("Palm Oil & Fats", "15", "Animal and vegetable fats and oils", True),
    ("Food & Agri", "10", "Cereals and food products", False),
    ("Textiles & Apparel", "62", "Apparel and clothing", False),
]
HS_CHAPTER = {c: hs for c, hs, _, _ in COMMODITIES}
_ELEC = [("Electronics", 80), ("Machinery", 10), ("Plastics", 10)]
SUBSECTOR_COMMODITIES = {
    "Electronics": _ELEC, "Electronic Devices": _ELEC, "Semiconductors": [("Electronics", 85), ("Chemicals", 15)],
    "Telecommunications": [("Electronics", 90), ("Machinery", 10)],
    "Auto Parts": [("Auto Parts", 80), ("Iron & Steel", 10), ("Electronics", 10)],
    "Vehicles": [("Auto Parts", 85), ("Electronics", 15)],
    "Precision Components": [("Precision Instruments", 45), ("Electronics", 30), ("Machinery", 25)],
    "Instruments": [("Precision Instruments", 70), ("Electronics", 30)],
    "Machinery": [("Machinery", 75), ("Iron & Steel", 15), ("Electronics", 10)],
    "Heavy Industry": [("Machinery", 60), ("Iron & Steel", 40)],
    "Chemicals": [("Chemicals", 65), ("Plastics", 35)],
    "Petrochemicals": [("Chemicals", 50), ("Plastics", 30), ("Mineral Fuels", 20)],
    "Steel": [("Iron & Steel", 75), ("Ores & Metals", 25)],
    "Advanced Materials": [("Plastics", 40), ("Chemicals", 40), ("Ores & Metals", 20)],
    "Fibre": [("Textiles & Apparel", 70), ("Plastics", 30)],
    "Oil, Gas & Coal": [("Mineral Fuels", 90), ("Machinery", 10)],
    "Power Generation": [("Mineral Fuels", 70), ("Machinery", 30)],
    "State Power": [("Mineral Fuels", 75), ("Machinery", 25)],
    "Mining": [("Ores & Metals", 70), ("Mineral Fuels", 30)],
    "Minerals": [("Ores & Metals", 80), ("Mineral Fuels", 20)],
    "Natural Resources": [("Ores & Metals", 50), ("Mineral Fuels", 50)],
    "Agribusiness": [("Food & Agri", 60), ("Palm Oil & Fats", 40)],
    "Palm Oil": [("Palm Oil & Fats", 85), ("Food & Agri", 15)],
    "Food & Beverage": [("Food & Agri", 80), ("Plastics", 20)],
    "Consumer Goods": [("Textiles & Apparel", 35), ("Electronics", 35), ("Food & Agri", 30)],
    "Infrastructure": [("Iron & Steel", 40), ("Machinery", 45), ("Electronics", 15)],
    "Construction": [("Iron & Steel", 45), ("Machinery", 35), ("Chemicals", 20)],
    "Property": [("Iron & Steel", 50), ("Machinery", 30), ("Chemicals", 20)],
    "Renewables": [("Electronics", 60), ("Machinery", 40)],
}
GENERIC_COMMODITIES = [("Electronics", 22), ("Machinery", 15), ("Auto Parts", 8), ("Precision Instruments", 3),
                       ("Chemicals", 8), ("Plastics", 6), ("Iron & Steel", 7), ("Mineral Fuels", 10),
                       ("Ores & Metals", 5), ("Palm Oil & Fats", 4), ("Food & Agri", 8), ("Textiles & Apparel", 4)]
OWN_GOODS_SHARE = 0.75             # share of a client's txns in its own industry's goods

# ---- storyline 6: VN/IN import-LC surge (storylines.VN_IN) ---------------------------------
SURGE_H1_FY2025_AT_SCALE_1 = 1100  # scripted import LCs in H1 FY2025 at SCALE 1.0
SURGE_HALVES = [("FY2023-H2", 0.95), ("FY2024-H1", 0.96), ("FY2024-H2", 1.00), ("FY2025-H1", 1.00),
                ("FY2025-H2", 1.18), ("FY2026-H1", None)]   # None = 1 + VN_IN["yoy_growth"] (exact vs FY2025-H1);
#   FY2023-H2 is legacy book (kept only if still open on TXN_START)
SURGE_IMPORTERS = {"Electronics", "Electronic Devices", "Semiconductors", "Telecommunications", "Auto Parts",
                   "Vehicles", "Precision Components", "Instruments", "Machinery", "Heavy Industry",
                   "General Trading", "Consumer Goods"}
SURGE_COMMODITIES = ("Electronics", "Auto Parts")
SURGE_ORIGIN_WEIGHTS = {"CN": 50, "KR": 25, "JP": 25}
SURGE_DEST_WEIGHTS = {"VN": 55, "IN": 45}
SURGE_TICKET_USD = 520_000.0       # median scripted LC (in line with the base book)
KINOKAWA_SHARE = 0.05              # Kinokawa VN's share of the scripted LCs (all CN -> VN electronics)

# ---- lifecycle ----------------------------------------------------------------------------
ISSUE_DETAIL = {"Import LC": "LC Issued", "Export LC": "LC Advised and Confirmed",
                "Guarantee / SBLC": "Guarantee Issued", "Documentary Collection": "Collection Lodged",
                "Trade Loan": "Loan Disbursed", "Receivables Purchase": "Receivables Purchased"}
AMENDMENTS = [("Increase Amount", 25), ("Decrease Amount", 10), ("Extend Shipment Date", 35),
              ("Change Documents Required", 20), ("Change Beneficiary Details", 10)]
DISCREPANCIES = [("Late Shipment", 18), ("Late Presentation", 12), ("Inconsistent Data Between Documents", 22),
                 ("Bill of Lading Not Clean", 8), ("Insurance Document Deficiency", 9),
                 ("Invoice Amount Exceeds LC", 8), ("Description of Goods Mismatch", 12),
                 ("Missing Document", 7), ("Certificate of Origin Issue", 4)]
DISC_BASE, DISC_HEALTH, DISC_DETERIORATION = 0.10, 0.25, 1.0   # propensity = f(health, 12m decline)
DISC_COUNTRY = {"SG": 0.85, "HK": 0.90, "KR": 0.90, "TW": 0.90, "JP": 0.85, "AU": 0.95,
                "CN": 1.10, "IN": 1.20, "VN": 1.25, "ID": 1.20, "PH": 1.15}
OPS_TEAM = {"SG": "SG Trade Ops Hub", "HK": "HK Trade Ops", "CN": "CN Trade Ops", "IN": "IN Trade Ops",
            "ID": "ID Trade Ops", "TH": "TH Trade Ops", "VN": "VN Trade Ops", "KR": "KR Trade Ops",
            "TW": "TW Trade Ops", "AU": "AU Trade Ops"}   # MY / PH / NZ are checked in the SG hub
TEAM_SPEED = {"SG Trade Ops Hub": 0.85, "HK Trade Ops": 0.90, "KR Trade Ops": 0.95, "TW Trade Ops": 0.95,
              "TH Trade Ops": 1.05, "IN Trade Ops": 1.25, "VN Trade Ops": 1.20, "ID Trade Ops": 1.20}
SLA_HOURS = 24.0                   # documents examined within one business day
CHECK_MEDIAN_HOURS = 14.0
USANCE_SHARE, USANCE_DAYS = 0.30, [30, 60, 90, 120]
FULL_DRAW_SHARE = 0.65             # LCs drawn in full (the rest leave an undrawn tolerance to lapse)


def _pick(seed: int, pairs: Sequence[Tuple], *keys):
    return rng.weighted_choice(seed, [p for p, _ in pairs], [w for _, w in pairs], *keys)


def _largest_remainder(total: int, weights: Sequence[float], minimum: int = 0) -> List[int]:
    """Integer counts >= minimum, summing to total, proportional to weights."""
    extra = total - minimum * len(weights)
    s = float(sum(weights))
    raw = [w / s * extra for w in weights]
    counts = [int(x) for x in raw]
    order = sorted(range(len(weights)), key=lambda i: raw[i] - counts[i], reverse=True)
    for i in order[: extra - sum(counts)]:
        counts[i] += 1
    return [c + minimum for c in counts]


class DateSampler:
    """Maps a [0,1) position to a date in [a, b), weighted by business days, month seasonality
    (March fiscal-year-end, quarter-ends, Lunar New Year) and a mild upward trend."""

    def __init__(self, start: D, end: D):
        self.start, cum, acc = start, [], 0.0
        for i in range((end - start).days):
            d = start + TD(days=i)
            acc += (MONTH_FACTOR.get(d.month, 1.0) * (1 + TREND_PER_YEAR * i / 365.25)
                    * (WEEKEND_FACTOR if d.weekday() >= 5 else 1.0))
            cum.append(acc)
        self.cum = cum

    def _c(self, d: D) -> float:
        i = (d - self.start).days
        return self.cum[i - 1] if i > 0 else 0.0

    def at(self, a: D, b: D, p: float) -> D:
        lo, hi = self._c(a), self._c(b)
        i = bisect.bisect_right(self.cum, lo + p * (hi - lo))
        return min(self.start + TD(days=i), b - TD(days=1))


def fx_lookup(cfg) -> Dict[Tuple[str, D], float]:
    """(currency, date) -> local units per USD, identical to bronze.fx_rate_daily."""
    return {(r["currency_code"], r["date"]): r["rate_per_usd"] for r in reference.build_fx_daily(cfg)}


# ---- relationships & SCF (truth) ------------------------------------------------------------
def build_trade_relationships(cfg, entities: List[Dict], party_map: Dict[str, str]) -> List[Dict]:
    """One trade relationship per entity with a trade_party identity. The largest ~40 corporate
    importers become SCF anchors."""
    seed = cfg.random_seed
    rels: List[Dict] = []
    meridian_lead = (storylines.lead_entity(entities, storylines.MERIDIAN["key"])["entity_id"]
                     if storylines.on(cfg, storylines.MERIDIAN_ER) else None)
    for e in entities:
        party = party_map.get(e["entity_id"])
        if not party:
            continue
        wealth = e.get("group_wealth", 1.0)
        # ~10-15% of a client's trade runs through bank instruments (most trade is open account)
        turnover = round(rng.lognormal(seed, 15.1, 0.9, "trturn", e["entity_id"]) * wealth, 2)
        if meridian_lead == e["entity_id"]:
            turnover = storylines.MERIDIAN["trade_turnover_usd"]
        import_share = round(0.30 + 0.40 * rng.unit(seed, "trimp", e["entity_id"]), 4)
        corridor = rng.weighted_choice(seed, [c for c, _ in CORRIDORS], [w for _, w in CORRIDORS],
                                       "trcorr", e["entity_id"])
        rels.append({
            "entity_id": e["entity_id"], "party_id": party, "booking_country": e["booking_country"],
            "segment": e["segment"], "trade_annual_turnover_usd": max(turnover, 1_000_000.0),
            "import_share": import_share, "primary_corridor": corridor,
            "is_corporate": e["segment"] in ("Japanese Corporate", "Non-Japanese Large Corporate"),
        })
    return rels


SCF_TYPES = [("Payables Finance", 0.70), ("Receivables Finance", 0.30)]
SCF_HOT_UTIL, SCF_COLD_UTIL, SCF_NORMAL_UTIL = (0.87, 0.94), (0.28, 0.38), (0.45, 0.82)
SCF_BAND_SHARE = 0.15              # share of programmes running hot (> 85%) and cold (< 40%) at as-of
SUPPLIER_WORDS = ["Components", "Supplies", "Materials", "Logistics", "Packaging", "Industries",
                  "Manufacturing", "Fabrication", "Textiles", "Electronics", "Agro", "Precision"]
SUPPLIER_COUNTRIES = ["CN", "VN", "IN", "TH", "ID", "MY", "TW", "KR", "PH", "SG"]
LAUNCH_FROM, LAUNCH_TO = D(2019, 1, 1), D(2025, 10, 1)


def build_scf_programmes(cfg, trade_rels: List[Dict]) -> List[Dict]:
    """~40 SCF programmes anchored by the largest corporate importers (scaled by SCALE). As-of
    utilisation is banded so a few programmes run hot (> 85%) and a few cold (< 40%)."""
    seed = cfg.random_seed
    n_prog = max(8, round(40 * cfg.scale)) if cfg.scale < 1.0 else 40
    anchors = sorted([r for r in trade_rels if r["is_corporate"]],
                     key=lambda r: r["trade_annual_turnover_usd"], reverse=True)[:n_prog]
    n_band = max(1, round(SCF_BAND_SHARE * len(anchors)))
    order = sorted(range(len(anchors)), key=lambda i: rng.hash64(seed, "scfband", i))
    band = {i: SCF_HOT_UTIL for i in order[:n_band]}
    band.update({i: SCF_COLD_UTIL for i in order[n_band:2 * n_band]})
    progs: List[Dict] = []
    for i, a in enumerate(anchors):
        ptype = rng.weighted_choice(seed, [t for t, _ in SCF_TYPES], [w for _, w in SCF_TYPES], "scftype", i)
        limit = max(round(a["trade_annual_turnover_usd"] * (0.4 + 0.5 * rng.unit(seed, "scflim", i)), 2), 5_000_000.0)
        lo, hi = band.get(i, SCF_NORMAL_UTIL)
        util = round(lo + (hi - lo) * rng.unit(seed, "scfutil", i), 4)
        launch = LAUNCH_FROM + TD(days=int(rng.unit(seed, "scflaunch", i) * (LAUNCH_TO - LAUNCH_FROM).days))
        progs.append({
            "programme_id": f"SCF-{i + 1:04d}", "anchor_party_id": a["party_id"],
            "anchor_entity_id": a["entity_id"], "programme_type": ptype, "launch_date": launch,
            "limit_usd": limit, "drawn_usd": round(limit * util, 2), "utilisation": util,
            "n_suppliers_total": rng.randint(seed, 12, 90, "scfnsup", i), "currency": "USD",
            "primary_corridor": a["primary_corridor"],
        })
    return progs


def supplier_name(seed: int, i: int, country: str) -> str:
    stems = names.SEA_STEMS if rng.unit(seed, "supreg", i) < 0.7 else names.JP_STEMS
    stem = stems[rng.hash64(seed, "supstem", i) % len(stems)]
    word = SUPPLIER_WORDS[rng.hash64(seed, "supword", i) % len(SUPPLIER_WORDS)]
    return f"{stem} {word} {names.legal_form(seed, country, i)}"


def build_scf_suppliers(cfg, programmes: List[Dict]) -> List[Dict]:
    """Supplier network across the programmes (Pareto: bigger programmes have more suppliers).

    ~60% are onboarded to SMBC SCF (financed); the rest are prospective — the onboarding
    opportunity. `financed_amount_usd` is the supplier's financed outstanding at as-of and sums to
    its programme's drawn amount; `n_invoices_financed` counts invoices financed in the last 12
    months. Some onboarded suppliers' wider banking still routes elsewhere.
    """
    seed = cfg.random_seed
    if not programmes:
        return []
    as_of = D.fromisoformat(cfg.as_of_date)
    n_sup = max(400, round(1900 * cfg.scale)) if cfg.scale < 1.0 else 1900
    weights = [p["limit_usd"] for p in programmes]
    suppliers: List[Dict] = []
    for i in range(n_sup):
        prog = rng.weighted_choice(seed, programmes, weights, "supprog", i)
        onboarded = rng.unit(seed, "supon", i) < 0.60
        country = rng.weighted_choice(seed, SUPPLIER_COUNTRIES, [3, 3, 3, 2, 2, 2, 1, 1, 1, 2], "supcc", i)
        onb_date = None
        if onboarded:  # early adopters join at launch; the rest spread to 3 months before as-of
            first = max(prog["launch_date"], TXN_START - TD(days=720))
            onb_date = first + TD(days=int((as_of - TD(days=90) - first).days * rng.unit(seed, "supond", i) ** 1.5))
        suppliers.append({
            "supplier_id": f"SUP-{i + 1:05d}", "programme_id": prog["programme_id"],
            "anchor_party_id": prog["anchor_party_id"],
            "supplier_name": supplier_name(seed, i, country), "supplier_country": country,
            "is_onboarded": onboarded, "onboarded_date": onb_date,
            "n_invoices_financed": rng.randint(seed, 1, 48, "supinv", i) if onboarded else 0,
            "financed_amount_usd": (0.3 + 1.4 * rng.unit(seed, "supfin", i)) if onboarded else 0.0,
            "avg_days_paid_early": rng.randint(seed, 15, 75, "supdays", i) if onboarded else 0,
            "discount_rate_bps": round(60 + 180 * rng.unit(seed, "supdisc", i), 1) if onboarded else 0.0,
            "routes_to_other_bank": rng.unit(seed, "supbank", i) < 0.40,
        })
    by_prog: Dict[str, List[Dict]] = defaultdict(list)
    for s in suppliers:
        by_prog[s["programme_id"]].append(s)
    for p in programmes:  # outstanding per supplier sums to the programme's drawn amount
        onb = [s for s in by_prog[p["programme_id"]] if s["is_onboarded"]]
        tot = sum(s["financed_amount_usd"] for s in onb)
        for s in onb:
            s["financed_amount_usd"] = round(p["drawn_usd"] * s["financed_amount_usd"] / tot, 2)
    return suppliers


# ---- instrument generation ----------------------------------------------------------------
def _seq(seed: int, party: str, k: int, j: int) -> float:
    """Low-discrepancy uniform along a client's txns (k in date order) for attribute j."""
    return (rng.unit(seed, "trseq", party, j) + k * SEQ_STEP[j]) % 1.0


def _at(pairs: Sequence[Tuple], u: float):
    """Weighted categorical from a given uniform."""
    total, acc = float(sum(w for _, w in pairs)), 0.0
    for v, w in pairs:
        acc += w / total
        if u < acc:
            return v
    return pairs[-1][0]


def client_corridors(seed: int, rel: Dict) -> List[Tuple[str, float]]:
    """A client's three trade corridors (counterparty countries) with their shares of its txns."""
    own, out = rel["booking_country"], []
    if rel["primary_corridor"] != own:
        out.append(rel["primary_corridor"])
    for j in range(12):
        if len(out) == 3:
            break
        c = _pick(seed, [(c, w) for c, w in CORRIDORS if c != own and c not in out], "trsec", rel["party_id"], j)
        out.append(c)
    return list(zip(out, CORRIDOR_SPLIT))


def client_commodities(subsector: str) -> List[Tuple[str, float]]:
    """A client's goods mix: its own industry's goods (OWN_GOODS_SHARE) + the generic mix."""
    own = SUBSECTOR_COMMODITIES.get(subsector)
    if not own:
        return list(GENERIC_COMMODITIES)
    t_own, t_gen = sum(w for _, w in own), sum(w for _, w in GENERIC_COMMODITIES)
    mix: Dict[str, float] = defaultdict(float)
    for c, w in own:
        mix[c] += OWN_GOODS_SHARE * w / t_own
    for c, w in GENERIC_COMMODITIES:
        mix[c] += (1 - OWN_GOODS_SHARE) * w / t_gen
    return sorted(mix.items(), key=lambda x: -x[1])


def _is_surge(product: str, dest: str, origin: str, commodity: str) -> bool:
    return (product == "Import LC" and dest in storylines.VN_IN["import_countries"]
            and origin in storylines.VN_IN["source_countries"] and commodity in SURGE_COMMODITIES)


def _allocate_by_half(seed: int, party: str, salt: str, dates: List[D], pairs: Sequence[Tuple],
                      groups: Optional[List[str]] = None) -> List:
    """A category per txn with exact proportions within each fiscal half (x optional group)."""
    out: List = [None] * len(dates)
    by_half: Dict[Tuple, List[int]] = defaultdict(list)
    for k, d in enumerate(dates):
        by_half[(fiscal.fiscal_half_label(d), groups[k] if groups else None)].append(k)
    for ks in by_half.values():
        ks = sorted(ks, key=lambda k: rng.hash64(seed, salt, party, k))
        i = 0
        for (c, _), n_c in zip(pairs, _largest_remainder(len(ks), [w for _, w in pairs])):
            for k in ks[i:i + n_c]:
                out[k] = c
            i += n_c
    return out


def _direction(seed: int, rel: Dict, product: str, k: int) -> str:
    if product in ("Import LC", "Export LC"):
        return product.split()[0]
    return "Import" if _seq(seed, rel["party_id"], k, 1) < rel["import_share"] else "Export"


def _base_txn(seed: int, rel: Dict, product: str, direction: str, cc: str, goods: List, k: int, d: D) -> Dict:
    party = rel["party_id"]
    commodity = _at(goods, _seq(seed, party, k, 3))
    if _is_surge(product, rel["booking_country"], cc, commodity):  # the storyline owns these LCs
        commodity = _pick(seed, [g for g in goods if g[0] not in SURGE_COMMODITIES], "trnosurge", party, k)
    return {"_key": f"B-{party}-{k}", "party_id": party, "txn_date": d, "product_type": product,
            "direction": direction, "counterparty_country": cc, "commodity": commodity,
            "booking_location": rel["booking_country"], "destination": None}


def tailwind(d: D) -> float:
    """Storyline-6 market tailwind on flows into VN/IN from CN/KR/JP (1.0 before the ramp)."""
    a, b = TAILWIND_RAMP
    return 1.0 + CORRIDOR_TAILWIND * min(1.0, max(0.0, (d - a).days / (b - a).days))


def _rel_start(seed: int, rel: Dict, as_of: D, median_turnover: float) -> D:
    if (rel["trade_annual_turnover_usd"] >= median_turnover
            or rng.unit(seed, "trlate", rel["party_id"]) >= 2 * LATE_STARTER_SHARE):
        return TXN_START
    span = (as_of - TD(days=240) - TXN_START).days
    return TXN_START + TD(days=int(span * rng.unit(seed, "trlated", rel["party_id"])))


SURGE_CELLS = [((o, d), wo * wd) for o, wo in SURGE_ORIGIN_WEIGHTS.items() for d, wd in SURGE_DEST_WEIGHTS.items()]


def _surge_rows(seed: int, half: str, a: D, b: D, n_hub: int, n_local: int, n_kino: int,
                hub: List[Dict], local: Dict[str, List[Dict]], kino: Optional[Dict], by_id: Dict,
                sampler: DateSampler) -> List[Dict]:
    """One half's scripted LCs; (origin, destination) cells are allocated in exact proportion."""
    plan = [("kino", storylines.KINOKAWA_SCRIPT["corridor"])] * n_kino
    for kind, n in (("hub", n_hub), ("local", n_local)):
        for cell, c in zip(SURGE_CELLS, _largest_remainder(n, [w for _, w in SURGE_CELLS])):
            plan += [(kind, cell[0])] * c
    order = sorted(range(len(plan)), key=lambda i: rng.hash64(seed, "srgpos", half, i))
    rows = []
    for pos, i in enumerate(order):
        kind, (origin, dest) = plan[i]
        key = (half, i)
        pool = hub if kind == "hub" else (local.get(dest) or [r for v in local.values() for r in v])
        rel = kino if kind == "kino" else rng.weighted_choice(
            seed, pool, [math.sqrt(r["trade_annual_turnover_usd"]) for r in pool], "srgrel", *key)
        sub = by_id[rel["entity_id"]]["industry_subsector"]
        p_auto = 0.0 if kind == "kino" else 0.85 if sub in ("Auto Parts", "Vehicles") else (
            0.10 if sub in ("Electronics", "Electronic Devices", "Semiconductors", "Telecommunications") else 0.40)
        rows.append({"_key": f"S-{half}-{i}", "_half": half, "_kind": kind, "_cell": (origin, dest, kind == "hub"),
                     "party_id": rel["party_id"],
                     "txn_date": sampler.at(a, b, (pos + rng.unit(seed, "srgdt", *key)) / len(plan)),
                     "product_type": "Import LC", "direction": "Import", "counterparty_country": origin,
                     "commodity": "Auto Parts" if rng.unit(seed, "srgcom", *key) < p_auto else "Electronics",
                     "booking_location": rel["booking_country"], "destination": dest,
                     "amount_usd": SURGE_TICKET_USD * math.exp(rng.normal(seed, 0.0, 0.55, "srgamt", *key))})
    return rows


def _rescale(rows: List[Dict], total: float) -> None:
    s = sum(r["amount_usd"] for r in rows)
    for r in rows:
        r["amount_usd"] *= total / s


def _usd(rows: List[Dict], **match) -> float:
    return sum(r["amount_usd"] for r in rows if all(r[k] == v for k, v in match.items()))


def vn_in_surge_txns(cfg, entities: List[Dict], rels: List[Dict], sampler: DateSampler) -> List[Dict]:
    """Storyline 6: every import LC into VN/IN of electronics or auto parts from CN/KR/JP.

    Importers are VN/IN electronics / auto / machinery / trading clients and Singapore regional
    hubs of groups with VN/IN entities (which book exactly `sg_booking_share` of them). H1 FY2026 is
    exactly 1 + yoy_growth times H1 FY2025 in count and USD — in total and in every (origin,
    destination, SG-booked) cell; Kinokawa VN's CN->VN LCs grow +50%.
    """
    seed, script = cfg.random_seed, storylines.VN_IN
    as_of = D.fromisoformat(cfg.as_of_date)
    by_id = {e["entity_id"]: e for e in entities}
    dests = set(script["import_countries"])
    group_cc: Dict[str, set] = defaultdict(set)
    for e in entities:
        group_cc[e["group_id"]].add(e["booking_country"])
    kino_e = storylines.entity_in(entities, "kinokawa", "VN") if storylines.on(cfg, storylines.KINOKAWA) else None
    kino = next((r for r in rels if kino_e and r["entity_id"] == kino_e["entity_id"]), None)

    def importer(r):
        return by_id[r["entity_id"]]["industry_subsector"] in SURGE_IMPORTERS

    local = {d: [r for r in rels if r["booking_country"] == d and importer(r) and r is not kino]
             or [r for r in rels if r["booking_country"] == d and r is not kino] for d in sorted(dests)}
    hub = [r for r in rels if r["booking_country"] == "SG" and importer(r)
           and group_cc[by_id[r["entity_id"]]["group_id"]] & dests]
    hub = hub or [r for r in rels if r["booking_country"] == "SG"]
    n25 = max(10, round(SURGE_H1_FY2025_AT_SCALE_1 * cfg.scale))
    k25 = max(2, round(KINOKAWA_SHARE * n25)) if kino else 0
    growth = 1.0 + script["yoy_growth"]
    k_growth = 1.0 + storylines.KINOKAWA_SCRIPT["corridor_growth"]
    by_half: Dict[str, List[Dict]] = {}
    for half, factor in SURGE_HALVES:
        fy, h = int(half[2:6]), half[-2:]
        a = D(fy, 4, 1) if h == "H1" else D(fy, 10, 1)
        b = min(D(fy, 10, 1) if h == "H1" else D(fy + 1, 4, 1), as_of)
        n = round(n25 * (factor or growth))
        n_kino = round(k25 * (k_growth if factor is None else (1.2 if factor > 1.0 else 1.0)))
        n_hub = round(script["sg_booking_share"] * n)
        by_half[half] = _surge_rows(seed, half, a, b, n_hub, n - n_hub - n_kino, n_kino,
                                    hub, local, kino, by_id, sampler)
    for half, factor in SURGE_HALVES:  # exact SG share in every half up to H1 FY2025
        if factor is None:
            continue
        rows = by_half[half]
        total = _usd(rows)
        _rescale([r for r in rows if r["_kind"] == "hub"], script["sg_booking_share"] * total)
        _rescale([r for r in rows if r["_kind"] == "local"], (1 - script["sg_booking_share"]) * total
                 - _usd(rows, _kind="kino"))
    ref, cur = by_half["FY2025-H1"], by_half["FY2026-H1"]  # H1 FY2026: every cell exactly x growth
    kin = [r for r in cur if r["_kind"] == "kino"]
    if kin:
        _rescale(kin, k_growth * _usd(ref, _kind="kino"))
    for cell in {r["_cell"] for r in cur}:
        others = [r for r in cur if r["_cell"] == cell and r["_kind"] != "kino"]
        target = growth * _usd(ref, _cell=cell) - sum(r["amount_usd"] for r in kin if r["_cell"] == cell)
        if others and target > 0:
            _rescale(others, target)
    rest = [r for r in cur if r["_kind"] != "kino"]  # guard: cells empty in one half (tiny SCALE)
    if abs(_usd(cur) - growth * _usd(ref)) > 1e-6 * _usd(ref):
        _rescale(rest, growth * _usd(ref) - _usd(kin))
    return [r for half, _ in SURGE_HALVES for r in by_half[half]]


def _corridor(t: Dict) -> Tuple[str, str]:
    """(origin, destination): imports come from the counterparty, exports go to it."""
    cc, own = t["counterparty_country"], t["booking_location"]
    if t["direction"] == "Import":
        return cc, t["destination"] or own
    return own, cc


def _in_surge_corridor(t: Dict) -> bool:
    origin, dest = _corridor(t)
    return dest in storylines.VN_IN["import_countries"] and origin in storylines.VN_IN["source_countries"]


def _base_amount(seed: int, t: Dict, ticket: float) -> None:
    t["amount_usd"] = max(50_000.0, ticket * math.exp((rng.unit(seed, "tramt", t["_key"]) - 0.5) * AMOUNT_SPREAD))
    if _in_surge_corridor(t):
        t["amount_usd"] *= tailwind(t["txn_date"])


def _exact_corridor_lcs(txns: List[Dict], growth: float, kino_party: Optional[str], kino_growth: float) -> None:
    """Unscripted import LCs into VN/IN from CN/KR/JP (goods other than the scripted electronics /
    auto parts): each (origin, destination) cell grows exactly like the scripted ones in H1 FY2026.
    Kinokawa VN's whole CN->VN book grows exactly like its scripted LCs (+50%)."""
    a, b = storylines.VN_IN["window"]
    a0, b0 = fiscal.same_period_prior_fy(a), fiscal.same_period_prior_fy(b)
    kino_corr = tuple(storylines.KINOKAWA_SCRIPT["corridor"])
    cells: Dict[Tuple, List[List[Dict]]] = defaultdict(lambda: [[], []])
    for t in txns:
        if not t["_key"].startswith("B-"):
            continue
        if t["party_id"] == kino_party and _corridor(t) == kino_corr:
            key = ("kino", kino_growth)
        elif t["product_type"] == "Import LC" and _in_surge_corridor(t):
            key = (_corridor(t), growth)
        else:
            continue
        if a0 <= t["txn_date"] <= b0:
            cells[key][0].append(t)
        elif a <= t["txn_date"] <= b:
            cells[key][1].append(t)
    for (_, g), (prev, cur) in cells.items():
        if prev and cur:
            _rescale(cur, g * _usd(prev))


def _finish_txn(seed: int, t: Dict, ent: Dict, fx: Dict) -> None:
    """Tenor, fees, currency and the corridor fields of one instrument (in place)."""
    k = t["_key"]
    product, cc, own = t["product_type"], t["counterparty_country"], t["booking_location"]
    t["amount_usd"] = round(t["amount_usd"], 2)
    lo, span = TENOR_DAYS[product]
    t["tenor_days"] = int(lo + span * rng.unit(seed, "trten", k))
    t["maturity_date"] = t["txn_date"] + TD(days=t["tenor_days"])
    fee_bps = FEE_BPS[product] * (0.85 + 0.3 * rng.unit(seed, "trfee", k))
    fee_bps *= JC_FEE_FACTOR if ent["segment"] == "Japanese Corporate" else 1.0
    t["fee_bps"] = round(fee_bps, 2)
    t["commission_usd"] = round(t["amount_usd"] * t["fee_bps"] / 10000.0
                                * (t["tenor_days"] / 365.0 if product in PER_ANNUM_FEES else 1.0), 2)
    t["origin_country"], t["destination_country"] = _corridor(t)
    t["beneficiary_country"] = cc if (product == "Guarantee / SBLC" or t["direction"] == "Import") else own
    t["counterparty_bank_country"] = cc if rng.unit(seed, "trbank", k) < 0.85 else rng.choice(
        seed, [h for h in BANK_HUBS if h != cc], "trbankh", k)
    ccy, p_ccy = TXN_CCY.get(cc, ("USD", 0.0))
    t["currency"] = ccy if rng.unit(seed, "trccy", k) < p_ccy else "USD"
    t["amount_ccy"] = round(t["amount_usd"] * fx.get((t["currency"], t["txn_date"]), 1.0), 2)
    t["hs_chapter"] = HS_CHAPTER[t["commodity"]]
    t["is_sustainable_trade"] = rng.unit(seed, "trgrn", k) < SUSTAINABLE_SHARE


def health_lookup(health_rows: List[Dict]) -> Dict[str, Dict[D, float]]:
    """entity_id -> {month start: latent health} from ops.synthetic_entity_health_monthly rows."""
    out: Dict[str, Dict[D, float]] = defaultdict(dict)
    for h in health_rows:
        out[h["entity_id"]][h["month"]] = h["health"]
    return out


def _months_back(m: D, n: int) -> D:
    y, mo = divmod(m.year * 12 + m.month - 1 - n, 12)
    return D(y, mo + 1, 1)


def discrepancy_propensity(health_by_month: Dict[D, float], booking_country: str,
                           product: str) -> Callable[[D], float]:
    """Chance a document presentation is discrepant in a month: higher for weak clients and
    sharply higher when health fell over the past year (the early-warning input)."""
    cf = DISC_COUNTRY.get(booking_country, 1.0) * (1.1 if product == "Export LC" else 1.0)

    def p(d: D) -> float:
        m = d.replace(day=1)
        h = health_by_month.get(m, 0.6)
        h0 = health_by_month.get(_months_back(m, 12), h)
        return min(0.8, max(0.05, (DISC_BASE + DISC_HEALTH * (1 - h) + DISC_DETERIORATION * max(0.0, h0 - h)) * cf))
    return p


def build_trade_txns(cfg, entities: List[Dict], rels: List[Dict], health: Dict[str, Dict[D, float]],
                     fx: Optional[Dict] = None) -> List[Dict]:
    """bronze.trade_finance_txn rows: base instruments (count ~ sqrt(turnover), dates stratified
    per client so corridor volumes are smooth) + the scripted VN/IN surge; status, closed date and
    outstanding from `instrument_lifecycle`."""
    seed = cfg.random_seed
    as_of = D.fromisoformat(cfg.as_of_date)
    fx = fx if fx is not None else fx_lookup(cfg)
    by_id = {e["entity_id"]: e for e in entities}
    rel_by_party = {r["party_id"]: r for r in rels}
    sampler = DateSampler(LEGACY_START, as_of)
    surge = vn_in_surge_txns(cfg, entities, rels, sampler) if storylines.on(cfg, storylines.VN_IN_TRADE) else []
    n_base = max(2000, round(TXNS_AT_SCALE_1 * cfg.scale)) - sum(r["txn_date"] >= TXN_START for r in surge)
    turnovers = sorted(r["trade_annual_turnover_usd"] for r in rels)
    starts = [_rel_start(seed, r, as_of, turnovers[len(turnovers) // 2]) for r in rels]
    window = (as_of - TXN_START).days
    counts = _largest_remainder(n_base, [math.sqrt(r["trade_annual_turnover_usd"]) * (as_of - s).days / window
                                         for r, s in zip(rels, starts)], minimum=3)
    txns, ticket = list(surge), {}
    for r, n, start in zip(rels, counts, starts):
        party = r["party_id"]
        ticket[party] = r["trade_annual_turnover_usd"] * (as_of - start).days / 365.25 / n
        goods = client_commodities(by_id[r["entity_id"]]["industry_subsector"])
        books = [("B", start, as_of, n)]
        if start == TXN_START:  # same density over FY2023; only instruments still open at TXN_START are kept
            books.append(("L", LEGACY_START, TXN_START, round(n * (TXN_START - LEGACY_START).days / window)))
        for book, a, b, m in books:
            dates = [sampler.at(a, b, (k + rng.unit(seed, "trpos", party, book, k)) / m) for k in range(m)]
            products = _allocate_by_half(seed, party, f"trprodk{book}", dates, TRADE_PRODUCTS)
            dirs = [_direction(seed, r, p, k) for k, p in enumerate(products)]
            ccs = _allocate_by_half(seed, party, f"trcorrk{book}", dates, client_corridors(seed, r),
                                    [f"{p}|{d}" for p, d in zip(products, dirs)])
            for k in range(m):
                t = _base_txn(seed, r, products[k], dirs[k], ccs[k], goods, k, dates[k])
                t["_key"] = f"{book}-{party}-{k}"
                _base_amount(seed, t, ticket[party])
                txns.append(t)
    if surge:
        kino = {r["party_id"] for r in surge if r["_kind"] == "kino"}
        _exact_corridor_lcs(txns, 1.0 + storylines.VN_IN["yoy_growth"], next(iter(kino), None),
                            1.0 + storylines.KINOKAWA_SCRIPT["corridor_growth"])
    for t in txns:
        _finish_txn(seed, t, by_id[rel_by_party[t["party_id"]]["entity_id"]], fx)
    txns.sort(key=lambda t: (t["txn_date"], t["party_id"], t["_key"]))
    out = []
    for i, t in enumerate(txns):
        t["txn_id"] = f"TRD-{i:09d}"
        ent_id = rel_by_party[t["party_id"]]["entity_id"]
        life = instrument_lifecycle(seed, t, discrepancy_propensity(health.get(ent_id, {}), t["booking_location"],
                                                                    t["product_type"]), as_of)
        if t["txn_date"] < TXN_START and life["closed_date"] and life["closed_date"] < TXN_START:
            continue  # legacy instrument already closed before the window: purged from the platform
        t.update(status=life["status"], closed_date=life["closed_date"], outstanding_usd=life["outstanding_usd"])
        out.append({c: t[c] for c in TXN_COLUMNS})
    party_map = {r["entity_id"]: r["party_id"] for r in rels}
    storyline_injectors.meridian_trade_scale(cfg, entities, party_map, out)   # storyline 3
    return out


TXN_COLUMNS = ["txn_id", "party_id", "txn_date", "product_type", "direction", "origin_country",
               "destination_country", "counterparty_country", "counterparty_bank_country", "beneficiary_country",
               "commodity", "hs_chapter", "currency", "amount_ccy", "amount_usd", "tenor_days", "maturity_date",
               "status", "closed_date", "outstanding_usd", "fee_bps", "commission_usd", "is_sustainable_trade",
               "booking_location"]


# ---- lifecycle ----------------------------------------------------------------------------
def _ts(d: D, hours: float) -> _dt.datetime:
    return _dt.datetime(d.year, d.month, d.day) + _dt.timedelta(hours=hours)


def _presentation(seed: int, t: Dict, no: int, recv: D, amount: float, disc_p: Callable[[D], float],
                  re_present: bool) -> Dict:
    key = (t["txn_id"], no)
    team = OPS_TEAM.get(t["booking_location"], "SG Trade Ops Hub")
    discrepant = (not re_present) and rng.unit(seed, "prdisc", *key) < disc_p(recv)
    types: List[str] = []
    if discrepant:
        for j in range(_pick(seed, [(1, 60), (2, 30), (3, 10)], "prnd", *key)):
            dt = _pick(seed, DISCREPANCIES, "prdt", *key, j)
            if dt not in types:
                types.append(dt)
    hours = (rng.lognormal(seed, math.log(CHECK_MEDIAN_HOURS), 0.45, "prtat", *key) * TEAM_SPEED.get(team, 1.0)
             * (1.8 if discrepant else 1.0) * (1.25 if recv.month in (3, 6, 9, 12) else 1.0))
    received = _ts(recv, 9.0 + 8.5 * rng.unit(seed, "prrcv", *key))
    refuse_p = min(0.6, 0.12 + 0.6 * max(0.0, disc_p(recv) - 0.2))
    decision = ("Complying" if not discrepant else
                "Discrepant - Refused" if rng.unit(seed, "prref", *key) < refuse_p else "Discrepant - Waived")
    return {"presentation_no": no, "is_re_presentation": re_present, "received_ts": received,
            "checked_ts": received + _dt.timedelta(hours=hours), "turnaround_hours": round(hours, 2),
            "is_discrepant": discrepant, "discrepancy_types": types, "decision": decision,
            "amount_usd": round(amount, 2), "ops_team": team}


def _lc_plan(seed: int, t: Dict, disc_p: Callable[[D], float]) -> Tuple[List[Tuple], List[Dict]]:
    tid, issue, expiry, amt = t["txn_id"], t["txn_date"], t["maturity_date"], t["amount_usd"]
    validity = max(10, (expiry - issue).days)
    ev: List[Tuple] = [(issue, "Issue", amt, ISSUE_DETAIL[t["product_type"]], None)]
    lc_amt = amt
    n_am = _pick(seed, [(0, 70), (1, 24), (2, 6)], "lcnam", tid)
    for j, frac in enumerate(sorted(0.08 + 0.5 * rng.unit(seed, "lcamd", tid, j) for j in range(n_am))):
        kind = _pick(seed, AMENDMENTS, "lcamk", tid, j)
        delta = (amt * (0.05 + 0.10 * rng.unit(seed, "lcamv", tid, j)) * (1 if kind == "Increase Amount" else -1)
                 if kind in ("Increase Amount", "Decrease Amount") else 0.0)
        lc_amt += delta
        ev.append((issue + TD(days=max(1, int(validity * frac))), "Amend", round(delta, 2), kind, None))
    n_pres = _pick(seed, [(0, 7), (1, 76), (2, 17)], "lcnp", tid)
    fracs = {0: [], 1: [0.35 + 0.5 * rng.unit(seed, "lcpf", tid, 0)],
             2: [0.25 + 0.3 * rng.unit(seed, "lcpf", tid, 0), 0.62 + 0.3 * rng.unit(seed, "lcpf", tid, 1)]}[n_pres]
    full = rng.unit(seed, "lcfull", tid) < FULL_DRAW_SHARE  # fully drawn: nothing left to lapse
    shares = [1.0 if full else 0.92 + 0.07 * rng.unit(seed, "lcps", tid)] if n_pres == 1 else (
        [0.45 + 0.1 * rng.unit(seed, "lcps", tid), 0.0] if n_pres == 2 else [])
    if n_pres == 2:
        shares[1] = (1 - shares[0]) * (1.0 if full else 0.9 + 0.09 * rng.unit(seed, "lcps2", tid))
    usance = USANCE_DAYS[rng.hash64(seed, "lcusd", tid) % 4] if rng.unit(seed, "lcus", tid) < USANCE_SHARE else 0
    pres, no = [], 0
    for frac, share in zip(fracs, shares):
        recv = issue + TD(days=max(2, int(validity * frac)))
        attempt_amt, re_present = lc_amt * share, False
        while True:
            no += 1
            p = _presentation(seed, t, no, recv, attempt_amt, disc_p, re_present)
            pres.append(p)
            ev.append((recv, "Present", p["amount_usd"], "Documents Presented", no))
            done = p["checked_ts"].date()
            if p["decision"] == "Discrepant - Refused":
                retry = done + TD(days=rng.randint(seed, 4, 12, "lcre", tid, no))
                if retry < expiry and not re_present:
                    recv, re_present = retry, True
                    continue
                break
            honour = done + TD(days=rng.randint(seed, 1, 5, "lcwv", tid, no)) if p["is_discrepant"] else done
            if usance:
                ev.append((honour, "Accept", p["amount_usd"], f"Accepted - {usance} Days Usance", no))
                ev.append((honour + TD(days=usance), "Pay", -p["amount_usd"], "Payment at Usance Maturity", no))
            else:
                ev.append((honour + TD(days=rng.randint(seed, 0, 2, "lcpay", tid, no)), "Pay", -p["amount_usd"],
                           "Payment Effected", no))
            break
    paid = -sum(a for _, kind, a, _, _ in ev if kind == "Pay")
    if lc_amt - paid > 0.01:  # the undrawn remainder (or the whole LC) lapses at expiry
        ev.append((expiry, "Expire", -(lc_amt - paid), "Expired Unutilised" if paid == 0 else "Undrawn Balance Lapsed", None))
    return ev, pres


def _other_plan(seed: int, t: Dict) -> List[Tuple]:
    tid, product, issue, mat, amt = t["txn_id"], t["product_type"], t["txn_date"], t["maturity_date"], t["amount_usd"]
    ev: List[Tuple] = [(issue, "Issue", amt, ISSUE_DETAIL[product], None)]
    if product == "Guarantee / SBLC":
        cur = amt
        if rng.unit(seed, "gtam", tid) < 0.15:
            delta = round(amt * (0.05 + 0.15 * rng.unit(seed, "gtamv", tid)), 2)
            cur += delta
            ev.append((issue + TD(days=int((mat - issue).days * 0.3)), "Amend", delta, "Increase Amount", None))
        if rng.unit(seed, "gtclaim", tid) < 0.02:
            claim = issue + TD(days=int((mat - issue).days * (0.3 + 0.6 * rng.unit(seed, "gtcd", tid))))
            ev += [(claim, "Claim", cur, "Demand Received", None),
                   (claim + TD(days=rng.randint(seed, 5, 20, "gtcp", tid)), "Pay", -cur, "Claim Paid", None)]
        else:
            ev.append((mat, "Expire", -cur, "Guarantee Expired", None))
    elif product == "Documentary Collection":
        present = issue + TD(days=rng.randint(seed, 3, 12, "dcpr", tid))
        ev.append((present, "Present", amt, "Documents Presented to Drawee", None))
        u = rng.unit(seed, "dcterm", tid)
        if u < 0.04:
            ev.append((present + TD(days=rng.randint(seed, 20, 40, "dcret", tid)), "Expire", -amt,
                       "Documents Returned Unpaid", None))
        elif u < 0.32:  # documents against acceptance
            acc = present + TD(days=rng.randint(seed, 1, 5, "dcacc", tid))
            ev += [(acc, "Accept", amt, "Draft Accepted (D/A)", None),
                   (max(mat, acc + TD(days=30)), "Pay", -amt, "Collection Proceeds Paid", None)]
        else:  # documents against payment
            ev.append((present + TD(days=rng.randint(seed, 1, 10, "dcpay", tid)), "Pay", -amt,
                       "Collection Proceeds Paid", None))
    elif product == "Trade Loan":
        due, cur = mat, amt
        if rng.unit(seed, "tlroll", tid) < 0.06:
            due = mat + TD(days=rng.randint(seed, 30, 90, "tlrolld", tid))
            ev.append((mat - TD(days=5), "Amend", 0.0, "Tenor Extension", None))
        if rng.unit(seed, "tlpre", tid) < 0.10:
            part = round(amt * (0.2 + 0.3 * rng.unit(seed, "tlprev", tid)), 2)
            cur -= part
            ev.append((issue + TD(days=int((mat - issue).days * 0.5)), "Pay", -part, "Partial Prepayment", None))
        ev.append((due, "Pay", -cur, "Loan Repaid", None))
    else:  # Receivables Purchase: the obligor pays at maturity, a few late
        late = rng.randint(seed, 5, 30, "rplated", tid) if rng.unit(seed, "rplate", tid) < 0.05 else 0
        ev.append((mat + TD(days=late), "Pay", -amt, "Obligor Paid" if not late else "Obligor Paid Late", None))
    return ev


EVENT_ORDER = ["Issue", "Amend", "Present", "Claim", "Accept", "Pay", "Expire"]


def instrument_lifecycle(seed: int, t: Dict, disc_p: Callable[[D], float], as_of: D) -> Dict:
    """Events and (LC) document presentations of one instrument known at as-of, its status
    (Issued / Negotiated / Settled / Expired), closed date and outstanding.

    `t` needs txn_id, product_type, txn_date, maturity_date, amount_usd and booking_location;
    `disc_p(date)` is the client's discrepancy propensity. Plan tuples are (date, type, amount
    signed as its effect on the outstanding, detail, presentation_no)."""
    pres: List[Dict] = []
    if t["product_type"] in ("Import LC", "Export LC"):
        plan, pres = _lc_plan(seed, t, disc_p)
    else:
        plan = _other_plan(seed, t)
    plan.sort(key=lambda e: (e[0], EVENT_ORDER.index(e[1])))
    events, out = [], 0.0
    for d, kind, amt, detail, no in plan:
        if d > as_of:
            break
        out = max(0.0, out + (amt if kind in ("Issue", "Amend", "Pay", "Expire") else 0.0))
        events.append({"event_date": d, "event_type": kind, "amount_usd": round(abs(amt), 2),
                       "outstanding_after_usd": round(out, 2), "detail": detail, "presentation_no": no})
    pres = [p for p in pres if p["received_ts"].date() <= as_of]
    for p in pres:  # still under examination at the as-of cut-off: outcome not known yet
        if p["checked_ts"] > _ts(as_of, 24.0):
            p.update(checked_ts=None, turnaround_hours=None, decision="Pending Examination",
                     is_discrepant=None, discrepancy_types=[])
    closing = events[-1]["event_date"] if events[-1]["event_type"] in ("Pay", "Expire") and out <= 0.01 else None
    if closing:
        status = "Settled" if any(e["event_type"] == "Pay" for e in events) else "Expired"
    else:
        status = "Negotiated" if any(e["event_type"] == "Accept" for e in events) else "Issued"
    return {"events": events, "presentations": pres, "status": status, "closed_date": closing,
            "outstanding_usd": round(out, 2)}


def surge_measures(txns: List[Dict], window: Tuple[D, D] = storylines.VN_IN["window"]) -> Dict[str, float]:
    """Storyline-6 numbers from trade_finance_txn rows: YoY growth (count, USD) of the scripted
    import LCs and the Singapore booking share in H1 FY2026."""
    a, b = window
    a0, b0 = fiscal.same_period_prior_fy(a), fiscal.same_period_prior_fy(b)
    sel = [t for t in txns if _is_surge(t["product_type"], t["destination_country"], t["origin_country"], t["commodity"])]
    cur = [t for t in sel if a <= t["txn_date"] <= b]
    prev = [t for t in sel if a0 <= t["txn_date"] <= b0]
    sg = [t for t in cur if t["booking_location"] == "SG"]

    def usd(rows):
        return sum(t["amount_usd"] for t in rows)
    return {"n_cur": len(cur), "n_prev": len(prev), "yoy_count": len(cur) / len(prev) - 1,
            "yoy_usd": usd(cur) / usd(prev) - 1, "sg_share_count": len(sg) / len(cur), "sg_share_usd": usd(sg) / usd(cur)}
