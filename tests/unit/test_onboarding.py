"""New-client onboarding schedule + its effect on accounts (pure Python; DECISIONS D43)."""
import datetime as _dt
from collections import defaultdict
from statistics import median

import pytest

from smbc_genie_lib import accounts, fragment, onboarding, storyline_injectors, storylines, truth
from smbc_genie_lib.config import load_config


@pytest.fixture(scope="module")
def built():
    cfg = load_config()
    groups = truth.build_groups(cfg)
    entities = truth.build_entities(cfg, groups)
    _, xref = fragment.fragment_all(cfg, entities, [g["group_id"] for g in groups])
    custno = {}
    for x in xref:
        if x["source_system"] == "core_customer" and not x["is_within_source_dup"]:
            custno.setdefault(x["entity_id"], x["source_id"])
    cohort = onboarding.new_client_cohort(cfg, entities, xref)
    accts = accounts.build_accounts(cfg, entities, custno, cohort)
    plain = accounts.build_accounts(cfg, entities, custno)
    return cfg, entities, xref, cohort, accts, plain


def test_cohort_is_deposits_and_payments_only(built):
    _, entities, xref, cohort, _, _ = built
    sources = defaultdict(set)
    for x in xref:
        sources[x["entity_id"]].add(x["source_system"])
    size = defaultdict(int)
    for e in entities:
        size[e["group_id"]] += 1
    by_id = {e["entity_id"]: e for e in entities}
    th = storyline_injectors.meridian_th_entity(load_config(), entities)
    for eid in cohort:
        e = by_id[eid]
        assert {"core_customer", "kyc_customer"} <= sources[eid]
        assert not sources[eid] & onboarding.THIN_EXCLUDE  # no lending, trade or FX identity
        assert not e["storyline_key"] or eid == th["entity_id"]   # only Meridian's scripted Thai subsidiary
        assert not (e["is_group_lead"] and size[e["group_id"]] > 1)
    assert th["entity_id"] in cohort and cohort[th["entity_id"]]["request_date"] == storylines.MERIDIAN["onboarding_request"]


def test_cohort_dates_ordered_and_live(built):
    cfg, _, _, cohort, _, _ = built
    as_of = _dt.date.fromisoformat(cfg.as_of_date)
    assert 200 <= len(cohort) <= 320, len(cohort)
    for s in cohort.values():
        assert onboarding.WINDOW_START <= s["request_date"] < s["go_live_date"] <= as_of
        assert s["first_txn_date"] > s["go_live_date"]
        assert (s["go_live_date"] - s["request_date"]).days == sum(s["stage_days"].values())


def test_days_to_live_storyline(built):
    _, _, _, cohort, _, _ = built
    before = [(s["go_live_date"] - s["request_date"]).days for s in cohort.values()
              if s["request_date"] < onboarding.MIGRATION_START]
    peak = [(s["go_live_date"] - s["request_date"]).days for s in cohort.values()
            if _dt.date(2026, 3, 1) <= s["request_date"] <= _dt.date(2026, 5, 31)]
    assert 19 <= median(before) <= 23, median(before)
    assert 36 <= median(peak) <= 40, median(peak)


def test_fy2026_go_lives_include_slow_first_transactions(built):
    cfg, _, _, cohort, _, _ = built
    as_of = _dt.date.fromisoformat(cfg.as_of_date)
    fy26 = [s for s in cohort.values() if s["go_live_date"] >= _dt.date(2026, 4, 1)]
    slow = [s for s in fy26 if s["go_live_date"] <= as_of - _dt.timedelta(days=60)
            and (s["first_txn_date"] - s["go_live_date"]).days > 60]
    assert len(fy26) >= 20 and len(slow) >= 2  # brief §5.6 Q8 has rows to return


def test_new_client_accounts_start_at_go_live(built):
    _, _, _, cohort, accts, _ = built
    by_entity = defaultdict(list)
    for a in accts:
        by_entity[a["entity_id"]].append(a)
    for eid, s in cohort.items():
        rows = by_entity[eid]
        assert rows and min(a["open_date"] for a in rows) == s["go_live_date"]
        for a in rows:
            assert a["active_from"] == max(a["open_date"], s["first_txn_date"])


def test_long_standing_accounts_unchanged(built):
    _, _, _, cohort, accts, plain = built
    assert [a["account_id"] for a in accts] == [a["account_id"] for a in plain]
    for a, p in zip(accts, plain):
        assert a["base_balance_usd"] == p["base_balance_usd"]
        if a["entity_id"] not in cohort:
            assert a["open_date"] == p["open_date"] == a["active_from"]


def test_customer_since_precedes_activity(built):
    cfg, entities, _, cohort, accts, _ = built
    first_open = {}
    for a in accts:
        first_open[a["entity_id"]] = min(first_open.get(a["entity_id"], a["open_date"]), a["open_date"])
    for e in entities:
        since = onboarding.customer_since(cfg, e, cohort)
        if e["entity_id"] in cohort:
            assert since == cohort[e["entity_id"]]["go_live_date"]
        elif e["entity_id"] in first_open:
            assert since < first_open[e["entity_id"]]


def test_storyline_switch_off_removes_slowdown():
    cfg = load_config()
    cfg.raw["storylines"] = {**cfg.raw.get("storylines", {}), "kyc_migration_backlog": False}
    assert onboarding.migration_factor(cfg, _dt.date(2026, 4, 15)) == 0.0
    on = load_config()
    assert onboarding.migration_factor(on, _dt.date(2026, 4, 15)) == 1.0
    assert onboarding.migration_factor(on, _dt.date(2025, 4, 15)) == 0.0
