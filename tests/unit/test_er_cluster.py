"""Clusters, stable golden ids, simulated stewards, survivorship and evaluation (PLAN §6)."""
import datetime as _dt

import pytest

from smbc_genie_lib.er import cluster, evaluate, records, steward, survivorship

FOOTPRINT = ["SG", "HK", "CN", "TH", "MY"]


def test_components_are_deterministic_and_ordered():
    nodes = ["kyc_customer:K2", "ext_company_master:E1", "trade_party:T1", "kyc_customer:K1", "crm_account:A9"]
    edges = [("kyc_customer:K1", "trade_party:T1"), ("ext_company_master:E1", "kyc_customer:K1")]
    comps = cluster.components(set(nodes), edges)
    assert comps == [["ext_company_master:E1", "kyc_customer:K1", "trade_party:T1"], ["kyc_customer:K2"],
                     ["crm_account:A9"]]
    assert cluster.components(reversed(nodes), list(reversed(edges))) == comps


def test_golden_ids_new_then_stable():
    m1, ev1, nxt = cluster.assign_golden_ids([["a:1", "b:1"], ["a:2"]], {}, 1)
    assert m1 == {"a:1": "GC-000001", "b:1": "GC-000001", "a:2": "GC-000002"} and nxt == 3
    assert [e["event_type"] for e in ev1] == ["NEW", "NEW"]
    m2, ev2, nxt2 = cluster.assign_golden_ids([["a:1", "b:1", "c:1"], ["a:2"]], m1, nxt)  # a record joins
    assert m2["c:1"] == "GC-000001" and ev2 == [] and nxt2 == 3


def test_merge_keeps_the_oldest_id_and_split_gets_a_new_one():
    prev = {"a:1": "GC-000001", "b:1": "GC-000001", "a:2": "GC-000002", "c:2": "GC-000003"}
    merged, ev, nxt = cluster.assign_golden_ids([["a:1", "b:1", "a:2", "c:2"]], prev, 4)
    assert set(merged.values()) == {"GC-000001"}
    assert sorted((e["event_type"], e["related_golden_client_id"]) for e in ev) == [("MERGE", "GC-000002"),
                                                                                    ("MERGE", "GC-000003")]
    g1, g2 = "GC-000001", "GC-000002"
    split, ev2, _ = cluster.assign_golden_ids([["a:1", "b:1"], ["a:2"], ["c:2"]],
                                              {"a:1": g1, "b:1": g1, "a:2": g1, "c:2": g2}, 7)
    assert split["a:1"] == g1 and split["c:2"] == g2 and split["a:2"] == "GC-000007"
    assert ev2 == [{"event_type": "SPLIT", "golden_client_id": "GC-000007", "related_golden_client_id": g1,
                    "n_records": 1}]


def test_pairwise_metrics_and_purity():
    truth = {"s:1": "E1", "s:2": "E1", "s:3": "E1", "t:4": "E2", "t:5": "E2"}
    perfect = {"s:1": "G1", "s:2": "G1", "s:3": "G1", "t:4": "G2", "t:5": "G2"}
    q = evaluate.pairwise(perfect, truth)
    assert (q["precision"], q["recall"], q["true_pairs"]) == (1.0, 1.0, 4)
    over = {k: "G1" for k in truth}  # one big cluster: 4 of 10 pairs right
    assert evaluate.pairwise(over, truth)["precision"] == pytest.approx(0.4)
    under = {"s:1": "G1", "s:2": "G1", "s:3": "G3", "t:4": "G2", "t:5": "G2"}
    assert evaluate.pairwise(under, truth)["recall"] == pytest.approx(2 / 4)
    assert evaluate.pairwise(under, truth, "t")["recall"] == 1.0  # only pairs touching source t
    assert evaluate.purity(over, truth) == pytest.approx(3 / 5)


def test_steward_error_rate_turnaround_and_assignment():
    keys = [f"a:{i}|b:{i}" for i in range(4000)]
    wrong = sum(steward.decide(7, k, True, 0.02) == steward.NO_MATCH for k in keys)
    assert 50 <= wrong <= 115  # ~2%
    assert steward.decide(7, keys[0], True, 0.0) == steward.MATCH
    assert steward.decide(7, keys[0], False, 0.0) == steward.NO_MATCH
    days = [steward.turnaround_days(7, k) for k in keys]
    assert min(days) >= 1 and max(days) <= steward.PARKED_DAYS[1]
    assert 0.03 < sum(d >= steward.PARKED_DAYS[0] for d in days) / len(days) < 0.10  # parked items
    assert steward.decided_date(7, keys[0], _dt.date(2026, 3, 31)) > _dt.date(2026, 3, 31)
    assert steward.assign(7, keys[0], ["P1", "P2"]) in ("P1", "P2") and steward.assign(7, keys[0], []) is None


def _std(src, **cols):
    return records.standardise(records.project(src, cols), "v2", FOOTPRINT, {"core_customer": 35})


def test_survivorship_priority_and_parent_consensus():
    members = [
        _std("trade_party", party_id="T1", party_name="Kinokawa Precision", party_country="SG"),
        _std("core_customer", cust_no="C1", cust_name="KINOKAWA PRECISION (SINGAPORE) PTE", cntry="SG",
             parent_cust_grp="SYN-G-0001", tax_no="TX1"),
        _std("kyc_customer", kyc_id="K1", legal_entity_name="Kinokawa Precision (Singapore) Pte Ltd",
             country_of_incorp="SG", parent_entity_id="SYN-G-0001", lei="SYNLEI1", tax_identification_no="TX1"),
        _std("ext_company_master", company_id="E1", registered_name="Kinokawa Precision Pte Ltd", country_code="US",
             parent_company_id="SYN-G-0142", lei_code="SYNLEI1"),  # wrong country + stale parent
        _std("crm_account", account_id="A1", account_name="Kinokawa Precision Pte Ltd", country="SG",
             ultimate_parent_id="SYN-G-0001"),
    ]
    links = {m.key: ("deterministic", 1.0) for m in members}
    g = survivorship.survive(members, links)
    assert (g["legal_name"], g["legal_name_source"]) == ("Kinokawa Precision Pte Ltd", "ext_company_master")
    assert g["display_name"] == "Kinokawa Precision (Singapore) Pte Ltd" and g["short_name"] == "Kinokawa Precision"
    assert g["country_of_incorporation"] == "SG"  # the ext country is contradicted by "Pte Ltd" -> corrected
    assert g["immediate_parent_id"] == "SYN-G-0142"  # strict priority: ext's (stale) parent
    assert (g["client_group_id"], g["parent_basis"]) == ("SYN-G-0001", "majority_vote")  # outvoted 3:1
    assert g["lei_like_id"] == "SYNLEI1" and g["n_distinct_lei"] == 1 and g["n_source_records"] == 5
    assert g["source_systems_present"][0] == "ext_company_master" and "Kinokawa Precision" in g["aliases"]
    assert 0.9 < g["golden_record_confidence"] <= 1.0


def test_survivorship_singleton_without_parent():
    g = survivorship.survive([_std("trade_party", party_id="T9", party_name="Rindo Foods", party_country="TH")])
    assert g["client_group_id"] is None and g["parent_basis"] == "none"
    assert g["golden_record_confidence"] < 0.6 and g["aliases"] == []
    assert survivorship.short_name("Hayashi Marine Logistics (HK) Ltd") == "Hayashi Marine Logistics"
