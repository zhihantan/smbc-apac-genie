"""Storyline script (PLAN §5.4; `storylines:` switches in config/smbc_genie.yaml; DECISIONS D44).

The single source of every scripted entity, date and value in the 13 storylines. Each generator
that carries part of a story reads it from here, so the same story shows up consistently in every
feed it touches (e.g. Sunda's January news, February outflow, April DPD and June downgrade).
Generators gate their storyline code on `on(cfg, KEY)` and find the scripted entities with the
helpers below, never by hard-coded ids. Base generators keep non-storyline entities away from
storyline thresholds so the scripted counts hold exactly.

Storyline groups (truth.build_groups) carry `storyline_key` = kinokawa / hayashi / tanaka / sunda /
meridian / banksia; the three HK electronics subsidiaries carry the entity-level key "hk_casa".
"""
from __future__ import annotations

import datetime as _dt
from typing import Dict, List, Optional

D = _dt.date

# config switch names (config/smbc_genie.yaml `storylines:`)
SUNDA_EWS = "sunda_energi_ews_cascade"          # 1
KINOKAWA = "kinokawa_wallet_recapture"          # 2
MERIDIAN_ER = "meridian_entity_resolution"      # 3
TANAKA = "tanaka_parent_support"                # 4
HK_CASA = "hk_casa_migration"                   # 5
VN_IN_TRADE = "vn_in_trade_surge"               # 6
KYC_BACKLOG = "kyc_migration_backlog"           # 7 (implemented in onboarding.py / kyc.py)
BELOW_HURDLE = "below_hurdle_cluster"           # 8
CASHFLOW_UPGRADE = "cashflow_model_upgrade"     # 9
AU_RENEWABLES = "au_renewables_positive"        # 10
COVENANT_BLIND_SPOT = "covenant_blind_spot"     # 11
PAYMENTS_REPLAY = "payments_replay_17jun"       # 12
SHARE_STALENESS = "jp_share_staleness"          # 13


def on(cfg, key: str) -> bool:
    """Is this storyline switched on in config (default on)?"""
    return bool(cfg.storylines.get(key, True))


# ---- scripted entities ----------------------------------------------------------------------
def group_entities(entities: List[Dict], storyline_key: str) -> List[Dict]:
    """Entities of a storyline group (or the hk_casa trio), lead first then by id."""
    rows = [e for e in entities if e.get("storyline_key") == storyline_key]
    return sorted(rows, key=lambda e: (not e["is_group_lead"], e["entity_id"]))


def lead_entity(entities: List[Dict], storyline_key: str) -> Dict:
    """The storyline group's lead entity: Sunda (Jakarta), Tanaka (Singapore), Kinokawa (Singapore),
    Meridian (Singapore HQ), Banksia (Sydney), Hayashi (Hong Kong)."""
    return group_entities(entities, storyline_key)[0]


def entity_in(entities: List[Dict], storyline_key: str, country: str, nth: int = 0) -> Optional[Dict]:
    """The nth entity of a storyline group booked in a country (e.g. Kinokawa's VN entity)."""
    rows = [e for e in group_entities(entities, storyline_key) if e["booking_country"] == country]
    return rows[nth] if len(rows) > nth else None


def scripted_entity_ids(entities: List[Dict]) -> set:
    return {e["entity_id"] for e in entities if e.get("storyline_key")}


# ---- 1. Sunda Energi Nusantara: EWS cascade (lead entity, Jakarta) -----------------------------
SUNDA = {
    "key": "sunda",
    "news_from": D(2026, 1, 5), "news_to": D(2026, 1, 30), "news_topic": "Coal Regulation",
    "news_sentiment": (-0.7, -0.5), "news_items": 8,           # January coal-regulation cluster
    "outflow_from": D(2026, 2, 1), "outflow_days": 30, "outflow_pct": 0.35,  # deposits -35% in 30 days
    "util_month_end": D(2026, 3, 31), "util": 0.95,             # March utilisation >= 95%
    "missed_payment_due": D(2026, 3, 31),                       # DPD 15 on 15-Apr, 30 on 30-Apr, 90 by end-Jun
    "covenant_test": D(2026, 5, 15), "covenant": "Net Debt / EBITDA", "covenant_actual": 4.6,
    "covenant_threshold": 4.0,                                  # breach on the FY2025 spread
    "downgrade_date": D(2026, 6, 12), "grade_from": 7, "grade_to": 9, "stage_to": 3,
    "watchlist_band": "Red", "ecl_delta_usd": 48_000_000,
    "score_path": [(D(2026, 1, 5), 22.0), (D(2026, 6, 30), 78.0)],  # EWS score pinned at these dates
    "review_due": D(2026, 5, 29), "review_days_late": 23,      # annual review submitted 23 days late
}

# ---- 2. Kinokawa Precision: wallet loss and recapture -------------------------------------------
KINOKAWA_SCRIPT = {
    "key": "kinokawa",
    "prepay_date": D(2026, 2, 16), "prepay_usd": 400_000_000,  # facility refinanced away (Q4 FY2025)
    "loan_service_from": D(2026, 1, 1),                        # monthly loan-service payments to another bank
    "revenue_yoy": -0.22, "deposit_change_max": 0.05,           # revenue down ~22% YoY, deposits hold
    "plan_revision_date": D(2026, 7, 10),                      # FY2026 plan reset mid-year
    "corridor": ("CN", "VN"), "corridor_growth": 0.50,          # CN->VN trade settlements +50%
    "signals": ["TRADE_CORRIDOR_GROWTH", "SCF_ANCHOR_CANDIDATE", "FX_FLOW_VIA_OTHER_BANK",
                "LOAN_SERVICE_TO_OTHER_BANK"],
    "nbp_top_product": "Supply Chain Finance",
    "scf_opportunity_usd": 120_000_000, "scf_opportunity_created": D(2026, 8, 12),
}

# ---- 3. Meridian Agri Holdings: why entity resolution matters -----------------------------------
MERIDIAN = {
    "key": "meridian",
    "scripted_records": 5,              # core x2 (different spellings), CRM, trade, KYC; one wrong country
    "v1_golden_records": 3, "v2_golden_records": 1, "v2_run_date": D(2026, 3, 31),
    "exposure_uplift": 0.40, "attention_threshold_usd": 500_000_000,
    "lending_limit_usd": 400_000_000, "lending_utilisation": 0.90,   # group lending (a sibling obligor)
    "trade_outstanding_usd": 154_000_000,   # on the lead's mis-countried trade record at the Mar-26 run
    "trade_turnover_usd": 300_000_000,
    "onboarding_country": "TH", "onboarding_request": D(2025, 5, 12),
    "match_timing": "After Account Opening",
}

# ---- 4. Tanaka Chemical: JP parent support (lead entity, Singapore) ------------------------------
TANAKA_SCRIPT = {
    "key": "tanaka",
    "amber_month": D(2026, 7, 1), "util_spike": 0.93,           # utilisation spike + weak FY2025 ICR
    "weak_icr": 2.1,
    "parent_upgrade_date": D(2026, 6, 18), "parent_grade_from": 4, "parent_grade_to": 3,
    "support_type": "Keepwell",
    "override_band": "Green", "override_reason": "parent support confirmed (JP data)",
    "reconfirm_date": D(2026, 8, 13),   # falls inside the JP share outage -> source_data_stale
}

# ---- 5. HK CASA migration (the three hk_casa entities) -----------------------------------------
HK_CASA_SCRIPT = {
    "key": "hk_casa",
    "move_from": D(2026, 6, 1), "move_to": D(2026, 8, 31), "moved_usd": 900_000_000,
    "td_tenor_months": (3, 6), "casa_may": 0.62, "casa_aug": 0.49, "casa_tolerance": 0.015,
    "surplus_forecast_month": D(2026, 5, 1),                   # May cash-flow forecasts flag surpluses
    "signal": "DEPOSIT_SURPLUS", "signal_action_lag_days": 40,
}

# ---- 6. VN and IN trade surge --------------------------------------------------------------------
VN_IN = {
    "import_countries": ("VN", "IN"), "source_countries": ("CN", "KR", "JP"),
    "yoy_growth": 0.40, "window": (D(2026, 4, 1), D(2026, 9, 30)),   # H1 FY2026 vs H1 FY2025
    "sg_booking_share": 0.45,
    "opportunities": 14, "closed": 10, "won": 6, "signal": "TRADE_CORRIDOR_GROWTH",
}

# ---- 8. Below-hurdle cluster (hurdles from config thresholds) ------------------------------------
BELOW_HURDLE_SCRIPT = {
    "segment": "Japanese Corporate", "single_product": "Lending", "count": 40,
    "ssf_raroc": 0.14,                 # Sponsor & Structured Finance clears the 12% RAROC hurdle
    "exception_deals": 9, "exception_reason": "Relationship", "caught_up": 3,
    "deal_window": (D(2025, 4, 1), D(2025, 12, 31)),
}

# ---- 9. Cash-flow model upgrade (MAPE lives in config realism; these are the liquidity events) --
CASHFLOW = {
    "window": (D(2026, 6, 1), D(2026, 9, 30)),
    "predicted_shortfalls": 23, "rcf_drawdowns_within_10d": 15, "drawdown_window_days": 10,
}

# ---- 10. AU renewables sponsor (Banksia, Sydney) ---------------------------------------------------
BANKSIA = {
    "key": "banksia",
    "news_from": D(2026, 4, 1), "news_to": D(2026, 7, 31), "news_topic": "Expansion",
    "news_items": 6, "min_avg_sentiment": 0.6,
    "green_loan_opportunities": 2, "sll_signals": 1, "signal": "SLL_ELIGIBLE",
}

# ---- 11. Covenant blind spot ----------------------------------------------------------------------
COVENANT = {
    "covenant": "Net Debt / EBITDA", "fiscal_year": 2025,
    "clients": 5, "headroom": (0.0, 0.10), "not_on_watchlist": 3,   # Sunda breaches separately
}

# ---- 12. Reprocessed payments file -------------------------------------------------------------
PAY_REPLAY = {
    "batch_id": "PAY_20260617_R", "replay_from": D(2026, 6, 1), "replay_to": D(2026, 6, 17),
    "duplicates_at_scale_1": 41_000, "tolerance_at_scale_1": 2_000,
}

# ---- 13. Delta Share staleness --------------------------------------------------------------------
SHARE_STALE = {
    "table": "share_jp_parent_rating", "gap": (D(2026, 8, 11), D(2026, 8, 15)), "lag_hours_min": 24,
}
