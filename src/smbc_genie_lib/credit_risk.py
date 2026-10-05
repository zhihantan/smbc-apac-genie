"""Credit & risk lifecycle (brief §5.1 / §5.3 / §5.4 / §5.5 / §5.9; storylines 1, 2, 5, 9).

Bronze rows carry the source systems' own keys (credit obligor_id / facility_id, core cust_no, a
finance-engine client reference), never truth ids:

  core_rating_history     rating actions: internal grade 1-10, rating equivalent, IFRS 9 stage
  core_dpd                one row per facility per day in arrears (days past due, overdue amount)
  core_loan_schedule      drawdowns / repayments / prepayments / maturities that explain the
                          month-end drawn balances, plus each live facility's scheduled maturity
  credit_review           annual / new-money / amendment / watchlist reviews with memo excerpts
  credit_spreading_task   one spreading task per obligor x FY statement (FY2023-FY2025)
  fin_capital_allocation  monthly per obligor: EAD, RWA, PD, LGD, stage, ECL, capital
  fin_cost_allocation     monthly per client x product family: direct, RM, operations, HO cost
  fin_client_revenue      monthly per client x product family: NII, fees, FX, trade
  cf_event                predicted liquidity shortfalls / surpluses and the action taken

Behaviour follows the latent monthly health: weaker names miss more payments and migrate down
the master scale. Ratings move only at annual reviews (to the 6-month health-implied grade, at
most 2 notches) and at interim watchlist reviews (2+ notches of deterioration), so the history is
sticky like a real rating book. Stage 2 = 3+ notches below origination, grade >= 8 or > 30 DPD;
Stage 3 = > 90 DPD (or Sunda's unlikely-to-pay call). Finance figures reuse profitability.py; in
fin_relationship_pnl's window each obligor-quarter's lending / deposit / payment revenue and cost
are allocated to months by monthly activity, so quarterly sums reconcile to the cent. Pure Python
and deterministic; `build_credit_risk` returns every table plus the ops truth rows.
"""
from __future__ import annotations

import datetime as _dt
import math
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from . import fiscal, names, profitability as prof, rng, storylines as sl
from .financials import fye as _fye

D, TD = _dt.date, _dt.timedelta
HISTORY_START = D(2023, 4, 1)   # ratings, arrears and loan schedule (FY2023)
FIN_START = D(2024, 4, 1)       # fin_* tables = fin_relationship_pnl's window (FY2024 Q1)

# ---- master scale and IFRS 9 staging ------------------------------------------------------
RATING_SCALE = ["AAA", "AA+", "AA", "A+", "A", "BBB+", "BBB", "BB+", "BB", "B"]  # = truth mapping
STAGE2_NOTCHES, STAGE2_GRADE, BACKSTOP_DPD, DEFAULT_DPD, DEFAULT_GRADE = 3, 8, 30, 90, 10
INTERIM_NOTCHES = 2       # interim watchlist downgrade when the 3-month implied grade is 2+ worse
REVIEW_MAX_MOVE = 2       # an annual review moves the grade at most 2 notches ...
REVIEW_BAND = 0.8         # ... and only when the 6-month implied grade is 0.8+ notch away (hysteresis)
COOL_OFF_MONTHS = 3       # no interim action within 3 months of the last rating action
RATING_MODEL = {"Financial Institution": "FI Scorecard v2", "Public Sector": "Public Sector Scorecard v1",
                "Sponsor & Structured Finance": "Project & Sponsor Model v1"}

# ---- arrears -----------------------------------------------------------------------------
ARREARS_P = (0.0020, 0.050)     # monthly missed-payment probability = base + stress x (1-h)^3
CURE_DAYS = (3.0, 34.0)         # median days to cure, strong .. weak health (lognormal)
N_DEFAULTS = 8                  # non-storyline obligors that reach > 90 DPD (Stage 3)
DEFAULT_FROM = D(2023, 7, 1)
BASE_RATE = 0.045               # reference rate for installment interest
SIGNATURE_BLOCK = (D(2026, 3, 1), D(2026, 4, 30))  # no other 30+ DPD episode falls due here
REVOLVING = {"Revolving Credit Facility", "Overdraft"}
RCF = "Revolving Credit Facility"

# ---- loan schedule -----------------------------------------------------------------------
PREPAY_SHARE, PREPAY_MIN_USD = 0.20, 1_000_000.0

# ---- reviews and spreading ---------------------------------------------------------------
REVIEW_CYCLES = [2022, 2023, 2024, 2025]   # FY of the financials an annual review covers
REVIEW_OFFSET_DAYS = (120, 270)            # annual review due = fiscal year-end + obligor offset
LATE_P = (0.20, 0.30)                      # late-submission probability = base + x (1-h)
REWORK_P = (0.08, 0.20)                    # returned for rework = base + x (1-h)
SPREAD_SLA_DAYS = 21
NEW_MONEY_PENDING_P, NEW_MONEY_DECLINED_P = 0.025, 0.015
AMENDMENT_P = 0.025                        # per covenanted facility-year
AMENDMENT_PURPOSES = ["Covenant reset", "Pricing review", "Security substitution",
                      "Information undertaking waiver"]
NEW_MONEY_PURPOSES = ["working capital", "capex", "refinancing", "acquisition", "general corporate purposes"]
TONE_SCORE = {"Positive": 0.55, "Neutral": 0.05, "Negative": -0.55}

# ---- capital ------------------------------------------------------------------------------
TARGET_CET1 = 0.125             # capital allocated on RWA at the CET1 target incl. buffers
TENOR_RANGE = (1.0, 7.0)        # remaining-life clamp (years) for lifetime PD
LGD_S3_CAP = 0.95

# ---- revenue and cost ----------------------------------------------------------------------
FAMILY_LINE = {"Corporate Lending": "Lending", "Cash": "Transaction Banking", "Liquidity": "Transaction Banking",
               "Payments": "Transaction Banking", "Trade Finance": "Transaction Banking", "FX": "Markets"}
DEPOSIT_FAMILIES = ("Cash", "Liquidity")
RM_SHARE, DIRECT_SHARE = 0.60, 0.15   # of the coverage cost base (RM, product desks); the rest is HO
ANCHOR_ORDER = ["credit_obligor", "core_customer", "tsy_counterparty", "trade_party"]

# ---- cash-flow events ---------------------------------------------------------------------
REL_MIN = 0.5                   # |predicted net| must be >= 50% of typical gross monthly flow
SURPLUS_MIN_USD = 1_500_000.0
SURPLUS_TD_SHARE = 0.25         # TD balance rise (vs the surplus) that counts as "TD Placed"
ACTION_WINDOW_DAYS = 30

# ---- memo templates (<= 40 words; tone drives the sentiment score) --------------------------
MEMOS = {
    ("Annual", "Upgrade"): "FY{fy} review: {name} delivered resilient {sector} earnings; ND/EBITDA {lev}x and "
                           "ICR {icr}x sit comfortably within appetite. Recommend upgrade to grade {grade}.",
    ("Annual", "Positive"): "FY{fy} review: stable trading and conservative leverage (ND/EBITDA {lev}x, ICR "
                            "{icr}x). Covenants met with good headroom. Recommend affirming grade {grade}; "
                            "relationship core.",
    ("Annual", "Neutral"): "FY{fy} review: {name} performed broadly in line with plan; leverage {lev}x, ICR "
                           "{icr}x. No covenant concerns. Grade {grade} affirmed; monitor working capital and pricing.",
    ("Annual", "Downgrade"): "FY{fy} review: margins under pressure in {sector}; ND/EBITDA up to {lev}x and ICR "
                             "down to {icr}x. Recommend downgrade to grade {grade} with closer monitoring.",
    ("Annual", "Negative"): "FY{fy} review: weak cash generation and tightening liquidity at {name}; leverage "
                            "{lev}x. Grade {grade} affirmed with conditions: quarterly accounts and no new money.",
    ("New Money", "Neutral"): "New money: USD {amount}m {ftype} for {purpose}. Grade {grade}, leverage {lev}x; "
                              "margin {margin} bps. Recommend approval on standard terms and covenants.",
    ("New Money", "Exception"): "New money: USD {amount}m {ftype} for {purpose} at {margin} bps, below the "
                                "standalone hurdle margin. Approved as a pricing exception: {reason}.",
    ("New Money", "Distressed"): "Liquidity support: USD {amount}m {ftype} approved under workout conditions "
                                 "(cash sweep, weekly cash reporting, no dividends) while existing lines are "
                                 "reviewed. Grade {grade}.",
    ("New Money", "Declined"): "New money request for USD {amount}m {ftype} ({purpose}) declined: leverage "
                               "{lev}x at grade {grade} leaves no appetite for further exposure. Revisit after "
                               "the next review.",
    ("Watchlist", "Downgrade"): "Watchlist review: deteriorating {sector} conditions and rising utilisation at "
                                "{name}. Grade moved to {grade}; action plan agreed with the RM, monthly monitoring.",
    ("Watchlist", "Negative"): "Watchlist review after an EWS {band} alert: {name} under watch; grade {grade} "
                               "affirmed. Action plan: tighter monitoring, covenant tracking and RM call within "
                               "30 days.",
    ("Amendment", "Neutral"): "Amendment request: {purpose} on the {ftype}; no change to limit or tenor. Credit "
                              "profile unchanged at grade {grade}; recommend approval.",
}
SUNDA_MEMOS = {
    "watch": "Watchlist review: RCF payment due 31-Mar-2026 is over 30 days past due amid coal regulation "
             "pressure. Grade 7 affirmed pending the FY2025 review; IFRS 9 Stage 2.",
    "downgrade": "Watchlist review: missed RCF payment unresolved and ND/EBITDA 4.6x breaches the 4.0x covenant. "
                 "Unlikely to pay: downgrade to grade 9, Stage 3, watchlist Red.",
    "annual": "FY2025 review submitted late: coal regulation hit earnings, the RCF payment due 31-Mar-2026 "
              "was missed and ND/EBITDA 4.6x breaches the 4.0x covenant. Grade 9, Stage 3; workout strategy.",
}


# ---- small helpers ---------------------------------------------------------------------------
def _iso(d: Optional[D]) -> Optional[str]:
    return d.isoformat() if d else None


def _ms(d: D) -> D:
    return d.replace(day=1)


def _me(d: D) -> D:
    return (d.replace(day=28) + TD(days=4)).replace(day=1) - TD(days=1)


def _add_months(d: D, n: int) -> D:
    y, m = divmod(d.month - 1 + n, 12)
    return D(d.year + y, m + 1, 1)


def _months_between(a: D, b: D) -> int:
    return (b.year - a.year) * 12 + b.month - a.month


def _quarter(m: D) -> Tuple[int, int]:
    return fiscal.fiscal_year(m), fiscal.fiscal_quarter(m)


def _bday(d: D) -> D:
    """The nearest business day inside d's month (weekends roll back; forward at a month start)."""
    if d.weekday() < 5:
        return d
    back = d - TD(days=d.weekday() - 4)
    return back if back.month == d.month else d + TD(days=7 - d.weekday())


def _day_in(seed: int, lo: D, hi: D, *keys) -> D:
    """A deterministic business day in [lo, hi] (lo itself if the span has none)."""
    if hi <= lo:
        return lo
    d = lo + TD(days=int(rng.unit(seed, "crday", *keys) * ((hi - lo).days + 1)))
    for cand in [d - TD(days=k) for k in range(3)] + [d + TD(days=k) for k in range(1, 3)]:
        if lo <= cand <= hi and cand.weekday() < 5:
            return cand
    return d


def _pick(seed: int, options: List[str], *keys) -> str:
    return options[rng.hash64(seed, "crpick", *keys) % len(options)]


def _r2(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(x, 2)


def id_maps(xref: List[Dict]) -> Dict[str, Dict[str, str]]:
    """source_system -> {entity_id: that system's primary (non-duplicate) id}."""
    out: Dict[str, Dict[str, str]] = defaultdict(dict)
    for x in xref:
        if not x["is_within_source_dup"]:
            out[x["source_system"]].setdefault(x["entity_id"], x["source_id"])
    return out


def implied_grade(health: float) -> int:
    """Master-scale grade implied by latent health (same mapping as health.build_health_monthly)."""
    return int(max(1, min(10, round(1 + (1 - health) * 9))))


def rating_equivalent(grade: int) -> str:
    return RATING_SCALE[max(1, min(10, grade)) - 1]


def ifrs9_stage(grade: int, orig_grade: int, dpd: int = 0, impaired: bool = False) -> int:
    """Stage 3 if credit-impaired (> 90 DPD or an unlikely-to-pay call); Stage 2 on significant
    deterioration (3+ notches below origination, grade >= 8) or > 30 DPD; else Stage 1."""
    if impaired or dpd > DEFAULT_DPD:
        return 3
    if grade - orig_grade >= STAGE2_NOTCHES or grade >= STAGE2_GRADE or dpd > BACKSTOP_DPD:
        return 2
    return 1


def dpd_bucket(dpd: int) -> str:
    return ("Current" if dpd <= 0 else "1-29" if dpd < 30 else "30-59" if dpd < 60
            else "60-89" if dpd < 90 else "90+")


def _avg_health(hser: Dict[D, float], month: D, n: int) -> float:
    vals = [hser[m] for m in (_add_months(month, -k) for k in range(n)) if m in hser]
    return sum(vals) / len(vals) if vals else 0.6


def _rate(fx: Dict, ccy: str, d: D) -> float:
    return 1.0 if ccy == "USD" else fx.get((ccy, d), fx.get((ccy, min(d, D(2027, 3, 31))), 1.0))


# ---- the lending book -----------------------------------------------------------------------
def facility_book(terms: List[Dict], balances: List[Dict], entity_of_obligor: Dict[str, str]) -> List[Dict]:
    """Facilities (credit_facility_terms) with their month-end (month, limit, drawn) rows in order."""
    rows: Dict[str, list] = defaultdict(list)
    for b in balances:
        rows[b["facility_id"]].append((_ms(b["balance_date"]), float(b["limit_usd"]), float(b["drawn_usd"])))
    book = []
    for t in sorted(terms, key=lambda t: t["facility_id"]):
        eid = entity_of_obligor.get(t["obligor_id"])
        if eid is not None:
            book.append({**t, "entity_id": eid, "is_revolving": t["facility_type"] in REVOLVING,
                         "rows": sorted(rows.get(t["facility_id"], []))})
    return book


def _pay_day(f: Dict, m: D) -> D:
    return _bday(D(m.year, m.month, min(f["origination_date"].day, 28)))


def _drawn_in(f: Dict, m: D) -> Optional[float]:
    return next((dr for mm, _, dr in f["rows"] if mm == m), None)


def installment_usd(f: Dict, drawn: float, month: D) -> float:
    """One monthly instalment: interest (base rate + margin) plus amortisation on term facilities."""
    interest = drawn * (BASE_RATE + f["margin_bps"] / 1e4) / 12.0
    if f["is_revolving"]:
        return round(interest, 2)
    left = max(1, _months_between(month, _ms(f["maturity_date"])))
    return round(interest + drawn / left, 2)


# ---- arrears -----------------------------------------------------------------------------------
def _episode(f: Dict, due: D, cure: Optional[D], drawn: float, kind: str) -> Dict:
    return {"facility_id": f["facility_id"], "obligor_id": f["obligor_id"], "entity_id": f["entity_id"],
            "due_date": due, "cure_date": cure, "kind": kind,
            "installment_usd": installment_usd(f, drawn, _ms(due))}


def _sunda_episode(cfg, book: List[Dict], entities: List[Dict]) -> Optional[Dict]:
    """Storyline 1: the payment due 31-Mar-2026 on Sunda's (Jakarta) revolving line is never cured."""
    due = sl.SUNDA["missed_payment_due"]
    lead = sl.lead_entity(entities, sl.SUNDA["key"])
    live = [f for f in book if f["entity_id"] == lead["entity_id"] and _drawn_in(f, _ms(due)) is not None
            and f["origination_date"] <= due < f["maturity_date"]]
    if not live:
        return None
    f = max(live, key=lambda f: (f["facility_type"] == RCF, f["is_revolving"], f["limit_usd"], f["facility_id"]))
    return _episode(f, due, None, _drawn_in(f, _ms(due)), "Storyline")


def _default_episodes(cfg, book: List[Dict], health: Dict, ents: Dict[str, Dict], as_of: D) -> List[Dict]:
    """The N weakest non-storyline borrowers (lowest 3-month health trough) default: a payment on a
    facility live through as-of is never cured, so DPD passes 90. Due dates avoid Sunda's window."""
    latest = _ms(as_of - TD(days=DEFAULT_DPD + 5))
    by_obl: Dict[str, List[Dict]] = defaultdict(list)
    for f in book:
        if not ents[f["entity_id"]].get("storyline_key"):
            by_obl[f["obligor_id"]].append(f)
    scored = []
    for obl, facs in by_obl.items():
        hser = health.get(facs[0]["entity_id"], {})
        months = sorted({m for f in facs for m, _, _ in f["rows"] if DEFAULT_FROM <= m <= latest})
        if months:
            trough = min(months, key=lambda m: (_avg_health(hser, m, 3), m))
            scored.append((_avg_health(hser, trough, 3), obl, trough, facs))
    out = []
    for _, obl, trough, facs in sorted(scored, key=lambda s: (s[0], s[1])):
        if len(out) == N_DEFAULTS:
            break
        m = trough
        while SIGNATURE_BLOCK[0] <= _ms(m) <= SIGNATURE_BLOCK[1]:
            m = _add_months(m, -1)
        live = [f for f in facs if f["maturity_date"] > as_of and _drawn_in(f, m) is not None
                and f["origination_date"] < _pay_day(f, m)]
        if live:
            f = max(live, key=lambda f: (f["limit_usd"], f["facility_id"]))
            out.append(_episode(f, _pay_day(f, m), None, _drawn_in(f, m), "Default"))
    return out


def arrears_episodes(cfg, book: List[Dict], health: Dict, entities: List[Dict], as_of: D) -> List[Dict]:
    """Missed-payment episodes: health-driven technical arrears (cured within 89 days), the targeted
    defaults and Sunda's scripted one. At most one open episode per facility at a time."""
    seed = cfg.random_seed
    ents = {e["entity_id"]: e for e in entities}
    fixed = _default_episodes(cfg, book, health, ents, as_of)
    if sl.on(cfg, sl.SUNDA_EWS):
        sunda = _sunda_episode(cfg, book, entities)
        fixed += [sunda] if sunda else []
    stop = {ep["facility_id"]: ep["due_date"] for ep in fixed}
    out = list(fixed)
    for f in book:
        if ents[f["entity_id"]].get("storyline_key"):
            continue
        fid, free = f["facility_id"], D.min
        for m, _, drawn in f["rows"]:
            due = _pay_day(f, m)
            if m < HISTORY_START or due > as_of or due <= free or due <= f["origination_date"]:
                continue
            h = health.get(f["entity_id"], {}).get(m, 0.6)
            if rng.unit(seed, "arr", fid, m.isoformat()) >= ARREARS_P[0] + ARREARS_P[1] * (1 - h) ** 3:
                continue
            med = CURE_DAYS[0] + (CURE_DAYS[1] - CURE_DAYS[0]) * (1 - h) ** 2
            days = max(1, min(89, int(rng.lognormal(seed, math.log(med), 0.8, "arrlen", fid, m.isoformat()))))
            if SIGNATURE_BLOCK[0] <= due <= SIGNATURE_BLOCK[1]:
                days = min(days, 25)
            days = min(days, (f["maturity_date"] - due).days - 1)
            cure = due + TD(days=days + 1)
            if days < 1 or (fid in stop and cure >= stop[fid] - TD(days=30)):
                continue
            out.append(_episode(f, due, cure, drawn, "Technical" if days < 30 else "Arrears"))
            free = cure + TD(days=30)
    out.sort(key=lambda ep: (ep["due_date"], ep["facility_id"]))
    for n, ep in enumerate(out, start=1):
        ep["episode_id"] = f"ARR-{n:05d}"
        ep["is_default"] = ep["cure_date"] is None
    return out


def dpd_rows(episodes: List[Dict], fac: Dict[str, Dict], fx: Dict, as_of: D) -> List[Dict]:
    """core_dpd: a row per facility per day while past due; another instalment falls due every 30 days."""
    rows = []
    for ep in episodes:
        ccy, due = fac[ep["facility_id"]]["currency"], ep["due_date"]
        inst_lcy = ep["installment_usd"] * _rate(fx, ccy, due)
        last = min(as_of, ep["cure_date"] - TD(days=1)) if ep["cure_date"] else as_of
        cured = ep["cure_date"] is not None and ep["cure_date"] <= as_of   # a future cure is not known yet
        d = due + TD(days=1)
        while d <= last:
            dpd = (d - due).days
            lcy = round(inst_lcy * (1 + (dpd - 1) // 30), 2)
            rows.append({"facility_id": ep["facility_id"], "obligor_id": ep["obligor_id"], "dpd_date": _iso(d),
                         "days_past_due": dpd, "dpd_bucket": dpd_bucket(dpd), "overdue_amount_lcy": lcy,
                         "overdue_amount_usd": round(lcy / _rate(fx, ccy, d), 2), "currency": ccy,
                         "arrears_episode_id": ep["episode_id"], "due_date": _iso(due),
                         "cure_date": _iso(ep["cure_date"]) if cured else None, "is_cured": cured})
            d += TD(days=1)
    return rows


def _dpd_at(eps: List[Dict], d: D) -> int:
    return max([(d - ep["due_date"]).days for ep in eps
                if ep["due_date"] < d and (ep["cure_date"] is None or d < ep["cure_date"])], default=0)


# ---- loan schedule ---------------------------------------------------------------------------
def _free_day(seed: int, lo: D, hi: D, blocks: List[Tuple[D, D]], *keys) -> D:
    d = _day_in(seed, lo, hi, *keys)
    for s, e in blocks:
        if s <= d <= e:
            d = _day_in(seed, e + TD(days=1), hi, "after", *keys) if e < hi else (
                _day_in(seed, lo, s - TD(days=1), "before", *keys) if s > lo else d)
    return d


def _facility_events(cfg, f: Dict, arrears: List[Tuple[D, D]], forced: List[Tuple[D, float]],
                     blocks: List[Tuple[D, D]], prepay: Optional[Dict], as_of: D) -> List[Dict]:
    seed, fid, mat = cfg.random_seed, f["facility_id"], f["maturity_date"]
    rows = [r for r in f["rows"] if r[0] <= _ms(as_of)]
    ev: List[Dict] = []

    def add(kind, d, amt, purpose, contractual=False):
        ev.append({"facility_id": fid, "obligor_id": f["obligor_id"], "event_type": kind, "event_date": d,
                   "amount_usd": round(amt, 2), "purpose": purpose, "is_contractual": contractual, "_seq": len(ev)})

    opening, bal = 0.0, None
    for i, (m, _, drawn) in enumerate(rows):
        if prepay and m >= _ms(prepay["date"]):
            break
        if i == 0:
            if f["origination_date"] >= HISTORY_START and m == _ms(f["origination_date"]):
                add("Drawdown", f["origination_date"], drawn, "Initial drawdown")
            else:
                opening = drawn
            bal = drawn
            continue
        delta = round(drawn - bal, 2)
        hi = max(m, mat - TD(days=1)) if m == _ms(mat) else _me(m)
        mk = m.isoformat()
        hit = [x for x in forced if _ms(x[0]) == m]
        if hit:
            d, amount = hit[0]
            dd = max(delta, round(amount, 2))
            add("Drawdown", d, dd, "Liquidity shortfall cover")
            if dd - delta > 0.004:
                add("Repayment", _day_in(seed, min(hi, d + TD(days=11)), hi, fid, m, "rep"), dd - delta,
                    "Repayment of short-term drawing")
        elif delta > 0:
            d = _free_day(seed, m, hi, blocks, fid, mk, "dd")
            purpose = ("Capitalised interest and fees" if any(s <= d < e for s, e in arrears)
                       else "Utilisation request" if f["is_revolving"] else "Further drawdown")
            add("Drawdown", d, delta, purpose)
        elif delta < 0:
            if not f["is_revolving"] and -delta >= max(PREPAY_MIN_USD, PREPAY_SHARE * bal):
                add("Prepayment", _day_in(seed, m, hi, fid, mk, "pp"), -delta, "Voluntary prepayment")
            elif not f["is_revolving"]:
                pd_ = _pay_day(f, m)
                add("Repayment", pd_ if m <= pd_ <= hi else _day_in(seed, m, hi, fid, mk, "rp"),
                    -delta, "Scheduled amortisation", True)
            else:
                add("Repayment", _day_in(seed, m, hi, fid, mk, "rr"), -delta, "Voluntary repayment")
        if m == _ms(mat) and mat <= as_of:
            add("Maturity", mat, drawn, "Final repayment at maturity", True)
        bal = drawn
    if prepay:
        add("Prepayment", prepay["date"], prepay["amount"], prepay["reason"])
    elif rows and mat > as_of:
        add("Maturity", mat, rows[-1][2], "Contractual maturity", True)
    ev.sort(key=lambda r: (r["event_date"], r["_seq"]))
    run = opening
    for r in ev:
        run += r["amount_usd"] if r["event_type"] == "Drawdown" else -r["amount_usd"]
        r["drawn_after_usd"] = round(max(0.0, run), 2) if r["event_date"] <= as_of else 0.0
    return ev


def loan_schedule(cfg, book: List[Dict], episodes: List[Dict], fx: Dict, as_of: D,
                  force: Optional[Dict] = None, block: Optional[Dict] = None,
                  prepay: Optional[Dict] = None) -> List[Dict]:
    """Every movement behind the month-end drawn balances: opening (first month-end in the window)
    + events up to a month-end = that month-end's drawn (the maturity month's row is the balance
    repaid at maturity). `force` / `block` place or keep out storyline RCF drawdowns."""
    force, block = force or {}, block or {}
    arrears: Dict[str, list] = defaultdict(list)
    for ep in episodes:
        arrears[ep["facility_id"]].append((ep["due_date"], ep["cure_date"] or D.max))
    out = []
    for f in book:
        fid = f["facility_id"]
        out += _facility_events(cfg, f, arrears.get(fid, []), force.get(fid, []), block.get(fid, []),
                                prepay if prepay and prepay["facility_id"] == fid else None, as_of)
    out.sort(key=lambda r: (r["event_date"], r["facility_id"], r["_seq"]))
    ccy = {f["facility_id"]: f["currency"] for f in book}
    for n, r in enumerate(out, start=1):
        c = ccy[r["facility_id"]]
        r.update(schedule_event_id=f"LSE-{n:07d}", currency=c,
                 amount_lcy=round(r["amount_usd"] * _rate(fx, c, min(r["event_date"], as_of)), 2),
                 status="Settled" if r["event_date"] <= as_of else "Scheduled")
        del r["_seq"]
    return out


def kinokawa_prepay_facility(book: List[Dict], entities: List[Dict]) -> Optional[Dict]:
    """Storyline 2: the Kinokawa lead's largest term-type facility live on the prepayment date (else
    its largest term-type facility, whose terms the integration step extends)."""
    lead = sl.lead_entity(entities, sl.KINOKAWA_SCRIPT["key"])
    terms = [f for f in book if f["entity_id"] == lead["entity_id"] and not f["is_revolving"]]
    if not terms:
        return None
    when = sl.KINOKAWA_SCRIPT["prepay_date"]
    return max(terms, key=lambda f: (f["origination_date"] <= when < f["maturity_date"], f["limit_usd"],
                                     f["facility_id"]))


# ---- ratings -------------------------------------------------------------------------------------
def _obligor_profiles(cfg, book: List[Dict], ents: Dict[str, Dict], as_of: D) -> List[Dict]:
    """Borrowers with exposure in the window: first origination, exposure end, origination grade."""
    by_obl: Dict[str, List[Dict]] = defaultdict(list)
    for f in book:
        if f["maturity_date"] >= HISTORY_START and f["origination_date"] <= as_of:
            by_obl[f["obligor_id"]].append(f)
    out = []
    for obl in sorted(by_obl):
        facs = by_obl[obl]
        e = ents[facs[0]["entity_id"]]
        out.append({"obligor_id": obl, "entity": e, "facilities": facs,
                    "first_orig": min(f["origination_date"] for f in facs),
                    "end": min(as_of, max(f["maturity_date"] for f in facs)),
                    "orig_grade": int(max(1, min(9, e["internal_rating_grade"])))})
    return out


def _outstanding(o: Dict, d: D) -> bool:
    return any(f["origination_date"] <= d <= f["maturity_date"] for f in o["facilities"])


def _rating_path(cfg, o: Dict, hser: Dict[D, float], approvals: List[Tuple[D, Tuple]],
                 start: Tuple[D, Optional[Tuple]], default_date: Optional[D], eps: List[Dict],
                 as_of: D) -> List[Dict]:
    """Rating actions for one obligor: the initial rating, annual-review actions (to the 6-month
    implied grade, +-2 notches), interim watchlist downgrades and a default downgrade to grade 10."""
    seed, obl, orig = cfg.random_seed, o["obligor_id"], o["orig_grade"]
    grade, impaired, events = orig, False, []

    def emit(d, action, new, reason, rkey):
        nonlocal grade
        prev = grade if events else None
        grade = new
        stage = ifrs9_stage(new, orig, _dpd_at(eps, d), impaired)
        events.append({"obligor_id": obl, "effective_date": d, "rating_action": action, "previous_grade": prev,
                       "internal_grade": new, "origination_grade": orig, "ifrs9_stage": stage,
                       "previous_stage": events[-1]["ifrs9_stage"] if events else None,
                       "action_reason": reason, "_rkey": rkey})

    d0, rkey0 = start
    emit(d0, "Initial", orig, "Initial rating at onboarding (new-money approval)" if rkey0
         else "Rating in force at the start of the FY2023 history", rkey0)
    items = [(d, 1, "review", k) for d, k in approvals]
    items += [(default_date, 0, "default", None)] if default_date else []
    m = _add_months(_ms(d0), 1)
    while m <= _ms(o["end"]):
        items.append((_day_in(seed, m + TD(days=3), m + TD(days=14), obl, m.isoformat(), "chk"), 2, "check", None))
        m = _add_months(m, 1)
    last = d0
    for d, _, kind, key in sorted(items, key=lambda x: (x[0], x[1])):
        if d <= d0 or d > as_of:
            continue
        prior = _add_months(_ms(d), -1)
        if kind == "default":
            if not impaired:
                impaired = True
                emit(d, "Downgrade", DEFAULT_GRADE, "Default: payment more than 90 days past due", None)
                last = d
        elif kind == "review":
            gc = 1 + (1 - _avg_health(hser, prior, 6)) * 9   # continuous implied grade
            target = grade
            if not impaired and abs(gc - grade) >= REVIEW_BAND:
                target = min(DEFAULT_GRADE - 1, max(1, int(round(gc))))
                target = max(grade - REVIEW_MAX_MOVE, min(grade + REVIEW_MAX_MOVE, target))
            action = "Upgrade" if target < grade else "Downgrade" if target > grade else "Affirm"
            emit(d, action, target, "Annual review", key)
            last = d
        elif not impaired and _months_between(last, d) >= COOL_OFF_MONTHS:
            target = implied_grade(_avg_health(hser, prior, 3))
            if target - grade >= INTERIM_NOTCHES:
                emit(d, "Downgrade", min(9, target, grade + 3), "Interim watchlist review: deterioration",
                     ("wl", obl, d))
                last = d
    return events


def _sunda_path(cfg, o: Dict, approvals: List[Tuple[D, Tuple]], start, eps: List[Dict], as_of: D) -> List[Dict]:
    """Storyline 1: grade 7 affirmed until a 30+ DPD watchlist review (Stage 2), the 12-Jun-2026
    downgrade 7 -> 9 with Stage 3, then the late FY2025 review affirming 9."""
    s = sl.SUNDA
    orig = s["grade_from"]
    rows = [(start[0], "Initial", orig, 1, "Rating in force at the start of the FY2023 history", start[1])]
    for d, key in approvals:
        if d < s["downgrade_date"]:
            rows.append((d, "Affirm", orig, ifrs9_stage(orig, orig, _dpd_at(eps, d)), "Annual review", key))
        else:
            rows.append((d, "Affirm", s["grade_to"], s["stage_to"], "Annual review (late FY2025 submission)", key))
    rows.append((_bday(s["missed_payment_due"] + TD(days=34)), "Affirm", orig, 2,
                 "Watchlist review: RCF payment over 30 days past due", ("wl", "sunda", 1)))
    rows.append((s["downgrade_date"], "Downgrade", s["grade_to"], s["stage_to"],
                 "Unlikely to pay: missed RCF payment and covenant breach (ND/EBITDA 4.6x vs 4.0x)",
                 ("wl", "sunda", 2)))
    events, prev = [], None
    for d, action, g, stage, reason, key in sorted(rows, key=lambda r: r[0]):
        if d > as_of:
            continue
        events.append({"obligor_id": o["obligor_id"], "effective_date": d, "rating_action": action,
                       "previous_grade": prev["internal_grade"] if prev else None, "internal_grade": g,
                       "origination_grade": orig, "ifrs9_stage": stage,
                       "previous_stage": prev["ifrs9_stage"] if prev else None,
                       "action_reason": reason, "_rkey": key})
        prev = events[-1]
    return events


def rating_at(events: List[Dict], d: D) -> Optional[Dict]:
    """The rating action in force on date d (events sorted by effective_date)."""
    cur = None
    for ev in events:
        if ev["effective_date"] > d:
            break
        cur = ev
    return cur


# ---- people and memos ----------------------------------------------------------------------------
def _staff(people: List[Dict], role: str) -> Tuple[Dict[str, List[str]], List[str]]:
    by_office: Dict[str, List[str]] = defaultdict(list)
    for p in people:
        if p["role"] == role:
            by_office[p["coverage_office"]].append(p["employee_id"])
    return by_office, sorted(i for ids in by_office.values() for i in ids)


def _assign(seed: int, staff, cc: str, *keys) -> str:
    by_office, every = staff
    pool = by_office.get(cc) or every
    return pool[rng.hash64(seed, "crstaff", *keys) % len(pool)]


def _memo(seed: int, kind: str, tone_key: str, key, **slots) -> Tuple[str, str, float]:
    text = MEMOS[(kind, tone_key)].format(**slots)
    tone = ("Positive" if tone_key in ("Upgrade", "Positive") else
            "Negative" if tone_key in ("Downgrade", "Negative", "Distressed", "Declined") else "Neutral")
    names.assert_clean(text)
    score = max(-1.0, min(1.0, TONE_SCORE[tone] + rng.normal(seed, 0.0, 0.12, "memotone", *key)))
    return text, tone, round(score, 3)


def _slots(o: Dict, ratios: Dict, fy: int, grade) -> Dict:
    r = ratios.get((o["obligor_id"], fy)) or ratios.get((o["obligor_id"], min(max(fy, 2023), 2025))) or {}
    return {"name": o["entity"]["short_name"], "sector": o["entity"]["industry_subsector"].lower(), "fy": fy,
            "lev": f"{r.get('Net Debt/EBITDA', 2.5):.1f}", "icr": f"{r.get('ICR', 4.0):.1f}", "grade": grade}


# ---- credit reviews ---------------------------------------------------------------------------------
def _review(o: Dict, rtype: str, **kw) -> Dict:
    base = {"obligor_id": o["obligor_id"], "facility_id": None, "review_type": rtype, "review_fiscal_year": None,
            "due_date": None, "preparation_start_date": None, "submitted_date": None, "approved_date": None,
            "submission_count": None, "outcome": None, "blocker_reason": None, "current_grade": None,
            "recommended_grade": None, "approved_grade": None, "requested_amount_usd": None,
            "proposed_margin_bps": None, "is_pricing_exception": None, "exception_reason": None,
            "memo_excerpt": None, "memo_tone": None, "memo_sentiment_score": None, "_o": o}
    base.update(kw)
    return base


def _process_dates(cfg, key, due: D, ready: Optional[D], h: float, lead=(20, 45)):
    """(prep start, submitted, approved, submissions) for a review due on `due` whose inputs are
    ready on `ready` (None = blocked, never submitted). Dates after as-of are dropped later."""
    seed = cfg.random_seed
    prep = due - TD(days=rng.randint(seed, lead[0], lead[1], "rvlead", *key))
    if ready is None:
        return prep, None, None, None
    prep = max(prep, ready)
    if rng.unit(seed, "rvlate", *key) < LATE_P[0] + LATE_P[1] * (1 - h):
        sub = due + TD(days=max(1, min(90, int(rng.lognormal(seed, math.log(12.0), 0.7, "rvlated", *key)))))
    else:
        sub = due - TD(days=rng.randint(seed, 0, 12, "rvearly", *key))
    sub = _bday(max(sub, prep + TD(days=7)))
    n_sub = 2 if rng.unit(seed, "rvrework", *key) < REWORK_P[0] + REWORK_P[1] * (1 - h) else 1
    lag = rng.randint(seed, 2, 12, "rvappr", *key) + (rng.randint(seed, 5, 15, "rvre", *key) if n_sub == 2 else 0)
    return prep, sub, _bday(sub + TD(days=lag)), n_sub


def _annual_reviews(cfg, profiles: List[Dict], stmts: Dict, health: Dict, sunda_obl: Optional[str],
                    as_of: D) -> List[Dict]:
    """One annual review per obligor x FY of financials while exposure is outstanding; it cannot be
    submitted before that FY is spread (the 128 unspread FY2025 statements block theirs)."""
    seed, out = cfg.random_seed, []
    for o in profiles:
        e, obl = o["entity"], o["obligor_id"]
        off = rng.randint(seed, REVIEW_OFFSET_DAYS[0], REVIEW_OFFSET_DAYS[1], "rvoff", obl)
        if obl == sunda_obl:
            off = (sl.SUNDA["review_due"] - _fye(e, 2025)).days
        for fy in REVIEW_CYCLES:
            due = _fye(e, fy) + TD(days=off)
            due = due if obl == sunda_obl else _bday(due)
            if due < HISTORY_START or not _outstanding(o, due) or o["first_orig"] > due - TD(days=180):
                continue
            st = stmts.get((obl, fy))
            spread = (st["spread_date"] if st else _fye(e, fy) + TD(days=75))
            ready = spread + TD(days=rng.randint(seed, 1, 5, "rvready", obl, fy)) if spread else None
            h = health.get(e["entity_id"], {}).get(_ms(min(due, as_of)), 0.6)
            prep, sub, appr, n_sub = _process_dates(cfg, (obl, fy), due, ready, h)
            sunda = obl == sunda_obl and fy == 2025
            if sunda:
                sub = due + TD(days=sl.SUNDA["review_days_late"])
                prep, appr, n_sub = due - TD(days=38), _bday(sub + TD(days=12)), 1
            out.append(_review(o, "Annual", review_fiscal_year=fy, due_date=due, preparation_start_date=prep,
                               submitted_date=sub, approved_date=appr, submission_count=n_sub,
                               blocker_reason=None if spread else f"Awaiting FY{fy} financial spreading",
                               _key=("annual", obl, fy), _h=h, _sunda="annual" if sunda else None))
    return out


def _new_money_reviews(cfg, profiles: List[Dict], health: Dict, pricing: Dict, as_of: D) -> List[Dict]:
    """A new-money approval before every in-window origination (pricing exceptions from
    fin_deal_pricing), plus a few requests still in approval or declined (no facility booked)."""
    seed, out = cfg.random_seed, []
    for o in profiles:
        hser = health.get(o["entity"]["entity_id"], {})
        for f in o["facilities"]:
            orig = f["origination_date"]
            if not HISTORY_START <= orig <= as_of:
                continue
            key, h = ("nm", f["facility_id"]), hser.get(_ms(orig), 0.6)
            appr = _bday(orig - TD(days=rng.randint(seed, 3, 20, "nmappr", *key)))
            tta = rng.randint(seed, 6, 25, "nmtta", *key) + int(10 * (1 - h)) + (8 if f["limit_usd"] > 1e8 else 0)
            sub = _bday(appr - TD(days=tta))
            dp = pricing.get(f["facility_id"], {})
            out.append(_review(o, "New Money", facility_id=f["facility_id"],
                               preparation_start_date=sub - TD(days=rng.randint(seed, 7, 20, "nmprep", *key)),
                               submitted_date=sub, approved_date=appr,
                               submission_count=2 if rng.unit(seed, "nmre", *key) < 0.12 else 1,
                               requested_amount_usd=round(f["limit_usd"], 2),
                               proposed_margin_bps=int(f["margin_bps"]),
                               is_pricing_exception=dp.get("meets_standalone_hurdle") is False,
                               exception_reason=dp.get("exception_reason"), _key=key, _h=h, _f=f))
        live = [f for f in o["facilities"] if f["origination_date"] <= as_of < f["maturity_date"]]
        u = rng.unit(seed, "nmextra", o["obligor_id"])
        if live and u < NEW_MONEY_PENDING_P + NEW_MONEY_DECLINED_P:
            key, pending = ("nmx", o["obligor_id"]), u < NEW_MONEY_PENDING_P
            back = rng.randint(seed, 5, 40, "nmxs", *key) if pending else rng.randint(seed, 60, 500, "nmxd", *key)
            sub = _bday(as_of - TD(days=back))
            appr = None if pending else _bday(sub + TD(days=rng.randint(seed, 10, 30, "nmxa", *key)))
            top = max(live, key=lambda f: f["limit_usd"])["limit_usd"]
            out.append(_review(o, "New Money", submitted_date=sub, approved_date=appr, submission_count=1,
                               preparation_start_date=sub - TD(days=rng.randint(seed, 7, 20, "nmxp", *key)),
                               requested_amount_usd=round(top * (0.3 + 0.9 * rng.unit(seed, "nmxamt", *key)), 2),
                               proposed_margin_bps=int(live[0]["margin_bps"]),
                               outcome=None if pending else "Declined", _key=key,
                               _h=hser.get(_ms(sub), 0.6), _f=live[0]))
    return out


def _amendment_reviews(cfg, profiles: List[Dict], waivers: List[Dict], as_of: D) -> List[Dict]:
    """One amendment per covenant waiver in credit_covenant_test, plus occasional term amendments
    that change neither limit nor tenor."""
    seed, out = cfg.random_seed, []
    by_fac = {f["facility_id"]: (o, f) for o in profiles for f in o["facilities"]}
    for w in sorted(waivers, key=lambda w: (w["facility_id"], w["test_date"])):
        if w["facility_id"] in by_fac:
            o, f = by_fac[w["facility_id"]]
            key = ("amw", w["facility_id"], w["test_date"].isoformat())
            sub = _bday(w["test_date"] + TD(days=rng.randint(seed, 5, 20, "amws", *key)))
            out.append(_review(o, "Amendment", facility_id=f["facility_id"], submitted_date=sub,
                               preparation_start_date=sub - TD(days=5), submission_count=1,
                               approved_date=_bday(sub + TD(days=rng.randint(seed, 3, 12, "amwa", *key))),
                               _purpose=f"{w['covenant_type']} covenant waiver", _key=key, _h=0.4, _f=f))
    for o in profiles:
        for f in o["facilities"]:
            if not f["has_covenant"]:
                continue
            for y in range(2023, as_of.year + 1):
                key = ("am", f["facility_id"], y)
                d = D(y, 1, 1) + TD(days=int(rng.unit(seed, "amd", *key) * 364))
                if (rng.unit(seed, "am", *key) >= AMENDMENT_P or not HISTORY_START <= d <= as_of
                        or not f["origination_date"] + TD(days=90) <= d < f["maturity_date"]):
                    continue
                sub = _bday(d)
                out.append(_review(o, "Amendment", facility_id=f["facility_id"], submitted_date=sub,
                                   preparation_start_date=sub - TD(days=rng.randint(seed, 4, 12, "amp", *key)),
                                   approved_date=_bday(sub + TD(days=rng.randint(seed, 3, 15, "ama", *key))),
                                   submission_count=1, _purpose=_pick(seed, AMENDMENT_PURPOSES, *key),
                                   _key=key, _h=0.6, _f=f))
    return out


def _watchlist_reviews(cfg, ctx: Dict, ratings: Dict[str, List[Dict]], watchlist: List[Dict],
                       existing: List[Dict], as_of: D) -> List[Dict]:
    """A watchlist review behind each interim downgrade (and Sunda's two), plus one at each current
    EWS watchlist name's entry unless another watchlist review sits within 90 days of it."""
    seed, out = cfg.random_seed, []
    for evs in ratings.values():
        for ev in evs:
            k = ev["_rkey"]
            if not (isinstance(k, tuple) and k[0] == "wl"):
                continue
            d, ks = ev["effective_date"], tuple(map(str, k))
            sub = d - TD(days=rng.randint(seed, 3, 8, "wls", *ks))
            out.append(_review(ctx["by_obl"][ev["obligor_id"]], "Watchlist", submitted_date=sub, approved_date=d,
                               submission_count=1, _key=k, _h=0.3,
                               preparation_start_date=sub - TD(days=rng.randint(seed, 5, 15, "wlp", *ks)),
                               _sunda={1: "watch", 2: "downgrade"}[k[2]] if k[1] == "sunda" else None))
    for w in sorted(watchlist, key=lambda w: w["obligor_id"]):
        o = ctx["by_obl"].get(w["obligor_id"])
        if o is None or w["obligor_id"] == ctx["sunda_obl"]:
            continue
        entry = _add_months(_ms(as_of), -(int(w["months_on_watch"]) - 1))
        d = _day_in(seed, entry + TD(days=10), min(as_of, entry + TD(days=25)), "ewsrv", w["obligor_id"])
        near = any(r["review_type"] == "Watchlist" and r["obligor_id"] == w["obligor_id"]
                   and abs((r["approved_date"] - d).days) <= 90 for r in existing + out)
        if not near and d <= as_of:
            sub = d - TD(days=rng.randint(seed, 3, 8, "ewss", w["obligor_id"]))
            out.append(_review(o, "Watchlist", submitted_date=sub, approved_date=d, submission_count=1,
                               preparation_start_date=sub - TD(days=6), _key=("ews", w["obligor_id"]), _h=0.3))
    return out


def _review_status(r: Dict, as_of: D) -> str:
    sub, appr = r["submitted_date"], r["approved_date"]
    if appr and appr <= as_of:
        return "Declined" if r["outcome"] == "Declined" else "Approved"
    if sub and sub <= as_of:
        return "Submitted - Awaiting Approval"
    if r["due_date"] and r["due_date"] < as_of:
        return "Overdue"
    prep = r["preparation_start_date"]
    return "In Preparation" if prep and prep <= as_of else "Not Started"


def _finish_review(cfg, r: Dict, rating_ev: Optional[Dict], grade_now: Optional[int], ratios: Dict,
                   ews_band: Optional[str], as_of: D) -> Dict:
    """Status as of today, grades and the memo excerpt (only for submitted reviews)."""
    seed, o, key = cfg.random_seed, r["_o"], r["_key"]
    status = _review_status(r, as_of)
    sub = r["submitted_date"] if r["submitted_date"] and r["submitted_date"] <= as_of else None
    appr = r["approved_date"] if status in ("Approved", "Declined") else None
    r.update(status=status, submitted_date=sub, approved_date=appr,
             submission_count=r["submission_count"] if sub else None)
    if r["preparation_start_date"] and r["preparation_start_date"] > as_of:
        r["preparation_start_date"] = None
    prep = r["preparation_start_date"]
    r["days_in_preparation"] = ((sub or as_of) - prep).days if prep else None
    late_ref = sub or (as_of if status == "Overdue" else None)
    r["days_late"] = max(0, (late_ref - r["due_date"]).days) if r["due_date"] and late_ref else None
    if rating_ev:
        r["current_grade"] = rating_ev["previous_grade"] or rating_ev["internal_grade"]
        r["recommended_grade"] = r["approved_grade"] = rating_ev["internal_grade"]
    else:
        r["current_grade"] = grade_now
        if sub:
            r["recommended_grade"] = grade_now
            r["approved_grade"] = grade_now if appr and status == "Approved" else None
    if r["review_type"] in ("Annual", "Watchlist") and appr and status == "Approved":
        weak = (rating_ev and rating_ev["rating_action"] == "Downgrade") or r["_h"] < 0.35
        r["outcome"] = "Approved with Conditions" if weak else "Approved"
    elif status == "Approved":
        exception = r.get("is_pricing_exception") or r.get("_distressed")
        r["outcome"] = "Approved with Conditions" if exception else "Approved"
    if not sub:
        return r
    fy = r["review_fiscal_year"] or fiscal.fiscal_year(sub) - 1
    slots = _slots(o, ratios, fy, r["recommended_grade"] or grade_now or o["orig_grade"])
    if r.get("_sunda"):
        r["memo_excerpt"], r["memo_tone"] = SUNDA_MEMOS[r["_sunda"]], "Negative"
        r["memo_sentiment_score"] = round(-0.7 + 0.1 * rng.unit(seed, "sundamemo", r["_sunda"]), 3)
        return r
    if r["review_type"] == "Annual":
        action = rating_ev["rating_action"] if rating_ev else "Affirm"
        tone = (action if action in ("Upgrade", "Downgrade") else
                "Positive" if r["_h"] >= 0.55 else "Neutral" if r["_h"] >= 0.35 else "Negative")
        memo = _memo(seed, "Annual", tone, key, **slots)
    elif r["review_type"] == "New Money":
        f = r["_f"]
        extra = dict(amount=f"{r['requested_amount_usd'] / 1e6:.1f}", ftype=f["facility_type"],
                     purpose=_pick(seed, NEW_MONEY_PURPOSES, *key), margin=r["proposed_margin_bps"],
                     reason=(r["exception_reason"] or "relationship").lower())
        tone = ("Declined" if r["outcome"] == "Declined" else "Distressed" if r.get("_distressed")
                else "Exception" if r.get("is_pricing_exception") else "Neutral")
        memo = _memo(seed, "New Money", tone, key, **{**slots, **extra})
    elif r["review_type"] == "Watchlist":
        tone = "Downgrade" if rating_ev and rating_ev["rating_action"] == "Downgrade" else "Negative"
        memo = _memo(seed, "Watchlist", tone, key, band=ews_band or "Amber", **slots)
    else:
        memo = _memo(seed, "Amendment", "Neutral", key, purpose=r["_purpose"].lower(),
                     ftype=r["_f"]["facility_type"], **slots)
    r["memo_excerpt"], r["memo_tone"], r["memo_sentiment_score"] = memo
    return r


# ---- spreading tasks ------------------------------------------------------------------------------
def spreading_tasks(cfg, stmts: Dict, obl_country: Dict[str, str], analysts, as_of: D) -> List[Dict]:
    """One task per obligor x FY statement; spread dates and the not-yet-spread backlog come
    straight from credit_financial_statement."""
    seed, out = cfg.random_seed, []
    for (obl, fy) in sorted(stmts):
        st, key = stmts[(obl, fy)], (obl, fy)
        ye, spread = st["fye"], st["spread_date"]
        if spread:
            received = max(ye + TD(days=20), spread - TD(days=rng.randint(seed, 5, 25, "sprcv", *key)))
        else:
            received = min(as_of - TD(days=7), ye + TD(days=rng.randint(seed, 40, 100, "sprcv2", *key)))
        due = received + TD(days=SPREAD_SLA_DAYS)
        cc = obl_country.get(obl, "SG")
        analyst, checker = _assign(seed, analysts, cc, "an", obl), _assign(seed, analysts, cc, "chk", obl, fy)
        if checker == analyst:
            checker = analysts[1][(analysts[1].index(analyst) + 1) % len(analysts[1])]
        checked = spread + TD(days=rng.randint(seed, 1, 8, "spchk", *key)) if spread else None
        checked = checked if checked and checked <= as_of else None
        if spread:
            status = "Checked" if checked else "Spread - Pending Check"
        else:
            status = "In Progress" if rng.unit(seed, "spstart", *key) < 0.4 else "Received - Not Started"
        out.append({"task_id": f"SPR-{obl.split('-')[-1]}-FY{fy}", "obligor_id": obl, "fiscal_year": fy,
                    "fiscal_year_label": f"FY{fy}", "fiscal_year_end": _iso(ye),
                    "statement_basis": "Audited" if st["is_audited"] else "Unaudited / Management",
                    "received_date": _iso(received), "due_date": _iso(due), "spread_date": _iso(spread),
                    "checked_date": _iso(checked), "analyst_id": analyst, "checker_id": checker if spread else None,
                    "status": status, "days_to_spread": (spread - received).days if spread else None,
                    "days_overdue": max(0, ((spread or as_of) - due).days), "is_overdue": not spread and as_of > due})
    return out


# ---- capital ---------------------------------------------------------------------------------------
def capital_rows(cfg, profiles: List[Dict], ratings: Dict[str, List[Dict]], eps_by_obl: Dict[str, List[Dict]],
                 as_of: D) -> List[Dict]:
    """fin_capital_allocation: per obligor per month-end from FY2024 (EAD, RWA, PD, LGD, stage, ECL)."""
    coe = float(cfg.thresholds.get("roe_hurdle", 0.10))
    out = []
    for o in profiles:
        obl, evs = o["obligor_id"], ratings.get(o["obligor_id"], [])
        rows_by_m: Dict[D, list] = defaultdict(list)
        for f in o["facilities"]:
            for m, lim, dr in f["rows"]:
                if _add_months(FIN_START, -1) <= m <= _ms(as_of):
                    rows_by_m[m].append((f, lim, dr))
        prev_ecl = 0.0
        for m in sorted(rows_by_m):
            me = _me(m)
            rt = rating_at(evs, me)
            if rt is None:
                continue
            grade = rt["internal_grade"]
            dpd = _dpd_at(eps_by_obl.get(obl, []), me)
            stage = ifrs9_stage(grade, rt["origination_grade"], dpd, rt["ifrs9_stage"] == 3)
            facs = rows_by_m[m]
            drawn, lim = sum(x[2] for x in facs), sum(x[1] for x in facs)
            ead = drawn + prof.UNDRAWN_CCF * max(0.0, lim - drawn)
            lgd = sum(x[1] * prof.lgd_of(x[0]["security_type"]) for x in facs) / lim if lim else 0.45
            life = sum(max(x[0]["maturity_date"] - me, TD(0)).days / 365.25 * x[2] for x in facs)
            tenor = max(TENOR_RANGE[0], min(TENOR_RANGE[1], life / drawn if drawn else 1.0))
            pd12 = 1.0 if stage == 3 else prof.pd_of(grade)
            pdl = 1.0 if stage == 3 else 1 - (1 - pd12) ** tenor
            ecl12, ecll = pd12 * lgd * ead, pdl * lgd * ead
            ecl = ecl12 if stage == 1 else ecll
            rwa = ead * prof.risk_weight(grade)
            alloc = rwa * TARGET_CET1
            row = {"obligor_id": obl, "month": m, "as_of_date": me, "fiscal_year": _quarter(m)[0],
                   "fiscal_quarter": _quarter(m)[1], "internal_grade": grade,
                   "rating_equivalent": rating_equivalent(grade), "ifrs9_stage": stage, "days_past_due_max": dpd,
                   "dpd_bucket": dpd_bucket(dpd), "drawn_usd": drawn, "limit_usd": lim, "ead_usd": ead,
                   "ccf": prof.UNDRAWN_CCF, "risk_weight": prof.risk_weight(grade), "rwa_usd": rwa,
                   "pd_12m": pd12, "pd_lifetime": pdl, "lgd": lgd, "remaining_tenor_years": tenor,
                   "ecl_12m_usd": ecl12, "ecl_lifetime_usd": ecll, "ecl_usd": ecl, "ecl_change_usd": ecl - prev_ecl,
                   "expected_loss_usd": prof.pd_of(grade) * lgd * ead / 12.0,
                   "economic_capital_usd": ead * prof.econ_capital_rate(grade),
                   "book_equity_usd": ead * prof.BOOK_EQUITY_RATE, "allocated_capital_usd": alloc,
                   "cost_of_capital_usd": alloc * coe / 12.0, "is_individually_assessed": False}
            prev_ecl = ecl
            if m >= FIN_START:
                out.append(row)
    return out


def apply_sunda_ecl(cfg, cap: List[Dict], sunda_obls: List[str], lead_obl: str) -> Dict:
    """Storyline 1: from the downgrade month the lead's Stage 3 ECL is individually assessed, its
    LGD solving for the scripted group ECL rise (capped at 95% of EAD)."""
    s = sl.SUNDA
    jun, may = _ms(s["downgrade_date"]), _add_months(_ms(s["downgrade_date"]), -1)
    by = {(r["obligor_id"], r["month"]): r for r in cap if r["obligor_id"] in sunda_obls}
    lead_jun = by.get((lead_obl, jun))
    if not lead_jun:
        return {}
    ecl_may = sum(by[(o, may)]["ecl_usd"] for o in sunda_obls if (o, may) in by)
    others_jun = sum(by[(o, jun)]["ecl_usd"] for o in sunda_obls if o != lead_obl and (o, jun) in by)
    need, ead = ecl_may + s["ecl_delta_usd"] - others_jun, lead_jun["ead_usd"]
    lgd = max(lead_jun["lgd"], min(LGD_S3_CAP, need / ead)) if ead else lead_jun["lgd"]
    prev = None
    for r in sorted((r for r in cap if r["obligor_id"] == lead_obl), key=lambda r: r["month"]):
        if r["month"] >= jun and r["ifrs9_stage"] == 3:
            r.update(lgd=lgd, ecl_12m_usd=r["ead_usd"] * lgd, ecl_lifetime_usd=r["ead_usd"] * lgd,
                     ecl_usd=r["ead_usd"] * lgd, is_individually_assessed=True)
        if prev is not None:
            r["ecl_change_usd"] = r["ecl_usd"] - prev
        prev = r["ecl_usd"]
    jun_total = sum(by[(o, jun)]["ecl_usd"] for o in sunda_obls if (o, jun) in by)
    return {"lgd": lgd, "ecl_may": ecl_may, "ecl_jun": jun_total, "delta": jun_total - ecl_may, "lead_ead_jun": ead}


# ---- revenue and cost -------------------------------------------------------------------------------
def client_anchors(entities: List[Dict], maps: Dict[str, Dict[str, str]]) -> Dict[str, Tuple[str, str]]:
    """entity -> the finance engine's client reference: obligor if a borrower, else core customer,
    else treasury counterparty, else trade party (deposits / payments / FX / trade roll up to it)."""
    out = {}
    for e in entities:
        for system in ANCHOR_ORDER:
            sid = maps.get(system, {}).get(e["entity_id"])
            if sid:
                out[e["entity_id"]] = (system, sid)
                break
    return out


def _bottom_up(cfg, entities, maps, book, inp, as_of) -> Dict[Tuple, Dict]:
    """{(entity, month, family): {nii, fee, trading, basis}} from FY2024 with profitability's rates."""
    cells: Dict[Tuple, Dict] = defaultdict(lambda: {"nii": 0.0, "fee": 0.0, "trading": 0.0, "basis": 0.0})
    am = _ms(as_of)
    for f in book:
        for m, lim, dr in f["rows"]:
            if FIN_START <= m <= am:
                c = cells[(f["entity_id"], m, "Corporate Lending")]
                c["nii"] += dr * f["margin_bps"] / 1e4 / 12.0
                c["lim"] = c.get("lim", 0.0) + lim
                c["basis"] += dr
    for c in [c for (_, _, fam), c in cells.items() if fam == "Corporate Lending"]:
        c["fee"] = (max(0.0, c["lim"] - c["basis"]) * prof.COMMITMENT_FEE_BPS / 1e4 / 12.0
                    + c["lim"] * prof.ARRANGEMENT_FEE_BPS / 1e4 / prof.ARRANGEMENT_TENOR_YEARS / 12.0)
    inv = {s: {v: k for k, v in m.items()} for s, m in maps.items()}
    spreads = (("Cash", 0, prof.DEPOSIT_SPREAD_CASA), ("Liquidity", 1, prof.DEPOSIT_SPREAD_TD))
    for (cust, m), bal in inp["deposits"].items():
        eid = inv["core_customer"].get(cust)
        if eid and FIN_START <= m <= am:
            for fam, i, spread in spreads:
                if bal[i] > 0:
                    cells[(eid, m, fam)]["nii"] += bal[i] * spread / 12.0
                    cells[(eid, m, fam)]["basis"] += bal[i]
    for (cust, m), (n_pay, xb) in inp["payments"].items():
        eid = inv["core_customer"].get(cust)
        if eid and FIN_START <= m <= am:
            c = cells[(eid, m, "Payments")]
            c["fee"] += n_pay * prof.PAYMENT_FEE_USD + xb * prof.CROSS_BORDER_BPS / 1e4
            c["basis"] += n_pay
    for src, key, fam, kind in (("tsy_counterparty", "fx_revenue", "FX", "trading"),
                                ("trade_party", "trade_fees", "Trade Finance", "fee")):
        for (sid, m), (rev, vol) in inp[key].items():
            eid = inv[src].get(sid)
            if eid and FIN_START <= m <= am:
                cells[(eid, m, fam)][kind] += rev
                cells[(eid, m, fam)]["basis"] += vol
    return cells


def _scale(values: List[Tuple[Dict, str]], target: float) -> None:
    """Scale cells' field so they sum to `target` exactly (cents; residual on the largest cell)."""
    if not values:
        return
    tot = sum(c[k] for c, k in values)
    for c, k in values:
        c[k] = round(target / len(values) if tot <= 0 else c[k] * target / tot, 2)
    big = max(values, key=lambda ck: ck[0][ck[1]])
    big[0][big[1]] = round(big[0][big[1]] + round(target, 2) - round(sum(c[k] for c, k in values), 2), 2)


def _reconcile(inp: Dict, cells: Dict, cost: Dict, months_by_e: Dict, obl_ent: Dict, anchors: Dict) -> set:
    """Allocate each fin_relationship_pnl obligor-quarter's lending / deposit / payment revenue and
    cost to its months in proportion to the bottom-up monthly values. Returns the reconciled cells."""
    lines = ((("Corporate Lending",), ("nii", "fee"), "rev_lending"), (DEPOSIT_FAMILIES, ("nii",), "rev_deposits"),
             (("Payments",), ("fee",), "rev_payments"))
    recon = set()
    for p in inp["pnl"]:
        eid = obl_ent.get(p["obligor_id"])
        if eid is None or anchors.get(eid, ("", ""))[0] != "credit_obligor":
            continue
        qm = [m for m in months_by_e.get(eid, ()) if _quarter(m) == (p["fiscal_year"], p["fiscal_quarter"])]
        if not qm:
            continue
        for fams, fields, target in lines:
            _scale([(cells[(eid, m, fam)], k) for m in qm for fam in fams if (eid, m, fam) in cells
                    for k in fields], p[target])
            recon.update((eid, m, fam) for m in qm for fam in fams)
        _scale([(cost[(eid, m)], k) for m in qm for k in ("base", "acct", "pay")], p["cost_to_serve"])
        recon.update((eid, m, "cost") for m in qm)
    return recon


def revenue_and_cost(cfg, entities: List[Dict], maps: Dict, book: List[Dict], inp: Dict, as_of: D):
    """fin_client_revenue + fin_cost_allocation rows; obligor-quarters in fin_relationship_pnl are
    allocated so lending / deposit / payment revenue and cost reconcile exactly."""
    ents = {e["entity_id"]: e for e in entities}
    anchors = client_anchors(entities, maps)
    cells = _bottom_up(cfg, entities, maps, book, inp, as_of)
    cust = maps.get("core_customer", {})
    active: Dict[str, set] = defaultdict(set)
    for (eid, m, _), c in cells.items():
        if c["nii"] + c["fee"] + c["trading"] > 0:
            active[eid].add(m)
    # sorted, never set order: date hashes are salted per process (D08 determinism)
    months_by_e = {eid: sorted(ms) for eid, ms in active.items()}
    cost: Dict[Tuple, Dict] = {}   # bottom-up monthly cost: coverage base + accounts + payment processing
    for eid, months in months_by_e.items():
        base = prof.COST_BASE_USD.get(ents[eid]["relationship_tier"], 60000.0) / 12.0
        acct = inp["n_accounts"].get(cust.get(eid), 0) * prof.COST_PER_ACCOUNT_USD / 12.0
        for m in months:
            pay = cells.get((eid, m, "Payments"), {}).get("basis", 0.0) * prof.COST_PER_PAYMENT_USD
            cost[(eid, m)] = {"base": base, "acct": acct, "pay": pay}
    obl_ent = {v: k for k, v in maps.get("credit_obligor", {}).items()}
    recon = _reconcile(inp, cells, cost, months_by_e, obl_ent, anchors)
    rev_rows, by_em = [], defaultdict(list)
    for (eid, m, fam), c in cells.items():
        tot = c["nii"] + c["fee"] + c["trading"]
        if tot <= 0 or eid not in anchors:
            continue
        by_em[(eid, m)].append((fam, tot))
        fy, fq = _quarter(m)
        rev_rows.append({"client_source_system": anchors[eid][0], "client_source_id": anchors[eid][1], "month": m,
                         "fiscal_year": fy, "fiscal_quarter": fq, "business_line": FAMILY_LINE[fam],
                         "product_family": fam, "nii_usd": round(c["nii"], 2), "fee_usd": round(c["fee"], 2),
                         "trading_usd": round(c["trading"], 2), "total_revenue_usd": round(tot, 2),
                         "activity_basis_usd": round(c["basis"], 2), "is_pnl_reconciled": (eid, m, fam) in recon})
    cost_rows = []
    for (eid, m), fams in sorted(by_em.items()):
        if (eid, m) in cost:
            cost_rows += _cost_split(anchors[eid], m, fams, cost[(eid, m)], (eid, m, "cost") in recon)
    return rev_rows, cost_rows


def _cost_split(anchor: Tuple[str, str], m: D, fams: List[Tuple[str, float]], k: Dict,
                reconciled: bool) -> List[Dict]:
    """One client-month's cost to serve by product family: the coverage base split into RM, direct
    product and HO cost by revenue share; operations = account upkeep on the deposit families and
    payment processing on Payments. Rows sum exactly (to the cent) to base + accounts + payments."""
    total_rev = sum(t for _, t in fams)
    dep_rev = sum(t for f, t in fams if f in DEPOSIT_FAMILIES)
    has_pay = any(f == "Payments" for f, _ in fams)
    fy, fq = _quarter(m)
    rows = []
    for fam, t in sorted(fams):
        share = t / total_rev if total_rev else 1.0 / len(fams)
        ops = (k["pay"] if fam == "Payments" else 0.0 if has_pay else k["pay"] * share)
        ops += (k["acct"] * t / dep_rev if fam in DEPOSIT_FAMILIES else 0.0) if dep_rev else k["acct"] * share
        rm, direct = round(k["base"] * RM_SHARE * share, 2), round(k["base"] * DIRECT_SHARE * share, 2)
        ho, ops = round(k["base"] * (1 - RM_SHARE - DIRECT_SHARE) * share, 2), round(ops, 2)
        rows.append({"client_source_system": anchor[0], "client_source_id": anchor[1], "month": m, "fiscal_year": fy,
                     "fiscal_quarter": fq, "product_family": fam, "direct_cost_usd": direct, "rm_cost_usd": rm,
                     "operations_cost_usd": ops, "ho_allocation_usd": ho,
                     "total_cost_usd": round(direct + rm + ops + ho, 2), "is_pnl_reconciled": reconciled})
    resid = round(round(k["base"] + k["acct"] + k["pay"], 2) - sum(r["total_cost_usd"] for r in rows), 2)
    big = max(rows, key=lambda r: (r["total_cost_usd"], r["product_family"]))
    big["ho_allocation_usd"] = round(big["ho_allocation_usd"] + resid, 2)
    big["total_cost_usd"] = round(big["total_cost_usd"] + resid, 2)
    return rows


# ---- cash-flow liquidity events ------------------------------------------------------------------------
def _cf_candidates(cfg, forecasts: List[Dict], actuals: Dict, as_of: D) -> List[Dict]:
    """Per forecast: predicted net = forecast inflows - the 3 prior months' average outflows, and its
    size relative to the client's typical gross monthly flow."""
    hist: Dict[str, Dict[D, Tuple[float, float]]] = defaultdict(dict)
    for (cust, m), v in actuals.items():
        hist[cust][m] = v
    out = []
    for fc in forecasts:
        cust, run, tgt = fc["cust_no"], fc["forecast_run_date"], fc["target_month"]
        h = hist.get(cust, {})
        prior3 = [h[m] for m in (_add_months(run, -k) for k in (1, 2, 3)) if m in h]
        prior6 = [h[m] for m in (_add_months(run, -k) for k in range(1, 7)) if m in h]
        if len(prior3) < 2 or tgt > _ms(as_of):
            continue
        exp_out = sum(o for _, o in prior3) / len(prior3)
        gross = sum(i + o for i, o in prior6) / len(prior6)
        net = fc["forecast_inflows_usd"] - exp_out
        out.append({"cust_no": cust, "forecast_run_date": run, "target_month": tgt,
                    "model_version": fc["model_version"], "forecast_inflows_usd": fc["forecast_inflows_usd"],
                    "expected_outflows_usd": exp_out, "predicted_net_usd": net, "rel": net / gross if gross else 0.0})
    return out


def _rcf_state(f: Dict, m: D) -> Optional[Tuple[float, float]]:
    """(limit, drawn) at the previous month-end if the RCF also has a row in month m."""
    rows = {mm: (lim, dr) for mm, lim, dr in f["rows"]}
    return rows.get(_add_months(m, -1)) if m in rows else None


def plan_liquidity_events(cfg, forecasts: List[Dict], actuals: Dict, cust_entity: Dict[str, str],
                          rcfs: Dict[str, List[Dict]], arrears_fac: set, as_of: D,
                          scripted: frozenset = frozenset()):
    """Predicted shortfalls / surpluses (cf_forecast vs the trailing 3-month outflows). Storyline 9:
    exactly 23 shortfalls Jun-Sep 2026 (other storylines' clients excluded), 15 on RCF clients whose
    drawdown is forced within 10 days, 8 kept clear of RCF drawdowns for 10 days. Returns (events,
    force, block, materiality threshold)."""
    seed = cfg.random_seed
    win = sl.CASHFLOW["window"]
    cands = _cf_candidates(cfg, forecasts, actuals, as_of)
    for c in cands:
        tm = c["target_month"]
        c["event_date"] = _bday(D(tm.year, tm.month, 3 + rng.hash64(seed, "cfday", c["cust_no"], tm.isoformat()) % 14))
    in_win = lambda c: win[0] <= c["event_date"] <= win[1]  # noqa: E731
    # window shortfalls, material ones (|net| >= 50% of typical gross flow) first, largest first
    short = sorted((c for c in cands if in_win(c) and c["predicted_net_usd"] < 0 and c["cust_no"] not in scripted),
                   key=lambda c: (c["rel"] > -REL_MIN, c["predicted_net_usd"], c["cust_no"]))
    material = [c for c in short if c["rel"] <= -REL_MIN]
    n_short, n_rcf = sl.CASHFLOW["predicted_shortfalls"], sl.CASHFLOW["rcf_drawdowns_within_10d"]
    # the materiality threshold elsewhere: what a plain rule would need to flag ~23 in the window
    threshold = -material[min(n_short, len(material)) - 1]["predicted_net_usd"] if material else 1e6
    events, force, block, used = [], defaultdict(list), defaultdict(list), set()

    def eligible(c):
        for f in rcfs.get(cust_entity.get(c["cust_no"]), []):
            st = _rcf_state(f, c["target_month"])
            if (st and st[0] - st[1] >= max(0.05 * st[0], 50_000.0) and f["facility_id"] not in arrears_fac
                    and f["maturity_date"] > c["event_date"] + TD(days=12)):
                return f, st[0] - st[1]
        return None

    if sl.on(cfg, sl.CASHFLOW_UPGRADE):
        picked = []
        for c in sorted(short, key=lambda c: (c["predicted_net_usd"], c["cust_no"])):
            hit = eligible(c) if c["cust_no"] not in used else None
            if hit and len(picked) < n_rcf:  # the 15: largest RCF-client shortfalls, drawdown within 10 days
                f, headroom = hit
                ed = c["event_date"]
                d = _day_in(seed, ed + TD(days=1), min(ed + TD(days=9), _me(ed) - TD(days=12)), c["cust_no"], "cfdd")
                force[f["facility_id"]].append((d, min(-c["predicted_net_usd"], 0.8 * headroom)))
                picked.append(c)
                used.add(c["cust_no"])
        for c in short:  # 8 more of the largest material shortfalls, no RCF drawdown for 10 days
            if len(picked) >= n_short:
                break
            if c["cust_no"] in used:
                continue
            for f in rcfs.get(cust_entity.get(c["cust_no"]), []):
                block[f["facility_id"]].append((c["event_date"], c["event_date"] + TD(days=10)))
            picked.append(c)
            used.add(c["cust_no"])
        events += [dict(c, event_type="Predicted Shortfall") for c in picked]
    for c in cands:  # the same materiality threshold everywhere else (and for surpluses)
        if in_win(c) and sl.on(cfg, sl.CASHFLOW_UPGRADE):
            pass
        elif c["predicted_net_usd"] <= -threshold and c["rel"] <= -REL_MIN:
            events.append(dict(c, event_type="Predicted Shortfall"))
            continue
        if c["predicted_net_usd"] >= max(SURPLUS_MIN_USD, threshold) and c["rel"] >= REL_MIN:
            events.append(dict(c, event_type="Predicted Surplus"))
    return events, force, block, threshold


def hk_casa_events(cfg, entities: List[Dict], cust: Dict[str, str]) -> List[Dict]:
    """Storyline 5: May-2026 forecasts flag the three HK electronics subsidiaries' surplus cash
    (~USD 900m in total); the TDs follow 40+ days later (Jun-Aug)."""
    s, out = sl.HK_CASA_SCRIPT, []
    trio = [e for e in sl.group_entities(entities, s["key"]) if e["entity_id"] in cust]
    for i, (e, share) in enumerate(zip(trio[:3], (0.36, 0.34, 0.30))):
        d = _bday(s["surplus_forecast_month"] + TD(days=6 + 5 * i))
        amount = s["moved_usd"] * share
        td_date = _bday(d + TD(days=s["signal_action_lag_days"] + 3 + 11 * i))
        out.append({"cust_no": cust[e["entity_id"]], "event_type": "Predicted Surplus", "event_date": d,
                    "forecast_run_date": _add_months(s["surplus_forecast_month"], -1),
                    "target_month": s["surplus_forecast_month"], "model_version": "v1",
                    "forecast_inflows_usd": None, "expected_outflows_usd": None, "predicted_net_usd": amount,
                    "rel": None, "_hk_action": (td_date, amount)})
    return out


def _event_action(ev: Dict, facs: List[Dict], draws: Dict, book_by_fac: Dict, deposits: Dict, as_of: D):
    """(action, date, amount, facility, schedule event, annual revenue) for one liquidity event."""
    amount = abs(ev["predicted_net_usd"])
    if ev["event_type"] == "Predicted Shortfall":
        end = ev["event_date"] + TD(days=ACTION_WINDOW_DAYS)
        hits = sorted((r for f in facs for r in draws.get(f["facility_id"], [])
                       if ev["event_date"] <= r["event_date"] <= end),
                      key=lambda r: (r["event_date"], r["schedule_event_id"]))
        if hits:
            r = hits[0]
            revenue = r["amount_usd"] * book_by_fac[r["facility_id"]]["margin_bps"] / 1e4
            return "RCF Drawdown", r["event_date"], r["amount_usd"], r["facility_id"], r["schedule_event_id"], revenue
    elif "_hk_action" in ev:
        d, amt = ev["_hk_action"]
        return "TD Placed", d, amt, None, None, amt * prof.DEPOSIT_SPREAD_TD
    else:
        m, cust = ev["target_month"], ev["cust_no"]
        rise = deposits.get((cust, m), (0.0, 0.0))[1] - deposits.get((cust, _add_months(m, -1)), (0.0, 0.0))[1]
        if rise >= SURPLUS_TD_SHARE * amount and _me(m) <= as_of:
            return "TD Placed", _bday(_me(m)), rise, None, None, rise * prof.DEPOSIT_SPREAD_TD
    return "None", None, None, None, None, None


def link_liquidity_events(cfg, events: List[Dict], schedule: List[Dict], cust_entity: Dict[str, str],
                          rcfs: Dict[str, List[Dict]], book_by_fac: Dict[str, Dict], deposits: Dict,
                          as_of: D) -> List[Dict]:
    """Action taken: the first RCF drawdown within 30 days of a shortfall, a TD-balance rise after a
    surplus (HK storyline: its scripted TD placement). Adds headroom, days to action and revenue."""
    draws: Dict[str, List[Dict]] = defaultdict(list)
    for r in schedule:
        if r["event_type"] == "Drawdown" and r["purpose"] in ("Utilisation request", "Liquidity shortfall cover"):
            draws[r["facility_id"]].append(r)
    out = []
    for ev in sorted(events, key=lambda e: (e["event_date"], e["cust_no"], e["event_type"])):
        facs = rcfs.get(cust_entity.get(ev["cust_no"]), [])
        undrawn = sum(max(0.0, st[0] - st[1]) for st in (_rcf_state(f, ev["target_month"]) for f in facs) if st)
        action, adate, aamt, fac, sched, revenue = _event_action(ev, facs, draws, book_by_fac, deposits, as_of)
        if adate and adate > as_of:
            action, adate, aamt, fac, sched, revenue = "None", None, None, None, None, None
        open_ = ev["event_date"] + TD(days=ACTION_WINDOW_DAYS) > as_of
        out.append({"cust_no": ev["cust_no"], "event_type": ev["event_type"], "event_date": ev["event_date"],
                    "forecast_run_date": ev["forecast_run_date"], "forecast_target_month": ev["target_month"],
                    "model_version": ev["model_version"],
                    "horizon_days": (ev["event_date"] - ev["forecast_run_date"]).days,
                    "forecast_inflows_usd": _r2(ev["forecast_inflows_usd"]),
                    "expected_outflows_usd": _r2(ev["expected_outflows_usd"]),
                    "predicted_net_usd": round(ev["predicted_net_usd"], 2),
                    "predicted_amount_usd": round(abs(ev["predicted_net_usd"]), 2),
                    "severity_ratio": None if ev["rel"] is None else round(abs(ev["rel"]), 4),
                    "has_rcf": bool(facs), "undrawn_rcf_usd": round(undrawn, 2), "action_taken": action,
                    "action_date": adate, "days_to_action": (adate - ev["event_date"]).days if adate else None,
                    "action_amount_usd": _r2(aamt), "linked_facility_id": fac, "linked_schedule_event_id": sched,
                    "est_action_revenue_usd": _r2(revenue),
                    "event_status": "Actioned" if adate else "Open" if open_ else "Closed - No Action"})
    for n, r in enumerate(out, start=1):
        r["event_id"] = f"CFE-{n:05d}"
    return out


# ---- assembly ----------------------------------------------------------------------------------
def _book_context(cfg, entities: List[Dict], xref: List[Dict], inp: Dict, as_of: D) -> Dict:
    """Ids, the lending book, arrears episodes and the rated borrowers (shared by every step)."""
    maps = id_maps(xref)
    obl_map = maps.get("credit_obligor", {})
    book = facility_book(inp["terms"], inp["balances"], {v: k for k, v in obl_map.items()})
    episodes = arrears_episodes(cfg, book, inp["health"], entities, as_of)
    eps_by_obl: Dict[str, List[Dict]] = defaultdict(list)
    for ep in episodes:
        eps_by_obl[ep["obligor_id"]].append(ep)
    ents = {e["entity_id"]: e for e in entities}
    profiles = _obligor_profiles(cfg, book, ents, as_of)
    sunda = sl.lead_entity(entities, sl.SUNDA["key"]) if sl.on(cfg, sl.SUNDA_EWS) else None
    return {"maps": maps, "obl_map": obl_map, "book": book, "fac": {f["facility_id"]: f for f in book},
            "episodes": episodes, "eps_by_obl": eps_by_obl, "ents": ents, "profiles": profiles,
            "by_obl": {o["obligor_id"]: o for o in profiles}, "health": inp["health"],
            "sunda_obl": obl_map.get(sunda["entity_id"]) if sunda else None}


def _ratings(cfg, ctx: Dict, reviews: List[Dict], as_of: D) -> Dict[str, List[Dict]]:
    """Rating paths: initial rating at the first new-money approval (new borrowers) or the start of
    the history, then annual-review actions, interim downgrades and defaults; Sunda scripted."""
    nm_first: Dict[str, Tuple[D, Tuple]] = {}
    approvals: Dict[str, List[Tuple[D, Tuple]]] = defaultdict(list)
    for r in reviews:
        o = r["_o"]
        if (r["review_type"] == "New Money" and r["facility_id"] and o["first_orig"] >= HISTORY_START
                and r["_f"]["origination_date"] == o["first_orig"]):
            nm_first[o["obligor_id"]] = (min(r["approved_date"], o["first_orig"]), r["_key"])
        if r["review_type"] == "Annual" and r["approved_date"] and r["approved_date"] <= as_of:
            approvals[r["obligor_id"]].append((r["approved_date"], r["_key"]))
    defaults = {ep["obligor_id"]: ep["due_date"] + TD(days=DEFAULT_DPD + 1)
                for ep in ctx["episodes"] if ep["kind"] == "Default"}
    out = {}
    for o in ctx["profiles"]:
        obl = o["obligor_id"]
        start = nm_first.get(obl) or (max(HISTORY_START, o["first_orig"]), None)
        eps, appr = ctx["eps_by_obl"].get(obl, []), sorted(approvals.get(obl, []))
        if obl == ctx["sunda_obl"]:
            out[obl] = _sunda_path(cfg, o, appr, start, eps, as_of)
        else:
            hser = ctx["health"].get(o["entity"]["entity_id"], {})
            out[obl] = _rating_path(cfg, o, hser, appr, start, defaults.get(obl), eps, as_of)
    return out


def _finalise_reviews(cfg, ctx: Dict, reviews: List[Dict], ratings: Dict[str, List[Dict]], inp: Dict,
                      analysts, approvers, as_of: D) -> Dict:
    """Status, grades and memos as of today; review ids in date order; analyst and approver.
    Returns {review key: review id} so rating actions can point at their review."""
    seed = cfg.random_seed
    ev_by_key = {ev["_rkey"]: ev for evs in ratings.values() for ev in evs if ev["_rkey"]}
    band = {w["obligor_id"]: w["band"] for w in inp["watchlist"]}
    for r in reviews:
        evs = ratings.get(r["obligor_id"], [])
        when = min(as_of, r["submitted_date"] or as_of)
        rt = rating_at(evs, when) if evs else None
        dpd = _dpd_at(ctx["eps_by_obl"].get(r["obligor_id"], []), when)
        r["_distressed"] = bool(rt and rt["ifrs9_stage"] >= 2) or dpd > 0
        _finish_review(cfg, r, ev_by_key.get(r["_key"]), rt["internal_grade"] if rt else None, inp["ratios"],
                       band.get(r["obligor_id"]), as_of)
    reviews.sort(key=lambda r: (r["submitted_date"] or r["due_date"] or as_of, r["obligor_id"], r["review_type"],
                                str(r["_key"])))
    key_to_id = {}
    for n, r in enumerate(reviews, start=1):
        r["review_id"] = f"CRV-{n:06d}"
        key_to_id[r["_key"]] = r["review_id"]
        cc = r["_o"]["entity"]["booking_country"]
        r["analyst_id"] = _assign(seed, analysts, cc, "an", r["obligor_id"])
        decided = r["status"] in ("Approved", "Declined")
        r["approver_id"] = _assign(seed, approvers, cc, "ap", r["review_id"]) if decided else None
    return key_to_id


def _rating_rows(cfg, ctx: Dict, ratings: Dict[str, List[Dict]], key_to_id: Dict, approvers) -> List[Dict]:
    seed, rows = cfg.random_seed, []
    for obl in sorted(ratings):
        e = ctx["by_obl"][obl]["entity"]
        for ev in ratings[obl]:
            prev = ev["previous_grade"]
            rows.append({**{k: v for k, v in ev.items() if k != "_rkey"},
                         "notch_change": 0 if prev is None else ev["internal_grade"] - prev,
                         "rating_equivalent": rating_equivalent(ev["internal_grade"]),
                         "rating_model": RATING_MODEL.get(e["segment"], "Corporate Scorecard v3"),
                         "review_id": key_to_id.get(ev["_rkey"]),
                         "approver_id": _assign(seed, approvers, e["booking_country"], "rt", obl,
                                                ev["effective_date"].isoformat())})
    rows.sort(key=lambda r: (r["effective_date"], r["obligor_id"]))
    for n, r in enumerate(rows, start=1):
        r["rating_event_id"] = f"RTG-{n:06d}"
    return rows


def _liquidity(cfg, ctx: Dict, entities: List[Dict], inp: Dict, as_of: D):
    """Cash-flow events -> storyline RCF constraints + the Kinokawa prepayment -> loan schedule ->
    event actions. Returns (schedule, cf events, meta)."""
    cust = ctx["maps"].get("core_customer", {})
    cust_entity = {v: k for k, v in cust.items()}
    rcfs: Dict[str, List[Dict]] = defaultdict(list)
    for f in ctx["book"]:
        if f["facility_type"] == RCF:
            rcfs[f["entity_id"]].append(f)
    arrears_fac = {ep["facility_id"] for ep in ctx["episodes"] if ep["kind"] in ("Default", "Storyline")}
    scripted = frozenset(cust[e["entity_id"]] for e in entities if e.get("storyline_key") and e["entity_id"] in cust)
    plan, force, block, threshold = plan_liquidity_events(cfg, inp["forecasts"], inp["actuals"], cust_entity,
                                                          rcfs, arrears_fac, as_of, scripted)
    if sl.on(cfg, sl.HK_CASA):
        hk = hk_casa_events(cfg, entities, cust)
        hk_keys = {(e["cust_no"], e["target_month"]) for e in hk}
        plan = [e for e in plan if (e["cust_no"], e["target_month"]) not in hk_keys] + hk
    kino = kinokawa_prepay_facility(ctx["book"], entities) if sl.on(cfg, sl.KINOKAWA) else None
    prepay = None if kino is None else {
        "facility_id": kino["facility_id"], "date": sl.KINOKAWA_SCRIPT["prepay_date"],
        "amount": float(sl.KINOKAWA_SCRIPT["prepay_usd"]), "reason": "Refinanced with another bank"}
    schedule = loan_schedule(cfg, ctx["book"], ctx["episodes"], inp["fx"], as_of, force, block, prepay)
    events = link_liquidity_events(cfg, plan, schedule, cust_entity, rcfs, ctx["fac"], inp["deposits"], as_of)
    return schedule, events, {"kinokawa_facility": kino["facility_id"] if kino else None,
                              "shortfall_threshold_usd": threshold}


def _sunda_ecl(cfg, ctx: Dict, entities: List[Dict], cap: List[Dict]) -> Dict:
    if not ctx["sunda_obl"]:
        return {}
    obl_map = ctx["obl_map"]
    group = [obl_map[e["entity_id"]] for e in sl.group_entities(entities, sl.SUNDA["key"]) if e["entity_id"] in obl_map]
    return apply_sunda_ecl(cfg, cap, group, ctx["sunda_obl"])


def build_credit_risk(cfg, entities: List[Dict], xref: List[Dict], people: List[Dict],
                      inp: Dict) -> Dict[str, List[Dict]]:
    """All WP1 tables. `inp` carries the landed inputs (see run_bronze_credit_risk.load_inputs):
    terms, balances, health, statements, ratios, watchlist, waivers, deal_pricing, pnl, deposits,
    payments, n_accounts, fx_revenue, trade_fees, forecasts, actuals, fx. `_meta` holds the run's
    storyline diagnostics (Sunda ECL, Kinokawa facility, shortfall threshold)."""
    as_of = D.fromisoformat(cfg.as_of_date)
    ctx = _book_context(cfg, entities, xref, inp, as_of)
    profiles, health = ctx["profiles"], inp["health"]
    reviews = (_annual_reviews(cfg, profiles, inp["statements"], health, ctx["sunda_obl"], as_of)
               + _new_money_reviews(cfg, profiles, health, inp["deal_pricing"], as_of)
               + _amendment_reviews(cfg, profiles, inp["waivers"], as_of))
    ratings = _ratings(cfg, ctx, reviews, as_of)
    reviews += _watchlist_reviews(cfg, ctx, ratings, inp["watchlist"], reviews, as_of)
    analysts, approvers = _staff(people, "Credit Analyst"), _staff(people, "Credit Approver")
    key_to_id = _finalise_reviews(cfg, ctx, reviews, ratings, inp, analysts, approvers, as_of)
    obl_map = ctx["obl_map"]
    obl_cc = {obl_map[e["entity_id"]]: e["booking_country"] for e in entities if e["entity_id"] in obl_map}
    schedule, cf_events, meta = _liquidity(cfg, ctx, entities, inp, as_of)
    cap = capital_rows(cfg, profiles, ratings, ctx["eps_by_obl"], as_of)
    meta["sunda_ecl"] = _sunda_ecl(cfg, ctx, entities, cap)
    rev, cost = revenue_and_cost(cfg, entities, ctx["maps"], ctx["book"], inp, as_of)
    truth_eps = [{"episode_id": ep["episode_id"], "facility_id": ep["facility_id"], "obligor_id": ep["obligor_id"],
                  "entity_id": ep["entity_id"], "due_date": ep["due_date"], "cure_date": ep["cure_date"],
                  "kind": ep["kind"], "is_default": ep["is_default"],
                  "storyline_key": ctx["ents"][ep["entity_id"]].get("storyline_key")} for ep in ctx["episodes"]]
    return {
        "core_rating_history": [_dates_to_iso(r) for r in _rating_rows(cfg, ctx, ratings, key_to_id, approvers)],
        "core_dpd": dpd_rows(ctx["episodes"], ctx["fac"], inp["fx"], as_of),
        "core_loan_schedule": [_dates_to_iso(r) for r in schedule],
        "credit_review": [_dates_to_iso({k: v for k, v in r.items() if not k.startswith("_")}) for r in reviews],
        "credit_spreading_task": spreading_tasks(cfg, inp["statements"], obl_cc, analysts, as_of),
        "fin_capital_allocation": [_dates_to_iso(_round_floats(r)) for r in cap],
        "fin_cost_allocation": [_dates_to_iso(r) for r in cost],
        "fin_client_revenue": [_dates_to_iso(r) for r in rev],
        "cf_event": [_dates_to_iso(r) for r in cf_events],
        "truth_arrears_episode": truth_eps,
        "_meta": meta,
    }


def _dates_to_iso(r: Dict) -> Dict:
    return {k: (v.isoformat() if isinstance(v, D) else v) for k, v in r.items()}


_SIX_DP = {"pd_12m", "pd_lifetime", "lgd", "risk_weight", "ccf", "remaining_tenor_years"}


def _round_floats(r: Dict) -> Dict:
    return {k: (round(v, 6 if k in _SIX_DP else 2) if isinstance(v, float) else v) for k, v in r.items()}


# ---- storyline measures (tests and the run's verification print) ------------------------------------
def sunda_dpd(out: Dict, facility_id: str, dates: List[D]) -> Dict[str, int]:
    """Days past due on given dates for one facility (0 if not in arrears)."""
    have = {r["dpd_date"]: r["days_past_due"] for r in out["core_dpd"] if r["facility_id"] == facility_id}
    return {d.isoformat(): have.get(d.isoformat(), 0) for d in dates}


def shortfalls_followed(out: Dict, window=None, days: int = 10) -> Tuple[int, int]:
    """(predicted shortfalls dated in the window, of which followed by an RCF drawdown within `days`)."""
    lo, hi = [d.isoformat() for d in (window or sl.CASHFLOW["window"])]
    ev = [r for r in out["cf_event"] if r["event_type"] == "Predicted Shortfall" and lo <= r["event_date"] <= hi]
    return len(ev), sum(1 for r in ev if r["action_taken"] == "RCF Drawdown" and r["days_to_action"] <= days)


def stage3_obligors(out: Dict, month: str) -> List[str]:
    rows = out["fin_capital_allocation"]
    return sorted({r["obligor_id"] for r in rows if r["month"] == month and r["ifrs9_stage"] == 3})
