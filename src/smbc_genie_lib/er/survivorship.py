"""Golden-record identity survivorship (PLAN §6).

Attribute-level source priority among valid values:
  legal name, country, LEI-like id   ext > KYC > credit > core > CRM > trade > tsy
  immediate parent                   ext > KYC > CRM, else a majority vote over the other records
  client group                       majority vote over all records' parents, ext > KYC > CRM on ties
A name cut at a source field limit loses to a complete one; a country that the record's own name
contradicts, or that lies outside the booking footprint, loses to a trustworthy one.
golden_record_confidence = 0.5 x link strength + 0.25 x source coverage + 0.25 x id agreement.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Callable, Dict, List, Optional, Tuple

from .. import naming
from .records import SOURCE_RANK, StdRecord

PARENT_SOURCES = ["ext_company_master", "kyc_customer", "crm_account"]
TRUSTED_COUNTRY = ("ok", "corrected", "inferred")
LINK_STRENGTH = {"deterministic": 1.0, "steward": 0.9}  # fuzzy links count their score
SINGLETON_LINK, NO_ID_AGREEMENT, FULL_COVERAGE_SOURCES = 0.5, 0.5, 4
_PARENS = re.compile(r"\([^)]*\)|\([^)]*$")


def _ranked(members: List[StdRecord]) -> List[StdRecord]:
    return sorted(members, key=lambda r: (SOURCE_RANK.get(r.source_system, 99), r.source_id))


def by_priority(members: List[StdRecord], value: Callable, valid: Optional[Callable] = None):
    """(value, source) from the highest-priority record with a valid value (any value as fallback)."""
    ranked = _ranked(members)
    for check in ([valid] if valid else []) + [None]:
        for r in ranked:
            v = value(r)
            if v and (check is None or check(r)):
                return v, r.source_system
    return None, None


def short_name(raw: Optional[str]) -> Optional[str]:
    """Display name: the recorded name without its parenthetical location and trailing legal form."""
    if not raw:
        return None
    words = _PARENS.sub(" ", raw).split()
    for n in range(min(4, len(words) - 1), 0, -1):
        if naming.normalize(" ".join(words[-n:])) in naming.LEGAL_FORM_VARIANTS:
            words = words[:-n]
            break
    return " ".join(words).strip(" ,.") or raw


def survive_parent(members: List[StdRecord]) -> Tuple[Optional[str], Optional[str], str]:
    """(immediate parent, client group, basis). The immediate parent follows the source priority
    ext > KYC > CRM (else a majority vote over the other records). The client group is the majority
    vote over every record's parent with that priority breaking ties, so one stale link can't move a
    client to another group; basis says whether the priority source stood or was outvoted."""
    ranked = _ranked(members)
    votes = Counter(r.parent for r in members if r.parent)
    if not votes:
        return None, None, "none"
    top = max(votes.values())
    consensus = next(r.parent for r in sorted(ranked, key=lambda r: PARENT_SOURCES.index(r.source_system)
                                              if r.source_system in PARENT_SOURCES else 9)
                     if r.parent and votes[r.parent] == top)
    for src in PARENT_SOURCES:
        for r in ranked:
            if r.source_system == src and r.parent:
                return r.parent, consensus, ("priority_source" if r.parent == consensus else "majority_vote")
    return consensus, consensus, "majority_vote"


def confidence(members: List[StdRecord], links: Dict[str, Tuple[str, float]], lei: Optional[str]) -> float:
    if len(members) == 1:
        link = SINGLETON_LINK
    else:
        vals = []
        for r in members:
            method, score = links.get(r.key, ("unmatched", 0.0))
            vals.append(LINK_STRENGTH.get(method, score))
        link = sum(vals) / len(vals)
    coverage = min(1.0, len({r.source_system for r in members}) / FULL_COVERAGE_SOURCES)
    ids = [r.lei == lei for r in members if r.lei] if lei else []
    taxes = Counter(r.tax for r in members if r.tax)
    if taxes:
        top_tax = taxes.most_common(1)[0][0]
        ids += [r.tax == top_tax for r in members if r.tax]
    agree = sum(ids) / len(ids) if ids else NO_ID_AGREEMENT
    return round(0.5 * link + 0.25 * coverage + 0.25 * agree, 4)


def survive(members: List[StdRecord], links: Optional[Dict[str, Tuple[str, float]]] = None) -> Dict:
    """Golden identity attributes for one cluster of standardised records."""
    links = links or {}
    name, name_src = by_priority(members, lambda r: r.name, lambda r: not r.std.truncated)
    # registry names drop the location, so siblings can share one; the display name keeps it
    located = [r.name for r in _ranked(members) if r.std.location and not r.std.truncated]
    country, country_src = by_priority(members, lambda r: r.eff_country,
                                       lambda r: r.country_check in TRUSTED_COUNTRY)
    lei, lei_src = by_priority(members, lambda r: r.lei)
    parent, group, group_basis = survive_parent(members)
    aliases = sorted({r.name for r in members if r.name and r.name != name})
    return {
        "legal_name": name, "legal_name_source": name_src, "short_name": short_name(name),
        "display_name": located[0] if located else name,
        "aliases": aliases, "lei_like_id": lei, "country_of_incorporation": country,
        "country_source": country_src, "client_group_id": group, "immediate_parent_id": parent,
        "parent_basis": group_basis,
        "source_systems_present": sorted({r.source_system for r in members}, key=lambda s: SOURCE_RANK.get(s, 99)),
        "n_source_records": len(members),
        "n_distinct_lei": len({r.lei for r in members if r.lei}),
        "golden_record_confidence": confidence(members, links, lei),
    }
