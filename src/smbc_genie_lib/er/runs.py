"""Six quarterly ER runs replayed on as-of snapshots of the source records (PLAN §6; DECISIONS D22).

  run_schedule(cfg)   v1 on 2025-06-30 / 09-30 / 12-31, v2 on 2026-03-31 / 06-30 / 09-30 (config)
  replay(...)         every silver output of every run, as row dicts

Per run: snapshot (record_available_from <= run date) -> deterministic + automatic matches ->
clusters -> one steward item per pair of clusters linked only by 0.75-0.90 pairs -> steward
decisions taken before the next run (the as-of date for the last run) -> final clusters -> stable
golden ids with merge / split / new events -> quality vs truth -> survivorship. A steward decision
persists: the pair is never queued again, and a "no match" overrides automatic rules for that pair.
The truth map only simulates steward decisions and scores runs; matching never sees it.
"""
from __future__ import annotations

import datetime as _dt
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import cluster, evaluate, matching, steward, survivorship
from .records import SOURCE_PRIORITY, StdRecord, standardise

DEFAULT_RUN_DATES = {"v1": ["2025-06-30", "2025-09-30", "2025-12-31"],
                     "v2": ["2026-03-31", "2026-06-30", "2026-09-30"]}
METHOD_DETERMINISTIC, METHOD_FUZZY, METHOD_STEWARD, METHOD_UNMATCHED = "deterministic", "fuzzy", "steward", "unmatched"
_METHOD_RANK = {METHOD_DETERMINISTIC: 0, METHOD_FUZZY: 1, METHOD_STEWARD: 2}
ALL = "ALL"


@dataclass(frozen=True)
class Run:
    run_id: str
    run_date: _dt.date
    rule_version: str
    seq: int


@dataclass
class Item:
    item_id: str
    run: Run
    a: str
    b: str
    score: float
    decision: str
    decided: _dt.date
    assigned_to: Optional[str]
    size_a: int
    size_b: int
    n_pairs: int = 1


def run_schedule(cfg) -> List[Run]:
    dates = cfg.entity_resolution.get("run_dates") or DEFAULT_RUN_DATES
    rows = sorted((_dt.date.fromisoformat(d), v) for v, ds in dates.items() for d in ds)
    return [Run(f"ER-{d:%Y%m%d}", d, v, i + 1) for i, (d, v) in enumerate(rows)]


def _links(final_edges: Dict[Tuple[str, str], Tuple[str, str, float]]) -> Dict[str, Tuple[str, str, float]]:
    """Record -> (method, rule, score) of its strongest accepted link."""
    best: Dict[str, Tuple[str, str, float]] = {}
    for (a, b), (method, rule, score) in sorted(final_edges.items()):
        for k in (a, b):
            cur = best.get(k)
            if cur is None or (_METHOD_RANK[method], -score, rule) < (_METHOD_RANK[cur[0]], -cur[2], cur[1]):
                best[k] = (method, rule, score)
    return best


@dataclass
class _State:
    """What carries over from run to run."""
    reviewed: Dict[Tuple[str, str], Item] = field(default_factory=dict)  # pairs a steward looked at
    settled: Dict[Tuple[str, str], Item] = field(default_factory=dict)   # 0.75-0.90 pairs an item settled
    prev: Dict[str, str] = field(default_factory=dict)                   # previous run's membership
    issued: Dict[str, Run] = field(default_factory=dict)                 # golden id -> run that issued it
    joined: Dict[str, Tuple[str, str]] = field(default_factory=dict)     # key -> (golden id, run it joined)
    next_seq: int = 1


def _automatic_edges(live: Dict, st: _State, snap: set, cycle_end: _dt.date) -> Dict:
    """Deterministic + auto pairs (unless a steward said no) + earlier steward matches now in effect."""
    edges: Dict[Tuple[str, str], Tuple[str, str, float]] = {}
    for p, r in live.items():
        manual = st.reviewed.get(p)
        if manual and manual.decision == steward.NO_MATCH and manual.decided <= cycle_end:
            continue
        if r.decision == matching.DECISION_DETERMINISTIC:
            edges[p] = (METHOD_DETERMINISTIC, r.method, 1.0)
        elif r.decision == matching.DECISION_AUTO:
            edges[p] = (METHOD_FUZZY, "fuzzy_auto", r.score)
    for p, it in st.reviewed.items():
        if it.decision == steward.MATCH and it.decided <= cycle_end and p[0] in snap and p[1] in snap:
            edges.setdefault(p, (METHOD_STEWARD, "steward_match", it.score))
    return edges


def _raise_items(run: Run, live: Dict, comps: List[List[str]], st: _State, cycle_end: _dt.date,
                 same_entity, seed: int, err: float, stewards: List[str], exact=lambda a, b: False) -> List[Item]:
    """One steward item per pair of clusters linked only by unsettled 0.75-0.90 pairs (the best pair
    represents them), skipping cluster pairs that already wait on an open item. Items `exact` flags
    (storyline clients) are decided without the simulated error."""
    comp_of = {k: ci for ci, c in enumerate(comps) for k in c}
    pending = {tuple(sorted((comp_of[it.a], comp_of[it.b]))) for it in st.reviewed.values()
               if it.decided > cycle_end and it.a in comp_of and it.b in comp_of}
    cands: Dict[Tuple[int, int], List] = defaultdict(list)
    for p, r in live.items():
        if r.decision == matching.DECISION_STEWARD and p not in st.settled and comp_of[p[0]] != comp_of[p[1]]:
            cp = tuple(sorted((comp_of[p[0]], comp_of[p[1]])))
            if cp not in pending:
                cands[cp].append(r)
    items: List[Item] = []
    order = sorted(cands, key=lambda cp: (-max(r.score for r in cands[cp]), comps[cp[0]][0], comps[cp[1]][0]))
    for n, cp in enumerate(order, 1):
        best = sorted(cands[cp], key=lambda r: (-r.score, r.a, r.b))[0]
        key = f"{best.a}|{best.b}"
        it = Item(item_id=f"SQ-{run.run_date:%y%m}-{n:05d}", run=run, a=best.a, b=best.b, score=best.score,
                  decision=steward.decide(seed, key, same_entity(best.a, best.b), 0.0 if exact(best.a, best.b) else err),
                  decided=steward.decided_date(seed, key, run.run_date), assigned_to=steward.assign(seed, key, stewards),
                  size_a=len(comps[cp[0]]), size_b=len(comps[cp[1]]), n_pairs=len(cands[cp]))
        st.reviewed[(best.a, best.b)] = it
        for r in cands[cp]:
            st.settled[(r.a, r.b)] = it
        items.append(it)
    return items


def _emit(out: Dict, run: Run, snap: set, live: Dict, membership: Dict[str, str], events: List[Dict],
          links: Dict, accepted: set, new_items: List[Item], st: _State, as_of: _dt.date, runs: List[Run]) -> None:
    """Membership, event, match and steward-queue rows of one run."""
    v = run.rule_version
    size = Counter(membership.values())
    for k in sorted(snap, key=cluster.record_order):
        g = membership[k]
        if st.joined.get(k, (None,))[0] != g:
            st.joined[k] = (g, run.run_id)
        method, rule, score = links.get(k, (METHOD_UNMATCHED, METHOD_UNMATCHED, None))
        src, _, sid = k.partition(":")
        out["membership"].append({
            "run_id": run.run_id, "run_date": run.run_date, "rule_version": v, "source_system": src,
            "source_id": sid, "golden_client_id": g, "match_method": method, "match_rule": rule,
            "match_score": score, "cluster_size": size[g], "is_new_record": k not in st.prev,
            "joined_golden_in_run_id": st.joined[k][1]})
    for e in events:
        out["events"].append({"run_id": run.run_id, "run_date": run.run_date, "rule_version": v, **e})
    for p in sorted(live):
        r = live[p]
        it = st.reviewed.get(p) or st.settled.get(p)
        out["matches"].append({
            "run_id": run.run_id, "run_date": run.run_date, "rule_version": v, "left_key": p[0],
            "right_key": p[1], "block": r.block, "method": r.method, "jaccard": r.jaccard,
            "levenshtein": r.levenshtein, "location": r.location, "same_parent": r.same_parent,
            "same_country": r.same_country, "fuzzy_score": r.fuzzy_score, "score": r.score,
            "veto": r.veto, "decision": r.decision, "steward_item_id": it.item_id if it else None,
            "is_link": p in accepted, "same_golden": membership[p[0]] == membership[p[1]]})
    for it in new_items:
        out["steward_queue"].append(_item_row(it, as_of, runs))


def _golden(snap: set, membership: Dict[str, str], S: Dict[str, StdRecord], links: Dict,
            issued: Dict[str, Run]) -> Dict[str, Dict]:
    """Survived identity of every golden client of the run."""
    members_of: Dict[str, List[StdRecord]] = defaultdict(list)
    for k in sorted(snap, key=cluster.record_order):
        members_of[membership[k]].append(S[k])
    link_scores = {k: (m, s) for k, (m, _, s) in links.items()}
    golden = {}
    for g, mem in members_of.items():
        golden[g] = survivorship.survive(mem, link_scores)
        golden[g]["created_run_id"], golden[g]["created_date"] = issued[g].run_id, issued[g].run_date
    return golden


def replay(cfg, records: List[Dict], truth: Dict[str, str], stewards: List[str],
           field_limits: Optional[Dict[str, int]] = None, runs: Optional[List[Run]] = None,
           exact_entities: Optional[set] = None) -> Dict:
    """Replay every run. `records` are canonical records carrying `available_from` (date).
    `exact_entities` (truth ids, e.g. the storyline clients) get error-free steward decisions, so the
    simulated 2% error never rewrites a scripted story."""
    seed = cfg.random_seed
    err = float(cfg.entity_resolution.get("steward_error_rate", 0.02))
    exact_ids = set(exact_entities or ())
    exact = lambda a, b: truth.get(a) in exact_ids or truth.get(b) in exact_ids  # noqa: E731
    as_of = _dt.date.fromisoformat(cfg.as_of_date)
    footprint = [l["code"] for l in cfg.booking_locations]
    runs = runs or run_schedule(cfg)
    by_key = {r["key"]: r for r in records}
    avail = {r["key"]: r["available_from"] for r in records}
    std: Dict[str, Dict[str, StdRecord]] = {}
    pairs: Dict[str, Dict] = {}
    for v in sorted({r.rule_version for r in runs}):
        std[v] = {r["key"]: standardise(r, v, footprint, field_limits) for r in records}
        pairs[v] = matching.match_all(std[v].values(), v)

    out = {k: [] for k in ("membership", "events", "matches", "steward_queue", "run_summary")}
    st = _State()
    golden_by_run: Dict[str, Dict[str, Dict]] = {}
    membership_by_run: Dict[str, Dict[str, str]] = {}
    for i, run in enumerate(runs):
        cycle_end = runs[i + 1].run_date - _dt.timedelta(days=1) if i + 1 < len(runs) else as_of
        snap = {k for k in by_key if avail[k] <= run.run_date}
        live = {p: r for p, r in pairs[run.rule_version].items() if p[0] in snap and p[1] in snap}
        edges = _automatic_edges(live, st, snap, cycle_end)
        new_items = _raise_items(run, live, cluster.components(snap, edges), st, cycle_end,
                                 lambda a, b: truth.get(a) is not None and truth.get(a) == truth.get(b),
                                 seed, err, stewards, exact)
        for it in new_items:
            if it.decision == steward.MATCH and it.decided <= cycle_end:
                edges[(it.a, it.b)] = (METHOD_STEWARD, "steward_match", it.score)
        membership, events, st.next_seq = cluster.assign_golden_ids(cluster.components(snap, edges), st.prev,
                                                                    st.next_seq)
        for g in set(membership.values()) - set(st.issued):
            st.issued[g] = run
        links = _links(edges)
        _emit(out, run, snap, live, membership, events, links, set(edges), new_items, st, as_of, runs)
        scored = {k: membership[k] for k in snap if k in truth}
        quality = {s: evaluate.pairwise(scored, truth, None if s == ALL else s) for s in [ALL] + SOURCE_PRIORITY}
        golden_by_run[run.run_id] = _golden(snap, membership, std[run.rule_version], links, st.issued)
        membership_by_run[run.run_id] = membership
        out["run_summary"] += _summaries(run, snap, membership, links, live, set(edges), new_items, events,
                                         quality, evaluate.purity(scored, truth), st.prev, as_of)
        st.prev = membership
    out["golden_by_run"], out["membership_by_run"] = golden_by_run, membership_by_run
    out["runs"], out["std"] = runs, std
    out["xref"] = _xref(runs[-1], out["membership"], st.reviewed, as_of)
    out["golden_identity"] = [{"golden_client_id": g, "run_id": runs[-1].run_id, "run_date": runs[-1].run_date, **a}
                              for g, a in sorted(golden_by_run[runs[-1].run_id].items())]
    return out


def _item_row(it: Item, as_of: _dt.date, runs: List[Run]) -> Dict:
    open_ = it.decided > as_of
    applied = next((r.run_id for j, r in enumerate(runs) if r.run_date >= it.run.run_date and
                    it.decided <= (runs[j + 1].run_date - _dt.timedelta(days=1) if j + 1 < len(runs) else as_of)),
                   None)
    sa, _, ia = it.a.partition(":")
    sb, _, ib = it.b.partition(":")
    return {"steward_item_id": it.item_id, "run_id": it.run.run_id, "run_date": it.run.run_date,
            "rule_version": it.run.rule_version, "left_source_system": sa, "left_source_id": ia,
            "right_source_system": sb, "right_source_id": ib, "match_score": it.score,
            "candidate_pairs": it.n_pairs, "left_cluster_records": it.size_a,
            "right_cluster_records": it.size_b, "assigned_to": it.assigned_to,
            "created_date": it.run.run_date, "decided_date": None if open_ else it.decided,
            "decision": None if open_ else it.decision, "status": "Open" if open_ else "Decided",
            "days_open": ((as_of if open_ else it.decided) - it.run.run_date).days,
            "is_open_at_as_of": open_, "applied_in_run_id": None if open_ else applied}


def _summaries(run, snap, membership, links, live, accepted, new_items, events, quality, pur, prev, as_of):
    rows = []
    ev = Counter(e["event_type"] for e in events)
    for s in [ALL] + SOURCE_PRIORITY:
        keys = [k for k in snap if s == ALL or k.startswith(s + ":")]
        if not keys:
            continue
        meth = Counter(links.get(k, (METHOD_UNMATCHED,))[0] for k in keys)
        g_of = Counter(membership[k] for k in keys)
        dup = 0
        if s == ALL:
            by_gs = Counter((membership[k], k.split(":", 1)[0]) for k in keys)
            dup = sum(n - 1 for n in by_gs.values())
        else:
            dup = sum(n - 1 for n in g_of.values())
        touches = (lambda p: True) if s == ALL else (lambda p: p[0].startswith(s + ":") or p[1].startswith(s + ":"))
        lp = [p for p in live if touches(p)]
        items = [it for it in new_items if s == ALL or it.a.startswith(s + ":") or it.b.startswith(s + ":")]
        q = quality[s]
        rows.append({
            "run_id": run.run_id, "run_date": run.run_date, "rule_version": run.rule_version, "source_system": s,
            "records_in": len(keys), "matched_deterministic": meth[METHOD_DETERMINISTIC],
            "matched_fuzzy": meth[METHOD_FUZZY], "steward_resolved": meth[METHOD_STEWARD],
            "unmatched": meth[METHOD_UNMATCHED], "duplicates_merged": dup, "golden_records": len(g_of),
            "precision": round(q["precision"], 6), "recall": round(q["recall"], 6),
            "started_at": _dt.datetime.combine(run.run_date, _dt.time(22, 0)),
            "candidate_pairs": len(lp),
            "deterministic_pairs": sum(1 for p in lp if live[p].decision == matching.DECISION_DETERMINISTIC),
            "auto_match_pairs": sum(1 for p in lp if live[p].decision == matching.DECISION_AUTO),
            "steward_band_pairs": sum(1 for p in lp if live[p].decision == matching.DECISION_STEWARD),
            "steward_items": len(items), "steward_items_open": sum(1 for it in items if it.decided > as_of),
            "links_accepted": sum(1 for p in lp if p in accepted),
            "records_new": sum(1 for k in keys if k not in prev),
            "purity": round(pur, 6) if s == ALL else None,
            "golden_new": ev["NEW"] if s == ALL else None, "golden_merged": ev["MERGE"] if s == ALL else None,
            "golden_split": ev["SPLIT"] if s == ALL else None,
            "true_pairs": q["true_pairs"], "predicted_pairs": q["predicted_pairs"], "tp_pairs": q["tp_pairs"]})
    return rows


def _xref(last: Run, membership_rows: List[Dict], reviewed: Dict, as_of: _dt.date) -> List[Dict]:
    """Latest run's source -> golden map, with the latest steward call on each record (or pending)."""
    last_dec: Dict[str, str] = {}
    for it in sorted(reviewed.values(), key=lambda it: (it.run.seq, it.item_id)):
        for k in (it.a, it.b):
            last_dec[k] = it.decision if it.decided <= as_of else "pending"
    rows = []
    for m in membership_rows:
        if m["run_id"] != last.run_id:
            continue
        k = f"{m['source_system']}:{m['source_id']}"
        rows.append({"source_system": m["source_system"], "source_id": m["source_id"],
                     "golden_client_id": m["golden_client_id"], "match_method": m["match_method"],
                     "match_rule": m["match_rule"], "match_score": m["match_score"],
                     "steward_decision": last_dec.get(k), "resolved_in_run_id": m["joined_golden_in_run_id"],
                     "run_id": last.run_id, "run_date": last.run_date})
    return rows


def group_exposure(runs: List[Run], membership_by_run: Dict[str, Dict[str, str]],
                   golden_by_run: Dict[str, Dict[str, Dict]], facts: Dict[str, Dict[str, Dict[str, float]]],
                   threshold_usd: float) -> List[Dict]:
    """Client group x run: lending drawn (credit_obligor) + trade outstanding (trade_party) at the run
    date, attributed through that run's resolution; also under the previous run's resolution (same
    facts) to isolate the impact of re-resolution. facts[run_id][record key] = {lending, trade}."""
    rows, prev_rows = [], {}
    for i, run in enumerate(runs):
        mem, gold = membership_by_run[run.run_id], golden_by_run[run.run_id]
        pmem = membership_by_run[runs[i - 1].run_id] if i else None
        pgold = golden_by_run[runs[i - 1].run_id] if i else None
        agg: Dict[Optional[str], Dict] = defaultdict(lambda: {"lending": 0.0, "trade": 0.0, "prior": 0.0,
                                                              "golden": set(), "obligors": 0, "parties": 0})
        for k, f in sorted(facts.get(run.run_id, {}).items()):
            g = mem.get(k)
            grp = gold[g]["client_group_id"] if g else None
            a = agg[grp]
            a["lending"] += f.get("lending", 0.0)
            a["trade"] += f.get("trade", 0.0)
            if g:
                a["golden"].add(g)
            a["obligors" if k.startswith("credit_obligor:") else "parties"] += 1
            if pmem is not None:
                pg = pmem.get(k)
                pgrp = pgold[pg]["client_group_id"] if pg else grp
                agg[pgrp]["prior"] += f.get("lending", 0.0) + f.get("trade", 0.0)
        cur = {}
        for grp, a in sorted(agg.items(), key=lambda kv: (kv[0] is None, kv[0] or "")):
            total = a["lending"] + a["trade"]
            prior = a["prior"] if i else None
            prev_total = prev_rows.get(grp)
            cur[grp] = total
            rows.append({
                "run_id": run.run_id, "run_date": run.run_date, "rule_version": run.rule_version,
                "client_group_id": grp, "attribution_status": "Attributed" if grp else "Unattributed",
                "n_golden_clients": len(a["golden"]), "n_obligor_records": a["obligors"],
                "n_trade_party_records": a["parties"], "lending_drawn_usd": round(a["lending"], 2),
                "trade_outstanding_usd": round(a["trade"], 2), "total_exposure_usd": round(total, 2),
                "prior_resolution_exposure_usd": None if prior is None else round(prior, 2),
                "resolution_change_usd": None if prior is None else round(total - prior, 2),
                "resolution_change_pct": (round((total - prior) / prior, 6) if prior else None),
                "prev_run_exposure_usd": None if prev_total is None else round(prev_total, 2),
                "change_vs_prev_run_pct": (round((total - prev_total) / prev_total, 6) if prev_total else None),
                "attention_threshold_usd": threshold_usd, "is_above_threshold": total >= threshold_usd,
                "crossed_threshold_on_resolution": bool(prior is not None and prior < threshold_usd <= total)})
        prev_rows = cur
    return rows
