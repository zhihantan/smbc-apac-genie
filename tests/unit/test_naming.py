"""Name-normalisation unit tests, including the Kinokawa fragmentation example from the brief."""
from smbc_genie_lib import naming
from smbc_genie_lib.er import similarity


def test_normalize_strips_punct_and_case():
    assert naming.normalize("Kinokawa Precision (Singapore) Pte. Ltd.") == "KINOKAWA PRECISION SINGAPORE PTE LTD"


def test_strip_legal_removes_forms():
    assert naming.strip_legal("Hayashi Marine Logistics (HK) Ltd") == "HAYASHI MARINE LOGISTICS HK"
    assert naming.strip_legal("Sunda Energi Nusantara PT Tbk") == "SUNDA ENERGI NUSANTARA"


def test_core_name_drops_parentheticals_and_expands_abbrev():
    assert naming.core_name("Kinokawa Precision (Singapore) Pte. Ltd.") == "KINOKAWA PRECISION"
    assert naming.core_name("Tanaka Chemical Asia Pte Ltd") == "TANAKA CHEMICAL ASIA"
    # group suffixes are part of the brand ("Tanaka Chemical" and "Tanaka Chemical Group" are two groups)
    assert naming.core_name("Meridian Agri Hldgs") == "MERIDIAN AGRI HOLDINGS"
    assert naming.core_name("Meridian Agri Hldgs", "v1") == "MERIDIAN AGRI HLDGS"  # v1 has no abbreviations
    assert naming.core_name("Tanaka Chemical Group") != naming.core_name("Tanaka Chemical")
    assert naming.core_name("ACME Intl Mfg Co") == "ACME INTERNATIONAL MANUFACTURING"


def test_romanisation_variant_expands():
    # 'ENERGI' -> 'ENERGY' so id/my romanisation variants collide on the core name
    assert "ENERGY" in naming.core_name("Sunda Energi Nusantara")


def test_tokens_and_first_token():
    assert naming.tokens("Kinokawa Precision (Singapore) Pte Ltd") == ["KINOKAWA", "PRECISION"]
    assert naming.first_token("Hayashi Marine Logistics Ltd") == "HAYASHI"


def test_parse_name_location_and_legal_form_evidence():
    p = naming.parse_name("Kinokawa Precision (Singapore) Pte. Ltd.")
    assert (p.legal_form, p.location, set(p.name_countries)) == ("PTE LTD", "SINGAPORE", {"SG"})
    assert {"SINGAPORE", "SG"} <= p.location_tokens
    hk = naming.parse_name("Hayashi Marine Logistics (HK) Ltd")
    assert hk.core == "HAYASHI MARINE LOGISTICS" and set(hk.name_countries) == {"HK"}
    pt = naming.parse_name("Sunda Energi Nusantara (Jakarta) PT Tbk")
    assert pt.legal_form == "PT TBK" and set(pt.name_countries) == {"ID"}
    # a legal form used in several countries is no location evidence
    assert naming.parse_name("Kinokawa Precision Co Ltd").name_countries == frozenset()
    assert set(naming.parse_name("Kinokawa Precision Sdn Bhd").name_countries) == {"MY"}


def test_parse_name_truncated_core_banking_field():
    cut_place = naming.parse_name("KINOKAWA PRECISION (HO CHI MINH CIT", max_len=35)
    assert cut_place.truncated and not cut_place.core_cut
    assert cut_place.location == "HO CHI MINH CITY" and set(cut_place.name_countries) == {"VN"}
    cut_form = naming.parse_name("KINOKAWA PRECISION (SHANGHAI) CO LT", max_len=35)
    assert cut_form.core == "KINOKAWA PRECISION" and cut_form.legal_form == "CO LTD"
    cut_body = naming.parse_name("MIDORIKAWA MARINE LOGISTICS MANUFAC", max_len=35)
    assert cut_body.core_cut and cut_body.core == "MIDORIKAWA MARINE LOGISTICS MANUFAC"
    # a cut "PTE LTD" read as "PT" must not claim Indonesia
    assert naming.parse_name("TAKAMURA HEAVY INDUSTRIES (SINGAPORE) PT", max_len=40).name_countries == {"SG"}


def test_gazetteer_tolerates_one_typo():
    assert naming.parse_name("Kinokawa Precision (Singapere) Pte Ltd").location == "SINGAPORE"
    assert naming.lookup_place("Atlantis") is None


def test_soundex_and_ids():
    assert naming.soundex("Kinokawa") == naming.soundex("Kinnkawa") == "K520"
    assert (naming.soundex("Robert"), naming.soundex("Ashcraft"), naming.soundex("Pfister")) == ("R163", "A261", "P236")
    assert naming.normalize_country(" sg ") == "SG" and naming.normalize_country("Viet Nam") == "VN"
    assert naming.normalize_country("United Kingdom") == "GB" and naming.normalize_country("") is None
    assert naming.normalize_id("syntx-sg-a99 19") == "SYNTXSGA9919" and naming.normalize_id("  ") is None


def test_similarity_name_plus_address_auto_matches():
    # Identical core name + overlapping address -> auto-match (name-only tops out ~0.85).
    score = similarity.score_pair(
        "KINOKAWA PRECISION PTE LTD",
        "Kinokawa Precision (Singapore) Pte. Ltd.",
        address_a=["1", "MARINA", "BLVD", "SINGAPORE"],
        address_b=["1", "MARINA", "BOULEVARD", "SINGAPORE"],
        same_parent=True,
        same_country=True,
    )
    assert score >= similarity.AUTO_MATCH
    assert similarity.classify(score) == "auto"


def test_similarity_name_only_lands_in_steward_range():
    # Identical core name, same country, but no address evidence -> steward queue (~0.80), not auto.
    score = similarity.score_pair(
        "Kinokawa Precision Pte Ltd",
        "Kinokawa Precision (Singapore) Pte. Ltd.",
        same_country=True,
    )
    assert similarity.STEWARD_FLOOR <= score < similarity.AUTO_MATCH
    assert similarity.classify(score) == "steward"


def test_similarity_rejects_unrelated():
    score = similarity.score_pair("Banksia Renewables Partners", "Tanaka Chemical Asia")
    assert similarity.classify(score) == "reject"


def test_levenshtein_sim_bounds():
    assert similarity.levenshtein_sim("abc", "abc") == 1.0
    assert 0.0 <= similarity.levenshtein_sim("abc", "xyz") < 1.0
