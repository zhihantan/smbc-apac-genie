"""Candidate-pair generation (blocking) per rule set (PLAN §6).

  v1: (country, first significant token), (soundex(first token), country)
  v2: + (first two tokens, either order) across countries — catches wrong-country records.

The v2 cross-country block only pairs records across countries when one side's country is suspect
(outside the booking footprint, or contradicted by its own name without a unique correction) and the
other side's country fits that record's name; otherwise every sibling of a multi-country group (same
brand, different country) would be compared. Same-country pairs in that block are kept when word
order differs (they share no first token).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Tuple

from .. import naming
from .records import StdRecord, is_suspect

BLOCK_COUNTRY_TOKEN = "country+first_token"
BLOCK_SOUNDEX = "soundex+country"
BLOCK_FIRST_TWO = "first_two_tokens"
BLOCK_CROSS_COUNTRY = "first_two_tokens_cross_country"
BLOCK_ORDER = [BLOCK_COUNTRY_TOKEN, BLOCK_SOUNDEX, BLOCK_FIRST_TWO, BLOCK_CROSS_COUNTRY]

Pair = Tuple[str, str]


def pair_key(a: str, b: str) -> Pair:
    return (a, b) if a < b else (b, a)


def block_keys(r: StdRecord, rule_version: str) -> List[tuple]:
    t = r.std.tokens
    if not t:
        return []
    cc = r.eff_country or ""
    keys = [(BLOCK_COUNTRY_TOKEN, cc, t[0]), (BLOCK_SOUNDEX, naming.soundex(t[0]), cc)]
    if rule_version == "v2" and len(t) >= 2:
        keys.append((BLOCK_FIRST_TWO,) + tuple(sorted(t[:2])))
    return keys


def cross_country_ok(a: StdRecord, b: StdRecord) -> bool:
    """May two records booked in different countries be compared? (v2 wrong-country rule)"""
    for s, o in ((a, b), (b, a)):
        if is_suspect(s) and (not s.std.name_countries or (o.eff_country in s.std.name_countries)):
            return True
    return False


def candidate_pairs(recs: List[StdRecord], rule_version: str) -> Dict[Pair, str]:
    """Pair -> the first blocking key type that proposed it."""
    blocks: Dict[tuple, List[StdRecord]] = defaultdict(list)
    for r in recs:
        for k in block_keys(r, rule_version):
            blocks[k].append(r)
    out: Dict[Pair, str] = {}
    rank = {b: i for i, b in enumerate(BLOCK_ORDER)}

    def add(a: StdRecord, b: StdRecord, kind: str) -> None:
        p = pair_key(a.key, b.key)
        if p not in out or rank[kind] < rank[out[p]]:
            out[p] = kind

    for k, members in blocks.items():
        if len(members) < 2:
            continue
        members = sorted(members, key=lambda r: r.key)
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                if k[0] != BLOCK_FIRST_TWO:
                    add(a, b, k[0])
                elif a.eff_country == b.eff_country:
                    if a.std.tokens[0] != b.std.tokens[0]:
                        add(a, b, BLOCK_FIRST_TWO)
                elif cross_country_ok(a, b):
                    add(a, b, BLOCK_CROSS_COUNTRY)
    return out
