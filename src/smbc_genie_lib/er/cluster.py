"""Clustering and stable golden ids (PLAN §6).

Connected components over the accepted match edges (union-find; same result as the PLAN's
min-label propagation, clusters are small). Golden ids persist across runs: each previous golden
id is inherited by the new cluster holding most of its records (ties: the cluster with the smallest
record key); a cluster inheriting several ids keeps the oldest and records the others as merged; a
cluster inheriting none gets a new id (SPLIT when its records came from existing ids, else NEW).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Tuple

from .records import SOURCE_RANK

GOLDEN_PREFIX = "GC-"
EVENT_NEW, EVENT_MERGE, EVENT_SPLIT = "NEW", "MERGE", "SPLIT"


def golden_id(seq: int) -> str:
    return f"{GOLDEN_PREFIX}{seq:06d}"


def golden_seq(gid: str) -> int:
    return int(gid[len(GOLDEN_PREFIX):])


def record_order(key: str) -> Tuple[int, str]:
    """Deterministic record order: source priority, then source id."""
    src, _, sid = key.partition(":")
    return SOURCE_RANK.get(src, 99), sid


def components(nodes: Iterable[str], edges: Iterable[Tuple[str, str]]) -> List[List[str]]:
    """Connected components, each sorted by record_order, listed by their first record."""
    parent: Dict[str, str] = {n: n for n in nodes}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        if a in parent and b in parent:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
    groups: Dict[str, List[str]] = defaultdict(list)
    for n in parent:
        groups[find(n)].append(n)
    out = [sorted(g, key=record_order) for g in groups.values()]
    return sorted(out, key=lambda c: record_order(c[0]))


def assign_golden_ids(clusters: List[List[str]], prev: Dict[str, str], next_seq: int):
    """Returns (membership key -> golden id, events, next_seq). `prev` is the previous run's membership."""
    counts = [Counter(prev[k] for k in c if k in prev) for c in clusters]
    best: Dict[str, tuple] = {}
    for i, cnt in enumerate(counts):
        for g, n in cnt.items():
            cand = (-n, record_order(clusters[i][0]), i)  # most of g's records, then smallest record
            if g not in best or cand < best[g]:
                best[g] = cand
    heir = {g: v[2] for g, v in best.items()}
    membership: Dict[str, str] = {}
    events: List[Dict] = []
    for i, c in enumerate(clusters):
        inherited = sorted((g for g in counts[i] if heir[g] == i), key=golden_seq)
        if inherited:
            gid = inherited[0]
            for g in inherited[1:]:
                events.append({"event_type": EVENT_MERGE, "golden_client_id": gid, "related_golden_client_id": g,
                               "n_records": counts[i][g]})
        else:
            gid = golden_id(next_seq)
            next_seq += 1
            if not counts[i]:
                events.append({"event_type": EVENT_NEW, "golden_client_id": gid, "related_golden_client_id": None,
                               "n_records": len(c)})
        for g, n in sorted(counts[i].items()):
            if heir[g] != i:
                events.append({"event_type": EVENT_SPLIT, "golden_client_id": gid, "related_golden_client_id": g,
                               "n_records": n})
        for k in c:
            membership[k] = gid
    return membership, events, next_seq
