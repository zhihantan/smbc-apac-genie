"""Unit tests for the Genie question bank (src/50_genie/build_question_bank.py): formatting, grading, curation
checks and the offline renderer, on a one-agent fixture (no workspace)."""
import csv
import io
import json
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "50_genie"))
import build_question_bank as qb  # noqa: E402

SLUG = "early_warning_monitoring"
SID = "0123456789abcdef0123456789abcdef"
MV = "smbc_genie.metrics.mv_ews_scores"
Q1_SQL = ("SELECT `Client`, MEASURE(`Exposure in Red USD`) AS exposure_usd, MEASURE(`Latest Score`) AS ews_score\n"
          "FROM smbc_genie.metrics.mv_ews_scores WHERE `Date` = DATE'2026-09-30' GROUP BY ALL")
X1_SQL = "SELECT MEASURE(`Clients Red`) AS clients_red FROM smbc_genie.metrics.mv_ews_scores WHERE `Date` = DATE'2026-09-30'"

SPACE_YAML = textwrap.dedent("""\
    slug: early_warning_monitoring
    title: APAC Genie - Early Warning Monitoring
    description: Early-warning scores. Data is synthetic and as of 30 Sep 2026.
    data_sources:
      - identifier: smbc_genie.metrics.mv_ews_scores
      - identifier: smbc_genie.gold.dim_ews_trigger
    sample_questions:
      - Which clients are Red today?
      - How many clients are Red?
""")

BENCHMARKS = [
    {"id": "a" * 32, "key": "Q1", "question": "Which clients are Red today, with exposure and score?",
     "expected_sql": Q1_SQL, "expected_checks": {"rows": 2}, "answer_ref": "Q1", "variant_of": None,
     "allow_extra_columns": True, "variant_of_id": None},
    {"id": "b" * 32, "key": "Q1-casual", "question": "who's in the red rn", "expected_sql": Q1_SQL,
     "expected_checks": {"rows": 2}, "answer_ref": None, "variant_of": "Q1", "allow_extra_columns": True,
     "variant_of_id": "a" * 32},
    {"id": "c" * 32, "key": "X1", "question": "How many clients are Red today?", "expected_sql": X1_SQL,
     "expected_checks": {"rows": 1}, "answer_ref": None, "variant_of": None, "allow_extra_columns": False,
     "variant_of_id": None},
]
EVAL = {"summary": {"passed": 2, "n": 3, "at": "2026-10-04T06:01:27+00:00"},
        "benchmarks": [{"key": "Q1", "pass": True}, {"key": "Q1-casual", "pass": True}, {"key": "X1", "pass": False}]}
ANSWERS_SQL = "-- Q1: Which clients are Red today?\n-- views: mv_ews_scores\n-- rows: 2\n" + Q1_SQL + ";\n"
METRIC_YAML = textwrap.dedent("""\
    view: mv_ews_scores
    space: early_warning_monitoring
    grain: one row per client per day (gold.fact_ews_score_daily)
    measures:
      - {name: Exposure in Red USD, format: {type: currency, currency_code: USD}}
      - {name: Latest Score, format: {type: number, decimal_places: {type: max, places: 1}}}
      - {name: Clients Red, format: {type: number, decimal_places: {type: exact, places: 0}}}
""")
CURATION = {
    "workspace_url": "https://ws.example.com",
    "guide": {"phrasing": ["Use full names."], "genie_one": ["Genie One routes to the agents."]},
    "tables": {"gold.dim_ews_trigger": "reference list"},
    "agents": {SLUG: {
        "tier": "Credit Workbench", "audience": "Credit officers.", "job": "See deterioration early.",
        "knows": "Daily EWS scores.",
        "starters": [{"benchmark": "Q1"}, {"benchmark": "X1"}],
        "themes": [{"name": "Who is at risk today", "keys": ["Q1"]}],
        "more": ["X1"],
        "flaky": {"X1": {"why": "Genie can count all dates.", "safer": "How many clients are Red as at 30 Sep 2026?"}},
        "follow_ups": [{"after": "S1", "ask": "Show the same for Amber clients.", "expect_in_sql": ["Amber"]}],
        "routing": [{"ask": "News behind a trigger", "agent": "signals_sentiment"}],
    }},
}
CACHE = {
    "answers_captured_at": "2026-10-04T14:30:00+00:00", "questions_captured_at": "2026-10-04T15:00:00+00:00",
    "coverage": [{"asset": MV, "type": "METRIC_VIEW", "field": "Date", "first": "2025-04-01", "last": "2026-09-30",
                  "periods": 548}],
    "answers": {
        "Q1": {"rows": 2, "columns": ["Client", "exposure_usd", "ews_score"], "types": ["STRING", "DOUBLE", "DOUBLE"],
               "head": [["Sunda Energi Nusantara (Jakarta) PT Tbk", "104400000", "77.7"], ["Sabah Energy", "12700000", "71.2"]],
               "error": None, "sql_sha": qb.sha(Q1_SQL)},
        "Q1-casual": {"rows": 2, "columns": ["Client", "exposure_usd", "ews_score"], "types": ["STRING", "DOUBLE", "DOUBLE"],
                      "head": [], "error": None, "sql_sha": qb.sha(Q1_SQL)},
        "X1": {"rows": 1, "columns": ["clients_red"], "types": ["LONG"], "head": [["8"]], "error": None,
               "sql_sha": qb.sha(X1_SQL)},
    },
}
RAW = {
    "expected": {"Q1": {"cols": ["Client", "exposure_usd", "ews_score"],
                        "rows": [["Sunda Energi Nusantara (Jakarta) PT Tbk", "104400000", "77.7"],
                                 ["Sabah Energy", "12700000", "71.2"]]},
                 "X1": {"cols": ["clients_red"], "rows": [["8"]]}},
    "questions": {
        "S1": {"question": "Which clients are Red today?", "status": "COMPLETED", "sql": "SELECT ...",
               "cols": ["Client", "Exposure"], "row_count": 2,
               "rows": [["Sabah Energy", "12700000.0"], ["Sunda Energi Nusantara (Jakarta) PT Tbk", "104400000"]],
               "asked_at": "2026-10-04T15:00:00+00:00", "seconds": 20.0, "deleted": True},
        "F1": {"question": "Show the same for Amber clients.", "status": "COMPLETED",
               "sql": "SELECT ... WHERE band = 'Amber'", "cols": ["Client"], "rows": [["X"]], "row_count": 1},
        "S2": {"question": "How many clients are Red?", "status": "COMPLETED", "sql": "SELECT count(*)",
               "cols": ["n"], "rows": [["802"]], "row_count": 1},
        "X1": {"question": "How many clients are Red as at 30 Sep 2026?", "status": "FAILED", "sql": None,
               "error": "timeout"},
    },
}


@pytest.fixture()
def tree(tmp_path):
    gd = tmp_path / "genie"
    (gd / SLUG).mkdir(parents=True)
    (gd / SLUG / "space.yaml").write_text(SPACE_YAML)
    (gd / SLUG / "benchmarks.json").write_text(json.dumps(BENCHMARKS))
    (gd / SLUG / "eval_report.json").write_text(json.dumps(EVAL))
    (gd / SLUG / "space_id").write_text(SID + "\n")
    (gd / SLUG / qb.CACHE_NAME).write_text(json.dumps(CACHE))
    ad = tmp_path / "answers"
    ad.mkdir()
    (ad / f"{SLUG}.sql").write_text(ANSWERS_SQL)
    md = tmp_path / "metrics"
    md.mkdir()
    (md / "mv_ews_scores.yaml").write_text(METRIC_YAML)
    return gd, ad, md


def _agents(tree, cur=CURATION):
    gd, ad, _ = tree
    return qb.load_agents(cur, [SLUG], gd, ad)


# ---- formatting ---------------------------------------------------------------------------------------
def test_fmt_value():
    assert qb.fmt_value(27.77e9, "usd") == "USD 27.77bn"
    assert qb.fmt_value("91400000", "usd") == "USD 91.4m"
    assert qb.fmt_value(4.25e6, "usd") == "USD 4.25m"
    assert qb.fmt_value(-950e3, "usd") == "USD -950k"
    assert qb.fmt_value("0.565", "pct") == "56.5%"
    assert qb.fmt_value(12, "days") == "12 days" and qb.fmt_value(12.34, "days") == "12.3 days"
    assert qb.fmt_value("3966", "count") == "3,966"
    assert qb.fmt_value("125.4", "bps") == "125 bps"
    assert qb.fmt_value("2026-09-01", "month") == "Sep 2026"
    assert qb.fmt_value("2026-09-30T00:00:00.000Z", "date") == "30 Sep 2026"
    assert qb.fmt_value(None, "usd") == "–" and qb.fmt_value("true", "text") == "yes"
    assert qb.fmt_value("77.7", "num") == "77.7" and qb.fmt_value("1234.5", "num") == "1,234"


def test_infer_units_from_measure_formats_then_names(tree):
    _, _, md = tree
    mu = qb.load_measure_units(md)
    assert mu["mv_ews_scores"] == {"Exposure in Red USD": "usd", "Latest Score": "num", "Clients Red": "count"}
    cols = ["Client", "exposure_usd", "ews_score", "attainment", "days_open", "Month"]
    types = ["STRING", "DOUBLE", "DOUBLE", "DOUBLE", "DOUBLE", "DATE"]
    head = [["A", "1", "2", "0.45", "12", "2026-09-01"]]
    assert qb.infer_units(Q1_SQL, cols, types, head, mu) == ["text", "usd", "num", "pct", "days", "month"]
    assert qb.infer_units(Q1_SQL, cols, types, head, mu, {"ews_score": "count"})[2] == "count"


def test_summarize_answer_and_labels():
    ans = CACHE["answers"]["Q1"]
    text = qb.summarize_answer(ans, ["text", "usd", "num"])
    assert text == ("2 rows: Sunda Energi Nusantara (Jakarta) PT Tbk: exposure USD 104.4m, EWS score 77.7; "
                    "Sabah Energy: exposure USD 12.7m, EWS score 71.2")
    assert qb.summarize_answer({"rows": 0}, []) == "no rows"
    assert qb.col_label("h1_actual_usd") == "H1 actual" and qb.col_label("Product Family") == "Product Family"


# ---- grading ------------------------------------------------------------------------------------------
EXP_COLS, EXP = ["client", "exposure_usd"], [["A", "100.0"], ["B", "250.123"]]


def test_grade_match_with_extra_and_fewer_columns():
    v, note = qb.grade_result(EXP_COLS, EXP, ["Exposure", "Client", "Band"], [["250.1234", "B", "Red"], ["100", "A", "Red"]])
    assert v == "match" and "adds 1 column" in note
    v, note = qb.grade_result(EXP_COLS, EXP, ["Client"], [["A"], ["B"]])
    assert v == "differs" and "none of the benchmark's figures" in note        # names only, figures asked for
    v, note = qb.grade_result(EXP_COLS, EXP, ["Client"], [["A"], ["B"]], need=["client"])
    assert v == "match" and "not shown: exposure USD" in note
    v, note = qb.grade_result(["a", "b", "x"], [["1", "2", "q"]], ["a", "y"], [["1", "z"]])
    assert v == "differs"                                                        # shares a column, but y is no b


def test_grade_row_differences():
    v, note = qb.grade_result(EXP_COLS, EXP, EXP_COLS, EXP + [["C", "1"]])
    assert v == "differs" and "its first 2 are exactly the benchmark's answer" in note    # a longer ranking
    v, note = qb.grade_result(EXP_COLS, EXP, EXP_COLS, [["C", "1"]] + EXP)
    assert v == "differs" and "all benchmark rows are included" in note
    v, note = qb.grade_result(["client", "x"], [["A", "1"]], ["client", "x", "rm"], [["B", "1", "R"]])
    assert v == "differs" and "client differ" in note                                      # same figure, other client
    v, note = qb.grade_result(EXP_COLS, EXP, EXP_COLS, EXP[:1])
    assert v == "differs" and "1 of the benchmark's 2 rows" in note
    v, note = qb.grade_result(EXP_COLS, EXP, EXP_COLS, [["A", "100"], ["B", "251"]])
    assert v == "differs" and "same 2 rows; values differ for exposure USD" in note
    v, note = qb.grade_result(EXP_COLS, EXP, EXP_COLS, [])
    assert v == "differs" and "no rows" in note


def test_grade_item_alternatives_and_errors():
    expected = {"Q1": {"cols": EXP_COLS, "rows": EXP}, "Q1b": {"cols": ["n"], "rows": [["2"]]}}
    ok = {"status": "COMPLETED", "sql": "SELECT 2", "cols": ["clients"], "rows": [["2"]]}
    assert qb.grade_item(ok, expected, ["Q1", "Q1b"])[::2] == ("match", "Q1b")
    assert qb.grade_item({"status": "COMPLETED", "sql": None, "text": "Which year?"}, expected, ["Q1"])[0] == "error"
    assert qb.grade_follow_up({"status": "COMPLETED", "sql": "where band = 'Amber'", "row_count": 3}, ["amber"])[0] == "match"
    assert qb.grade_follow_up({"status": "COMPLETED", "sql": "select 1", "row_count": 1}, ["Amber"])[0] == "differs"


def test_bind_parameters_and_ledger(tmp_path):
    sql = "SELECT x::date, ':band' FROM t -- :band\nWHERE b = :band AND c = :n AND d = :d"
    params = [{"keyword": "band", "value": "Red", "sql_type": "STRING"}, {"keyword": "n", "value": "3", "sql_type": "INT"},
              {"keyword": "d", "value": "2026-09-30", "sql_type": "DATE"}]
    assert qb.bind_parameters(sql, params) == \
        "SELECT x::date, ':band' FROM t -- :band\nWHERE b = 'Red' AND c = 3 AND d = DATE'2026-09-30'"
    led = qb.Ledger(tmp_path / "ledger.jsonl")
    led.log("created", "s", "c1", question="q1")
    led.log("created", "s", "c2", question="q2")
    led.log("deleted", "s", "c1")
    assert led.pending() == [("s", "c2")]


# ---- curation and rendering ---------------------------------------------------------------------------
def test_regrade_and_render(tree):
    gd, _, md = tree
    (a,) = _agents(tree)
    assert qb.check_curation(CURATION, [a]) == []
    qb.regrade(a, RAW)
    got = {q["id"]: (q["verdict"], q.get("matched")) for q in a.cache["questions"]}
    assert got == {"S1": ("match", "Q1"), "F1": ("match", None), "S2": ("differs", None), "X1": ("error", None)}
    mu, specs = qb.load_measure_units(md), qb.load_metric_specs(md)
    doc = qb.render_markdown([a], CURATION, mu, specs)
    assert "## 4. Early Warning Monitoring" in doc
    assert f"[Open the agent](https://ws.example.com/genie/rooms/{SID})" in doc
    assert "| `metrics.mv_ews_scores` (metric view) | client per day | 1 Apr 2025 – 30 Sep 2026; 548 distinct dates |" in doc
    assert "| `gold.dim_ews_trigger` (table) |" in doc and "| reference list |" in doc
    assert "| 1 | Which clients are Red today? | ✅ | Q1 | Genie shows 2 of the benchmark's 3 columns (not shown: EWS score). |" in doc
    assert "| 2 | How many clients are Red? | ⚠️ | X1 | Same row count (1); no Genie column matches clients red. |" in doc
    assert "Answer (`mv_ews_scores`): 2 rows: Sunda Energi Nusantara (Jakarta) PT Tbk: exposure USD 104.4m" in doc
    assert "Also works when asked as: \"who's in the red rn\" ✅" in doc
    assert "#### More tested questions (storyline checks)" in doc and "- ⚠️ **X1**" in doc
    assert "Safer wording: \"How many clients are Red as at 30 Sep 2026?\" — ❌" in doc
    assert "After starter 1: \"Show the same for Amber clients.\" — ✅" in doc
    assert "[Signals & Sentiment](#10-signals--sentiment)" in doc
    assert "| 4 | [Early Warning Monitoring](#4-early-warning-monitoring) | Credit Workbench |" in doc
    rows = list(csv.DictReader(io.StringIO(qb.render_csv([a], CURATION, mu))))
    assert [r["kind"] for r in rows] == ["starter", "starter", "must-answer", "variant", "storyline check", "safer wording", "follow-up"]
    assert rows[0]["result"] == "matches" and rows[0]["benchmark"] == "Q1" and rows[4]["result"] == "missed"
    assert rows[0]["agent_url"].endswith(SID)


def test_stale_and_untested_entries(tree):
    (a,) = _agents(tree)
    a.cache["answers"]["X1"]["sql_sha"] = "0" * 16          # expected SQL changed after the capture
    assert qb.answer_text(a, "X1", {}).startswith("stale")
    a.samples[0] = "Reworded question?"                    # captured result belongs to the old wording
    qb.regrade(a, RAW)
    assert qb.starter_rows(a)[0]["verdict"] is None and qb.starter_rows(a)[0]["note"] == "Not tested yet."


def test_check_curation_reports_problems(tree):
    bad = json.loads(json.dumps(CURATION))
    c = bad["agents"][SLUG]
    c["starters"] = [{"benchmark": "Q9"}]                  # wrong count + unknown key
    c["themes"] = [{"name": "T", "keys": ["Q1-casual"]}]   # a variant; Q1 itself is then in no theme
    c["flaky"] = {}                                       # X1 failed but has no safer wording
    c["routing"] = [{"ask": "x", "agent": SLUG}]
    errs = " | ".join(qb.check_curation(bad, _agents(tree, bad)))
    for needle in ["1 entries for 2 sample questions", "'Q9' is not a benchmark key", "'Q1-casual' is a variant",
                   "base benchmark Q1 is in no theme", "X1 failed its last evaluation", "another space slug"]:
        assert needle in errs, needle


def test_curated_note_only_for_the_answer_it_describes(tree):
    cur = json.loads(json.dumps(CURATION))
    cur["agents"][SLUG]["starters"][1] = {"benchmark": "X1", "note": "Genie counts every date.",
                                          "note_sql": qb.sha("SELECT count(*)")}
    (a,) = _agents(tree, cur)
    qb.regrade(a, RAW)
    assert qb.starter_rows(a)[1]["note"] == "Genie counts every date."
    cur["agents"][SLUG]["starters"][1]["note_sql"] = "0" * 16    # a re-capture got another answer: note dropped
    (a,) = _agents(tree, cur)
    qb.regrade(a, RAW)
    assert qb.starter_rows(a)[1]["note"].startswith("Same row count (1)")


class FakeAsker:
    """Stands in for qb.Asker: one conversation per starter, follow-ups reuse it; records deletions."""

    def __init__(self):
        self.asked, self.deleted, self.n = [], [], 0

    def ask(self, sid, question, cid=None, label=""):
        self.asked.append((question, cid))
        if cid is None:
            self.n += 1
            cid = f"c{self.n}"
        return {"question": question, "status": "COMPLETED", "sql": "SELECT 1", "cols": ["x"], "rows": [["1"]],
                "row_count": 1, "conversation_id": cid, "seconds": 1.0}

    def delete(self, sid, cid):
        self.deleted.append(cid)
        return True


def test_capture_follow_up_reasks_its_starter_without_overwriting_it(tree, monkeypatch):
    monkeypatch.setattr(qb, "save_raw", lambda slug, raw: None)
    (a,) = _agents(tree)
    raw = {"expected": {}, "questions": {"S1": {"question": "Which clients are Red today?", "conversation_id": "old"}}}
    fake = FakeAsker()
    assert qb.capture_questions(None, fake, a, raw, ids=["F1"]) == 0
    assert [q for q, _ in fake.asked] == ["Which clients are Red today?", "Show the same for Amber clients."]
    assert fake.asked[1][1] == "c1" and fake.deleted == ["c1"]          # same conversation, deleted once
    assert raw["questions"]["S1"]["conversation_id"] == "old"           # the starter's own capture is kept
    assert raw["questions"]["S1~host"]["deleted"] and raw["questions"]["F1"]["deleted"]


def test_grade_booleans_and_row_filter():
    assert qb.grade_result(["g", "crossed"], [["M", "1"]], ["g", "crossed"], [["M", "true"]])[0] == "match"
    expected = {"Q8": {"cols": ["tier", "completeness"], "rows": [["Core", "0.81"], ["Strategic", "0.83"]]}}
    rq = {"status": "COMPLETED", "sql": "SELECT ...", "cols": ["c"], "rows": [["0.83"]]}
    assert qb.grade_item(rq, expected, ["Q8"])[0] == "differs"
    v, note, hit = qb.grade_item(rq, expected, ["Q8"], where={"tier": "Strategic"})   # the starter asks for one tier
    assert v == "match" and hit == "Q8" and "tier = Strategic" in note


def test_grade_percentage_shown_x100():
    exp_cols, exp = ["timing", "cases", "share"], [["At Intake", "263", "0.7346368715"], ["After", "39", "0.1089385475"]]
    v, note = qb.grade_result(exp_cols, exp, ["timing", "cases", "pct"], [["At Intake", "263", "73.46368715"],
                                                                         ["After", "39", "10.89385475"]])
    assert v == "match" and "share on a 0-100 scale" in note
    v, note = qb.grade_result(exp_cols, exp, ["timing", "pct"], [["At Intake", "73.46"], ["After", "10.89"]], need=["share"])
    assert v == "match" and "share on a 0-100 scale" in note
    v, _ = qb.grade_result(exp_cols, exp, ["timing", "cases", "pct"], [["At Intake", "26300", "73.46"], ["After", "3900", "10.89"]])
    assert v == "differs"                                    # counts are never rescaled


def test_grade_two_part_union_answer():
    expected = {"Q4": {"cols": ["country", "rate"], "rows": [["SG", "0.25"], ["HK", "0.2"]]},
                "Q4c": {"cols": ["client", "h2", "h1"], "rows": [["A", "0.1", "0.3"]]}}
    union = {"status": "COMPLETED", "sql": "SELECT ... UNION ALL SELECT ...",
             "cols": ["section", "country", "client", "rate", "prior", "current"],
             "rows": [["by_country", "SG", None, "0.25", None, None], ["by_country", "HK", None, "0.2", None, None],
                      ["rising", None, "A", None, "0.1", "0.3"]]}
    v, note, hit = qb.grade_item(union, expected, ["Q4", "Q4c"])
    assert v == "match" and hit == "Q4+Q4c" and "'by country' rows match Q4" in note
    union["rows"][2][5] = "0.31"                               # one part wrong -> no match
    assert qb.grade_item(union, expected, ["Q4", "Q4c"])[0] == "differs"
    assert qb.split_sections(["a", "b"], [["x", "1"], ["y", "2"]]) is None      # same columns: not a union
