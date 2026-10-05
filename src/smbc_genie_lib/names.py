"""Deterministic fictional company-name bank (brief §3.5, DECISIONS D23, D26).

Invented Japanese- and APAC-flavoured stems combined with industry words and local legal forms.
Nothing here is a real company; `assert_clean()` checks generated names against a blocklist of
well-known real corporates and banks. Names are assigned deterministically from the seed so the
universe is reproducible.

`group_name` is the short brand ("Kinokawa Precision"); entity legal names add a location and
legal form ("Kinokawa Precision (Singapore) Pte Ltd").
"""
from __future__ import annotations

from typing import Dict, List

from . import rng

# Invented stems — not real companies (checked against BLOCKLIST in tests).
JP_STEMS: List[str] = [
    "Kinokawa", "Hayashi", "Tanaka", "Aokumo", "Shiramine", "Hoshioka", "Midorikawa", "Takasugi",
    "Nishimori", "Kurogane", "Yamabe", "Sakaida", "Terauchi", "Oedo", "Konabe", "Harashima",
    "Mitsukawa", "Shinonome", "Akatsuki", "Hagane", "Tsukimori", "Narumi", "Oribe", "Fuwa",
    "Kazami", "Souma", "Tachibana-do", "Kisaragi", "Yukinoshita", "Amagase", "Hoshogawa",
    "Rindo", "Seto-oka", "Takamura", "Isurugi", "Nagatsuki", "Hazama", "Owari", "Katsuragi",
    "Minakata", "Shishido", "Umezono", "Kadono", "Tsujimura", "Ebisawa", "Hagiwara",
]
SEA_STEMS: List[str] = [
    "Sunda", "Nusantara", "Jelita", "Selat", "Kinabalu", "Andaman", "Suvarna", "Meridian",
    "Banksia", "Waratah", "Kowari", "Mekong", "Catba", "Halong", "Rajang", "Batavia", "Majapahit",
    "Langkawi", "Palawan", "Visayan", "Chaophraya", "Irrawaddy", "Sabah", "Tenggara", "Barito",
    "Mahakam", "Kapuas", "Dampier", "Pilbara", "Tasman", "Arafura", "Timor", "Luzon",
]
INDUSTRY_WORDS: Dict[str, List[str]] = {
    "Japanese Corporate": [
        "Precision", "Chemical", "Electronics", "Heavy Industries", "Trading", "Marine Logistics",
        "Motor", "Steel", "Materials", "Devices", "Semiconductor", "Machinery", "Construction",
        "Shipping", "Foods", "Auto Parts", "Instruments", "Fibre",
    ],
    "Non-Japanese Large Corporate": [
        "Energy", "Agri", "Resources", "Mining", "Palm", "Telecom", "Power", "Infrastructure",
        "Logistics", "Petrochem", "Minerals", "Consumer", "Beverages", "Property",
    ],
    "Financial Institution": [
        "Capital", "Securities", "Financial", "Asset Management", "Insurance", "Credit", "Leasing",
    ],
    "Sponsor & Structured Finance": [
        "Partners", "Capital Partners", "Infrastructure Fund", "Renewables", "Investments", "Ventures",
    ],
    "Public Sector": [
        "Authority", "Development Board", "Investment Corporation", "Power Corporation", "Rail",
    ],
}
GROUP_SUFFIX: Dict[str, List[str]] = {
    "Japanese Corporate": ["", "", "", "Holdings", "Group"],
    "Non-Japanese Large Corporate": ["", "", "Holdings", "Group", "Berhad Group"],
    "Financial Institution": ["", "Group", "Holdings"],
    "Sponsor & Structured Finance": ["", "Partners", "Capital"],
    "Public Sector": [""],
}
LEGAL_FORMS: Dict[str, List[str]] = {
    "SG": ["Pte Ltd"], "HK": ["Ltd", "(HK) Ltd"], "CN": ["Co Ltd"], "TH": ["Co Ltd", "PCL"],
    "ID": ["PT", "PT Tbk"], "IN": ["Pvt Ltd", "Ltd"], "AU": ["Pty Ltd", "Ltd"],
    "VN": ["JSC", "Co Ltd"], "MY": ["Sdn Bhd", "Berhad"], "TW": ["Co Ltd"], "KR": ["Co Ltd"],
    "PH": ["Inc", "Corp"], "NZ": ["Ltd"], "JP": ["KK", "Co Ltd"],
    "EMEA": ["GmbH", "SA", "BV", "Ltd"], "AMER": ["Inc", "LLC", "Corp"],
}
CITY_BY_CC: Dict[str, str] = {
    "SG": "Singapore", "HK": "Hong Kong", "CN": "Shanghai", "TH": "Bangkok", "ID": "Jakarta",
    "IN": "Mumbai", "AU": "Sydney", "VN": "Ho Chi Minh City", "MY": "Kuala Lumpur", "TW": "Taipei",
    "KR": "Seoul", "PH": "Manila", "NZ": "Auckland", "JP": "Tokyo",
}

# Fictional competitor / other banks (for FX-flow and loan-service-elsewhere signals).
COMPETITOR_BANKS: List[str] = [
    "Meridian Pacific Bank", "Garuda Commercial Bank", "Nordlys Bank", "Astra Union Bank",
    "Blue Harbour Bank", "Cathay Delta Bank", "Everest Mercantile Bank", "Solaris Bank",
]

# Substrings that must never appear in generated names (real corporates/banks). Lower-cased.
BLOCKLIST: List[str] = [
    "mitsubishi", "mitsui", "sumitomo", "smbc", "toyota", "honda", "nissan", "sony", "panasonic",
    "hitachi", "toshiba", "nippon", "marubeni", "itochu", "mizuho", "nomura", "softbank", "canon",
    "fujitsu", "sharp", "denso", "komatsu", "bridgestone", "mazda", "subaru", "suzuki", "kubota",
    "dbs", "ocbc", "uob", "hsbc", "standard chartered", "citibank", "citigroup", "jpmorgan",
    "barclays", "deutsche bank", "bnp paribas", "santander", "maybank", "cimb", "petronas",
    "petrobras", "sinopec", "petrochina", "reliance", "tata", "adani", "samsung", "hyundai", "lg ",
    "temasek", "garuda indonesia",
]

# Reserved brands for the storyline entities (brief §6.4). Must be used verbatim.
# `entity_countries` scripts each storyline group's booking footprint (PLAN §5.4 names list; cycled
# over the group's entities, lead first); `internal_grade` pins a scripted starting grade.
STORYLINE_GROUPS: Dict[str, Dict] = {
    "kinokawa": {"group_name": "Kinokawa Precision", "segment": "Japanese Corporate",
                 "industry": "Precision", "hq_country": "JP",
                 "entity_countries": ["SG", "TH", "VN", "CN", "IN", "ID"]},
    "hayashi": {"group_name": "Hayashi Marine Logistics", "segment": "Japanese Corporate",
                "industry": "Marine Logistics", "hq_country": "JP",
                "entity_countries": ["HK", "SG", "CN", "VN", "IN", "AU"]},
    "tanaka": {"group_name": "Tanaka Chemical", "segment": "Japanese Corporate",
               "industry": "Chemical", "hq_country": "JP", "entity_countries": ["SG", "HK"]},
    "sunda": {"group_name": "Sunda Energi Nusantara", "segment": "Non-Japanese Large Corporate",
              "industry": "Energy", "hq_country": "ID", "entity_countries": ["ID", "SG"],
              "internal_grade": 7},
    "meridian": {"group_name": "Meridian Agri Holdings", "segment": "Non-Japanese Large Corporate",
                 "industry": "Agri", "hq_country": "SG",
                 "entity_countries": ["SG", "ID", "MY", "TH", "IN", "AU"]},
    "banksia": {"group_name": "Banksia Renewables Partners", "segment": "Sponsor & Structured Finance",
                "industry": "Renewables", "hq_country": "AU", "entity_countries": ["AU", "AU", "NZ"]},
}


# Distinguishing qualifiers inserted (attempt > 0) to resolve brand collisions.
BRAND_QUALIFIERS: List[str] = ["Asia", "Pacific", "Oriental", "Union", "Prime", "Global",
                               "Delta", "Summit", "Crest", "Apex", "Orient", "Grand"]


def group_industry_word(seed: int, i: int, segment: str) -> str:
    """The industry word used in the group's brand (so name and sector classification agree)."""
    return rng.choice(seed, INDUSTRY_WORDS[segment], "indword", i)


def group_brand(seed: int, i: int, segment: str, attempt: int = 0) -> str:
    """Deterministic short brand for group index i; attempt>0 inserts a qualifier for uniqueness."""
    stems = JP_STEMS if segment == "Japanese Corporate" else SEA_STEMS
    stem = stems[rng.hash64(seed, "stem", segment, i) % len(stems)]
    word = group_industry_word(seed, i, segment)
    suffix = rng.choice(seed, GROUP_SUFFIX[segment], "suffix", i)
    mid = ""
    if attempt > 0:
        q = BRAND_QUALIFIERS[rng.hash64(seed, "qual", i, attempt) % len(BRAND_QUALIFIERS)]
        mid = f" {q}"
    return f"{stem}{mid} {word}{(' ' + suffix) if suffix else ''}".strip()


def legal_form(seed: int, country: str, i: int) -> str:
    forms = LEGAL_FORMS.get(country, ["Ltd"])
    return rng.choice(seed, forms, "legalform", country, i)


# Division qualifiers to keep multiple same-country entities of one group distinct & realistic.
DIVISION_WORDS: List[str] = ["Trading", "Manufacturing", "Services", "Industries", "Logistics",
                             "Capital", "Technologies", "Resources"]


def entity_legal_name(brand: str, country: str, seed: int, i: int, *,
                      with_location: bool = True, division_idx: int = 0) -> str:
    """Entity legal name: brand [division] + (location) + legal form.

    `division_idx` > 0 inserts a division word so repeated (group, country) entities stay unique
    and realistic, e.g. 'Kinokawa Precision Trading (Shanghai) Co Ltd'.
    """
    form = legal_form(seed, country, i)
    loc = CITY_BY_CC.get(country, country)
    if division_idx > 0:
        d = division_idx - 1
        div = DIVISION_WORDS[d % len(DIVISION_WORDS)]
        brand = f"{brand} {div}"
        extra = d // len(DIVISION_WORDS)
        if extra > 0:  # escalate with a second qualifier when the division words are exhausted
            brand = f"{brand} {BRAND_QUALIFIERS[(extra - 1) % len(BRAND_QUALIFIERS)]}"
    if with_location:
        return f"{brand} ({loc}) {form}".strip()
    return f"{brand} {form}".strip()


def assert_clean(name: str) -> None:
    low = name.lower()
    for bad in BLOCKLIST:
        if bad in low:
            raise ValueError(f"name {name!r} contains blocklisted substring {bad!r}")
