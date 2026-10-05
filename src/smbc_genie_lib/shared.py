"""Shared lakehouses: simulated Delta Sharing from JP / EMEA / AMER (brief §3.2, §6.1 share_*;
D26, D27, D41; storylines 4 and 13).

What the other regions publish to the APAC lakehouse, keyed by the providers' own ids plus the
global group id (= truth group id, the bank's global group-master key):

  share_jp_group_master             380 groups as mastered by Tokyo HO: JP parent legal name,
                                    global tier, global RM, owner region
  share_jp_parent_exposure_monthly  JP-booked committed / drawn / deposits / revenue per JP parent
                                    (or JP-booked group entity) x product family x month
  share_jp_parent_financials        JP parents' consolidated financials (JPY, March FYE)
  share_jp_parent_rating            Tokyo HO internal rating history of each JP parent
  share_jp_support_letters          keepwell / parent-guarantee register for APAC subsidiaries
  share_<emea|amer>_entity_master   ~600 EMEA / AMER subsidiaries of the client groups
  share_<emea|amer>_exposure_monthly, share_<emea|amer>_revenue_monthly
  bronze.dq_share_refresh_log       the recipient's 09:00 SGT freshness check per table and day

Each provider table refreshes daily (one Delta version per refresh). Rows carry the version and
publish time that last wrote them (_provider_version, _shared_at), so an outage shows in the rows
as well as in the log. Regional revenue is calibrated to the group's APAC revenue, so the APAC
share of group revenue is realistic (Japanese groups mostly 10-45%). Storyline 4: Tanaka's JP
parent keeps a keepwell for its Singapore lead and is upgraded 4 -> 3 on 18-Jun-2026. Storyline
13: the parent-rating table gets no refresh 11-15 Aug 2026 (lag > 24h on exactly those days).
Pure Python and deterministic.
"""
from __future__ import annotations

import bisect
import datetime as _dt
import math
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple

from . import crm, fragment, health, names, profitability as prof, reference, rng, storylines as S

D, TD, DT = _dt.date, _dt.timedelta, _dt.datetime
JC, NJLC = "Japanese Corporate", "Non-Japanese Large Corporate"
FI, SSF = "Financial Institution", "Sponsor & Structured Finance"

# ---- Delta Sharing simulation -------------------------------------------------------------
INITIAL_LOAD = D(2024, 3, 31)     # shares go live: the initial load publishes all history
LOG_START = D(2024, 4, 1)         # first daily freshness check
CHECK_HOUR = 9                    # the recipient's freshness monitor runs at 09:00 SGT
# provider refresh times (SGT): JP lands before the check, EMEA / AMER the previous afternoon /
# evening, so normal lags are ~4h / ~17h / ~12h. Jitter +-40 min; 2.5% of runs are 1-3h late.
REFRESH_AT = {"JP": (5, 0), "EMEA": (15, 30), "AMER": (20, 30)}
JITTER_MIN, LATE_P, LATE_MINUTES = 40, 0.025, (60, 180)
STALE_HOURS = S.SHARE_STALE["lag_hours_min"]
SHARE_NAME = {"JP": "jp_head_office_share", "EMEA": "emea_regional_share", "AMER": "amer_regional_share"}
TABLES = [("JP", "share_jp_group_master"), ("JP", "share_jp_parent_exposure_monthly"),
          ("JP", "share_jp_parent_financials"), ("JP", "share_jp_parent_rating"),
          ("JP", "share_jp_support_letters"),
          ("EMEA", "share_emea_entity_master"), ("EMEA", "share_emea_exposure_monthly"),
          ("EMEA", "share_emea_revenue_monthly"),
          ("AMER", "share_amer_entity_master"), ("AMER", "share_amer_exposure_monthly"),
          ("AMER", "share_amer_revenue_monthly")]
REGION_OF = {t: r for r, t in TABLES}
# additive provider schema changes, not back-filled: rows last written before them carry NULL
SCHEMA_DRIFT = {
    "share_emea_exposure_monthly": (D(2025, 1, 15), "ifrs9_stage", "ADD COLUMN ifrs9_stage INT"),
    "share_amer_entity_master": (D(2025, 6, 2), "naics_code", "ADD COLUMN naics_code STRING"),
    "share_jp_parent_financials": (D(2026, 6, 1), "ghg_scope12_kt", "ADD COLUMN ghg_scope12_kt DOUBLE"),
}
INCIDENTS, INCIDENT_WINDOW = 4, (D(2024, 6, 1), D(2026, 3, 31))  # isolated failed EMEA/AMER runs
OUTAGE_MESSAGE = "Rating-system extract not delivered during the JP HO Obon maintenance window; table not refreshed."
INCIDENT_MESSAGE = "Provider refresh job failed (cluster start-up timeout); picked up by the next scheduled run."

# ---- who has what outside APAC --------------------------------------------------------------
APAC_ONLY = {"sunda", "meridian", "banksia", "tanaka"}   # storyline groups with no EMEA/AMER entities
JP_PARENT_WORDS = {FI: {"Leasing", "Insurance", "Securities", "Credit"}, SSF: {"Investments", "Capital Partners"}}
JP_PARENT_P = 0.35          # share of those Japanese-owned (most non-Japanese groups have no JP parent)
JP_BOOKED_P = {NJLC: 0.15, FI: 0.10}   # Tokyo-booked lending to Strategic/Core non-Japanese groups
PRESENCE_P = {JC: {"Strategic": 0.85, "Core": 0.65, "Transactional": 0.35},
              NJLC: {"Strategic": 0.45, "Core": 0.25, "Transactional": 0.10},
              FI: {"Strategic": 0.50, "Core": 0.35, "Transactional": 0.20},
              SSF: {"Strategic": 0.40, "Core": 0.30, "Transactional": 0.15}}
TIER_W = {"Strategic": 2.0, "Core": 1.2, "Transactional": 0.7}
SEGMENT_W = {JC: 1.3, NJLC: 1.0, FI: 0.9, SSF: 0.8}
EMEA_P = {JC: 0.55, NJLC: 0.45, FI: 0.60, SSF: 0.60}   # chance a regional entity sits in EMEA
EMEA_P_SUB = {"Auto Parts": 0.40, "Vehicles": 0.40, "Mining": 0.35, "Minerals": 0.35,
              "Natural Resources": 0.40, "Chemicals": 0.65}
# Hayashi (brief 5.1 Q4): a scripted global footprint (region, country, city)
HAYASHI_FOOTPRINT = [("EMEA", "NL", "Rotterdam"), ("EMEA", "DE", "Hamburg"), ("EMEA", "GB", "London"),
                     ("EMEA", "AE", "Dubai"), ("AMER", "US", "Los Angeles"), ("AMER", "PA", "Panama City"),
                     ("AMER", "BR", "Sao Paulo")]
HAYASHI_SPLIT = {"JP": 0.55, "EMEA": 0.27, "AMER": 0.18}
APAC_SHARE_SCRIPT = {"hayashi": 0.22, "kinokawa": 0.30, "tanaka": 0.10}
MIN_APAC_REVENUE = 25_000.0   # USD/yr floor for groups with almost no APAC revenue

# country -> (name qualifier, legal form, cities, booking office, reporting currency)
COUNTRIES = {
    "GB": ("(UK)", "Ltd", ["London"], "London", "GBP"),
    "DE": ("Deutschland", "GmbH", ["Dusseldorf", "Frankfurt", "Hamburg"], "Dusseldorf", "EUR"),
    "NL": ("Nederland", "BV", ["Amsterdam", "Rotterdam"], "Amsterdam", "EUR"),
    "FR": ("France", "SAS", ["Paris", "Lyon"], "Paris", "EUR"),
    "IT": ("Italia", "Srl", ["Milan", "Turin"], "Milan", "EUR"),
    "ES": ("Iberica", "SL", ["Madrid", "Barcelona"], "Madrid", "EUR"),
    "BE": ("Belgium", "NV", ["Brussels", "Antwerp"], "Amsterdam", "EUR"),
    "PL": ("Polska", "Sp z o.o.", ["Warsaw", "Wroclaw"], "Dusseldorf", "EUR"),
    "CZ": ("Czech", "s.r.o.", ["Prague", "Plzen"], "Dusseldorf", "EUR"),
    "SE": ("Nordic", "AB", ["Stockholm", "Gothenburg"], "Amsterdam", "EUR"),
    "CH": ("Switzerland", "AG", ["Zurich", "Geneva"], "Dusseldorf", "EUR"),
    "LU": ("Luxembourg", "Sarl", ["Luxembourg"], "Amsterdam", "EUR"),
    "AE": ("Middle East", "FZE", ["Dubai", "Abu Dhabi"], "Dubai", "USD"),
    "ZA": ("South Africa", "(Pty) Ltd", ["Johannesburg", "Durban"], "London", "USD"),
    "TR": ("Turkey", "AS", ["Istanbul", "Izmir"], "Dubai", "USD"),
    "US": ("USA", "Inc", ["New York", "Los Angeles", "Chicago", "Houston", "Detroit", "Atlanta"], None, "USD"),
    "CA": ("Canada", "Ltd", ["Toronto", "Vancouver", "Calgary"], "Toronto", "USD"),
    "MX": ("Mexico", "SA de CV", ["Mexico City", "Monterrey", "Guadalajara"], "Mexico City", "USD"),
    "BR": ("do Brasil", "Ltda", ["Sao Paulo", "Rio de Janeiro"], "Sao Paulo", "USD"),
    "CL": ("Chile", "SpA", ["Santiago"], "New York", "USD"),
    "PA": ("Panama", "SA", ["Panama City"], "New York", "USD"),
}
US_OFFICE = {"Detroit": "Chicago", "Atlanta": "New York"}   # else the city's own office
COUNTRY_W = {"EMEA": {"GB": 22, "DE": 17, "NL": 14, "FR": 9, "IT": 6, "ES": 5, "BE": 4, "PL": 5, "CZ": 3,
                      "SE": 2, "CH": 2, "LU": 1, "AE": 6, "ZA": 2, "TR": 2},
             "AMER": {"US": 58, "MX": 14, "CA": 12, "BR": 11, "CL": 3, "PA": 2}}
HQ_W = {"EMEA": {"NL": 35, "GB": 35, "DE": 30}, "AMER": {"US": 1}}
COUNTRY_TILT = {   # industry / segment multipliers on the country mix
    "Auto Parts": {"MX": 3, "CZ": 2.5, "PL": 2.5, "DE": 1.5}, "Vehicles": {"MX": 3, "CZ": 2, "PL": 2},
    "Shipping": {"NL": 2, "PA": 6, "AE": 2.5, "BR": 1.5}, "Logistics": {"NL": 2, "BE": 2, "AE": 2},
    "Mining": {"CA": 3, "CL": 6, "ZA": 4}, "Minerals": {"CA": 3, "CL": 5, "ZA": 3},
    "Natural Resources": {"CA": 3, "CL": 4, "BR": 2}, "Oil, Gas & Coal": {"CA": 2, "AE": 3, "BR": 2},
    "Semiconductors": {"NL": 2, "DE": 1.5}, "Electronics": {"DE": 1.5, "MX": 2, "CZ": 1.5},
    "Chemicals": {"DE": 2, "BE": 2.5, "NL": 1.5}, "Steel": {"DE": 1.5, "BR": 2, "MX": 1.5, "TR": 2},
    FI: {"GB": 2.5, "LU": 6, "US": 1.4}, SSF: {"GB": 2, "LU": 6, "US": 1.3},
}
REGIONAL_SEGMENT = {"EMEA": {JC: "Japanese Corporates", NJLC: "Corporates", FI: "Financial Institutions",
                             SSF: "Financial Sponsors"},
                    "AMER": {JC: "Japanese Corporate", NJLC: "Non-Japanese Corporate", FI: "FIG", SSF: "Sponsors"}}
NAICS_BY_SECTOR = {"Automotive": "336390", "Technology": "334413", "Materials": "325998", "Industrials": "333249",
                   "Transport & Logistics": "488510", "Trading Houses": "425120", "Consumer": "311999",
                   "Energy": "211120", "Utilities": "221118", "Agriculture": "111998", "Telecom": "517111",
                   "Infrastructure": "237990", "Real Estate": "531120", "Financial Institution": "523999",
                   "Sponsor": "523940"}

# ---- products and pricing -------------------------------------------------------------------
LENDING = ("Corporate Lending", "Structured Lending", "Sustainable Finance")
DEPOSITS = ("Cash", "Liquidity")
TRADE = "Trade Finance"
FAMILIES = list(LENDING) + [TRADE] + list(DEPOSITS) + ["Payments", "FX", "Rates"]
BALANCE_FAMILIES = set(LENDING) | {TRADE} | set(DEPOSITS)
REVENUE_TYPE = {**{f: "Net Interest Income" for f in LENDING + DEPOSITS}, TRADE: "Fees & Commissions",
                "Payments": "Fees & Commissions", "FX": "Trading Revenue", "Rates": "Trading Revenue"}
# annual margin on drawn lending / fee on trade outstanding; NII spread on deposits. Japanese parents
# price tightest; Tokyo-booked USD loans to non-Japanese groups carry BOOKED_MARGIN_X.
MARGIN = {r: dict(zip(LENDING + (TRADE,), m)) for r, m in (("JP", (0.0045, 0.0120, 0.0040, 0.0050)),
                                                            ("EMEA", (0.0110, 0.0200, 0.0090, 0.0080)),
                                                            ("AMER", (0.0130, 0.0230, 0.0100, 0.0085)))}
SPREAD = {"JP": {"Cash": 0.0015, "Liquidity": 0.0008}, "EMEA": {"Cash": 0.0080, "Liquidity": 0.0040},
          "AMER": {"Cash": 0.0150, "Liquidity": 0.0080}}
BOOKED_MARGIN_X = 2.2
COMMIT_FEE_SHARE = 0.25     # undrawn commitment fee as a share of the drawn margin
REV_WEIGHT = {"Corporate Lending": 40, "Structured Lending": 15, "Sustainable Finance": 6, TRADE: 10,
              "Cash": 18, "Liquidity": 5, "Payments": 7, "FX": 9, "Rates": 4}
FAMILY_P = {"parent": {"Corporate Lending": 0.97, "Cash": 0.95, "Liquidity": 0.50, "Payments": 0.80, "FX": 0.70,
                       "Rates": 0.35, TRADE: 0.30, "Structured Lending": 0.15, "Sustainable Finance": 0.18},
            "sub": {"Cash": 0.85, "Corporate Lending": 0.60, "Payments": 0.60, "FX": 0.45, TRADE: 0.25,
                    "Liquidity": 0.20, "Structured Lending": 0.06, "Sustainable Finance": 0.08, "Rates": 0.10}}
TRADE_HEAVY = {"General Trading", "Steel", "Food & Beverage", "Auto Parts", "Vehicles", "Electronics", "Chemicals",
               "Agribusiness", "Palm Oil"}
GROWTH = {"parent": (0.02, 0.04), "booked": (0.03, 0.05), "sub": (0.05, 0.06)}   # annual (mean, sd)
GREEN_GROWTH = 0.35

# ---- Tokyo HO coverage -----------------------------------------------------------------------
GLOBAL_TIER = {"Strategic": "Global Strategic", "Core": "Global Core", "Transactional": "Global Standard"}
GLOBAL_SEGMENT = {JC: "Japanese Corporate", NJLC: "Non-Japanese Corporate", FI: "Financial Institutions",
                  SSF: "Sponsor Coverage", "Public Sector": "Public Sector"}
COVERAGE_DEPT = {"Automotive": 1, "Industrials": 1, "Technology": 2, "Trading Houses": 3,
                 "Transport & Logistics": 3, "Materials": 4, "Energy": 4, "Utilities": 4}
DEPT_NAME = {1: "Automotive & Machinery", 2: "Electronics & Technology", 3: "Trading & Logistics",
             4: "Materials & Energy", 5: "Consumer & Real Estate"}
TOKYO_RMS_PER_DEPT = 8
JP_GIVEN = ["Kenji", "Haruka", "Takeshi", "Yuko", "Daisuke", "Emi", "Shota", "Naoko", "Ryo", "Mai",
            "Kazuki", "Sayaka", "Tetsuya", "Ayumi", "Hiroshi", "Rie"]
JP_FAMILY = ["Morimoto", "Ishikawa", "Nakagawa", "Fujiwara", "Kondo", "Sakamoto", "Hirano", "Murata",
             "Ogawa", "Kubo", "Endo", "Aoki", "Yoshida", "Matsuda", "Kaneko", "Okada", "Hayakawa"]
JP_PARENT_FORMS = ["Co Ltd", "Corporation", "KK"]
STORYLINE_STEMS = {"Kinokawa", "Hayashi", "Tanaka", "Aokumo", "Shiramine", "Hoshioka"}
CORE_SUFFIXES = (" Berhad Group", " Holdings", " Group")
EXTRA_QUALIFIERS = ["Global", "Summit", "Crest", "Apex", "Prime"]   # names beyond the division words
EMEA_RMS, AMER_RMS = 30, 25

# ---- parent ratings and financials ------------------------------------------------------------
RATING_SCALE = ["AAA", "AA+", "AA", "A+", "A", "BBB+", "BBB", "BB+", "BB", "B"]   # = truth group rating map
REVIEW_YEARS = [2022, 2023, 2024, 2025, 2026]   # annual reviews follow the March FY results (Jun-Sep)
CYCLE_NOTCH = 1.6        # notches per unit of sector-cycle change (x parent beta)
IDIO_P = 0.06            # per-year chance of an idiosyncratic one-notch move
EVENT_DRIVEN_P = 0.40    # share of 2025/2026 downgrades taken early, out of cycle (Jan-Mar)
RATING_REASON = {
    "Initial": ("Rating carried over on migration to the global group master; strong domestic franchise "
                "and conservative balance sheet."),
    "Affirm": ("Rating affirmed on FY{fy} results: earnings and leverage in line with expectations, "
               "liquidity comfortable; outlook {outlook}."),
    "Upgrade": ("Upgrade on FY{fy} results: stronger consolidated earnings, lower leverage and steady cash "
                "generation; support for overseas subsidiaries remains group policy."),
    "Downgrade": ("Downgrade on FY{fy} results: weaker earnings amid the {sector} downturn and higher leverage; "
                  "liquidity adequate with committed bank lines."),
    "Event-Driven": ("Event-driven downgrade after a profit warning on FY{fy} earnings and softer {sector} demand, "
                     "ahead of the annual review."),
    "tanaka": ("Upgrade on FY2025 results: improved consolidated earnings and lower leverage; parent support to "
               "APAC subsidiaries reaffirmed, including the keepwell for the Singapore subsidiary."),
}
FIN_YEARS = [2022, 2023, 2024, 2025]
PARENT_REVENUE_FLOOR_USD = 250_000_000.0
EBITDA_BASE = {"Trading Houses": 0.05, "Financial Institution": 0.28, "Sponsor": 0.30, "Technology": 0.16,
               "Energy": 0.20, "Materials": 0.13, "Utilities": 0.25, "Automotive": 0.11}
GHG_INTENSITY = {"Steel": 1800, "Chemicals": 650, "Shipping": 900, "Heavy Industry": 500, "Fibre": 300,
                 "Advanced Materials": 250, "Construction": 120, "Vehicles": 60, "Auto Parts": 90}  # t / USD m
TANAKA_KEEPWELL_ISSUED = D(2024, 5, 31)   # signed ahead of the Singapore lead's keepwell-backed RCF


# ---- small helpers ---------------------------------------------------------------------------
def _pick(seed: int, weights: Dict[str, float], *keys) -> str:
    opts = sorted(weights)
    return rng.weighted_choice(seed, opts, [weights[o] for o in opts], *keys)


def _month_starts(start: D, end: D) -> List[D]:
    out, m = [], D(start.year, start.month, 1)
    while m <= end:
        out.append(m)
        m = D(m.year + (m.month == 12), m.month % 12 + 1, 1)
    return out


def _month_end(m: D) -> D:
    return D(m.year + (m.month == 12), m.month % 12 + 1, 1) - TD(days=1)


def _months_back(m: D, k: int) -> D:
    i = m.year * 12 + m.month - 1 - k
    return D(i // 12, i % 12 + 1, 1)


def _ts(t: DT) -> str:
    """ISO-8601 timestamp in Singapore time (UTC+8)."""
    return t.isoformat(timespec="seconds") + "+08:00"


def _at(d: D, hour: int = 18) -> DT:
    return DT(d.year, d.month, d.day, hour)


def _cutoff(cfg) -> DT:
    """The as-of moment the shared tables are read: the last 09:00 freshness check."""
    return _at(D.fromisoformat(cfg.as_of_date), CHECK_HOUR)


def _core_brand(group_name: str) -> str:
    for suf in CORE_SUFFIXES:
        if group_name.endswith(suf):
            return group_name[: -len(suf)]
    return group_name


def _tokyo_spelling(legal_name: str) -> str:
    """Tokyo HO records APAC names without brackets: 'X (Singapore) Pte Ltd' -> 'X Singapore Pte Ltd'."""
    return " ".join(legal_name.replace("(", " ").replace(")", " ").split())


def _rand_date(seed: int, lo: D, hi: D, *keys) -> D:
    return lo + TD(days=rng.randint(seed, 0, (hi - lo).days, *keys))


def _allocate(weights: List[float], total: int) -> List[int]:
    """Counts >= 1 summing to total, proportional to weights (largest remainder)."""
    n, s = len(weights), sum(weights)
    raw = [w / s * (total - n) for w in weights]
    counts = [int(x) + 1 for x in raw]
    order = sorted(range(n), key=lambda i: (-(raw[i] - int(raw[i])), i))
    for k in range(total - sum(counts)):
        counts[order[k % n]] += 1
    return counts


def fx_rates(cfg) -> Dict[Tuple[str, D], float]:
    """(currency, date) -> units per USD for the shared currencies (same series as bronze.fx_rate_daily)."""
    return {(r["currency_code"], r["date"]): r["rate_per_usd"] for r in reference.build_fx_daily(cfg)
            if r["currency_code"] in ("USD", "JPY", "EUR", "GBP")}


def _rate(cfg, fx: Dict, ccy: str, d: D) -> float:
    """Rate on a date; dates before the FX history (e.g. FYE Mar-2023) use its first day."""
    return fx[(ccy, max(d, D.fromisoformat(cfg.history_start)))]


# ---- JP parents, JP-booked counterparties, regional subsidiaries ----------------------------------
def jp_parented(cfg, g: Dict) -> bool:
    """Japanese corporates always have a JP parent; a few Japanese-owned FIs / sponsors do too."""
    if g["segment"] == JC:
        return True
    words = JP_PARENT_WORDS.get(g["segment"], set())
    return (not g["storyline_key"] and g["group_industry"] in words
            and rng.unit(cfg.random_seed, "shjpp", g["group_id"]) < JP_PARENT_P)


def build_jp_parents(cfg, groups: List[Dict], used_names: set) -> Dict[str, Dict]:
    """group_id -> JP parent (Tokyo HO id, legal name, LEI-like id, city, beta, relationship start)."""
    seed = cfg.random_seed
    brands = {g["group_name"] for g in groups}
    stems = [s for s in names.JP_STEMS if s not in STORYLINE_STEMS]
    out: Dict[str, Dict] = {}
    for g in groups:
        if not jp_parented(cfg, g):
            continue
        gid = g["group_id"]
        brand = g["group_name"]
        if g["segment"] != JC:   # a Japanese owner trading under its own brand
            word = "Capital" if g["group_industry"] == "Capital Partners" else g["group_industry"]
            brand = next(b for b in (f"{stems[rng.hash64(seed, 'shjpstem', gid, a) % len(stems)]} {word}"
                                     for a in range(50)) if b not in brands)
            brands.add(brand)
        forms = JP_PARENT_FORMS[rng.hash64(seed, "shjpform", gid) % 3:] + JP_PARENT_FORMS
        legal = next((f"{brand} {f}" for f in forms if f"{brand} {f}" not in used_names), f"{brand} Holdings KK")
        names.assert_clean(legal)
        used_names.add(legal)
        sub = g["industry_subsector"]
        nagoya = sub in ("Vehicles", "Auto Parts", "Machinery") and rng.unit(seed, "shngy", gid) < 0.5
        u = rng.unit(seed, "shcity", gid)
        out[gid] = {
            "jp_parent_id": f"JPP-{len(out) + 1:04d}", "legal_name": legal,
            "lei": fragment.make_lei(f"JPP:{gid}"),
            "city": "Nagoya" if nagoya else ("Osaka" if u < 0.22 else "Tokyo"),
            "beta": round(0.5 + 0.8 * rng.unit(seed, "shpbeta", gid), 4),
            "since": D(1975 + rng.randint(seed, 0, 40, "shsince", gid), rng.randint(seed, 1, 12, "shsincem", gid), 1),
        }
    return out


def jp_booked_counterparties(cfg, groups: List[Dict], entities: List[Dict], parents: Dict) -> Dict[str, Dict]:
    """group_id -> Tokyo-booked lending counterparty for non-Japanese groups without a JP parent."""
    seed = cfg.random_seed
    lead = {e["group_id"]: e for e in entities if e["is_group_lead"]}
    out: Dict[str, Dict] = {}
    for g in groups:
        gid = g["group_id"]
        if (gid in parents or g["storyline_key"] or g["relationship_tier"] == "Transactional"
                or rng.unit(seed, "shbooked", gid) >= JP_BOOKED_P.get(g["segment"], 0.0)):
            continue
        fams = ["Structured Lending" if rng.unit(seed, "shbkfam", gid) < 0.3 else "Corporate Lending"]
        if rng.unit(seed, "shbkrates", gid) < 0.25:
            fams.append("Rates")
        out[gid] = {"jp_counterparty_id": f"JPC-{len(out) + 1:04d}",
                    "name": _tokyo_spelling(lead[gid]["legal_name"]), "families": fams}
    return out


def _regional_counts(cfg, groups: List[Dict], entities: List[Dict]) -> Dict[str, int]:
    """group_id -> number of EMEA + AMER subsidiaries (Hayashi scripted; ~600 in total)."""
    seed = cfg.random_seed
    n_apac = Counter(e["group_id"] for e in entities)
    total = int(cfg.volumes.get("emea_amer_entities", 600))
    elig = [g for g in groups if g["segment"] in PRESENCE_P and g["storyline_key"] not in APAC_ONLY | {"hayashi"}
            and (g["storyline_key"] == "kinokawa"   # the RM storyline group always has a global footprint
                 or rng.unit(seed, "shpres", g["group_id"]) < PRESENCE_P[g["segment"]][g["relationship_tier"]])]
    weights = [math.sqrt(g["deposit_wealth"]) * TIER_W[g["relationship_tier"]] * SEGMENT_W[g["segment"]]
               * n_apac[g["group_id"]] ** 0.35 for g in elig]
    counts = dict(zip((g["group_id"] for g in elig), _allocate(weights, total - len(HAYASHI_FOOTPRINT))))
    counts.update({g["group_id"]: len(HAYASHI_FOOTPRINT) for g in groups if g["storyline_key"] == "hayashi"})
    return counts


def _country(seed: int, g: Dict, region: str, is_hq: bool, *keys) -> str:
    if is_hq:
        return _pick(seed, HQ_W[region], "shhq", *keys)
    w = dict(COUNTRY_W[region])
    for tilt in (COUNTRY_TILT.get(g["industry_subsector"], {}), COUNTRY_TILT.get(g["segment"], {})):
        for cc, x in tilt.items():
            if cc in w:
                w[cc] *= x
    return _pick(seed, w, "shcc", *keys)


def _regional_name(core: str, region: str, cc: str, is_hq: bool, div: int) -> str:
    qual, form = COUNTRIES[cc][0], COUNTRIES[cc][1]
    if is_hq:
        qual = "Europe" if region == "EMEA" else "America"
    n_div = len(names.DIVISION_WORDS)
    mid = f" {names.DIVISION_WORDS[(div - 1) % n_div]}" if div else ""
    extra = f" {EXTRA_QUALIFIERS[((div - 1) // n_div - 1) % len(EXTRA_QUALIFIERS)]}" if div > n_div else ""
    return f"{core}{mid}{extra} {qual} {form}"


def build_regional_entities(cfg, groups: List[Dict], entities: List[Dict], parents: Dict,
                            used_names: set) -> List[Dict]:
    """EMEA / AMER subsidiaries (~600): the first in each region is the regional holding company."""
    seed = cfg.random_seed
    as_of = D.fromisoformat(cfg.as_of_date)
    lead = {e["group_id"]: e for e in entities if e["is_group_lead"]}
    counts = _regional_counts(cfg, groups, entities)
    out: List[Dict] = []
    for g in groups:
        gid, n = g["group_id"], counts.get(g["group_id"], 0)
        if not n:
            continue
        p_emea = EMEA_P_SUB.get(g["industry_subsector"], EMEA_P[g["segment"]])
        if g["storyline_key"] == "hayashi":
            plan = list(HAYASHI_FOOTPRINT)
        else:
            plan = [("EMEA" if rng.unit(seed, "shreg", gid, j) < p_emea else "AMER", None, None) for j in range(n)]
        hq: Dict[str, Dict] = {}
        core = _core_brand(g["group_name"])
        for j, (region, cc, city) in enumerate(plan):
            is_hq = region not in hq
            cc = cc or _country(seed, g, region, is_hq, gid, j)
            qual, form, cities, office, ccy = COUNTRIES[cc]
            city = city or cities[rng.hash64(seed, "shcity", gid, j) % len(cities)]
            div = 0
            while _regional_name(core, region, cc, is_hq, div) in used_names:
                div += 1
            legal = _regional_name(core, region, cc, is_hq, div)
            names.assert_clean(legal)
            used_names.add(legal)
            scripted = g["storyline_key"] == "hayashi"
            new = not (is_hq or scripted) and rng.unit(seed, "shnew", gid, j) < 0.15
            if new:
                start = _rand_date(seed, D(2023, 4, 1), D(2026, 6, 30), "shstart", gid, j)
            else:
                start = D(2005 + rng.randint(seed, 0, 17, "shstarty", gid, j),
                          rng.randint(seed, 1, 12, "shstartm", gid, j), 1)
            closed = None
            if not (is_hq or scripted or new) and rng.unit(seed, "shclose", gid, j) < 0.04:
                closed = _rand_date(seed, D(2024, 6, 1), D(2026, 8, 31), "shclosed", gid, j)
            if is_hq:
                par = parents.get(gid)
                parent_name, parent_type = ((par["legal_name"], "JP Parent") if par
                                            else (lead[gid]["legal_name"], "Group HQ (APAC)"))
            else:
                parent_name, parent_type = hq[region]["legal_name"], "Regional Holding"
            first = max(_at(INITIAL_LOAD, 0), _at(start, 18))
            upd = None
            if rng.unit(seed, "shentupd", gid, j) < 0.40:   # an attribute change (RM, address, ...)
                upd = first + TD(days=rng.randint(seed, 1, max(1, (as_of - first.date()).days), "shentupdd", gid, j))
            last = max([t for t in (first, upd, _at(closed) if closed else None) if t])
            rm_n = rng.hash64(seed, "shregrm", gid, region) % (EMEA_RMS if region == "EMEA" else AMER_RMS) + 1
            rec = {"__region": region, "__group": g, "__j": j, "__is_hq": is_hq, "__first": first, "__last": last,
                   "legal_name": legal, "short_name": g["group_name"], "country_code": cc, "city": city,
                   "global_group_id": gid, "immediate_parent_name": parent_name,
                   "immediate_parent_type": parent_type, "industry_sector": g["industry_sector"],
                   "regional_segment": REGIONAL_SEGMENT[region][g["segment"]],
                   "booking_office": office or US_OFFICE.get(city, city),
                   "regional_rm_code": f"{region}-RM-{rm_n:03d}", "reporting_currency": ccy,
                   "relationship_start_date": start,
                   "relationship_status": "Closed" if closed else "Active", "closed_date": closed}
            if region == "AMER":
                rec["naics_code"] = NAICS_BY_SECTOR.get(g["industry_sector"], "999999")
            hq.setdefault(region, rec)
            out.append(rec)
    for region in ("EMEA", "AMER"):
        for n, rec in enumerate((r for r in out if r["__region"] == region), start=1):
            rec["provider_entity_id"] = f"{region}-E-{n:05d}"
            known = rng.unit(seed, "shlei", region, n) < 0.9
            rec["lei_like_id"] = fragment.make_lei(rec["provider_entity_id"]) if known else None
    return out


# ---- revenue calibration --------------------------------------------------------------------
def apac_share_target(cfg, g: Dict, n_apac: int) -> float:
    """Target APAC share of the group's global revenue (Japanese groups are Tokyo-centred)."""
    key = g.get("storyline_key")
    if key in APAC_SHARE_SCRIPT:
        return APAC_SHARE_SCRIPT[key]
    u = rng.unit(cfg.random_seed, "shapac", g["group_id"])
    if g["segment"] == JC:
        return min(0.75, 0.07 + 0.45 * u ** 1.3 + 0.12 * min(1.0, n_apac / 25.0))
    if g["segment"] == FI:
        return 0.35 + 0.50 * u
    if g["segment"] == SSF:
        return 0.50 + 0.40 * u
    return 0.55 + 0.40 * u


def regional_wallet(cfg, groups: List[Dict], entities: List[Dict], parents: Dict, booked: Dict,
                    regional: List[Dict], apac_revenue: Dict[str, float]) -> Dict[str, Dict[str, float]]:
    """group_id -> {JP / EMEA / AMER: annual revenue USD}: non-APAC = APAC x (1 - s) / s, split by
    presence (a JP parent dominates; EMEA / AMER grow with their entity counts)."""
    n_apac = Counter(e["group_id"] for e in entities)
    n_reg = Counter((r["global_group_id"], r["__region"]) for r in regional)
    out: Dict[str, Dict[str, float]] = {}
    for g in groups:
        gid = g["group_id"]
        w: Dict[str, float] = {}
        if gid in parents:
            w["JP"] = 3.0 if g["segment"] == JC else 2.0
        elif gid in booked:
            w["JP"] = 0.6
        for region in ("EMEA", "AMER"):
            if n_reg[(gid, region)]:
                w[region] = 0.45 * n_reg[(gid, region)] ** 0.7
        if not w:
            continue
        if g["storyline_key"] == "hayashi":
            w = dict(HAYASHI_SPLIT)
        s = apac_share_target(cfg, g, n_apac[gid])
        non_apac = max(MIN_APAC_REVENUE, apac_revenue.get(gid, 0.0)) * (1 - s) / s
        out[gid] = {r: non_apac * v / sum(w.values()) for r, v in w.items()}
    return out


def apac_revenue_proxy(cfg, entities: List[Dict], accounts: List[Dict], facilities: List[Dict]) -> Dict[str, float]:
    """Approximate APAC annual revenue per group from the account / facility truth (deposit NII +
    lending margin and fees). The runner uses the bronze aggregates; tests use this."""
    by_id = {e["entity_id"]: e for e in entities}
    out: Dict[str, float] = defaultdict(float)
    for a in accounts:
        e = by_id[a["entity_id"]]
        spread = prof.DEPOSIT_SPREAD_CASA if a["is_casa"] else prof.DEPOSIT_SPREAD_TD
        out[e["group_id"]] += a["base_balance_usd"] * (0.4 + 0.8 * e["health_base"]) * spread
    for f in facilities:
        drawn = f["limit_usd"] * f.get("base_utilisation", 0.5)
        out[by_id[f["entity_id"]]["group_id"]] += (drawn * f["margin_bps"] + (f["limit_usd"] - drawn)
                                                   * prof.COMMITMENT_FEE_BPS) / 10000.0
    return dict(out)


# ---- monthly exposure / revenue lines --------------------------------------------------------------
def _families(cfg, key: str, kind: str, g: Dict) -> List[str]:
    seed = cfg.random_seed
    probs = dict(FAMILY_P[kind])
    if g["industry_subsector"] in TRADE_HEAVY:
        probs[TRADE] = min(0.9, probs[TRADE] * 2.0)
    if g["segment"] == SSF or g["industry_subsector"] == "Infrastructure":
        probs["Structured Lending"] = 0.5
    fams = [f for f in FAMILIES if rng.unit(seed, "shfam", key, f) < probs[f]]
    return fams or ["Cash"]


def _lines(cfg, region: str, key: str, kind: str, g: Dict, revenue: float,
           families: Optional[List[str]] = None) -> List[Dict]:
    """Product lines of one counterparty, sized so their as-of annual revenue sums to `revenue`."""
    seed = cfg.random_seed
    fams = families or _families(cfg, key, kind, g)
    tot = sum(REV_WEIGHT[f] for f in fams)
    beta = 0.4 + 0.9 * rng.unit(seed, "shbeta", key)
    mean, sd = GROWTH[kind]
    out = []
    for f in fams:
        r = revenue * REV_WEIGHT[f] / tot
        growth = (GREEN_GROWTH if f == "Sustainable Finance"
                  else min(0.30, max(-0.15, rng.normal(seed, mean, sd, "shgr", key, f))))
        line = {"key": key, "family": f, "revenue": r, "beta": beta, "growth": growth}
        u = rng.unit(seed, "shutil", key, f)
        if f in LENDING or f == TRADE:
            m = MARGIN[region][f] * (BOOKED_MARGIN_X if kind == "booked" else 1.0)
            util = 0.35 + 0.45 * u if f in LENDING else 0.40 + 0.35 * u
            fee = m * COMMIT_FEE_SHARE if f in LENDING else 0.0
            line.update(margin=m, util=util, fee=fee, committed=r / (util * m + (1 - util) * fee))
        elif f in DEPOSITS:
            line.update(spread=SPREAD[region][f], deposits=r / SPREAD[region][f])
        out.append(line)
    return out


def _season(region: str, family: str, month: int) -> float:
    if family in DEPOSITS:
        table = {3: 1.12, 9: 1.05, 6: 1.03, 12: 1.03} if region == "JP" else {12: 1.06, 3: 1.03, 6: 1.03, 9: 1.03}
        return table.get(month, 1.0)
    if family in LENDING or family == TRADE:
        return 1.0
    return 1.10 if month in (3, 6, 9, 12) else 1.0


def _values(cfg, region: str, line: Dict, m: D, as_of_m: D, cyc: float, cyc_now: float,
            ramp: float) -> Tuple[float, float, float, float]:
    """(committed, drawn, deposits, revenue) USD for one line-month, normalised to the as-of month:
    stressed sectors draw more and hold fewer deposits; deposits peak at fiscal year-ends."""
    seed, key, f = cfg.random_seed, line["key"], line["family"]
    k = m.isoformat()
    years = ((m.year - as_of_m.year) * 12 + m.month - as_of_m.month) / 12.0
    trend = (1 + line["growth"]) ** years * ramp
    u1 = rng.unit(seed, "shm1", key, f, k) - 0.5
    u2 = rng.unit(seed, "shm2", key, f, k) - 0.5
    b = line["beta"]
    if f in LENDING or f == TRADE:
        committed = line["committed"] * trend * (1 + 0.03 * u1)
        util = min(0.95, max(0.05, line["util"] - 0.10 * b * (cyc - cyc_now) + 0.04 * u2))
        drawn = committed * util
        return committed, drawn, 0.0, (drawn * line["margin"] + (committed - drawn) * line["fee"]) / 12.0
    season = _season(region, f, m.month) / _season(region, f, as_of_m.month)
    if f in DEPOSITS:
        dep = line["deposits"] * trend * season * (1 + 0.12 * b * cyc) / (1 + 0.12 * b * cyc_now) * (1 + 0.06 * u1)
        return 0.0, 0.0, dep, dep * line["spread"] / 12.0 * (1 + 0.04 * u2)
    return 0.0, 0.0, 0.0, line["revenue"] / 12.0 * trend * season * (1 + 0.10 * b * (cyc - cyc_now)) * (1 + 0.24 * u1)


def _publish_window(cfg, m: D, as_of_m: D) -> Tuple[DT, DT]:
    """(first, last) provider write times of a month row: the month's snapshot is first shared on
    its first day and re-published daily until the month-end close (as-of month: still open)."""
    first = _at(m, 0)
    return first, (_cutoff(cfg) if m == as_of_m else _at(_month_end(m) + TD(days=1), 0))


def _expand(cfg, region: str, line: Dict, g: Dict, start: D, end: Optional[D], cyc_cache: Dict,
            new: bool) -> List[Tuple[D, Tuple[float, float, float, float]]]:
    """Monthly values of a line from its start month to the as-of month (or the month before closure)."""
    seed = cfg.random_seed
    as_of_m = D.fromisoformat(cfg.as_of_date).replace(day=1)
    sub = g["industry_subsector"]

    def cyc(m):
        if (sub, m) not in cyc_cache:
            cyc_cache[(sub, m)] = health.sector_cycle(sub, m, seed)
        return cyc_cache[(sub, m)]

    first_m = max(D.fromisoformat(cfg.history_start).replace(day=1), start.replace(day=1))
    out = []
    for i, m in enumerate(_month_starts(first_m, as_of_m)):
        if end is not None and m >= end.replace(day=1):
            break
        ramp = min(1.0, (i + 1) / 6.0) if new else 1.0
        out.append((m, _values(cfg, region, line, m, as_of_m, cyc(m), cyc(as_of_m), ramp)))
    return out


def build_monthly(cfg, groups: List[Dict], parents: Dict, booked: Dict, regional: List[Dict],
                  wallet: Dict[str, Dict[str, float]], fx: Dict) -> Dict[str, List[Dict]]:
    """share_jp_parent_exposure_monthly + share_<emea|amer>_exposure_monthly / _revenue_monthly."""
    seed = cfg.random_seed
    as_of_m = D.fromisoformat(cfg.as_of_date).replace(day=1)
    hist_m = D.fromisoformat(cfg.history_start).replace(day=1)
    gby = {g["group_id"]: g for g in groups}
    cyc_cache: Dict = {}
    out = {t: [] for _, t in TABLES if t.endswith("_monthly")}

    def money(row, usd: Dict[str, float], ccy: str, me: D):
        rate = _rate(cfg, fx, ccy, me)
        for col, v in usd.items():
            row[f"{col}_lcy"], row[f"{col}_usd"] = round(v * rate, 2), round(v, 2)

    for gid in sorted(set(parents) | set(booked)):   # Tokyo-booked
        g, r_jp = gby[gid], wallet.get(gid, {}).get("JP", 0.0)
        if r_jp <= 0:
            continue
        if gid in parents:
            p = parents[gid]
            cid, name, ctype, ccy, office = p["jp_parent_id"], p["legal_name"], "JP Parent", "JPY", p["city"]
            lines = _lines(cfg, "JP", cid, "parent", g, r_jp)
        else:
            b = booked[gid]
            cid, name, ccy, office = b["jp_counterparty_id"], b["name"], "USD", "Tokyo"
            ctype = "Group Entity (JP-booked)"
            lines = _lines(cfg, "JP", cid, "booked", g, r_jp, b["families"])
        for line in lines:
            for m, (c, dr, dep, rev) in _expand(cfg, "JP", line, g, hist_m, None, cyc_cache, False):
                me = _month_end(m)
                first, last = _publish_window(cfg, m, as_of_m)
                row = {"jp_counterparty_id": cid, "jp_counterparty_name": name, "counterparty_type": ctype,
                       "global_group_id": gid, "month_end_date": me, "product_family": line["family"],
                       "booking_office": office, "currency": ccy, "__first": first, "__last": last}
                money(row, {"committed": c, "drawn": dr, "deposits": dep, "revenue": rev}, ccy, me)
                out["share_jp_parent_exposure_monthly"].append(row)

    by_group_region: Dict[Tuple[str, str], List[Dict]] = defaultdict(list)
    for r in regional:
        by_group_region[(r["global_group_id"], r["__region"])].append(r)
    for (gid, region), ents in sorted(by_group_region.items()):
        g, r_reg = gby[gid], wallet.get(gid, {}).get(region, 0.0)
        w = [rng.lognormal(seed, 0.0, 0.8, "shentw", e["provider_entity_id"]) * (2.0 if e["__is_hq"] else 1.0)
             for e in ents]
        low = region.lower()
        for e, we in zip(ents, w):
            new = e["relationship_start_date"] >= hist_m
            for line in _lines(cfg, region, e["provider_entity_id"], "sub", g, r_reg * we / sum(w)):
                for m, (c, dr, dep, rev) in _expand(cfg, region, line, g, e["relationship_start_date"],
                                                    e["closed_date"], cyc_cache, new):
                    me, f = _month_end(m), line["family"]
                    first, last = _publish_window(cfg, m, as_of_m)
                    first = max(first, e["__first"])
                    key = {"provider_entity_id": e["provider_entity_id"], "global_group_id": gid,
                           "month_end_date": me, "product_family": f}
                    rev_row = {**key, "revenue_type": REVENUE_TYPE[f], "currency": e["reporting_currency"],
                               "__first": first, "__last": last}
                    money(rev_row, {"revenue": rev}, e["reporting_currency"], me)
                    out[f"share_{low}_revenue_monthly"].append(rev_row)
                    if f not in BALANCE_FAMILIES:
                        continue
                    exp_row = {**key, "booking_office": e["booking_office"], "currency": e["reporting_currency"],
                               "__first": first, "__last": last}
                    money(exp_row, {"committed": c, "drawn": dr, "deposits": dep}, e["reporting_currency"], me)
                    if region == "EMEA":   # IFRS 9 stage on credit exposures (added by the provider in 2025)
                        stressed = cyc_cache[(g["industry_subsector"], m)] < -0.45
                        u = rng.unit(seed, "shstage", e["provider_entity_id"], f, me.isoformat())
                        exp_row["ifrs9_stage"] = (2 if stressed and u < 0.4 else 1) if f not in DEPOSITS else None
                    out[f"share_{low}_exposure_monthly"].append(exp_row)
    return out


# ---- group master -----------------------------------------------------------------------------
def _tokyo_rm(cfg, dept: int, gid: str) -> Tuple[str, str]:
    """(RM code, name) of the Tokyo HO global RM covering a group in a coverage department."""
    i = (dept - 1) * TOKYO_RMS_PER_DEPT + rng.hash64(cfg.random_seed, "shtkrm", gid) % TOKYO_RMS_PER_DEPT
    name = f"{JP_GIVEN[i % len(JP_GIVEN)]} {JP_FAMILY[(i * 7 + i // len(JP_GIVEN)) % len(JP_FAMILY)]}"
    names.assert_clean(name)
    return f"JPHO-RM-{i + 1:03d}", name


def build_group_master(cfg, groups: List[Dict], entities: List[Dict], xref: List[Dict], parents: Dict,
                       regional: List[Dict], wallet: Dict[str, Dict[str, float]]) -> List[Dict]:
    """share_jp_group_master: every group as mastered by Tokyo HO (global tier ranks the JP-owned
    groups by their non-APAC wallet; APAC-owned groups mirror the APAC tier)."""
    seed = cfg.random_seed
    as_of = D.fromisoformat(cfg.as_of_date)
    crm_ids: Dict[str, str] = {}
    for x in xref:
        if x["source_system"] == "crm_account" and not x["is_within_source_dup"]:
            crm_ids.setdefault(x["entity_id"], x["source_id"])
    by_group: Dict[str, List[Dict]] = defaultdict(list)
    for e in sorted(entities, key=lambda e: (not e["is_group_lead"], e["entity_id"])):
        by_group[e["group_id"]].append(e)
    active = Counter((r["global_group_id"], r["__region"]) for r in regional if r["relationship_status"] == "Active")
    jp_owned = sorted((g for g in groups if g["global_relationship_owner_region"] == "JP"),
                      key=lambda g: (-sum(wallet.get(g["group_id"], {}).values()), g["group_id"]))
    tier = {}
    for i, g in enumerate(jp_owned):
        q = i / len(jp_owned)
        t = "Global Strategic" if q < 0.25 else ("Global Core" if q < 0.70 else "Global Standard")
        tier[g["group_id"]] = "Global Core" if t == "Global Standard" and g["relationship_tier"] == "Strategic" else t
    rows = []
    for g in groups:
        gid, p = g["group_id"], parents.get(g["group_id"])
        owner = g["global_relationship_owner_region"]
        if owner == "JP":
            dept = 5 if g["segment"] != JC else COVERAGE_DEPT.get(g["industry_sector"], 5)
            rm, rm_name = _tokyo_rm(cfg, dept, gid)
            unit = f"Tokyo HO Global Corporate Banking Dept {dept} ({DEPT_NAME[dept]})"
        else:   # the APAC lead RM, as Tokyo was told (CRM owner of the first group entity in CRM)
            crm_id = next((crm_ids[e["entity_id"]] for e in by_group[gid] if e["entity_id"] in crm_ids), None)
            rm, rm_name = (crm.rm_code(seed, crm_id) if crm_id else None), None
            unit = "APAC Coverage (Singapore)" if g["segment"] != FI else "APAC Financial Institutions (Singapore)"
        updates = [_at(INITIAL_LOAD, 0)]
        if rng.unit(seed, "shgmrm", gid) < 0.30:   # global RM reassigned
            span = (as_of - D(2024, 5, 1)).days
            updates.append(_at(D(2024, 5, 1) + TD(days=rng.randint(seed, 0, span, "shgmrmd", gid)), 11))
        if owner == "JP" and rng.unit(seed, "shgmtier", gid) < 0.15:   # FY2026 re-tiering batch
            updates.append(_at(D(2026, 4, 1), 1))
        since = p["since"] if p else min(e.get("_since", D(2001, 1, 1)) for e in by_group[gid])
        rows.append({
            "global_group_id": gid, "group_name_global": g["group_name"],
            "jp_parent_id": p["jp_parent_id"] if p else None, "jp_parent_legal_name": p["legal_name"] if p else None,
            "jp_parent_lei": p["lei"] if p else None, "jp_parent_city": p["city"] if p else None,
            "hq_country": g["hq_country"], "hq_region": g["hq_region"],
            "global_segment": GLOBAL_SEGMENT[g["segment"]], "global_industry": g["group_industry"],
            "global_tier": tier.get(gid, GLOBAL_TIER[g["relationship_tier"]]),
            "global_relationship_owner_region": owner, "global_rm_code": rm, "global_rm_name": rm_name,
            "global_coverage_unit": unit, "relationship_since": since,
            "n_subsidiaries_emea": active[(gid, "EMEA")], "n_subsidiaries_amer": active[(gid, "AMER")],
            "master_status": "Active", "__first": updates[0], "__last": max(updates)})
    return rows


# ---- JP parent ratings and financials --------------------------------------------------------
def _review_date(cfg, gid: str, year: int) -> D:
    """Annual review after the March FY results: a fixed slot per parent, 4 Jun - 20 Sep."""
    seed = cfg.random_seed
    return (D(year, 6, 8) + TD(days=rng.randint(seed, 0, 100, "shrevoff", gid)
                               + rng.randint(seed, -4, 4, "shrevj", gid, year)))


def _grade_path(cfg, g: Dict, p: Dict, dates: Dict[int, D]) -> Dict[int, int]:
    """Grade at each annual review, ending at the truth group grade: earlier grades differ by the
    sector cycle since then (shipping / coal parents were stronger, semiconductors weaker) plus rare
    idiosyncratic notches."""
    seed, gid, sub = cfg.random_seed, g["group_id"], g["industry_subsector"]

    def cyc(d: D) -> float:   # six-month average of the sector cycle before the review
        return sum(health.sector_cycle(sub, _months_back(d.replace(day=1), k), seed) for k in range(6)) / 6.0

    last = REVIEW_YEARS[-1]
    anchor, c_last = g["group_internal_grade"], cyc(dates[last])
    grades, idio, nxt = {last: anchor}, 0, anchor
    for y in reversed(REVIEW_YEARS[:-1]):
        u = rng.unit(seed, "shidio", gid, y)
        idio += -1 if u < IDIO_P else (1 if u > 1 - IDIO_P else 0)
        gr = min(10, max(1, anchor + round(CYCLE_NOTCH * p["beta"] * (c_last - cyc(dates[y]))) + idio))
        grades[y] = nxt = min(nxt + 2, max(nxt - 2, gr))
    return grades


def build_parent_rating(cfg, groups: List[Dict], parents: Dict) -> Tuple[List[Dict], Dict[str, Dict[int, int]]]:
    """share_jp_parent_rating (one row per rating action) + each parent's grade by review year."""
    seed = cfg.random_seed
    ts = S.TANAKA_SCRIPT
    rows, paths = [], {}
    for g in groups:
        gid, p = g["group_id"], parents.get(g["group_id"])
        if not p:
            continue
        scripted = g["storyline_key"] == "tanaka" and S.on(cfg, S.TANAKA)
        dates = {y: _review_date(cfg, gid, y) for y in REVIEW_YEARS + [2027]}
        if scripted:
            dates[2026] = ts["parent_upgrade_date"]
            grades = {**{y: ts["parent_grade_from"] for y in REVIEW_YEARS}, 2026: ts["parent_grade_to"]}
        else:
            grades = _grade_path(cfg, g, p, dates)
        paths[gid] = grades
        acts, prev = [], None   # (date, action, from, to, review type, next review)
        for y in REVIEW_YEARS:
            gr = grades[y]
            if prev is None:
                acts.append((dates[y], "Initial", None, gr, "Annual Review", dates[y + 1]))
            elif gr > prev and y >= 2025 and not scripted and rng.unit(seed, "shevt", gid, y) < EVENT_DRIVEN_P:
                evt = D(y, 1, 12) + TD(days=rng.randint(seed, 0, 60, "shevtd", gid, y))
                acts.append((evt, "Downgrade", prev, gr, "Event-Driven", dates[y]))
                acts.append((dates[y], "Affirm", gr, gr, "Annual Review", dates[y + 1]))
            else:
                act = "Affirm" if gr == prev else ("Upgrade" if gr < prev else "Downgrade")
                acts.append((dates[y], act, prev, gr, "Annual Review", dates[y + 1]))
            prev = gr
        trend = health.sector_cycle(g["industry_subsector"], D(2026, 9, 1), seed)
        for i, (d, act, gf, gt, rtype, nxt_review) in enumerate(acts):
            following = acts[i + 1] if i + 1 < len(acts) else None   # the outlook signals the next move
            if following and following[3] != following[2]:
                outlook = "Positive" if following[3] < following[2] else "Negative"
            elif following:
                outlook = "Stable"
            else:
                u = rng.unit(seed, "shoutlook", gid)
                outlook = ("Negative" if trend < -0.4 and u < 0.35 else "Positive" if trend > 0.4 and u < 0.30
                           else "Stable")
            if scripted:
                outlook = "Positive" if d.year == 2025 else "Stable"
            if scripted and act == "Upgrade":
                reason = RATING_REASON["tanaka"]
            else:
                tmpl = RATING_REASON["Event-Driven" if rtype == "Event-Driven" else act]
                reason = tmpl.format(fy=d.year - 1, outlook=outlook.lower(), sector=g["industry_subsector"].lower())
            nxt = acts[i + 1][0] if i + 1 < len(acts) else None
            rows.append({"jp_parent_id": p["jp_parent_id"], "global_group_id": gid, "rating_date": d,
                         "rating_action": act, "review_type": rtype, "grade_from": gf, "grade_to": gt,
                         "rating_equivalent": RATING_SCALE[gt - 1], "outlook": outlook, "rating_reason": reason,
                         "next_review_date": nxt_review, "valid_to": nxt, "is_current": nxt is None,
                         "__first": _at(d, 17), "__last": _at(nxt, 17) if nxt else _at(d, 17)})
    rows.sort(key=lambda r: (r["rating_date"], r["jp_parent_id"]))
    for n, r in enumerate(rows, start=1):
        r["rating_id"] = f"JPR-{n:05d}"
    return rows, paths


def build_parent_financials(cfg, groups: List[Dict], entities: List[Dict], parents: Dict,
                            paths: Dict[str, Dict[int, int]], regional: List[Dict], fx: Dict) -> List[Dict]:
    """share_jp_parent_financials: consolidated JPY financials per JP parent x FY (March FYE). The
    parent is several times its APAC subsidiaries' combined sales; margins, leverage and coverage
    follow the grade set at the review after each FY's results."""
    seed = cfg.random_seed
    by_group: Dict[str, List[Dict]] = defaultdict(list)
    for e in entities:
        by_group[e["group_id"]].append(e)
    n_reg = Counter(r["global_group_id"] for r in regional)
    rows = []
    for g in groups:
        gid, p = g["group_id"], parents.get(g["group_id"])
        if not p:
            continue
        apac_sales = sum(rng.lognormal(seed, 17.6, 0.6, "finsize", e["entity_id"]) * e["group_wealth"]
                         for e in by_group[gid])   # same size draw as the APAC spreads (financials.py)
        base = max(PARENT_REVENUE_FLOOR_USD, apac_sales / (0.12 + 0.28 * rng.unit(seed, "shsales", gid)))
        growth = rng.normal(seed, 0.03, 0.03, "shfgrow", gid)
        ifrs = rng.unit(seed, "shifrs", gid) < 0.35
        n_subs = len(by_group[gid]) + n_reg[gid] + rng.randint(seed, 15, 120, "shnsubs", gid)
        for fy in FIN_YEARS:
            grade = paths[gid][fy + 1]
            h = 1.0 - (grade - 1) / 9.0
            fye = D(fy + 1, 3, 31)
            u = [rng.unit(seed, "shfin", gid, fy, k) - 0.5 for k in range(4)]
            cyc = health.sector_cycle(g["industry_subsector"], D(fy + 1, 3, 1), seed)
            revenue = base * (1 + growth) ** (fy - 2025) * (1 + 0.12 * p["beta"] * cyc) * (1 + 0.04 * u[0])
            ebitda = revenue * max(0.03, EBITDA_BASE.get(g["industry_sector"], 0.12) + 0.08 * (h - 0.5) + 0.02 * u[1])
            op_income = ebitda - revenue * 0.035
            net_debt = ebitda * max(0.1, 0.3 + 0.35 * (grade - 1) + 0.3 * u[2])
            cash = revenue * (0.08 + 0.10 * h)
            debt = net_debt + cash
            interest = debt * (0.008 + 0.006 * (grade - 1) / 9.0)
            net_income = (op_income - interest) * 0.70
            assets = max(revenue * (1.0 + 0.4 * rng.unit(seed, "shturn", gid)), debt / 0.55)
            equity = assets * (0.28 + 0.27 * h + 0.04 * u[3])
            published = D(fy + 1, 5, 8) + TD(days=rng.randint(seed, 0, 12, "shfinpub", gid, fy))
            shared = published + TD(days=rng.randint(seed, 10, 45, "shfinshr", gid, fy))
            usd = {"revenue": revenue, "operating_income": op_income, "ebitda": ebitda, "net_income": net_income,
                   "interest_expense": interest, "total_assets": assets, "total_debt": debt, "cash": cash,
                   "net_debt": net_debt, "equity": equity}
            rate = _rate(cfg, fx, "JPY", fye)
            row = {"jp_parent_id": p["jp_parent_id"], "global_group_id": gid, "fiscal_year": fy,
                   "fiscal_year_label": f"FY{fy}", "fiscal_year_end": fye,
                   "accounting_standard": "IFRS" if ifrs else "J-GAAP", "currency": "JPY"}
            row.update({f"{k}_jpy": round(v * rate, 0) for k, v in usd.items()})
            row.update({f"{k}_usd": round(v, 2) for k, v in usd.items()})
            row.update({"net_debt_to_ebitda": round(net_debt / ebitda, 2),
                        "interest_coverage": round(op_income / interest, 2),
                        "equity_ratio": round(equity / assets, 4), "roe": round(net_income / equity, 4),
                        "operating_margin": round(op_income / revenue, 4), "n_consolidated_subsidiaries": n_subs,
                        "audit_opinion": "Unqualified", "results_published_date": published,
                        "ghg_scope12_kt": round(revenue / 1e6 * GHG_INTENSITY.get(g["industry_subsector"], 25)
                                                * (1 + 0.2 * u[0]) / 1000.0, 1),
                        "__first": _at(shared), "__last": _at(shared)})
            rows.append(row)
    return rows


# ---- support letters ----------------------------------------------------------------------------
def build_support_letters(cfg, entities: List[Dict], parents: Dict, facilities: List[Dict],
                          obligor_of: Dict[str, str]) -> List[Dict]:
    """share_jp_support_letters: a facility-specific guarantee for every 'Parent Guarantee' facility
    and a general keepwell per subsidiary with 'Keepwell' facilities (JP-parented groups, as in
    bronze.credit_facility_terms). Storyline 4: Tanaka's Singapore lead always has an active keepwell."""
    seed = cfg.random_seed
    as_of = D.fromisoformat(cfg.as_of_date)
    by_id = {e["entity_id"]: e for e in entities}
    rows: List[Dict] = []
    keep: Dict[Tuple[str, str], List[Dict]] = defaultdict(list)

    def subsidiary(e: Dict, obligor: str, key) -> Dict:
        return {"jp_parent_id": parents[e["group_id"]]["jp_parent_id"], "global_group_id": e["group_id"],
                "apac_obligor_ref": obligor, "subsidiary_name": _tokyo_spelling(e["legal_name"]),
                "subsidiary_country": e["booking_country"],
                "subsidiary_lei": fragment.make_lei(e["entity_id"]) if rng.unit(seed, "shsllei", key) < 0.9 else None,
                "beneficiary_booking_entity": e["booking_country"],
                "parent_ownership_pct": 100.0 if rng.unit(seed, "shown", e["entity_id"]) < 0.8
                else float(rng.randint(seed, 51, 95, "shownp", e["entity_id"]))}

    for f in sorted(facilities, key=lambda f: f["facility_id"]):
        e = by_id.get(f.get("entity_id"))
        if e is None or e["group_id"] not in parents:
            continue
        if f["guarantor_type"] == "Keepwell":
            keep[(e["entity_id"], f["obligor_id"])].append(f)
        elif f["guarantor_type"] == "Parent Guarantee":
            fid = f["facility_id"]
            issue = f["origination_date"] - TD(days=rng.randint(seed, 0, 20, "shgiss", fid))
            expiry = f["maturity_date"] + TD(days=30)
            closed = f.get("closed_date")
            status, status_date = (("Released", closed) if closed and closed <= as_of
                                   else ("Expired", expiry) if expiry < as_of else ("Active", None))
            rows.append({**subsidiary(e, f["obligor_id"], fid), "support_type": "Parent Guarantee",
                         "is_legally_binding": True, "coverage_scope": "Facility", "covered_facility_id": fid,
                         "support_amount_usd": round(f["limit_usd"] * (1.0 + 0.05 * rng.unit(seed, "shgamt", fid)), 2),
                         "currency": f["currency"], "issue_date": issue, "expiry_date": expiry,
                         "last_confirmed_date": None, "status": status, "status_date": status_date,
                         "__first": _at(issue), "__last": _at(status_date) if status_date else _at(issue)})
    tanaka = S.lead_entity(entities, "tanaka") if S.on(cfg, S.TANAKA) else None
    if tanaka and tanaka["group_id"] in parents and tanaka["entity_id"] in obligor_of:
        keep.setdefault((tanaka["entity_id"], obligor_of[tanaka["entity_id"]]), [])
    for (eid, obligor), facs in sorted(keep.items()):
        e, scripted = by_id[eid], tanaka is not None and eid == tanaka["entity_id"]
        live = [f for f in facs if f["maturity_date"] >= as_of
                and not (f.get("closed_date") and f["closed_date"] <= as_of)]
        if scripted:
            issue, expiry = TANAKA_KEEPWELL_ISSUED, None
        else:
            issue = min(f["origination_date"] for f in facs) - TD(days=rng.randint(seed, 5, 60, "shkiss", obligor))
            expiry = None if live else max(f["maturity_date"] for f in facs) + TD(days=30)
        status, status_date = ("Expired", expiry) if expiry and expiry < as_of else ("Active", None)
        horizon = min(as_of, expiry) if expiry else as_of
        confirmed = next((D(y, issue.month, min(issue.day, 28)) for y in range(horizon.year, issue.year, -1)
                          if D(y, issue.month, min(issue.day, 28)) <= horizon), None)   # annual reconfirmation
        sub = subsidiary(e, obligor, obligor)
        if scripted:   # wholly owned; reaffirmed a week after the parent upgrade
            confirmed, sub["parent_ownership_pct"] = S.TANAKA_SCRIPT["parent_upgrade_date"] + TD(days=7), 100.0
        rows.append({**sub, "support_type": "Keepwell", "is_legally_binding": False,
                     "coverage_scope": "General", "covered_facility_id": None, "support_amount_usd": None,
                     "currency": None, "issue_date": issue, "expiry_date": expiry, "last_confirmed_date": confirmed,
                     "status": status, "status_date": status_date, "__first": _at(issue),
                     "__last": max(_at(d) for d in (issue, confirmed, status_date) if d)})
    rows.sort(key=lambda r: (r["issue_date"], r["apac_obligor_ref"], r["covered_facility_id"] or ""))
    for n, r in enumerate(rows, start=1):
        r["letter_id"] = f"JPSL-{n:05d}"
    return rows


# ---- provider refreshes, row versions and the freshness log --------------------------------------
def _outage(cfg, table: str) -> set:
    """Storyline 13: the JP parent-rating table is not refreshed on the scripted days."""
    if table != S.SHARE_STALE["table"] or not S.on(cfg, S.SHARE_STALENESS):
        return set()
    a, b = S.SHARE_STALE["gap"]
    return {a + TD(days=i) for i in range((b - a).days + 1)}


def _incidents(cfg) -> Dict[str, set]:
    """A few isolated failed EMEA / AMER refreshes (one stale morning each), away from drift days."""
    seed = cfg.random_seed
    tables = [t for r, t in TABLES if r != "JP"]
    span = (INCIDENT_WINDOW[1] - INCIDENT_WINDOW[0]).days
    blocked = {d + TD(days=k) for d, *_ in SCHEMA_DRIFT.values() for k in (-1, 0, 1)}
    out: Dict[str, set] = defaultdict(set)
    i = 0
    while sum(len(v) for v in out.values()) < INCIDENTS:
        t = tables[rng.hash64(seed, "shinct", i) % len(tables)]
        d = INCIDENT_WINDOW[0] + TD(days=rng.randint(seed, 0, span, "shincd", i))
        if d not in blocked and not any(d in v for v in out.values()):
            out[t].add(d)
        i += 1
    return dict(out)


def refresh_runs(cfg, table: str) -> List[Dict]:
    """The provider's daily refresh runs for one table from the initial load to as-of: scheduled
    time, status (Success / Late / Failed), publish time and the Delta version it created."""
    seed = cfg.random_seed
    region = REGION_OF[table]
    as_of = D.fromisoformat(cfg.as_of_date)
    hh, mm = REFRESH_AT[region]
    gap, bad, drift = _outage(cfg, table), _incidents(cfg).get(table, set()), SCHEMA_DRIFT.get(table)
    runs, version, d = [], 0, INITIAL_LOAD
    while d <= as_of:
        k = d.isoformat()
        jitter = round((2 * rng.unit(seed, "shrun", table, k) - 1) * JITTER_MIN)
        sched = DT(d.year, d.month, d.day, hh, mm) + TD(minutes=jitter)
        run = {"run_date": d, "scheduled_at": sched, "status": "Failed", "published_at": None, "version": None,
               "message": OUTAGE_MESSAGE if d in gap else INCIDENT_MESSAGE}
        if d not in gap and d not in bad:
            late = d > INITIAL_LOAD and rng.unit(seed, "shlate", table, k) < LATE_P
            version += 2 if drift and d == drift[0] else 1   # a schema change is its own commit
            delay = rng.randint(seed, LATE_MINUTES[0], LATE_MINUTES[1], "shlated", table, k) if late else 0
            run.update(status="Late" if late else "Success", published_at=sched + TD(minutes=delay),
                       version=version, message=None)
        runs.append(run)
        d += TD(days=1)
    return runs


def stamp_rows(cfg, table: str, rows: List[Dict], runs: List[Dict]) -> Tuple[List[Dict], List[int]]:
    """Attach the D27 sharing columns. A row carries the version / time of the first refresh after
    its last change (its first refresh for the row count); rows first shared after the as-of cut-off
    are dropped; a drift column is NULL on rows last written before the schema change."""
    region, cutoff = REGION_OF[table], _cutoff(cfg)
    pubs = [r for r in runs if r["published_at"] and r["published_at"] <= cutoff]
    times = [r["published_at"] for r in pubs]
    drift = SCHEMA_DRIFT.get(table)
    drift_v = next((r["version"] for r in pubs if drift and r["run_date"] == drift[0]), None)
    out, firsts = [], []
    for row in rows:
        i = bisect.bisect_left(times, row["__first"])
        if i == len(times):
            continue
        run = pubs[min(len(pubs) - 1, max(i, bisect.bisect_left(times, row["__last"])))]
        clean = {k: v for k, v in row.items() if not k.startswith("__")}
        if drift and (drift_v is None or run["version"] < drift_v):
            clean[drift[1]] = None
        clean.update(source_region=region, ingest_method="delta_sharing", _share_name=SHARE_NAME[region],
                     _provider_version=run["version"], _shared_at=_ts(run["published_at"]))
        out.append(clean)
        firsts.append(pubs[i]["version"])
    return out, sorted(firsts)


def build_refresh_log(cfg, runs: Dict[str, List[Dict]], firsts: Dict[str, List[int]]) -> List[Dict]:
    """bronze.dq_share_refresh_log: one row per provider table per day, as seen by the 09:00 SGT
    check: latest version and publish time, lag hours, latest scheduled run status, row count."""
    as_of = D.fromisoformat(cfg.as_of_date)
    out = []
    for region, table in TABLES:
        rr = runs[table]
        pubs = [r for r in rr if r["published_at"]]
        times, scheds = [r["published_at"] for r in pubs], [r["scheduled_at"] for r in rr]
        drift = SCHEMA_DRIFT.get(table)
        drift_v = next((r["version"] for r in pubs if drift and r["run_date"] == drift[0]), None)
        d = LOG_START
        while d <= as_of:
            check = _at(d, CHECK_HOUR)
            last = pubs[bisect.bisect_right(times, check) - 1]
            run = rr[bisect.bisect_right(scheds, check) - 1]
            lag = round((check - last["published_at"]).total_seconds() / 3600.0, 2)
            drifted = bool(drift) and last["run_date"] == drift[0]
            out.append({"log_date": d.isoformat(), "checked_at": _ts(check), "provider_region": region,
                        "share_name": SHARE_NAME[region], "table_name": table,
                        "scheduled_refresh_at": _ts(run["scheduled_at"]), "refresh_status": run["status"],
                        "refreshed_at": _ts(last["published_at"]), "provider_version": last["version"],
                        "lag_hours": lag, "is_stale": lag > STALE_HOURS, "sla_hours": STALE_HOURS,
                        "row_count": bisect.bisect_right(firsts[table], last["version"]),
                        "schema_version": 1 + int(drift_v is not None and last["version"] >= drift_v),
                        "schema_drift_flag": drifted, "schema_change": drift[2] if drifted else None,
                        "error_message": run["message"] if run["status"] == "Failed" else None})
            d += TD(days=1)
    return out


# ---- assembly ------------------------------------------------------------------------------------
def build_shared(cfg, groups: List[Dict], entities: List[Dict], xref: List[Dict], facilities: List[Dict],
                 apac_revenue: Dict[str, float],
                 customer_since: Optional[Dict[str, D]] = None) -> Dict[str, List[Dict]]:
    """Every shared table (stamped with the D27 columns) + bronze.dq_share_refresh_log.

    `facilities` are credit facility terms with entity_id (bronze.credit_facility_terms + the
    obligor -> entity map); `apac_revenue` is each group's APAC trailing-12M revenue (USD)."""
    used = {e["legal_name"] for e in entities}
    ents = [{**e, "_since": (customer_since or {}).get(e["entity_id"], D(2001, 1, 1))} for e in entities]
    obligor_of: Dict[str, str] = {}
    for x in xref:
        if x["source_system"] == "credit_obligor" and not x["is_within_source_dup"]:
            obligor_of.setdefault(x["entity_id"], x["source_id"])
    fx = fx_rates(cfg)
    parents = build_jp_parents(cfg, groups, used)
    booked = jp_booked_counterparties(cfg, groups, entities, parents)
    regional = build_regional_entities(cfg, groups, entities, parents, used)
    wallet = regional_wallet(cfg, groups, entities, parents, booked, regional, apac_revenue)
    ratings, paths = build_parent_rating(cfg, groups, parents)
    tables = {"share_jp_group_master": build_group_master(cfg, groups, ents, xref, parents, regional, wallet),
              "share_jp_parent_financials": build_parent_financials(cfg, groups, entities, parents, paths,
                                                                    regional, fx),
              "share_jp_parent_rating": ratings,
              "share_jp_support_letters": build_support_letters(cfg, entities, parents, facilities, obligor_of),
              "share_emea_entity_master": [r for r in regional if r["__region"] == "EMEA"],
              "share_amer_entity_master": [r for r in regional if r["__region"] == "AMER"]}
    tables.update(build_monthly(cfg, groups, parents, booked, regional, wallet, fx))
    runs = {t: refresh_runs(cfg, t) for _, t in TABLES}
    out, firsts = {}, {}
    for _, t in TABLES:
        out[t], firsts[t] = stamp_rows(cfg, t, tables[t], runs[t])
    out["dq_share_refresh_log"] = build_refresh_log(cfg, runs, firsts)
    return out


# ---- measures (tests and the run's verification print) ---------------------------------------------
def stale_days(log: List[Dict], table: str) -> List[str]:
    """Days on which a table's lag at the morning check exceeded the 24h SLA."""
    return sorted(r["log_date"] for r in log if r["table_name"] == table and r["is_stale"])


def apac_share_by_group(apac_revenue: Dict[str, float], tables: Dict[str, List[Dict]],
                        as_of_m: D) -> Dict[str, float]:
    """APAC share of trailing-12M global revenue per group with any shared revenue."""
    lo = _months_back(as_of_m, 11)
    other: Dict[str, float] = defaultdict(float)
    for t in ("share_jp_parent_exposure_monthly", "share_emea_revenue_monthly", "share_amer_revenue_monthly"):
        for r in tables[t]:
            if lo <= r["month_end_date"].replace(day=1) <= as_of_m:
                other[r["global_group_id"]] += r["revenue_usd"]
    return {gid: apac_revenue.get(gid, 0.0) / (apac_revenue.get(gid, 0.0) + v)
            for gid, v in other.items() if v > 0}
