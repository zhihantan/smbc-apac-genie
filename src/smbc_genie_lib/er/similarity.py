"""Name/identity similarity scoring for entity resolution (PLAN §6, DECISIONS D22).

Composite score = 0.45 token Jaccard + 0.30 normalised Levenshtein similarity
               + 0.15 location-token overlap + 0.05 same parent + 0.05 same country.

There are no addresses in the identity masters, so the location tokens are the gazetteer place of
the name's parenthetical plus the country it (or a country-specific legal form) implies; overlap is
the overlap coefficient, so "(Singapore) Pte Ltd" fully agrees with "Pte Ltd". Names cut at a source
field limit are compared on the common prefix. Pure stdlib. Thresholds:
  >= 0.90 auto-match | 0.75-0.90 steward queue | < 0.75 reject.
"""
from __future__ import annotations

from typing import Dict, Iterable, Optional, Sequence, Set, Tuple

from ..naming import StdName, one_edit, tokens

W_JACCARD = 0.45
W_LEVENSHTEIN = 0.30
W_ADDRESS = 0.15
W_PARENT = 0.05
W_COUNTRY = 0.05

AUTO_MATCH = 0.90
STEWARD_FLOOR = 0.75


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa: Set[str] = set(a)
    sb: Set[str] = set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def overlap(a: Iterable[str], b: Iterable[str]) -> float:
    """Overlap coefficient |A n B| / min(|A|, |B|); 0 when either side has no tokens."""
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / min(len(sa), len(sb))


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def levenshtein_sim(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    longest = max(len(a), len(b))
    return 1.0 - levenshtein(a, b) / longest if longest else 1.0


def address_overlap(a_tokens: Iterable[str], b_tokens: Iterable[str]) -> float:
    return jaccard(a_tokens, b_tokens)


TYPO_MIN_LEN = 5  # tokens this long that are one edit apart count as the same token


def token_jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    """Multiset token Jaccard ("TRADING TRADING" != "TRADING"); a 1-typo variant of a token of
    >= 5 characters counts as that token."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    rest, left, inter = list(b), [], 0
    for t in a:
        if t in rest:
            rest.remove(t)
            inter += 1
        else:
            left.append(t)
    for t in left:
        for i, u in enumerate(rest):
            if len(t) >= TYPO_MIN_LEN and len(u) >= TYPO_MIN_LEN and one_edit(t, u):
                del rest[i]
                inter += 1
                break
    return inter / (len(a) + len(b) - inter)


def _align_cut(cut: Sequence[str], other: Sequence[str]) -> Tuple[str, ...]:
    """A name cut mid-word: its partial last token stands for the other's token it prefixes."""
    if not cut:
        return tuple(cut)
    last = cut[-1]
    for t in other:
        if t != last and t.startswith(last):
            return tuple(cut[:-1]) + (t,)
    return tuple(cut)


def name_features(a: StdName, b: StdName) -> Tuple[float, float]:
    """(token Jaccard, normalised Levenshtein) of two core names. Levenshtein is the better of the
    recorded and the token-sorted word order; names cut at a field limit compare on the prefix."""
    ta, tb, ca, cb = a.tokens, b.tokens, a.core, b.core
    if a.core_cut or b.core_cut:
        if a.core_cut and not b.core_cut:
            ta, cb = _align_cut(ta, tb), cb[:len(ca)]
        elif b.core_cut and not a.core_cut:
            tb, ca = _align_cut(tb, ta), ca[:len(cb)]
        else:
            n = min(len(ca), len(cb))
            ca, cb = ca[:n], cb[:n]
        return token_jaccard(ta, tb), levenshtein_sim(ca, cb)
    lev = levenshtein_sim(ca, cb)
    if lev < 1.0:
        lev = max(lev, levenshtein_sim(" ".join(sorted(ta)), " ".join(sorted(tb))))
    return token_jaccard(ta, tb), lev


def composite(jac: float, lev: float, loc: float, same_parent: bool, same_country: bool) -> float:
    score = (W_JACCARD * jac + W_LEVENSHTEIN * lev + W_ADDRESS * loc
             + W_PARENT * (1.0 if same_parent else 0.0) + W_COUNTRY * (1.0 if same_country else 0.0))
    return round(score, 6)


def score_std(a: StdName, b: StdName, *, same_parent: bool, same_country: bool,
              lev_cache: Optional[Dict] = None) -> Dict[str, float]:
    """Feature breakdown + composite score for two standardised names."""
    key = (a.core, b.core, a.core_cut, b.core_cut, a.tokens, b.tokens)
    if lev_cache is not None and key in lev_cache:
        jac, lev = lev_cache[key]
    else:
        jac, lev = name_features(a, b)
        if lev_cache is not None:
            lev_cache[key] = (jac, lev)
    loc = overlap(a.location_tokens, b.location_tokens)
    return {"jaccard": round(jac, 6), "levenshtein": round(lev, 6), "location": round(loc, 6),
            "same_parent": bool(same_parent), "same_country": bool(same_country),
            "score": composite(jac, lev, loc, same_parent, same_country)}


def score_pair(
    name_a: str,
    name_b: str,
    *,
    address_a: Optional[Iterable[str]] = None,
    address_b: Optional[Iterable[str]] = None,
    same_parent: bool = False,
    same_country: bool = False,
) -> float:
    """Composite similarity in [0, 1] from raw names and explicit address tokens (Phase-1 helper)."""
    ta, tb = tokens(name_a), tokens(name_b)
    jac = jaccard(ta, tb)
    lev = levenshtein_sim(" ".join(ta), " ".join(tb))
    addr = address_overlap(address_a or [], address_b or []) if (address_a or address_b) else 0.0
    return composite(jac, lev, addr, same_parent, same_country)


def classify(score: float) -> str:
    if score >= AUTO_MATCH:
        return "auto"
    if score >= STEWARD_FLOOR:
        return "steward"
    return "reject"
