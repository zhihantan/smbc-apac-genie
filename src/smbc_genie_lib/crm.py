"""CRM: pipeline, account plans and next-best-product (brief §5; Coverage/CRM Genie space).

For clients with a CRM account identity (~40% of the book) this builds the coverage view: the
sales pipeline (opportunities by stage), annual account plans (deliberately 8-12% optimistic vs
actual, the plan_optimism realism knob), and next-best-product recommendations derived from real
product gaps (no lending, FX wallet leaking to competitors, importing without trade finance,
suppliers to onboard). All pure Python and deterministic. Keyed by the CRM account id so gold
resolves to the golden client via ER.
"""
from __future__ import annotations

from typing import Dict, List, Set

from . import rng

OPP_PRODUCTS = [("Term Loan", 16), ("Revolving Credit Facility", 14), ("FX Forward Programme", 14),
                ("Trade Finance Line", 13), ("Supply Chain Finance", 10), ("Cash Management Mandate", 12),
                ("Interest Rate Hedge", 8), ("Green / Sustainability-Linked Loan", 8), ("Syndicated Loan", 5)]
# stage -> win probability
OPP_STAGES = [("Prospecting", 0.10), ("Qualification", 0.25), ("Proposal", 0.45),
              ("Negotiation", 0.70), ("Won", 1.0), ("Lost", 0.0)]
STAGE_WEIGHTS = [("Prospecting", 22), ("Qualification", 20), ("Proposal", 18),
                 ("Negotiation", 14), ("Won", 16), ("Lost", 10)]
N_OPPS = ([0, 1, 2, 3, 4], [30, 30, 22, 12, 6])
PRIORITY_BY_TIER = {"Strategic": "High", "Core": "Medium", "Transactional": "Low"}


def rm_code(seed: int, crm_id: str) -> str:
    """Owning RM, matching the crm_account.rm_code convention from the bronze source."""
    return f"RM{rng.hash64(seed, 'rm', crm_id) % 120 + 1:03d}"


def _amount(seed: int, wealth: float, key: str, eid: str) -> float:
    return round(rng.lognormal(seed, 14.0, 0.9, key, eid) * max(wealth, 0.2), 2)


def build_opportunities(cfg, entities: List[Dict], crm_map: Dict[str, str]) -> List[Dict]:
    seed = cfg.random_seed
    as_of = cfg.as_of_date
    import datetime as _dt
    base = _dt.date.fromisoformat(as_of)
    out: List[Dict] = []
    oi = 0
    for e in entities:
        crm = crm_map.get(e["entity_id"])
        if not crm:
            continue
        n = rng.weighted_choice(seed, N_OPPS[0], N_OPPS[1], "nopp", e["entity_id"])
        for j in range(n):
            oi += 1
            product = rng.weighted_choice(seed, [p for p, _ in OPP_PRODUCTS], [w for _, w in OPP_PRODUCTS],
                                          "oppprod", e["entity_id"], j)
            stage = rng.weighted_choice(seed, [s for s, _ in STAGE_WEIGHTS], [w for _, w in STAGE_WEIGHTS],
                                        "oppstage", e["entity_id"], j)
            prob = dict(OPP_STAGES)[stage]
            created = base - _dt.timedelta(days=rng.randint(seed, 20, 500, "oppage", e["entity_id"], j))
            close = created + _dt.timedelta(days=rng.randint(seed, 30, 270, "oppclose", e["entity_id"], j))
            status = "Won" if stage == "Won" else ("Lost" if stage == "Lost" else "Open")
            out.append({
                "opportunity_id": f"OPP-{oi:06d}", "crm_account_id": crm, "owner_rm": rm_code(seed, crm),
                "product": product, "stage": stage, "win_probability": prob,
                "expected_revenue_usd": _amount(seed, e.get("group_wealth", 1.0), "oppamt", e["entity_id"] + str(j)),
                "created_date": created.isoformat(), "expected_close_date": close.isoformat(),
                "status": status, "segment": e["segment"], "relationship_tier": e["relationship_tier"],
            })
    return out


def build_account_plans(cfg, entities: List[Dict], crm_map: Dict[str, str]) -> List[Dict]:
    """Annual account plans for FY2025/FY2026 — planned revenue runs plan_optimism (8-12%) above
    actual, the realism knob behind 'plans vs actuals' coverage conversations."""
    seed = cfg.random_seed
    lo = float(cfg.realism.get("plan_optimism_min", 0.08))
    hi = float(cfg.realism.get("plan_optimism_max", 0.12))
    out: List[Dict] = []
    for e in entities:
        crm = crm_map.get(e["entity_id"])
        if not crm:
            continue
        for fy in (2025, 2026):
            actual = _amount(seed, e.get("group_wealth", 1.0), f"planact{fy}", e["entity_id"])
            optimism = round(lo + (hi - lo) * rng.unit(seed, "planopt", e["entity_id"], fy), 4)
            out.append({
                "plan_id": f"AP-{fy}-{crm}", "crm_account_id": crm, "fiscal_year": fy,
                "fiscal_year_label": f"FY{fy}", "owner_rm": rm_code(seed, crm),
                "actual_revenue_usd": actual, "planned_revenue_usd": round(actual * (1 + optimism), 2),
                "plan_optimism": optimism, "strategic_priority": PRIORITY_BY_TIER.get(e["relationship_tier"], "Low"),
                "wallet_share_target": round(0.4 + 0.5 * rng.unit(seed, "wst", e["entity_id"], fy), 3),
            })
    return out


def build_nbp(cfg, entities: List[Dict], crm_map: Dict[str, str], has_lending: Set[str],
              has_fx: Set[str], has_trade: Set[str]) -> List[Dict]:
    """Next-best-product recommendations from real product gaps, ranked by propensity."""
    seed = cfg.random_seed
    out: List[Dict] = []
    for e in entities:
        crm = crm_map.get(e["entity_id"])
        if not crm:
            continue
        eid = e["entity_id"]
        is_corp = e["segment"] in ("Japanese Corporate", "Non-Japanese Large Corporate")
        # (product, rationale, base propensity by gap type)
        cands = []
        if eid not in has_lending and is_corp:
            cands.append(("Credit Facility", "Transaction-banking client with no lending relationship", 0.70))
        if eid not in has_fx:
            cands.append(("FX & Treasury", "Cross-border flows with no FX dealing relationship", 0.66))
        elif eid in has_fx:
            cands.append(("FX Wallet Recapture", "FX wallet partially routed to competitor banks", 0.60))
        if eid not in has_trade and is_corp:
            cands.append(("Trade Finance", "Importer/exporter with no trade finance line", 0.68))
        if eid in has_trade:
            cands.append(("Supply Chain Finance", "Trade anchor with un-onboarded supplier base", 0.55))
        # score with idiosyncratic noise, then rank by propensity (rank 1 = highest)
        scored = sorted(((p, r, round(max(0.1, base - 0.25 * rng.unit(seed, "nbp", eid, p)), 3))
                         for (p, r, base) in cands), key=lambda x: x[2], reverse=True)
        for rank, (product, rationale, prop) in enumerate(scored[:3], start=1):
            out.append({
                "crm_account_id": crm, "owner_rm": rm_code(seed, crm), "rank": rank,
                "recommended_product": product, "rationale": rationale, "propensity_score": prop,
                "expected_revenue_usd": _amount(seed, e.get("group_wealth", 1.0), f"nbp{rank}", eid),
                "segment": e["segment"],
            })
    return out
