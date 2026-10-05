"""Transaction-banking & trade depth tests (pure Python; brief §5.7, §5.8, storylines 5 and 6)."""
import datetime as _dt
from collections import Counter, defaultdict

import pytest

from smbc_genie_lib import accounts, fragment, health, names, onboarding, storylines, tb_trade, trade, truth
from smbc_genie_lib.config import load_config

D, TD = _dt.date, _dt.timedelta


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

    party, core = nondup("trade_party"), nondup("core_customer")
    rels = trade.build_trade_relationships(cfg, entities, party)
    progs = trade.build_scf_programmes(cfg, rels)
    sups = trade.build_scf_suppliers(cfg, progs)
    hl = trade.health_lookup(health.build_health_monthly(cfg, entities))
    fx = trade.fx_lookup(cfg)
    txns = trade.build_trade_txns(cfg, entities, rels, hl, fx)
    accts = accounts.build_accounts(cfg, entities, core, onboarding.new_client_cohort(cfg, entities, xref))
    # bronze shapes: programme / supplier dates land as ISO strings
    progs_b = [{**p, "launch_date": p["launch_date"].isoformat()} for p in progs]
    sups_b = [{**s, "onboarded_date": s["onboarded_date"] and s["onboarded_date"].isoformat()} for s in sups]
    depth = tb_trade.trade_depth(cfg, txns, {v: k for k, v in party.items()}, hl)
    tds = tb_trade.td_schedule(cfg, accts)
    seq = defaultdict(int)
    for r in tds:
        seq[r["account_id"]] = max(seq[r["account_id"]], int(r["placement_id"][-3:]))
    return {
        "cfg": cfg, "groups": groups, "entities": entities, "party": party, "core": core, "txns": txns,
        "progs": progs, "sups": sups, "accts": accts, "depth": depth,
        "invoices": tb_trade.build_scf_invoices(cfg, progs_b, sups_b),
        "stats": tb_trade.build_trade_statistics(cfg), "tds": tds,
        "hk": tb_trade.hk_casa_placements(cfg, entities, accts, fx, dict(seq)),
        "liq": tb_trade.build_liquidity_structures(cfg, groups, entities, accts),
        "profile": tb_trade.channel_profile(cfg, entities, core),
    }


def _as_of(b):
    return D.fromisoformat(b["cfg"].as_of_date)


# ---- trade events & presentations ------------------------------------------------------------------
def test_events_agree_with_instrument_status(built):
    dep, txns = built["depth"], built["txns"]
    assert dep["status_mismatches"] == 0
    ev = dep["trade_event"]
    assert {e["event_type"] for e in ev} <= set(trade.EVENT_ORDER)
    assert len({e["event_id"] for e in ev}) == len(ev)
    last = {}
    for e in ev:
        last[e["txn_id"]] = e
    assert set(last) == {t["txn_id"] for t in txns}  # every instrument starts with its issue event
    for t in txns:
        assert abs(last[t["txn_id"]]["outstanding_after_usd"] - t["outstanding_usd"]) < 0.02  # cent rounding
    assert all(e["event_date"] <= built["cfg"].as_of_date for e in ev)
    assert 2.5 <= len(ev) / len(txns) <= 3.5  # ~400k events per ~140-180k instruments at SCALE 1.0


def test_presentations_only_for_lcs_with_sane_checks(built):
    pres, txns = built["depth"]["trade_presentation"], built["txns"]
    product = {t["txn_id"]: t["product_type"] for t in txns}
    assert {product[p["txn_id"]] for p in pres} == {"Import LC", "Export LC"}
    assert 0.8 <= len(pres) / round(90_000 * built["cfg"].scale) <= 1.2
    assert len({p["presentation_id"] for p in pres}) == len(pres)
    checked = [p for p in pres if p["decision"] != "Pending Examination"]
    rate = sum(p["is_discrepant"] for p in checked) / len(checked)
    assert 0.15 <= rate <= 0.35, rate
    assert all(p["is_discrepant"] is None and p["checked_ts"] is None for p in pres if p not in checked)
    for p in checked:
        assert p["is_discrepant"] == (p["discrepancy_count"] > 0) == (p["decision"].startswith("Discrepant"))
        if p["checked_ts"]:
            assert p["checked_ts"] > p["received_ts"] and p["is_over_sla"] == (p["turnaround_hours"] > p["sla_hours"])
        assert not (p["is_re_presentation"] and p["is_discrepant"])
    over = [p["is_over_sla"] for p in pres if p["is_over_sla"] is not None]
    assert 0.10 <= sum(over) / len(over) <= 0.40


def test_discrepancies_track_health_deterioration():
    p_ok = trade.discrepancy_propensity({D(2025, 6, 1): 0.70, D(2026, 6, 1): 0.70}, "SG", "Export LC")
    p_bad = trade.discrepancy_propensity({D(2025, 6, 1): 0.70, D(2026, 6, 1): 0.40}, "SG", "Export LC")
    assert p_bad(D(2026, 6, 15)) > p_ok(D(2026, 6, 15)) + 0.25  # a falling client's rate rises (EWS input)


# ---- SCF invoices ------------------------------------------------------------------------------------
def test_invoices_reconcile_to_suppliers_and_programmes(built):
    inv, sups, progs = built["invoices"], built["sups"], built["progs"]
    as_of, w12 = built["cfg"].as_of_date, (_as_of(built) - TD(days=365)).isoformat()
    assert 0.8 <= len(inv) / round(tb_trade.SCF_INVOICES_AT_SCALE_1 * built["cfg"].scale) <= 1.2
    out, n12 = defaultdict(float), Counter()
    for i in inv:
        if i["is_financed"]:
            if i["financing_date"] <= as_of < i["due_date"]:
                out[i["supplier_id"]] += i["financed_amount_usd"]
            if i["financing_date"] > w12:
                n12[i["supplier_id"]] += 1
    for s in sups:
        assert abs(out[s["supplier_id"]] - s["financed_amount_usd"]) < 1.0, s["supplier_id"]
        assert n12[s["supplier_id"]] == s["n_invoices_financed"], s["supplier_id"]
    for p in progs:  # programme utilisation at as-of is the invoices' financed outstanding / limit
        o = sum(out[s["supplier_id"]] for s in sups if s["programme_id"] == p["programme_id"])
        assert abs(o / p["limit_usd"] - p["utilisation"]) < 1e-4


def test_invoice_financing_rules(built):
    onb = {s["supplier_id"]: s for s in built["sups"]}
    for i in built["invoices"]:
        s = onb[i["supplier_id"]]
        assert i["invoice_date"] < i["approval_date"] <= built["cfg"].as_of_date < i["due_date"] or i["due_date"] <= built["cfg"].as_of_date
        if not i["is_financed"]:
            assert i["financed_amount_usd"] == 0.0 and i["status"] in ("Approved", "Paid at Maturity")
            continue
        assert s["is_onboarded"] and i["approval_date"] >= s["onboarded_date"].isoformat()
        assert i["financing_date"] >= i["approval_date"] and i["days_financed"] > 0
        assert abs(i["days_financed"] - s["avg_days_paid_early"]) <= 12
        assert abs(i["discount_amount_usd"] - i["financed_amount_usd"] * i["discount_rate_pct"] / 100
                   * i["days_financed"] / 360) < 0.02


# ---- external trade statistics -----------------------------------------------------------------------
def test_trade_statistics_mirror_the_surge(built):
    st = built["stats"]
    apac = {l["code"] for l in built["cfg"].booking_locations}
    assert all(r["origin_country"] != r["destination_country"] for r in st)
    assert all({r["origin_country"], r["destination_country"]} & apac for r in st)
    assert {r["hs_chapter"] for r in st} == {hs for _, hs, _, _ in trade.COMMODITIES}
    sel = [r for r in st if r["origin_country"] in storylines.VN_IN["source_countries"]
           and r["destination_country"] in storylines.VN_IN["import_countries"] and r["hs_chapter"] in ("85", "87")]

    def h(a, b):
        return sum(r["trade_value_usd"] for r in sel if a <= r["period_month"] <= b)
    assert abs(h("2026-04-01", "2026-09-30") / h("2025-04-01", "2025-09-30") - 1 - tb_trade.MARKET_SURGE) < 0.001
    assert Counter(r["data_status"] for r in st if r["period_month"] == "2026-09-01") == Counter(
        {"Flash Estimate": len([r for r in st if r["period_month"] == "2026-09-01"])})


def test_smbc_share_of_market_is_plausible(built):
    smbc, mkt = defaultdict(float), defaultdict(float)
    for t in built["txns"]:
        if "2025-04-01" <= t["txn_date"].isoformat() <= "2026-03-31":
            smbc[(t["origin_country"], t["destination_country"])] += t["amount_usd"]
    for r in built["stats"]:
        if "2025-04-01" <= r["period_month"] <= "2026-03-31":
            mkt[(r["origin_country"], r["destination_country"])] += r["trade_value_usd"]
    total = sum(smbc.values()) / sum(mkt[k] for k in smbc)
    assert 0.001 <= total <= 0.08, total  # SMBC finances a fraction of a percent to a few % of its corridors' flows


# ---- time deposits -------------------------------------------------------------------------------------
def test_td_chains_are_contiguous_with_one_live_placement(built):
    as_of = _as_of(built)
    by = defaultdict(list)
    for r in built["tds"]:
        by[r["account_id"]].append(r)
    td_accts = {a["account_id"] for a in built["accts"] if a["account_type"] in tb_trade.TD_TENORS}
    assert set(by) == td_accts
    for rows in by.values():
        rows.sort(key=lambda r: r["placement_date"])
        assert all(a["maturity_date"] == b["placement_date"] for a, b in zip(rows, rows[1:]))
        assert sum(r["maturity_date"] > as_of for r in rows) == 1 and rows[-1]["placement_date"] <= as_of
        assert all(r["tenor_months"] in (1, 3, 6, 12) and 0 < r["interest_rate_pct"] < 8 for r in rows)


def test_hk_casa_placements(built):
    hk, script, as_of = built["hk"], storylines.HK_CASA_SCRIPT, _as_of(built)
    trio = {e["entity_id"] for e in storylines.group_entities(built["entities"], script["key"])}
    ent_of = {a["account_id"]: a["entity_id"] for a in built["accts"]}
    assert len(hk) == 9 and abs(sum(r["principal_usd"] for r in hk) - script["moved_usd"]) < 1.0
    for r in hk:
        assert script["move_from"] <= r["placement_date"] <= script["move_to"]
        assert r["tenor_months"] in range(script["td_tenor_months"][0], script["td_tenor_months"][1] + 1)
        assert r["maturity_date"] > as_of  # still in TDs at H1 close
        assert ent_of[r["account_id"]] in trio and ent_of[r["funding_account_id"]] == ent_of[r["account_id"]]
    assert len({r["placement_id"] for r in built["tds"] + hk}) == len(built["tds"]) + len(hk)


# ---- liquidity structures --------------------------------------------------------------------------
def test_liquidity_structures(built):
    structures, parts = built["liq"]
    ent = {a["account_id"]: a["entity_id"] for a in built["accts"]}
    group = {e["entity_id"]: e["group_id"] for e in built["entities"]}
    by = defaultdict(list)
    for p in parts:
        by[p["structure_id"]].append(p)
    assert 40 <= len(structures) <= 250
    for s in structures:
        ps = by[s["structure_id"]]
        assert len(ps) >= 2 and sum(p["role"] == "Header" for p in ps) == 1
        assert {group[ent[p["participant_account_id"]]] for p in ps} == {s["group_master_id"]}
        assert s["header_account_id"] in {p["participant_account_id"] for p in ps}
        assert s["is_cross_border"] == (len({p["participant_country"] for p in ps}) > 1)
        names.assert_clean(s["structure_name"])
    # many multi-country groups still have no pool: the cross-sell list (brief §5.7 Q6)
    countries = defaultdict(set)
    for a in built["accts"]:
        countries[group[a["entity_id"]]].add(next(e["booking_country"] for e in built["entities"]
                                                 if e["entity_id"] == a["entity_id"]))
    multi = {g for g, cc in countries.items() if len(cc) >= 3}
    pooled = {s["group_master_id"] for s in structures}
    assert 0.3 <= len(multi - pooled) / len(multi) <= 0.85


# ---- channel profile -------------------------------------------------------------------------------------
def test_channel_profile(built):
    prof = built["profile"]
    seg = {e["entity_id"]: e["segment"] for e in built["entities"]}
    assert {p["cust_no"] for p in prof} == set(built["core"].values())
    assert all(0.0 <= p["manual_share"] <= 0.85 and p["portal_users"] >= 1 for p in prof)
    jc = [p["manual_share"] for p in prof if seg[p["entity_id"]] == "Japanese Corporate"]
    other = [p["manual_share"] for p in prof if seg[p["entity_id"]] != "Japanese Corporate"]
    assert sum(jc) / len(jc) > sum(other) / len(other)  # who still sends manual instructions (§5.7 Q9)
    assert 0.3 <= sum(p["manual_share"] == 0 for p in prof) / len(prof) <= 0.7


def test_bronze_rows_carry_no_truth_ids(built):
    rows = (built["depth"]["trade_event"][:5] + built["depth"]["trade_presentation"][:5] + built["invoices"][:5]
            + built["stats"][:5] + built["liq"][0][:5] + built["liq"][1][:5])
    assert all("entity_id" not in r for r in rows)


def test_deterministic(built):
    cfg = built["cfg"]
    assert tb_trade.build_trade_statistics(cfg)[:50] == built["stats"][:50]
    assert tb_trade.build_liquidity_structures(cfg, built["groups"], built["entities"], built["accts"]) == built["liq"]


def test_programme_limits_hold_every_month(built):
    inv, progs = built["invoices"], built["progs"]
    for p in progs:
        fin = [i for i in inv if i["programme_id"] == p["programme_id"] and i["is_financed"]]
        for k in range(30):  # month-ends Apr-2024 .. Sep-2026
            me = tb_trade.month_end(tb_trade.add_months(D(2024, 4, 1), k)).isoformat()
            out = sum(i["financed_amount_usd"] for i in fin if i["financing_date"] <= me < i["due_date"])
            assert out <= max(tb_trade.UTIL_CAP, p["utilisation"]) * p["limit_usd"] + 1.0, (p["programme_id"], me)
