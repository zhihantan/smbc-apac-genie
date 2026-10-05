"""How the storyline script is applied (PLAN §5.5 "apply_to_truth" / "apply_to_bronze"; D44).

`storylines.py` says *what* happens to whom and when; the functions here turn that into concrete
overrides that the existing generators call while they generate, so every downstream engine
derives consistent effects instead of being patched afterwards:

  health_override        scripted latent health + effective grade (Sunda's cascade, Tanaka's dip)
  statement_override     FY leverage / EBITDA-margin overrides (Sunda 4.6x FY2025, Tanaka weak ICR)
  scripted_facilities    facilities the random book lacks (Kinokawa USD 400m, Sunda term loan,
                         Tanaka keepwell RCF)
  utilisation_overrides  month-end utilisation pins (Sunda >= 95% from March, Tanaka July spike,
                         Kinokawa prepaid in February)
  adjust_covenants       Sunda's FY2025 breach + the five blind-spot names at the latest test
  deposit_story          per-account deposit multipliers / shapes (Sunda outflow, HK CASA migration)
  kinokawa_payments      loan-service payments to another bank + CN->VN trade settlements
  exception_reasons      deal-pricing exception reasons (exactly 9 FY2025 "Relationship" deals, 3 caught up)

Pure Python and deterministic (rng keyed by business keys); unit-tested in
tests/unit/test_storyline_injectors.py.
"""
from __future__ import annotations

import datetime as _dt
from typing import Dict, List, Optional, Tuple

from . import names, rng, storylines as S

D, TD = _dt.date, _dt.timedelta


# ---- latent health (apply_to_truth) ------------------------------------------------------
# (from month, health, grade): Sunda's lead stays a quiet grade-7 name until the January news,
# then deteriorates month by month into default; the June downgrade is 7 -> 9.
SUNDA_HEALTH = [(D(2023, 4, 1), 0.62, 7), (D(2026, 1, 1), 0.60, 7), (D(2026, 2, 1), 0.50, 7),
                (D(2026, 3, 1), 0.42, 7), (D(2026, 4, 1), 0.32, 7), (D(2026, 5, 1), 0.24, 7),
                (D(2026, 6, 1), 0.19, 9), (D(2026, 7, 1), 0.20, 9)]   # EWS ~20 early Jan -> ~78 end-Jun
SUNDA_SIBLING_UPLIFT = 0.15        # the Singapore entity follows the group, milder, no downgrade
TANAKA_DIP = [(D(2026, 7, 1), 0.42), (D(2026, 10, 1), 0.50)]   # computed EWS ~44 (Amber) from July


def _step(path, month: D):
    cur = None
    for p in path:
        if month >= p[0]:
            cur = p
    return cur


def health_override(cfg, entity: Dict, month: D) -> Optional[Tuple[float, int]]:
    """(health, grade) for scripted entities, or None to keep the AR(1) path."""
    key = entity.get("storyline_key")
    if key == "sunda" and S.on(cfg, S.SUNDA_EWS):
        p = _step(SUNDA_HEALTH, month)
        if p is None:
            return None
        wobble = 0.02 * (rng.unit(cfg.random_seed, "sundah", entity["entity_id"], month.isoformat()) - 0.5)
        if entity["is_group_lead"]:
            return round(p[1] + (wobble if month < D(2026, 1, 1) else 0.0), 4), p[2]
        return round(min(0.95, p[1] + SUNDA_SIBLING_UPLIFT + wobble), 4), 7
    if key == "tanaka" and entity["is_group_lead"] and S.on(cfg, S.TANAKA):
        p = _step(TANAKA_DIP, month)
        if p is not None:
            return p[1], 4   # behaviour weakens; the rating holds (parent support)
    return None


# ---- financial statements --------------------------------------------------------------
def statement_override(cfg, entity: Dict, fy: int) -> Dict:
    """Overrides for one client-FY statement: leverage (Net Debt/EBITDA) and EBITDA margin."""
    key = entity.get("storyline_key")
    if key == "sunda" and entity["is_group_lead"] and S.on(cfg, S.SUNDA_EWS):
        return {"leverage": {2023: 2.9, 2024: 3.4, 2025: S.SUNDA["covenant_actual"]}.get(fy)}
    if key == "tanaka" and entity["is_group_lead"] and S.on(cfg, S.TANAKA) and fy == 2025:
        return {"leverage": 3.0, "ebitda_margin": 0.07}   # FY2025 margin squeeze -> ICR ~2.0
    return {}


# ---- lending book ------------------------------------------------------------------------
def scripted_facilities(cfg, entities: List[Dict], entity_obligor: Dict[str, str], next_no: int) -> List[Dict]:
    """Facilities the storylines need that the random book does not provide (ids continue the
    FAC- sequence so no existing id moves)."""
    out: List[Dict] = []

    def add(e, ftype, limit, orig, mat, margin, guarantor, util, covenant, closed=None):
        nonlocal next_no
        obligor = entity_obligor.get(e["entity_id"])
        if not obligor:
            return
        out.append({
            "facility_id": f"FAC-{next_no:06d}", "entity_id": e["entity_id"], "obligor_id": obligor,
            "facility_type": ftype, "limit_usd": float(limit), "currency": "USD", "margin_bps": margin,
            "security_type": "Unsecured", "is_secured": False, "guarantor_type": guarantor,
            "origination_date": orig, "maturity_date": mat, "is_revolving": ftype == "Revolving Credit Facility",
            "base_utilisation": util, "has_covenant": covenant, "closed_date": closed,
        })
        next_no += 1

    if S.on(cfg, S.KINOKAWA):
        k = S.KINOKAWA_SCRIPT
        add(S.lead_entity(entities, "kinokawa"), "Syndicated Loan", k["prepay_usd"], D(2023, 3, 15),
            D(2028, 3, 15), 32, "Parent Guarantee", 1.0, False, closed=k["prepay_date"])  # JC parent-guaranteed pricing
    if S.on(cfg, S.SUNDA_EWS):
        add(S.lead_entity(entities, "sunda"), "Syndicated Loan", 95_000_000, D(2023, 9, 20),
            D(2028, 9, 20), 285, "None", 0.78, True)   # sized for the ~USD 48m Stage-3 ECL jump
    if S.on(cfg, S.TANAKA):
        add(S.lead_entity(entities, "tanaka"), "Revolving Credit Facility", 60_000_000, D(2024, 6, 14),
            D(2027, 6, 14), 55, S.TANAKA_SCRIPT["support_type"], 0.45, False)
    if S.on(cfg, S.MERIDIAN_ER):  # the group's lending sits on a sibling with a credit identity
        m = S.MERIDIAN
        sib = [e for e in S.group_entities(entities, m["key"]) if not e["is_group_lead"]
               and e["entity_id"] in entity_obligor and e["booking_country"] != m["onboarding_country"]]
        if sib:
            add(sib[0], "Syndicated Loan", m["lending_limit_usd"], D(2023, 6, 15), D(2028, 6, 15), 150, "None",
                m["lending_utilisation"], True)
    return out


def _month_ends(start: D, end: D) -> List[D]:
    out, d = [], D(start.year, start.month, 1)
    while d <= end:
        nxt = D(d.year + (d.month == 12), d.month % 12 + 1, 1)
        out.append(nxt - TD(days=1))
        d = nxt
    return out


def utilisation_overrides(cfg, entities: List[Dict], facilities: List[Dict]) -> Dict[Tuple[str, D], float]:
    """(facility_id, month_end) -> utilisation for scripted facilities."""
    by_id = {e["entity_id"]: e for e in entities}
    out: Dict[Tuple[str, D], float] = {}
    for f in facilities:
        e = by_id.get(f["entity_id"], {})
        key = e.get("storyline_key")
        if key == "sunda" and e.get("is_group_lead") and S.on(cfg, S.SUNDA_EWS):
            for me in _month_ends(D(2025, 10, 1), D(2027, 3, 31)):
                if not f["is_revolving"]:
                    u = 0.78        # amortising term loan: flat, below the EWS utilisation floor
                elif me < D(2026, 2, 1):
                    u = 0.78 if me < D(2026, 1, 1) else 0.80
                elif me < D(2026, 3, 1):
                    u = 0.90
                else:
                    u = 0.97        # >= 95% from March (storyline 1)
                out[(f["facility_id"], me)] = u
        elif key == "tanaka" and e.get("is_group_lead") and S.on(cfg, S.TANAKA) and f["is_revolving"]:
            for me in _month_ends(D(2026, 7, 1), D(2027, 3, 31)):
                out[(f["facility_id"], me)] = S.TANAKA_SCRIPT["util_spike"]
        if f.get("closed_date"):  # fully drawn until prepaid, nil at the closing month-end
            for me in _month_ends(f["origination_date"], f["closed_date"])[:-1]:
                out[(f["facility_id"], me)] = f["base_utilisation"]
            out[(f["facility_id"], _month_ends(f["closed_date"], f["closed_date"])[0])] = 0.0
    return out


def redate_storyline_facilities(cfg, entities: List[Dict], facilities: List[Dict]) -> None:
    """No new money for Sunda mid-cascade: its facilities originated from Oct-2025 are re-dated
    2.5 years earlier (same tenor) so the book predates the January news."""
    if not S.on(cfg, S.SUNDA_EWS):
        return
    sunda = {e["entity_id"] for e in S.group_entities(entities, "sunda")}
    for f in facilities:
        if f["entity_id"] in sunda and f["origination_date"] >= D(2025, 10, 1):
            f["origination_date"] -= TD(days=913)
            f["maturity_date"] -= TD(days=913)
            if f["maturity_date"] <= D.fromisoformat(cfg.as_of_date):
                f["maturity_date"] += TD(days=1826)


def adjust_covenants(cfg, entities: List[Dict], facilities: List[Dict], tests: List[Dict],
                     health_lut: Dict) -> Tuple[List[Dict], List[str]]:
    """Sunda's scripted covenant path + exactly five blind-spot names (leverage headroom 0-10% at
    the latest test, three of them healthy enough to stay off the watchlist). Returns the adjusted
    tests and the blind-spot obligor ids."""
    seed = cfg.random_seed
    as_of = D.fromisoformat(cfg.as_of_date)
    by_id = {e["entity_id"]: e for e in entities}
    fac_by_id = {f["facility_id"]: f for f in facilities}
    out = list(tests)
    if S.on(cfg, S.SUNDA_EWS):
        lead = S.lead_entity(entities, "sunda")["entity_id"]
        sf = [f for f in facilities if f["entity_id"] == lead and f["has_covenant"]
              and f["facility_type"] == "Syndicated Loan"]
        if sf:
            fid, obl = sf[0]["facility_id"], sf[0]["obligor_id"]
            thr = S.SUNDA["covenant_threshold"]
            path = {D(2026, 3, 31): 3.30, S.SUNDA["covenant_test"]: S.SUNDA["covenant_actual"],
                    D(2026, 6, 30): 4.80, D(2026, 9, 30): 5.10}
            out = [t for t in out if not (t["facility_id"] == fid and t["covenant_type"] == "Net Debt/EBITDA"
                                          and t["test_date"] >= D(2026, 1, 1))]
            for t in out:
                if t["facility_id"] == fid and t["covenant_type"] == "Net Debt/EBITDA":
                    t["threshold"] = thr
                    t["headroom_pct"] = round((thr - t["actual"]) / thr, 4)
                    t["breached"] = t["actual"] > thr
            for td, actual in path.items():
                if td <= as_of:
                    out.append({"facility_id": fid, "obligor_id": obl, "covenant_type": "Net Debt/EBITDA",
                                "test_date": td, "threshold": thr, "actual": actual,
                                "headroom_pct": round((thr - actual) / thr, 4), "breached": actual > thr,
                                "waiver": False, "is_latest_test": False,
                                "test_basis": "FY2025 Annual" if td == S.SUNDA["covenant_test"] else "Quarterly"})
    blind: List[str] = []
    if S.on(cfg, S.COVENANT_BLIND_SPOT):
        latest = [t for t in out if t["covenant_type"] == "Net Debt/EBITDA" and t["test_date"] == D(2026, 9, 30)]
        cands = {}
        for t in latest:
            e = by_id[fac_by_id[t["facility_id"]]["entity_id"]]
            if e.get("storyline_key") or t["obligor_id"] in cands:
                continue
            h = health_lut.get((e["entity_id"], D(2026, 9, 1)), 0.6)
            cands[t["obligor_id"]] = (h, rng.hash64(seed, "blind", t["obligor_id"]))
        ranked = sorted(cands.items(), key=lambda kv: kv[1][1])
        healthy = [o for o, (h, _) in ranked if h >= 0.75][:3]       # stay Green -> off the watchlist
        weak = [o for o, (h, _) in ranked if h <= 0.30][:2]          # already Amber -> on the watchlist
        blind = healthy + weak
        for t in latest:
            if t["obligor_id"] in blind:
                hr = 0.03 + 0.06 * rng.unit(seed, "blindhr", t["facility_id"])
                t["actual"] = round(t["threshold"] * (1 - hr), 2)
                t["headroom_pct"] = round((t["threshold"] - t["actual"]) / t["threshold"], 4)
                t["breached"] = False
    for t in out:
        t.setdefault("test_basis", "Quarterly")
    latest_by: Dict = {}
    for t in out:
        t["is_latest_test"] = False
        k = (t["facility_id"], t["covenant_type"])
        if k not in latest_by or t["test_date"] > latest_by[k]["test_date"]:
            latest_by[k] = t
    for t in latest_by.values():
        t["is_latest_test"] = True
    return out, blind


# ---- deposits ----------------------------------------------------------------------------------
HK_RAMP = (S.HK_CASA_SCRIPT["move_from"], S.HK_CASA_SCRIPT["move_to"])
SUNDA_OUTFLOW_FACTOR = 0.30   # account-level drop; with the health slide it measures ~35% in 30 days


def deposit_story(cfg, entities: List[Dict], accounts: List[Dict], health_lut: Dict) -> Dict[str, Dict]:
    """account_id -> {deposit_mult, story_kind, story_rate} for storyline accounts.

    HK CASA (storyline 5): the three hk_casa entities are scaled so the HK book at end-May is
    moved / (casa_may - casa_aug) (~USD 6.9bn) with a 62% CASA ratio; from June to August their
    current accounts lose `moved_usd` into their term / money-market accounts.
    """
    as_of_may = D(2026, 5, 1)
    by_id = {e["entity_id"]: e for e in entities}
    out: Dict[str, Dict] = {}
    if S.on(cfg, S.SUNDA_EWS):
        lead = S.lead_entity(entities, "sunda")["entity_id"]
        for a in accounts:
            if a["entity_id"] == lead:
                out[a["account_id"]] = {"deposit_mult": 1.0, "story_kind": "sunda_outflow",
                                        "story_rate": SUNDA_OUTFLOW_FACTOR}
    if S.on(cfg, S.HK_CASA):
        sc = S.HK_CASA_SCRIPT
        trio = {e["entity_id"] for e in S.group_entities(entities, "hk_casa")}

        def may_bal(a):  # expected end-May balance before any storyline scaling (E[noise] = 1)
            if a["active_from"] > D(2026, 5, 31):
                return 0.0
            return a["base_balance_usd"] * (0.4 + 0.8 * health_lut.get((a["entity_id"], as_of_may), 0.6))

        hk_other = [a for a in accounts if by_id[a["entity_id"]]["booking_country"] == "HK"
                    and a["entity_id"] not in trio]
        b_nt = sum(may_bal(a) for a in hk_other)
        c_nt = sum(may_bal(a) for a in hk_other if a["is_casa"])
        total = sc["moved_usd"] / (sc["casa_may"] - sc["casa_aug"])
        trio_casa = max(0.0, sc["casa_may"] * total - c_nt)
        trio_td = max(0.0, total - b_nt - trio_casa)
        moved_each = sc["moved_usd"] / max(1, len(trio))
        for eid in sorted(trio):
            accts = [a for a in accounts if a["entity_id"] == eid]
            casa = [a for a in accts if a["is_casa"]]
            term = [a for a in accts if not a["is_casa"]]
            for group, target in ((casa, trio_casa / len(trio)), (term, trio_td / len(trio))):
                base = sum(may_bal(a) for a in group)
                for a in group:
                    mult = target / base if base else 1.0
                    rate = moved_each / target if target else 0.0
                    out[a["account_id"]] = {"deposit_mult": round(mult, 6),
                                            "story_kind": "hk_casa_ca" if a["is_casa"] else "hk_casa_td",
                                            "story_rate": round(rate, 6)}
    return out


def deposit_factor_sql(date_col: str) -> str:
    """SQL factor applied to a storyline account's balance on `date_col` (needs columns
    story_kind / story_rate from ops.synthetic_account)."""
    o_from = S.SUNDA["outflow_from"].isoformat()
    o_days = S.SUNDA["outflow_days"]
    r0, r1 = HK_RAMP[0].isoformat(), HK_RAMP[1].isoformat()
    ramp = (f"least(1.0, greatest(0.0, datediff({date_col}, DATE'{r0}') / "
            f"{(HK_RAMP[1] - HK_RAMP[0]).days}.0))")
    return (f"CASE a.story_kind "
            f"WHEN 'sunda_outflow' THEN CASE WHEN {date_col} < DATE'{o_from}' THEN 1.0 "
            f"WHEN datediff({date_col}, DATE'{o_from}') < {o_days} "
            f"THEN 1.0 - a.story_rate * datediff({date_col}, DATE'{o_from}') / {o_days}.0 "
            f"ELSE 1.0 - a.story_rate END "
            f"WHEN 'hk_casa_ca' THEN 1.0 - a.story_rate * {ramp} "
            f"WHEN 'hk_casa_td' THEN 1.0 + a.story_rate * {ramp} ELSE 1.0 END")


# ---- payments ----------------------------------------------------------------------------------
REFINANCING_BANK = names.COMPETITOR_BANKS[2]   # took Kinokawa's USD 400m facility
CORRIDOR_BANK = names.COMPETITOR_BANKS[4]      # settles its CN->VN trade flows

def kinokawa_payments(cfg, entities: List[Dict], accounts: List[Dict], start_seq: int) -> List[Dict]:
    """Kinokawa's scripted payment flows (storyline 2): monthly loan service to the refinancing
    bank from January 2026, and CN->VN trade settlements up ~50% in H1 FY2026."""
    if not S.on(cfg, S.KINOKAWA):
        return []
    seed = cfg.random_seed
    as_of = D.fromisoformat(cfg.as_of_date)
    k = S.KINOKAWA_SCRIPT
    rows: List[Dict] = []
    seq = start_seq

    def current_account(eid):
        cas = sorted((a for a in accounts if a["entity_id"] == eid and a["account_type"] == "Current Account"),
                     key=lambda a: a["account_id"])
        return cas[0] if cas else None

    def pay(acct, d, purpose, direction, cpty_cc, amount, bank):
        nonlocal seq
        seq += 1
        e_cc = acct["booking_country"]
        rows.append({"payment_id": f"PAY-{seq:09d}", "cust_no": acct["cust_no"], "account_id": acct["account_id"],
                     "payment_date": d, "direction": direction, "channel": "SWIFT MT", "payment_purpose": purpose,
                     "debtor_country": e_cc, "counterparty_country": cpty_cc, "is_cross_border": cpty_cc != e_cc,
                     "counterparty_bank_type": "Other Bank", "counterparty_bank_name": bank,
                     "amount_usd": round(amount, 2),
                     "counterparty_ref": f"{acct['cust_no']}-CP9{seq % 10:02d}" if purpose == "Trade Settlement" else None,
                     "stp_flag": True, "repair_reason": None,
                     "processing_seconds": 4 + rng.randint(seed, 0, 30, "kinops", seq)})

    lead = S.lead_entity(entities, "kinokawa")
    acct = current_account(lead["entity_id"])
    if acct:
        acct = {**acct, "booking_country": lead["booking_country"]}
        d = k["loan_service_from"]
        while d <= as_of:
            amt = k["prepay_usd"] * 0.052 / 12 * (0.97 + 0.06 * rng.unit(seed, "kinsvc", d.isoformat()))
            pay(acct, d + TD(days=14), "Loan Service", "Outbound", lead["booking_country"], amt, REFINANCING_BANK)
            d = D(d.year + (d.month == 12), d.month % 12 + 1, 1)
    src, dst = k["corridor"]
    cn = S.entity_in(entities, "kinokawa", src)
    acct = current_account(cn["entity_id"]) if cn else None
    if acct:
        acct = {**acct, "booking_country": src}
        d = D(2025, 4, 1)
        while d <= as_of:
            n = 6 if d >= D(2026, 4, 1) else 4      # +50% in H1 FY2026
            for i in range(n):  # fixed tickets so the flow grows exactly with the count
                pay(acct, d + TD(days=3 + 5 * i), "Trade Settlement", "Outbound", dst, 3_000_000.0, CORRIDOR_BANK)
            d = D(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return rows


# ---- deal pricing exceptions (storyline 8) ------------------------------------------------------
OTHER_EXCEPTION_REASONS = ["Strategic Client", "Competitive Pricing", "Cross-sell Commitment"]


def exception_reasons(cfg, deals: List[Dict]) -> Dict[str, Optional[str]]:
    """facility_id -> exception reason for deals approved below the RAROC hurdle. Exactly
    `exception_deals` seasoned deals signed in the storyline window carry "Relationship"
    (`caught_up` of them with realised RAROC back above the hurdle); every other below-hurdle
    deal carries one of the other reasons. Each deal dict needs facility_id, signing_date,
    approved_below_hurdle and realised_raroc (None if not yet seasoned)."""
    seed = cfg.random_seed
    hurdle = float(cfg.thresholds.get("raroc_hurdle", 0.12))
    out: Dict[str, Optional[str]] = {}
    for d in deals:
        if d["approved_below_hurdle"]:
            out[d["facility_id"]] = OTHER_EXCEPTION_REASONS[rng.hash64(seed, "excr", d["facility_id"]) % 3]
    if S.on(cfg, S.BELOW_HURDLE):
        sc = S.BELOW_HURDLE_SCRIPT
        lo, hi = sc["deal_window"]
        pool = sorted((d for d in deals if d["approved_below_hurdle"] and lo <= d["signing_date"] <= hi
                       and d["realised_raroc"] is not None),
                      key=lambda d: rng.hash64(seed, "relexc", d["facility_id"]))
        caught = [d for d in pool if d["realised_raroc"] >= hurdle][:sc["caught_up"]]
        lagging = [d for d in pool if d["realised_raroc"] < hurdle][:sc["exception_deals"] - sc["caught_up"]]
        for d in caught + lagging:
            out[d["facility_id"]] = sc["exception_reason"]
    return out


# ---- Meridian Agri Holdings: why entity resolution matters (storyline 3) -------------------------
# The lead's five scripted source records (mirrors the case in tests/unit/test_er_rules.py): v1 rules
# resolve them into 3 golden records (KYC + core A + CRM; core B's "HLDGS"; the trade record booked to
# the wrong country), v2 (abbreviation expansion + cross-country blocking) merges them into one.
MERIDIAN_RECORDS = [  # (source, name as recorded, country, carries LEI, carries tax id, within-source dup)
    ("core_customer", "MERIDIAN AGRI HOLDINGS (SINGAPORE)", "SG", False, True, False),
    ("core_customer", "MERIDIAN AGRI HLDGS PTE LTD", "SG", False, False, True),
    ("crm_account", "Meridian Agri Holdings Pte Ltd", "SG", False, False, False),
    ("trade_party", "Meridian Agri Holdings (Singapore)", "MY", False, False, False),
    ("kyc_customer", "Meridian Agri Holdings (Singapore) Pte Ltd", "SG", True, True, False),
]
MERIDIAN_TH_SOURCES = ["ext_company_master", "core_customer", "kyc_customer", "crm_account"]


def meridian_lead_records(cfg, entities: List[Dict]) -> Optional[Tuple[str, List[Tuple]]]:
    """(lead entity_id, scripted records) when the Meridian storyline is on."""
    if not S.on(cfg, S.MERIDIAN_ER):
        return None
    return S.lead_entity(entities, S.MERIDIAN["key"])["entity_id"], MERIDIAN_RECORDS


def meridian_th_entity(cfg, entities: List[Dict]) -> Optional[Dict]:
    """The Thai subsidiary onboarded in 2025 (new to the bank, unmatched to the group at intake)."""
    if not S.on(cfg, S.MERIDIAN_ER):
        return None
    return S.entity_in(entities, S.MERIDIAN["key"], S.MERIDIAN["onboarding_country"])


def meridian_trade_scale(cfg, entities: List[Dict], party_map: Dict[str, str], txns: List[Dict]) -> Optional[str]:
    """Scale the Meridian lead's trade instruments so those live at the Mar-2026 ER run total the
    scripted outstanding (the exposure the v2 merge brings back to the group). Returns the party id."""
    if not S.on(cfg, S.MERIDIAN_ER):
        return None
    m = S.MERIDIAN
    party = party_map.get(S.lead_entity(entities, m["key"])["entity_id"])
    run = m["v2_run_date"]
    mine = [t for t in txns if t["party_id"] == party]
    live = sum(t["amount_usd"] for t in mine
               if t["txn_date"] <= run and (t.get("closed_date") is None or t["closed_date"] > run))
    if not party or not live:
        return party
    k = m["trade_outstanding_usd"] / live
    for t in mine:
        for col in ("amount_usd", "amount_ccy", "commission_usd", "outstanding_usd"):
            if t.get(col) is not None:
                t[col] = round(t[col] * k, 2)
    return party


# ---- below-hurdle cluster (storyline 8) ----------------------------------------------------------
JC_LENDING_ONLY_MARGIN_FACTOR = 0.70   # Japanese subsidiaries price tighter (brief §6.3) ...
MIN_MARGIN_BPS = 35


def tighten_jc_lending_only(cfg, entities: List[Dict], xref: List[Dict], facilities: List[Dict]) -> int:
    """... and lending-only relationships carry no ancillary revenue, so most of them sit below the
    RoRWA hurdle (~40 of them: the storyline-8 cluster). Returns the number of facilities repriced."""
    if not S.on(cfg, S.BELOW_HURDLE):
        return 0
    other = {x["entity_id"] for x in xref if x["source_system"] in ("core_customer", "tsy_counterparty", "trade_party")}
    by_id = {e["entity_id"]: e for e in entities}
    n = 0
    for f in facilities:
        e = by_id[f["entity_id"]]
        if e["segment"] == S.BELOW_HURDLE_SCRIPT["segment"] and not e.get("storyline_key") and f["entity_id"] not in other:
            f["margin_bps"] = max(MIN_MARGIN_BPS, int(f["margin_bps"] * JC_LENDING_ONLY_MARGIN_FACTOR))
            n += 1
    return n
