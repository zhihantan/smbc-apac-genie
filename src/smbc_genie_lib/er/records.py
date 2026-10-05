"""Source identity records for entity resolution (PLAN §6; DECISIONS D22, D24).

Projects the seven bronze identity masters (each with its own column names) onto one canonical
record, standardises it per rule set and decides when each record became visible to ER
(`record_available_from`), so the six quarterly runs can be replayed on as-of snapshots:

  kyc_customer   -> customer_since (relationship start)
  core_customer  -> first open date of its accounts in bronze.core_account
  anything else  -> assumed to pre-date the first run (history start); overrides win when given.

Records are keyed "<source_system>:<source_id>". No truth ids anywhere.
"""
from __future__ import annotations

import datetime as _dt
from collections import Counter
from typing import Dict, Iterable, List, NamedTuple, Optional

from .. import naming

# Survivorship priority for identity attributes (PLAN §6): ext > KYC > credit > core > CRM > trade > tsy.
SOURCE_PRIORITY = ["ext_company_master", "kyc_customer", "credit_obligor", "core_customer", "crm_account",
                   "trade_party", "tsy_counterparty"]
SOURCE_RANK = {s: i for i, s in enumerate(SOURCE_PRIORITY)}
# bronze column for each canonical field (sources without a field simply lack the key)
SOURCE_COLUMNS = {
    "ext_company_master": {"id": "company_id", "name": "registered_name", "country": "country_code",
                           "parent": "parent_company_id", "lei": "lei_code", "tax": "tax_ref"},
    "kyc_customer": {"id": "kyc_id", "name": "legal_entity_name", "country": "country_of_incorp",
                     "parent": "parent_entity_id", "lei": "lei", "tax": "tax_identification_no"},
    "credit_obligor": {"id": "obligor_id", "name": "obligor_name", "country": "country",
                       "parent": "parent_obligor_grp", "lei": "lei"},
    "core_customer": {"id": "cust_no", "name": "cust_name", "country": "cntry", "parent": "parent_cust_grp",
                      "lei": "lei_code", "tax": "tax_no"},
    "crm_account": {"id": "account_id", "name": "account_name", "country": "country",
                    "parent": "ultimate_parent_id", "lei": "lei"},
    "trade_party": {"id": "party_id", "name": "party_name", "country": "party_country", "lei": "lei"},
    "tsy_counterparty": {"id": "cpty_id", "name": "cpty_name", "country": "cpty_country", "lei": "lei",
                         "tax": "tax_id"},
}
# source attributes carried through to silver (non-identity survivorship happens downstream)
SOURCE_ATTRS = {"ext_company_master": ["listing_status"], "kyc_customer": ["kyc_risk_rating", "customer_since"],
                "credit_obligor": ["internal_grade"], "core_customer": ["cust_type"],
                "crm_account": ["segment_label", "rm_code"], "trade_party": [], "tsy_counterparty": []}
FIELD_LIMIT_SHARE = 0.05  # a source whose names pile up at one max length has a field limit

BASIS_KYC, BASIS_CORE = "kyc_customer_since", "core_first_account_open"
BASIS_OVERRIDE, BASIS_ASSUMED = "override", "assumed_pre_existing"


class StdRecord(NamedTuple):
    key: str
    source_system: str
    source_id: str
    name: str                   # as recorded
    std: naming.StdName
    country: Optional[str]      # ISO-2 as recorded
    eff_country: Optional[str]  # country the rule set blocks / scores on (v2: corrected from the name)
    country_check: str          # ok | corrected | suspect | inferred | missing
    parent: Optional[str]
    lei: Optional[str]
    tax: Optional[str]


def rec_key(source_system: str, source_id: str) -> str:
    return f"{source_system}:{source_id}"


def project(source_system: str, row: Dict) -> Dict:
    """Canonical record from one bronze row (dict of bronze columns)."""
    cols = SOURCE_COLUMNS[source_system]
    rec = {"source_system": source_system, "source_id": str(row[cols["id"]])}
    for fld in ("name", "country", "parent", "lei", "tax"):
        val = row.get(cols[fld]) if fld in cols else None
        rec[fld] = None if val is None or str(val).strip() == "" else str(val)
    rec["attrs"] = {a: row.get(a) for a in SOURCE_ATTRS[source_system]}
    rec["key"] = rec_key(source_system, rec["source_id"])
    return rec


def detect_field_limits(records: Iterable[Dict]) -> Dict[str, int]:
    """Source -> name field length limit, where >= 5% of its names sit exactly at the maximum length."""
    lens: Dict[str, Counter] = {}
    for r in records:
        lens.setdefault(r["source_system"], Counter())[len(r["name"] or "")] += 1
    out = {}
    for src, c in lens.items():
        top = max(c)
        if top >= 20 and c[top] >= FIELD_LIMIT_SHARE * sum(c.values()):
            out[src] = top
    return out


def availability(rec: Dict, core_first_open: Dict[str, _dt.date], default: _dt.date,
                 overrides: Optional[Dict[str, _dt.date]] = None) -> tuple:
    """(record_available_from, basis) for a canonical record."""
    if overrides and rec["key"] in overrides:
        return overrides[rec["key"]], BASIS_OVERRIDE
    if rec["source_system"] == "kyc_customer" and rec["attrs"].get("customer_since"):
        return _dt.date.fromisoformat(str(rec["attrs"]["customer_since"])[:10]), BASIS_KYC
    if rec["source_system"] == "core_customer" and rec["source_id"] in core_first_open:
        return core_first_open[rec["source_id"]], BASIS_CORE
    return default, BASIS_ASSUMED


def standardise(rec: Dict, rule_version: str, footprint: Iterable[str],
                field_limits: Optional[Dict[str, int]] = None) -> StdRecord:
    """Standardised view of a canonical record under rule set v1 or v2 (PLAN §6)."""
    std = naming.parse_name(rec["name"], rule_version, (field_limits or {}).get(rec["source_system"]))
    country = naming.normalize_country(rec["country"])
    eff, check = country_check(country, std.name_countries, set(footprint))
    if rule_version == "v1":
        eff = country  # v1 trusts the recorded country
    return StdRecord(key=rec["key"], source_system=rec["source_system"], source_id=rec["source_id"],
                     name=rec["name"] or "", std=std, country=country, eff_country=eff, country_check=check,
                     parent=(rec["parent"] or "").strip().upper() or None,  # a group-master key: kept as is
                     lei=naming.normalize_id(rec["lei"]), tax=naming.normalize_id(rec["tax"]))


def country_check(country: Optional[str], name_countries: frozenset, footprint: set) -> tuple:
    """(effective country, check) — v2 corrects a recorded country that its own name contradicts."""
    if country is None:
        if len(name_countries) == 1:
            return next(iter(name_countries)), "inferred"
        return None, "missing"
    if name_countries and country not in name_countries:
        if len(name_countries) == 1:
            return next(iter(name_countries)), "corrected"
        return country, "suspect"
    if country not in footprint:
        return country, "suspect"
    return country, "ok"


def is_suspect(r: StdRecord) -> bool:
    """A record whose country can't be trusted or fixed: candidate for cross-country comparison."""
    return r.country_check in ("suspect", "missing")
