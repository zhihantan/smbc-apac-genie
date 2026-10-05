"""Identity fragmentation (brief §6.1 entity-resolution truth, §3.2; DECISIONS D22, D24).

Turns each true legal entity into 2-4 messy source-system records across the seven identity
sources, with controlled noise (name variants, wrong country, stale parent, within-source
duplicates, single-source-only entities). Records carry synthetic LEI-like / tax ids at
source-specific presence rates so the deterministic matcher can anchor on them and the fuzzy
matcher handles the rest.

Pure Python and deterministic. Produces canonical records + the ground-truth xref that Phase 4's
entity resolution is scored against. The entry script projects canonical records into each
source's own (differently-named) schema so silver standardisation has real work to do.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
from typing import Dict, List, Tuple

from . import rng, storyline_injectors

IDENTITY_SOURCES = ["ext_company_master", "core_customer", "crm_account", "kyc_customer",
                    "credit_obligor", "trade_party", "tsy_counterparty"]

# Probability an entity appears in each source. ext = authoritative registry; core banking
# presence doubles as the deposit-relationship rate (an account needs a core customer record).
# The specialised products (credit=lending, trade, tsy=FX) stay at realistic penetration rates.
PRESENCE = {"ext_company_master": 0.98, "core_customer": 0.78, "kyc_customer": 0.55,
            "crm_account": 0.40, "credit_obligor": 0.35, "trade_party": 0.22, "tsy_counterparty": 0.18}
# LEI-like id presence per source (brief §6.1).
LEI_RATE = {"ext_company_master": 0.95, "kyc_customer": 0.80, "credit_obligor": 0.60,
            "tsy_counterparty": 0.50, "crm_account": 0.40, "core_customer": 0.30, "trade_party": 0.20}
# Tax id presence per source.
TAX_RATE = {"kyc_customer": 0.90, "ext_company_master": 0.85, "core_customer": 0.70,
            "tsy_counterparty": 0.50, "crm_account": 0.0, "credit_obligor": 0.0, "trade_party": 0.0}
SOURCE_PREFIX = {"ext_company_master": "EXT", "core_customer": "CORE", "crm_account": "CRM",
                 "kyc_customer": "KYC", "credit_obligor": "CRD", "trade_party": "TRD",
                 "tsy_counterparty": "TSY"}

LEGAL_SUFFIXES = ["Pte Ltd", "Co Ltd", "(HK) Ltd", "Pvt Ltd", "Pty Ltd", "Sdn Bhd", "Berhad",
                  "PT Tbk", "PT", "JSC", "PCL", "Inc", "Corp", "KK", "GmbH", "LLC", "Ltd"]
ABBREV_FWD = {"International": "Intl", "Manufacturing": "Mfg", "Holdings": "Hldgs",
              "Industries": "Inds", "Electronics": "Elec", "Technology": "Tech",
              "Services": "Svcs", "Corporation": "Corp", "Resources": "Res"}


# ---- synthetic identifiers (clearly non-real; DECISIONS D23) --------------------------
def make_lei(entity_id: str) -> str:
    h = hashlib.blake2b(entity_id.encode(), digest_size=9).hexdigest().upper()
    return f"SYNLEI{h}"  # 6 + 18 chars, not a real 20-char LEI


def make_tax_id(entity_id: str, country: str) -> str:
    h = hashlib.blake2b(f"{entity_id}:{country}".encode(), digest_size=5).hexdigest().upper()
    return f"SYNTX-{country}-{h}"


# ---- name variants (case-preserving, deterministic) ----------------------------------
def strip_legal_suffix(name: str) -> str:
    for suf in LEGAL_SUFFIXES:
        if name.endswith(" " + suf):
            return name[: -(len(suf) + 1)].strip()
    return name


def strip_parenthetical(name: str) -> str:
    import re
    return re.sub(r"\s*\([^)]*\)", "", name).strip()


def abbreviate(name: str) -> str:
    out = name
    for full, ab in ABBREV_FWD.items():
        out = out.replace(full, ab)
    return out


def truncate(name: str, n: int = 35) -> str:
    return name[:n].strip()


def swap_first_two_words(name: str) -> str:
    parts = name.split(" ")
    if len(parts) >= 2:
        parts[0], parts[1] = parts[1], parts[0]
    return " ".join(parts)


def typo(name: str, seed: int, *salt) -> str:
    letters = [i for i, ch in enumerate(name) if ch.isalpha()]
    if len(letters) < 4:
        return name
    pos = letters[rng.hash64(seed, "typopos", *salt) % len(letters)]
    repl = "aeiostnrl"[rng.hash64(seed, "typoch", *salt) % 9]
    return name[:pos] + repl + name[pos + 1:]


def record_name(legal_name: str, source: str, seed: int, entity_id: str) -> str:
    """Apply source-specific name recording style + occasional typo."""
    salt = (entity_id, source)
    n = legal_name
    if source == "ext_company_master":
        n = strip_parenthetical(legal_name)  # registry canonical, no location
    elif source == "core_customer":
        n = truncate(strip_legal_suffix(legal_name).upper() if rng.unit(seed, "coredrop", *salt) < 0.5
                     else legal_name.upper(), 35)
    elif source == "crm_account":
        n = strip_parenthetical(legal_name) if rng.unit(seed, "crmloc", *salt) < 0.5 else legal_name
    elif source == "kyc_customer":
        n = legal_name  # cleanest
    elif source == "credit_obligor":
        n = abbreviate(strip_parenthetical(legal_name))
    elif source == "trade_party":
        n = strip_legal_suffix(strip_parenthetical(legal_name))  # short brand-ish
    elif source == "tsy_counterparty":
        n = abbreviate(strip_legal_suffix(legal_name)) if rng.unit(seed, "tsyab", *salt) < 0.5 else legal_name
    if rng.unit(seed, "wordswap", *salt) < 0.03:
        n = swap_first_two_words(n)
    if rng.unit(seed, "typo", *salt) < 0.05:
        n = typo(n, seed, *salt)
    return n


# ---- fragmentation -------------------------------------------------------------------
def _sources_for(entity_id: str, seed: int, noise: Dict, full: bool = False) -> List[str]:
    if full:  # storyline entities appear everywhere so all their products/facts can attach
        return list(IDENTITY_SOURCES)
    if rng.unit(seed, "single", entity_id) < noise.get("single_source_only", 0.015):
        return [rng.choice(seed, IDENTITY_SOURCES, "singlesrc", entity_id)]
    chosen = [s for s in IDENTITY_SOURCES if rng.unit(seed, "present", s, entity_id) < PRESENCE[s]]
    for fallback in ("ext_company_master", "kyc_customer", "core_customer"):
        if len(chosen) >= 2:
            break
        if fallback not in chosen:
            chosen.append(fallback)
    return chosen


def fragment_all(cfg, entities: List[Dict], group_ids: List[str]) -> Tuple[List[Dict], List[Dict]]:
    """Return (canonical_records, xref_rows). Canonical records carry a `source_system`."""
    seed = cfg.random_seed
    noise = cfg.entity_resolution.get("noise", {})
    records: List[Dict] = []
    xref: List[Dict] = []
    counters: Dict[str, int] = {s: 0 for s in IDENTITY_SOURCES}
    # storyline entities (except Meridian, whose messy fragmentation is scripted) get full
    # source presence so every product/fact their storyline needs can attach.
    force_full = {e["entity_id"] for e in entities
                  if e.get("storyline_key") and e["storyline_key"] != "meridian"}
    meridian = storyline_injectors.meridian_lead_records(cfg, entities)       # storyline 3
    th = storyline_injectors.meridian_th_entity(cfg, entities)

    for e in entities:
        eid = e["entity_id"]
        true_lei = make_lei(eid)
        true_tax = make_tax_id(eid, e["booking_country"])
        if meridian and eid == meridian[0]:  # the lead's five scripted, deliberately messy records
            for src, name, cc, has_lei, has_tax, is_dup in meridian[1]:
                counters[src] += 1
                sid = f"{SOURCE_PREFIX[src]}-{counters[src]:06d}"
                lei, tax = (true_lei if has_lei else None), (true_tax if has_tax else None)
                records.append({"source_system": src, "source_id": sid, "name_recorded": name,
                                "country_code": cc, "parent_group_id": e["group_id"], "lei": lei, "tax_id": tax,
                                "segment_label": e["segment"], "internal_grade": e["internal_rating_grade"],
                                "is_within_source_dup": is_dup})
                xref.append({"source_system": src, "source_id": sid, "entity_id": eid, "group_id": e["group_id"],
                             "is_within_source_dup": is_dup, "has_lei": lei is not None, "has_tax_id": tax is not None,
                             "country_correct": cc == e["booking_country"], "parent_correct": True})
            continue
        sources = (list(storyline_injectors.MERIDIAN_TH_SOURCES) if th and eid == th["entity_id"]
                   else _sources_for(eid, seed, noise, full=eid in force_full))
        for src in sources:
            n_recs = 2 if rng.unit(seed, "dup", src, eid) < noise.get("dup_within_source", 0.06) else 1
            for dup in range(n_recs):
                counters[src] += 1
                sid = f"{SOURCE_PREFIX[src]}-{counters[src]:06d}"
                salt = (eid, src, dup)
                # country: 2% wrong
                cc = e["booking_country"]
                if rng.unit(seed, "wrongcc", *salt) < noise.get("wrong_country", 0.02):
                    cc = rng.choice(seed, ["US", "GB", "SG", "HK", "CN"], "badcc", *salt)
                # parent: 3% stale (points at a different group)
                parent = e["group_id"]
                if rng.unit(seed, "staleparent", *salt) < noise.get("stale_parent", 0.03) and group_ids:
                    parent = rng.choice(seed, group_ids, "badparent", *salt)
                lei = true_lei if rng.unit(seed, "lei", *salt) < LEI_RATE[src] else None
                tax = true_tax if rng.unit(seed, "tax", *salt) < TAX_RATE.get(src, 0.0) else None
                name_rec = record_name(e["legal_name"], src, seed, f"{eid}:{dup}")
                rec = {
                    "source_system": src, "source_id": sid, "name_recorded": name_rec,
                    "country_code": cc, "parent_group_id": parent, "lei": lei, "tax_id": tax,
                    "segment_label": e["segment"], "internal_grade": e["internal_rating_grade"],
                    "is_within_source_dup": dup > 0,
                }
                records.append(rec)
                xref.append({
                    "source_system": src, "source_id": sid, "entity_id": eid,
                    "group_id": e["group_id"], "is_within_source_dup": dup > 0,
                    "has_lei": lei is not None, "has_tax_id": tax is not None,
                    "country_correct": cc == e["booking_country"],
                    "parent_correct": parent == e["group_id"],
                })
    return records, xref
