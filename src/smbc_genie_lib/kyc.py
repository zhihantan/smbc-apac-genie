"""Onboarding & KYC platform (brief §5.6, §6.1 kyc_*; D24 kyc_feedback; storyline 7; D43).

Bronze rows are keyed by the KYC platform's own ids (kyc_id, applicant_id, case_id), never by
truth ids:

  kyc_case        onboarding cases: the in-window new clients (all live) plus applicants that are
                  still open, withdrew or were rejected (provisional applicant ids, no kyc_id)
  kyc_case_stage  one row per case per stage entered: dates, days in stage, SLA met
  kyc_review      review history from FY2023: onboarding, periodic and trigger reviews
  kyc_document    documents requested per case: received, outstanding, waived or cancelled
  kyc_screening   sanctions / PEP / adverse-media hits, at onboarding and from ongoing screening
  kyc_feedback    post-go-live survey score and a short comment with a theme

New clients' dates come from `onboarding`, so each case ends on the dates its accounts and
payments start. Storyline 7: the Feb-2026 workflow migration slows Mar-May requests
(onboarding.stage_days) and freezes periodic reviews due Feb-May 2026, most of which clear from
June. Pure Python and deterministic; `build_kyc` returns every table plus the ops truth rows.
"""
from __future__ import annotations

import bisect
import datetime as _dt
import math
from collections import defaultdict
from statistics import mean, median
from typing import Dict, List, Optional, Tuple

from . import crm, names, onboarding, rng
from .onboarding import MIGRATION_START, SLA_DAYS, STAGE_NAMES, STAGE_NO, WINDOW_START

D, TD = _dt.date, _dt.timedelta
FI = "Financial Institution"
MIGRATION_PEAK = (D(2026, 3, 1), D(2026, 5, 31))  # requests that felt the full slowdown

# ---- risk & reviews -------------------------------------------------------------------
RISK_LEVELS = ["Low", "Medium", "High"]
CADENCE_YEARS = {"Low": 3, "Medium": 2, "High": 1}
RISK_CHANGE_P = 0.08          # long-standing clients whose rating changed at their latest review
REVIEW_FROM = D(2023, 4, 1)   # review history starts FY2023
DUE_SOON_DAYS = 90
BASE_LATE_P = 0.25            # periodic reviews normally completed after their due date
BACKLOG_DUE = (D(2026, 2, 1), D(2026, 5, 30))  # dues the migration can freeze (overdue by end-May)
MAY_END = D(2026, 5, 31)
BASELINE_ENDS = [D(2025, m + 1, 1) - TD(days=1) for m in range(4, 12)] + [D(2025, 12, 31), D(2026, 1, 31)]
BACKLOG_RATIO = 3.0           # end-May overdue vs the Apr-25..Jan-26 month-end average, per risk level
BACKLOG_CLEARED = 0.70        # share of the end-May backlog completed by as-of
BACKLOG_CLEAR_FROM = D(2026, 6, 1)
TRIGGER_P = 0.06
TRIGGER_REASONS = [("Adverse Media", 35), ("Ownership Change", 25), ("Screening Alert", 20),
                   ("Credit Deterioration", 15), ("Regulatory Request", 5)]

# ---- cases ------------------------------------------------------------------------------
APPLICANT_SHARE = 0.45        # applicants (never live) per in-window new client
APPLICANT_SEGMENTS = [("Japanese Corporate", 30), ("Non-Japanese Large Corporate", 25), (FI, 25),
                      ("Sponsor & Structured Finance", 12), ("Public Sector", 8)]
APPLICANT_OUTCOMES = [("Open", 40), ("Withdrawn", 35), ("Rejected", 25)]
WITHDRAW_STAGES = [("KYC Docs", 50), ("Screening", 10), ("Risk Assessment", 15),
                   ("Credit/Product Approval", 25)]
WITHDRAW_REASONS = [("Client Withdrew - Timeline", 35), ("Mandate Awarded to Another Bank", 25),
                    ("Documents Not Provided", 25), ("Client Withdrew - Pricing", 15)]
REJECT_STAGES = [("Screening", 40), ("Risk Assessment", 35), ("Credit/Product Approval", 25)]
REJECT_REASONS = {"Screening": "Screening True Match", "Risk Assessment": "Outside Risk Appetite",
                  "Credit/Product Approval": "Credit Declined"}
OPEN_STUCK_STAGES = [("KYC Docs", 50), ("Screening", 15), ("Risk Assessment", 15),
                     ("Credit/Product Approval", 15), ("Account Open", 5)]
BLOCKERS = {"Request": ["Awaiting Triage"],
            "KYC Docs": ["Awaiting Client Documents", "UBO Verification Pending"],
            "Screening": ["Screening Hit Under Review"], "Risk Assessment": ["Enhanced Due Diligence"],
            "Credit/Product Approval": ["Credit Approval Pending"],
            "Account Open": ["Account Setup in Progress"]}
MIGRATION_BLOCKER = "Workflow Migration Backlog"
NEW_CLIENT_EXTRAS = [("Payments", 0.70), ("Time Deposit", 0.30), ("Liquidity Sweeping", 0.15)]
APPLICANT_PRODUCTS = {
    "Japanese Corporate": ["Payments", "Trade Finance", "FX", "Term Loan"],
    "Non-Japanese Large Corporate": ["Payments", "Revolving Credit Facility", "FX", "Trade Finance"],
    FI: ["FX", "Money Market", "Custody Cash", "Payments"],
    "Sponsor & Structured Finance": ["Project Finance", "Interest Rate Hedging", "Payments"],
    "Public Sector": ["Payments", "Time Deposit", "Liquidity Sweeping"],
}

# ---- documents ------------------------------------------------------------------------
DOCS_COMMON = ["Certificate of Incorporation", "Constitutional Documents", "Board Resolution",
               "Register of Directors", "UBO Declaration", "Authorised Signatory List",
               "Audited Financial Statements", "Tax Self-Certification (FATCA/CRS)"]
DOCS_FI_EXTRA = ["Banking Licence", "AML/CTF Questionnaire"]
OPTIONAL_DOCS = {"Group Structure Chart"}
HARD_DOCS = {"UBO Declaration": 3.0, "AML/CTF Questionnaire": 3.0,
             "Audited Financial Statements": 2.0, "Board Resolution": 1.5}  # x chance outstanding

# ---- screening --------------------------------------------------------------------------
ONGOING_SCREENINGS_AT_SCALE_1 = 150_000
SCREEN_LISTS = [("Adverse Media", 30), ("PEP Database", 25), ("US OFAC SDN", 10), ("UN Sanctions", 8),
                ("EU Sanctions", 8), ("UK HMT Sanctions", 6), ("Local Regulator Watchlist", 8),
                ("Internal Watchlist", 5)]
TRUE_MATCH_P = {"PEP Database": 0.05, "Adverse Media": 0.04, "Internal Watchlist": 0.03}
HIT_TYPES = [("Fuzzy Name Match", 55), ("Partial Name Match", 20), ("Alias Match", 15),
             ("Exact Name Match", 10)]

# ---- feedback (comments <= 40 words, generic so they never contradict the case dates) -----
THEMES = {"Negative": [("Turnaround Time", 40), ("Document Requests", 30), ("Communication", 20),
                       ("Digital Portal", 10)],
          "Neutral": [("Turnaround Time", 40), ("Document Requests", 35), ("Communication", 25)],
          "Positive": [("Relationship Manager", 35), ("Account Setup", 25), ("Digital Portal", 20),
                       ("Turnaround Time", 20)]}
COMMENTS = {
    ("Negative", "Turnaround Time"): [
        "Onboarding took far longer than we were told. We waited weeks between steps with no clear timeline.",
        "Account opening dragged on and delayed our first payment run. The process needs to be much faster."],
    ("Negative", "Document Requests"): [
        "We were asked for the same documents more than once and the checklist kept changing.",
        "Repeated requests for ownership and board papers made the process feel disorganised."],
    ("Negative", "Communication"): [
        "It was hard to get status updates. Nobody could tell us where our application was stuck."],
    ("Negative", "Digital Portal"): ["The upload portal kept rejecting our files and timed out several times."],
    ("Neutral", "Turnaround Time"): [
        "The process was acceptable, although the document stage took longer than we expected."],
    ("Neutral", "Document Requests"): [
        "Document requirements were clear, but collecting them across our group took time."],
    ("Neutral", "Communication"): ["Updates came through, though we sometimes had to chase for them."],
    ("Positive", "Relationship Manager"): [
        "Our relationship manager kept us informed and chased internal teams on our behalf.",
        "Excellent support from our relationship manager throughout the onboarding."],
    ("Positive", "Account Setup"): [
        "Accounts were set up smoothly once approvals were done and our first payments went through without issues."],
    ("Positive", "Digital Portal"): ["The portal made uploading documents simple and we could track progress easily."],
    ("Positive", "Turnaround Time"): [
        "Onboarding was quicker than with our previous bank and the steps were well organised."],
}


def risk_rating(cfg, e: Dict) -> str:
    """Current KYC risk rating; skews higher for FIs, sponsors and carbon-intensive names."""
    u = rng.unit(cfg.random_seed, "kycrisk", e["entity_id"])
    bump = 0.0
    if e["segment"] in (FI, "Sponsor & Structured Finance"):
        bump += 0.15
    if e.get("is_carbon_intensive"):
        bump += 0.10
    u = max(0.0, u - bump)  # lower u -> higher risk band
    return "High" if u < 0.15 else ("Medium" if u < 0.58 else "Low")


def _pick(seed: int, pairs, *keys):
    return rng.weighted_choice(seed, [p for p, _ in pairs], [w for _, w in pairs], *keys)


def _iso(d: Optional[D]) -> Optional[str]:
    return d.isoformat() if d else None


def _add_years(d: D, n: int) -> D:
    return D(d.year + n, d.month, min(d.day, 28))


def _walk(request: D, days: Dict[str, int], upto: Optional[str] = None) -> List[list]:
    """[stage, entered, exited] for stages 1-6 in order, stopping after `upto`."""
    plan, t = [], request
    for name in STAGE_NAMES[:6]:
        plan.append([name, t, t + TD(days=days[name])])
        t += TD(days=days[name])
        if name == upto:
            break
    return plan


def _stage(plan: List[list], name: str) -> Optional[list]:
    return next((p for p in plan if p[0] == name), None)


# ---- cases ----------------------------------------------------------------------------
def _new_client_case(cfg, e, sched, kyc_id, crm_id, as_of) -> Dict:
    seed, eid = cfg.random_seed, e["entity_id"]
    plan = _walk(sched["request_date"], sched["stage_days"])
    go_live, ft = plan[-1][2], sched["first_txn_date"]
    plan.append(["First Transaction", go_live, ft if ft <= as_of else None])
    products = ["Current Account"] + [p for p, prob in NEW_CLIENT_EXTRAS
                                      if rng.unit(seed, "obprod", eid, p) < prob]
    existing_group = e["_group_size"] > 1
    # storyline 3: Meridian's Thai subsidiary was not linked to its group at intake (ER finds it later)
    matched = existing_group and rng.unit(seed, "obmatch", eid) < 0.85 and e.get("storyline_key") != "meridian"
    return {
        "_key": eid, "_entity_id": eid, "_group_id": e["group_id"], "_plan": plan,
        "_kind": "New Client - Existing Group" if existing_group else "New Client - New Group",
        "_go_live": go_live, "_first_txn": ft,
        "kyc_id": kyc_id, "applicant_name": e["legal_name"], "booking_country": e["booking_country"],
        "segment": e["segment"], "products": products, "request_date": sched["request_date"],
        "current_stage": "Complete" if ft <= as_of else "First Transaction", "status": "Live",
        "status_date": go_live, "blocker_reason": None, "outcome_reason": None,
        "rm_code": crm.rm_code(seed, crm_id) if crm_id else f"RM{rng.hash64(seed, 'kycrm', eid) % 120 + 1:03d}",
        "intake_group_match": matched, "intake_group_ref": e["group_id"] if matched else None,
    }


def _applicant_case(cfg, i, groups_by_seg, used_names, used_brands, codes, weights, as_of) -> Dict:
    seed, key = cfg.random_seed, f"APPL{i:05d}"
    segment = _pick(seed, APPLICANT_SEGMENTS, "appseg", key)
    cc = rng.weighted_choice(seed, codes, weights, "appcc", key)
    group = None
    if rng.unit(seed, "appgrp", key) < 0.60:  # a new entity of an existing client group
        pool = groups_by_seg[segment]
        group = pool[rng.hash64(seed, "appgrpid", key) % len(pool)]
        legal = next(n for n in (names.entity_legal_name(group["group_name"], cc, seed, 70000 + i,
                                                         division_idx=d) for d in range(60))
                     if n not in used_names)
    else:  # new to the bank: a brand no existing group uses
        brand = next(b for b in (names.group_brand(seed, 60000 + i, segment, a) for a in range(30))
                     if b not in used_brands)
        used_brands.add(brand)
        legal = names.entity_legal_name(brand, cc, seed, 70000 + i)
    names.assert_clean(legal)
    used_names.add(legal)

    outcome = _pick(seed, APPLICANT_OUTCOMES, "appout", key)
    reason, blocker = None, None
    if outcome == "Open":
        back = (rng.randint(seed, 3, 120, "appage", key) if rng.unit(seed, "appold", key) < 0.8
                else rng.randint(seed, 121, 240, "appage2", key))
        request = as_of - TD(days=back)
        days = onboarding.stage_days(cfg, key, segment, request)
        plan, t = [], request
        for name in STAGE_NAMES[:6]:
            if t + TD(days=days[name]) > as_of:
                plan.append([name, t, None])
                break
            plan.append([name, t, t + TD(days=days[name])])
            t += TD(days=days[name])
        else:  # would already be live: it is stuck in a stage instead
            plan = _walk(request, days, upto=_pick(seed, OPEN_STUCK_STAGES, "appstuck", key))
            plan[-1][2] = None
        current, status_date = plan[-1][0], None
        if (onboarding.migration_factor(cfg, request) >= 0.35 and rng.unit(seed, "appmig", key) < 0.5
                and current in ("KYC Docs", "Screening", "Risk Assessment")):
            blocker = MIGRATION_BLOCKER
        else:
            opts = BLOCKERS[current]
            blocker = opts[rng.hash64(seed, "appblk", key) % len(opts)]
    else:
        span = (as_of - TD(days=90) - WINDOW_START).days
        request = WINDOW_START + TD(days=int(rng.unit(seed, "appreq", key) * span))
        days = onboarding.stage_days(cfg, key, segment, request)
        if outcome == "Withdrawn":
            end, reason = _pick(seed, WITHDRAW_STAGES, "appwd", key), _pick(seed, WITHDRAW_REASONS, "appwdr", key)
        else:
            end = _pick(seed, REJECT_STAGES, "apprj", key)
            reason = REJECT_REASONS[end]
        plan = _walk(request, days, upto=end)
        plan[-1][2] = plan[-1][1] + TD(days=max(1, int(days[end] * (0.4 + 0.8 * rng.unit(seed, "append", key)))))
        current, status_date = end, plan[-1][2]

    first = "Correspondent Account" if segment == FI else "Current Account"
    extras = APPLICANT_PRODUCTS[segment]
    n_extra = 1 + (rng.unit(seed, "appnprod", key) < 0.5)
    start = rng.hash64(seed, "appprod", key) % len(extras)
    products = [first] + [extras[(start + k) % len(extras)] for k in range(n_extra)]
    matched = group is not None and rng.unit(seed, "appmatch", key) < 0.80
    return {
        "_key": key, "_entity_id": None, "_group_id": group["group_id"] if group else None,
        "_plan": plan, "_kind": "Applicant - Existing Group" if group else "Applicant - New Group",
        "_go_live": None, "_first_txn": None,
        "kyc_id": None, "applicant_name": legal, "booking_country": cc, "segment": segment,
        "products": products, "request_date": request, "current_stage": current, "status": outcome,
        "status_date": status_date, "blocker_reason": blocker, "outcome_reason": reason,
        "rm_code": f"RM{rng.hash64(seed, 'kycrm', key) % 120 + 1:03d}",
        "intake_group_match": matched, "intake_group_ref": group["group_id"] if matched else None,
    }


def _stage_rows(case: Dict, as_of: D) -> List[Dict]:
    rows = []
    for name, entered, exited in case["_plan"]:
        days = ((exited or as_of) - entered).days
        rows.append({"case_id": case["case_id"], "stage_no": STAGE_NO[name], "stage_name": name,
                     "entered_date": entered.isoformat(), "exited_date": _iso(exited),
                     "days_in_stage": days, "sla_days": SLA_DAYS[name],
                     "is_sla_met": days <= SLA_DAYS[name], "is_current": exited is None,
                     "actor_id": case["owner_id"]})
    return rows


def _document_rows(cfg, case: Dict, as_of: D) -> List[Dict]:
    seed, key = cfg.random_seed, case["_key"]
    kd = _stage(case["_plan"], "KYC Docs")
    if kd is None:
        return []
    _, entered, exited = kd
    types = list(DOCS_COMMON)
    if case["segment"] == "Public Sector":
        types[0] = "Government Mandate Letter"
    if case["segment"] == FI:
        types += DOCS_FI_EXTRA
    if case["segment"] == "Sponsor & Structured Finance":
        types += ["Group Structure Chart"]
    collecting = exited is None or case["current_stage"] == "KYC Docs"  # open, or abandoned here
    horizon = exited or as_of
    rows = []
    for k, dt in enumerate(types):
        received, status = None, "Received"
        if rng.unit(seed, "docwaive", key, dt) < 0.03 and dt not in HARD_DOCS:
            status = "Waived"
        elif collecting and rng.unit(seed, "docout", key, dt) < min(0.9, 0.25 * HARD_DOCS.get(dt, 1.0)):
            status = "Outstanding" if case["status"] == "Open" else "Cancelled"
        else:
            received = entered + TD(days=int(rng.unit(seed, "docrcv", key, dt) * (horizon - entered).days))
        rows.append({"document_id": f"{case['case_id']}-D{k + 1:02d}", "case_id": case["case_id"],
                     "document_type": dt, "is_mandatory": dt not in OPTIONAL_DOCS,
                     "requested_date": entered.isoformat(), "received_date": _iso(received), "status": status})
    if case["status"] == "Open" and exited is None and not any(r["status"] == "Outstanding" for r in rows):
        hardest = max(rows, key=lambda r: HARD_DOCS.get(r["document_type"], 1.0))  # stuck on something
        hardest.update(status="Outstanding", received_date=None)
    return rows


# ---- screening ----------------------------------------------------------------------------
def _hit(cfg, lst: str, true: bool, slow: float, *keys) -> Dict:
    seed = cfg.random_seed
    hours = rng.lognormal(seed, math.log(26.0 if true else 3.5), 0.6, "scrh", *keys) * slow
    if not true:
        disp = "False Positive - Cleared"
    else:
        disp = "True Match - EDD Applied" if lst == "PEP Database" else "True Match - Escalated"
    return {"list_name": lst, "hit_type": _pick(seed, HIT_TYPES, "scrhit", *keys), "is_true_match": true,
            "resolution_hours": round(hours, 2), "disposition": disp}


def _onboarding_screening(cfg, case: Dict, as_of: D) -> List[Dict]:
    seed, key = cfg.random_seed, case["_key"]
    sc = _stage(case["_plan"], "Screening")
    if sc is None:
        return []
    _, entered, exited = sc
    forced = case["status"] == "Rejected" and case["current_stage"] == "Screening"
    n = _pick(seed, [(0, 40), (1, 30), (2, 20), (3, 10)], "scrn", key)
    n = max(n, 1) if forced else n
    span = max(1, ((exited or as_of) - entered).days)
    slow = 1.4 if onboarding.migration_factor(cfg, case["request_date"]) > 0 else 1.0
    rows = []
    for h in range(n):
        if forced and h == 0:
            lst = _pick(seed, [("Adverse Media", 60), ("US OFAC SDN", 20), ("UN Sanctions", 20)], "scrforce", key)
            hit = _hit(cfg, lst, True, slow, key, h)
            hit["disposition"] = "True Match - Rejected"
        else:
            lst = _pick(seed, SCREEN_LISTS, "scrl", key, h)
            hit = _hit(cfg, lst, rng.unit(seed, "scrt", key, h) < TRUE_MATCH_P.get(lst, 0.0), slow, key, h)
        rows.append({"subject_ref": case["kyc_id"] or case["applicant_id"], "case_id": case["case_id"],
                     "screening_context": "Onboarding",
                     "screening_date": (entered + TD(days=int(rng.unit(seed, "scrd", key, h) * span))).isoformat(),
                     **hit, "resolved_by": case["owner_id"]})
    return rows


def _ongoing_screening(cfg, clients: List[Tuple], officers: List[str], as_of: D) -> List[Dict]:
    """Batch name-screening alerts on onboarded clients (scales with SCALE; ~150k at 1.0)."""
    seed = cfg.random_seed
    n = round(ONGOING_SCREENINGS_AT_SCALE_1 * cfg.scale)
    weights = [{"High": 3.0, "Medium": 1.5, "Low": 1.0}[risk] * (1.5 if seg == FI else 1.0)
               for _, risk, seg, _ in clients]
    cum, acc = [], 0.0
    for w in weights:
        acc += w
        cum.append(acc)
    rows = []
    for i in range(n):
        kyc_id, _, _, start = clients[min(len(clients) - 1, bisect.bisect_right(cum, rng.unit(seed, "ongsub", i) * acc))]
        d = start + TD(days=int(rng.unit(seed, "ongd", i) * max(1, (as_of - start).days)))
        lst = _pick(seed, SCREEN_LISTS, "ongl", i)
        true = rng.unit(seed, "ongt", i) < 0.5 * TRUE_MATCH_P.get(lst, 0.0)
        rows.append({"subject_ref": kyc_id, "case_id": None, "screening_context": "Ongoing",
                     "screening_date": d.isoformat(), **_hit(cfg, lst, true, 1.0, "ong", i),
                     "resolved_by": officers[rng.hash64(seed, "ongby", i) % len(officers)]})
    return rows


# ---- reviews ------------------------------------------------------------------------------
def _completed(cfg, kyc_id: str, due: D, as_of: D) -> Optional[D]:
    """Business-as-usual completion date of a periodic review, or None if still open at as-of."""
    seed, key = cfg.random_seed, (kyc_id, due.isoformat())
    if rng.unit(seed, "kyclate", *key) < BASE_LATE_P:
        done = due + TD(days=min(150, max(1, int(rng.lognormal(seed, math.log(25.0), 0.6, "kyclated", *key)))))
    else:
        done = due - TD(days=rng.randint(seed, 0, 45, "kycearly", *key))
    return done if done <= as_of else None


def _overdue(items: List[Dict], when: D) -> int:
    return sum(1 for it in items if it["due"] < when and (it["done"] is None or it["done"] > when))


def _apply_backlog(cfg, items: List[Dict], as_of: D) -> None:
    """Storyline 7: freeze enough Feb-May 2026 periodic reviews per risk level that end-May overdue
    is ~3x normal, then complete ~70% of the end-May backlog in a front-loaded Jun-Sep clean-up.
    Targeted (not probabilistic) so the counts hold whatever the rest of the universe does."""
    seed = cfg.random_seed
    periodic = [it for it in items if it["review_type"] == "Periodic"]
    for level in RISK_LEVELS:
        pool = [it for it in periodic if it["rating"] == level]
        need = round(BACKLOG_RATIO * mean(_overdue(pool, d) for d in BASELINE_ENDS)) - _overdue(pool, MAY_END)
        cands = sorted((it for it in pool if BACKLOG_DUE[0] <= it["due"] <= BACKLOG_DUE[1]
                        and it["done"] is not None and it["done"] <= MAY_END),
                       key=lambda it: rng.hash64(seed, "kycbk", it["kyc_id"], it["due"].isoformat()))
        for it in cands[:max(0, need)]:
            it["done"], it["frozen"] = None, True
    backlog = [it for it in periodic if it["due"] < MAY_END and (it["done"] is None or it["done"] > MAY_END)]
    need = round(BACKLOG_CLEARED * len(backlog)) - sum(1 for it in backlog if it["done"] is not None)
    frozen = sorted((it for it in backlog if it.get("frozen")),
                    key=lambda it: rng.hash64(seed, "kycclr", it["kyc_id"], it["due"].isoformat()))
    span = (as_of - BACKLOG_CLEAR_FROM).days
    for it in frozen[:max(0, need)]:  # clean-up is front-loaded, so May stays the peak
        u = rng.unit(seed, "kycclrd", it["kyc_id"], it["due"].isoformat())
        it["done"] = BACKLOG_CLEAR_FROM + TD(days=int(span * u ** 2))


def _review_status(due: D, done: Optional[D], as_of: D) -> Tuple[str, int]:
    if done:
        return "Completed", max(0, (done - due).days)
    if due < as_of:
        return "Overdue", (as_of - due).days
    return ("Due Soon" if (due - as_of).days <= DUE_SOON_DAYS else "Scheduled"), 0


def _client_reviews(cfg, e: Dict, kyc_id: str, since: D, case: Optional[Dict], as_of: D) -> List[Dict]:
    """A client's onboarding, periodic and trigger reviews (business-as-usual completion)."""
    seed, eid = cfg.random_seed, e["entity_id"]
    rating = risk_rating(cfg, e)
    cadence = CADENCE_YEARS[rating]
    base = {"kyc_id": kyc_id, "rating": rating, "cc": e["booking_country"], "trigger_reason": None}
    items = []
    if case is not None:  # onboarding review = the Risk Assessment stage
        _, ra_in, ra_out = _stage(case["_plan"], "Risk Assessment")
        items.append({**base, "review_type": "Onboarding",
                      "due": ra_in + TD(days=SLA_DAYS["Risk Assessment"]), "done": ra_out})
    k = 1
    while True:
        due = _add_years(since, k * cadence)
        k += 1
        if due < REVIEW_FROM:
            continue
        items.append({**base, "review_type": "Periodic", "due": due, "done": _completed(cfg, kyc_id, due, as_of)})
        if due > as_of and items[-1]["done"] is None:  # the next open review is on the books
            break
    p_trig = TRIGGER_P * (2.0 if rating == "High" else 1.0) * (1.5 if e.get("is_carbon_intensive") else 1.0)
    start = max(WINDOW_START, since + TD(days=30))
    if rng.unit(seed, "kyctrg", eid) < p_trig and start < as_of - TD(days=10):
        t = start + TD(days=int(rng.unit(seed, "kyctrgd", eid) * (as_of - start).days))
        done = t + TD(days=rng.randint(seed, 5, 40, "kyctrgc", eid))
        items.append({**base, "review_type": "Trigger", "trigger_reason": _pick(seed, TRIGGER_REASONS, "kyctrgr", eid),
                      "due": t + TD(days=30), "done": done if done <= as_of else None})
    changed = case is None and rng.unit(seed, "kycchg", eid) < RISK_CHANGE_P
    prev = rating
    if changed:
        prev = "Medium" if rating != "Medium" else rng.choice(seed, ["Low", "High"], "kycprev", eid)
    for it in items:
        it["prev"] = prev
    return sorted(items, key=lambda it: it["due"])


def _review_rows(items: List[Dict], reviewer, as_of: D) -> List[Dict]:
    """Final rows for one client: status as of today and risk before/after each review."""
    done_dates = [it["done"] for it in items if it["done"]]
    last_done = max(done_dates) if done_dates else None
    rows = []
    for n, it in enumerate(items, start=1):
        rating, prev, done = it["rating"], it["prev"], it["done"]
        if it["review_type"] == "Onboarding":
            before, after = None, rating
        elif done is None:
            before, after = rating, None
        elif prev != rating and done < last_done:
            before = after = prev
        elif prev != rating:  # the client's latest review changed the rating
            before, after = prev, rating
        else:
            before = after = rating
        status, overdue = _review_status(it["due"], done, as_of)
        rows.append({"review_id": f"KYR-{it['kyc_id'].split('-')[-1]}-{n:02d}", "kyc_id": it["kyc_id"],
                     "review_type": it["review_type"], "trigger_reason": it["trigger_reason"],
                     "due_date": it["due"].isoformat(), "completed_date": _iso(done), "status": status,
                     "days_overdue": overdue, "risk_before": before, "risk_after": after,
                     "reviewer_id": reviewer(it["cc"], it["kyc_id"], n)})
    return rows


# ---- feedback -----------------------------------------------------------------------------
def _feedback(cfg, case: Dict, as_of: D) -> Optional[Dict]:
    seed, key = cfg.random_seed, case["_key"]
    survey = case["_go_live"] + TD(days=rng.randint(seed, 14, 45, "fbday", key))
    if survey > as_of or rng.unit(seed, "fbresp", key) >= 0.60:
        return None
    dtl = (case["_go_live"] - case["request_date"]).days
    peak = onboarding.migration_factor(cfg, case["request_date"]) >= 1.0
    raw = 4.1 - 0.05 * max(0, dtl - 21) - (0.4 if peak else 0.0) + rng.normal(seed, 0.0, 0.9, "fbs", key)
    score = min(5, max(1, int(raw + 0.5)))
    tone = "Negative" if score <= 2 else ("Neutral" if score == 3 else "Positive")
    theme = _pick(seed, THEMES[tone], "fbtheme", key)
    return {"case_id": case["case_id"], "kyc_id": case["kyc_id"], "survey_date": survey.isoformat(),
            "score": score, "comment_text": rng.choice(seed, COMMENTS[(tone, theme)], "fbtext", key),
            "theme": theme}


# ---- assembly -----------------------------------------------------------------------------
def build_kyc(cfg, groups: List[Dict], entities: List[Dict], people: List[Dict],
              kyc_map: Dict[str, str], crm_map: Dict[str, str], cohort: Dict[str, Dict]) -> Dict[str, List[Dict]]:
    """All KYC tables: bronze kyc_case/_case_stage/_review/_document/_screening/_feedback + truth."""
    seed = cfg.random_seed
    as_of = D.fromisoformat(cfg.as_of_date)
    group_size: Dict[str, int] = defaultdict(int)
    for e in entities:
        group_size[e["group_id"]] += 1
    officers = [p for p in people if p["role"] == "Onboarding Officer"]
    by_office: Dict[str, List[str]] = defaultdict(list)
    for p in officers:
        by_office[p["coverage_office"]].append(p["employee_id"])
    officer_ids = [p["employee_id"] for p in officers]

    def officer(cc: str, *keys) -> str:
        pool = by_office.get(cc) or officer_ids
        return pool[rng.hash64(seed, "kycowner", *keys) % len(pool)]

    # cases: every in-window new client (live) + applicants that never went live
    cases = []
    for e in entities:
        sched = cohort.get(e["entity_id"])
        if sched and e["entity_id"] in kyc_map:
            cases.append(_new_client_case(cfg, {**e, "_group_size": group_size[e["group_id"]]}, sched,
                                          kyc_map[e["entity_id"]], crm_map.get(e["entity_id"]), as_of))
    groups_by_seg: Dict[str, List[Dict]] = defaultdict(list)
    for g in groups:
        groups_by_seg[g["segment"]].append(g)
    used_names = {e["legal_name"] for e in entities}
    used_brands = {g["group_name"] for g in groups}
    codes = [l["code"] for l in cfg.booking_locations]
    weights = [float(l["weight"]) for l in cfg.booking_locations]
    for i in range(round(len(cases) * APPLICANT_SHARE)):
        cases.append(_applicant_case(cfg, i, groups_by_seg, used_names, used_brands, codes, weights, as_of))
    cases.sort(key=lambda c: (c["request_date"], c["_key"]))
    for n, c in enumerate(cases, start=1):
        c["case_id"], c["applicant_id"] = f"ONB-{n:05d}", f"APP-{n:05d}"
        c["owner_id"] = officer(c["booking_country"], c["_key"])

    stages, docs, screens, feedback, case_rows, truth_rows = [], [], [], [], [], []
    for c in cases:
        stages += _stage_rows(c, as_of)
        cdocs = _document_rows(cfg, c, as_of)
        docs += cdocs
        screens += _onboarding_screening(cfg, c, as_of)
        if c["status"] == "Live":
            fb = _feedback(cfg, c, as_of)
            if fb:
                feedback.append(fb)
        case_rows.append({
            "case_id": c["case_id"], "applicant_id": c["applicant_id"], "kyc_id": c["kyc_id"],
            "applicant_name": c["applicant_name"], "booking_country": c["booking_country"],
            "segment": c["segment"], "products_requested": "; ".join(c["products"]),
            "primary_product": c["products"][0], "request_date": c["request_date"].isoformat(),
            "current_stage": c["current_stage"], "status": c["status"], "status_date": _iso(c["status_date"]),
            "blocker_reason": c["blocker_reason"], "outcome_reason": c["outcome_reason"],
            "documents_outstanding": sum(d["status"] == "Outstanding" for d in cdocs),
            "owner_id": c["owner_id"], "rm_code": c["rm_code"],
            "intake_group_match": c["intake_group_match"], "intake_group_ref": c["intake_group_ref"]})
        truth_rows.append({
            "case_id": c["case_id"], "applicant_id": c["applicant_id"], "kyc_id": c["kyc_id"],
            "entity_id": c["_entity_id"], "group_id": c["_group_id"], "applicant_kind": c["_kind"],
            "segment": c["segment"], "request_date": c["request_date"], "go_live_date": c["_go_live"],
            "first_txn_date": c["_first_txn"], "status": c["status"],
            "days_to_live": (c["_go_live"] - c["request_date"]).days if c["_go_live"] else None,
            "is_migration_peak": MIGRATION_PEAK[0] <= c["request_date"] <= MIGRATION_PEAK[1],
            "intake_group_match": c["intake_group_match"]})

    # reviews (every onboarded KYC client) and ongoing screening alerts
    case_by_entity = {c["_entity_id"]: c for c in cases if c["_entity_id"]}
    per_client, clients = [], []
    for e in entities:
        kyc_id = kyc_map.get(e["entity_id"])
        if not kyc_id:
            continue
        since = onboarding.customer_since(cfg, e, cohort)
        per_client.append(_client_reviews(cfg, e, kyc_id, since, case_by_entity.get(e["entity_id"]), as_of))
        clients.append((kyc_id, risk_rating(cfg, e), e["segment"], max(WINDOW_START, since)))
    if cfg.storylines.get("kyc_migration_backlog", True):
        _apply_backlog(cfg, [it for items in per_client for it in items], as_of)
    reviews = [r for items in per_client
               for r in _review_rows(items, lambda cc, k, n: officer(cc, "rev", k, n), as_of)]
    screens += _ongoing_screening(cfg, clients, officer_ids, as_of)
    screens.sort(key=lambda r: (r["screening_date"], r["subject_ref"], r["list_name"], r["screening_context"]))
    for n, r in enumerate(screens, start=1):
        r["screening_id"] = f"SCR-{n:07d}"
    for n, r in enumerate(feedback, start=1):
        r["feedback_id"] = f"FBK-{n:05d}"
    return {"kyc_case": case_rows, "kyc_case_stage": stages, "kyc_review": reviews,
            "kyc_document": docs, "kyc_screening": screens, "kyc_feedback": feedback,
            "truth_onboarding_case": truth_rows}


# ---- storyline-7 measures (used by tests and the run's verification print) ----------------
def days_to_live_medians(truth_rows: List[Dict]) -> Tuple[float, float]:
    """(median days to live for requests before the migration, for Mar-May 2026 requests)."""
    live = [r for r in truth_rows if r["status"] == "Live"]
    before = [r["days_to_live"] for r in live if r["request_date"] < MIGRATION_START]
    peak = [r["days_to_live"] for r in live if r["is_migration_peak"]]
    return median(before), median(peak)


def fi_kyc_docs_delta(case_rows: List[Dict], stage_rows: List[Dict]) -> float:
    """FI cases' average KYC Docs days, Mar-May 2026 requests minus pre-migration requests."""
    req = {c["case_id"]: D.fromisoformat(c["request_date"]) for c in case_rows if c["segment"] == FI}
    before, peak = [], []
    for s in stage_rows:
        r = req.get(s["case_id"])
        if r is None or s["stage_name"] != "KYC Docs" or s["exited_date"] is None:
            continue
        if r < MIGRATION_START:
            before.append(s["days_in_stage"])
        elif MIGRATION_PEAK[0] <= r <= MIGRATION_PEAK[1]:
            peak.append(s["days_in_stage"])
    return mean(peak) - mean(before)


def overdue_at(reviews: List[Dict], when: D, kyc_ids: Optional[set] = None) -> int:
    """Periodic reviews overdue at a date (past due, not completed by then)."""
    n = 0
    for r in reviews:
        if r["review_type"] != "Periodic" or (kyc_ids is not None and r["kyc_id"] not in kyc_ids):
            continue
        done = r["completed_date"]
        if D.fromisoformat(r["due_date"]) < when and (done is None or D.fromisoformat(done) > when):
            n += 1
    return n


def backlog_cleared_share(reviews: List[Dict], as_of: D, may_end: D = D(2026, 5, 31)) -> float:
    """Share of the periodic reviews overdue at end-May 2026 completed by as-of."""
    backlog = [r for r in reviews if r["review_type"] == "Periodic" and D.fromisoformat(r["due_date"]) < may_end
               and (r["completed_date"] is None or D.fromisoformat(r["completed_date"]) > may_end)]
    cleared = [r for r in backlog if r["completed_date"] and D.fromisoformat(r["completed_date"]) <= as_of]
    return len(cleared) / len(backlog)
