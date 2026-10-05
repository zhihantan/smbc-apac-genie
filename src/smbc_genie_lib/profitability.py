"""Relationship economics: revenue, cost-to-serve, capital and risk-adjusted returns
(brief §5.3; Profitability Genie space; DECISIONS D20).

Per credit relationship (obligor) per fiscal quarter the bank's own P&L on the relationship:
revenue by product line (lending NII + fees, deposit NII, payments/cash-management fees, other
transaction-banking/advisory), cost-to-serve, expected loss, risk-weighted assets, economic
capital and the three return metrics compared to their hurdles — RORWA, RAROC and relationship
ROE. This is the *bank's* return on the relationship, distinct from the client's own accounting
ROE in financials.py.

All economics and the return formulas are pure Python and deterministic (hence unit-tested here);
the entry script aggregates the heavy inputs (facility balances, deposits, payments, health grade)
in Spark and applies these functions. Coefficients are calibrated so each metric's distribution
straddles its hurdle, with weak / stressed / high-RWA relationships falling below — connecting the
sector downturn to the below-hurdle narrative. Deposits (the larger book) carry most revenue while
RWA comes from lending, so deposit-rich relationships clear the hurdles and lending-thin ones do
not.
"""
from __future__ import annotations

from typing import Dict

PRODUCT_LINES = ["Lending", "Deposits & Cash", "Payments", "Other TB & Advisory"]

# ---- revenue rates (annual) ----------------------------------------------------------
COMMITMENT_FEE_BPS = 35.0          # on undrawn commitment
ARRANGEMENT_FEE_BPS = 55.0         # upfront, amortised over ARRANGEMENT_TENOR_YEARS
ARRANGEMENT_TENOR_YEARS = 5.0
DEPOSIT_SPREAD_CASA = 0.020        # NII spread the bank earns on cheap CASA funding
DEPOSIT_SPREAD_TD = 0.010          # ... on pricier term deposits
PAYMENT_FEE_USD = 3.0              # per payment message
CROSS_BORDER_BPS = 10.0            # on cross-border payment value
OTHER_FEE_FRAC = 0.05              # other TB / advisory, as a fraction of the above
SSF_FEE_BPS = 45.0                 # Sponsor & Structured Finance structuring / agency fees, p.a. on limit

# ---- cost-to-serve (annual) ----------------------------------------------------------
COST_BASE_USD = {"Strategic": 160000.0, "Core": 80000.0, "Transactional": 30000.0}
COST_PER_ACCOUNT_USD = 1500.0
COST_PER_PAYMENT_USD = 1.0

# ---- risk + capital ------------------------------------------------------------------
# PD by internal grade 1 (strongest) .. 10 (weakest)
PD_BY_GRADE = [0.0004, 0.0008, 0.0015, 0.003, 0.006, 0.012, 0.025, 0.05, 0.10, 0.19]
RW_MIN, RW_MAX = 0.25, 1.25         # risk weight on EAD, by grade
ECAP_MIN, ECAP_MAX = 0.07, 0.16     # economic (unexpected-loss) capital rate on EAD, by grade
BOOK_EQUITY_RATE = 0.09             # allocated book equity on EAD
UNDRAWN_CCF = 0.5                   # credit-conversion factor on undrawn for EAD
# Return ratios are winsorised to this range for reporting: low-RWA relationships have a tiny
# denominator, so an uncapped ratio produces extreme outliers that distort averages. Caps sit
# well outside the hurdles, so the below-hurdle flags are unaffected.
RETURN_FLOOR, RETURN_CAP = -0.25, 1.00
LGD_BY_SECURITY = {"Unsecured": 0.45, "Real Estate": 0.30, "Receivables": 0.35,
                   "Cash": 0.18, "Fixed Assets": 0.35, "Inventory": 0.40}


def _grade(g) -> int:
    return max(1, min(10, int(round(g))))


def risk_weight(grade) -> float:
    return RW_MIN + (_grade(grade) - 1) / 9.0 * (RW_MAX - RW_MIN)


def pd_of(grade) -> float:
    return PD_BY_GRADE[_grade(grade) - 1]


def econ_capital_rate(grade) -> float:
    return ECAP_MIN + (_grade(grade) - 1) / 9.0 * (ECAP_MAX - ECAP_MIN)


def lgd_of(security: str) -> float:
    return LGD_BY_SECURITY.get(security, 0.45)


def relationship_pnl(cfg, agg: Dict) -> Dict:
    """Full quarterly relationship P&L + returns from one aggregated input row.

    `agg` carries (per obligor-quarter): avg_drawn, avg_limit, wgt_margin_bps, lgd_blended,
    avg_dep_bal, casa_share, n_pay, xb_amt, grade_avg, n_acct, relationship_tier.
    """
    grade = _grade(agg["grade_avg"])
    ead = agg["avg_drawn"] + UNDRAWN_CCF * max(0.0, agg["avg_limit"] - agg["avg_drawn"])

    lend_nii = agg["avg_drawn"] * agg["wgt_margin_bps"] / 10000.0 / 4.0
    commit_fee = max(0.0, agg["avg_limit"] - agg["avg_drawn"]) * COMMITMENT_FEE_BPS / 10000.0 / 4.0
    arrange_fee = agg["avg_limit"] * ARRANGEMENT_FEE_BPS / 10000.0 / ARRANGEMENT_TENOR_YEARS / 4.0
    rev_lending = lend_nii + commit_fee + arrange_fee

    dep_spread = agg["casa_share"] * DEPOSIT_SPREAD_CASA + (1.0 - agg["casa_share"]) * DEPOSIT_SPREAD_TD
    rev_deposits = agg["avg_dep_bal"] * dep_spread / 4.0

    rev_payments = agg["n_pay"] * PAYMENT_FEE_USD + agg["xb_amt"] * CROSS_BORDER_BPS / 10000.0
    rev_other = OTHER_FEE_FRAC * (rev_lending + rev_deposits + rev_payments)
    if agg.get("segment") == "Sponsor & Structured Finance":   # structured deals carry agency / structuring fees
        rev_other += agg["avg_limit"] * SSF_FEE_BPS / 10000.0 / 4.0
    revenue_total = rev_lending + rev_deposits + rev_payments + rev_other

    cost_q = (COST_BASE_USD.get(agg["relationship_tier"], 60000.0) + agg["n_acct"] * COST_PER_ACCOUNT_USD) / 4.0 \
        + agg["n_pay"] * COST_PER_PAYMENT_USD
    el_q = pd_of(grade) * agg["lgd_blended"] * ead / 4.0

    rwa = ead * risk_weight(grade)
    economic_capital = ead * econ_capital_rate(grade)
    book_equity = ead * BOOK_EQUITY_RATE
    net_profit_q = revenue_total - cost_q - el_q
    net_profit_annualised = net_profit_q * 4.0

    rorwa_raw = net_profit_annualised / rwa if rwa else 0.0
    raroc_raw = net_profit_annualised / economic_capital if economic_capital else 0.0
    roe_raw = net_profit_annualised / book_equity if book_equity else 0.0
    # hurdle flags come from the raw ratios; stored ratios are winsorised so averages are robust
    rorwa = min(RETURN_CAP, max(RETURN_FLOOR, rorwa_raw))
    raroc = min(RETURN_CAP, max(RETURN_FLOOR, raroc_raw))
    roe = min(RETURN_CAP, max(RETURN_FLOOR, roe_raw))

    t = cfg.thresholds
    return {
        "ead": round(ead, 2), "rwa": round(rwa, 2), "economic_capital": round(economic_capital, 2),
        "book_equity": round(book_equity, 2), "internal_grade": grade,
        "rev_lending": round(rev_lending, 2), "rev_deposits": round(rev_deposits, 2),
        "rev_payments": round(rev_payments, 2), "rev_other": round(rev_other, 2),
        "revenue_total": round(revenue_total, 2), "cost_to_serve": round(cost_q, 2),
        "expected_loss": round(el_q, 2), "net_profit": round(net_profit_q, 2),
        "net_profit_annualised": round(net_profit_annualised, 2),
        "rorwa": round(rorwa, 4), "raroc": round(raroc, 4), "roe": round(roe, 4),
        "below_rorwa_hurdle": rorwa_raw < float(t.get("rorwa_hurdle", 0.012)),
        "below_raroc_hurdle": raroc_raw < float(t.get("raroc_hurdle", 0.12)),
        "below_roe_hurdle": roe_raw < float(t.get("roe_hurdle", 0.10)),
    }


def deal_pricing(cfg, *, facility_id: str, obligor_id: str, limit_usd: float,
                 base_utilisation: float, priced_margin_bps: float, security_type: str,
                 grade, ancillary_revenue: float = 0.0) -> Dict:
    """Standalone lending economics for one facility: the margin needed to clear the RORWA
    hurdle on lending alone, and whether the priced margin clears it. Many relationship-driven
    deals are priced below the standalone hurdle and rely on ancillary (deposit/fee) revenue —
    that gap is the pricing-discipline story, read against the relationship P&L.
    """
    g = _grade(grade)
    drawn = limit_usd * base_utilisation
    undrawn = max(0.0, limit_usd - drawn)
    ead = drawn + UNDRAWN_CCF * undrawn
    lgd = lgd_of(security_type)
    rwa = ead * risk_weight(g)
    el = pd_of(g) * lgd * ead
    cost = ead * 0.003  # lending ops-cost proxy for standalone pricing
    hurdle = float(cfg.thresholds.get("rorwa_hurdle", 0.012))

    # revenue needed (annual) to clear hurdle = hurdle*RWA + cost + EL; commitment fee covers
    # the undrawn, the rest must come from the drawn margin.
    revenue_needed = hurdle * rwa + cost + el
    commit_rev = undrawn * COMMITMENT_FEE_BPS / 10000.0
    hurdle_margin_bps = max(0.0, (revenue_needed - commit_rev) / drawn * 10000.0) if drawn else 0.0

    priced_rev = drawn * priced_margin_bps / 10000.0 + commit_rev
    standalone_rorwa = (priced_rev - cost - el) / rwa if rwa else 0.0
    ecap = ead * econ_capital_rate(g)
    # relationship pricing: the deal is credited with its share of the client's ancillary revenue
    origination_raroc = (priced_rev + ancillary_revenue - cost - el) / ecap if ecap else 0.0
    meets = priced_margin_bps >= hurdle_margin_bps
    gap = priced_margin_bps - hurdle_margin_bps
    return {
        "facility_id": facility_id, "obligor_id": obligor_id, "internal_grade": g,
        "ead": round(ead, 2), "rwa": round(rwa, 2), "lgd": round(lgd, 4),
        "priced_margin_bps": round(priced_margin_bps, 1),
        "hurdle_margin_bps": round(hurdle_margin_bps, 1),
        "margin_gap_bps": round(gap, 1), "standalone_rorwa": round(standalone_rorwa, 4),
        "meets_standalone_hurdle": meets,
        "origination_raroc": round(min(RETURN_CAP, max(RETURN_FLOOR, origination_raroc)), 4),
        "approved_below_hurdle": origination_raroc < float(cfg.thresholds.get("raroc_hurdle", 0.12)),
        "pricing_status": "Above hurdle" if gap >= 25 else ("At hurdle" if gap >= -25 else "Below hurdle"),
    }
