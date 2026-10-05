"""Phase 7/8 benchmarks (PLAN §9; brief §8): expected-SQL checks, Genie evaluation, ops.genie_benchmarks.

Per space:
  1. execute every benchmark's expected SQL on the warehouse and apply its checks
     (rows / min_rows / max_rows / must_contain_columns / top_row_contains);
  2. evaluate Genie on all benchmarks with the eval-runs API (Beta; POST /genie/spaces/{id}/eval-runs, poll,
     then GET .../results and .../results/{result_id}); fallback: the Conversation API, one question at a time
     (start-conversation, poll the message, take the generated SQL);
  3. grade: PASS when eval-runs says GOOD, else when Genie's SQL - re-executed here - matches the expected result
     under the comparison rule (genie/README.md: same multiset of rows, values to 4 significant digits, columns
     matched by values with names / order ignored, extra rows fail, extra columns fail unless the benchmark sets
     allow_extra_columns for a genuinely ambiguous question);
  4. write per-question results + the space pass rate to ops.genie_benchmarks (MERGE on space_slug +
     benchmark_id), a summary row to ops.build_run_log and genie/<slug>/eval_report.json + eval_history.jsonl.

Standalone (bundle job p08): .venv/bin/python src/50_genie/run_benchmarks.py --profile my-workspace
[--only <slug>] - the same as `run_genie.py --evaluate`. Evaluations run sequentially (one space at a time).
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (str(REPO / "src"), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from smbc_genie_lib import genie as g  # noqa: E402
from genie_ws import TAG, Workspace, chunks, first_line, sql_str  # noqa: E402

# Columns added to ops.genie_benchmarks by Phase 7 (additive; the Phase-2 columns keep their meaning).
OPS_NEW_COLUMNS = [
    ("benchmark_key", "STRING", "Human key of the benchmark in genie/<slug>/space.yaml (Q1, Q1-casual, X1)"),
    ("space_id", "STRING", "Genie space id that was evaluated"),
    ("genie_sql", "STRING", "SQL Genie generated in the last evaluation (NULL when it answered without SQL)"),
    ("last_eval_pass", "BOOLEAN", "Final verdict of the last evaluation: eval-runs GOOD, or a match under the comparison rule"),
    ("eval_reason", "STRING", "Why: eval assessment, comparison-rule detail or error"),
    ("eval_method", "STRING", "eval_runs | conversation"),
    ("eval_run_id", "STRING", "Genie eval-run id (eval_runs method)"),
    ("space_pass_rate", "DOUBLE", "Pass rate of the space in that evaluation (passes / benchmarks)"),
]
_DONE = {"DONE", "FAILED", "CANCELLED", "CANCELED", "ERROR", "TIMEOUT"}
_MSG_DONE = {"COMPLETED", "FAILED", "CANCELLED", "QUERY_RESULT_EXPIRED"}


class EvalRunsUnavailable(RuntimeError):
    pass


# ---- 1. expected SQL --------------------------------------------------------------------------------
def run_sql_checks(ws: Workspace, built: g.BuiltSpace) -> Dict[str, Dict[str, Any]]:
    out = {}
    for b in built.benchmarks:
        try:
            cols, types, rows = ws.sql(b["expected_sql"])
            fails = g.check_result(cols, rows, b["expected_checks"])
            out[b["id"]] = {"pass": not fails, "detail": "; ".join(fails) or f"{len(rows)} rows ok",
                            "cols": cols, "rows": rows}
        except Exception as e:  # noqa: BLE001
            out[b["id"]] = {"pass": False, "detail": f"expected SQL failed: {first_line(e)}", "cols": None, "rows": None}
        r = out[b["id"]]
        print(f"{TAG}   sql   {b['key']:14} {'ok  ' if r['pass'] else 'FAIL'} {r['detail'][:110]}")
    return out


# ---- 2a. eval-runs ----------------------------------------------------------------------------------
def eval_runs(ws: Workspace, sid: str, built: g.BuiltSpace, poll_s: int = 15,
              timeout_s: int = 3600) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    base = f"/api/2.0/genie/spaces/{sid}/eval-runs"
    try:
        run = ws.api("POST", base, body={})
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "No API found" in msg or "ENDPOINT_NOT_FOUND" in msg or "FEATURE_DISABLED" in msg or "not enabled" in msg.lower():
            raise EvalRunsUnavailable(first_line(e)) from e
        raise
    run_id = run["eval_run_id"]
    print(f"{TAG}   eval-run {run_id} started ({run.get('num_questions')} questions)")
    t0 = time.time()
    while str(run.get("eval_run_status")).upper() not in _DONE:
        if time.time() - t0 > timeout_s:
            raise RuntimeError(f"eval-run {run_id} still {run.get('eval_run_status')} after {timeout_s}s")
        time.sleep(poll_s)
        run = ws.api("GET", f"{base}/{run_id}")
        print(f"{TAG}   eval-run {run.get('eval_run_status')}: {run.get('num_done', '?')}/{run.get('num_questions')} done, "
              f"{run.get('num_correct')} correct, {run.get('num_needs_review')} need review")
    results, tok = [], None
    while True:
        r = ws.api("GET", f"{base}/{run_id}/results", query={"page_token": tok} if tok else None)
        results += r.get("eval_results", []) or []
        tok = r.get("next_page_token")
        if not tok:
            break
    out: Dict[str, Dict[str, Any]] = {}
    for res in results:
        det = ws.api("GET", f"{base}/{run_id}/results/{res['result_id']}")
        act = (det.get("actual_response") or [{}])[0] if det.get("actual_response") else {}
        sql = act.get("response") if str(act.get("response_type", "SQL")).upper() == "SQL" else None
        out[res["benchmark_question_id"]] = {
            "assessment": det.get("assessment"), "genie_sql": sql, "response_type": act.get("response_type"),
            "reasons": det.get("assessment_reasons") or det.get("assessment_reason"), "result_id": res["result_id"],
            "text": None if sql else act.get("response"),
            "result": g.typed_result(act.get("sql_execution_result") or {}),
        }
    meta = {"run_id": run_id, "status": run.get("eval_run_status"), "num_questions": run.get("num_questions"),
            "num_correct": run.get("num_correct"), "num_needs_review": run.get("num_needs_review")}
    return out, meta


# ---- 2b. Conversation API fallback ------------------------------------------------------------------
def conversation_eval(ws: Workspace, sid: str, built: g.BuiltSpace, spacing_s: float = 13.0,
                      timeout_s: int = 600) -> Dict[str, Dict[str, Any]]:
    """One question at a time (<= ~5 questions / minute / workspace); back-off is inside ws.api."""
    out = {}
    for b in built.benchmarks:
        t_start = time.time()
        try:
            r = ws.api("POST", f"/api/2.0/genie/spaces/{sid}/start-conversation", body={"content": b["question"]})
            cid = r.get("conversation_id") or (r.get("conversation") or {}).get("id")
            mid = r.get("message_id") or (r.get("message") or {}).get("id")
            msg: Dict[str, Any] = r.get("message") or {}
            while str(msg.get("status")).upper() not in _MSG_DONE:
                if time.time() - t_start > timeout_s:
                    raise RuntimeError(f"message still {msg.get('status')} after {timeout_s}s")
                time.sleep(3)
                msg = ws.api("GET", f"/api/2.0/genie/spaces/{sid}/conversations/{cid}/messages/{mid}")
            sql, text, result = None, None, None
            for att in msg.get("attachments") or []:
                if (att.get("query") or {}).get("query"):
                    sql = att["query"]["query"]
                    try:   # Genie's own result (needed when it answered with a parameterised trusted query)
                        qr = ws.api("GET", f"/api/2.0/genie/spaces/{sid}/conversations/{cid}/messages/{mid}"
                                           f"/attachments/{att['attachment_id']}/query-result")
                        sr = qr.get("statement_response") or {}
                        res = sr.get("result") or {}
                        if res.get("data_array") is not None and "data_typed_array" not in res:
                            res["data_typed_array"] = [{"values": [{"str": v} if v is not None else {} for v in row]}
                                                       for row in res["data_array"]]
                        result = g.typed_result({"status": sr.get("status"), "manifest": sr.get("manifest"), "result": res})
                    except Exception:  # noqa: BLE001 - fall back to re-executing the SQL
                        result = None
                elif (att.get("text") or {}).get("content"):
                    text = att["text"]["content"]
            out[b["id"]] = {"assessment": None, "genie_sql": sql, "response_type": "SQL" if sql else "TEXT",
                            "reasons": msg.get("error"), "text": text, "status": msg.get("status"), "result": result}
        except Exception as e:  # noqa: BLE001
            out[b["id"]] = {"assessment": None, "genie_sql": None, "response_type": "ERROR", "reasons": first_line(e),
                            "text": None}
        print(f"{TAG}   conv  {b['key']:14} {out[b['id']]['response_type']}")
        wait = spacing_s - (time.time() - t_start)
        if wait > 0:
            time.sleep(wait)
    return out


# ---- 3. grading -------------------------------------------------------------------------------------
def grade(ws: Workspace, built: g.BuiltSpace, sqlres: Dict[str, Dict[str, Any]],
          evalres: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out = {}
    for b in built.benchmarks:
        e = evalres.get(b["id"]) or {}
        asm = e.get("assessment")
        why_api = f"{asm or 'conversation'}" + (f" {e.get('reasons')}" if e.get("reasons") and asm else "")
        if asm == "GOOD":
            out[b["id"]] = {"pass": True, "reason": "eval-runs GOOD"}
        elif not e.get("genie_sql"):
            what = e.get("text") or e.get("reasons") or e.get("response_type") or "no answer"
            out[b["id"]] = {"pass": False, "reason": f"{asm or 'conversation'}: no SQL ({g.one_line(what)[:200]})"}
        elif sqlres[b["id"]]["rows"] is None:
            out[b["id"]] = {"pass": False, "reason": f"{why_api}; expected SQL failed"}
        else:
            try:
                if e.get("result") is not None:          # the result Genie / the grader actually got
                    cols, rows = e["result"]
                elif g.sql_parameters(e["genie_sql"]):
                    raise RuntimeError("parameterised Genie SQL without an inline result - cannot re-execute")
                else:
                    cols, _, rows = ws.sql(e["genie_sql"])
                ok, why = g.compare_results(sqlres[b["id"]]["cols"], sqlres[b["id"]]["rows"], cols, rows,
                                            allow_extra_columns=b["allow_extra_columns"])
                out[b["id"]] = {"pass": ok, "reason": f"{why_api}; rule: {why}"}
            except Exception as ex:  # noqa: BLE001
                out[b["id"]] = {"pass": False, "reason": f"{why_api}; Genie SQL failed here: {first_line(ex)}"}
        r = out[b["id"]]
        print(f"{TAG}   grade {b['key']:14} {'PASS' if r['pass'] else 'FAIL'}  {r['reason'][:120]}")
    return out


# ---- 4. outputs -------------------------------------------------------------------------------------
def ensure_ops_columns(ws: Workspace, catalog: str) -> List[str]:
    _, _, rows = ws.sql(f"DESCRIBE TABLE {catalog}.ops.genie_benchmarks")
    have = {str(r[0]).lower() for r in rows if r and r[0] and not str(r[0]).startswith("#")}
    missing = [(n, t, c) for n, t, c in OPS_NEW_COLUMNS if n not in have]
    if missing:
        cols = ", ".join(f"{n} {t} COMMENT {sql_str(c)}" for n, t, c in missing)
        ws.execute(f"ALTER TABLE {catalog}.ops.genie_benchmarks ADD COLUMNS ({cols})")
        print(f"{TAG}   ops.genie_benchmarks: added columns {', '.join(n for n, _, _ in missing)}")
    return [n for n, _, _ in missing]


def _ts(t: Optional[dt.datetime]) -> str:
    return "NULL" if t is None else f"TIMESTAMP'{t:%Y-%m-%d %H:%M:%S}'"


def _b(v: Optional[bool]) -> str:
    return "NULL" if v is None else ("TRUE" if v else "FALSE")


def write_ops(ws: Workspace, catalog: str, built: g.BuiltSpace, sid: Optional[str],
              results: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
    """MERGE the benchmark catalogue (+ results when given) into ops.genie_benchmarks for this space.
    Without results, earlier results are kept for benchmarks whose question and expected SQL are unchanged."""
    ensure_ops_columns(ws, catalog)
    now = dt.datetime.now(dt.timezone.utc)
    rows = []
    for b in built.benchmarks:
        r = (results or {}).get(b["id"]) or {}
        c = b["expected_checks"]
        min_rows = c.get("rows", c.get("min_rows"))
        rows.append("(" + ", ".join([
            sql_str(built.slug), sql_str(b["id"]), sql_str(b["question"]), sql_str(b["expected_sql"]),
            "NULL" if min_rows is None else str(int(min_rows)),
            sql_str(",".join(c.get("must_contain_columns") or [])) if c.get("must_contain_columns") else "NULL",
            sql_str(json.dumps(c.get("top_row_contains"))) if c.get("top_row_contains") else "NULL",
            sql_str(b["variant_of_id"]), _b(r.get("sql_pass")), sql_str(r.get("assessment")),
            _ts(now) if results else "NULL", sql_str(b["key"]), sql_str(sid), sql_str(r.get("genie_sql")),
            _b(r.get("pass")), sql_str(r.get("reason")), sql_str(r.get("method")), sql_str(r.get("run_id")),
            "NULL" if r.get("pass_rate") is None else repr(float(r["pass_rate"])),
        ]) + ")")
    res_cols = ["last_sql_pass", "last_eval_assessment", "checked_at", "space_id", "genie_sql", "last_eval_pass",
                "eval_reason", "eval_method", "eval_run_id", "space_pass_rate"]
    cat_cols = ["question", "expected_sql", "min_rows", "must_contain_columns", "top_row_contains", "variant_of",
                "benchmark_key"]
    types = {"space_slug": "STRING", "benchmark_id": "STRING", "question": "STRING", "expected_sql": "STRING",
             "min_rows": "INT", "must_contain_columns": "STRING", "top_row_contains": "STRING", "variant_of": "STRING",
             "last_sql_pass": "BOOLEAN", "last_eval_assessment": "STRING", "checked_at": "TIMESTAMP",
             "benchmark_key": "STRING", "space_id": "STRING", "genie_sql": "STRING", "last_eval_pass": "BOOLEAN",
             "eval_reason": "STRING", "eval_method": "STRING", "eval_run_id": "STRING", "space_pass_rate": "DOUBLE"}
    all_cols = list(types)
    keep = "t.question = s.question AND t.expected_sql = s.expected_sql"
    upd = [f"t.{c} = s.{c}" for c in cat_cols] + \
          [f"t.{c} = {'s.' + c if results else f'CASE WHEN {keep} THEN t.{c} ELSE NULL END'}" for c in res_cols
           if c != "space_id"] + ["t.space_id = s.space_id"]
    casts = ", ".join(f"CAST({c} AS {t}) AS {c}" for c, t in types.items())
    src = f"SELECT {casts} FROM VALUES {', '.join(rows)} AS v({', '.join(all_cols)})"
    merge = (f"MERGE INTO {catalog}.ops.genie_benchmarks t USING ({src}) s "
             f"ON t.space_slug = s.space_slug AND t.benchmark_id = s.benchmark_id "
             f"WHEN MATCHED THEN UPDATE SET {', '.join(upd)} "
             f"WHEN NOT MATCHED THEN INSERT ({', '.join(all_cols)}) VALUES ({', '.join('s.' + c for c in all_cols)}) "
             f"WHEN NOT MATCHED BY SOURCE AND t.space_slug = {sql_str(built.slug)} THEN DELETE")
    delay = 3.0
    for attempt in range(1, 7):        # several spaces may merge concurrently (one agent per space group)
        try:
            ws.execute(merge)
            break
        except Exception as e:  # noqa: BLE001
            if attempt == 6 or "concurrent" not in str(e).lower():
                raise
            print(f"{TAG}   ops.genie_benchmarks: concurrent write, retry in {delay:.0f}s")
            time.sleep(delay)
            delay *= 2
    print(f"{TAG}   ops.genie_benchmarks: {len(rows)} rows merged for {built.slug}" + (" (with results)" if results else ""))


def log_run(ws: Workspace, catalog: str, built: g.BuiltSpace, sid: str, summary: Dict[str, Any], t0: float) -> None:
    msg = (f"pass {summary['passed']}/{summary['n']} = {summary['pass_rate']:.1%} via {summary['method']}"
           f" (eval GOOD {summary.get('api_good')}, NEEDS_REVIEW {summary.get('api_needs_review')}, "
           f"BAD {summary.get('api_bad')}; expected SQL ok {summary['sql_ok']}/{summary['n']}; run {summary.get('run_id')})")
    now = time.time()
    ts = lambda t: f"TIMESTAMP'{dt.datetime.fromtimestamp(t, tz=dt.timezone.utc):%Y-%m-%d %H:%M:%S}'"  # noqa: E731
    try:
        ws.execute(
            f"INSERT INTO {catalog}.ops.build_run_log (run_id, phase, step, object_name, row_count, status, message, "
            f"scale, started_at, finished_at, duration_sec) VALUES ({sql_str(summary['eval_id'])}, 'p07_genie', "
            f"'evaluate', {sql_str(f'{built.title} [{sid}]')}, {summary['n']}, "
            f"{sql_str('ok' if summary['pass_rate'] >= 0.85 else 'warn')}, {sql_str(msg)}, NULL, {ts(t0)}, {ts(now)}, "
            f"{round(now - t0, 1)})")
    except Exception as e:  # noqa: BLE001 - logging must never mask the result
        print(f"{TAG}   WARN could not write ops.build_run_log: {first_line(e)}")


def evaluate(ws: Workspace, catalog: str, built: g.BuiltSpace, sid: str, method: str = "auto",
             label: str = "") -> Dict[str, Any]:
    t0 = time.time()
    print(f"{TAG} evaluate {built.slug} ({sid}): {len(built.benchmarks)} benchmarks")
    sqlres = run_sql_checks(ws, built)
    meta: Dict[str, Any] = {}
    used = method
    if method in ("auto", "eval_runs"):
        try:
            evalres, meta = eval_runs(ws, sid, built)
            used = "eval_runs"
        except EvalRunsUnavailable as e:
            if method == "eval_runs":
                raise
            print(f"{TAG}   eval-runs unavailable ({e}); falling back to the Conversation API")
            evalres, used = conversation_eval(ws, sid, built), "conversation"
    else:
        evalres, used = conversation_eval(ws, sid, built), "conversation"
    graded = grade(ws, built, sqlres, evalres)
    n = len(built.benchmarks)
    passed = sum(1 for r in graded.values() if r["pass"])
    asms = [((evalres.get(b["id"]) or {}).get("assessment")) for b in built.benchmarks]
    summary = {
        "eval_id": f"p07-eval-{dt.datetime.now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}", "slug": built.slug,
        "space_id": sid, "label": label, "method": used, "run_id": meta.get("run_id"), "n": n, "passed": passed,
        "pass_rate": passed / n if n else 0.0, "api_good": asms.count("GOOD"), "api_bad": asms.count("BAD"),
        "api_needs_review": asms.count("NEEDS_REVIEW"), "sql_ok": sum(1 for r in sqlres.values() if r["pass"]),
        "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    results = {}
    for b in built.benchmarks:
        e = evalres.get(b["id"]) or {}
        results[b["id"]] = {"sql_pass": sqlres[b["id"]]["pass"], "assessment": e.get("assessment"),
                            "genie_sql": e.get("genie_sql"), "pass": graded[b["id"]]["pass"],
                            "reason": graded[b["id"]]["reason"], "method": used, "run_id": meta.get("run_id"),
                            "pass_rate": summary["pass_rate"]}
    write_ops(ws, catalog, built, sid, results)
    log_run(ws, catalog, built, sid, summary, t0)
    report = {"summary": summary, "benchmarks": [{
        "key": b["key"], "id": b["id"], "question": b["question"], "variant_of": b["variant_of"],
        "expected_sql_pass": sqlres[b["id"]]["pass"], "expected_sql_detail": sqlres[b["id"]]["detail"],
        "assessment": results[b["id"]]["assessment"], "pass": results[b["id"]]["pass"],
        "reason": results[b["id"]]["reason"], "genie_sql": results[b["id"]]["genie_sql"],
        "expected_sql": b["expected_sql"]} for b in built.benchmarks]}
    d = g.GENIE_DIR / built.slug
    (d / "eval_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    with open(d / "eval_history.jsonl", "a") as f:
        f.write(json.dumps(summary) + "\n")
    print(f"{TAG} evaluate {built.slug}: PASS {passed}/{n} = {summary['pass_rate']:.1%} via {used} "
          f"(eval-runs GOOD {summary['api_good']}, NEEDS_REVIEW {summary['api_needs_review']}, BAD {summary['api_bad']}; "
          f"expected SQL ok {summary['sql_ok']}/{n})")
    return summary


def main(argv: List[str] | None = None) -> int:
    from run_genie import main as genie_main

    args = list(sys.argv[1:] if argv is None else argv)
    if "--evaluate" not in args:
        args.append("--evaluate")
    return genie_main(args)


if __name__ == "__main__":
    sys.exit(main())
