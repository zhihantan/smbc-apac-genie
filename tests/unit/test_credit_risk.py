"""Credit & risk lifecycle tests (pure Python; storylines 1, 2, 5, 9; reconciliation to profitability).

The fixture lands synthetic stand-ins for the Spark-built feeds (month-end balances, deposits,
payments, cash flows, forecasts) from the pure-Python truth, and builds fin_relationship_pnl rows
with profitability.relationship_pnl itself so the exact reconciliation is tested end to end.
"""
import datetime as _dt
import hashlib
import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import pytest

from smbc_genie_lib import (credit, credit_risk as cr, financials, fiscal, fragment, health, profitability as prof,
                            reference, rng, storyline_injectors, storylines as sl, truth)
from smbc_genie_lib.config import load_config

D, TD = _dt.date, _dt.timedelta
AS_OF = D(2026, 9, 30)
BRONZE = ["core_rating_history", "core_dpd", "core_loan_schedule", "credit_review", "credit_spreading_task",
          "fin_capital_allocation", "fin_cost_allocation", "fin_client_revenue", "cf_event"]
LINES = {"Corporate Lending": "lend", "Cash": "dep", "Liquidity": "dep", "Payments": "pay"}  # P&L revenue lines


def _month_starts(a, b):
    out, m = [], a.replace(day=1)
    while m <= b:
        out.append(m)
        m = (m + TD(days=32)).replace(day=1)
    return out


def _inputs(cfg, entities, xref):
    seed = cfg.random_seed
    maps = cr.id_maps(xref)
    obl, cust = maps["credit_obligor"], maps["core_customer"]
    hm = health.build_health_monthly(cfg, entities)
    hser = defaultdict(dict)
    for r in hm:
        hser[r["entity_id"]][r["month"]] = r["health"]
    facs = credit.build_facilities(cfg, entities, obl)
    pins = storyline_injectors.utilisation_overrides(cfg, entities, facs)
    balances = []
    for f in facs:  # the Spark utilisation formula (+ storyline pins / prepaid cut-off), Python hash noise
        end = min(f["closed_date"] or f["maturity_date"], D(2027, 3, 31))
        for m in _month_starts(max(f["origination_date"], D(2023, 4, 1)), end):
            u = rng.unit(seed, "testutil", f["facility_id"], m.isoformat())
            util = pins.get((f["facility_id"], cr._me(m)),
                            min(1.08, max(0.05, f["base_utilisation"] + (1 - hser[f["entity_id"]][m]) * 0.40 + (u - 0.5) * 0.08)))
            balances.append({"facility_id": f["facility_id"], "balance_date": cr._me(m),
                             "limit_usd": f["limit_usd"], "drawn_usd": round(f["limit_usd"] * util, 2)})
    hlut = {(r["entity_id"], r["month"]): r["health"] for r in hm}
    lines, ratios = financials.build_statements(cfg, entities, hlut, obl)
    stmts = {}
    for ln in lines:
        stmts[(ln["obligor_id"], ln["fiscal_year"])] = {k: ln[k] for k in ("is_spread", "spread_date", "is_audited")}
        stmts[(ln["obligor_id"], ln["fiscal_year"])]["fye"] = ln["fiscal_year_end"]
    rat = defaultdict(dict)
    for r in ratios:
        if r["ratio_name"] in ("Net Debt/EBITDA", "ICR"):
            rat[(r["obligor_id"], r["fiscal_year"])][r["ratio_name"]] = r["ratio_value"]
    months = _month_starts(D(2023, 4, 1), AS_OF)
    deposits, payments, actuals, forecasts = {}, {}, {}, []
    for eid, c in cust.items():
        base = rng.lognormal(seed, 15.5, 1.0, "tdep", c)
        flow = rng.lognormal(seed, 13.0, 1.2, "tflow", c)
        for m in months:
            k = (c, m.isoformat())
            deposits[(c, m)] = (base * (0.6 + 0.8 * rng.unit(seed, "tcasa", *k)),
                                base * 0.5 * rng.unit(seed, "ttd", *k))
            if m >= D(2024, 4, 1):
                payments[(c, m)] = (rng.randint(seed, 0, 6, "tnpay", *k), flow * rng.unit(seed, "txb", *k))
                actuals[(c, m)] = (flow * rng.lognormal(seed, 0.0, 0.7, "tin", *k),
                                   flow * rng.lognormal(seed, 0.0, 0.7, "tout", *k))
                if m >= D(2024, 5, 1):
                    fc = actuals[(c, m)][0] * (1 + 0.4 * (rng.unit(seed, "tfc", *k) - 0.5))
                    forecasts.append({"cust_no": c, "forecast_run_date": (m - TD(days=1)).replace(day=1),
                                      "target_month": m, "model_version": "v2" if m >= D(2026, 6, 1) else "v1",
                                      "forecast_inflows_usd": fc})
    n_acct = {c: 1 + rng.hash64(seed, "tnacct", c) % 5 for c in cust.values()}
    inp = {"terms": facs, "balances": balances, "health": dict(hser), "statements": stmts, "ratios": dict(rat),
           "watchlist": [{"obligor_id": o, "band": "Amber", "months_on_watch": 4}
                         for o in sorted(obl.values())[100:110]],
           "waivers": [], "deal_pricing": {f["facility_id"]: {"meets_standalone_hurdle": f["margin_bps"] > 120,
                                                              "exception_reason": None} for f in facs},
           "deposits": deposits, "payments": payments, "n_accounts": n_acct,
           "fx_revenue": {(c, m): (5000.0, 1e6) for e, c in maps["tsy_counterparty"].items() for m in months[12::5]},
           "trade_fees": {(p, m): (8000.0, 2e6) for e, p in maps["trade_party"].items() for m in months[13::4]},
           "forecasts": forecasts, "actuals": actuals,
           "fx": {(r["currency_code"], r["date"]): r["rate_per_usd"] for r in reference.build_fx_daily(cfg)}}
    inp["pnl"] = _pnl(cfg, entities, maps, facs, inp, hser)
    return inp


def _pnl(cfg, entities, maps, facs, inp, hser):
    """fin_relationship_pnl rows as run_bronze_profitability builds them (quarterly averages)."""
    ents = {e["entity_id"]: e for e in entities}
    cust = maps["core_customer"]
    fac_m = defaultdict(lambda: [0.0, 0.0, 0.0])
    by_id = {f["facility_id"]: f for f in facs}
    for b in inp["balances"]:
        m = b["balance_date"].replace(day=1)
        if D(2024, 4, 1) <= m <= AS_OF:
            f = by_id[b["facility_id"]]
            x = fac_m[(f["obligor_id"], f["entity_id"], m)]
            x[0] += b["drawn_usd"]
            x[1] += b["limit_usd"]
            x[2] += b["drawn_usd"] * f["margin_bps"]
    q = defaultdict(list)
    for (o, e, m), (dr, lim, dm) in fac_m.items():
        q[(o, e, fiscal.fiscal_year(m), fiscal.fiscal_quarter(m))].append((m, dr, lim, dm / dr if dr else 0.0))
    rows = []
    for (o, e, fy, fq), ms in q.items():
        months = [m for m in _month_starts(D(2024, 4, 1), AS_OF)
                  if (fiscal.fiscal_year(m), fiscal.fiscal_quarter(m)) == (fy, fq)]
        c = cust.get(e)
        dep = [inp["deposits"][(c, m)] for m in months if c and (c, m) in inp["deposits"]]
        pay = [inp["payments"][(c, m)] for m in months if c and (c, m) in inp["payments"]]
        bal = sum(a + b for a, b in dep)
        agg = {"avg_drawn": sum(x[1] for x in ms) / len(ms), "avg_limit": sum(x[2] for x in ms) / len(ms),
               "wgt_margin_bps": sum(x[3] for x in ms) / len(ms), "lgd_blended": 0.45,
               "avg_dep_bal": bal / len(dep) if dep else 0.0,
               "casa_share": sum(a for a, _ in dep) / bal if bal else 0.0,
               "n_pay": sum(n for n, _ in pay), "xb_amt": sum(x for _, x in pay), "n_acct": inp["n_accounts"].get(c, 0),
               "relationship_tier": ents[e]["relationship_tier"],
               "grade_avg": sum(cr.implied_grade(hser[e][m]) for m, *_ in ms) / len(ms)}
        p = prof.relationship_pnl(cfg, agg)
        rows.append({"obligor_id": o, "fiscal_year": fy, "fiscal_quarter": fq, "rev_lending": p["rev_lending"],
                     "rev_deposits": p["rev_deposits"], "rev_payments": p["rev_payments"],
                     "cost_to_serve": p["cost_to_serve"]})
    return rows


@pytest.fixture(scope="module")
def built():
    cfg = load_config()
    groups = truth.build_groups(cfg)
    entities = truth.build_entities(cfg, groups)
    _, xref = fragment.fragment_all(cfg, entities, [g["group_id"] for g in groups])
    inp = _inputs(cfg, entities, xref)
    out = cr.build_credit_risk(cfg, entities, xref, truth.build_people(cfg), inp)
    return cfg, entities, xref, inp, out


def _sunda(entities, xref):
    return cr.id_maps(xref)["credit_obligor"][sl.lead_entity(entities, sl.SUNDA["key"])["entity_id"]]


def test_stage_rules():
    assert cr.ifrs9_stage(4, 4) == 1
    assert cr.ifrs9_stage(7, 4) == 2 and cr.ifrs9_stage(6, 4) == 1      # 3 notches below origination
    assert cr.ifrs9_stage(8, 8) == 2                                    # grade >= 8
    assert cr.ifrs9_stage(3, 3, dpd=31) == 2 and cr.ifrs9_stage(3, 3, dpd=30) == 1
    assert cr.ifrs9_stage(3, 3, dpd=91) == 3 and cr.ifrs9_stage(3, 3, impaired=True) == 3
    assert cr.dpd_bucket(0) == "Current" and cr.dpd_bucket(30) == "30-59" and cr.dpd_bucket(91) == "90+"
    assert cr.rating_equivalent(1) == "AAA" and cr.rating_equivalent(7) == truth._external_rating(7)


def test_bronze_carries_no_truth_ids(built):
    *_, out = built
    for t in BRONZE:
        assert out[t], t
        assert all("entity_id" not in r and "group_id" not in r for r in out[t]), t
    assert all(r["entity_id"] for r in out["truth_arrears_episode"])


def test_keys_are_unique(built):
    *_, out = built
    for t, key in [("core_rating_history", ("rating_event_id",)), ("core_loan_schedule", ("schedule_event_id",)),
                   ("credit_review", ("review_id",)), ("credit_spreading_task", ("task_id",)),
                   ("cf_event", ("event_id",)),
                   ("core_dpd", ("facility_id", "dpd_date")), ("fin_capital_allocation", ("obligor_id", "month")),
                   ("fin_client_revenue", ("client_source_system", "client_source_id", "month", "product_family")),
                   ("fin_cost_allocation", ("client_source_system", "client_source_id", "month", "product_family"))]:
        keys = [tuple(r[k] for k in key) for r in out[t]]
        assert len(keys) == len(set(keys)), t


def test_rating_history_is_sticky_and_consistent(built):
    *_, out = built
    by = defaultdict(list)
    for r in out["core_rating_history"]:
        by[r["obligor_id"]].append(r)
    changes = 0
    for rows in by.values():
        rows.sort(key=lambda r: r["effective_date"])
        assert rows[0]["rating_action"] == "Initial" and rows[0]["previous_grade"] is None
        for a, b in zip(rows, rows[1:]):
            assert b["previous_grade"] == a["internal_grade"]
            assert b["notch_change"] == b["internal_grade"] - b["previous_grade"]
            want = "Downgrade" if b["notch_change"] > 0 else "Upgrade" if b["notch_change"] < 0 else "Affirm"
            assert b["rating_action"] == want
            changes += b["notch_change"] != 0
        for r in rows:
            g = r["internal_grade"]
            assert 1 <= g <= 10 and r["rating_equivalent"] == cr.rating_equivalent(g)
            if r["ifrs9_stage"] == 1:
                assert r["internal_grade"] < 8 and r["internal_grade"] - r["origination_grade"] < 3
    assert changes / len(by) < 1.0      # ~0.4 rating changes per obligor over 3.5 years, not monthly noise
    assert sum(r["action_reason"] == "Annual review" for r in out["core_rating_history"]) > len(by)


def test_arrears_and_defaults(built):
    cfg, entities, xref, _, out = built
    eps = out["truth_arrears_episode"]
    assert 150 <= len({e["facility_id"] for e in eps}) <= 320
    defaults = [e for e in eps if e["is_default"] and not e["storyline_key"]]
    assert len(defaults) == cr.N_DEFAULTS
    by_ep = defaultdict(list)
    for r in out["core_dpd"]:
        by_ep[r["arrears_episode_id"]].append(r)
    for ep in eps:
        rows = sorted(by_ep[ep["episode_id"]], key=lambda r: r["dpd_date"])
        assert [r["days_past_due"] for r in rows] == list(range(1, len(rows) + 1))
        if ep["is_default"]:
            assert rows[-1]["dpd_date"] == AS_OF.isoformat() and rows[-1]["days_past_due"] > 90
        else:
            assert rows[-1]["days_past_due"] <= 89
            # a cure after the as-of date is not known yet: still in arrears, no cure date
            cured = ep["cure_date"] is not None and ep["cure_date"] <= AS_OF
            assert bool(rows[0]["cure_date"]) == cured and rows[0]["is_cured"] == cured
    # nobody but Sunda carries its signature (a 30+ DPD episode falling due in Mar-Apr 2026)
    sig = {r["arrears_episode_id"] for r in out["core_dpd"] if r["days_past_due"] >= 30
           and "2026-03-01" <= r["due_date"] <= "2026-04-30"}
    assert {e["storyline_key"] for e in eps if e["episode_id"] in sig} == {"sunda"}
    stage3 = cr.stage3_obligors(out, "2026-09-01")
    assert _sunda(entities, xref) in stage3 and len(stage3) <= cr.N_DEFAULTS + 2


def test_sunda_cascade(built):
    cfg, entities, xref, _, out = built
    obl = _sunda(entities, xref)
    ep = next(e for e in out["truth_arrears_episode"] if e["kind"] == "Storyline")
    dpd = cr.sunda_dpd(out, ep["facility_id"], [D(2026, 4, 15), D(2026, 4, 30), D(2026, 6, 30)])
    assert dpd == {"2026-04-15": 15, "2026-04-30": 30, "2026-06-30": 91} and ep["cure_date"] is None
    rows = [r for r in out["core_rating_history"] if r["obligor_id"] == obl]
    dg = [r for r in rows if r["effective_date"] == sl.SUNDA["downgrade_date"].isoformat()]
    assert len(dg) == 1 and (dg[0]["previous_grade"], dg[0]["internal_grade"], dg[0]["ifrs9_stage"]) == (7, 9, 3)
    assert all(r["internal_grade"] == 7 for r in rows if r["effective_date"] < "2026-06-12")
    rv = [r for r in out["credit_review"] if r["obligor_id"] == obl and r["review_type"] == "Annual"
          and r["review_fiscal_year"] == 2025]
    assert len(rv) == 1 and rv[0]["days_late"] == sl.SUNDA["review_days_late"] and rv[0]["due_date"] == "2026-05-29"
    cap = {r["month"]: r for r in out["fin_capital_allocation"] if r["obligor_id"] == obl}
    assert cap["2026-06-01"]["ifrs9_stage"] == 3 and cap["2026-06-01"]["is_individually_assessed"]
    assert cap["2026-05-01"]["ifrs9_stage"] == 2 and cap["2026-04-01"]["ifrs9_stage"] == 1
    assert cap["2026-06-01"]["ecl_usd"] > 10 * cap["2026-05-01"]["ecl_usd"]
    # no other obligor is downgraded 7 -> 9 to Stage 3 on the Sunda date
    others = [r for r in out["core_rating_history"] if r["effective_date"] == "2026-06-12" and r["obligor_id"] != obl
              and r["ifrs9_stage"] == 3]
    assert not others


def test_loan_schedule_rebuilds_month_end_balances(built):
    cfg, entities, xref, inp, out = built
    terms = {t["facility_id"]: t for t in inp["terms"]}
    ev = defaultdict(list)
    for r in out["core_loan_schedule"]:
        assert r["amount_usd"] >= 0 and r["event_type"] in ("Drawdown", "Repayment", "Prepayment", "Maturity")
        if r["status"] == "Settled":
            sign = 1 if r["event_type"] == "Drawdown" else -1
            ev[r["facility_id"]].append((r["event_date"], sign * r["amount_usd"]))
        else:
            assert r["event_type"] == "Maturity" and r["event_date"] > AS_OF.isoformat()
    bal = defaultdict(list)
    for b in inp["balances"]:
        if b["balance_date"] <= AS_OF:
            bal[b["facility_id"]].append((b["balance_date"], b["drawn_usd"]))
    checked = 0
    for fid, rows in bal.items():
        t = terms[fid]
        rows.sort()
        opening = rows[0][1] if t["origination_date"] < cr.HISTORY_START else 0.0
        for d, drawn in rows:
            if (d.year, d.month) < (t["maturity_date"].year, t["maturity_date"].month):
                rebuilt = opening + sum(a for e, a in ev[fid] if e <= d.isoformat())
                assert abs(rebuilt - drawn) < 0.05, (fid, d)
                checked += 1
    assert checked > 20000


def test_kinokawa_prepayment(built):
    cfg, entities, xref, _, out = built
    pp = [r for r in out["core_loan_schedule"] if r["purpose"] == "Refinanced with another bank"]
    assert len(pp) == 1
    r = pp[0]
    lead = sl.lead_entity(entities, sl.KINOKAWA_SCRIPT["key"])
    assert r["event_type"] == "Prepayment" and r["event_date"] == "2026-02-16" and r["amount_usd"] == 400_000_000
    assert r["obligor_id"] == cr.id_maps(xref)["credit_obligor"][lead["entity_id"]]
    assert out["_meta"]["kinokawa_facility"] == r["facility_id"]


def test_cashflow_upgrade_storyline(built):
    cfg, entities, xref, inp, out = built
    assert cr.shortfalls_followed(out) == (sl.CASHFLOW["predicted_shortfalls"], sl.CASHFLOW["rcf_drawdowns_within_10d"])
    sched = {r["schedule_event_id"]: r for r in out["core_loan_schedule"]}
    rcf = {t["facility_id"] for t in inp["terms"] if t["facility_type"] == "Revolving Credit Facility"}
    for e in out["cf_event"]:
        assert e["event_type"] in ("Predicted Shortfall", "Predicted Surplus")
        assert (e["predicted_net_usd"] < 0) == (e["event_type"] == "Predicted Shortfall")
        if e["action_taken"] == "RCF Drawdown":
            s = sched[e["linked_schedule_event_id"]]
            assert s["event_type"] == "Drawdown" and s["facility_id"] in rcf
            assert s["facility_id"] == e["linked_facility_id"]
            assert 0 <= e["days_to_action"] <= cr.ACTION_WINDOW_DAYS and s["event_date"] == e["action_date"]


def test_hk_casa_surpluses_flagged_in_may(built):
    *_, out = built
    hk = [e for e in out["cf_event"] if e["event_type"] == "Predicted Surplus" and e["event_date"][:7] == "2026-05"
          and e["predicted_amount_usd"] > 2e8]
    assert len(hk) == 3 and abs(sum(e["predicted_amount_usd"] for e in hk) - sl.HK_CASA_SCRIPT["moved_usd"]) < 1
    for e in hk:
        assert e["action_taken"] == "TD Placed" and e["days_to_action"] >= 40
        assert "2026-06" <= e["action_date"][:7] <= "2026-08"


def test_revenue_and_cost_reconcile_to_relationship_pnl(built):
    *_, inp, out = built
    agg = defaultdict(float)
    for r in out["fin_client_revenue"]:
        assert abs(r["nii_usd"] + r["fee_usd"] + r["trading_usd"] - r["total_revenue_usd"]) < 0.011
        if r["client_source_system"] == "credit_obligor":
            line = LINES.get(r["product_family"])
            if line:
                agg[(r["client_source_id"], r["fiscal_year"], r["fiscal_quarter"], line)] += r["total_revenue_usd"]
    for r in out["fin_cost_allocation"]:
        parts = r["direct_cost_usd"] + r["rm_cost_usd"] + r["operations_cost_usd"] + r["ho_allocation_usd"]
        assert abs(parts - r["total_cost_usd"]) < 0.011 and min(r["rm_cost_usd"], r["operations_cost_usd"]) >= 0
        if r["client_source_system"] == "credit_obligor":
            agg[(r["client_source_id"], r["fiscal_year"], r["fiscal_quarter"], "cost")] += r["total_cost_usd"]
    for p in inp["pnl"]:
        k = (p["obligor_id"], p["fiscal_year"], p["fiscal_quarter"])
        for line, target in (("lend", p["rev_lending"]), ("dep", p["rev_deposits"]), ("pay", p["rev_payments"]),
                             ("cost", p["cost_to_serve"])):
            assert abs(agg[k + (line,)] - target) < 0.011, (k, line)
    fams = {r["product_family"] for r in out["fin_client_revenue"]}
    assert fams == set(cr.FAMILY_LINE)


def test_capital_formulas(built):
    *_, out = built
    for r in out["fin_capital_allocation"]:
        undrawn = max(0.0, r["limit_usd"] - r["drawn_usd"])
        assert abs(r["ead_usd"] - (r["drawn_usd"] + prof.UNDRAWN_CCF * undrawn)) < 0.02
        assert abs(r["rwa_usd"] - r["ead_usd"] * prof.risk_weight(r["internal_grade"])) < 0.05
        if r["is_individually_assessed"]:
            continue
        booked = {1: r["ecl_12m_usd"], 2: r["ecl_lifetime_usd"], 3: r["ead_usd"] * r["lgd"]}[r["ifrs9_stage"]]
        assert abs(r["ecl_usd"] - booked) < 0.05 + 1e-6 * r["ead_usd"]   # lgd is stored to 6 dp
        assert r["ecl_12m_usd"] <= r["ecl_lifetime_usd"] + 0.01
        assert (r["pd_12m"] == 1.0) == (r["ifrs9_stage"] == 3)


def test_spreading_tasks_match_statements(built):
    *_, inp, out = built
    tasks = out["credit_spreading_task"]
    assert len(tasks) == len(inp["statements"])
    unspread = {k for k, s in inp["statements"].items() if not s["is_spread"]}
    assert {(t["obligor_id"], t["fiscal_year"]) for t in tasks if t["spread_date"] is None} == unspread
    assert all(t["status"] in ("Received - Not Started", "In Progress") and t["is_overdue"] for t in tasks
               if t["spread_date"] is None)
    for t in tasks:
        assert t["received_date"] <= (t["spread_date"] or AS_OF.isoformat())
        if t["checked_date"]:
            assert t["spread_date"] < t["checked_date"] <= AS_OF.isoformat() and t["checker_id"] != t["analyst_id"]


def test_reviews(built):
    cfg, entities, xref, inp, out = built
    people = {p["employee_id"]: p["role"] for p in truth.build_people(cfg)}
    unspread = {k for k, s in inp["statements"].items() if not s["is_spread"]}
    statuses = {"Approved", "Declined", "Submitted - Awaiting Approval", "Overdue", "In Preparation", "Not Started"}
    for r in out["credit_review"]:
        assert r["status"] in statuses and people[r["analyst_id"]] == "Credit Analyst"
        if r["approver_id"]:
            assert people[r["approver_id"]] == "Credit Approver"
        if r["review_type"] == "Annual" and (r["obligor_id"], r["review_fiscal_year"]) in unspread:
            assert r["submitted_date"] is None and r["blocker_reason"]
        if r["memo_excerpt"]:
            assert len(r["memo_excerpt"].split()) <= 40 and -1 <= r["memo_sentiment_score"] <= 1
        if r["approved_date"]:
            assert r["submitted_date"] <= r["approved_date"] <= AS_OF.isoformat()
    types = {r["review_type"] for r in out["credit_review"]}
    assert types == {"Annual", "New Money", "Watchlist", "Amendment"}
    q3 = [r for r in out["credit_review"] if r["review_type"] == "Annual"
          and "2026-10-01" <= (r["due_date"] or "") <= "2026-12-31"]   # due in Q3 FY2026 (5.3 Q5)
    assert q3 and {r["status"] for r in q3} <= {"Not Started", "In Preparation", "Submitted - Awaiting Approval",
                                                 "Approved"}
    tone = {r["memo_tone"]: r["memo_sentiment_score"] for r in out["credit_review"] if r["memo_tone"]}
    assert tone["Positive"] > 0 > tone["Negative"]


def _digest(out):
    return hashlib.sha256(json.dumps({t: out[t] for t in BRONZE}, sort_keys=True, default=str).encode()).hexdigest()


def test_deterministic_across_processes(built):
    """Python salts str/date hashes per process, so nothing may depend on set order (D08): a fresh
    process with another hash seed must produce byte-identical tables."""
    *_, out = built
    here = Path(__file__).resolve()
    code = (f"import sys; sys.path[:0] = [{str(here.parents[2] / 'src')!r}, {str(here.parent)!r}]\n"
            "import test_credit_risk as t\n"
            "from smbc_genie_lib import credit_risk as cr, fragment, truth\n"
            "from smbc_genie_lib.config import load_config\n"
            "cfg = load_config(); g = truth.build_groups(cfg); e = truth.build_entities(cfg, g)\n"
            "_, x = fragment.fragment_all(cfg, e, [r['group_id'] for r in g])\n"
            "print(t._digest(cr.build_credit_risk(cfg, e, x, truth.build_people(cfg), t._inputs(cfg, e, x))))\n")
    res = subprocess.run([sys.executable, "-c", code], env={**os.environ, "PYTHONHASHSEED": "4242"},
                         capture_output=True, text=True, check=True)
    assert res.stdout.strip().splitlines()[-1] == _digest(out)
