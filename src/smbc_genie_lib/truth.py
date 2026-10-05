"""The *true* universe (brief §6.1 "entity-resolution truth table", DECISIONS D22).

Pure-Python, deterministic generation of the real groups, legal entities, staff and products
*before* any source-system fragmentation or noise. Everything downstream (bronze source records,
facts, storylines) derives from this. Dimensions are fixed regardless of SCALE (D09), so this is
always generated in full, including the six storyline groups which are seeded first with their
reserved brands/segments/locations.

Returns lists of dicts; the entry script turns them into Spark DataFrames and writes them to
`ops.synthetic_truth_*`. Kept side-effect-free so it is fully unit-testable.
"""
from __future__ import annotations

from typing import Dict, List

from . import names, rng

# Industry word -> (sector, subsector, carbon_intensive)
INDUSTRY_MAP: Dict[str, tuple] = {
    "Precision": ("Industrials", "Precision Components", False),
    "Chemical": ("Materials", "Chemicals", True),
    "Electronics": ("Technology", "Electronics", False),
    "Semiconductor": ("Technology", "Semiconductors", False),
    "Devices": ("Technology", "Electronic Devices", False),
    "Heavy Industries": ("Industrials", "Heavy Industry", True),
    "Machinery": ("Industrials", "Machinery", False),
    "Instruments": ("Industrials", "Instruments", False),
    "Trading": ("Trading Houses", "General Trading", False),
    "Marine Logistics": ("Transport & Logistics", "Shipping", True),
    "Shipping": ("Transport & Logistics", "Shipping", True),
    "Logistics": ("Transport & Logistics", "Logistics", False),
    "Motor": ("Automotive", "Vehicles", False),
    "Auto Parts": ("Automotive", "Auto Parts", False),
    "Steel": ("Materials", "Steel", True),
    "Materials": ("Materials", "Advanced Materials", False),
    "Fibre": ("Materials", "Fibre", False),
    "Foods": ("Consumer", "Food & Beverage", False),
    "Beverages": ("Consumer", "Food & Beverage", False),
    "Consumer": ("Consumer", "Consumer Goods", False),
    "Energy": ("Energy", "Oil, Gas & Coal", True),
    "Petrochem": ("Energy", "Petrochemicals", True),
    "Power": ("Utilities", "Power Generation", True),
    "Agri": ("Agriculture", "Agribusiness", False),
    "Palm": ("Agriculture", "Palm Oil", True),
    "Mining": ("Materials", "Mining", True),
    "Minerals": ("Materials", "Minerals", True),
    "Resources": ("Materials", "Natural Resources", True),
    "Telecom": ("Telecom", "Telecommunications", False),
    "Infrastructure": ("Infrastructure", "Infrastructure", False),
    "Property": ("Real Estate", "Property", False),
    "Construction": ("Real Estate", "Construction", False),
    "Capital": ("Financial Institution", "Investment Bank", False),
    "Securities": ("Financial Institution", "Securities", False),
    "Financial": ("Financial Institution", "Diversified Financials", False),
    "Asset Management": ("Financial Institution", "Asset Management", False),
    "Insurance": ("Financial Institution", "Insurance", False),
    "Credit": ("Financial Institution", "Consumer Finance", False),
    "Leasing": ("Financial Institution", "Leasing", False),
    "Partners": ("Sponsor", "Private Capital", False),
    "Capital Partners": ("Sponsor", "Private Equity", False),
    "Infrastructure Fund": ("Sponsor", "Infrastructure Fund", False),
    "Renewables": ("Sponsor", "Renewables", False),
    "Investments": ("Sponsor", "Investments", False),
    "Ventures": ("Sponsor", "Venture Capital", False),
    "Authority": ("Public Sector", "Statutory Board", False),
    "Development Board": ("Public Sector", "Development Agency", False),
    "Investment Corporation": ("Public Sector", "Sovereign Investment", False),
    "Power Corporation": ("Utilities", "State Power", True),
    "Rail": ("Public Sector", "Rail", False),
}

RATING_GRADES = list(range(1, 11))  # 1 (best) .. 10 (worst)


def _booking_codes_weights(cfg) -> tuple:
    locs = cfg.booking_locations
    return [l["code"] for l in locs], [float(l["weight"]) for l in locs]


def _industry_meta(word: str) -> tuple:
    return INDUSTRY_MAP.get(word, ("Diversified", word, False))


def allocate_counts(seed: int, n: int, total: int, salt: str = "") -> List[int]:
    """n integer counts, each >= 1, summing exactly to total, power-law skewed (largest-remainder)."""
    if total < n:
        raise ValueError("total must be >= n")
    weights = [rng.lognormal(seed, 0.0, 1.1, salt, i) for i in range(n)]
    s = sum(weights)
    extra = total - n  # after giving everyone the mandatory 1
    raw = [w / s * extra for w in weights]
    base = [int(x) for x in raw]
    counts = [b + 1 for b in base]
    remainder = extra - sum(base)
    # hand out the remaining units to the largest fractional parts
    order = sorted(range(n), key=lambda i: raw[i] - base[i], reverse=True)
    for k in range(remainder):
        counts[order[k % n]] += 1
    return counts


def build_groups(cfg) -> List[Dict]:
    seed = cfg.random_seed
    seg_counts = {k: v["n_groups"] for k, v in cfg.raw["segments"].items()}
    groups: List[Dict] = []
    gi = 0

    used_brands: set = set()

    # 1) storyline groups first, with reserved identity.
    reserved_segment_debit: Dict[str, int] = {}
    for key, spec in names.STORYLINE_GROUPS.items():
        gi += 1
        seg = spec["segment"]
        reserved_segment_debit[seg] = reserved_segment_debit.get(seg, 0) + 1
        used_brands.add(spec["group_name"])
        groups.append(_group_record(cfg, gi, seg, key, spec, used_brands))

    # 2) fill the rest per segment, minus the reserved storyline groups.
    for seg, n in seg_counts.items():
        remaining = n - reserved_segment_debit.get(seg, 0)
        for _ in range(max(remaining, 0)):
            gi += 1
            groups.append(_group_record(cfg, gi, seg, None, None, used_brands))
    return groups


def _unique_brand(seed: int, gi: int, segment: str, used: set) -> str:
    for attempt in range(0, 40):
        brand = names.group_brand(seed, gi, segment, attempt)
        if brand not in used:
            used.add(brand)
            return brand
    # extremely unlikely; fall back to an index-tagged brand
    brand = f"{names.group_brand(seed, gi, segment)} {gi}"
    used.add(brand)
    return brand


def _group_record(cfg, gi: int, segment: str, storyline_key, spec, used_brands: set) -> Dict:
    seed = cfg.random_seed
    if spec:
        brand = spec["group_name"]
        industry_word = spec["industry"]
        hq = spec["hq_country"]
    else:
        brand = _unique_brand(seed, gi, segment, used_brands)
        industry_word = names.group_industry_word(seed, gi, segment)
        hq = "JP" if segment == "Japanese Corporate" else rng.weighted_choice(
            seed, [l["code"] for l in cfg.booking_locations],
            [float(l["weight"]) for l in cfg.booking_locations], "ghq", gi)
    names.assert_clean(brand)
    sector, subsector, carbon = _industry_meta(industry_word)
    # tier skew: strategic concentrated in JC/NJLC
    u = rng.unit(seed, "tier", gi)
    strat_cut = 0.28 if segment in ("Japanese Corporate", "Non-Japanese Large Corporate") else 0.08
    tier = "Strategic" if u < strat_cut else ("Core" if u < strat_cut + 0.42 else "Transactional")
    # Japanese corporates rate better (parent support); storyline groups may pin a scripted grade
    grade = spec["internal_grade"] if spec and "internal_grade" in spec else _rating_grade(seed, gi, segment)
    hq_region = "JP" if hq == "JP" else "APAC"
    tier_factor = {"Strategic": 3.0, "Core": 1.2, "Transactional": 0.5}[tier]
    deposit_wealth = round(tier_factor * rng.lognormal(seed, 0.0, 1.0, "wealth", gi), 4)
    return {
        "group_id": f"SYN-G-{gi:04d}",
        "group_name": brand,
        "segment": segment,
        "relationship_tier": tier,
        "is_strategic_group": tier == "Strategic",
        "hq_country": hq,
        "hq_region": hq_region,
        "global_relationship_owner_region": "JP" if segment == "Japanese Corporate" else "APAC",
        "group_industry": industry_word,
        "industry_sector": sector,
        "industry_subsector": subsector,
        "is_carbon_intensive": carbon,
        "group_external_rating": _external_rating(grade),
        "group_internal_grade": grade,
        "deposit_wealth": deposit_wealth,
        "storyline_key": storyline_key,
    }


def _rating_grade(seed: int, gi: int, segment: str) -> int:
    base = rng.normal(seed, 4.5, 1.6, "grade", gi)
    if segment == "Japanese Corporate":
        base -= 0.8
    if segment == "Public Sector":
        base -= 1.0
    if segment == "Sponsor & Structured Finance":
        base += 1.0
    return int(min(10, max(1, round(base))))


def _external_rating(grade: int) -> str:
    scale = ["AAA", "AA+", "AA", "A+", "A", "BBB+", "BBB", "BB+", "BB", "B"]
    return scale[min(len(scale) - 1, max(0, grade - 1))]


def build_entities(cfg, groups: List[Dict]) -> List[Dict]:
    seed = cfg.random_seed
    total = int(cfg.volumes["apac_entities"]) if "apac_entities" in cfg.volumes else 2500
    codes, weights = _booking_codes_weights(cfg)
    counts = allocate_counts(seed, len(groups), total, salt="entities")
    wealth_map = {g["group_id"]: g.get("deposit_wealth", 1.0) for g in groups}
    entities: List[Dict] = []
    used_names: set = set()  # enforce GLOBAL legal-name uniqueness (clean ER truth)
    ei = 0
    for g, n_ent in zip(groups, counts):
        seen_country: Dict[str, int] = {}  # how many entities of this group already in a country
        scripted = names.STORYLINE_GROUPS.get(g["storyline_key"] or "", {}).get("entity_countries")
        for j in range(n_ent):
            ei += 1
            cc = (scripted[j % len(scripted)] if scripted
                  else rng.weighted_choice(seed, codes, weights, "ecc", g["group_id"], j))
            div_idx = seen_country.get(cc, 0)
            seen_country[cc] = div_idx + 1
            legal = names.entity_legal_name(g["group_name"], cc, seed, ei, division_idx=div_idx)
            bump = 0
            while legal in used_names and bump < 100:
                bump += 1
                legal = names.entity_legal_name(g["group_name"], cc, seed, ei,
                                                division_idx=max(div_idx, 1) + bump)
            used_names.add(legal)
            names.assert_clean(legal)
            base_q = _entity_quality(seed, g, ei)
            entities.append({
                "entity_id": f"SYN-E-{ei:05d}",
                "group_id": g["group_id"],
                "legal_name": legal,
                "short_name": g["group_name"],
                "segment": g["segment"],
                "relationship_tier": g["relationship_tier"],
                "industry_sector": g["industry_sector"],
                "industry_subsector": g["industry_subsector"],
                "is_carbon_intensive": g["is_carbon_intensive"],
                "booking_country": cc,
                "coverage_office": cc,
                "is_listed": bool(rng.unit(seed, "listed", ei) < 0.12),
                "internal_rating_grade": g["group_internal_grade"],
                "health_base": round(base_q, 4),
                "health_vol": round(0.03 + 0.07 * rng.unit(seed, "vol", ei), 4),
                "industry_cycle_beta": round(0.3 + 0.9 * rng.unit(seed, "beta", ei), 4),
                "group_wealth": wealth_map[g["group_id"]],
                "is_group_lead": j == 0,
                "storyline_key": g["storyline_key"],
            })
    # storyline 5: the three largest HK subsidiaries of distinct Japanese electronics groups
    hk = sorted((e for e in entities if not e["storyline_key"] and e["booking_country"] == "HK"
                 and e["segment"] == "Japanese Corporate" and "Electronic" in e["industry_subsector"]),
                key=lambda e: (-e["group_wealth"], e["entity_id"]))
    picked: set = set()
    for e in hk:
        if len(picked) < 3 and e["group_id"] not in picked:
            picked.add(e["group_id"])
            e["storyline_key"] = HK_CASA_KEY
    return entities


HK_CASA_KEY = "hk_casa"  # entity-level storyline key (its groups are ordinary)


def _entity_quality(seed: int, g: Dict, ei: int) -> float:
    # 0 (weak) .. 1 (strong); better grade -> higher quality; small idiosyncratic noise
    q = 1.0 - (g["group_internal_grade"] - 1) / 9.0
    return min(0.98, max(0.05, q + rng.normal(seed, 0.0, 0.06, "q", ei)))


def build_people(cfg) -> List[Dict]:
    seed = cfg.random_seed
    codes = [l["code"] for l in cfg.booking_locations]
    roles = [("RM", 120), ("Credit Analyst", 60), ("TB Sales", 30), ("Onboarding Officer", 25),
             ("Credit Approver", 10)]
    people: List[Dict] = []
    pid = 0
    for role, count in roles:
        for _ in range(count):
            pid += 1
            office = codes[rng.hash64(seed, "office", role, pid) % len(codes)]
            people.append({
                "employee_id": f"SYN-P-{pid:04d}",
                "name": _person_name(seed, pid),
                "role": role,
                "coverage_office": office,
                "team": f"{office}-{role.split()[0]}",
            })
    return people


_GIVEN = ["Aiko", "Hideo", "Mei", "Rahul", "Siti", "Wei", "Nguyen", "Arun", "Yuki", "Hana",
          "Jun", "Priya", "Budi", "Chen", "Minh", "Sofia", "Ken", "Lara", "Tomo", "Ravi"]
_SURN = ["Tan", "Lim", "Watanabe", "Sharma", "Wong", "Nguyen", "Kurosawa", "Reddy", "Lee",
         "Putra", "Zhang", "Devi", "Ito", "Rahman", "Goh", "Oka", "Mehta", "Chua", "Park", "Vo"]


def _person_name(seed: int, pid: int) -> str:
    g = _GIVEN[rng.hash64(seed, "given", pid) % len(_GIVEN)]
    s = _SURN[rng.hash64(seed, "surn", pid) % len(_SURN)]
    return f"{g} {s}"


def build_products() -> List[Dict]:
    hierarchy = {
        "Lending": {"Corporate Lending": ["Term Loan", "Revolving Credit Facility", "Bilateral Loan",
                                          "Syndicated Loan", "Overdraft"],
                    "Structured Lending": ["Project Finance", "Acquisition Finance", "Asset Finance"]},
        "Transaction Banking": {
            "Cash": ["Current Account", "Notional Pooling", "Physical Pooling"],
            "Liquidity": ["Time Deposit", "Money Market Deposit", "Investment Account"],
            "Payments": ["Domestic Payments", "Cross-Border Payments", "Host-to-Host", "Payments API"],
            "Trade Finance": ["Import LC", "Export LC", "Guarantee / SBLC", "Documentary Collection",
                              "Trade Loan", "Receivables Purchase"],
            "Supply Chain Finance": ["Payables Finance", "Receivables Finance"]},
        "Markets": {"FX": ["FX Spot", "FX Forward", "FX Swap"], "Rates": ["Interest Rate Swap", "Cap/Floor"]},
        "Sustainable Finance": {"Sustainable Finance": ["Green Loan", "Sustainability-Linked Loan"]},
    }
    rows: List[Dict] = []
    pid = 0
    for bl, fams in hierarchy.items():
        for fam, prods in fams.items():
            for p in prods:
                pid += 1
                rows.append({"product_id": f"SYN-PR-{pid:03d}", "business_line": bl,
                             "product_family": fam, "product": p})
    return rows
