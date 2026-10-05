"""Pair decisions per rule set: deterministic rules, conflict vetoes and the fuzzy score (PLAN §6).

Deterministic (score 1.0):  exact LEI-like id | tax id + country | v2: normalised name + legal form +
country (names cut at a field limit excluded). Vetoes — two records can't be the same legal entity
when both carry different LEI-like ids, different tax ids, or names pointing to different countries
(parenthetical city / country-specific legal form). Everything else is scored (similarity.py):
>= 0.90 auto | 0.75-0.90 steward | < 0.75 reject. Pairs are evaluated once per rule set over all
records; each quarterly run uses the pairs whose two records are in its snapshot.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List, NamedTuple, Optional

from . import similarity
from .blocking import Pair, candidate_pairs, pair_key
from .records import StdRecord

METHOD_LEI = "deterministic_lei"
METHOD_TAX = "deterministic_tax_country"
METHOD_NAME = "deterministic_name_country"
METHOD_FUZZY = "fuzzy"
DETERMINISTIC = (METHOD_LEI, METHOD_TAX, METHOD_NAME)
BLOCK_DETERMINISTIC = "deterministic_index"

DECISION_DETERMINISTIC, DECISION_AUTO = "deterministic", "auto"
DECISION_STEWARD, DECISION_REJECT = "steward", "reject"


class PairResult(NamedTuple):
    a: str
    b: str
    block: str
    method: str
    jaccard: float
    levenshtein: float
    location: float
    same_parent: bool
    same_country: bool
    fuzzy_score: float
    score: float          # 1.0 for deterministic matches, else the fuzzy score
    veto: Optional[str]
    decision: str         # deterministic | auto | steward | reject


def conflict(a: StdRecord, b: StdRecord) -> Optional[str]:
    if a.lei and b.lei and a.lei != b.lei:
        return "lei_conflict"
    if a.tax and b.tax and a.tax != b.tax:
        return "tax_conflict"
    na, nb = a.std.name_countries, b.std.name_countries
    if na and nb and not (na & nb):
        return "location_conflict"
    return None


def name_key(r: StdRecord) -> Optional[tuple]:
    """v2 exact key: normalised name + canonical legal form + country (complete names only)."""
    if not r.std.core or not r.std.legal_form or r.std.core_cut or not r.eff_country:
        return None
    return (r.std.core, r.std.legal_form, r.eff_country)


def deterministic_index_pairs(recs: List[StdRecord], rule_version: str) -> set:
    """Pairs sharing an exact key (LEI; tax + country; v2 name + country), found by index not blocking."""
    idx: Dict[tuple, List[str]] = defaultdict(list)
    for r in recs:
        if r.lei:
            idx[("lei", r.lei)].append(r.key)
        if r.tax and r.eff_country:
            idx[("tax", r.tax, r.eff_country)].append(r.key)
        if rule_version == "v2" and name_key(r):
            idx[("name",) + name_key(r)].append(r.key)
    out = set()
    for keys in idx.values():
        keys = sorted(set(keys))
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                out.add(pair_key(a, b))
    return out


def evaluate(a: StdRecord, b: StdRecord, rule_version: str, block: str,
             lev_cache: Optional[Dict] = None) -> PairResult:
    same_parent = bool(a.parent and a.parent == b.parent)
    same_country = bool(a.eff_country and a.eff_country == b.eff_country)
    f = similarity.score_std(a.std, b.std, same_parent=same_parent, same_country=same_country,
                             lev_cache=lev_cache)
    veto = conflict(a, b)
    method, decision = METHOD_FUZZY, None
    if a.lei and a.lei == b.lei:
        method = METHOD_LEI
    elif veto is None and a.tax and a.tax == b.tax and same_country:
        method = METHOD_TAX
    elif veto is None and rule_version == "v2" and name_key(a) and name_key(a) == name_key(b):
        method = METHOD_NAME
    if method != METHOD_FUZZY:
        decision, score, veto = DECISION_DETERMINISTIC, 1.0, None
    else:
        score = f["score"]
        decision = DECISION_REJECT if veto else similarity.classify(score)
    x, y = (a, b) if a.key < b.key else (b, a)
    return PairResult(a=x.key, b=y.key, block=block, method=method, jaccard=f["jaccard"],
                      levenshtein=f["levenshtein"], location=f["location"], same_parent=same_parent,
                      same_country=same_country, fuzzy_score=f["score"], score=score, veto=veto,
                      decision=decision)


def match_all(recs: Iterable[StdRecord], rule_version: str) -> Dict[Pair, PairResult]:
    """Every candidate pair (blocking + deterministic indexes) with its rule-set decision."""
    recs = list(recs)
    by_key = {r.key: r for r in recs}
    cands = candidate_pairs(recs, rule_version)
    for p in deterministic_index_pairs(recs, rule_version):
        cands.setdefault(p, BLOCK_DETERMINISTIC)
    cache: Dict = {}
    return {p: evaluate(by_key[p[0]], by_key[p[1]], rule_version, blk, cache) for p, blk in cands.items()}
