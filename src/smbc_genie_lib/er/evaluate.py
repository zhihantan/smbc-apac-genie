"""ER quality against the synthetic truth (PLAN §6, §11): pairwise precision / recall and purity.

Counted from the cluster x true-entity contingency table, so no pair enumeration is needed. The
per-source variant counts the pairs that involve at least one record of that source.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Dict, Optional


def _c2(n: int) -> int:
    return n * (n - 1) // 2


def pairwise(membership: Dict[str, str], truth: Dict[str, str], source: Optional[str] = None) -> Dict:
    """membership: record key -> golden id; truth: record key -> true entity id (same keys)."""
    in_src = (lambda k: k.split(":", 1)[0] == source) if source else (lambda k: True)
    cell, cell_s = Counter(), Counter()
    clus, clus_s, ent, ent_s = Counter(), Counter(), Counter(), Counter()
    for k, g in membership.items():
        e = truth[k]
        s = in_src(k)
        cell[(g, e)] += 1
        clus[g] += 1
        ent[e] += 1
        if s:
            cell_s[(g, e)] += 1
            clus_s[g] += 1
            ent_s[e] += 1
    if source:  # pairs with at least one record of the source = all pairs - pairs among the others
        tp = sum(_c2(n) - _c2(n - cell_s[ge]) for ge, n in cell.items())
        pred = sum(_c2(n) - _c2(n - clus_s[g]) for g, n in clus.items())
        true = sum(_c2(n) - _c2(n - ent_s[e]) for e, n in ent.items())
    else:
        tp = sum(_c2(n) for n in cell.values())
        pred = sum(_c2(n) for n in clus.values())
        true = sum(_c2(n) for n in ent.values())
    return {"tp_pairs": tp, "predicted_pairs": pred, "true_pairs": true,
            "precision": tp / pred if pred else 1.0, "recall": tp / true if true else 1.0}


def purity(membership: Dict[str, str], truth: Dict[str, str]) -> float:
    """Share of records that belong to their cluster's majority true entity."""
    by: Dict[str, Counter] = defaultdict(Counter)
    for k, g in membership.items():
        by[g][truth[k]] += 1
    n = sum(sum(c.values()) for c in by.values())
    return sum(max(c.values()) for c in by.values()) / n if n else 1.0
