"""Company-name standardisation for entity resolution (PLAN §6; DECISIONS D22, D23).

  normalize(s)          uppercase, de-accented, punctuation stripped, whitespace collapsed.
  parse_name(s, v)      -> StdName: the core name (parenthetical locations and the trailing legal form
                        removed; rule set v2 also expands abbreviations), its tokens, the canonical
                        legal form, the gazetteer location of the parenthetical, and the countries the
                        name itself implies (parenthetical city / country-specific legal form).
  core_name / tokens / first_token / strip_legal   convenience views (v2 by default).
  soundex(token)        American Soundex, for the (soundex(first token), country) block.
  normalize_country / normalize_id                 ISO-2 countries; LEI-like / tax ids without separators.

Group suffixes (Holdings, Group) are part of the brand, not legal forms: "Tanaka Chemical" and
"Tanaka Chemical Group" are different groups. Dependency-free so it runs identically on the driver
and in unit tests.
"""
from __future__ import annotations

import re
import unicodedata
from typing import FrozenSet, List, NamedTuple, Optional, Tuple

RULE_VERSIONS = ("v1", "v2")

# Trailing legal forms: normalised token sequence -> canonical form (longest match wins).
LEGAL_FORM_VARIANTS = {
    "PTE LTD": "PTE LTD", "PTE LIMITED": "PTE LTD", "PTE": "PTE LTD",
    "PRIVATE LIMITED": "PRIVATE LTD", "PRIVATE LTD": "PRIVATE LTD",
    "PVT LTD": "PVT LTD", "PVT LIMITED": "PVT LTD", "PVT": "PVT LTD",
    "PTY LTD": "PTY LTD", "PTY LIMITED": "PTY LTD", "PROPRIETARY LIMITED": "PTY LTD", "PTY": "PTY LTD",
    "SDN BHD": "SDN BHD", "SENDIRIAN BERHAD": "SDN BHD", "SDN": "SDN BHD",
    "BERHAD": "BERHAD", "BHD": "BERHAD",
    "PUBLIC CO LTD": "PCL", "PUBLIC COMPANY LIMITED": "PCL", "PCL": "PCL",
    "CO LTD": "CO LTD", "CO LIMITED": "CO LTD", "COMPANY LIMITED": "CO LTD", "COMPANY LTD": "CO LTD",
    "CO": "CO LTD",
    "JSC": "JSC", "JOINT STOCK COMPANY": "JSC",
    "PT TBK": "PT TBK", "TBK": "PT TBK", "PT": "PT",
    "LTD": "LTD", "LIMITED": "LTD",
    "INC": "INC", "INCORPORATED": "INC", "CORP": "CORP",
    "KK": "KK", "KABUSHIKI KAISHA": "KK", "GK": "GK", "YK": "YK",
    "LLC": "LLC", "LLP": "LLP", "PLC": "PLC", "GMBH": "GMBH", "AG": "AG", "SA": "SA", "SAS": "SAS",
    "BV": "BV", "NV": "NV", "SPA": "SPA", "SRL": "SRL", "AB": "AB", "OY": "OY",
}
_MAX_FORM_LEN = max(len(k.split()) for k in LEGAL_FORM_VARIANTS)
# Countries in which a canonical legal form is used (one country = location evidence).
LEGAL_FORM_COUNTRIES = {
    "PTE LTD": {"SG"}, "PRIVATE LTD": {"SG", "IN"}, "PVT LTD": {"IN"}, "PTY LTD": {"AU"},
    "SDN BHD": {"MY"}, "BERHAD": {"MY"}, "PCL": {"TH"}, "JSC": {"VN"}, "PT": {"ID"}, "PT TBK": {"ID"},
    "KK": {"JP"}, "GK": {"JP"}, "YK": {"JP"},
    "CO LTD": {"CN", "TH", "VN", "TW", "KR", "JP"}, "LTD": {"HK", "IN", "AU", "NZ", "SG", "GB"},
    "INC": {"PH", "US"}, "CORP": {"PH", "US"}, "LLC": {"US"},
}
# Kept for Phase-1 callers: legal-form tokens dropped anywhere by strip_legal().
LEGAL_TOKENS = {t for k in LEGAL_FORM_VARIANTS for t in k.split()} - {"PUBLIC", "COMPANY", "JOINT", "STOCK",
                                                                       "PRIVATE", "PROPRIETARY"}

# Rule set v2 abbreviation dictionary (normalised token -> expansion). Romanisation variants too.
ABBREVIATIONS = {
    "INTL": "INTERNATIONAL", "INT": "INTERNATIONAL",
    "MFG": "MANUFACTURING", "MFR": "MANUFACTURING",
    "HLDGS": "HOLDINGS", "HLDG": "HOLDINGS", "HOLDING": "HOLDINGS",
    "INDS": "INDUSTRIES", "IND": "INDUSTRIES",
    "ELEC": "ELECTRONICS", "ELECS": "ELECTRONICS",
    "TECH": "TECHNOLOGY", "TECHNOL": "TECHNOLOGY",
    "SVCS": "SERVICES", "SVC": "SERVICES",
    "CORP": "CORPORATION", "RES": "RESOURCES", "GRP": "GROUP",
    "MGMT": "MANAGEMENT", "DEV": "DEVELOPMENT", "INV": "INVESTMENT", "ENGG": "ENGINEERING",
    "LOGISTIC": "LOGISTICS", "RESOURCE": "RESOURCES",
    "ENERGI": "ENERGY",  # romanisation variant seen in ID / MY names
}

# Gazetteer for parenthetical locations: normalised place -> (canonical location, ISO-2).
_PLACES = {
    "SG": ["SINGAPORE", "SG"], "HK": ["HONG KONG", "HK", "HONGKONG"],
    "CN": ["SHANGHAI", "BEIJING", "SHENZHEN", "GUANGZHOU", "CHINA", "PRC"],
    "TH": ["BANGKOK", "THAILAND"], "ID": ["JAKARTA", "INDONESIA"],
    "IN": ["MUMBAI", "NEW DELHI", "DELHI", "INDIA"], "AU": ["SYDNEY", "MELBOURNE", "AUSTRALIA"],
    "VN": ["HO CHI MINH CITY", "HO CHI MINH", "HANOI", "VIETNAM", "VIET NAM"],
    "MY": ["KUALA LUMPUR", "MALAYSIA"], "TW": ["TAIPEI", "TAIWAN"], "KR": ["SEOUL", "KOREA"],
    "PH": ["MANILA", "PHILIPPINES"], "NZ": ["AUCKLAND", "NEW ZEALAND"], "JP": ["TOKYO", "OSAKA", "JAPAN"],
}
GAZETTEER = {p: (names[0], cc) for cc, names in _PLACES.items() for p in names}
COUNTRY_ALIASES = {"SINGAPORE": "SG", "HONG KONG": "HK", "CHINA": "CN", "THAILAND": "TH",
                   "INDONESIA": "ID", "INDIA": "IN", "AUSTRALIA": "AU", "VIETNAM": "VN", "VIET NAM": "VN",
                   "MALAYSIA": "MY", "TAIWAN": "TW", "KOREA": "KR", "SOUTH KOREA": "KR", "PHILIPPINES": "PH",
                   "NEW ZEALAND": "NZ", "JAPAN": "JP", "UK": "GB", "UNITED KINGDOM": "GB", "USA": "US",
                   "UNITED STATES": "US"}

_PUNCT_RE = re.compile(r"[^0-9A-Z\s]")
_PAREN_RE = re.compile(r"\(([^)]*)\)|\(([^)]*)$")  # closed, or unclosed at the end (field truncation)
_WS_RE = re.compile(r"\s+")
_ID_RE = re.compile(r"[^0-9A-Z]")


class StdName(NamedTuple):
    normalized: str                 # normalize(raw), parentheses and all
    core: str                       # match key: no parenthetical, no legal form (v2: abbreviations expanded)
    tokens: Tuple[str, ...]         # core tokens
    legal_form: str                 # canonical trailing legal form, or ""
    location: str                   # canonical gazetteer location of the parenthetical, or ""
    location_tokens: FrozenSet[str]  # location words + the ISO-2 country they / the legal form imply
    name_countries: FrozenSet[str]  # countries consistent with the name evidence (empty = no evidence)
    truncated: bool                 # recorded name hit its source field limit
    core_cut: bool                  # ... and the cut fell inside the name body (no parenthetical survived)


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize(s: Optional[str]) -> str:
    """Uppercase, de-accent, drop punctuation, collapse whitespace."""
    if s is None:
        return ""
    s = _strip_accents(str(s)).upper()
    return _WS_RE.sub(" ", _PUNCT_RE.sub(" ", s)).strip()


def remove_parentheticals(s: str) -> str:
    return _WS_RE.sub(" ", _PAREN_RE.sub(" ", str(s))).strip()


def one_edit(a: str, b: str) -> bool:
    """True if a and b are at most one edit apart (cheap typo tolerance)."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    i = 0
    while i < min(la, lb) and a[i] == b[i]:
        i += 1
    if la == lb:
        return a[i + 1:] == b[i + 1:]
    return (a[i:] == b[i + 1:]) if la < lb else (a[i + 1:] == b[i:])


def lookup_place(text: str, truncated: bool = False) -> Optional[Tuple[str, str]]:
    """Gazetteer lookup of a parenthetical: exact, prefix (truncated field) or one-typo match."""
    t = normalize(text)
    if not t:
        return None
    if t in GAZETTEER:
        return GAZETTEER[t]
    if truncated and len(t) >= 4:
        hits = {GAZETTEER[p] for p in GAZETTEER if p.startswith(t)}
        if len({cc for _, cc in hits}) == 1:
            return sorted(hits)[0]
    if len(t) >= 5:
        hits = {GAZETTEER[p] for p in GAZETTEER if len(p) >= 5 and one_edit(t, p)}
        if len({cc for _, cc in hits}) == 1:
            return sorted(hits)[0]
    return None


_FORM_WORDS = sorted({t for k in LEGAL_FORM_VARIANTS for t in k.split()})


def _complete_cut_form(toks: List[str]) -> List[str]:
    """A field cut inside the trailing legal form ('... CO LT', '... SDN B'): complete its last word."""
    if len(toks) < 2 or toks[-1] in LEGAL_FORM_VARIANTS:
        return toks
    words = [w for w in _FORM_WORDS if w.startswith(toks[-1])]
    for w in words:  # prefer a completion that forms a two-word legal form with the previous token
        if f"{toks[-2]} {w}" in LEGAL_FORM_VARIANTS:
            return toks[:-1] + [w]
    for w in words:
        if w in LEGAL_FORM_VARIANTS:
            return toks[:-1] + [w]
    return toks


def _split_legal_form(toks: List[str]) -> Tuple[List[str], str]:
    """Remove a leading 'PT' and the longest trailing legal form. Returns (tokens, canonical form)."""
    form = ""
    for n in range(min(_MAX_FORM_LEN, len(toks) - 1), 0, -1):
        key = " ".join(toks[-n:])
        if key in LEGAL_FORM_VARIANTS:
            form, toks = LEGAL_FORM_VARIANTS[key], toks[:-n]
            break
    if len(toks) > 1 and toks[0] == "PT":
        toks, form = toks[1:], form or "PT"
    return toks, form


def parse_name(raw: Optional[str], rule_version: str = "v2", max_len: Optional[int] = None) -> StdName:
    """Standardise a recorded company name under a rule set (see module docstring)."""
    s = _strip_accents(str(raw or "")).upper()
    truncated = bool(max_len) and len(str(raw or "")) >= max_len
    core_cut = truncated and "(" not in s
    locs = []
    for m in _PAREN_RE.finditer(s):
        closed, unclosed = m.group(1), m.group(2)
        hit = lookup_place(closed if closed is not None else unclosed, truncated=closed is None)
        if hit:
            locs.append(hit)
    toks = [t for t in normalize(_PAREN_RE.sub(" ", s)).split(" ") if t]
    if truncated and not core_cut:
        toks = _complete_cut_form(toks)
    toks, form = _split_legal_form(toks)
    if rule_version == "v2":
        toks = [ABBREVIATIONS.get(t, t) for t in toks]
    location, loc_cc = (locs[0] if locs else ("", ""))
    # location evidence: the parenthetical place, else a single-country legal form (not a cut one)
    form_cc = LEGAL_FORM_COUNTRIES.get(form, set()) if (form and not truncated) else set()
    countries = {loc_cc} if loc_cc else (set(form_cc) if len(form_cc) == 1 else set())
    loc_tokens = set(location.split()) | countries
    return StdName(normalized=normalize(s), core=" ".join(toks), tokens=tuple(toks), legal_form=form,
                   location=location, location_tokens=frozenset(loc_tokens),
                   name_countries=frozenset(countries), truncated=truncated, core_cut=core_cut)


def strip_legal(s: str) -> str:
    """normalize() then drop legal-form tokens anywhere (Phase-1 helper)."""
    return " ".join(t for t in normalize(s).split(" ") if t and t not in LEGAL_TOKENS)


def core_name(s: str, rule_version: str = "v2") -> str:
    """Blocking / match key: parentheticals and the legal form removed (v2: abbreviations expanded)."""
    return parse_name(s, rule_version).core


def tokens(s: str, rule_version: str = "v2") -> List[str]:
    """Significant tokens of the core name (for Jaccard / blocking)."""
    return list(parse_name(s, rule_version).tokens)


def first_token(s: str, rule_version: str = "v2") -> str:
    toks = tokens(s, rule_version)
    return toks[0] if toks else ""


_SOUNDEX = {**dict.fromkeys("BFPV", "1"), **dict.fromkeys("CGJKQSXZ", "2"), **dict.fromkeys("DT", "3"),
            "L": "4", **dict.fromkeys("MN", "5"), "R": "6"}


def soundex(token: str) -> str:
    """American Soundex code (letter + 3 digits) of a token; '' for no letters."""
    letters = [c for c in normalize(token) if c.isalpha()]
    if not letters:
        return ""
    out, prev = [letters[0]], _SOUNDEX.get(letters[0], "")
    for c in letters[1:]:
        code = _SOUNDEX.get(c, "")
        if code and code != prev:
            out.append(code)
        if c not in "HW":  # H and W don't separate equal codes
            prev = code
    return ("".join(out) + "000")[:4]


def normalize_country(s: Optional[str]) -> Optional[str]:
    """ISO-2 country code from a code or common name; None when blank."""
    t = normalize(s)
    if not t:
        return None
    if t in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[t]
    if len(t) > 2 and t in GAZETTEER:
        return GAZETTEER[t][1]
    return t  # ISO-2 already, or an unrecognised value kept as recorded


def normalize_id(s: Optional[str]) -> Optional[str]:
    """LEI-like / tax id with case and separators removed; None when blank."""
    t = _ID_RE.sub("", _strip_accents(str(s or "")).upper())
    return t or None
