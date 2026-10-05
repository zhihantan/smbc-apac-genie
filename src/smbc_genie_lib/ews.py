"""Early-Warning System (EWS) scoring + trigger catalog (brief §5.4; Early Warning Genie space).

Composite early-warning scores (0-100, banded Green / Amber / Red) derived from behaviour already
generated upstream — latent credit health, facility utilisation, covenant headroom, deposit
outflow and wallet leakage to competitor banks — so the brief's "signals precede downgrades"
story is real rather than drawn. The scoring is pure Python and deterministic, hence unit-tested
here; the entry script computes the heavy input aggregations and the daily fan-out in Spark over
the already-written ops/bronze tables.

Base scores deliberately leave most of the book Green with a realistic Amber/Red tail coming from
the negative-sector cycle (coal / shipping / petrochem ramping down through 2025-26). The
storyline injectors (e.g. Sunda Energi) later push specific names into sustained Red; the news
feed (ext_ step) later populates the External component and the NEWS_NEGATIVE trigger.
"""
from __future__ import annotations

import datetime as _dt
from typing import Dict, List, Optional

from . import rng

# ---- composite weights (sum to 1.0 over the five stress terms) -----------------------
W_HEALTH, W_UTIL, W_COV, W_DEP, W_LEAK = 0.45, 0.20, 0.15, 0.12, 0.08
COMPOSITE_GAIN = 1.12          # lifts the stressed tail into Amber/Red; healthy book stays Green

# ---- term shaping thresholds ---------------------------------------------------------
UTIL_FLOOR, UTIL_SPAN = 0.80, 0.20      # utilisation bites from 80%, saturates at 100%
COV_WARN_SPAN = 0.15                    # headroom below 15% contributes; breach saturates
DEP_SPAN = 0.25                         # a 25% MoM deposit outflow saturates the liquidity term
LEAK_FLOOR, LEAK_SPAN = 0.30, 0.40      # other-bank wallet share above 30% contributes

# ---- signal firing thresholds --------------------------------------------------------
UTIL_HIGH, UTIL_ELEVATED = 0.90, 0.80
DEP_OUTFLOW, DEP_OUTFLOW_SEVERE = -0.10, -0.25
LEAK_SIGNAL = 0.45
HEALTH_DECLINE_3M = 0.08
NEWS_NEGATIVE_MAX = -0.3                # monthly average news sentiment that fires NEWS_NEGATIVE
NEWS_SPAN = 0.6                         # sentiment of -0.6 saturates the (informational) external component
OVERRIDE_RATE = 0.03
ESCALATE_MIN_SCORE = 30.0

CREDIT, LIQUIDITY, BEHAVIOURAL, EXTERNAL = "Credit", "Liquidity", "Behavioural", "External"

# (code, name, category, default_severity, threshold_desc, description, is_active)
TRIGGERS = [
    ("UTIL_HIGH", "Facility utilisation >= 90%", CREDIT, "High",
     "max utilisation across facilities >= 0.90", "Headline funded limit nearly exhausted.", True),
    ("UTIL_ELEVATED", "Facility utilisation 80-90%", CREDIT, "Low",
     "0.80 <= max utilisation < 0.90", "Elevated draw on committed lines.", True),
    ("COV_HEADROOM_LOW", "Covenant headroom < 10%", CREDIT, "Medium",
     "min covenant headroom < 0.10 (not breached)", "Tightening covenant headroom.", True),
    ("COV_BREACH", "Covenant breach", CREDIT, "High",
     "any covenant breached in the latest test", "Financial covenant breached.", True),
    ("DEPOSIT_OUTFLOW", "Deposit outflow >= 10% MoM", LIQUIDITY, "Medium",
     "-0.25 < month-on-month deposit change <= -0.10", "Operating deposits leaving the bank.", True),
    ("DEPOSIT_OUTFLOW_SEVERE", "Deposit outflow >= 25% MoM", LIQUIDITY, "High",
     "month-on-month deposit change <= -0.25", "Severe deposit flight.", True),
    ("WALLET_LEAKAGE", "Wallet share to competitors >= 45%", BEHAVIOURAL, "Medium",
     "share of payment value to other banks >= 0.45", "Transaction wallet migrating to competitors.", True),
    ("HEALTH_DECLINE", "Credit health decline >= 8pts / 3m", CREDIT, "Medium",
     "latent health fell >= 0.08 over three months", "Deteriorating latent credit health.", True),
    ("GRADE_DOWNGRADE", "Internal grade downgrade", CREDIT, "Medium",
     "effective internal grade worse than three months ago", "Internal rating migrated down.", True),
    ("NEWS_NEGATIVE", "Adverse news sentiment", EXTERNAL, "Medium",
     "average monthly news sentiment <= -0.3", "Adverse external news / market sentiment.", True),
    ("DPD_15", "Payment 15-29 days past due", CREDIT, "Medium",
     "15 <= max days past due in the month < 30", "Scheduled payment overdue.", True),
    ("DPD_30", "Payment 30+ days past due", CREDIT, "High",
     "max days past due in the month >= 30", "Payment arrears beyond 30 days.", True),
    ("PAYMENT_DELAY", "Rising payment delays / repairs", BEHAVIOURAL, "Low",
     "sustained rise in repaired / delayed payments (populated by the payments feed)",
     "Payment execution friction.", False),
]
_TRIG_CATEGORY = {t[0]: t[2] for t in TRIGGERS}
_ANALYSTS = ["K. Watanabe", "R. Sharma", "M. Tan", "A. Reddy", "S. Goh", "J. Park", "L. Mehta", "H. Ito"]


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def band(cfg, score: float) -> str:
    """Green / Amber / Red from the configured EWS band thresholds (default 40 / 70)."""
    t = cfg.thresholds
    red = float(t.get("ews_red_min", 70))
    amber = float(t.get("ews_amber_min", 40))
    return "Red" if score >= red else "Amber" if score >= amber else "Green"


def score_components(cfg, *, health: float, util_max: float, cov_headroom_min: float,
                     cov_breached: bool, deposit_mom: float, leakage: float,
                     news_sentiment: Optional[float] = None) -> Dict:
    """Composite EWS score + the four explainable sub-components for one obligor-month.

    Returns composite_score (0-100), band, and credit/liquidity/behavioural/external components
    (also 0-100). The composite is a weighted blend of five stress terms; the sub-components
    regroup the same terms so Genie can answer "what is driving the score". The external component
    comes from news sentiment and is informational: it does not move the calibrated composite.
    """
    health_term = _clamp01(1.0 - health)
    util_term = _clamp01((util_max - UTIL_FLOOR) / UTIL_SPAN)
    cov_term = 1.0 if cov_breached else _clamp01((COV_WARN_SPAN - cov_headroom_min) / COV_WARN_SPAN)
    dep_term = _clamp01((-deposit_mom) / DEP_SPAN)
    leak_term = _clamp01((leakage - LEAK_FLOOR) / LEAK_SPAN)

    stress = (W_HEALTH * health_term + W_UTIL * util_term + W_COV * cov_term
              + W_DEP * dep_term + W_LEAK * leak_term)
    composite = round(100.0 * _clamp01(stress * COMPOSITE_GAIN), 1)
    credit = round(100.0 * _clamp01(0.50 * health_term + 0.30 * util_term + 0.20 * cov_term), 1)
    liquidity = round(100.0 * dep_term, 1)
    behavioural = round(100.0 * leak_term, 1)
    external = 0.0 if news_sentiment is None else round(100.0 * _clamp01(-news_sentiment / NEWS_SPAN), 1)
    return {
        "composite_score": composite, "band": band(cfg, composite),
        "credit_component": credit, "liquidity_component": liquidity,
        "behavioural_component": behavioural, "external_component": external,
        "health_term": health_term, "util_term": util_term, "cov_term": cov_term,
        "dep_term": dep_term, "leak_term": leak_term,
    }


def signals_for(cfg, m: Dict) -> List[Dict]:
    """Fired-trigger rows for one obligor-month given its metric dict.

    `m` carries health, util_max, cov_headroom_min, cov_breached, deposit_mom, leakage,
    grade_effective, the 3-month-lagged health_3m_ago / grade_3m_ago (may be None) and optionally
    dpd_max, news_sentiment and news_items.
    """
    out: List[Dict] = []

    def fire(code: str, value: Optional[float], severity: str, points: float, detail: str):
        out.append({"trigger_code": code, "category": _TRIG_CATEGORY[code],
                    "signal_value": None if value is None else round(value, 4),
                    "severity": severity, "points_contributed": round(points, 1), "detail": detail})

    util = m["util_max"]
    if util >= UTIL_HIGH:
        fire("UTIL_HIGH", util, "High", 100 * W_UTIL * _clamp01((util - UTIL_FLOOR) / UTIL_SPAN),
             f"Utilisation {util:.0%} of committed limit.")
    elif util >= UTIL_ELEVATED:
        fire("UTIL_ELEVATED", util, "Low", 100 * W_UTIL * _clamp01((util - UTIL_FLOOR) / UTIL_SPAN),
             f"Utilisation {util:.0%} of committed limit.")

    hr = m["cov_headroom_min"]
    if m["cov_breached"]:
        fire("COV_BREACH", hr, "High", 100 * W_COV, "Covenant breached in the latest quarterly test.")
    elif hr < cfg.thresholds.get("covenant_headroom_warn", 0.10):
        fire("COV_HEADROOM_LOW", hr, "Medium", 100 * W_COV * _clamp01((COV_WARN_SPAN - hr) / COV_WARN_SPAN),
             f"Covenant headroom {hr:.0%}.")

    dmom = m["deposit_mom"]
    if dmom <= DEP_OUTFLOW_SEVERE:
        fire("DEPOSIT_OUTFLOW_SEVERE", dmom, "High", 100 * W_DEP, f"Deposits down {-dmom:.0%} MoM.")
    elif dmom <= DEP_OUTFLOW:
        fire("DEPOSIT_OUTFLOW", dmom, "Medium", 100 * W_DEP * _clamp01((-dmom) / DEP_SPAN),
             f"Deposits down {-dmom:.0%} MoM.")

    leak = m["leakage"]
    if leak >= LEAK_SIGNAL:
        fire("WALLET_LEAKAGE", leak, "Medium", 100 * W_LEAK * _clamp01((leak - LEAK_FLOOR) / LEAK_SPAN),
             f"{leak:.0%} of payment value routed to other banks.")

    h3 = m.get("health_3m_ago")
    if h3 is not None and (h3 - m["health"]) >= HEALTH_DECLINE_3M:
        drop = h3 - m["health"]
        fire("HEALTH_DECLINE", drop, "Medium", 100 * W_HEALTH * drop,
             f"Latent health fell {drop * 100:.0f}pts over three months.")

    dpd = m.get("dpd_max") or 0
    if dpd >= 30:
        fire("DPD_30", float(dpd), "High", 0.0, f"Payment {dpd} days past due.")
    elif dpd >= 15:
        fire("DPD_15", float(dpd), "Medium", 0.0, f"Payment {dpd} days past due.")

    news = m.get("news_sentiment")
    if news is not None and news <= NEWS_NEGATIVE_MAX:
        fire("NEWS_NEGATIVE", news, "Medium", 0.0,
             f"Adverse news: average sentiment {news:+.2f} across {m.get('news_items') or 0} item(s).")

    g3 = m.get("grade_3m_ago")
    if g3 is not None and m["grade_effective"] > g3:
        notches = m["grade_effective"] - g3
        fire("GRADE_DOWNGRADE", float(notches), "Medium", notches * 3.0,
             f"Internal grade worsened {notches} notch(es) over three months.")
    return out


def lead_driver(row: Dict) -> tuple:
    """(category, reason) for the dominant sub-component of a scored row — the watchlist headline."""
    cands = [(row["credit_component"], CREDIT, "Credit deterioration (health / utilisation / covenant)"),
             (row["liquidity_component"], LIQUIDITY, "Deposit outflow"),
             (row["behavioural_component"], BEHAVIOURAL, "Wallet migration to competitors"),
             (row["external_component"], EXTERNAL, "Adverse external news")]
    _, cat, reason = max(cands, key=lambda c: c[0])
    return cat, reason


def rm_code(seed: int, obligor_id: str) -> str:
    """RM code in the RM001..RM120 space used by crm_account.rm_code (keeps watchlist joinable)."""
    return f"RM{rng.hash64(seed, 'rm', obligor_id) % 120 + 1:03d}"


def build_trigger_catalog(cfg) -> List[Dict]:
    return [{"trigger_code": c, "trigger_name": n, "category": cat, "default_severity": sev,
             "threshold_desc": thr, "description": desc, "is_active": active}
            for (c, n, cat, sev, thr, desc, active) in TRIGGERS]


def build_overrides(cfg, asof_rows: List[Dict], exclude: frozenset = frozenset()) -> List[Dict]:
    """Sparse credit-steward overrides on the model band (~3% of obligors), deterministic.

    Stewards affirm most alerts, occasionally stand a Green name up to watch (judgement ahead of
    the model), and occasionally soften a model alert where a waiver / mitigant exists.
    """
    seed = cfg.random_seed
    as_of = cfg.as_of_date
    out: List[Dict] = []
    i = 0
    for r in sorted(asof_rows, key=lambda x: x["obligor_id"]):
        obl = r["obligor_id"]
        if obl in exclude or rng.unit(seed, "ovr", obl) >= OVERRIDE_RATE:
            continue
        if r["band"] == "Green" and r["composite_score"] < ESCALATE_MIN_SCORE:
            continue  # stewards escalate ahead of the model only for names close to the Amber line
        i += 1
        sys_band = r["band"]
        cat, reason = lead_driver(r)
        u = rng.unit(seed, "ovrdir", obl)
        if sys_band == "Green":
            override_band, direction = "Amber", "Escalate"
            rationale = f"Steward escalation ahead of the model: {reason.lower()} flagged in review."
        elif u < 0.35:
            override_band = "Amber" if sys_band == "Red" else "Green"
            direction = "Soften"
            rationale = f"Model alert softened: approved waiver / mitigant covers {reason.lower()}."
        else:
            override_band, direction = sys_band, "Affirm"
            rationale = f"Model alert affirmed on review; {reason.lower()} confirmed."
        out.append({
            "override_id": f"EWS-OVR-{i:04d}", "obligor_id": obl, "override_date": as_of,
            "analyst": _ANALYSTS[rng.hash64(seed, "an", obl) % len(_ANALYSTS)],
            "system_band": sys_band, "override_band": override_band, "direction": direction,
            "lead_category": cat, "rationale": rationale,
            "expiry_date": _plus_days(as_of, 90), "status": "Active",
            "source_data_stale": False, "evidence_ref": None,
        })
    return out


def parent_support_overrides(cfg, obligor_id: str, month_rows: List[Dict], stale_on: Dict[str, bool],
                             evidence_ref: Optional[str]) -> List[Dict]:
    """Storyline 4: the steward softens the subsidiary's computed Amber to Green on Japanese-parent
    support (keepwell + June parent upgrade, both from the JP share), then re-confirms it in August —
    on a day the JP share was stale, so that re-confirmation is flagged source_data_stale."""
    from . import storylines as S
    t = S.TANAKA_SCRIPT
    amber = sorted(m["month"] for m in month_rows if m["band"] != "Green" and m["month"] >= t["amber_month"])
    if not amber:
        return []
    first = amber[0].replace(day=6)
    out = []
    for i, (when, expiry) in enumerate([(first, t["reconfirm_date"] + _dt.timedelta(days=1)),
                                        (t["reconfirm_date"], t["reconfirm_date"] + _dt.timedelta(days=90))], start=1):
        out.append({
            "override_id": f"EWS-OVR-T{i:03d}", "obligor_id": obligor_id, "override_date": when.isoformat(),
            "analyst": _ANALYSTS[0], "system_band": "Amber", "override_band": t["override_band"],
            "direction": "Soften", "lead_category": CREDIT,
            "rationale": ("Parent support confirmed (JP data): keepwell from the Japanese parent; parent "
                          "upgraded 18-Jun-2026 per the Japan share." if i == 1 else
                          "Parent support confirmed (JP data): re-confirmation of the July override."),
            "expiry_date": expiry.isoformat(), "status": "Superseded" if i == 1 else "Active",
            "source_data_stale": bool(stale_on.get(when.isoformat(), False)), "evidence_ref": evidence_ref,
        })
    return out


def _plus_days(iso: str, days: int) -> str:
    import datetime as _dt
    return (_dt.date.fromisoformat(iso) + _dt.timedelta(days=days)).isoformat()
