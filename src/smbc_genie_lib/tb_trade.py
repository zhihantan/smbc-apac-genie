"""Transaction-banking & trade depth (brief §5.7, §5.8; DECISIONS D24; storylines 5 and 6).

Bronze tables, keyed by the source systems' own ids (never truth ids):

  trade_event                  lifecycle events of every instrument in trade_finance_txn (issue / amend /
                               present / accept / claim / pay / expire) with the outstanding after each
  trade_presentation           LC document presentations: received / checked, discrepancies, decision
  trade_scf_invoice            approved invoices of every programme supplier; the financed outstanding at
                               as-of equals scf_supplier.financed_amount_usd (and so the programme drawn)
  ext_trade_statistics         vendor monthly trade flows, origin -> destination x HS chapter
  core_time_deposit            TD placements: a rolling chain per TD / money-market account whose
                               principal is the account's month-end balance, + the HK CASA placements
  core_liquidity_structure(+_participant)  cash pools of multi-entity groups (header + participants)
  pay_channel_usage            monthly cust_no x channel usage (Spark: payments + `channel_profile`)
  fin_tb_fee                   monthly TB fee lines not derivable from payments / trade / FX (Spark)

Instrument events come from trade.instrument_lifecycle, so they always agree with the instrument
status. Pure Python and deterministic; the runner only aggregates payments and balances in Spark.
"""
from __future__ import annotations

import datetime as _dt
import math
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

from . import fiscal, names, reference, rng, storylines, trade

D, TD = _dt.date, _dt.timedelta


def _pick(seed: int, pairs: Sequence[Tuple], *keys):
    return rng.weighted_choice(seed, [p for p, _ in pairs], [w for _, w in pairs], *keys)


def _d(x) -> Optional[D]:
    if x is None or isinstance(x, D):
        return x
    return D.fromisoformat(str(x)[:10])


def _iso(d) -> Optional[str]:
    return d.isoformat() if d else None


def _ts(x: Optional[_dt.datetime]) -> Optional[str]:
    return x.strftime("%Y-%m-%d %H:%M:%S") if x else None


def add_months(d: D, n: int) -> D:
    y, m = divmod(d.year * 12 + d.month - 1 + n, 12)
    nxt = D(y + (m == 11), (m + 1) % 12 + 1, 1)
    return D(y, m + 1, min(d.day, (nxt - TD(days=1)).day))


def month_end(d: D) -> D:
    return add_months(d.replace(day=1), 1) - TD(days=1)


def _interp(points: Sequence[Tuple[D, float]], d: D) -> float:
    """Piecewise-linear value of a dated curve (flat outside its range)."""
    if d <= points[0][0]:
        return points[0][1]
    for (d0, v0), (d1, v1) in zip(points, points[1:]):
        if d <= d1:
            return v0 + (v1 - v0) * (d - d0).days / (d1 - d0).days
    return points[-1][1]


# ---- trade events & document presentations ----------------------------------------------------
def trade_depth(cfg, txns: List[Dict], party_entity: Dict[str, str],
                health: Dict[str, Dict[D, float]]) -> Dict[str, object]:
    """bronze.trade_event + bronze.trade_presentation from trade_finance_txn rows (as landed).
    `status_mismatches` counts instruments whose lifecycle status differs from the landed one."""
    seed, as_of = cfg.random_seed, D.fromisoformat(cfg.as_of_date)
    events, pres, mismatches = [], [], 0
    for t in txns:
        hb = health.get(party_entity.get(t["party_id"]), {})
        life = trade.instrument_lifecycle(
            seed, t, trade.discrepancy_propensity(hb, t["booking_location"], t["product_type"]), as_of)
        mismatches += life["status"] != t["status"]
        tid = t["txn_id"]
        for n, e in enumerate(life["events"], start=1):
            events.append({"event_id": f"{tid}-E{n:02d}", "txn_id": tid, "event_seq": n,
                           "event_type": e["event_type"], "event_date": e["event_date"].isoformat(),
                           "event_detail": e["detail"], "amount_usd": e["amount_usd"],
                           "outstanding_after_usd": e["outstanding_after_usd"],
                           "presentation_id": f"{tid}-P{e['presentation_no']}" if e["presentation_no"] else None})
        for p in life["presentations"]:
            types, hours = p["discrepancy_types"], p["turnaround_hours"]
            pres.append({"presentation_id": f"{tid}-P{p['presentation_no']}", "txn_id": tid,
                         "presentation_no": p["presentation_no"], "is_re_presentation": p["is_re_presentation"],
                         "received_ts": _ts(p["received_ts"]), "checked_ts": _ts(p["checked_ts"]),
                         "turnaround_hours": hours, "sla_hours": trade.SLA_HOURS,
                         "is_over_sla": None if hours is None else hours > trade.SLA_HOURS,
                         "is_discrepant": p["is_discrepant"], "discrepancy_count": len(types),
                         "discrepancy_types": "; ".join(types) or None,
                         "primary_discrepancy_type": types[0] if types else None, "decision": p["decision"],
                         "amount_usd": p["amount_usd"], "ops_team": p["ops_team"]})
    return {"trade_event": events, "trade_presentation": pres, "status_mismatches": mismatches}


# ---- SCF invoices -------------------------------------------------------------------------
SCF_INVOICES_AT_SCALE_1 = 300_000
SCF_BENCHMARK = [(D(2024, 4, 1), 5.30), (D(2024, 12, 31), 4.50), (D(2025, 12, 31), 3.90),
                 (D(2026, 9, 30), 3.70)]       # USD term benchmark (%), plus the supplier margin
ADVANCE_RATE = {"Payables Finance": 1.00, "Receivables Finance": 0.90}
TERMS_BUFFER_DAYS = 8                          # payment terms = days paid early + approval / request lag
TAKEUP = (0.45, 0.85)                          # share of eligible invoices an onboarded supplier finances
INVOICE_SIGMA = 0.30                           # lognormal spread of a supplier's invoice amounts
UTIL_CAP = 0.97                                # programme limit check on past month-end outstanding


def _supplier_plan(cfg, s: Dict, p: Dict, as_of: D) -> Dict:
    """Invoice cadence of one supplier: start, rate per month, terms, and (onboarded) take-up."""
    seed, sid = cfg.random_seed, s["supplier_id"]
    start = max(trade.TXN_START, _d(p["launch_date"]))
    plan = {"start": start, "months": max(1.0, (as_of - start).days / 30.44)}
    if s["is_onboarded"]:
        onb = _d(s["onboarded_date"])
        takeup = TAKEUP[0] + (TAKEUP[1] - TAKEUP[0]) * rng.unit(seed, "invtake", sid)
        w12 = max(onb, as_of - TD(days=365))
        n12 = max(s["n_invoices_financed"] + 2, round(s["n_invoices_financed"] / takeup))
        plan.update(onb=onb, takeup=takeup, rate=n12 / max(1.0, (as_of - w12).days / 30.44),
                    terms=s["avg_days_paid_early"] + TERMS_BUFFER_DAYS)
    else:
        plan.update(onb=None, takeup=0.0, rate=0.5 + 2.5 * rng.unit(seed, "invrate", sid),
                    terms=rng.randint(seed, 30, 90, "invterm", sid))
    return plan


def _benchmark(d: D) -> float:
    return _interp(SCF_BENCHMARK, d)


def build_scf_invoices(cfg, programmes: List[Dict], suppliers: List[Dict]) -> List[Dict]:
    """bronze.trade_scf_invoice: approved payables uploaded per programme supplier (prospective
    suppliers are paid at maturity; onboarded ones finance a share early). Targeted so each
    supplier finances exactly `n_invoices_financed` in the last 12 months and its financed
    outstanding at as-of equals `financed_amount_usd`."""
    seed, as_of = cfg.random_seed, D.fromisoformat(cfg.as_of_date)
    progs = {p["programme_id"]: p for p in programmes}
    plans = {s["supplier_id"]: _supplier_plan(cfg, s, progs[s["programme_id"]], as_of) for s in suppliers}
    target = round(SCF_INVOICES_AT_SCALE_1 * cfg.scale)
    onb_n = sum(plans[s["supplier_id"]]["rate"] * plans[s["supplier_id"]]["months"] for s in suppliers if s["is_onboarded"])
    pro_n = sum(plans[s["supplier_id"]]["rate"] * plans[s["supplier_id"]]["months"] for s in suppliers if not s["is_onboarded"])
    pro_scale = max(0.2, (target - onb_n) / pro_n) if pro_n else 1.0
    ticket = defaultdict(list)
    rows: List[Dict] = []
    for s in sorted(suppliers, key=lambda s: not s["is_onboarded"]):  # onboarded first: they set tickets
        p, plan, sid = progs[s["programme_id"]], plans[s["supplier_id"]], s["supplier_id"]
        adv = ADVANCE_RATE.get(p["programme_type"], 1.0)
        if s["is_onboarded"]:
            exp_out = s["n_invoices_financed"] / 12.0 * s["avg_days_paid_early"] / 30.44
            mean = s["financed_amount_usd"] / max(0.5, exp_out) / adv
            ticket[p["programme_id"]].append(mean)
            rate = plan["rate"]
        else:
            peers = ticket.get(p["programme_id"]) or [p["limit_usd"] / 200.0]
            mean = sorted(peers)[len(peers) // 2] * (0.3 + 0.9 * rng.unit(seed, "invpmean", sid))
            rate = plan["rate"] * pro_scale
        rows += _supplier_invoices(seed, s, p, plan, rate, mean, adv, as_of)
    _cap_utilisation(rows, programmes, as_of)
    rows.sort(key=lambda r: (r["approval_date"], r["supplier_id"], r["_k"]))
    for n, r in enumerate(rows, start=1):
        r["invoice_id"] = f"INV-{n:07d}"
    return [{c: (_iso(r[c]) if isinstance(r[c], D) else r[c]) for c in INVOICE_COLUMNS} for r in rows]


INVOICE_COLUMNS = ["invoice_id", "programme_id", "supplier_id", "anchor_party_id", "supplier_invoice_no",
                   "invoice_date", "approval_date", "due_date", "currency", "invoice_amount_usd", "is_financed",
                   "financing_date", "financed_amount_usd", "advance_rate", "discount_rate_pct", "days_financed",
                   "discount_amount_usd", "net_proceeds_usd", "status"]


def _supplier_invoices(seed: int, s: Dict, p: Dict, plan: Dict, rate: float, mean: float, adv: float,
                       as_of: D) -> List[Dict]:
    sid, start = s["supplier_id"], plan["start"]
    n = max(1, round(rate * plan["months"]))
    span = (as_of - start).days
    rows = []
    for k in range(n):
        inv = start + TD(days=int((k + rng.unit(seed, "invpos", sid, k)) / n * span))
        appr = inv + TD(days=rng.randint(seed, 3, 10, "invappr", sid, k))
        if appr <= as_of:
            rows.append(_invoice(seed, s, k, inv, appr, plan["terms"], mean, adv))
    if not plan["onb"]:
        return [_settle(r, as_of) for r in rows]
    onb, w12 = plan["onb"], as_of - TD(days=365)
    eligible = [r for r in rows if r["approval_date"] >= onb and r["_fin_date"] <= as_of]
    recent = [r for r in eligible if r["_fin_date"] > w12]
    live = [r for r in recent if r["due_date"] > as_of]
    if not live:  # make sure a financed invoice is outstanding at as-of
        inv = as_of - TD(days=rng.randint(seed, 4, 9, "invlast", sid))
        r = _invoice(seed, s, n, inv, inv + TD(days=2), plan["terms"], mean, adv)
        rows.append(r)
        recent.append(r)
        live = [r]
    # financed invoices are evenly spaced (low-discrepancy picks), so the financed outstanding path is
    # smooth over time and only the as-of point needs pinning; exactly n_invoices_financed in 12 months
    must = max(live, key=lambda r: r["approval_date"])
    rest = sorted((r for r in recent if r is not must), key=lambda r: r["approval_date"])
    n_rest, off = s["n_invoices_financed"] - 1, rng.unit(seed, "invfin", sid)
    for r in [must] + [rest[int((j + off) * len(rest) / n_rest)] for j in range(n_rest)]:
        r["is_financed"] = True
    old = sorted((r for r in eligible if r["_fin_date"] <= w12), key=lambda r: r["approval_date"])
    for j, r in enumerate(old):
        if (off + j * plan["takeup"]) % 1.0 < plan["takeup"]:
            r["is_financed"] = True
    out = [r for r in rows if r["is_financed"] and r["_fin_date"] <= as_of < r["due_date"]]
    f = s["financed_amount_usd"] / (adv * sum(r["invoice_amount_usd"] for r in out))
    for r in out:  # financed outstanding reconciles to the supplier (and the programme drawn)
        r["invoice_amount_usd"] *= f
    return [_settle(r, as_of, adv, s["discount_rate_bps"]) for r in rows]


def _invoice(seed: int, s: Dict, k: int, inv: D, appr: D, terms: int, mean: float, adv: float) -> Dict:
    sid = s["supplier_id"]
    return {"_k": k, "programme_id": s["programme_id"], "supplier_id": sid, "anchor_party_id": s["anchor_party_id"],
            "supplier_invoice_no": f"{sid[4:]}-{inv:%y%m}-{k:04d}", "invoice_date": inv, "approval_date": appr,
            "due_date": inv + TD(days=terms), "currency": "USD", "is_financed": False,
            "_fin_date": appr + TD(days=rng.randint(seed, 0, 2, "invreq", sid, k)),
            "invoice_amount_usd": mean * math.exp(rng.normal(seed, 0.0, INVOICE_SIGMA, "invamt", sid, k)),
            "advance_rate": adv}


def _cap_utilisation(rows: List[Dict], programmes: List[Dict], as_of: D) -> None:
    """Programme limit check: at past month-ends the financed outstanding stays within UTIL_CAP of
    the limit (invoices live then but not at as-of are scaled down; the as-of book is untouched)."""
    lim = {p["programme_id"]: p["limit_usd"] for p in programmes}
    fin: Dict[str, List[Dict]] = defaultdict(list)
    for r in rows:
        if r["is_financed"]:
            fin[r["programme_id"]].append(r)
    for pid, inv in fin.items():
        me = month_end(min(r["financing_date"] for r in inv))
        while me < as_of.replace(day=1):
            live = [r for r in inv if r["financing_date"] <= me < r["due_date"]]
            excess = sum(r["financed_amount_usd"] for r in live) - UTIL_CAP * lim[pid]
            adjustable = [r for r in live if not r["financing_date"] <= as_of < r["due_date"]]
            room = sum(r["financed_amount_usd"] for r in adjustable)
            if excess > 0 and room > 0:
                f = max(0.0, (room - excess) / room)
                for r in adjustable:
                    _scale_invoice(r, f)
            me = month_end(add_months(me, 1))


def _scale_invoice(r: Dict, f: float) -> None:
    r["invoice_amount_usd"] = round(r["invoice_amount_usd"] * f, 2)
    r["financed_amount_usd"] = round(r["invoice_amount_usd"] * r["advance_rate"], 2)
    r["discount_amount_usd"] = round(r["financed_amount_usd"] * r["discount_rate_pct"] / 100.0
                                     * r["days_financed"] / 360.0, 2)
    r["net_proceeds_usd"] = round(r["financed_amount_usd"] - r["discount_amount_usd"], 2)


def _settle(r: Dict, as_of: D, adv: float = 1.0, margin_bps: float = 0.0) -> Dict:
    """Financing economics and as-of status of one invoice (in place)."""
    r["invoice_amount_usd"] = round(r["invoice_amount_usd"], 2)
    if r["is_financed"]:
        fin = r["_fin_date"]
        days = (r["due_date"] - fin).days
        rate = round(_benchmark(fin) + margin_bps / 100.0, 4)
        amt = round(r["invoice_amount_usd"] * adv, 2)
        disc = round(amt * rate / 100.0 * days / 360.0, 2)
        r.update(financing_date=fin, financed_amount_usd=amt, discount_rate_pct=rate, days_financed=days,
                 discount_amount_usd=disc, net_proceeds_usd=round(amt - disc, 2),
                 status="Settled" if r["due_date"] <= as_of else "Financed")
    else:
        r.update(financing_date=None, financed_amount_usd=0.0, advance_rate=None, discount_rate_pct=None,
                 days_financed=None, discount_amount_usd=0.0, net_proceeds_usd=None,
                 status="Paid at Maturity" if r["due_date"] <= as_of else "Approved")
    return r


# ---- external trade statistics -------------------------------------------------------------------
TRADE_VENDOR = "Harbourline Trade Analytics"   # fictional vendor
STATS_FROM = D(2023, 4, 1)
TOTAL_FLOW_USD = 2.4e12                        # annual flows across the covered corridors
MIN_CELL_USD = 40e6                            # cells below this annual value are not published
MASS = {"CN": 34, "US": 20, "DE": 12, "JP": 11, "KR": 9, "HK": 8, "SG": 7, "TW": 6, "IN": 6, "VN": 5,
        "MY": 4, "TH": 4, "AU": 4, "GB": 4, "ID": 3.5, "PH": 1.6, "NZ": 0.6}
NEIGHBOURS = {frozenset(p) for p in [("CN", "HK"), ("CN", "VN"), ("CN", "KR"), ("CN", "TW"), ("CN", "JP"),
                                     ("JP", "KR"), ("SG", "MY"), ("SG", "ID"), ("MY", "TH"), ("TH", "VN"),
                                     ("AU", "NZ"), ("HK", "TW"), ("SG", "IN"), ("CN", "AU")]}
EXPORT_MIX = {
    "CN": {"Electronics": 30, "Machinery": 18, "Textiles & Apparel": 8, "Iron & Steel": 6, "Chemicals": 6,
           "Plastics": 6, "Auto Parts": 6, "Precision Instruments": 4},
    "JP": {"Auto Parts": 22, "Machinery": 22, "Electronics": 18, "Chemicals": 9, "Precision Instruments": 9,
           "Iron & Steel": 7, "Plastics": 6},
    "KR": {"Electronics": 33, "Auto Parts": 15, "Machinery": 12, "Chemicals": 9, "Iron & Steel": 8,
           "Mineral Fuels": 8, "Plastics": 6},
    "TW": {"Electronics": 55, "Machinery": 12, "Precision Instruments": 8, "Plastics": 7, "Chemicals": 6},
    "US": {"Machinery": 15, "Electronics": 15, "Mineral Fuels": 15, "Food & Agri": 12, "Chemicals": 10,
           "Precision Instruments": 8, "Auto Parts": 8, "Plastics": 6, "Ores & Metals": 4},
    "DE": {"Auto Parts": 22, "Machinery": 22, "Chemicals": 14, "Electronics": 12, "Precision Instruments": 9,
           "Plastics": 6, "Iron & Steel": 5},
    "GB": {"Machinery": 18, "Auto Parts": 14, "Chemicals": 14, "Precision Instruments": 10, "Electronics": 10,
           "Food & Agri": 8, "Mineral Fuels": 8},
    "AU": {"Ores & Metals": 38, "Mineral Fuels": 32, "Food & Agri": 15, "Machinery": 3},
    "ID": {"Mineral Fuels": 28, "Palm Oil & Fats": 16, "Ores & Metals": 10, "Iron & Steel": 8,
           "Textiles & Apparel": 8, "Electronics": 6, "Food & Agri": 6, "Chemicals": 5},
    "MY": {"Electronics": 38, "Mineral Fuels": 14, "Palm Oil & Fats": 10, "Machinery": 8, "Chemicals": 6, "Plastics": 6},
    "TH": {"Electronics": 22, "Machinery": 14, "Auto Parts": 14, "Food & Agri": 12, "Plastics": 8, "Chemicals": 6},
    "VN": {"Electronics": 42, "Textiles & Apparel": 15, "Machinery": 10, "Food & Agri": 6, "Iron & Steel": 4},
    "IN": {"Mineral Fuels": 18, "Chemicals": 12, "Textiles & Apparel": 10, "Iron & Steel": 8, "Food & Agri": 8,
           "Machinery": 8, "Electronics": 8, "Auto Parts": 6},
    "PH": {"Electronics": 55, "Machinery": 10, "Food & Agri": 8, "Ores & Metals": 6},
    "SG": {"Electronics": 35, "Mineral Fuels": 18, "Machinery": 14, "Chemicals": 12, "Precision Instruments": 6,
           "Plastics": 5},
    "HK": {"Electronics": 55, "Machinery": 10, "Precision Instruments": 8, "Textiles & Apparel": 6},
    "NZ": {"Food & Agri": 60, "Machinery": 6, "Ores & Metals": 4},
}
MIX_FLOOR = 1.0                                # every chapter carries at least this weight
MARKET_SURGE = 0.37                            # storyline 6: HS 85/87 into VN/IN from CN/KR/JP, H1 YoY
MARKET_TAILWIND = 0.20                         # ... other goods on those corridors
DOWNTURN = {"Mineral Fuels": -0.10, "Ores & Metals": -0.06}   # coal & resources cycle from 2025 (p.a.)


def _affinity(o: str, d: str) -> float:
    if frozenset((o, d)) in NEIGHBOURS:
        return 1.6
    if o in ("US",) or d in ("US",):
        return 0.7
    if o in ("DE", "GB") or d in ("DE", "GB"):
        return 0.5
    return 1.0


def build_trade_statistics(cfg) -> List[Dict]:
    """bronze.ext_trade_statistics: monthly vendor flows (USD) by origin -> destination x HS chapter,
    Apr-2023 to as-of month (last three months preliminary, as-of month a flash estimate). Gravity
    base (mass x mass x affinity x export mix), trend + seasonality + noise; resources fall from
    2025; storyline 6's corridors surge exactly MARKET_SURGE in H1 FY2026 (HS 85 / 87)."""
    seed = cfg.random_seed
    as_of = D.fromisoformat(cfg.as_of_date)
    apac = {l["code"] for l in cfg.booking_locations}
    countries = sorted(MASS)
    hs = {c: (h, desc) for c, h, desc, _ in trade.COMMODITIES}
    cells = []
    for o in countries:
        mix = {c: EXPORT_MIX.get(o, {}).get(c, 0.0) + MIX_FLOOR for c in hs}
        tot = sum(mix.values())
        for d in countries:
            if o == d or not ({o, d} & apac):
                continue
            for c, w in mix.items():
                cells.append([o, d, c, MASS[o] * MASS[d] * _affinity(o, d) * w / tot
                              * (0.75 + 0.5 * rng.unit(seed, "tsbase", o, d, c))])
    k = TOTAL_FLOW_USD / sum(v for *_, v in cells)
    months, m = [], STATS_FROM
    while m <= as_of:
        months.append(m)
        m = add_months(m, 1)
    src, dst = set(storylines.VN_IN["source_countries"]), set(storylines.VN_IN["import_countries"])
    rows, story = [], []
    for o, d, c, v in cells:
        annual = v * k
        if annual < MIN_CELL_USD:
            continue
        g = 0.04 + 0.04 * (rng.unit(seed, "tsg", o, d, c) - 0.5) + (0.03 if c == "Electronics" else 0.0)
        surge_cell = o in src and d in dst
        for m in months:
            yrs = (m - STATS_FROM).days / 365.25
            val = annual / 12 * (1 + g) ** yrs * (1 + DOWNTURN.get(c, 0.0) * max(0.0, (m - D(2025, 1, 1)).days / 365.25))
            val *= {2: 0.85 if o == "CN" else 0.93, 3: 1.06, 10: 1.05, 11: 1.05}.get(m.month, 1.0)
            val *= 1 + 0.04 * (rng.unit(seed, "tsn", o, d, c, m.isoformat()) - 0.5)
            if surge_cell:
                ramp = min(1.0, max(0.0, (m - trade.TAILWIND_RAMP[0]).days / (trade.TAILWIND_RAMP[1] - trade.TAILWIND_RAMP[0]).days))
                val *= 1 + (MARKET_SURGE if c in trade.SURGE_COMMODITIES else MARKET_TAILWIND) * ramp
            released = month_end(m) + TD(days=35)
            status = ("Flash Estimate" if m == as_of.replace(day=1) or released > as_of else
                      "Preliminary" if m >= add_months(as_of.replace(day=1), -3) else "Final")
            row = {"record_id": f"TS-{o}{d}-{hs[c][0]}-{m:%Y%m}", "period_month": m.isoformat(),
                   "origin_country": o, "destination_country": d, "flow_type": "Import", "hs_chapter": hs[c][0],
                   "hs_description": hs[c][1], "trade_value_usd": val, "data_status": status,
                   "release_date": (as_of if status == "Flash Estimate" else released).isoformat(),
                   "vendor": TRADE_VENDOR}
            rows.append(row)
            if surge_cell and c in trade.SURGE_COMMODITIES:
                story.append(row)
    a, b = storylines.VN_IN["window"]  # exact market surge on the storyline cells
    a0, b0 = fiscal.same_period_prior_fy(a), fiscal.same_period_prior_fy(b)
    prev = sum(r["trade_value_usd"] for r in story if a0.isoformat() <= r["period_month"] <= b0.isoformat())
    cur = [r for r in story if a.isoformat() <= r["period_month"] <= b.isoformat()]
    f = (1 + MARKET_SURGE) * prev / sum(r["trade_value_usd"] for r in cur)
    for r in cur:
        r["trade_value_usd"] *= f
    for r in rows:
        r["trade_value_usd"] = round(r["trade_value_usd"], 2)
    return rows


# ---- time deposits ------------------------------------------------------------------------------
TD_TENORS = {"Time Deposit": [(1, 15), (3, 35), (6, 30), (12, 20)],
             "Money Market Deposit": [(1, 50), (3, 35), (6, 15)]}
ROLL_SAME_TENOR = 0.70
TD_RATE_CURVES = {  # indicative deposit benchmark (%) at Apr-2023, Oct-2024, Sep-2026
    "USD": (5.10, 5.00, 3.90), "SGD": (3.60, 3.30, 2.10), "HKD": (4.50, 4.20, 3.10), "JPY": (0.00, 0.20, 0.55),
    "AUD": (3.90, 4.30, 3.60), "INR": (6.80, 6.70, 5.70), "IDR": (5.90, 6.20, 5.10), "THB": (1.90, 2.40, 1.60),
    "MYR": (2.90, 3.00, 2.80), "VND": (5.00, 4.60, 4.40), "CNY": (1.90, 1.70, 1.20), "PHP": (5.80, 6.20, 5.20),
    "KRW": (3.40, 3.40, 2.40), "TWD": (1.30, 1.50, 1.50), "NZD": (5.30, 5.40, 3.10), "EUR": (3.00, 3.40, 1.90),
    "GBP": (4.30, 5.00, 3.90)}
TD_CURVE_DATES = (D(2023, 4, 1), D(2024, 10, 1), D(2026, 9, 30))
TERM_PREMIUM = {1: -0.15, 3: 0.0, 6: 0.10, 12: 0.20}
MATURITY_INSTRUCTIONS = [("Auto Rollover - Principal + Interest", 50), ("Auto Rollover - Principal", 30),
                         ("Credit to Current Account", 20)]
HK_CASA_SPLIT = (0.40, 0.33, 0.27)            # share of the moved amount per hk_casa entity (lead first)
HK_CASA_MONTH_SHARE = (0.38, 0.34, 0.28)      # ... placed in Jun / Jul / Aug


def td_rate(ccy: str, d: D, tenor: int) -> float:
    curve = list(zip(TD_CURVE_DATES, TD_RATE_CURVES.get(ccy, TD_RATE_CURVES["USD"])))
    return _interp(curve, d) + TERM_PREMIUM.get(tenor, 0.0)


def td_schedule(cfg, accounts: List[Dict]) -> List[Dict]:
    """Rolling TD placements for every TD-class account (Time Deposit / Money Market Deposit):
    contiguous placements from the account's start (or one straddling history start) to the one
    live at as-of. `balance_month_end` is the month-end whose balance sets the principal."""
    seed, as_of = cfg.random_seed, D.fromisoformat(cfg.as_of_date)
    hist = D.fromisoformat(cfg.history_start)
    rows = []
    for a in sorted(accounts, key=lambda a: a["account_id"]):
        tenors = TD_TENORS.get(a["account_type"])
        if not tenors:
            continue
        aid, af = a["account_id"], _d(a["active_from"])
        tenor = _pick(seed, tenors, "tdten", aid, 0)
        d = af if af >= hist else add_months(hist, -int(tenor * rng.unit(seed, "tdpre", aid)) - 1)
        seq = 0
        while d <= as_of:
            seq += 1
            if seq > 1 and rng.unit(seed, "tdroll", aid, seq) >= ROLL_SAME_TENOR:
                tenor = _pick(seed, tenors, "tdten", aid, seq)
            mat = add_months(d, tenor)
            spread = 0.15 + 0.30 * rng.unit(seed, "tdspr", aid)
            rows.append({"placement_id": f"TDP-{aid[4:]}-{seq:03d}", "account_id": aid, "cust_no": a["cust_no"],
                         "currency": a["currency"], "placement_date": d, "maturity_date": mat, "tenor_months": tenor,
                         "interest_rate_pct": round(max(0.01, td_rate(a["currency"], d, tenor) - spread), 4),
                         "maturity_instruction": (_pick(seed, MATURITY_INSTRUCTIONS, "tdins", aid, seq)
                                                  if mat > as_of else "Auto Rollover - Principal + Interest"),
                         "funding_source": "New Funds" if seq == 1 else "Rollover", "funding_account_id": None,
                         "balance_month_end": month_end(max(d, hist)), "principal_usd": None})
            d = mat
    return rows


def hk_casa_placements(cfg, entities: List[Dict], accounts: List[Dict], fx: Dict, seq_start: Dict[str, int]) -> List[Dict]:
    """Storyline 5: the three hk_casa entities move ~USD 900m from current accounts into 3-6 month
    TDs over Jun-Aug 2026, all still placed at as-of. Principal is fixed in USD (sums exactly to
    `moved_usd`); `funding_account_id` is the current account debited."""
    seed, script = cfg.random_seed, storylines.HK_CASA_SCRIPT
    as_of = D.fromisoformat(cfg.as_of_date)
    by_ent: Dict[str, List[Dict]] = defaultdict(list)
    for a in accounts:
        by_ent[a["entity_id"]].append(a)
    rows, seq = [], dict(seq_start)
    trio = storylines.group_entities(entities, script["key"])
    for e, share in zip(trio, HK_CASA_SPLIT):
        accts = sorted(by_ent[e["entity_id"]], key=lambda a: a["account_id"])
        tds = [a for a in accts if a["account_type"] in TD_TENORS]
        cas = [a for a in accts if a["account_type"] == "Current Account"] or [a for a in accts if a["is_casa"]]
        if not tds or not cas:
            continue
        td = min(tds, key=lambda a: (a["account_type"] != "Time Deposit", a["currency"] != "HKD", a["account_id"]))
        ca = max(cas, key=lambda a: (a["currency"] == td["currency"], a["base_balance_usd"]))
        for j, (mshare, month) in enumerate(zip(HK_CASA_MONTH_SHARE, (6, 7, 8))):
            d = D(2026, month, rng.randint(seed, 3, 26, "hkday", e["entity_id"], j))
            tenor = 6 if month == 6 else (3 if month == 8 else (3 if rng.unit(seed, "hkten", e["entity_id"]) < 0.5 else 6))
            mat = add_months(d, tenor)
            assert mat > as_of, "HK CASA placements must still be live at as-of"
            seq[td["account_id"]] = seq.get(td["account_id"], 0) + 1
            usd = script["moved_usd"] * share * mshare
            rows.append({"placement_id": f"TDP-{td['account_id'][4:]}-{seq[td['account_id']]:03d}",
                         "account_id": td["account_id"], "cust_no": td["cust_no"], "currency": td["currency"],
                         "placement_date": d, "maturity_date": mat, "tenor_months": tenor,
                         "interest_rate_pct": round(td_rate(td["currency"], d, tenor) - 0.10, 4),
                         "maturity_instruction": "Credit to Current Account",
                         "funding_source": "Transfer from Current Account", "funding_account_id": ca["account_id"],
                         "balance_month_end": month_end(d), "principal_usd": round(usd, 2),
                         "principal_lcy": round(usd * fx.get((td["currency"], d), 1.0), 2)})
    return rows


# ---- liquidity structures -----------------------------------------------------------------------
P_POOL = {"Strategic": 0.65, "Core": 0.30, "Transactional": 0.08}
CROSS_BORDER_TYPES = [("Notional Pooling", 45), ("Physical Pooling", 40), ("Interest Optimisation", 15)]
SWEEP_FREQUENCY = {"Physical Pooling": [("Daily", 70), ("Intraday", 15), ("Weekly", 15)],
                   "Sweep": [("Daily", 50), ("Weekly", 30), ("Monthly", 20)],
                   "Notional Pooling": [("Daily (Notional)", 1)], "Interest Optimisation": [("Monthly", 1)]}
INTEREST_BENEFIT_BPS = {"Notional Pooling": (15, 40), "Physical Pooling": (10, 30),
                        "Interest Optimisation": (20, 50), "Sweep": (5, 15)}
LIQ_FEE_BASE_USD = {"Notional Pooling": 4000.0, "Physical Pooling": 2500.0, "Interest Optimisation": 3000.0,
                    "Sweep": 800.0}
LIQ_FEE_PER_PARTICIPANT_USD = 300.0
MAX_PARTICIPANTS = 12
POOL_FROM = D(2019, 1, 1)


def _main_ca(accts: List[Dict], prefer_ccy: Optional[str] = None) -> Optional[Dict]:
    cas = [a for a in accts if a["account_type"] == "Current Account"]
    if not cas:
        return None
    return max(cas, key=lambda a: (a["currency"] == prefer_ccy, a["base_balance_usd"], a["account_id"]))


def build_liquidity_structures(cfg, groups: List[Dict], entities: List[Dict],
                               accounts: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """Cash pools of multi-entity groups: likelier for Strategic, Japanese and multi-country groups.
    Header = the group's Singapore (else Hong Kong, else lead) entity's main current account;
    participants = the other entities' main current accounts. Many multi-country groups have no
    pool — the cross-sell list (brief §5.7 Q6)."""
    seed, as_of = cfg.random_seed, D.fromisoformat(cfg.as_of_date)
    by_ent: Dict[str, List[Dict]] = defaultdict(list)
    for a in accounts:
        by_ent[a["entity_id"]].append(a)
    ents_by_group: Dict[str, List[Dict]] = defaultdict(list)
    for e in entities:
        if _main_ca(by_ent[e["entity_id"]]):
            ents_by_group[e["group_id"]].append(e)
    structures, parts = [], []
    for g in groups:
        ents = sorted(ents_by_group.get(g["group_id"], []), key=lambda e: (not e["is_group_lead"], e["entity_id"]))
        countries = {e["booking_country"] for e in ents}
        if len(ents) < 2:
            continue
        p = P_POOL[g["relationship_tier"]] * (1.25 if g["segment"] == "Japanese Corporate" else 1.0) \
            * (1.1 if len(countries) >= 3 else 1.0)
        if rng.unit(seed, "lqs", g["group_id"]) >= p:
            continue
        plans = [("Sweep" if len(countries) == 1 else _pick(seed, CROSS_BORDER_TYPES, "lqstype", g["group_id"]), ents)]
        by_cc = defaultdict(list)
        for e in ents:
            by_cc[e["booking_country"]].append(e)
        dom = max(by_cc.values(), key=len)
        if (len(countries) >= 4 and g["relationship_tier"] == "Strategic" and len(dom) >= 2
                and rng.unit(seed, "lqs2", g["group_id"]) < 0.35):
            plans.append(("Sweep", dom))
        for j, (stype, scope) in enumerate(plans):
            s, ps = _structure(cfg, g, j, stype, scope, by_ent, len(structures) + 1, as_of)
            structures.append(s)
            parts += ps
    return structures, parts


def _structure(cfg, g: Dict, j: int, stype: str, scope: List[Dict], by_ent: Dict, n: int, as_of: D):
    seed, key = cfg.random_seed, (g["group_id"], j)
    header_e = next((e for cc in ("SG", "HK") for e in scope if e["booking_country"] == cc), scope[0])
    if stype == "Sweep":
        header_e = scope[0]
    prefer = "USD" if stype in ("Notional Pooling", "Interest Optimisation") else None
    header = _main_ca(by_ent[header_e["entity_id"]], prefer)
    members = [header_e] + [e for e in scope if e is not header_e][: MAX_PARTICIPANTS - 1]
    accts = [header] + [_main_ca(by_ent[e["entity_id"]], header["currency"] if stype == "Physical Pooling" else None)
                        for e in members[1:]]
    opened = max(max(_d(a["open_date"]) for a in accts), POOL_FROM)
    start = opened + TD(days=int(max(0, (as_of - TD(days=60) - opened).days) * rng.unit(seed, "lqsst", *key) ** 0.8))
    ended = rng.unit(seed, "lqsend", *key) < 0.04 and (as_of - start).days > 400
    end = start + TD(days=int((as_of - start).days * (0.5 + 0.4 * rng.unit(seed, "lqsendd", *key)))) if ended else None
    lo, hi = INTEREST_BENEFIT_BPS[stype]
    jc = g["segment"] == "Japanese Corporate"
    sid = f"LQS-{n:04d}"
    region = "Domestic" if stype == "Sweep" else "APAC"
    structure = {"structure_id": sid, "structure_name": f"{g['group_name']} {region} {stype}",
                 "structure_type": stype, "group_master_id": g["group_id"], "header_cust_no": header["cust_no"],
                 "header_account_id": header["account_id"], "header_country": header_e["booking_country"],
                 "pooling_currency": header["currency"],
                 "sweep_frequency": _pick(seed, SWEEP_FREQUENCY[stype], "lqsfreq", *key),
                 "is_cross_border": len({e["booking_country"] for e in members}) > 1,
                 "interest_benefit_bps": round(lo + (hi - lo) * rng.unit(seed, "lqsben", *key), 1),
                 "monthly_fee_usd": round((LIQ_FEE_BASE_USD[stype] + LIQ_FEE_PER_PARTICIPANT_USD * (len(members) - 1))
                                          * (trade.JC_FEE_FACTOR if jc else 1.0), 2),
                 "start_date": start.isoformat(), "end_date": _iso(end),
                 "status": "Terminated" if end else "Active"}
    names.assert_clean(structure["structure_name"])
    parts = []
    for i, (e, a) in enumerate(zip(members, accts)):
        joined = start if i == 0 or rng.unit(seed, "lqsjoin", *key, i) < 0.8 else \
            start + TD(days=int(((end or as_of) - start).days * 0.6 * rng.unit(seed, "lqsjoind", *key, i)))
        left = end if end else (joined + TD(days=int((as_of - joined).days * rng.unit(seed, "lqsleft", *key, i)))
                                if i and rng.unit(seed, "lqsleave", *key, i) < 0.08 else None)
        target = None
        if stype == "Sweep" and i:
            target = round(rng.choice(seed, [0.0, 50_000.0, 100_000.0, 250_000.0], "lqstgt", *key, i)
                           * reference.FX_BASE.get(a["currency"], 1.0), 2)
        parts.append({"structure_id": sid, "participant_account_id": a["account_id"], "participant_cust_no": a["cust_no"],
                      "participant_country": e["booking_country"], "participant_currency": a["currency"],
                      "role": "Header" if i == 0 else "Participant",
                      "sweep_direction": None if i == 0 else ("Notional (no movement)" if stype in ("Notional Pooling", "Interest Optimisation")
                                                           else _pick(seed, [("Two-way", 70), ("Concentration only", 30)], "lqsdir", *key, i)),
                      "target_balance_lcy": target, "join_date": joined.isoformat(), "leave_date": _iso(left)})
    return structure, parts


# ---- channel profile & TB fees --------------------------------------------------------------------
PORTAL_USERS = {"Strategic": (6, 15), "Core": (3, 8), "Transactional": (1, 4)}
API_P = {"Strategic": 0.45, "Core": 0.25, "Transactional": 0.08}
H2H_P = {"Strategic": 0.55, "Core": 0.25, "Transactional": 0.05}
MANUAL_BASE = {"Strategic": 0.04, "Core": 0.10, "Transactional": 0.25}
FULLY_DIGITAL_P = {"Strategic": 0.60, "Core": 0.45, "Transactional": 0.30}
MANUAL_DECLINE_PER_YEAR = 0.25                 # digital migration of manual instructions from FY2024
MANUAL_CHANNELS = ("SWIFT MT", "RTGS", "FAST", "ISO 20022")   # rails that ops keys manual instructions on
CHANNEL_FROM = D(2024, 4, 1)
# fee tariff (USD per month; Japanese subsidiaries at trade.JC_FEE_FACTOR)
ACCOUNT_FEE_USD = {"Current Account": 40.0, "Savings Account": 15.0}
PORTAL_FEE_PER_USER_USD, PORTAL_MIN_FEE_USD = 35.0, 100.0
H2H_FEE_USD, API_FEE_USD, API_FEE_PER_1K_CALLS_USD = 750.0, 400.0, 0.50


def channel_profile(cfg, entities: List[Dict], core_custno: Dict[str, str]) -> List[Dict]:
    """Per core customer (cust_no): e-banking users, API / host-to-host adoption, manual-instruction
    habit (higher for Transactional, Japanese and public-sector clients) and the billing currency."""
    seed = cfg.random_seed
    ccy = {l["code"]: l["currency"] for l in cfg.booking_locations}
    rows = []
    for e in entities:
        cust = core_custno.get(e["entity_id"])
        if not cust:
            continue
        tier, eid = e["relationship_tier"], e["entity_id"]
        lo, hi = PORTAL_USERS[tier]
        fi = e["segment"] == "Financial Institution"
        api = rng.unit(seed, "chapi", eid) < API_P[tier] * (1.5 if fi else 1.0)
        h2h = rng.unit(seed, "chh2h", eid) < H2H_P[tier]
        manual = 0.0
        if rng.unit(seed, "chdig", eid) >= FULLY_DIGITAL_P[tier]:
            manual = MANUAL_BASE[tier] * math.exp(rng.normal(seed, 0.0, 0.6, "chman", eid))
            manual *= (1.6 if e["segment"] == "Japanese Corporate" else 1.0) * (1.5 if e["segment"] == "Public Sector" else 1.0)
        rows.append({"cust_no": cust, "entity_id": eid, "billing_currency": ccy.get(e["booking_country"], "USD"),
                     "portal_users": rng.randint(seed, lo, hi, "chusers", eid) + (1 if e["is_group_lead"] else 0),
                     "logins_per_user": round(8 + 22 * rng.unit(seed, "chlog", eid), 1),
                     "api_from": (D(2022, 1, 1) + TD(days=int(1640 * rng.unit(seed, "chapid", eid)))) if api else None,
                     "api_base_calls": rng.randint(seed, 2_000, 40_000, "chapic", eid) if api else 0,
                     "h2h_from": (D(2019, 6, 1) + TD(days=int(2300 * rng.unit(seed, "chh2hd", eid)))) if h2h else None,
                     "h2h_files": rng.randint(seed, 4, 40, "chh2hf", eid) if h2h else 0,
                     "manual_share": round(min(0.85, manual), 4),
                     "fee_factor": trade.JC_FEE_FACTOR if e["segment"] == "Japanese Corporate" else 1.0})
    return rows
