"""Shared-lakehouse (simulated Delta Sharing) tests (pure Python; brief §3.2, storylines 4 and 13)."""
import datetime as _dt
import statistics
from collections import Counter, defaultdict

import pytest

from smbc_genie_lib import accounts, credit, fragment, names, onboarding, shared, storylines as S, truth
from smbc_genie_lib.config import load_config

D = _dt.date
SHARE_TABLES = [t for _, t in shared.TABLES]
MONTHLY = [t for t in SHARE_TABLES if t.endswith("_monthly")]


def _universe(cfg):
    groups = truth.build_groups(cfg)
    entities = truth.build_entities(cfg, groups)
    _, xref = fragment.fragment_all(cfg, entities, [g["group_id"] for g in groups])

    def nondup(src):
        m = {}
        for x in xref:
            if x["source_system"] == src and not x["is_within_source_dup"]:
                m.setdefault(x["entity_id"], x["source_id"])
        return m

    cohort = onboarding.new_client_cohort(cfg, entities, xref)
    facs = credit.build_facilities(cfg, entities, nondup("credit_obligor"))
    accts = accounts.build_accounts(cfg, entities, nondup("core_customer"), cohort)
    proxy = shared.apac_revenue_proxy(cfg, entities, accts, facs)
    since = {e["entity_id"]: onboarding.customer_since(cfg, e, cohort) for e in entities}
    return groups, entities, xref, facs, proxy, since


@pytest.fixture(scope="module")
def built():
    cfg = load_config()
    groups, entities, xref, facs, proxy, since = _universe(cfg)
    out = shared.build_shared(cfg, groups, entities, xref, facs, proxy, since)
    return cfg, groups, entities, xref, facs, proxy, since, out


def test_tables_and_volumes(built):
    cfg, groups, *_, out = built
    assert set(out) == set(SHARE_TABLES) | {"dq_share_refresh_log"}
    assert all(out[t] for t in out)
    assert len(out["share_jp_group_master"]) == len(groups) == 380
    assert len(out["share_emea_entity_master"]) + len(out["share_amer_entity_master"]) == 600
    days = (D.fromisoformat(cfg.as_of_date) - shared.LOG_START).days + 1
    assert len(out["dq_share_refresh_log"]) == len(SHARE_TABLES) * days


def test_no_truth_entity_ids(built):
    *_, out = built
    for t, rows in out.items():
        for r in rows:
            assert "entity_id" not in r and not any(isinstance(v, str) and v.startswith("SYN-E-") for v in r.values()), t


def test_sharing_columns(built):
    cfg, *_, out = built
    cutoff = shared._ts(shared._cutoff(cfg))
    for t in SHARE_TABLES:
        region = shared.REGION_OF[t]
        for r in out[t]:
            assert r["source_region"] == region and r["ingest_method"] == "delta_sharing"
            assert r["_share_name"] == shared.SHARE_NAME[region]
            assert r["_provider_version"] >= 1 and r["_shared_at"] <= cutoff
            assert not any(k.startswith("__") for k in r)


def test_group_master_and_jp_parents(built):
    _, groups, entities, *_, out = built
    master = {r["global_group_id"]: r for r in out["share_jp_group_master"]}
    assert set(master) == {g["group_id"] for g in groups}
    for g in groups:
        m = master[g["group_id"]]
        assert m["global_relationship_owner_region"] == g["global_relationship_owner_region"]
        if g["segment"] == "Japanese Corporate":
            assert m["jp_parent_id"] and m["jp_parent_legal_name"] and m["global_rm_code"].startswith("JPHO-RM-")
    others = [m for m in master.values() if m["jp_parent_id"] and m["global_segment"] != "Japanese Corporate"]
    assert 1 <= len(others) <= 20 and {m["global_segment"] for m in others} <= {"Financial Institutions", "Sponsor Coverage"}
    parent_names = [m["jp_parent_legal_name"] for m in master.values() if m["jp_parent_id"]]
    assert len(set(parent_names)) == len(parent_names)
    assert not set(parent_names) & {e["legal_name"] for e in entities}
    for n in parent_names + [m["global_rm_name"] for m in master.values() if m["global_rm_name"]]:
        names.assert_clean(n)
    assert {m["global_tier"] for m in master.values()} == {"Global Strategic", "Global Core", "Global Standard"}


def test_regional_entities(built):
    _, groups, entities, *_, out = built
    regional = out["share_emea_entity_master"] + out["share_amer_entity_master"]
    legal = [r["legal_name"] for r in regional]
    assert len(set(legal)) == len(legal) and not set(legal) & {e["legal_name"] for e in entities}
    for n in legal:
        names.assert_clean(n)
    assert len({r["provider_entity_id"] for r in regional}) == len(regional)
    key = {g["group_id"]: g["storyline_key"] for g in groups}
    assert not [r for r in regional if key[r["global_group_id"]] in shared.APAC_ONLY]
    hay = sorted((r["country_code"] for r in regional if key[r["global_group_id"]] == "hayashi"))
    assert hay == sorted(cc for _, cc, _ in shared.HAYASHI_FOOTPRINT)
    assert any(key[r["global_group_id"]] == "kinokawa" for r in regional)
    master = {m["global_group_id"]: m for m in out["share_jp_group_master"]}
    for region, t in (("emea", "share_emea_entity_master"), ("amer", "share_amer_entity_master")):
        active = Counter(r["global_group_id"] for r in out[t] if r["relationship_status"] == "Active")
        assert all(master[g][f"n_subsidiaries_{region}"] == active[g] for g in master)
    # every non-holding entity points at its region's holding company
    holding = {(r["global_group_id"], r["source_region"]): r["legal_name"] for r in regional
               if r["immediate_parent_type"] != "Regional Holding"}
    assert all(r["immediate_parent_name"] == holding[(r["global_group_id"], r["source_region"])]
               for r in regional if r["immediate_parent_type"] == "Regional Holding")


def test_monthly_integrity(built):
    cfg, *_, out = built
    as_of = D.fromisoformat(cfg.as_of_date)
    start = {r["provider_entity_id"]: r for r in out["share_emea_entity_master"] + out["share_amer_entity_master"]}
    for t in MONTHLY:
        keys = set()
        for r in out[t]:
            me = r["month_end_date"]
            assert D(2023, 4, 30) <= me <= as_of
            k = (r.get("provider_entity_id") or r["jp_counterparty_id"], me, r["product_family"])
            assert k not in keys
            keys.add(k)
            amounts = [v for c, v in r.items() if c.endswith("_usd")]
            assert all(v >= 0 for v in amounts)
            if "drawn_usd" in r:
                assert r["drawn_usd"] <= r["committed_usd"] + 0.01
            if "provider_entity_id" in r:
                e = start[r["provider_entity_id"]]
                assert me >= e["relationship_start_date"].replace(day=1)
                assert e["closed_date"] is None or me < e["closed_date"].replace(day=1)
    jp = {r["jp_counterparty_id"] for r in out["share_jp_parent_exposure_monthly"]}
    parents = {m["jp_parent_id"] for m in out["share_jp_group_master"] if m["jp_parent_id"]}
    assert parents <= jp and any(c.startswith("JPC-") for c in jp)


def test_hayashi_global_relationship(built):
    _, groups, *_, out = built
    gid = next(g["group_id"] for g in groups if g["storyline_key"] == "hayashi")
    me = D(2026, 9, 30)
    for t in ("share_jp_parent_exposure_monthly", "share_emea_exposure_monthly", "share_amer_exposure_monthly"):
        rows = [r for r in out[t] if r["global_group_id"] == gid and r["month_end_date"] == me]
        assert sum(r["committed_usd"] for r in rows) > 0 and sum(r["deposits_usd"] for r in rows) > 0, t
    for t in ("share_jp_parent_exposure_monthly", "share_emea_revenue_monthly", "share_amer_revenue_monthly"):
        assert sum(r["revenue_usd"] for r in out[t] if r["global_group_id"] == gid and r["month_end_date"] == me) > 0


def test_apac_share_of_group_revenue(built):
    cfg, groups, *_, proxy, _, out = built
    share = shared.apac_share_by_group(proxy, out, D.fromisoformat(cfg.as_of_date).replace(day=1))
    seg = {g["group_id"]: g["segment"] for g in groups}
    jc = [v for g, v in share.items() if seg[g] == "Japanese Corporate"]
    assert len(jc) == 190 and 0.20 <= statistics.median(jc) <= 0.40
    assert 30 <= sum(v < 0.20 for v in jc) <= 90           # brief 5.1 Q9 has a real answer
    njlc = [v for g, v in share.items() if seg[g] == "Non-Japanese Large Corporate"]
    assert statistics.median(njlc) > 0.6
    hay = next(g["group_id"] for g in groups if g["storyline_key"] == "hayashi")
    assert abs(share[hay] - shared.APAC_SHARE_SCRIPT["hayashi"]) < 0.05


def test_support_letters_match_facilities(built):
    cfg, groups, entities, xref, facs, *_, out = built
    as_of = D.fromisoformat(cfg.as_of_date)
    parented = {m["global_group_id"] for m in out["share_jp_group_master"] if m["jp_parent_id"]}
    grp = {e["entity_id"]: e["group_id"] for e in entities}
    letters = out["share_jp_support_letters"]
    pg = {f["facility_id"] for f in facs if f["guarantor_type"] == "Parent Guarantee" and grp[f["entity_id"]] in parented}
    assert Counter(r["covered_facility_id"] for r in letters if r["support_type"] == "Parent Guarantee") == Counter(pg)
    kw = {f["obligor_id"] for f in facs if f["guarantor_type"] == "Keepwell" and grp[f["entity_id"]] in parented}
    kw_rows = Counter(r["apac_obligor_ref"] for r in letters if r["support_type"] == "Keepwell")
    assert set(kw_rows) >= kw and max(kw_rows.values()) == 1
    assert {r["global_group_id"] for r in letters} <= parented
    for r in letters:
        if r["status"] == "Active":
            assert r["expiry_date"] is None or r["expiry_date"] >= as_of
        else:
            assert r["status_date"] is not None and r["status_date"] <= as_of
        assert (r["covered_facility_id"] is None) == (r["support_type"] == "Keepwell")
    assert len({r["letter_id"] for r in letters}) == len(letters)


def _tanaka(out, entities, xref):
    lead = S.lead_entity(entities, "tanaka")
    obligor = next(x["source_id"] for x in xref if x["entity_id"] == lead["entity_id"]
                   and x["source_system"] == "credit_obligor" and not x["is_within_source_dup"])
    return lead, [r for r in out["share_jp_support_letters"] if r["apac_obligor_ref"] == obligor
                  and r["support_type"] == "Keepwell"]


def test_tanaka_keepwell_and_parent_upgrade(built):
    _, groups, entities, xref, *_, out = built
    lead, kw = _tanaka(out, entities, xref)
    assert lead["booking_country"] == "SG" and len(kw) == 1
    assert kw[0]["status"] == "Active" and kw[0]["coverage_scope"] == "General" and kw[0]["subsidiary_country"] == "SG"
    ts = S.TANAKA_SCRIPT
    ratings = sorted((r for r in out["share_jp_parent_rating"] if r["global_group_id"] == lead["group_id"]),
                     key=lambda r: r["rating_date"])
    up = [r for r in ratings if r["rating_action"] == "Upgrade"]
    assert len(up) == 1 and up[0]["rating_date"] == ts["parent_upgrade_date"] and up[0]["is_current"]
    assert (up[0]["grade_from"], up[0]["grade_to"]) == (ts["parent_grade_from"], ts["parent_grade_to"])
    assert all(r["grade_to"] == ts["parent_grade_from"] for r in ratings if r["rating_date"] < ts["parent_upgrade_date"])
    assert kw[0]["last_confirmed_date"] > ts["parent_upgrade_date"]


def test_tanaka_keepwell_with_scripted_rcf():
    """Once the integration step adds Tanaka's keepwell-backed RCF, the same keepwell covers it."""
    cfg = load_config()
    groups, entities, xref, facs, proxy, since = _universe(cfg)
    lead = S.lead_entity(entities, "tanaka")
    obligor = next(f["obligor_id"] for f in facs if f["entity_id"] == lead["entity_id"])
    rcf = {**facs[-1], "facility_id": "FAC-999999", "entity_id": lead["entity_id"], "obligor_id": obligor,
           "facility_type": "Revolving Credit Facility", "guarantor_type": "Keepwell",
           "origination_date": D(2024, 6, 14), "maturity_date": D(2027, 6, 14), "closed_date": None}
    out = shared.build_shared(cfg, groups, entities, xref, facs + [rcf], proxy, since)
    _, kw = _tanaka(out, entities, xref)
    assert len(kw) == 1 and kw[0]["status"] == "Active" and kw[0]["issue_date"] == shared.TANAKA_KEEPWELL_ISSUED


def test_rating_history_consistent(built):
    _, groups, *_, out = built
    from smbc_genie_lib.truth import _external_rating
    assert shared.RATING_SCALE == [_external_rating(g) for g in range(1, 11)]
    gby = {g["group_id"]: g for g in groups}
    by_parent = defaultdict(list)
    for r in out["share_jp_parent_rating"]:
        by_parent[r["jp_parent_id"]].append(r)
        assert len(r["rating_reason"].split()) <= 40 and r["rating_equivalent"] == shared.RATING_SCALE[r["grade_to"] - 1]
    for rows in by_parent.values():
        rows.sort(key=lambda r: r["rating_date"])
        assert rows[0]["rating_action"] == "Initial" and sum(r["is_current"] for r in rows) == 1 and rows[-1]["is_current"]
        for a, b in zip(rows, rows[1:]):
            assert b["grade_from"] == a["grade_to"] and a["valid_to"] == b["rating_date"]
            assert b["rating_action"] == ("Affirm" if b["grade_to"] == b["grade_from"] else
                                          "Upgrade" if b["grade_to"] < b["grade_from"] else "Downgrade")
        g = gby[rows[0]["global_group_id"]]
        if g["storyline_key"] != "tanaka":
            assert rows[-1]["grade_to"] == g["group_internal_grade"]
    recent = [r for r in out["share_jp_parent_rating"] if r["rating_date"] >= D(2025, 1, 1)]
    down = Counter(gby[r["global_group_id"]]["industry_subsector"] for r in recent if r["rating_action"] == "Downgrade")
    up = Counter(gby[r["global_group_id"]]["industry_subsector"] for r in recent if r["rating_action"] == "Upgrade")
    assert down.most_common(1)[0][0] == "Shipping" and up["Semiconductors"] + up["Electronic Devices"] > down["Semiconductors"]
    assert any(r["review_type"] == "Event-Driven" for r in recent)


def test_jp_share_staleness(built):
    cfg, *_, out = built
    log, table = out["dq_share_refresh_log"], S.SHARE_STALE["table"]
    a, b = S.SHARE_STALE["gap"]
    gap = [(a + _dt.timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]
    assert shared.stale_days(log, table) == gap                     # exactly the five outage mornings
    assert not [t for _, t in shared.TABLES if t.startswith("share_jp") and t != table and shared.stale_days(log, t)]
    rows = {r["log_date"]: r for r in log if r["table_name"] == table}
    assert all(rows[d]["refresh_status"] == "Failed" and rows[d]["lag_hours"] > 24 for d in gap)
    assert len({rows[d]["provider_version"] for d in gap}) == 1 and rows[gap[-1]]["lag_hours"] > 96
    nxt = (b + _dt.timedelta(days=1)).isoformat()
    assert rows[nxt]["lag_hours"] < 24 and rows[nxt]["provider_version"] == rows[gap[0]]["provider_version"] + 1
    ratings = out[table]
    assert not [r for r in ratings if gap[0] <= r["_shared_at"][:10] <= gap[-1]]   # the rows show the gap
    held = [r for r in ratings if a - _dt.timedelta(days=1) <= r["rating_date"] <= b]
    assert held and all(r["_shared_at"][:10] == nxt for r in held)


def test_staleness_switch_off():
    cfg = load_config()
    cfg.raw["storylines"] = {**cfg.raw["storylines"], S.SHARE_STALENESS: False}
    runs = shared.refresh_runs(cfg, S.SHARE_STALE["table"])
    assert not [r for r in runs if r["status"] == "Failed"]


def test_refresh_log_consistent(built):
    cfg, *_, out = built
    log = out["dq_share_refresh_log"]
    by_table = defaultdict(list)
    for r in log:
        by_table[r["table_name"]].append(r)
        assert r["lag_hours"] >= 0 and r["is_stale"] == (r["lag_hours"] > 24)
        assert (r["error_message"] is not None) == (r["refresh_status"] == "Failed")
        assert len((r["error_message"] or "").split()) <= 40
    for t, rows in by_table.items():
        rows.sort(key=lambda r: r["log_date"])
        assert all(a["provider_version"] <= b["provider_version"] for a, b in zip(rows, rows[1:]))
        assert all(a["row_count"] <= b["row_count"] for a, b in zip(rows, rows[1:])) or t.endswith("_master")
        assert rows[-1]["row_count"] == len(out[t])
        versions = {(r["provider_version"], r["refreshed_at"]) for r in rows}
        assert {(r["_provider_version"], r["_shared_at"]) for r in out[t]} <= versions | {
            (rr["version"], shared._ts(rr["published_at"])) for rr in shared.refresh_runs(cfg, t) if rr["version"]}
    assert sum(r["schema_drift_flag"] for r in log) == len(shared.SCHEMA_DRIFT)
    stale = [r for r in log if r["is_stale"]]
    assert len(stale) == 5 + shared.INCIDENTS
    assert Counter(r["refresh_status"] for r in log)["Late"] > 0


def test_schema_drift_columns(built):
    *_, out = built
    for t, (day, col, _) in shared.SCHEMA_DRIFT.items():
        rows = out[t]
        filled = [r for r in rows if r[col] is not None]
        assert filled and len(filled) < len(rows)
        assert all(r["_shared_at"][:10] >= day.isoformat() for r in filled)


def test_parent_financials_consistent(built):
    cfg, *_, out = built
    fx = shared.fx_rates(cfg)
    for r in out["share_jp_parent_financials"]:
        assert abs(r["net_debt_usd"] - (r["total_debt_usd"] - r["cash_usd"])) < 1.0
        assert abs(r["net_debt_to_ebitda"] - r["net_debt_usd"] / r["ebitda_usd"]) < 0.01
        assert r["equity_usd"] + r["total_debt_usd"] <= r["total_assets_usd"] + 1.0
        rate = shared._rate(cfg, fx, "JPY", r["fiscal_year_end"])
        assert abs(r["revenue_jpy"] - r["revenue_usd"] * rate) < 2.0   # JPY whole units, USD cents
        assert r["results_published_date"].year == r["fiscal_year"] + 1
    assert {r["fiscal_year"] for r in out["share_jp_parent_financials"]} == set(shared.FIN_YEARS)


def test_deterministic(built):
    cfg, groups, entities, xref, facs, proxy, since, out = built
    again = shared.build_shared(cfg, groups, entities, xref, facs, proxy, since)
    for t in ("share_jp_parent_rating", "share_jp_support_letters", "share_amer_entity_master",
              "share_emea_exposure_monthly", "dq_share_refresh_log"):
        assert again[t] == out[t], t
