"""Data-quality rules (pure; Phase 4b): generation from table specs, evaluation SQL, statuses, scores,
the simulated DQ history and the repo's rule catalogue (config/table_specs.yaml + config/dq_rules.yaml)."""
import datetime as _dt
import json

import pytest

from smbc_genie_lib import dq, silver

AS_OF = "2026-09-30"
SPEC = {
    "conform": {
        "xref": "silver.xref_client_source", "golden": "silver.client_golden_identity",
        "group_master": "shared.share_jp_group_master.global_group_id",
        "identity_keys": {"cust_no": "core_customer"}, "lookups": {},
    },
    "tables": {"pay": {
        "source": "bronze.pay", "domain": "payments", "comment": "Payments.", "keys": ["payment_id"],
        "client": {"key": "cust_no"}, "date": "payment_date",
        "columns": {"payment_id": "string", "cust_no": "string", "account_id": "string", "payment_date": "date",
                    "value_date": "date", "currency": "code", "direction": "string", "amount_usd": "double"},
        "checks": {"required": ["amount_usd"], "enum": {"direction": ["Inbound", "Outbound"]},
                   "enum_ref": {"currency": "silver.ref_currency.currency_code"},
                   "range": {"amount_usd": [0, None], "payment_date": ["2024-04-01", "@as_of"]},
                   "date_order": [["payment_date", "value_date"]], "not_future": ["payment_date"],
                   "fk": {"account_id": "silver.core_account.account_id", "cust_no": "silver.cust.cust_no"},
                   "fk_blocking": ["account_id"], "fresh": {"lag_days": 3}}}},
}


@pytest.fixture(scope="module")
def mini():
    specs = silver.parse_specs(SPEC)
    rules = dq.table_rules(specs.tables["pay"], specs, AS_OF, conformance="(`_raw_payment_date` IS NULL OR `payment_date` IS NOT NULL)")
    return specs, {r.kind: r for r in rules}, rules


def test_generated_rule_set(mini):
    _, by, rules = mini
    assert [r.rule_id for r in rules] == [
        "pay:key_present", "pay:key_unique", "pay:golden_key", "pay:not_future", "pay:parent_exists", "pay:references",
        "pay:valid_values", "pay:required", "pay:date_order", "pay:freshness"]
    assert {r.rule_id for r in rules if r.blocking} == {"pay:key_present", "pay:golden_key", "pay:not_future",
                                                        "pay:parent_exists"}
    assert by["key_unique"].action == "deduplicate" and not by["key_unique"].blocking
    assert by["freshness"].grain == "table" and by["freshness"].pass_expr == "max(`payment_date`) >= DATE'2026-09-27'"
    assert by["not_future"].pass_expr == "`payment_date` <= DATE'2026-09-30'"
    assert {r.dimension for r in rules} == set(dq.DIMENSIONS)


def test_layer_expressions(mini):
    _, by, _ = mini
    assert by["key_unique"].expr_for("bronze") == "_dedup_rank = 1"
    vv = by["valid_values"]
    assert "_raw_payment_date" in vv.expr_for("bronze") and "_raw_payment_date" not in vv.expr_for("silver")
    assert "`payment_date` <= DATE'2026-09-30'" in vv.expr_for("silver")      # '@as_of' bound
    assert "`payment_date` >= DATE'2024-04-01'" in vv.expr_for("silver")      # ISO bound
    assert by["references"].layers == ("silver",) and by["references"].expr_for("bronze") is None
    assert by["parent_exists"].expr_for("bronze") == "coalesce((`account_id` IS NULL OR __fk_account_id IS NOT NULL), true)"
    assert len(vv.parts_for("bronze")) == len(vv.parts_for("silver")) + 1


def test_batch_sql_adds_each_join_once_and_skips_existing(mini):
    _, by, _ = mini
    rows = [by["key_present"], by["valid_values"], by["parent_exists"], by["key_unique"]]
    sql = dq.batch_sql("v", ["payment_id"], rows, "bronze", "cat", have_cols=["__fk_account_id"], extra=["count(*) AS __x"])
    assert sql.count("FROM cat.silver.ref_currency") == 1
    assert "FROM cat.silver.core_account" not in sql          # already exposed by the staging view
    assert "count_if(NOT coalesce(_dedup_rank = 1, true)) AS f_3" in sql
    assert "AS p_1_0" in sql and "count(*) AS __x" in sql
    silver_sql = dq.batch_sql("cat.silver.pay", ["payment_id"], [by["parent_exists"]], "silver", "cat")
    assert "FROM cat.silver.core_account" in silver_sql


def test_unique_and_table_sql():
    u = dq.unique_sql("cat.silver.pay", ["a", "b"])
    assert "GROUP BY `a`, `b`" in u and "sum(n - 1)" in u
    r = dq.Rule("t:freshness", "t", "timeliness", "freshness", "d", "max(`d`) >= DATE'2026-09-27'", grain="table")
    assert dq.table_rule_sql("x", r).startswith("SELECT 1 AS total, CASE WHEN coalesce(max(`d`) >= DATE'2026-09-27'")


def test_templates_and_placeholders():
    cfg = {"rules": [], "templates": [{"for_each": ["core_customer", "trade_party"], "rule": {
        "id": "{item}:x:fmt", "table": "bronze.{item}", "layer": "bronze", "dimension": "validity",
        "description": "d", "from": "{catalog}.silver.client_source_record", "where": "source_system = '{item}'",
        "pass": "lei RLIKE '^SYNLEI[0-9A-F]{18}$'", "key": "source_id"}}]}
    rules = dq.hand_rules(cfg)
    assert [r.rule_id for r in rules] == ["core_customer:x:fmt", "trade_party:x:fmt"]
    assert rules[0].layers == ("bronze",) and rules[0].table == "bronze.core_customer"
    sql = dq.hand_sql(rules[1], {"catalog": "cat", "as_of": AS_OF})
    assert "FROM cat.silver.client_source_record WHERE source_system = 'trade_party'" in sql
    assert "{18}" in sql                                       # regex quantifiers survive substitution
    with pytest.raises(ValueError, match="unknown dimension"):
        dq.hand_rules({"rules": [{"id": "x", "table": "t", "dimension": "freshness", "description": "d",
                                  "from": "t", "pass": "true"}]})


def test_status_and_result_row(mini):
    _, by, _ = mini
    assert dq.status(0, 100, 0.0, "error") == "pass"
    assert dq.status(1, 100, 0.0, "error") == "fail" and dq.status(1, 100, 0.0, "warn") == "warn"
    assert dq.status(1, 1000, 0.001, "error") == "pass" and dq.status(0, 0, 0.0, "error") == "pass"
    assert dq.status(60, 100, 0.0, "info") == "pass"
    ts = _dt.datetime(2026, 9, 30, 12)
    row = dq.result_row(by["key_unique"], "bronze", "bronze.pay", 200, 4, ["PAY-1", "PAY-2"], "DQ-20260930", ts,
                        {"removed_by_batch": {"PAY_20260617_R": 4}})
    assert row["pass_rate"] == pytest.approx(0.98) and row["status"] == "fail" and row["action"] == "deduplicate"
    assert json.loads(row["detail"]) == {"removed_by_batch": {"PAY_20260617_R": 4}}
    clean = dq.result_row(by["key_unique"], "silver", "silver.pay", 196, 0, [], "DQ-20260930", ts)
    assert clean["action"] == "none" and clean["status"] == "pass"


def test_scores_are_mean_rule_pass_rates():
    res = [{"layer": "silver", "table_name": "silver.t", "dimension": "validity", "status": "pass", "total_rows": 100,
            "failed_rows": 0, "pass_rate": 1.0},
           {"layer": "silver", "table_name": "silver.t", "dimension": "validity", "status": "warn", "total_rows": 10,
            "failed_rows": 2, "pass_rate": 0.8}]
    s = dq.scores(res)[("silver", "silver.t", "validity")]
    assert s["score"] == pytest.approx(0.9) and s["rules"] == 2 and s["failed_rules"] == 1 and s["failed_rows"] == 2


HIST_CFG = {"baseline": {"initial_gap": [0.004, 0.025], "noise_sd": 0.0015, "floor": 0.90, "cap": 0.9995},
            "incidents": [
                {"key": "replay", "tables": ["pay"], "dimension": "uniqueness", "layers": ["bronze"], "from": "2026-06",
                 "to": "latest", "anchor": "actual", "base_offset": 0.02, "note": "replay"},
                {"key": "outage", "tables": ["share"], "dimension": "timeliness", "from": "2026-08", "to": "2026-08",
                 "score": [0.835, 0.842], "note": "outage"},
                {"key": "mig", "tables": ["kyc"], "dimension": "timeliness", "from": "2026-02", "to": "2026-05",
                 "delta": [-0.10, -0.06], "base_offset": 0.025, "note": "migration"}]}
ACTUAL = {("bronze", "bronze.pay", "uniqueness"): {"score": 0.978, "rules": 1, "failed_rules": 1, "rows": 188_000,
                                                   "failed_rows": 4_100},
          ("silver", "silver.share", "timeliness"): {"score": 1.0, "rules": 1, "failed_rules": 0, "rows": 30,
                                                     "failed_rows": 0},
          ("silver", "silver.kyc", "timeliness"): {"score": 0.92, "rules": 2, "failed_rules": 1, "rows": 5_000,
                                                   "failed_rows": 400},
          ("silver", "silver.ref", "validity"): {"score": 1.0, "rules": 1, "failed_rules": 0, "rows": 17,
                                                 "failed_rows": 0}}


def _series(rows, table, dim):
    return {r["month"]: r for r in rows if r["table_name"] == table and r["dimension"] == dim}


def test_history_shape_and_determinism():
    months = dq.month_starts("2023-04", AS_OF)
    assert len(months) == 42 and months[0] == _dt.date(2023, 4, 1) and months[-1] == _dt.date(2026, 9, 1)
    starts = {"pay": _dt.date(2024, 4, 1)}
    a = dq.simulate_history(20260930, ACTUAL, months, starts, HIST_CFG)
    b = dq.simulate_history(20260930, ACTUAL, months, starts, HIST_CFG)
    assert a == b
    pay = _series(a, "bronze.pay", "uniqueness")
    assert min(pay) == _dt.date(2024, 4, 1) and len(pay) == 30          # history starts with the data
    for key, rows in (("bronze.pay", pay), ("silver.ref", _series(a, "silver.ref", "validity"))):
        latest = rows[months[-1]]
        assert latest["basis"] == "actual" and latest["score"] == ACTUAL[(key.split(".")[0], key, latest["dimension"])]["score"]
    assert all(0.0 <= r["score"] <= 1.0 for r in a)
    assert all(r["score"] >= 0.90 for r in a if r["basis"] == "simulated")      # floor on simulated months


def test_history_incidents():
    months = dq.month_starts("2023-04", AS_OF)
    rows = dq.simulate_history(20260930, ACTUAL, months, {"pay": _dt.date(2024, 4, 1)}, HIST_CFG)
    pay = _series(rows, "bronze.pay", "uniqueness")
    before = [r["score"] for m, r in pay.items() if m < _dt.date(2026, 6, 1)]
    after = [r for m, r in pay.items() if _dt.date(2026, 6, 1) <= m < months[-1]]
    assert min(before) > 0.975 and all(r["incident_key"] == "replay" for r in after)   # step down from June
    assert all(abs(r["score"] - 0.978) < 0.003 for r in after)
    share = _series(rows, "silver.share", "timeliness")
    assert 0.835 <= share[_dt.date(2026, 8, 1)]["score"] <= 0.842 and share[_dt.date(2026, 8, 1)]["storyline"] is None
    assert share[_dt.date(2026, 7, 1)]["score"] > 0.97 and share[months[-1]]["score"] == 1.0
    kyc = _series(rows, "silver.kyc", "timeliness")
    dip = [kyc[_dt.date(2026, m, 1)]["score"] for m in (2, 3, 4, 5)]
    assert all(0.82 - 1e-9 <= s <= 0.86 + 1e-9 for s in dip) and kyc[_dt.date(2025, 6, 1)]["score"] > max(dip) + 0.05
    assert all(r["rows_failed"] <= r["rows_evaluated"] for r in rows)


def test_history_of_a_low_actual_has_no_false_drop():
    """A rule family whose actual sits below the floor (a 0.675 process SLA) keeps a history just under
    its actual instead of a flat floor that would read as a crash in the latest month."""
    months = dq.month_starts("2023-04", AS_OF)
    low = {("silver", "silver.sla", "timeliness"): {"score": 0.675, "rules": 1, "failed_rules": 1, "rows": 2_900,
                                                     "failed_rows": 942}}
    s = _series(dq.simulate_history(20260930, low, months, {}, HIST_CFG), "silver.sla", "timeliness")
    hist = [r["score"] for m, r in s.items() if m < months[-1]]
    assert max(hist) <= 0.675 and min(hist) >= 0.675 - 0.025 - 3 * 0.0015 - 1e-9
    assert abs(s[months[-1]]["score"] - s[months[-2]]["score"]) < 0.01


def test_catalogue_rows(mini):
    specs, by, rules = mini
    hand = dq.hand_rules({"rules": [{"id": "pay:x:tie", "table": "pay", "dimension": "consistency", "description": "d",
                                     "from": "{catalog}.silver.pay p", "pass": "p.amount_usd >= 0"}]})
    rows = dq.catalogue_rows(rules + hand, specs, {"catalog": "cat", "as_of": AS_OF})
    by_id = {r["rule_id"]: r for r in rows}
    assert by_id["pay:key_unique"]["table_name"] == "silver.pay"
    assert by_id["pay:key_unique"]["rule_expr"] == "count(*) = 1 per (payment_id)"
    assert by_id["pay:valid_values"]["rule_parts"][:2] == ["values parse into their silver types",
                                                          "direction in (Inbound, Outbound)"]
    assert by_id["pay:x:tie"]["table_name"] == "silver.pay" and by_id["pay:x:tie"]["rule_from"] == "cat.silver.pay p"
    assert by_id["pay:freshness"]["rule_grain"] == "table"


# ---- the repo's catalogue ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def catalogue():
    specs, cfg = silver.load_specs(), dq.load_rules_config()
    rules = dq.generate_rules(specs, AS_OF, cfg.get("thresholds")) + dq.hand_rules(cfg)
    dq.check_catalogue(rules)
    return specs, cfg, rules


def test_repo_catalogue_covers_five_dimensions(catalogue):
    specs, _, rules = catalogue
    counts = dq.dimension_counts(rules)
    assert set(counts) == set(dq.DIMENSIONS) and min(counts.values()) >= 40
    assert 400 <= len(rules) <= 650
    for name in specs.tables:                                  # every silver table: key present + unique
        assert {f"{name}:key_present", f"{name}:key_unique"} <= {r.rule_id for r in rules}
    golden = [r for r in rules if r.kind == "golden_key"]
    assert len(golden) == sum(1 for t in specs.tables.values() if t.client) and all(r.blocking for r in golden)


def test_repo_hand_rules_target_known_tables(catalogue):
    specs, _, rules = catalogue
    identity = {"ext_company_master", "kyc_customer", "credit_obligor", "core_customer", "crm_account", "trade_party",
                "tsy_counterparty"}
    for r in rules:
        if r.origin == "hand_written":
            name = r.table.split(".")[-1]
            assert name in specs.tables or name in identity or r.table.startswith("bronze."), r.rule_id
            assert r.from_sql and r.pass_expr and r.description
    by_story = {}
    for r in rules:
        by_story.setdefault(r.storyline, []).append(r.rule_id)
    assert "cf_actual_monthly:x:ties_to_payments" in by_story[12]
    assert "pay_payment_message:x:landing_ties_to_cash_flow" in by_story[12]
    assert "share_jp_parent_rating:x:refreshed_within_sla_30d" in by_story[13]
    assert "kyc_review:x:periodic_reviews_on_time" in by_story[7]
    shared = [t for t in specs.tables if t.startswith("share_")]
    assert all(f"{t}:x:refreshed_within_sla_30d" in by_story[13] for t in shared)
    assert sum(1 for r in rules if r.kind == "identity" and r.rule_id.endswith(":x:no_duplicate_identity")) == 7


def test_repo_history_incidents_match_storylines(catalogue):
    _, cfg, _ = catalogue
    inc = {i["key"]: i for i in cfg["history"]["incidents"]}
    assert inc["payments_replay_landing"]["from"] == "2026-06" and inc["payments_replay_landing"]["dimension"] == "uniqueness"
    assert inc["jp_share_outage"]["tables"] == ["share_jp_parent_rating"] and inc["jp_share_outage"]["from"] == "2026-08"
    assert inc["kyc_migration_backlog"]["from"] == "2026-02" and inc["kyc_migration_backlog"]["to"] == "2026-05"
    assert len(dq.month_starts(cfg["history"]["start"], AS_OF)) == 42
