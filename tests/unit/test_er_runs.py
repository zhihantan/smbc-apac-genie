"""Six replayed ER runs end to end on the locally fragmented universe (the same generator as bronze):
quality gates, v1 < v2 recall, golden-id stability, steward queue, exposure attribution, and the
Meridian-like 3 -> 1 merge at the first v2 run."""
import datetime as _dt
import random
from collections import Counter

import pytest

from smbc_genie_lib import accounts, fragment, onboarding, truth
from smbc_genie_lib.config import load_config
from smbc_genie_lib.er import records, runs

NO_PARENT = {"trade_party", "tsy_counterparty"}  # bronze projections without a parent column


def _bronze_like(cfg, ents, recs, xref):
    """Canonical records as run_er.py builds them from bronze (projection, KYC/core availability)."""
    def nondup(src):
        m = {}
        for x in xref:
            if x["source_system"] == src and not x["is_within_source_dup"]:
                m.setdefault(x["entity_id"], x["source_id"])
        return m

    cohort = onboarding.new_client_cohort(cfg, ents, xref)
    by_id = {e["entity_id"]: e for e in ents}
    first_open = {}
    for a in accounts.build_accounts(cfg, ents, nondup("core_customer"), cohort):
        first_open[a["cust_no"]] = min(first_open.get(a["cust_no"], a["open_date"]), a["open_date"])
    out, tmap = [], {}
    for r, x in zip(recs, xref):
        src, cols = r["source_system"], records.SOURCE_COLUMNS[r["source_system"]]
        row = {cols["id"]: r["source_id"], cols["name"]: r["name_recorded"], cols["country"]: r["country_code"],
               cols["lei"]: r["lei"]}
        if "parent" in cols and src not in NO_PARENT:
            row[cols["parent"]] = r["parent_group_id"]
        if "tax" in cols:
            row[cols["tax"]] = r["tax_id"]
        if src == "kyc_customer":
            row["customer_since"] = onboarding.customer_since(cfg, by_id[x["entity_id"]], cohort).isoformat()
        c = records.project(src, row)
        c["available_from"], c["available_basis"] = records.availability(c, first_open, _dt.date(2023, 4, 1))
        out.append(c)
        tmap[c["key"]] = x["entity_id"]
    return out, tmap


@pytest.fixture(scope="module")
def replayed():
    cfg = load_config()
    groups = truth.build_groups(cfg)
    ents = truth.build_entities(cfg, groups)
    recs, xref = fragment.fragment_all(cfg, ents, [g["group_id"] for g in groups])
    canon, tmap = _bronze_like(cfg, ents, recs, xref)
    stewards = [p["employee_id"] for p in truth.build_people(cfg) if p["role"] == "Onboarding Officer"]
    limits = records.detect_field_limits(canon)
    out = runs.replay(cfg, canon, tmap, stewards, limits)
    summ = {r["run_id"]: r for r in out["run_summary"] if r["source_system"] == runs.ALL}
    return cfg, canon, tmap, stewards, limits, out, summ


def test_run_schedule_from_config(replayed):
    cfg, *_ = replayed
    sched = runs.run_schedule(cfg)
    assert [(r.run_id, r.rule_version) for r in sched] == [
        ("ER-20250630", "v1"), ("ER-20250930", "v1"), ("ER-20251231", "v1"),
        ("ER-20260331", "v2"), ("ER-20260630", "v2"), ("ER-20260930", "v2")]


def test_gates_at_latest_run_and_v1_recall_lower(replayed):
    cfg, *_, out, summ = replayed
    er = cfg.entity_resolution
    last = summ[out["runs"][-1].run_id]
    assert last["rule_version"] == "v2"
    assert last["precision"] >= er["gate_precision"] and last["recall"] >= er["gate_recall"]
    v1 = [s["recall"] for s in summ.values() if s["rule_version"] == "v1"]
    v2 = [s["recall"] for s in summ.values() if s["rule_version"] == "v2"]
    assert max(v1) + 0.03 < min(v2)  # v2 catches abbreviations and wrong-country records
    assert all(s["purity"] > 0.99 for s in summ.values())


def test_snapshots_grow_and_v2_collapses_golden_records(replayed):
    *_, out, summ = replayed
    rows = [summ[r.run_id] for r in out["runs"]]
    assert [r["records_in"] for r in rows] == sorted(r["records_in"] for r in rows)
    assert rows[0]["records_in"] < rows[-1]["records_in"] == len(replayed[1])
    assert rows[0]["golden_new"] == rows[0]["golden_records"]
    first_v2 = next(r for r in rows if r["rule_version"] == "v2")
    assert first_v2["golden_merged"] > 100 and first_v2["golden_records"] < rows[2]["golden_records"]


def test_golden_ids_stable_across_runs(replayed):
    *_, out, _ = replayed
    mem = [out["membership_by_run"][r.run_id] for r in out["runs"]]
    for prev, cur in zip(mem, mem[1:]):
        common = [k for k in prev if k in cur]
        assert sum(prev[k] == cur[k] for k in common) / len(common) > 0.95
    assert set(mem[-1].values()) <= {g for m in mem for g in m.values()}
    merged_away = {e["related_golden_client_id"] for e in out["events"] if e["event_type"] == "MERGE"}
    assert not merged_away & set(mem[-1].values())  # retired ids never come back


def test_steward_queue_shape(replayed):
    *_, out, summ = replayed
    q = out["steward_queue"]
    per_run = Counter(i["run_id"] for i in q)
    first, last = out["runs"][0].run_id, out["runs"][-1].run_id
    assert per_run[first] == max(per_run.values())  # initial backlog
    assert all(i["status"] == "Open" and i["decision"] is None for i in q if i["run_id"] == last)
    decided = [i for i in q if i["status"] == "Decided"]
    assert decided and all(i["decision"] in ("match", "no_match") and i["days_open"] >= 1 for i in decided)
    assert all(0.75 <= i["match_score"] < 0.90 for i in q)
    assert sum(summ[r.run_id]["steward_items"] for r in out["runs"]) == len(q)


def test_outputs_carry_no_truth_ids(replayed):
    *_, out, _ = replayed
    for table in ("membership", "events", "steward_queue", "xref", "golden_identity"):
        for row in out[table]:
            assert not any(isinstance(v, str) and v.startswith("SYN-E-") for v in row.values()), table


def test_xref_and_golden_identity_cover_the_latest_run(replayed):
    _, canon, *_, out, _ = replayed
    xref = out["xref"]
    assert len(xref) == len({(x["source_system"], x["source_id"]) for x in xref}) == len(canon)
    golden = {g["golden_client_id"] for g in out["golden_identity"]}
    assert golden == {x["golden_client_id"] for x in xref}
    assert {x["match_method"] for x in xref} == {"deterministic", "fuzzy", "steward", "unmatched"}
    with_group = sum(1 for g in out["golden_identity"] if g["client_group_id"])
    assert with_group / len(golden) > 0.97


def test_replay_is_order_independent(replayed):
    cfg, canon, tmap, stewards, limits, *_ = replayed
    keep = {e for e in sorted(set(tmap.values()))[:400]}
    sub = [r for r in canon if tmap[r["key"]] in keep]
    shuffled = list(sub)
    random.Random(4).shuffle(shuffled)
    a = runs.replay(cfg, sub, tmap, stewards, limits)
    b = runs.replay(cfg, shuffled, tmap, stewards, limits)
    assert a["membership"] == b["membership"] and a["steward_queue"] == b["steward_queue"]


def test_exact_entities_get_error_free_steward_decisions(replayed):
    cfg, canon, tmap, stewards, limits, out, _ = replayed

    def keys(i):
        return f"{i['left_source_system']}:{i['left_source_id']}", f"{i['right_source_system']}:{i['right_source_id']}"

    def wrong(i):
        a, b = keys(i)
        return i["decision"] is not None and (i["decision"] == "match") != (tmap[a] == tmap[b])

    bad = [i for i in out["steward_queue"] if wrong(i)]
    assert bad  # the simulated 2% error is present
    exact = {tmap[k] for i in bad[:3] for k in keys(i)}
    keep = exact | set(sorted(set(tmap.values()))[:300])
    sub = [r for r in canon if tmap[r["key"]] in keep]
    base = runs.replay(cfg, sub, tmap, stewards, limits)
    fixed = runs.replay(cfg, sub, tmap, stewards, limits, exact_entities=exact)
    touches = lambda i: bool({tmap[k] for k in keys(i)} & exact)  # noqa: E731
    assert any(wrong(i) and touches(i) for i in base["steward_queue"])
    assert not any(wrong(i) and touches(i) for i in fixed["steward_queue"])
    dec = lambda q: {keys(i): i["decision"] for i in q if not touches(i) and i["decision"]}  # noqa: E731
    d0, d1 = dec(base["steward_queue"]), dec(fixed["steward_queue"])
    common = d0.keys() & d1.keys()
    assert common and all(d0[k] == d1[k] for k in common)  # everyone else keeps the simulated error


def test_group_exposure_attribution_and_resolution_impact():
    r1, r2 = runs.Run("ER-1", _dt.date(2025, 12, 31), "v1", 1), runs.Run("ER-2", _dt.date(2026, 3, 31), "v2", 2)
    mem = {"ER-1": {"credit_obligor:O1": "GC-1", "trade_party:T1": "GC-2"},
           "ER-2": {"credit_obligor:O1": "GC-1", "trade_party:T1": "GC-1"}}
    gold = {"ER-1": {"GC-1": {"client_group_id": "G"}, "GC-2": {"client_group_id": None}},
            "ER-2": {"GC-1": {"client_group_id": "G"}}}
    facts = {"ER-1": {"credit_obligor:O1": {"lending": 400.0}, "trade_party:T1": {"trade": 150.0}},
             "ER-2": {"credit_obligor:O1": {"lending": 400.0}, "trade_party:T1": {"trade": 160.0}}}
    rows = runs.group_exposure([r1, r2], mem, gold, facts, 500.0)
    g1 = next(r for r in rows if r["run_id"] == "ER-1" and r["client_group_id"] == "G")
    un1 = next(r for r in rows if r["run_id"] == "ER-1" and r["client_group_id"] is None)
    g2 = next(r for r in rows if r["run_id"] == "ER-2" and r["client_group_id"] == "G")
    assert (g1["total_exposure_usd"], un1["attribution_status"], un1["total_exposure_usd"]) == (400.0, "Unattributed", 150.0)
    assert g2["total_exposure_usd"] == 560.0 and g2["prior_resolution_exposure_usd"] == 400.0
    assert g2["resolution_change_pct"] == pytest.approx(0.4) and g2["crossed_threshold_on_resolution"]
    assert g2["prev_run_exposure_usd"] == 400.0


def test_meridian_like_replay_three_then_one():
    from test_er_rules import MERIDIAN, SIBLING  # noqa: WPS433 (shared fixture data)
    cfg = load_config()
    canon = []
    for src, cols in MERIDIAN + SIBLING:
        c = records.project(src, cols)
        c["available_from"], c["available_basis"] = _dt.date(2023, 4, 1), records.BASIS_ASSUMED
        canon.append(c)
    tmap = {c["key"]: ("E-SIB" if c["source_id"] in ("K2", "E2") else "E-MER") for c in canon}
    out = runs.replay(cfg, canon, tmap, ["P1"], {"core_customer": 35})
    mer = [c["key"] for c in canon if tmap[c["key"]] == "E-MER"]
    n_golden = {r.run_id: len({out["membership_by_run"][r.run_id][k] for k in mer}) for r in out["runs"]}
    assert [n_golden[r.run_id] for r in out["runs"]] == [3, 3, 3, 1, 1, 1]
    v2 = out["runs"][3].run_id
    merges = [e for e in out["events"] if e["run_id"] == v2 and e["event_type"] == "MERGE"]
    assert len(merges) == 2
    first_ids = {out["membership_by_run"][out["runs"][0].run_id][k] for k in mer}
    assert out["membership_by_run"][v2][mer[0]] == min(first_ids)  # oldest golden id survives
