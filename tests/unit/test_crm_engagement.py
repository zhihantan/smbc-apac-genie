"""CRM engagement & opportunity-signal tests (pure Python; brief §5.1, §5.2, §5.10; storylines 2, 5, 6, 10).

The engine normally reads behaviour aggregates collected by Spark (crm_engagement.load_features);
here `offline_features` stands in with deterministic flows hashed from the same truth builders
(accounts, facilities, FX / trade relationships, SCF programmes), so every rule is exercised.
"""
import datetime as _dt
from collections import defaultdict

import pytest

from smbc_genie_lib import (accounts, credit, crm_engagement as ce, fragment, fx, health, names, onboarding,
                            rng, storylines as sl, trade, truth)
from smbc_genie_lib.config import load_config

D = _dt.date
BRONZE = ["crm_contact", "crm_account_team_history", "crm_activity", "crm_signal", "crm_opportunity",
          "crm_account_plan", "crm_account_plan_initiative", "crm_wallet_estimate", "crm_nbp_score",
          "crm_next_best_product"]
KEYS = {"crm_contact": ("contact_id",), "crm_account_team_history": ("assignment_id",),
        "crm_activity": ("activity_id",), "crm_signal": ("signal_id",), "crm_opportunity": ("opportunity_id",),
        "crm_account_plan": ("plan_line_id",), "crm_account_plan_initiative": ("initiative_id",),
        "crm_wallet_estimate": ("wallet_id",), "crm_nbp_score": ("crm_account_id", "score_month", "product"),
        "crm_next_best_product": ("crm_account_id", "rank")}


def _nondup(xref, src):
    m = {}
    for x in xref:
        if x["source_system"] == src and not x["is_within_source_dup"]:
            m.setdefault(x["entity_id"], x["source_id"])
    return m


def offline_features(cfg, entities, xref):
    """Deterministic stand-in for load_features (same row shapes), flows hashed from truth."""
    seed = cfg.random_seed
    months = [D(2023 + (m + 3) // 12, (m + 3) % 12 + 1, 1) for m in range(42)]  # 2023-04 .. 2026-09
    hm = {(r["entity_id"], r["month"]): r["health"] for r in health.build_health_monthly(cfg, entities)}
    accts = accounts.build_accounts(cfg, entities, _nondup(xref, "core_customer"),
                                    onboarding.new_client_cohort(cfg, entities, xref))
    facs = credit.build_facilities(cfg, entities, _nondup(xref, "credit_obligor"))
    fxr = {r["entity_id"]: r for r in fx.build_fx_relationships(cfg, entities, _nondup(xref, "tsy_counterparty"))}
    trr = trade.build_trade_relationships(cfg, entities, _nondup(xref, "trade_party"))
    progs = trade.build_scf_programmes(cfg, trr)
    by_e = defaultdict(list)
    for a in accts:
        by_e[a["entity_id"]].append(a)
    fac_e = defaultdict(list)
    for f in facs:
        fac_e[f["entity_id"]].append(f)
    tr_e = {r["entity_id"]: r for r in trr}
    monthly, pob, fxp, trd, news = [], [], [], [], []
    ccs = ["CN", "JP", "KR", "VN", "IN", "US", "DE", "GB", "SG", "HK", "AU", "TH"]
    for e in entities:
        eid = e["entity_id"]
        if not (by_e[eid] or fac_e[eid] or eid in fxr or eid in tr_e):
            continue
        for i, m in enumerate(months):
            h = hm.get((eid, m), 0.6)
            season = 1.18 if m.month == 3 else (1.08 if m.month in (6, 9, 12) else 1.0)
            live = [a for a in by_e[eid] if a["active_from"] <= m]
            casa = sum(a["base_balance_usd"] for a in live if a["is_casa"]) * (0.4 + 0.8 * h) * season
            td = sum(a["base_balance_usd"] for a in live if not a["is_casa"]) * (0.4 + 0.8 * h)
            paying = bool(live) and m >= D(2024, 4, 1)
            n_pay = rng.randint(seed, 0, 6, "tpay", eid, i) if paying else 0
            avg = sum(a["base_balance_usd"] for a in live) * 0.03 if live else 0.0
            act = [f for f in fac_e[eid] if f["origination_date"] <= m <= f["maturity_date"]]
            drawn = sum(f["limit_usd"] * min(1.0, f["base_utilisation"] + (1 - h) * 0.4) for f in act)
            lim = sum(f["limit_usd"] for f in act)
            lend = sum(f["limit_usd"] * min(1.0, f["base_utilisation"]) * f["margin_bps"] / 1e4 / 12 for f in act)
            fxrev = (fxr[eid]["fx_annual_turnover_usd"] * fxr[eid]["smbc_wallet_share"] * 6e-4 / 12
                     if eid in fxr and m >= D(2024, 4, 1) else 0.0)
            tr = tr_e.get(eid)
            trade_usd = tr["trade_annual_turnover_usd"] / 120 if tr and m >= D(2024, 4, 1) else 0.0
            loan_n = 1 if paying and rng.unit(seed, "tloan", eid, i) < 0.06 else 0
            monthly.append({"entity_id": eid, "month": m, "casa_usd": casa, "td_usd": td, "n_pay": n_pay,
                            "xb_usd": avg * n_pay * 0.3, "supplier_out_usd": avg * n_pay * 0.4,
                            "loan_other_usd": avg * 2 * loan_n, "loan_other_n": loan_n, "drawn_usd": drawn,
                            "limit_usd": lim, "lend_rev_usd": lend, "fx_rev_usd": fxrev,
                            "trade_fee_usd": trade_usd * 0.004, "trade_usd": trade_usd})
            if paying and rng.unit(seed, "tpob", eid, i) < 0.35:
                cc = ccs[rng.hash64(seed, "tpobcc", eid, i % 3) % len(ccs)]
                pob.append({"entity_id": eid, "month": m, "counterparty_country": cc, "n": 2,
                            "usd": avg * rng.lognormal(seed, 0.0, 0.8, "tpobusd", eid, i)})
            if eid in fxr and m >= D(2024, 4, 1) and rng.unit(seed, "tfx", eid, i) < 0.6:
                fxp.append({"entity_id": eid, "month": m, "ccy_pair": fxr[eid]["primary_ccy_pair"], "n": 2,
                            "notional_usd": fxrev * 1e4})
            if tr and m >= D(2024, 4, 1):
                cc = tr["primary_corridor"]
                if cc == e["booking_country"]:
                    continue
                into_vn_in = e["booking_country"] in ("VN", "IN") or cc in ("VN", "IN")
                growth = 1.6 if into_vn_in and m >= D(2025, 10, 1) else 1.0
                trd.append({"entity_id": eid, "month": m, "counterparty_country": cc,
                            "direction": "Import" if rng.unit(seed, "tdir", eid) < 0.6 else "Export", "n": 2,
                            "usd": trade_usd * growth * (0.8 + 0.4 * rng.unit(seed, "ttrd", eid, i)),
                            "fee_usd": trade_usd * 0.004})
        if rng.unit(seed, "tnews", eid) < 0.12:
            news.append({"entity_id": eid, "news_id": f"NWS-T{eid[-5:]}", "published_date": "2026-03-16",
                         "topic": "Expansion" if rng.unit(seed, "tnt", eid) < 0.6 else "M&A",
                         "subtopic": "New Facility",
                         "raw_sentiment": 0.6, "relevance": 0.85,
                         "headline": f"{e['short_name']} to build USD 120m plant"})
    banksia_days = [D(2026, 4, 9), D(2026, 5, 6), D(2026, 5, 27), D(2026, 6, 17), D(2026, 7, 8), D(2026, 7, 29)]
    for k, d in enumerate(banksia_days):
        news.append({"entity_id": sl.lead_entity(entities, "banksia")["entity_id"], "news_id": f"NWS-B{k}",
                     "published_date": d.isoformat(), "topic": "Expansion", "subtopic": "Renewables Pipeline",
                     "raw_sentiment": 0.7, "relevance": 0.9,
                     "headline": "Banksia Renewables Partners adds solar pipeline"})
    fxw = [{"entity_id": eid, "fiscal_year": fy, "revenue_captured_usd": 1e4 * r["smbc_wallet_share"] * q,
            "revenue_opportunity_usd": 1e4 * (1 - r["smbc_wallet_share"]) * q, "quarters": q}
           for eid, r in fxr.items() for fy, q in ((2024, 4), (2025, 4), (2026, 2))]
    return {"monthly": monthly, "pay_other_bank": pob, "fx_pair": fxp, "trade": trd,
            "facility": facs, "account": accts, "fx_wallet": fxw, "health": [], "news": news,
            "scf": [{"programme_id": p["programme_id"], "anchor_entity_id": p["anchor_entity_id"],
                     "limit_usd": p["limit_usd"], "drawn_usd": p["drawn_usd"],
                     "launch_date": D(2025, 4, 1) if i % 2 else None} for i, p in enumerate(progs)]}


@pytest.fixture(scope="module")
def built():
    cfg = load_config()
    groups = truth.build_groups(cfg)
    entities = truth.build_entities(cfg, groups)
    _, xref = fragment.fragment_all(cfg, entities, [g["group_id"] for g in groups])
    feats = offline_features(cfg, entities, xref)
    out = ce.build_crm(cfg, groups, entities, truth.build_people(cfg), xref, feats)
    return cfg, groups, entities, xref, feats, out


def test_volumes_and_unique_keys(built):
    cfg, *_, out = built
    for t in BRONZE:
        assert out[t], t
        keys = [tuple(r[k] for k in KEYS[t]) for r in out[t]]
        assert len(set(keys)) == len(keys) and all(all(k) for k in keys), t
    assert len(out["crm_contact"]) == 6000
    assert len(out["crm_signal"]) == round(ce.SIGNALS_AT_SCALE_1 * cfg.scale)
    n_act = ce.ACTIVITIES_AT_SCALE_1 * cfg.scale
    assert abs(len(out["crm_activity"]) - n_act) <= 0.05 * n_act
    assert len(out["crm_account_plan_initiative"]) == round(ce.INITIATIVES_AT_SCALE_1 * cfg.scale)
    assert {s["signal_code"] for s in out["crm_signal"]} == set(ce.SIGNALS)


def test_bronze_carries_no_truth_ids(built):
    *_, out = built
    for t in BRONZE:
        cols = {k for r in out[t] for k in r if not k.startswith("_")}
        assert not cols & {"entity_id", "group_id"}, t
    assert all(r["entity_id"] for r in out["truth_crm_account"])


def test_referential_integrity(built):
    _, _, _, xref, _, out = built
    crm_ids = set(_nondup(xref, "crm_account").values())
    for t in BRONZE:
        assert {r["crm_account_id"] for r in out[t] if r.get("crm_account_id")} <= crm_ids, t
    sigs = {s["signal_id"]: s for s in out["crm_signal"]}
    opps = {o["opportunity_id"]: o for o in out["crm_opportunity"]}
    for o in opps.values():
        if o["source_signal_id"]:
            s = sigs[o["source_signal_id"]]
            assert s["linked_opportunity_id"] == o["opportunity_id"] and s["status"] == "Converted"
            assert s["crm_account_id"] == o["crm_account_id"] and o["created_date"] >= s["detected_date"]
            assert s["recommended_product"] == o["product"] and s["actioned_date"] <= o["created_date"]
    assert all(opps[s["linked_opportunity_id"]]["source_signal_id"] == sid
               for sid, s in sigs.items() if s["linked_opportunity_id"])
    contacts = {c["contact_id"]: c for c in out["crm_contact"]}
    for a in out["crm_activity"]:
        assert a["contact_id"] is None or contacts[a["contact_id"]]["crm_account_id"] == a["crm_account_id"]
        assert a["related_signal_id"] is None or a["related_signal_id"] in sigs
        assert a["related_opportunity_id"] is None or a["related_opportunity_id"] in opps
    plans = {p["plan_id"] for p in out["crm_account_plan"]}
    for i in out["crm_account_plan_initiative"]:
        assert i["plan_id"] in plans
        assert i["linked_opportunity_id"] is None or i["linked_opportunity_id"] in opps


def test_signal_lifecycle(built):
    cfg, *_, out = built
    as_of = cfg.as_of_date
    for s in out["crm_signal"]:
        assert s["status"] in ("New", "Actioned", "Converted", "Dismissed")
        assert ce.WINDOW_START.isoformat() <= s["detected_date"] <= as_of
        assert 0 < s["strength"] <= 1 and 0 < s["confidence"] <= 1 and s["estimated_revenue_usd"] >= 0
        assert (s["status"] == "Converted") == (s["linked_opportunity_id"] is not None)
        assert (s["status"] == "Dismissed") == (s["dismissed_reason"] is not None)
        if s["status"] == "New":
            assert s["actioned_date"] is None
        if s["actioned_date"]:
            assert s["detected_date"] < s["actioned_date"] <= as_of and s["actioned_by"].startswith("RM")
        if s["signal_code"] == "FX_FLOW_VIA_OTHER_BANK":
            assert s["currency_pair"] and s["observed_amount_usd"] > 0
        if s["signal_code"] == "TRADE_CORRIDOR_GROWTH":
            assert s["corridor_origin"] != s["corridor_destination"] and s["metric_value"] >= ce.TCG_MIN_GROWTH
    # an expired signal was never actioned; nothing organic stays open beyond the expiry window
    assert all(s["actioned_date"] is None for s in out["crm_signal"] if s["dismissed_reason"] == ce.EXPIRED_REASON)
    age = {s["signal_id"]: (D.fromisoformat(as_of) - D.fromisoformat(s["actioned_date"] or s["detected_date"])).days
           for s in out["crm_signal"] if s["status"] in ("New", "Actioned")}
    assert sum(a >= ce.EXPIRY_DAYS for a in age.values()) <= 1  # only Kinokawa's scripted recapture signal


def test_pipeline_states(built):
    cfg, *_, out = built
    as_of = cfg.as_of_date
    stages = dict(ce.OPP_STAGES)
    for o in out["crm_opportunity"]:
        assert o["win_probability"] == stages[o["stage"]] and o["created_date"] <= as_of
        assert o["product_family"] == ce.PRODUCTS[o["product"]][0] and o["amount_usd"] > 0
        if o["stage"] in ("Won", "Lost"):
            assert o["status"] == o["stage"] and o["created_date"] <= o["actual_close_date"] <= as_of
        else:
            assert o["status"] == "Open" and o["actual_close_date"] is None
        assert (o["lost_reason"] is not None) == (o["stage"] == "Lost")
    assert {o["status"] for o in out["crm_opportunity"]} == {"Open", "Won", "Lost"}


def test_activities_follow_team_history(built):
    *_, out = built
    periods = defaultdict(list)
    for t in out["crm_account_team_history"]:
        if t["team_role"] == "Primary RM":
            periods[t["crm_account_id"]].append((t["valid_from"], t["valid_to"] or "9999-12-31", t["rm_code"]))
            assert t["employee_id"] == ce.rm_employee_id(t["rm_code"])
    assert sum(len(v) > 1 for v in periods.values()) > 0.1 * len(periods)  # some RM changes
    for a in out["crm_activity"]:
        rm = [r for f, to, r in periods[a["crm_account_id"]] if f <= a["activity_date"] <= to]
        assert rm == [a["rm_code"]], a
        assert a["raw_tone"] in ce.TONE_SCORE and -1 <= a["sentiment_score"] <= 1
        assert (a["sentiment_score"] > 0) == (a["raw_tone"] == "Positive") or a["raw_tone"] == "Neutral"


def test_no_contact_in_90_days_is_exact(built):
    cfg, *_, out = built
    cutoff = (D.fromisoformat(cfg.as_of_date) - _dt.timedelta(days=90)).isoformat()
    last = {}
    for a in out["crm_activity"]:
        last[a["crm_account_id"]] = max(last.get(a["crm_account_id"], ""), a["activity_date"])
    for t in out["truth_crm_account"]:
        stale = last.get(t["crm_account_id"], "") < cutoff
        assert stale == t["is_neglected"], t
    strategic = [t for t in out["truth_crm_account"] if t["relationship_tier"] == "Strategic"]
    assert 0 < sum(t["is_neglected"] for t in strategic) < 0.15 * len(strategic)


def test_text_lengths_and_names(built):
    *_, out = built
    for a in out["crm_activity"]:
        assert 0 < len(a["note_text"].split()) <= 40, a["note_text"]
    for s in out["crm_signal"]:
        assert len(s["signal_detail"].split()) <= 40, s["signal_detail"]
    for i in out["crm_account_plan_initiative"]:
        assert len(i["description"].split()) <= 40
    for c in out["crm_contact"]:
        names.assert_clean(c["full_name"])
        assert c["email"].endswith(".example") and " " not in c["email"]
    prim = defaultdict(int)
    for c in out["crm_contact"]:
        prim[c["crm_account_id"]] += c["is_primary"]
    assert set(prim.values()) == {1}


def test_storylines_exact(built):
    cfg, _, entities, xref, _, out = built
    chk = ce.storyline_checks(cfg, entities, xref, out)
    assert chk["kinokawa_signals"] == sorted(sl.KINOKAWA_SCRIPT["signals"])
    assert chk["kinokawa_scf_opportunity"] == [("2026-08-12", 120_000_000.0, "SCF_ANCHOR_CANDIDATE")]
    assert chk["kinokawa_nbp_rank1"] == ["Supply Chain Finance"]
    assert chk["kinokawa_fy2026_revised"] == [True]
    assert chk["vn_in_tcg_opportunities"] == (14, 14, 10, 6)
    assert chk["banksia_green_in_pipeline"] == (2, 2)
    assert chk["banksia_sll_signals"] == 1
    assert len(chk["hk_casa_surplus"]) == 3
    assert all(m == "2026-05" and lag >= 40 for m, lag in chk["hk_casa_surplus"])


def test_account_plans(built):
    cfg, *_, out = built
    lo, hi = cfg.realism["plan_optimism_min"], cfg.realism["plan_optimism_max"]
    for p in out["crm_account_plan"]:
        if p["plan_optimism"] is not None:
            assert lo <= p["plan_optimism"] <= hi
            assert abs(p["planned_revenue_usd"] - p["prior_year_actual_usd"] * (1 + p["plan_optimism"])) < 0.02
        assert (p["revised_target_usd"] is not None) == p["mid_year_revision"] == (p["revision_date"] is not None)
        assert p["fiscal_year"] in ce.PLAN_FYS
    assert any(p["mid_year_revision"] for p in out["crm_account_plan"] if p["fiscal_year"] == 2026
               and p["group_ref"] != "SYN-G-0001")  # organic H1 revisions too


def test_revenue_feed_drives_plan_targets(built):
    """With bronze.fin_client_revenue present, plans sit on that feed (SCF stays programme-based)."""
    cfg, groups, entities, xref, feats, _ = built
    rev = [{"entity_id": r["entity_id"], "month": r["month"], "product_family": "Cash", "usd": r["casa_usd"] * 0.002}
           for r in feats["monthly"] if r["casa_usd"] and r["month"] >= D(2024, 4, 1)]
    out = ce.build_crm(cfg, groups, entities, truth.build_people(cfg), xref, {**feats, "revenue": rev})
    group_of = {e["entity_id"]: e["group_id"] for e in entities}
    fy25 = defaultdict(float)
    for r in rev:
        if D(2025, 4, 1) <= r["month"] <= D(2026, 3, 1):
            fy25[group_of[r["entity_id"]]] += r["usd"]
    lines = [p for p in out["crm_account_plan"] if p["fiscal_year"] == 2026 and p["product_family"] == "Cash"]
    assert lines and all(abs(p["prior_year_actual_usd"] - fy25[p["group_ref"]]) < 0.05 for p in lines)
    assert {p["product_family"] for p in out["crm_account_plan"]} <= {"Cash", "Supply Chain Finance"}
    assert all(p["actual_revenue_usd"] is None for p in out["crm_account_plan"] if p["fiscal_year"] == 2023)


def test_nbp_scores_and_top_picks(built):
    *_, out = built
    by = defaultdict(list)
    for s in out["crm_nbp_score"]:
        assert 0 < s["propensity"] < 1 and s["top_drivers"]
        by[(s["crm_account_id"], s["score_month"])].append(s)
    assert len({m for _, m in by}) == ce.NBP_MONTHS
    for rows in by.values():
        rows.sort(key=lambda r: r["rank"])
        assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1))
        assert all(a["propensity"] >= b["propensity"] for a, b in zip(rows, rows[1:]))
    latest = max(m for _, m in by)
    top = sorted((s["crm_account_id"], s["rank"], s["product"]) for s in out["crm_nbp_score"]
                 if s["score_month"] == latest and s["rank"] <= 3)
    assert top == sorted((n["crm_account_id"], n["rank"], n["recommended_product"])
                         for n in out["crm_next_best_product"])


def test_wallet_estimates(built):
    *_, out = built
    for r in out["crm_wallet_estimate"]:
        assert 0 <= r["share_of_wallet"] <= 1 and r["share_of_wallet"] + r["top_competitor_share"] <= 1.0001
        assert r["estimated_wallet_usd"] > 0 and r["fiscal_year"] in ce.WALLET_FYS
        assert r["top_competitor_bank"] in names.COMPETITOR_BANKS
    fams = {r["product_family"] for r in out["crm_wallet_estimate"]}
    assert fams == set(ce.FAMILIES)


def test_initiatives_open_in_q3_fy2026(built):
    *_, out = built
    q3 = [i for i in out["crm_account_plan_initiative"] if "2026-10-01" <= i["due_date"] <= "2026-12-31"
          and i["status"] in ce.OPEN_INITIATIVE]
    assert len(q3) >= 10 and all(i["owner_rm"].startswith("RM") for i in q3)
    assert all(i["completed_date"] for i in out["crm_account_plan_initiative"] if i["status"] == "Completed")


def test_deterministic(built):
    cfg, groups, entities, xref, feats, out = built
    again = ce.build_crm(cfg, groups, entities, truth.build_people(cfg), xref, feats)
    for t in BRONZE:
        strip = [{k: v for k, v in r.items() if not k.startswith("_")} for r in out[t]]
        assert [{k: v for k, v in r.items() if not k.startswith("_")} for r in again[t]] == strip, t
