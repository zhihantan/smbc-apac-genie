"""Metric-view framework tests (pure Python; smbc_genie_lib.metrics, metrics/*.yaml; PLAN §8; D14, D31-D33)."""
import copy
import re
from pathlib import Path

import pytest
import yaml

from smbc_genie_lib import metrics as mx

BLOCKS = mx.load_blocks()
PARAMS = mx.build_params()
ARRAYS = {"dim_client": {"aliases", "source_systems_present"}}
STANDARD_CLIENT_FIELDS = ["Client Group", "Client", "Segment", "Relationship Tier", "Is Japanese Corporate", "Industry",
                          "Subsector", "Coverage Office", "Primary RM", "Primary RM Office", "Rating Grade", "IFRS 9 Stage", "Watchlist",
                          "KYC Risk Rating", "EWS Band"]


def _m(name, expr, fmt=None, **kw):
    d = {"name": name, "expr": expr, "comment": f"{name} definition.", "display_name": name,
         "format": fmt or {"type": "number", "decimal_places": {"type": "max", "places": 2}}}
    d.update(kw)
    return d


def _view(**over):
    base = {
        "view": "mv_tb_deposits", "space": "tb_cash_payments_liquidity",
        "comment": "Deposit balances per account and month-end (point-in-time; never sum across months).",
        "grain": "one row per account per month-end",
        "source": "${catalog}.gold.fact_deposit_balance_monthly",
        "blocks": {"calendar": {"date": "source.balance_date", "grain": "month"}, "client": {}},
        "dimensions": [{"name": "Account Type", "expr": "source.account_type", "comment": "CASA or TD account type.",
                        "display_name": "Account Type", "synonyms": ["product", "account product"]}],
        "measures": [
            _m("Balance USD", "SUM(source.balance_usd)", {"type": "currency", "currency_code": "USD"},
               window=[{"order": "Month", "range": "current", "semiadditive": "last"}]),
            _m("CASA Balance USD", "SUM(source.casa_balance_usd)", {"type": "currency", "currency_code": "USD"},
               window=[{"order": "Month", "range": "current", "semiadditive": "last"}]),
            _m("CASA Ratio %", "MEASURE(`CASA Balance USD`) / NULLIF(MEASURE(`Balance USD`), 0)",
               {"type": "percentage"}, caveat="Ratio of month-end balances."),
            _m("Accounts", "COUNT(DISTINCT source.account_id)"),
        ],
        "validate": {"dims": ["Month", "Coverage Office"]},
        "reconcile": [
            {"name": "total_sep", "measure": "Balance USD", "where": "`Month` = DATE'2026-09-01'",
             "gold": "SELECT sum(balance_usd) FROM ${catalog}.gold.fact_deposit_balance_monthly WHERE month = DATE'2026-09-01'"},
            {"name": "casa_by_month", "measure": "CASA Balance USD", "by": ["Month"],
             "gold": "SELECT month, sum(casa_balance_usd) FROM ${catalog}.gold.fact_deposit_balance_monthly GROUP BY 1"},
        ],
    }
    base.update(over)
    return base


def _parse(raw):
    return mx.parse_view(raw, BLOCKS)


def _errors(raw):
    return mx.validate_view(_parse(raw), ARRAYS)


def _has(errs, pattern):
    return any(re.search(pattern, e) for e in errs)


# ---- blocks ----------------------------------------------------------------------------------------
def test_blocks_present():
    assert {"calendar", "client", "client_group"} <= set(BLOCKS)


@pytest.mark.parametrize("block", ["client", "client_group"])
def test_client_blocks_carry_the_standard_vocabulary(block):
    _, dims = mx.expand_block(BLOCKS[block], {}, "t")
    names = [d["name"] for d in dims]
    want = [n if not (block == "client_group" and n == "Client") else "Lead Client" for n in STANDARD_CLIENT_FIELDS]
    assert names == want
    for d in dims:   # every block field is fully described
        assert d["comment"] and d["display_name"] and 1 <= len(d["synonyms"]) <= mx.MAX_SYNONYMS


def test_client_block_joins_dim_client_with_group_nested():
    joins, dims = mx.expand_block(BLOCKS["client"], {"key": "source.lead_golden_client_sk"}, "t")
    assert len(joins) == 1 and joins[0]["name"] == "dc" and joins[0]["source"] == "${catalog}.gold.dim_client"
    assert joins[0]["on"] == "source.lead_golden_client_sk = dc.golden_client_sk"
    assert joins[0]["joins"][0]["name"] == "dg" and joins[0]["joins"][0]["source"].endswith("gold.dim_client_group")
    assert next(d for d in dims if d["name"] == "Client Group")["expr"] == "dc.dg.group_name"
    assert not any(re.search(r"\b(aliases|source_systems_present)\b", d["expr"]) for d in dims)   # D31


def test_block_join_aliases_never_equal_field_names():
    for b in BLOCKS.values():
        labels = {d["name"].lower() for d in b.dimensions}
        stack = list(b.joins)
        while stack:
            j = stack.pop()
            assert j["name"].lower() not in labels
            stack += j.get("joins") or []


@pytest.mark.parametrize("grain,fields", [
    ("day", ["Date", "Month", "Fiscal Year", "Fiscal Quarter", "Fiscal Half", "Is Month End", "Is Latest Month"]),
    ("month", ["Month", "Fiscal Year", "Fiscal Quarter", "Fiscal Half", "Is Month End", "Is Latest Month"]),
    ("fiscal_year", ["Fiscal Year Number", "Fiscal Year", "Is Latest Fiscal Year"]),
])
def test_calendar_fields_per_grain(grain, fields):
    _, dims = mx.expand_block(BLOCKS["calendar"], {"date": "source.d", "grain": grain}, "t")
    assert [d["name"] for d in dims] == fields


def test_calendar_hierarchy_derives_from_the_order_field_by_name():
    """D33 + docs: a hierarchy defined on the source column breaks window grouping."""
    for grain, order in (("day", "Date"), ("month", "Month")):
        _, dims = mx.expand_block(BLOCKS["calendar"], {"date": "source.balance_date", "grain": grain}, "t")
        by = {d["name"]: d["expr"] for d in dims}
        for f in ("Fiscal Year", "Fiscal Quarter", "Fiscal Half", "Is Latest Month"):
            assert "`Month`" in by[f] and "source." not in by[f], f
        assert by[order] == "source.balance_date" if grain == "day" else "source.balance_date" in by["Month"]
    _, dims = mx.expand_block(BLOCKS["calendar"], {"date": "source.d", "grain": "day"}, "t")
    assert {d["name"]: d["expr"] for d in dims}["Month"] == "TRUNC(`Date`, 'MM')"


def test_calendar_params_are_checked():
    with pytest.raises(mx.MetricSpecError, match="needs `date`"):
        mx.expand_block(BLOCKS["calendar"], {"grain": "month"}, "t")
    with pytest.raises(mx.MetricSpecError, match="not in"):
        mx.expand_block(BLOCKS["calendar"], {"date": "source.d", "grain": "week"}, "t")
    with pytest.raises(mx.MetricSpecError, match="no params"):
        mx.expand_block(BLOCKS["calendar"], {"date": "source.d", "grain": "day", "bogus": 1}, "t")
    _, dims = mx.expand_block(BLOCKS["calendar"], {"date": "source.fy_start", "grain": "fiscal_year"}, "t")
    assert "MONTH(source.fy_start) >= ${fy_start_month}" in dims[0]["expr"]
    _, dims = mx.expand_block(BLOCKS["calendar"], {"fy": "source.fiscal_year", "grain": "fiscal_year"}, "t")
    assert dims[0]["expr"] == "source.fiscal_year"


def _fiscal_eval(expr: str, month_start: str) -> str:
    """Evaluate the block's label expressions for one month (tiny SQL subset: CONCAT, CASE, MONTH, YEAR, PMOD, FLOOR)."""
    y, m = int(month_start[:4]), int(month_start[5:7])
    s = expr.replace("${fy_start_month}", "4").replace("`Month`", "M")
    s = s.replace("MONTH(M)", str(m)).replace("YEAR(M)", str(y))
    s = re.sub(r"CASE WHEN (.+?) THEN (.+?) ELSE (.+?) END", r"((\2) if (\1) else (\3))", s)
    s = s.replace("PMOD(", "_pmod(").replace("FLOOR(", "_floor(").replace("CAST(", "(").replace(" AS INT)", ")")
    s = s.replace("CONCAT(", "_concat(")
    return str(eval(s, {"_pmod": lambda a, b: a % b, "_floor": lambda a: int(a // 1),   # noqa: S307 - test-only
                        "_concat": lambda *a: "".join(str(x) for x in a)}))


@pytest.mark.parametrize("month,fy,fq,fh", [("2026-09-01", "FY2026", "FY2026-Q2", "FY2026-H1"),
                                            ("2026-04-01", "FY2026", "FY2026-Q1", "FY2026-H1"),
                                            ("2026-03-01", "FY2025", "FY2025-Q4", "FY2025-H2"),
                                            ("2025-12-01", "FY2025", "FY2025-Q3", "FY2025-H2"),
                                            ("2027-01-01", "FY2026", "FY2026-Q4", "FY2026-H2")])
def test_calendar_labels_match_dim_date_and_fiscal_lib(month, fy, fq, fh):
    from smbc_genie_lib import fiscal
    _, dims = mx.expand_block(BLOCKS["calendar"], {"date": "source.d", "grain": "month"}, "t")
    by = {d["name"]: d["expr"] for d in dims}
    assert (fiscal.fiscal_year_label(month), fiscal.fiscal_quarter_label(month), fiscal.fiscal_half_label(month)) == (fy, fq, fh)
    assert _fiscal_eval(by["Fiscal Year"], month) == fy
    assert _fiscal_eval(by["Fiscal Quarter"], month) == fq
    assert _fiscal_eval(by["Fiscal Half"], month) == fh


# ---- parse / validate ------------------------------------------------------------------------------
def test_valid_view_has_no_errors():
    spec = _parse(_view())
    assert mx.validate_view(spec, ARRAYS) == []
    assert spec.field_names[:6] == ["Month", "Fiscal Year", "Fiscal Quarter", "Fiscal Half", "Is Month End", "Is Latest Month"]
    assert spec.field_names[6:21] == STANDARD_CLIENT_FIELDS and spec.field_names[-1] == "Account Type"
    assert spec.block_fields["Month"] == "calendar" and spec.block_fields["EWS Band"] == "client"
    assert [j["name"] for j in spec.joins] == ["dc"]


def test_unquoted_on_key_is_repaired():
    raw = yaml.safe_load("joins:\n  - name: x\n    on: a = b\n")
    assert True in raw["joins"][0]
    assert mx._fix_on_key(raw)["joins"][0]["on"] == "a = b"


@pytest.mark.parametrize("mutate,pattern", [
    (lambda v: v["measures"][0].pop("comment"), r"`comment` is required"),
    (lambda v: v["dimensions"][0].pop("display_name"), r"`display_name` is required"),
    (lambda v: v["dimensions"][0].update(synonyms=[f"s{i}" for i in range(11)]), r"11 synonyms"),
    (lambda v: v["measures"][3].pop("format"), r"needs a `format`"),
    (lambda v: v["measures"][0].update(format={"type": "number"}), r"USD' measure needs format"),
    (lambda v: v["measures"][2].update(format={"type": "number"}), r"% ' measure|'... %' measure"),
    (lambda v: v["measures"][2].update(format={"type": "percent"}), r"not in .*percentage"),
    (lambda v: v["measures"][0].update(format={"type": "currency"}), r"currency_code"),
    (lambda v: v["measures"][3].update(format={"type": "number", "decimal_places": {"type": "exact"}}), r"places must be"),
    (lambda v: v["measures"].append(_m("account type", "COUNT(1)")), r"duplicate name"),
    (lambda v: v["dimensions"].append({"name": "Aliases", "expr": "dc.aliases", "comment": "x", "display_name": "x"}),
     r"ARRAY column dc\.aliases"),
    (lambda v: v["measures"][0].update(window=[{"order": "Day", "range": "current", "semiadditive": "last"}]),
     r"window order 'Day'"),
    (lambda v: v["measures"][0].update(window=[{"order": "Month", "range": "trailing month", "semiadditive": "last"}]),
     r"window range"),
    (lambda v: v["measures"][0].update(window=[{"order": "Month", "range": "current", "semiadditive": "max"}]),
     r"semiadditive must be"),
    (lambda v: v["measures"].append(_m("Bad Window", "MEASURE(`Balance USD`) * 2",
                                       window=[{"order": "Month", "range": "current", "semiadditive": "last"}])),
     r"cannot reference window measures"),
    (lambda v: v["measures"].insert(0, _m("Early", "MEASURE(`Accounts`) + 1")), r"must be defined before"),
    (lambda v: v["measures"].append(_m("Ghost", "MEASURE(`Nope`)")), r"is not a measure of this view"),
    (lambda v: v["measures"][3].update(expr="source.account_id"), r"expr must aggregate"),
    (lambda v: v["dimensions"].append({"name": "Today", "expr": "CURRENT_DATE()", "comment": "x", "display_name": "x"}),
     r"CURRENT_DATE"),
    (lambda v: v.update(source="${catalog}.silver.core_deposit_balance_monthly"), r"read gold only"),
    (lambda v: v.update(source="smbc_genie.gold.fact_deposit_balance_monthly"), r"not a literal catalog"),
    (lambda v: v["reconcile"].pop(), r"at least 2 `reconcile`"),
    (lambda v: v["reconcile"][0].update(measure="Nope"), r"is not a measure"),
    (lambda v: v["reconcile"][1].update(by=["Nope"]), r"by 'Nope' is not a dimension"),
    (lambda v: v["blocks"].pop("client"), r"standard client block"),
    (lambda v: v["blocks"].pop("calendar"), r"calendar block"),
    (lambda v: v.update(space="tb_trade_scf"), r"PLAN §8 puts this view"),
    (lambda v: v.update(space="nope"), r"space slug"),
    (lambda v: v.update(joins=[{"name": "account type", "source": "${catalog}.gold.dim_account",
                                "on": "source.account_id = x.account_id"}]), r"join name must be"),
    (lambda v: v.update(joins=[{"name": "segment", "source": "${catalog}.gold.dim_account",
                                "on": "source.account_id = segment.account_id"}]), r"equals a field"),
    (lambda v: v.update(joins=[{"name": "acct", "source": "${catalog}.gold.dim_account"}]), r"exactly one of `on`"),
    (lambda v: v["dimensions"].insert(0, {"name": "Prior", "expr": "`Account Type`", "comment": "x", "display_name": "x"}),
     r"before it is defined"),
    (lambda v: v["dimensions"][0].update(name="Bad`Name"), r"must be ASCII"),
    (lambda v: v["dimensions"][0].update(unexpected="x"), r"unexpected keys"),
    (lambda v: v["validate"].update(dims=["Month"]), r"2-3 dimensions"),
])
def test_validation_catches(mutate, pattern):
    raw = copy.deepcopy(_view())
    mutate(raw)
    errs = _errors(raw)
    assert _has(errs, pattern), errs


def test_unknown_top_level_key_and_version_rejected():
    with pytest.raises(mx.MetricSpecError, match="unknown keys"):
        _parse(_view(materialisation={}))
    with pytest.raises(mx.MetricSpecError, match="version"):
        _parse(_view(version=0.1))
    with pytest.raises(mx.MetricSpecError, match="mv_"):
        _parse(_view(view="tb_deposits"))


def test_exemptions_need_a_reason():
    raw = _view(client_block_exempt="Table-grain DQ scores have no client.")
    raw["blocks"].pop("client")
    assert not _has(_errors(raw), "client block")


def test_overrides_patch_block_fields_only():
    raw = _view(overrides={"Month": {"synonyms": ["balance month"]}})
    assert next(d for d in _parse(raw).dimensions if d["name"] == "Month")["synonyms"] == ["balance month"]
    with pytest.raises(mx.MetricSpecError, match="not a block field"):
        _parse(_view(overrides={"Account Type": {"comment": "x"}}))
    with pytest.raises(mx.MetricSpecError, match="may only patch"):
        _parse(_view(overrides={"Month": {"expr": "x"}}))


# ---- rendering -------------------------------------------------------------------------------------
def test_render_ddl_shape_and_round_trip():
    spec = _parse(_view())
    ddl = mx.render_ddl(spec, PARAMS)
    assert ddl.startswith("CREATE OR REPLACE VIEW smbc_genie.metrics.mv_tb_deposits\nWITH METRICS\nLANGUAGE YAML\nCOMMENT '")
    body = ddl.split("AS $$\n", 1)[1].rsplit("$$", 1)[0]
    doc = yaml.safe_load(body)
    assert doc["version"] == 1.1 and doc["source"] == "smbc_genie.gold.fact_deposit_balance_monthly"
    assert list(doc) == ["version", "comment", "source", "joins", "dimensions", "measures"]
    assert "'on':" in body and doc["joins"][0]["on"] == "source.golden_client_sk = dc.golden_client_sk"
    for f in doc["dimensions"] + doc["measures"]:
        assert set(f) <= mx.MEASURE_KEYS, f            # runner-only keys (unit, caveat, grains, requires) stripped
    assert "${" not in body
    exprs = {f["name"]: f["expr"] for f in doc["dimensions"]}
    assert exprs["Is Latest Month"] == "`Month` = DATE'2026-09-01'" and "MONTH(`Month`) >= 4" in exprs["Fiscal Year"]
    assert doc["comment"].endswith("Grain: one row per account per month-end.")


def test_comment_is_escaped_and_dollar_quotes_rejected():
    spec = _parse(_view(comment="Client's deposits."))
    assert "COMMENT 'Client\\'s deposits. Grain:" in mx.render_ddl(spec, PARAMS)
    raw = _view()
    raw["measures"][3]["expr"] = "COUNT(DISTINCT '$$')"
    with pytest.raises(mx.MetricSpecError, match=r"\$\$"):
        mx.render_ddl(_parse(raw), PARAMS)
    raw = _view()
    raw["measures"][3]["expr"] = "COUNT(${nope})"
    with pytest.raises(mx.MetricSpecError, match="unknown placeholder"):
        mx.render_ddl(_parse(raw), PARAMS)


def test_params():
    assert PARAMS["as_of_date"] == "2026-09-30" and PARAMS["as_of_month"] == "2026-09-01"
    assert PARAMS["as_of_fiscal_year"] == "FY2026" and PARAMS["as_of_fiscal_year_no"] == 2026
    assert PARAMS["fy_start_month"] == 4 and PARAMS["rorwa_hurdle"] == 0.012


def test_sql_refs_and_tags():
    spec = _parse(_view())
    assert mx.sql_refs(spec) == {("gold", "fact_deposit_balance_monthly"), ("gold", "dim_client"), ("gold", "dim_client_group")}
    assert mx.render_tags(spec, "smbc_genie") == ("ALTER VIEW smbc_genie.metrics.mv_tb_deposits SET TAGS ('smbc_layer' = "
                                                 "'metrics', 'smbc_domain' = 'tb_cash_payments_liquidity', 'smbc_synthetic' = 'true')")


def test_validation_and_reconcile_sql():
    spec = _parse(_view())
    v = mx.validation_sql(spec, "smbc_genie")
    assert v.startswith("SELECT `Month`, `Coverage Office`, MEASURE(`Balance USD`) AS `m_0`")
    assert v.endswith("GROUP BY ALL") and v.count("MEASURE(") == 4
    assert mx.measure_sql(spec, "c", "CASA Ratio %", "`Month` = DATE'2026-09-01'", ["Coverage Office"]) == (
        "SELECT `Coverage Office`, MEASURE(`CASA Ratio %`) AS v FROM c.metrics.mv_tb_deposits "
        "WHERE `Month` = DATE'2026-09-01' GROUP BY ALL")
    assert "LIMIT 1" in mx.dims_probe_sql(spec, "c")


def test_compare_rows_and_tolerance():
    assert mx.within(100.0, 100.009) and not mx.within(100.0, 100.02)          # 0.01% relative
    assert mx.within(None, 0.0) and mx.within(None, None) and not mx.within(None, 5.0)
    ok, msg = mx.compare_rows([["1000.0"]], [["1000.05"]], 0)
    assert ok, msg
    ok, msg = mx.compare_rows([["2026-09-01", "5"], ["2026-08-01", "4"]], [["2026-09-01", "5"], ["2026-08-01", "3"]], 1)
    assert not ok and "2026-08-01" in msg
    ok, _ = mx.compare_rows([["true", "SG", "1"]], [["TRUE", "SG", "1.0"]], 2)
    assert ok


def test_measure_kind_and_units():
    spec = _parse(_view())
    assert [mx.measure_kind(m) for m in spec.measures] == ["point_in_time", "point_in_time", "composed", "aggregate"]
    assert mx.unit_of(spec.measures[0]) == "USD" and mx.unit_of(spec.measures[3]) == "count"
    assert mx.unit_of(spec.measures[2]).startswith("%")
    prior = _m("Prior", "SUM(x)", window=[{"order": "Month", "range": "current", "semiadditive": "last", "offset": "-1 month"}])
    assert mx.measure_kind(prior) == "prior_period"


def test_coverage_gaps():
    desc = {"comment": "v", "columns": [
        {"name": "Month", "comment": "m", "metadata": {"display_name": "Month"}},
        {"name": "Balance USD", "comment": "b", "is_measure": True, "metadata": {"display_name": "B"}},
        {"name": "X", "comment": "", "metadata": {}}]}
    assert mx.coverage_gaps(desc) == ["Balance USD: format", "X: comment", "X: display_name"]


def test_glossary_renders():
    spec = _parse(_view())
    md = mx.render_glossary({spec.name: spec}, BLOCKS, PARAMS)
    assert "#### `mv_tb_deposits`" in md and "| CASA Ratio % |" in md and "Point-in-time" in md
    assert "Ratio of month-end balances." in md and "### Block `client`" in md and "Planned views not built yet" in md


# ---- answers files ---------------------------------------------------------------------------------
ANSWERS = """-- Space: tb_cash_payments_liquidity
-- Q1: Total CASA balance by booking country at end September 2026?
-- views: mv_tb_deposits
SELECT `Coverage Office`, MEASURE(`CASA Balance USD`) AS casa
FROM smbc_genie.metrics.mv_tb_deposits WHERE `Month` = DATE'2026-09-01' GROUP BY ALL;

-- Q3: CASA ratio trend
-- rows: 7
SELECT `Month`, MEASURE(`CASA Ratio %`) FROM ${catalog}.metrics.mv_tb_deposits GROUP BY ALL;
"""


def test_parse_answers_and_write_rows():
    ans = mx.parse_answers(ANSWERS)
    assert [a.qid for a in ans] == ["Q1", "Q3"] and ans[0].views == ["mv_tb_deposits"] and ans[1].rows == 7
    assert ans[0].sql.startswith("SELECT `Coverage Office`") and "--" not in ans[0].sql
    out = mx.write_answer_rows(ANSWERS, {"Q1": 13, "Q3": 18})
    assert "-- views: mv_tb_deposits\n-- rows: 13\nSELECT" in out and "-- rows: 18" in out and "-- rows: 7" not in out
    assert mx.write_answer_rows(out, {"Q1": 13, "Q3": 18}) == out             # idempotent
    with pytest.raises(mx.MetricSpecError, match="header"):
        mx.parse_answers("SELECT 1;")
    with pytest.raises(mx.MetricSpecError, match="CURRENT_DATE"):
        mx.parse_answers("-- Q1: x\nSELECT current_date();")
    with pytest.raises(mx.MetricSpecError, match="duplicate"):
        mx.parse_answers("-- Q1: x\nSELECT 1;\n-- Q1: y\nSELECT 2;")


# ---- the real catalogue ----------------------------------------------------------------------------
def test_plan_has_43_views_over_11_spaces():
    assert len(mx.PLAN_VIEWS) == 43 and set(mx.PLAN_VIEWS.values()) == set(mx.SPACES) and len(mx.SPACES) == 11


REAL = sorted(p for p in mx.METRICS_DIR.glob("*.yaml") if not p.name.startswith("_"))


@pytest.mark.parametrize("path", REAL, ids=[p.stem for p in REAL])
def test_every_view_file_is_valid(path):
    spec = mx.parse_view(mx.load_yaml(path), BLOCKS, path)
    assert mx.validate_view(spec) == []
    assert spec.name in mx.PLAN_VIEWS
    mx.render_ddl(spec, PARAMS)          # renders without unknown placeholders or $$


ANSWER_FILES = sorted(mx.ANSWERS_DIR.glob("*.sql")) if mx.ANSWERS_DIR.exists() else []


@pytest.mark.parametrize("path", ANSWER_FILES, ids=[p.stem for p in ANSWER_FILES])
def test_answer_files_parse_and_reference_known_views(path):
    assert path.stem in mx.SPACES
    for a in mx.parse_answers(path.read_text()):
        assert a.views, f"{a.qid} references no metric view"
        assert set(a.views) <= set(mx.PLAN_VIEWS), a.views
        assert "MEASURE(" in a.sql.upper()
