"""ER rules (PLAN §6): records, similarity, blocking, deterministic / fuzzy decisions, and a
Meridian-like case that rule set v1 splits into 3 clusters and v2 merges into 1."""
import datetime as _dt

import pytest

from smbc_genie_lib.er import blocking, cluster, matching, records, similarity

FOOTPRINT = ["SG", "HK", "CN", "TH", "ID", "IN", "AU", "VN", "MY", "TW", "KR", "PH", "NZ"]
LIMITS = {"core_customer": 35}
G = "SYN-G-9001"


def _rec(src, **cols):
    return records.project(src, cols)


def _std(rows, v):
    return [records.standardise(r, v, FOOTPRINT, LIMITS) for r in rows]


def _clusters(rows, v, accept=("deterministic", "auto")):
    std = _std(rows, v)
    res = matching.match_all(std, v)
    return cluster.components([r.key for r in std], [p for p, r in res.items() if r.decision in accept]), res


# ---- a Meridian-like case: core x2 (one abbreviated), CRM, trade with a wrong country, KYC ----------
MERIDIAN = [
    ("core_customer", dict(cust_no="C1", cust_name="MERIDIAN AGRI HOLDINGS (SINGAPORE)", cntry="SG",
                           parent_cust_grp=G, tax_no="SYNTX-SG-1111")),
    ("core_customer", dict(cust_no="C2", cust_name="MERIDIAN AGRI HLDGS PTE LTD", cntry="SG", parent_cust_grp=G)),
    ("crm_account", dict(account_id="A1", account_name="Meridian Agri Holdings Pte Ltd", country="SG",
                         ultimate_parent_id=G)),
    ("trade_party", dict(party_id="T1", party_name="Meridian Agri Holdings (Singapore)", party_country="MY")),
    ("kyc_customer", dict(kyc_id="K1", legal_entity_name="Meridian Agri Holdings (Singapore) Pte Ltd",
                          country_of_incorp="SG", parent_entity_id=G, lei="SYNLEI0001",
                          tax_identification_no="SYNTX-SG-1111", customer_since="2015-01-01")),
]
SIBLING = [  # the group's Malaysian entity must stay apart
    ("kyc_customer", dict(kyc_id="K2", legal_entity_name="Meridian Agri Holdings (Kuala Lumpur) Sdn Bhd",
                          country_of_incorp="MY", parent_entity_id=G, lei="SYNLEI0002", customer_since="2016-01-01")),
    ("ext_company_master", dict(company_id="E2", registered_name="Meridian Agri Holdings Sdn Bhd", country_code="MY",
                                parent_company_id=G, lei_code="SYNLEI0002")),
]


@pytest.fixture(scope="module")
def meridian():
    return [_rec(s, **c) for s, c in MERIDIAN + SIBLING]


def test_meridian_like_v1_splits_into_three(meridian):
    comps, res = _clusters(meridian, "v1")
    mine = [c for c in comps if not any(k.endswith(("K2", "E2")) for k in c)]
    assert len(mine) == 3
    assert ["core_customer:C2"] in mine and ["trade_party:T1"] in mine  # abbreviation + wrong country
    assert res[("kyc_customer:K2", "trade_party:T1")].veto == "location_conflict"


def test_meridian_like_v2_merges_into_one(meridian):
    comps, res = _clusters(meridian, "v2")
    merged = [c for c in comps if "kyc_customer:K1" in c][0]
    assert sorted(merged) == sorted(r["key"] for r in meridian[:5])
    assert ["ext_company_master:E2", "kyc_customer:K2"] in comps  # sibling untouched
    assert res[("core_customer:C2", "crm_account:A1")].method == matching.METHOD_NAME
    assert res[("kyc_customer:K1", "trade_party:T1")].decision == "auto"  # country corrected from "(Singapore)"


# ---- records -------------------------------------------------------------------------------------
def test_project_maps_each_source_schema():
    r = _rec("tsy_counterparty", cpty_id="TSY-1", cpty_name="X", cpty_country="sg", lei=" ", tax_id="T-1")
    assert (r["key"], r["name"], r["country"], r["parent"], r["lei"], r["tax"]) == ("tsy_counterparty:TSY-1", "X", "sg",
                                                                                     None, None, "T-1")
    assert set(records.SOURCE_COLUMNS) == set(records.SOURCE_PRIORITY)


def test_availability_basis():
    d0 = _dt.date(2023, 4, 1)
    kyc = _rec("kyc_customer", kyc_id="K", legal_entity_name="N", customer_since="2025-08-01")
    core = _rec("core_customer", cust_no="C", cust_name="N")
    ext = _rec("ext_company_master", company_id="E", registered_name="N")
    first_open = {"C": _dt.date(2025, 11, 3)}
    assert records.availability(kyc, first_open, d0) == (_dt.date(2025, 8, 1), records.BASIS_KYC)
    assert records.availability(core, first_open, d0) == (_dt.date(2025, 11, 3), records.BASIS_CORE)
    assert records.availability(ext, first_open, d0) == (d0, records.BASIS_ASSUMED)
    assert records.availability(ext, {}, d0, {ext["key"]: _dt.date(2026, 1, 5)})[1] == records.BASIS_OVERRIDE


def test_field_limit_detection():
    rows = [{"source_system": "core_customer", "name": "X" * 35}] * 30 + [{"source_system": "core_customer",
                                                                          "name": "SHORT NAME"}] * 70
    rows += [{"source_system": "kyc_customer", "name": "N" * k} for k in range(10, 60)]
    assert records.detect_field_limits(rows) == {"core_customer": 35}


def test_country_check_v2_corrects_from_the_name():
    trade = _rec("trade_party", party_id="T", party_name="Kinokawa Precision (Singapore)", party_country="US")
    v1, v2 = records.standardise(trade, "v1", FOOTPRINT), records.standardise(trade, "v2", FOOTPRINT)
    assert v2.country_check == "corrected" and v2.eff_country == "SG" and v1.eff_country == "US"
    bare = records.standardise(_rec("trade_party", party_id="U", party_name="Kinokawa Precision", party_country="GB"),
                               "v2", FOOTPRINT)
    assert bare.country_check == "suspect" and records.is_suspect(bare)
    assert records.standardise(_rec("crm_account", account_id="A", account_name="X Pte Ltd", country="SG"),
                               "v2", FOOTPRINT).country_check == "ok"


# ---- similarity ----------------------------------------------------------------------------------
def test_token_jaccard_is_multiset_and_typo_tolerant():
    assert similarity.token_jaccard(["HAGIWARA", "TRADING"], ["HAGIWARA", "TRADING", "TRADING"]) == pytest.approx(2 / 3)
    assert similarity.token_jaccard(["KINNKAWA", "PRECISION"], ["KINOKAWA", "PRECISION"]) == 1.0
    assert similarity.token_jaccard(["AGAI"], ["AGRI"]) == 0.0  # short tokens need an exact match
    assert similarity.overlap({"SINGAPORE", "SG"}, {"SG"}) == 1.0 and similarity.overlap(set(), {"SG"}) == 0.0


def test_name_features_word_order_and_truncation():
    from smbc_genie_lib import naming
    swapped = similarity.name_features(naming.parse_name("Marine Hayashi Logistics"),
                                       naming.parse_name("Hayashi Marine Logistics"))
    assert swapped == (1.0, 1.0)
    cut = naming.parse_name("MIDORIKAWA MARINE LOGISTICS MANUFAC", max_len=35)
    full = naming.parse_name("Midorikawa Marine Logistics Manufacturing (Singapore) Pte Ltd")
    assert similarity.name_features(cut, full) == (1.0, 1.0)


# ---- blocking and decisions ------------------------------------------------------------------------
def test_blocking_keys_by_rule_version():
    r = _std([_rec("kyc_customer", kyc_id="K", legal_entity_name="Kinokawa Precision (Bangkok) PCL",
                   country_of_incorp="TH")], "v2")[0]
    kinds = {k[0] for k in blocking.block_keys(r, "v2")}
    assert kinds == {blocking.BLOCK_COUNTRY_TOKEN, blocking.BLOCK_SOUNDEX, blocking.BLOCK_FIRST_TWO}
    r1 = _std([_rec("kyc_customer", kyc_id="K", legal_entity_name="Kinokawa Precision (Bangkok) PCL",
                    country_of_incorp="TH")], "v1")[0]
    assert blocking.BLOCK_FIRST_TWO not in {k[0] for k in blocking.block_keys(r1, "v1")}


def test_cross_country_siblings_are_not_compared():
    rows = [_rec("kyc_customer", kyc_id="K1", legal_entity_name="Kinokawa Precision (Singapore) Pte Ltd",
                 country_of_incorp="SG", parent_entity_id=G),
            _rec("trade_party", party_id="T2", party_name="Kinokawa Precision", party_country="TH"),
            _rec("trade_party", party_id="T3", party_name="Kinokawa Precision", party_country="GB")]
    pairs = blocking.candidate_pairs(_std(rows, "v2"), "v2")
    assert ("kyc_customer:K1", "trade_party:T2") not in pairs  # two trustworthy countries: siblings
    assert pairs[("kyc_customer:K1", "trade_party:T3")] == blocking.BLOCK_CROSS_COUNTRY  # GB is suspect


def test_deterministic_rules_and_vetoes():
    a = _rec("ext_company_master", company_id="E1", registered_name="Oedo Steel Co Ltd", country_code="CN",
             lei_code="SYNLEI1", tax_ref="SYNTX-CN-1")
    b = _rec("credit_obligor", obligor_id="O1", obligor_name="Oedo Steel Co Ltd", country="CN", lei="SYNLEI1")
    c = _rec("ext_company_master", company_id="E2", registered_name="Oedo Steel Co Ltd", country_code="CN",
             lei_code="SYNLEI2")
    d = _rec("core_customer", cust_no="C1", cust_name="OEDO STEEL (SHANGHAI)", cntry="CN", tax_no="syntx cn 1")
    sa, sb, sc, sd = _std([a, b, c, d], "v2")
    assert matching.evaluate(sa, sb, "v2", "x").method == matching.METHOD_LEI
    assert matching.evaluate(sa, sd, "v2", "x").method == matching.METHOD_TAX  # separators ignored
    lei_clash = matching.evaluate(sa, sc, "v2", "x")
    assert (lei_clash.veto, lei_clash.decision) == ("lei_conflict", "reject")
    loc = matching.evaluate(*_std([_rec("crm_account", account_id="A", account_name="Oedo Steel (Bangkok) PCL",
                                        country="TH"),
                                   _rec("kyc_customer", kyc_id="K", legal_entity_name="Oedo Steel (Singapore) Pte Ltd",
                                        country_of_incorp="TH")], "v2"), "v2", "x")
    assert loc.veto == "location_conflict"


def test_name_country_exact_is_v2_only_and_skips_cut_names():
    a = _rec("ext_company_master", company_id="E1", registered_name="Waratah Mining Pty Ltd", country_code="AU")
    b = _rec("crm_account", account_id="A1", account_name="WARATAH MINING PTY. LTD.", country="AU")
    assert matching.evaluate(*_std([a, b], "v2"), "v2", "x").method == matching.METHOD_NAME
    assert matching.evaluate(*_std([a, b], "v1"), "v1", "x").method == matching.METHOD_FUZZY
    cut = _rec("core_customer", cust_no="C", cust_name="WARATAH MINING HOLDINGS MANUFACTURIN", cntry="AU")
    assert matching.name_key(_std([cut], "v2")[0]) is None


def test_scores_band_into_decisions():
    kyc = _rec("kyc_customer", kyc_id="K", legal_entity_name="Kinokawa Precision (Bangkok) PCL",
               country_of_incorp="TH", parent_entity_id=G)
    core = _rec("core_customer", cust_no="C", cust_name="KINOKAWA PRECISION (BANGKOK)", cntry="TH", parent_cust_grp=G)
    trade = _rec("trade_party", party_id="T", party_name="Kinokawa Precision", party_country="TH")
    div = _rec("crm_account", account_id="A", account_name="Kinokawa Precision Trading (Bangkok) Co Ltd",
               country="TH", ultimate_parent_id=G)
    sk, sc, st, sd = _std([kyc, core, trade, div], "v1")
    assert matching.evaluate(sk, sc, "v1", "x").decision == "auto"
    assert matching.evaluate(sk, st, "v1", "x").decision == "steward"  # name only: trade carries no location
    assert matching.evaluate(st, sd, "v1", "x").decision == "reject"   # division word = another entity
