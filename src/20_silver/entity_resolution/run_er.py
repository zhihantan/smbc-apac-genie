"""Phase 4 — entity resolution: six quarterly runs replayed on as-of snapshots (PLAN §6, D22).

  silver.client_source_record      standardised identity records (7 bronze masters) + availability date
  silver.er_match                  scored pairs per run (matched / steward band / near misses >= 0.60)
  silver.er_cluster_membership     run x source record -> golden_client_id (+ how it was matched)
  silver.er_cluster_event          NEW / MERGE / SPLIT golden-id events per run
  silver.steward_queue             0.75-0.90 items per run: created, assigned, decided (or open), aging
  silver.xref_client_source        latest run: source record -> golden client, match method / score
  silver.client_golden_identity    one row per current golden client (survived identity attributes)
  silver.group_exposure_by_er_run  client group x run: lending drawn + trade outstanding attributed
  ops.entity_resolution_runs       run x source system (+ ALL): volumes, P / R / purity vs truth

Pure-Python ER on the driver (smbc_genie_lib.er); Spark for I/O. ops.synthetic_truth_xref is read only
to simulate steward decisions and to score runs. Parameter-free and re-runnable (overwrites).

  .venv/bin/python src/20_silver/entity_resolution/run_er.py --profile my-workspace
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from smbc_genie_lib import storylines  # noqa: E402
from smbc_genie_lib import truth as truth_mod  # noqa: E402
from smbc_genie_lib.config import load_config  # noqa: E402
from smbc_genie_lib.er import records, runs  # noqa: E402
from smbc_genie_lib.spark_io import get_spark, write_table  # noqa: E402

TAG = "[p04er]"
OVERRIDE_TABLE = "ops.er_record_availability"  # optional hook: source_system, source_id, available_from
NEAR_MISS = 0.60  # er_match stores rejects from this fuzzy score up (near misses); all pairs are counted
# column specs: (name, type, comment). s STRING, i INT, l BIGINT, d DOUBLE, b BOOLEAN, dt DATE,
# ts TIMESTAMP, as ARRAY<STRING>
RUN_COLS = [("run_id", "s", "ER run id, ER-YYYYMMDD (quarterly as-of run)"),
            ("run_date", "dt", "As-of date of the source snapshot the run resolved"),
            ("rule_version", "s", "Resolution rule set: v1 (2025 runs) | v2 (from Mar-2026)")]
SPECS = {
    "client_source_record": [
        ("source_system", "s", "Identity source system (bronze table name)"),
        ("source_id", "s", "Record id in its source system"),
        ("source_name_as_recorded", "s", "Client name exactly as recorded by the source"),
        ("country_as_recorded", "s", "Country as recorded by the source"),
        ("parent_ref_as_recorded", "s", "Parent / group reference as recorded (group-master key), if any"),
        ("lei_as_recorded", "s", "LEI-like id as recorded, if any"),
        ("tax_id_as_recorded", "s", "Tax id as recorded, if any"),
        ("name_normalized", "s", "Uppercase, de-accented, punctuation-free name"),
        ("core_name_v1", "s", "Rule-set v1 match key: no parenthetical location, no legal form"),
        ("core_name_v2", "s", "Rule-set v2 match key: v1 plus abbreviation expansion (Hldgs -> Holdings)"),
        ("name_tokens_v2", "as", "Tokens of the v2 core name"),
        ("legal_form", "s", "Canonical trailing legal form (PTE LTD, CO LTD, SDN BHD, ...), if any"),
        ("location", "s", "Gazetteer place of the name's parenthetical (e.g. SINGAPORE), if any"),
        ("name_country", "s", "Country implied by the name (parenthetical place or single-country legal form)"),
        ("is_name_truncated", "b", "Name hit the source's field length limit (core banking: 35 chars)"),
        ("is_core_name_cut", "b", "Truncation fell inside the name body (compared on the common prefix)"),
        ("country_iso2", "s", "Recorded country as ISO-2"),
        ("country_v2", "s", "Country used by rule set v2 (corrected when the name contradicts the record)"),
        ("country_check", "s", "ok | corrected (name contradicts record) | suspect (outside footprint) | missing"),
        ("lei_norm", "s", "LEI-like id without case / separators"),
        ("tax_id_norm", "s", "Tax id without case / separators"),
        ("first_token_v2", "s", "First significant token (blocking key)"),
        ("soundex_first_token", "s", "Soundex of the first token (blocking key)"),
        ("kyc_risk_rating", "s", "KYC risk rating (kyc_customer only)"),
        ("customer_since", "dt", "Relationship start (kyc_customer only)"),
        ("segment_label", "s", "Segment as labelled in CRM (crm_account only)"),
        ("rm_code", "s", "Primary RM code in CRM (crm_account only)"),
        ("internal_grade", "s", "Internal rating grade in the credit workflow (credit_obligor only)"),
        ("listing_status", "s", "Listed / Private (ext_company_master only)"),
        ("cust_type", "s", "Core-banking customer type (core_customer only)"),
        ("record_available_from", "dt", "Date the record existed in its source (snapshot inclusion)"),
        ("available_from_basis", "s", "kyc_customer_since | core_first_account_open | override | assumed_pre_existing"),
        ("_source_batch_id", "s", "Bronze batch id"), ("_source_ingest_ts", "s", "Bronze ingest timestamp")],
    "er_match": RUN_COLS + [
        ("left_source_system", "s", "Source system of the first record of the pair"),
        ("left_source_id", "s", "Source id of the first record"),
        ("right_source_system", "s", "Source system of the second record"),
        ("right_source_id", "s", "Source id of the second record"),
        ("blocking_key", "s", "Block that proposed the pair (country+first_token, soundex+country, "
                              "first_two_tokens, first_two_tokens_cross_country, deterministic_index)"),
        ("match_rule", "s", "deterministic_lei | deterministic_tax_country | deterministic_name_country | fuzzy"),
        ("token_jaccard", "d", "Multiset token Jaccard of the core names (0-1; weight 0.45)"),
        ("levenshtein_sim", "d", "Normalised Levenshtein similarity of the core names (0-1; weight 0.30)"),
        ("location_overlap", "d", "Location-token overlap (0-1; weight 0.15)"),
        ("same_parent", "b", "Both records name the same parent (weight 0.05)"),
        ("same_country", "b", "Same country under the run's rule set (weight 0.05)"),
        ("fuzzy_score", "d", "Composite fuzzy score 0-1"),
        ("match_score", "d", "1.0 for deterministic rules, else the fuzzy score"),
        ("veto_reason", "s", "lei_conflict | tax_conflict | location_conflict: can't be the same entity"),
        ("decision", "s", "deterministic | auto (>= 0.90) | steward (0.75-0.90) | reject"),
        ("steward_item_id", "s", "Steward item that settled this pair, if any"),
        ("is_accepted_link", "b", "Pair is an edge of the run's final match graph"),
        ("in_same_golden", "b", "Both records ended in the same golden client in this run")],
    "er_cluster_membership": RUN_COLS + [
        ("source_system", "s", "Identity source system"), ("source_id", "s", "Record id in its source system"),
        ("golden_client_id", "s", "Golden client the record resolved to in this run"),
        ("match_method", "s", "Strongest link: deterministic | fuzzy | steward | unmatched"),
        ("match_rule", "s", "Rule of that link (deterministic_lei, fuzzy_auto, steward_match, ...)"),
        ("match_score", "d", "Score of that link (1.0 deterministic); null when unmatched"),
        ("cluster_size", "i", "Source records in the golden client in this run"),
        ("is_new_record", "b", "Record first appeared in this run's snapshot"),
        ("joined_golden_in_run_id", "s", "Run in which the record joined its current golden id")],
    "er_cluster_event": RUN_COLS + [
        ("event_id", "s", "Event id (run id + sequence)"),
        ("event_type", "s", "NEW (new golden id) | MERGE (related id absorbed) | SPLIT (records left related id)"),
        ("golden_client_id", "s", "Surviving / resulting golden client id"),
        ("related_golden_client_id", "s", "Absorbed id (MERGE) or the id the records split from (SPLIT)"),
        ("n_records", "i", "Source records involved")],
    "steward_queue": RUN_COLS + [
        ("steward_item_id", "s", "Queue item id (pair id), SQ-YYMM-nnnnn"),
        ("left_source_system", "s", "Source system of the first candidate record"),
        ("left_source_id", "s", "Source id of the first candidate record"),
        ("left_source_name", "s", "Name of the first candidate as recorded"),
        ("right_source_system", "s", "Source system of the second candidate record"),
        ("right_source_id", "s", "Source id of the second candidate record"),
        ("right_source_name", "s", "Name of the second candidate as recorded"),
        ("match_score", "d", "Fuzzy score of the pair (0.75-0.90)"),
        ("candidate_pairs", "i", "0.75-0.90 record pairs between the two clusters settled by this item"),
        ("left_cluster_records", "i", "Records in the first candidate cluster"),
        ("right_cluster_records", "i", "Records in the second candidate cluster"),
        ("assigned_to", "s", "Steward (employee id, client-data team)"),
        ("created_date", "dt", "Date the run raised the item"),
        ("decided_date", "dt", "Date of the steward decision; null while open"),
        ("decision", "s", "match | no_match; null while open (simulated from truth, 2% error)"),
        ("status", "s", "Open | Decided (at the as-of date)"),
        ("days_open", "i", "Days from creation to decision, or to the as-of date while open"),
        ("is_open_at_as_of", "b", "Still undecided at the as-of date"),
        ("applied_in_run_id", "s", "Run whose resolution first reflects the decision")],
    "xref_client_source": [
        ("source_system", "s", "Identity source system"), ("source_id", "s", "Record id in its source system"),
        ("golden_client_id", "s", "Golden client (latest run)"),
        ("source_name_as_recorded", "s", "Client name as recorded by the source"),
        ("match_method", "s", "deterministic | fuzzy | steward | unmatched"),
        ("match_rule", "s", "Rule of the record's strongest link"),
        ("match_score", "d", "Score of that link (1.0 deterministic); null when unmatched"),
        ("steward_decision", "s", "Latest steward call on the record: match | no_match | pending; null if none"),
        ("resolved_in_run_id", "s", "Run in which the record joined its golden client"),
        ("resolved_at", "dt", "Run date of that run"),
        ("run_id", "s", "Latest ER run id"), ("run_date", "dt", "Latest ER run date")],
    "client_golden_identity": [
        ("golden_client_id", "s", "Durable golden client id (kept across runs; oldest id survives merges)"),
        ("legal_name", "s", "Survived legal name (ext > KYC > credit > core > CRM > trade > tsy)"),
        ("legal_name_source", "s", "Source system the legal name came from"),
        ("short_name", "s", "Legal name without location and legal form"),
        ("display_name", "s", "Highest-priority recorded name that keeps its location (unique label)"),
        ("aliases", "as", "Other names the source records use"),
        ("lei_like_id", "s", "Survived LEI-like id"),
        ("country_of_incorporation", "s", "Survived ISO-2 country (trusted values first)"),
        ("country_source", "s", "Source system the country came from"),
        ("client_group_id", "s", "Client group (group-master key): majority vote over the records' parents"),
        ("immediate_parent_id", "s", "Parent as recorded by the most authoritative source (ext > KYC > CRM)"),
        ("parent_basis", "s", "priority_source | majority_vote | none"),
        ("source_systems_present", "as", "Source systems holding the client (priority order)"),
        ("n_source_records", "i", "Source records resolved to the client"),
        ("n_distinct_lei", "i", "Distinct LEI-like ids among the records (> 1 flags a conflict)"),
        ("golden_record_confidence", "d", "0-1: 0.5 link strength + 0.25 source coverage + 0.25 id agreement"),
        ("created_run_id", "s", "Run that issued the golden id"), ("created_date", "dt", "Run date that issued it"),
        ("run_id", "s", "Latest ER run id"), ("run_date", "dt", "Latest ER run date")],
    "group_exposure_by_er_run": RUN_COLS + [
        ("client_group_id", "s", "Client group the exposure is attributed to; null = unattributed"),
        ("attribution_status", "s", "Attributed | Unattributed (record resolved to no group)"),
        ("n_golden_clients", "i", "Golden clients carrying the exposure"),
        ("n_obligor_records", "i", "credit_obligor records with drawn lending at the run date"),
        ("n_trade_party_records", "i", "trade_party records with trade outstanding at the run date"),
        ("lending_drawn_usd", "d", "Drawn lending (core_facility_balance_monthly at the run date), USD"),
        ("trade_outstanding_usd", "d", "Trade finance live at the run date (trade_finance_txn), USD"),
        ("total_exposure_usd", "d", "Lending drawn + trade outstanding, USD"),
        ("prior_resolution_exposure_usd", "d", "Same facts attributed with the previous run's resolution, USD"),
        ("resolution_change_usd", "d", "Exposure change caused by re-resolution, USD"),
        ("resolution_change_pct", "d", "resolution_change_usd / prior_resolution_exposure_usd"),
        ("prev_run_exposure_usd", "d", "The group's exposure in the previous run (its own date), USD"),
        ("change_vs_prev_run_pct", "d", "Change vs the previous run (facts and resolution)"),
        ("attention_threshold_usd", "d", "Single-borrower attention threshold, USD"),
        ("is_above_threshold", "b", "Exposure at or above the attention threshold"),
        ("crossed_threshold_on_resolution", "b", "Below the threshold under the previous resolution, at/above now")],
}
OPS_EXTRA = [("candidate_pairs", "l", "Candidate pairs scored (blocking + deterministic indexes)"),
             ("deterministic_pairs", "l", "Pairs matched by a deterministic rule"),
             ("auto_match_pairs", "l", "Pairs auto-matched (fuzzy score >= 0.90)"),
             ("steward_band_pairs", "l", "Pairs scoring 0.75-0.90"),
             ("steward_items", "l", "Steward queue items raised by the run"),
             ("steward_items_open", "l", "Of those, still open at the as-of date"),
             ("links_accepted", "l", "Edges of the final match graph"),
             ("records_new", "l", "Records new since the previous run"),
             ("purity", "d", "Cluster purity vs synthetic truth (ALL row)"),
             ("golden_new", "l", "Golden ids issued (ALL row)"), ("golden_merged", "l", "Golden ids merged away (ALL row)"),
             ("golden_split", "l", "Split events (ALL row)"),
             ("true_pairs", "l", "Same-entity record pairs in the snapshot (truth)"),
             ("predicted_pairs", "l", "Same-golden record pairs"), ("tp_pairs", "l", "Correct same-golden pairs"),
             ("rule_set_note", "s", "What the rule set adds"), ("finished_at", "ts", "Run end")]
RULE_NOTES = {"v1": "LEI / tax+country exact; blocks (country, first token), (soundex, country); fuzzy 0.45/0.30/0.15/0.05/0.05",
              "v2": "v1 + abbreviation expansion, name+legal form+country exact, country corrected from the name, "
                    "cross-country first-two-token block"}
COMMENTS = {
    "client_source_record": "Standardised client identity records from the 7 bronze identity masters, with the date "
                            "each record existed (snapshot inclusion for the replayed ER runs). No truth ids.",
    "er_match": "Scored candidate record pairs per ER run: blocking key, rule, score components, decision. Holds "
                "every matched, steward-band and near-miss pair (rejects scoring >= 0.60); clear rejects are "
                "counted in ops.entity_resolution_runs.candidate_pairs.",
    "er_cluster_membership": "Run x source record -> golden client id, with the record's strongest match link.",
    "er_cluster_event": "Golden-id events per ER run: NEW, MERGE (oldest id survives), SPLIT.",
    "steward_queue": "Simulated data-steward queue (0.75-0.90 band), one item per pair of clusters; decisions "
                     "simulated from truth with 2% error; open items at the as-of date have no decision.",
    "xref_client_source": "Latest ER run: source record -> golden client id, match method, score, steward call.",
    "client_golden_identity": "One row per current golden client: survived identity attributes (latest ER run).",
    "group_exposure_by_er_run": "Client group x ER run: lending drawn + trade outstanding at the run date attributed "
                                "via the run's resolution, and via the previous run's resolution (impact).",
}


def _schema(spec):
    from pyspark.sql.types import (ArrayType, BooleanType, DateType, DoubleType, IntegerType, LongType,
                                   StringType, StructField, StructType, TimestampType)
    types = {"s": StringType(), "i": IntegerType(), "l": LongType(), "d": DoubleType(), "b": BooleanType(),
             "dt": DateType(), "ts": TimestampType(), "as": ArrayType(StringType())}
    return StructType([StructField(c, types[t], True, {"comment": cm}) for c, t, cm in spec])


CHUNK_ROWS = 50_000  # rows per uploaded local relation
ENCODE_MAX = 120     # string columns with at most this many values travel as byte codes


def _df(spark, rows, spec):
    """DataFrame with an explicit, commented schema. The rows are uploaded from the driver, so the
    upload is kept small: built through pandas (Connect's row-by-row list conversion is ~40x slower),
    in chunks, with low-cardinality strings sent as byte codes and decoded on the server."""
    import functools

    import pandas as pd
    from pyspark.sql import functions as F
    from pyspark.sql.types import ByteType, StructField, StructType
    cols, schema = [c for c, _, _ in spec], _schema(spec)
    codes = {}
    for c, t, _ in spec:
        vals = sorted({r.get(c) for r in rows} - {None}) if t == "s" else []
        if 0 < len(vals) <= ENCODE_MAX:
            codes[c] = {v: i for i, v in enumerate(vals)}
    wire = StructType([StructField(f.name, ByteType(), True) if f.name in codes else f for f in schema.fields])
    parts = []
    for i in range(0, max(len(rows), 1), CHUNK_ROWS):
        data = [[(codes[c].get(r.get(c)) if c in codes else r.get(c)) for c in cols] for r in rows[i:i + CHUNK_ROWS]]
        parts.append(spark.createDataFrame(pd.DataFrame(data, columns=cols, dtype=object), schema=wire))
    df = functools.reduce(lambda a, b: a.unionAll(b), parts)
    comment = {c: cm for c, _, cm in spec}
    # when(...) keeps the decoded column nullable (a bare element_at on a null code trips the optimizer)
    return df.select(*[
        F.when(F.col(c).isNotNull(), F.element_at(F.array(*[F.lit(v) for v in codes[c]]), F.col(c).cast("int") + 1))
         .alias(c, metadata={"comment": comment[c]}) if c in codes else F.col(c) for c in cols])


def _write(spark, c, name, rows, spec, comment):
    t = time.time()
    n = write_table(_df(spark, rows, spec), f"{c}.silver.{name}", comment=comment)
    print(f"{TAG} silver.{name:26} {n:>8,} rows ({time.time() - t:.0f}s)")
    return n


def read_records(spark, c, cfg):
    """Canonical records from the 7 bronze identity masters + their availability dates."""
    recs = []
    for src in records.SOURCE_PRIORITY:
        for row in spark.table(f"{c}.bronze.{src}").collect():
            d = row.asDict()
            r = records.project(src, d)
            r["meta"] = {"_batch_id": d.get("_batch_id"), "_ingest_ts": d.get("_ingest_ts")}
            recs.append(r)
    first_open = {r["cust_no"]: r["d"] for r in spark.sql(
        f"SELECT cust_no, min(CAST(open_date AS DATE)) d FROM {c}.bronze.core_account GROUP BY cust_no").collect()}
    overrides = {}
    if spark.catalog.tableExists(f"{c}.{OVERRIDE_TABLE}"):
        overrides = {records.rec_key(r["source_system"], r["source_id"]): r["available_from"]
                     for r in spark.sql(f"SELECT source_system, source_id, CAST(available_from AS DATE) available_from "
                                        f"FROM {c}.{OVERRIDE_TABLE}").collect()}
    default = _dt.date.fromisoformat(cfg.history_start)
    for r in recs:
        r["available_from"], r["available_basis"] = records.availability(r, first_open, default, overrides)
    return recs, len(overrides)


def read_facts(spark, c, run_list):
    """facts[run_id][record key] = {lending, trade}: drawn lending per obligor at the run date and trade
    finance live at the run date per trade party."""
    facts = {r.run_id: {} for r in run_list}
    dates = ", ".join(f"DATE'{r.run_date}'" for r in run_list)
    by_date = {r.run_date: r.run_id for r in run_list}
    for row in spark.sql(f"""SELECT balance_date d, obligor_id k, sum(drawn_usd) v
                             FROM {c}.bronze.core_facility_balance_monthly
                             WHERE balance_date IN ({dates}) GROUP BY 1, 2""").collect():
        facts[by_date[row["d"]]].setdefault(records.rec_key("credit_obligor", row["k"]), {})["lending"] = float(row["v"] or 0)
    for row in spark.sql(f"""SELECT r.d, t.party_id k, sum(t.amount_usd) v
                             FROM {c}.bronze.trade_finance_txn t
                             JOIN (SELECT explode(array({dates})) d) r
                               ON t.txn_date <= r.d AND t.maturity_date > r.d
                             GROUP BY 1, 2""").collect():
        facts[by_date[row["d"]]].setdefault(records.rec_key("trade_party", row["k"]), {})["trade"] = float(row["v"] or 0)
    return facts


def source_record_rows(recs, std):
    from smbc_genie_lib import naming
    rows = []
    for r in recs:
        s1, s2 = std["v1"][r["key"]], std["v2"][r["key"]]
        a = r["attrs"]
        since = a.get("customer_since")
        rows.append({
            "source_system": r["source_system"], "source_id": r["source_id"], "source_name_as_recorded": r["name"],
            "country_as_recorded": r["country"], "parent_ref_as_recorded": r["parent"], "lei_as_recorded": r["lei"],
            "tax_id_as_recorded": r["tax"], "name_normalized": s2.std.normalized, "core_name_v1": s1.std.core,
            "core_name_v2": s2.std.core, "name_tokens_v2": list(s2.std.tokens), "legal_form": s2.std.legal_form or None,
            "location": s2.std.location or None,
            "name_country": next(iter(s2.std.name_countries)) if s2.std.name_countries else None,
            "is_name_truncated": s2.std.truncated, "is_core_name_cut": s2.std.core_cut,
            "country_iso2": s2.country, "country_v2": s2.eff_country, "country_check": s2.country_check,
            "lei_norm": s2.lei, "tax_id_norm": s2.tax,
            "first_token_v2": s2.std.tokens[0] if s2.std.tokens else None,
            "soundex_first_token": naming.soundex(s2.std.tokens[0]) if s2.std.tokens else None,
            "kyc_risk_rating": a.get("kyc_risk_rating"),
            "customer_since": _dt.date.fromisoformat(str(since)[:10]) if since else None,
            "segment_label": a.get("segment_label"), "rm_code": a.get("rm_code"),
            "internal_grade": a.get("internal_grade"), "listing_status": a.get("listing_status"),
            "cust_type": a.get("cust_type"), "record_available_from": r["available_from"],
            "available_from_basis": r["available_basis"],
            "_source_batch_id": r["meta"]["_batch_id"], "_source_ingest_ts": r["meta"]["_ingest_ts"]})
    return rows


def _split_key(k):
    s, _, i = k.partition(":")
    return s, i


def write_ops_runs(spark, c, rows):
    """Fill ops.entity_resolution_runs (Phase-2 DDL) and append the extra run metrics as new columns."""
    fqn = f"{c}.ops.entity_resolution_runs"
    have = {f.name for f in spark.table(fqn).schema.fields}
    types = {"l": "BIGINT", "d": "DOUBLE", "s": "STRING", "ts": "TIMESTAMP"}
    missing = [(n, t, cm) for n, t, cm in OPS_EXTRA if n not in have]
    if missing:
        cols = ", ".join(f"{n} {types[t]} COMMENT '{cm}'" for n, t, cm in missing)
        spark.sql(f"ALTER TABLE {fqn} ADD COLUMNS ({cols})")
    base = [("run_id", "s"), ("run_date", "dt"), ("rule_version", "s"), ("source_system", "s"),
            ("records_in", "l"), ("matched_deterministic", "l"), ("matched_fuzzy", "l"), ("steward_resolved", "l"),
            ("unmatched", "l"), ("duplicates_merged", "l"), ("golden_records", "l"), ("precision", "d"),
            ("recall", "d"), ("started_at", "ts")]
    spec = [(n, t, "") for n, t in base] + OPS_EXTRA
    for r in rows:
        r["rule_set_note"] = RULE_NOTES[r["rule_version"]]
        r["finished_at"] = r["started_at"] + _dt.timedelta(minutes=12)
    view = "p04er_ops_runs"
    _df(spark, rows, spec).createOrReplaceTempView(view)
    cols = ", ".join(n for n, _, _ in spec)
    spark.sql(f"INSERT OVERWRITE {fqn} ({cols}) SELECT {cols} FROM {view}")
    return spark.table(fqn).count()


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 4: entity resolution (6 replayed quarterly runs)")
    ap.add_argument("--catalog", default=None)
    ap.add_argument("--profile", default=None)
    args = ap.parse_args()
    cfg = load_config(catalog=args.catalog) if args.catalog else load_config()
    c = cfg.catalog
    spark = get_spark(args.profile)
    t0 = time.time()

    recs, n_over = read_records(spark, c, cfg)
    limits = records.detect_field_limits(recs)
    basis = Counter(r["available_basis"] for r in recs)
    print(f"{TAG} {len(recs):,} identity records from 7 sources; field limits {limits}; availability {dict(basis)}"
          f"{f'; {n_over} overrides' if n_over else ''}")
    truth = {records.rec_key(r["source_system"], r["source_id"]): r["entity_id"] for r in spark.sql(
        f"SELECT source_system, source_id, entity_id FROM {c}.ops.synthetic_truth_xref").collect()}
    missing_truth = sum(1 for r in recs if r["key"] not in truth)
    stewards = [p["employee_id"] for p in truth_mod.build_people(cfg) if p["role"] == "Onboarding Officer"]
    exact = storylines.scripted_entity_ids(truth_mod.build_entities(cfg, truth_mod.build_groups(cfg)))
    run_list = runs.run_schedule(cfg)
    out = runs.replay(cfg, recs, truth, stewards, limits, run_list, exact_entities=exact)
    print(f"{TAG} replayed {len(run_list)} runs in {time.time() - t0:.0f}s ({len(exact)} storyline entities "
          f"with error-free steward decisions)"
          f"{f'; {missing_truth} records without truth (not scored)' if missing_truth else ''}")
    facts = read_facts(spark, c, run_list)
    threshold = float(cfg.thresholds.get("single_borrower_attention_usd", 500_000_000))
    exposure = runs.group_exposure(run_list, out["membership_by_run"], out["golden_by_run"], facts, threshold)

    by_key = {r["key"]: r for r in recs}
    name = {k: r["name"] for k, r in by_key.items()}
    matches = []
    for m in out["matches"]:
        if m["decision"] == "reject" and m["fuzzy_score"] < NEAR_MISS:
            continue  # clear non-matches are counted in ops.entity_resolution_runs.candidate_pairs only
        ls, li = _split_key(m["left_key"])
        rs, ri = _split_key(m["right_key"])
        matches.append({**{k: m[k] for k in ("run_id", "run_date", "rule_version")}, "left_source_system": ls,
                        "left_source_id": li, "right_source_system": rs, "right_source_id": ri,
                        "blocking_key": m["block"], "match_rule": m["method"], "token_jaccard": m["jaccard"],
                        "levenshtein_sim": m["levenshtein"], "location_overlap": m["location"],
                        "same_parent": m["same_parent"], "same_country": m["same_country"],
                        "fuzzy_score": m["fuzzy_score"], "match_score": m["score"], "veto_reason": m["veto"],
                        "decision": m["decision"], "steward_item_id": m["steward_item_id"],
                        "is_accepted_link": m["is_link"], "in_same_golden": m["same_golden"]})
    events = [{**e, "event_id": f"{e['run_id']}-{i:05d}"} for i, e in enumerate(out["events"], 1)]
    queue = [{**q, "left_source_name": name[f"{q['left_source_system']}:{q['left_source_id']}"],
              "right_source_name": name[f"{q['right_source_system']}:{q['right_source_id']}"]}
             for q in out["steward_queue"]]
    run_date = {r.run_id: r.run_date for r in run_list}
    xref = [{**x, "source_name_as_recorded": name[f"{x['source_system']}:{x['source_id']}"],
             "resolved_at": run_date[x["resolved_in_run_id"]]} for x in out["xref"]]

    _write(spark, c, "client_source_record", source_record_rows(recs, out["std"]), SPECS["client_source_record"],
           COMMENTS["client_source_record"])
    _write(spark, c, "er_match", matches, SPECS["er_match"], COMMENTS["er_match"])
    _write(spark, c, "er_cluster_membership", out["membership"], SPECS["er_cluster_membership"],
           COMMENTS["er_cluster_membership"])
    _write(spark, c, "er_cluster_event", events, SPECS["er_cluster_event"], COMMENTS["er_cluster_event"])
    _write(spark, c, "steward_queue", queue, SPECS["steward_queue"], COMMENTS["steward_queue"])
    _write(spark, c, "xref_client_source", xref, SPECS["xref_client_source"], COMMENTS["xref_client_source"])
    _write(spark, c, "client_golden_identity", out["golden_identity"], SPECS["client_golden_identity"],
           COMMENTS["client_golden_identity"])
    _write(spark, c, "group_exposure_by_er_run", exposure, SPECS["group_exposure_by_er_run"],
           COMMENTS["group_exposure_by_er_run"])
    n = write_ops_runs(spark, c, out["run_summary"])
    print(f"{TAG} ops.entity_resolution_runs       {n:>8,} rows")
    verify(spark, c, cfg, run_list)
    print(f"{TAG} done in {time.time() - t0:.0f}s.")


def verify(spark, c, cfg, run_list):
    """Per-run quality, volumes, id stability and exposure from the written tables."""
    q = spark.sql(f"""SELECT run_id, rule_version, records_in, candidate_pairs, deterministic_pairs, auto_match_pairs,
                             steward_items, steward_items_open, golden_records, precision, recall, purity,
                             golden_merged
                      FROM {c}.ops.entity_resolution_runs WHERE source_system = 'ALL' ORDER BY run_date""").collect()
    print(f"{TAG} run          rule records  pairs   det  auto steward(open) golden  precision recall purity merged")
    for r in q:
        print(f"{TAG} {r['run_id']} {r['rule_version']:>4} {r['records_in']:>7,} {r['candidate_pairs']:>7,} "
              f"{r['deterministic_pairs']:>5,} {r['auto_match_pairs']:>5,} {r['steward_items']:>6,}({r['steward_items_open']:>3}) "
              f"{r['golden_records']:>6,} {r['precision']:>9.4f} {r['recall']:>6.4f} {r['purity']:.4f} {r['golden_merged']:>6}")
    last = q[-1]
    g_p, g_r = float(cfg.entity_resolution.get("gate_precision", 0.97)), float(cfg.entity_resolution.get("gate_recall", 0.95))
    ok = last["precision"] >= g_p and last["recall"] >= g_r
    v1_r = max(r["recall"] for r in q if r["rule_version"] == "v1")
    print(f"{TAG} gate (latest v2 run): precision {last['precision']:.4f} >= {g_p}, recall {last['recall']:.4f} >= {g_r}: "
          f"{'PASS' if ok else 'FAIL'}; best v1 recall {v1_r:.4f} (< v2: {v1_r < last['recall']})")
    src = spark.sql(f"""SELECT source_system, records_in, matched_deterministic, matched_fuzzy, steward_resolved, unmatched,
                               precision, recall FROM {c}.ops.entity_resolution_runs
                        WHERE run_id = '{last['run_id']}' AND source_system <> 'ALL' ORDER BY source_system""").collect()
    for r in src:
        rate = 1 - r["unmatched"] / r["records_in"]
        print(f"{TAG}   {r['source_system']:20} in {r['records_in']:>5,} det {r['matched_deterministic']:>5,} "
              f"fuzzy {r['matched_fuzzy']:>4,} steward {r['steward_resolved']:>4,} unmatched {r['unmatched']:>4,} "
              f"match rate {rate:.3f} P {r['precision']:.4f} R {r['recall']:.4f}")
    st = spark.sql(f"""WITH m AS (SELECT run_id, source_system, source_id, golden_client_id
                                  FROM {c}.silver.er_cluster_membership),
                       r AS (SELECT run_id, lag(run_id) OVER (ORDER BY run_id) prev FROM (SELECT DISTINCT run_id FROM m))
                       SELECT a.run_id, count(*) n,
                              sum(CASE WHEN a.golden_client_id = b.golden_client_id THEN 1 ELSE 0 END) same
                       FROM m a JOIN r ON r.run_id = a.run_id
                       JOIN m b ON b.run_id = r.prev AND b.source_system = a.source_system AND b.source_id = a.source_id
                       GROUP BY a.run_id ORDER BY a.run_id""").collect()
    print(f"{TAG} id stability (records keeping their golden id vs previous run): "
          + ", ".join(f"{r['run_id']} {r['same'] / r['n']:.4f}" for r in st))
    sq = spark.sql(f"""SELECT run_id, count(*) n, sum(CASE WHEN status = 'Open' THEN 1 ELSE 0 END) open,
                              sum(CASE WHEN decision = 'match' THEN 1 ELSE 0 END) m,
                              percentile_approx(days_open, 0.5) med, max(days_open) mx
                       FROM {c}.silver.steward_queue GROUP BY run_id ORDER BY run_id""").collect()
    print(f"{TAG} steward queue: " + "; ".join(f"{r['run_id']} {r['n']} items ({r['open']} open, {r['m']} match, "
                                              f"median {r['med']}d, max {r['mx']}d)" for r in sq))
    ex = spark.sql(f"""SELECT run_id, round(sum(total_exposure_usd)/1e9, 2) tot,
                              round(sum(CASE WHEN client_group_id IS NULL THEN total_exposure_usd END)/1e9, 3) unattr,
                              sum(CASE WHEN abs(resolution_change_pct) > 0.2 THEN 1 ELSE 0 END) moved20,
                              sum(CASE WHEN crossed_threshold_on_resolution THEN 1 ELSE 0 END) crossed
                       FROM {c}.silver.group_exposure_by_er_run GROUP BY run_id ORDER BY run_id""").collect()
    print(f"{TAG} group exposure: " + "; ".join(f"{r['run_id']} ${r['tot']}bn (unattributed ${r['unattr'] or 0}bn, "
                                               f"{r['moved20']} groups moved >20% on re-resolution, "
                                               f"{r['crossed']} crossed threshold)" for r in ex))
    dup = spark.sql(f"""SELECT (SELECT count(*) FROM {c}.silver.xref_client_source) n,
                               (SELECT count(DISTINCT source_system, source_id) FROM {c}.silver.xref_client_source) d,
                               (SELECT count(*) FROM {c}.silver.client_golden_identity) g,
                               (SELECT count(DISTINCT golden_client_id) FROM {c}.silver.xref_client_source) gx""").collect()[0]
    print(f"{TAG} xref {dup['n']:,} rows ({dup['d']:,} distinct records) -> {dup['gx']:,} golden ids; "
          f"golden identity rows {dup['g']:,}")


if __name__ == "__main__":
    main()
