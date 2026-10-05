"""Onboarding & KYC generator tests (pure Python; brief §5.6, storyline 7)."""
import datetime as _dt
from collections import defaultdict
from statistics import mean

import pytest

from smbc_genie_lib import fragment, kyc, names, onboarding, truth
from smbc_genie_lib.config import load_config

BRONZE = ["kyc_case", "kyc_case_stage", "kyc_review", "kyc_document", "kyc_screening", "kyc_feedback"]


def _month_end(y, m):
    return _dt.date(y + (m == 12), m % 12 + 1, 1) - _dt.timedelta(days=1)


@pytest.fixture(scope="module")
def built():
    cfg = load_config()
    groups = truth.build_groups(cfg)
    entities = truth.build_entities(cfg, groups)
    _, xref = fragment.fragment_all(cfg, entities, [g["group_id"] for g in groups])

    def nondup(src):
        m = {}
        for x in xref:
            if x["source_system"] == src and not x["is_within_source_dup"]:
                m.setdefault(x["entity_id"], x["source_id"])
        return m

    kyc_map, crm_map = nondup("kyc_customer"), nondup("crm_account")
    cohort = onboarding.new_client_cohort(cfg, entities, xref)
    out = kyc.build_kyc(cfg, groups, entities, truth.build_people(cfg), kyc_map, crm_map, cohort)
    return cfg, groups, entities, kyc_map, crm_map, cohort, out


def test_bronze_carries_no_truth_ids(built):
    *_, out = built
    for t in BRONZE:
        assert out[t], t
        assert all("entity_id" not in r and "group_id" not in r for r in out[t]), t
    assert all(r["entity_id"] for r in out["truth_onboarding_case"] if r["status"] == "Live")


def test_cases_cover_new_clients_and_applicants(built):
    _, _, _, kyc_map, _, cohort, out = built
    cases = out["kyc_case"]
    live = [c for c in cases if c["status"] == "Live"]
    assert len(live) == len(cohort)
    assert {c["kyc_id"] for c in live} <= set(kyc_map.values())
    assert all(c["kyc_id"] is None for c in cases if c["status"] != "Live")  # applicants
    assert {c["status"] for c in cases} == {"Live", "Open", "Withdrawn", "Rejected"}
    assert len({c["case_id"] for c in cases}) == len(cases)
    assert all(c["outcome_reason"] for c in cases if c["status"] in ("Withdrawn", "Rejected"))
    assert all(c["blocker_reason"] for c in cases if c["status"] == "Open")


def test_live_cases_end_on_the_account_dates(built):
    _, _, _, _, _, cohort, out = built
    truth_by_case = {r["case_id"]: r for r in out["truth_onboarding_case"]}
    for c in out["kyc_case"]:
        if c["status"] != "Live":
            continue
        s = cohort[truth_by_case[c["case_id"]]["entity_id"]]
        assert c["status_date"] == s["go_live_date"].isoformat()
        assert c["request_date"] == s["request_date"].isoformat()


def test_stage_events_are_contiguous(built):
    cfg, *_, out = built
    as_of = _dt.date.fromisoformat(cfg.as_of_date)
    by_case = defaultdict(list)
    for s in out["kyc_case_stage"]:
        by_case[s["case_id"]].append(s)
    for c in out["kyc_case"]:
        st = sorted(by_case[c["case_id"]], key=lambda s: s["stage_no"])
        assert [s["stage_no"] for s in st] == list(range(1, len(st) + 1))
        assert st[0]["entered_date"] == c["request_date"]
        for a, b in zip(st, st[1:]):
            assert a["exited_date"] == b["entered_date"]
        assert sum(s["is_current"] for s in st) <= 1
        for s in st:
            end = _dt.date.fromisoformat(s["exited_date"]) if s["exited_date"] else as_of
            assert s["days_in_stage"] == (end - _dt.date.fromisoformat(s["entered_date"])).days >= 0
            assert s["is_sla_met"] == (s["days_in_stage"] <= s["sla_days"])
        if c["status"] == "Live":
            assert st[5]["stage_name"] == "Account Open" and st[5]["exited_date"] == c["status_date"]
        elif c["status"] == "Open":
            assert st[-1]["is_current"] and st[-1]["stage_name"] == c["current_stage"]
        else:
            assert st[-1]["exited_date"] == c["status_date"] and st[-1]["stage_name"] == c["current_stage"]


def test_documents_outstanding_match_cases(built):
    *_, out = built
    outstanding = defaultdict(int)
    for d in out["kyc_document"]:
        outstanding[d["case_id"]] += d["status"] == "Outstanding"
        assert (d["received_date"] is not None) == (d["status"] == "Received")
    status = {c["case_id"]: c["status"] for c in out["kyc_case"]}
    for c in out["kyc_case"]:
        assert c["documents_outstanding"] == outstanding[c["case_id"]]
    assert all(status[d["case_id"]] == "Open" for d in out["kyc_document"] if d["status"] == "Outstanding")


def test_cases_stuck_in_kyc_docs_have_documents_outstanding(built):
    *_, out = built
    cases = {c["case_id"]: c for c in out["kyc_case"]}
    stuck = [s for s in out["kyc_case_stage"]
             if s["is_current"] and s["stage_name"] == "KYC Docs" and s["days_in_stage"] > 15]
    assert len(stuck) >= 5  # brief §5.6 Q4
    assert all(cases[s["case_id"]]["documents_outstanding"] > 0 for s in stuck)


def test_review_status_consistent(built):
    cfg, *_, out = built
    as_of = _dt.date.fromisoformat(cfg.as_of_date)
    assert len({r["review_id"] for r in out["kyc_review"]}) == len(out["kyc_review"])
    for r in out["kyc_review"]:
        due = _dt.date.fromisoformat(r["due_date"])
        if r["completed_date"]:
            done = _dt.date.fromisoformat(r["completed_date"])
            assert r["status"] == "Completed" and done <= as_of and r["risk_after"]
            assert r["days_overdue"] == max(0, (done - due).days)
        elif due < as_of:
            assert r["status"] == "Overdue" and r["days_overdue"] == (as_of - due).days
        else:
            assert r["status"] in ("Due Soon", "Scheduled") and r["days_overdue"] == 0
    # every onboarded client has its next review on the books
    upcoming = {r["kyc_id"] for r in out["kyc_review"] if r["status"] in ("Due Soon", "Scheduled", "Overdue")}
    assert upcoming == {r["kyc_id"] for r in out["kyc_review"]}


def test_latest_review_matches_current_risk(built):
    cfg, _, entities, kyc_map, *_, out = built
    latest = {}
    for r in out["kyc_review"]:
        if r["completed_date"] and r["completed_date"] >= latest.get(r["kyc_id"], ("",))[0]:
            latest[r["kyc_id"]] = (r["completed_date"], r["risk_after"])
    for e in entities:
        k = kyc_map.get(e["entity_id"])
        if k in latest:
            assert latest[k][1] == kyc.risk_rating(cfg, e)


def test_backlog_peaks_in_may_at_about_three_times_normal(built):
    cfg, _, entities, kyc_map, *_, out = built
    high = {kyc_map[e["entity_id"]] for e in entities
            if e["entity_id"] in kyc_map and kyc.risk_rating(cfg, e) == "High"}
    ends = [_month_end(2025, m) for m in range(4, 13)] + [_month_end(2026, m) for m in range(1, 10)]
    series = {d: kyc.overdue_at(out["kyc_review"], d, high) for d in ends}
    base = mean(v for d, v in series.items() if d <= _dt.date(2026, 1, 31))
    may = series[_dt.date(2026, 5, 31)]
    assert 2.7 <= may / base <= 3.6, (may, base)
    assert may == max(series.values())
    all_series = {d: kyc.overdue_at(out["kyc_review"], d) for d in ends}
    assert max(all_series, key=all_series.get) == _dt.date(2026, 5, 31)


def test_backlog_mostly_cleared_by_september(built):
    cfg, *_, out = built
    share = kyc.backlog_cleared_share(out["kyc_review"], _dt.date.fromisoformat(cfg.as_of_date))
    assert 0.65 <= share <= 0.75, share


def test_fi_kyc_docs_slowdown(built):
    *_, out = built
    delta = kyc.fi_kyc_docs_delta(out["kyc_case"], out["kyc_case_stage"])
    assert 10 <= delta <= 14, delta


def test_days_to_live_medians(built):
    *_, out = built
    before, peak = kyc.days_to_live_medians(out["truth_onboarding_case"])
    assert 19 <= before <= 23 and 36 <= peak <= 40, (before, peak)


def test_screening_valid(built):
    cfg, *_, out = built
    rows = out["kyc_screening"]
    ongoing = [r for r in rows if r["screening_context"] == "Ongoing"]
    assert len(ongoing) == round(kyc.ONGOING_SCREENINGS_AT_SCALE_1 * cfg.scale)
    assert all(r["resolution_hours"] > 0 for r in rows)
    # onboarded clients are never true sanctions matches
    assert not [r for r in ongoing if r["is_true_match"] and r["list_name"] not in kyc.TRUE_MATCH_P]
    rejected = {c["case_id"] for c in out["kyc_case"]
                if c["status"] == "Rejected" and c["outcome_reason"] == "Screening True Match"}
    hits = {r["case_id"] for r in rows if r["is_true_match"] and r["disposition"] == "True Match - Rejected"}
    assert rejected and rejected == hits
    assert 0.005 <= mean(r["is_true_match"] for r in rows) <= 0.03


def test_feedback_valid(built):
    cfg, *_, out = built
    as_of = _dt.date.fromisoformat(cfg.as_of_date)
    live = {c["case_id"]: c for c in out["kyc_case"] if c["status"] == "Live"}
    peak = {r["case_id"] for r in out["truth_onboarding_case"] if r["is_migration_peak"]}
    for f in out["kyc_feedback"]:
        assert f["case_id"] in live and 1 <= f["score"] <= 5
        assert live[f["case_id"]]["status_date"] < f["survey_date"] <= as_of.isoformat()
        assert len(f["comment_text"].split()) <= 40
    in_peak = [f["score"] for f in out["kyc_feedback"] if f["case_id"] in peak]
    others = [f["score"] for f in out["kyc_feedback"] if f["case_id"] not in peak]
    assert in_peak and mean(in_peak) < mean(others)
    assert any(f["score"] <= 2 for f in out["kyc_feedback"])


def test_applicant_names_are_new_and_clean(built):
    _, groups, entities, *_, out = built
    existing = {e["legal_name"] for e in entities}
    for c in out["kyc_case"]:
        if c["status"] != "Live":
            assert c["applicant_name"] not in existing
            names.assert_clean(c["applicant_name"])
    assert any(c["intake_group_match"] for c in out["kyc_case"])
    assert not all(c["intake_group_match"] for c in out["kyc_case"])


def test_deterministic(built):
    cfg, groups, entities, kyc_map, crm_map, cohort, out = built
    again = kyc.build_kyc(cfg, groups, entities, truth.build_people(cfg), kyc_map, crm_map, cohort)
    for t in BRONZE:
        assert again[t] == out[t], t
