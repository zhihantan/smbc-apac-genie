"""New-client onboarding schedule (brief §5.6; storyline 7; DECISIONS D43).

Clients onboarded inside the data window must not show activity before they went live. The
new-client cohort is drawn from clients that hold only deposits and payments (core-banking and
KYC identities, no lending, trade or FX identity), because new relationships start simple. It
excludes storyline entities and the lead entity of a multi-entity group, whose relationship
predates its subsidiaries'. Each cohort client gets a request date, per-stage durations, a
go-live date (its first account opens) and a first-transaction date:

  * Phase 3c-1 opens the client's accounts at go-live and starts balances at first transaction;
  * Phase 3c-3 starts its payments at first transaction;
  * Phase 3c-11 writes the onboarding case and stage events that end on those same dates.

Storyline 7 (KYC migration backlog) lives in the stage durations: the Feb-2026 workflow
migration slows requests made Mar-May 2026 (median days to live 21 -> 38), and the FI
segment's KYC Docs stage is hit hardest (+12 days). Pure Python and deterministic.
"""
from __future__ import annotations

import datetime as _dt
import math
from collections import defaultdict
from typing import Dict, List

from . import accounts, rng, storyline_injectors, storylines

WINDOW_START = _dt.date(2024, 4, 1)     # FY2024: onboarding case history starts here, as do payments
MIGRATION_START = _dt.date(2026, 2, 1)  # KYC workflow migration go-live (storyline 7)
COHORT_SHARE = 0.72                     # share of eligible clients that were onboarded in-window
LAST_REQUEST_LAG_DAYS = 75              # cohort requests stop 75 days before as-of: every case is live
THIN_EXCLUDE = {"credit_obligor", "trade_party", "tsy_counterparty"}

# (stage_no, stage_name, sla_days). Days to live = stages 1-6 (request -> first account opens);
# stage 7 runs from go-live to the first transaction.
STAGES = [(1, "Request", 2), (2, "KYC Docs", 10), (3, "Screening", 5), (4, "Risk Assessment", 5),
          (5, "Credit/Product Approval", 7), (6, "Account Open", 3), (7, "First Transaction", 30)]
STAGE_NAMES = [s for _, s, _ in STAGES]
STAGE_NO = {s: n for n, s, _ in STAGES}
SLA_DAYS = {s: d for _, s, d in STAGES}
BASE_MEAN_DAYS = {"Request": 1.2, "KYC Docs": 7.5, "Screening": 3.0, "Risk Assessment": 3.8,
                  "Credit/Product Approval": 3.5, "Account Open": 1.2}
FI_KYC_DOCS_BASE = 9.0  # FI document packs are heavier (licences, AML questionnaire)
# full migration slowdown in extra days (requests Mar-May 2026); FI's KYC Docs stage is worst
MIGRATION_EXTRA_DAYS = {"KYC Docs": 9.0, "Screening": 3.0, "Risk Assessment": 3.0,
                        "Credit/Product Approval": 2.0}
FI_KYC_DOCS_EXTRA = 12.0
FIRST_TXN_SLOW_P = 0.20  # new clients that take more than 60 days to transact


def migration_factor(cfg, request: _dt.date) -> float:
    """Share of the full migration slowdown felt by a request made on this date."""
    if not cfg.storylines.get("kyc_migration_backlog", True) or request < MIGRATION_START:
        return 0.0
    if request < _dt.date(2026, 3, 1):
        return 0.35  # Feb: migration under way
    if request < _dt.date(2026, 6, 1):
        return 1.0   # Mar-May: full backlog
    if request < _dt.date(2026, 7, 1):
        return 0.45  # Jun: clearing
    if request < _dt.date(2026, 8, 1):
        return 0.15
    return 0.0


def stage_days(cfg, key: str, segment: str, request: _dt.date) -> Dict[str, int]:
    """Whole days a case spends in each of stages 1-6 (at least 1 each)."""
    seed = cfg.random_seed
    f = migration_factor(cfg, request)
    fi = segment == "Financial Institution"
    out: Dict[str, int] = {}
    for name in STAGE_NAMES[:6]:
        fi_docs = fi and name == "KYC Docs"
        mean = FI_KYC_DOCS_BASE if fi_docs else BASE_MEAN_DAYS[name]
        extra = FI_KYC_DOCS_EXTRA if fi_docs else MIGRATION_EXTRA_DAYS.get(name, 0.0)
        noise = math.exp(rng.normal(seed, 0.0, 0.30, "stgdays", key, name))
        jitter = 0.85 + 0.30 * rng.unit(seed, "stgextra", key, name)
        out[name] = max(1, int(mean * noise + extra * f * jitter + 0.5))
    return out


def first_txn_lag_days(cfg, key: str) -> int:
    """Days from go-live to the first payment: mostly within a fortnight, ~20% over 60 days."""
    seed = cfg.random_seed
    if rng.unit(seed, "ftxnslow", key) < FIRST_TXN_SLOW_P:
        return rng.randint(seed, 61, 150, "ftxnslowd", key)
    return min(60, max(1, int(rng.lognormal(seed, math.log(8.0), 0.7, "ftxn", key))))


def eligible_entities(entities: List[Dict], xref: List[Dict]) -> List[Dict]:
    """Deposits-and-payments-only clients that can be new to the bank inside the window."""
    sources: Dict[str, set] = defaultdict(set)
    for x in xref:
        sources[x["entity_id"]].add(x["source_system"])
    group_size: Dict[str, int] = defaultdict(int)
    for e in entities:
        group_size[e["group_id"]] += 1
    out = []
    for e in entities:
        s = sources[e["entity_id"]]
        if not {"core_customer", "kyc_customer"} <= s or s & THIN_EXCLUDE or e.get("storyline_key"):
            continue
        if e["is_group_lead"] and group_size[e["group_id"]] > 1:
            continue  # a group's lead relationship predates its subsidiaries
        out.append(e)
    return out


def new_client_cohort(cfg, entities: List[Dict], xref: List[Dict]) -> Dict[str, Dict]:
    """entity_id -> onboarding schedule for the clients onboarded inside the window."""
    seed = cfg.random_seed
    as_of = _dt.date.fromisoformat(cfg.as_of_date)
    span = (as_of - _dt.timedelta(days=LAST_REQUEST_LAG_DAYS) - WINDOW_START).days
    cohort: Dict[str, Dict] = {}
    for e in eligible_entities(entities, xref):
        eid = e["entity_id"]
        if rng.unit(seed, "obcohort", eid) >= COHORT_SHARE:
            continue
        request = WINDOW_START + _dt.timedelta(days=int(rng.unit(seed, "obreq", eid) * span))
        days = stage_days(cfg, eid, e["segment"], request)
        go_live = request + _dt.timedelta(days=sum(days.values()))
        cohort[eid] = {
            "entity_id": eid, "request_date": request, "stage_days": days, "go_live_date": go_live,
            "first_txn_date": go_live + _dt.timedelta(days=first_txn_lag_days(cfg, eid)),
        }
    th = storyline_injectors.meridian_th_entity(cfg, entities)   # storyline 3: new Thai subsidiary, 2025
    if th:
        request = storylines.MERIDIAN["onboarding_request"]
        days = stage_days(cfg, th["entity_id"], th["segment"], request)
        go_live = request + _dt.timedelta(days=sum(days.values()))
        cohort[th["entity_id"]] = {
            "entity_id": th["entity_id"], "request_date": request, "stage_days": days, "go_live_date": go_live,
            "first_txn_date": go_live + _dt.timedelta(days=first_txn_lag_days(cfg, th["entity_id"])),
        }
    return cohort


def customer_since(cfg, entity: Dict, cohort: Dict[str, Dict]) -> _dt.date:
    """Relationship start: go-live for the cohort; otherwise a long-standing date that precedes
    every long-standing account (group leads skew earlier than their subsidiaries)."""
    eid = entity["entity_id"]
    if eid in cohort:
        return cohort[eid]["go_live_date"]
    as_of = _dt.date.fromisoformat(cfg.as_of_date)
    latest = as_of - _dt.timedelta(days=accounts.MIN_OPEN_DAYS + accounts.OPEN_DAYS_SPAN + 30)
    earliest = _dt.date(2001, 1, 1)
    u = rng.unit(cfg.random_seed, "custsince", eid) * (0.5 if entity["is_group_lead"] else 1.0)
    return earliest + _dt.timedelta(days=int(u * (latest - earliest).days))
