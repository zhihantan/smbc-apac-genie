"""Phase 2 smoke test — validate the risky metric-view and Genie features before scaling.

Creates throwaway ``ops._smoke_*`` objects, exercises each feature, prints PASS/FAIL with the
real error, then tears everything down (incl. the Genie space). Resolves provisional decisions
D31 (arrays in joins), D32 (window measures), D33 (calendar from order field), plus MEASURE()
inside a SQL UDF, '%' in field names, and the Genie serialized_space metric_views shape.

Run locally:  .venv/bin/python src/00_setup/run_smoke.py --warehouse-id <id> --profile <p>
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from smbc_genie_lib.sql_runner import SqlRunner  # noqa: E402
from smbc_genie_lib.genie import sort_serialized_space  # noqa: E402

RESULTS: list[tuple[str, str, str]] = []


def check(name: str, fn):
    try:
        detail = fn() or ""
        RESULTS.append((name, "PASS", str(detail)[:160]))
        print(f"  PASS  {name}  {str(detail)[:120]}")
    except Exception as e:  # noqa: BLE001
        msg = str(e).replace("\n", " ")
        RESULTS.append((name, "FAIL", msg[:200]))
        print(f"  FAIL  {name}  -> {msg[:180]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--warehouse-id", required=True)
    ap.add_argument("--catalog", default="smbc_genie")
    ap.add_argument("--profile", default=None)
    a = ap.parse_args()
    c = a.catalog
    r = SqlRunner(a.warehouse_id, profile=a.profile)
    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient(profile=a.profile) if a.profile else WorkspaceClient()

    def scalar(sql: str):
        resp = r.execute(sql)
        return resp.result.data_array[0] if resp.result and resp.result.data_array else []

    print("[smoke] backing tables")
    r.execute(f"CREATE OR REPLACE TABLE {c}.ops._smoke_group (client_group_id STRING, group_name STRING)")
    r.execute(f"INSERT INTO {c}.ops._smoke_group VALUES ('G1','Kinokawa Precision')")
    r.execute(f"""CREATE OR REPLACE TABLE {c}.ops._smoke_client_arr
                  (golden_client_sk BIGINT, client_group_id STRING, legal_name STRING, aliases ARRAY<STRING>)""")
    r.execute(f"""INSERT INTO {c}.ops._smoke_client_arr VALUES
                  (1,'G1','Kinokawa Precision SG', array('KPSG','Kinokawa SG')),
                  (2,'G1','Kinokawa Precision VN', array('KPVN'))""")
    r.execute(f"CREATE OR REPLACE VIEW {c}.ops._smoke_client_core AS "
              f"SELECT golden_client_sk, client_group_id, legal_name FROM {c}.ops._smoke_client_arr")
    r.execute(f"CREATE OR REPLACE TABLE {c}.ops._smoke_fact (d DATE, golden_client_sk BIGINT, balance DOUBLE, revenue DOUBLE)")
    r.execute(f"""INSERT INTO {c}.ops._smoke_fact VALUES
                  (DATE'2026-08-31',1,100,10),(DATE'2026-09-30',1,150,15),(DATE'2026-09-30',2,200,20)""")

    print("[smoke] metric-view features")

    def mv_full():
        r.execute(f"""CREATE OR REPLACE VIEW {c}.ops._smoke_mv WITH METRICS LANGUAGE YAML
COMMENT 'smoke' AS $$
version: 1.1
comment: Smoke metric view (fact -> client -> group, window + ratio).
source: {c}.ops._smoke_fact
joins:
  - name: client
    source: {c}.ops._smoke_client_core
    "on": source.golden_client_sk = client.golden_client_sk
    joins:
      - name: grp
        source: {c}.ops._smoke_group
        "on": client.client_group_id = grp.client_group_id
dimensions:
  - name: Month
    expr: DATE_TRUNC('MONTH', source.d)
  - name: Fiscal Year
    expr: "CASE WHEN MONTH(source.d) >= 4 THEN YEAR(source.d) ELSE YEAR(source.d) - 1 END"
  - name: Client Group
    expr: client.grp.group_name
    synonyms: [group, parent]
measures:
  - name: Total Balance
    expr: SUM(source.balance)
    comment: Sum of balances.
    format: {{type: number, decimal_places: {{type: exact, places: 0}}}}
  - name: Total Revenue
    expr: SUM(source.revenue)
  - name: Balance Month End
    expr: SUM(source.balance)
    comment: Point-in-time; latest month in selection.
    window:
      - order: Month
        range: current
        semiadditive: last
  - name: Rev to Bal Ratio
    expr: MEASURE(`Total Revenue`) / MEASURE(`Total Balance`)
$$""")
        return "created"

    check("F1 metric view create (nested join, format, synonyms)", mv_full)
    check("F1 MEASURE query + nested-join dim",
          lambda: f"rows={scalar(f'SELECT count(*) FROM (SELECT `Client Group`, `Month`, MEASURE(`Total Balance`) tb FROM {c}.ops._smoke_mv GROUP BY `Client Group`, `Month`)')}")
    check("F3 Fiscal Year dim from order field",
          lambda: f"fy={scalar(f'SELECT `Fiscal Year`, MEASURE(`Total Balance`) FROM {c}.ops._smoke_mv GROUP BY `Fiscal Year`')}")

    def window_semiadditive():
        # Group by Client Group only: Balance Month End must be last month (350), not sum (450).
        row = scalar(f"SELECT MEASURE(`Total Balance`) tb, MEASURE(`Balance Month End`) bme "
                     f"FROM {c}.ops._smoke_mv GROUP BY `Client Group`")
        tb, bme = float(row[0]), float(row[1])
        assert abs(tb - 450) < 1e-6, f"total balance {tb} != 450"
        assert abs(bme - 350) < 1e-6, f"month-end {bme} != 350 (semiadditive:last not applied)"
        return f"tb={tb} bme={bme} (last-month, not summed)"

    check("F2 window semiadditive:last (D32)", window_semiadditive)
    check("F4 MEASURE() composition ratio",
          lambda: f"ratio={scalar(f'SELECT round(MEASURE(`Rev to Bal Ratio`),4) FROM {c}.ops._smoke_mv')}")

    def pct_name():
        r.execute(f"""CREATE OR REPLACE VIEW {c}.ops._smoke_mv_pct WITH METRICS LANGUAGE YAML AS $$
version: 1.1
source: {c}.ops._smoke_fact
dimensions:
  - name: Client SK
    expr: source.golden_client_sk
measures:
  - name: Rev Pct %
    expr: 100 * SUM(source.revenue) / SUM(source.balance)
$$""")
        return f"val={scalar(f'SELECT round(MEASURE(`Rev Pct %`),2) FROM {c}.ops._smoke_mv_pct')}"

    check("F5 '%' in measure name", pct_name)

    def array_in_join_fails():
        try:
            r.execute(f"""CREATE OR REPLACE VIEW {c}.ops._smoke_mv_arr WITH METRICS LANGUAGE YAML AS $$
version: 1.1
source: {c}.ops._smoke_fact
joins:
  - name: client
    source: {c}.ops._smoke_client_arr
    "on": source.golden_client_sk = client.golden_client_sk
dimensions:
  - name: Name
    expr: client.legal_name
measures:
  - name: Bal
    expr: SUM(source.balance)
$$""")
            # If create succeeded, try querying — array in join may fail at query time instead.
            scalar(f"SELECT `Name`, MEASURE(`Bal`) FROM {c}.ops._smoke_mv_arr GROUP BY `Name`")
            return "UNEXPECTED: array-in-join source worked (D31 may be unnecessary)"
        except Exception as e:  # noqa: BLE001
            return f"as expected, array join rejected: {str(e).splitlines()[0][:90]}"

    check("F6 array col in join -> handled (D31)", array_in_join_fails)

    def udf_measure():
        r.execute(f"""CREATE OR REPLACE FUNCTION {c}.ops._smoke_fn_balance()
                      RETURNS TABLE (client_group STRING, total_balance DOUBLE)
                      COMMENT 'smoke udf using MEASURE()'
                      RETURN SELECT `Client Group`, MEASURE(`Total Balance`) FROM {c}.ops._smoke_mv GROUP BY `Client Group`""")
        return f"rows={scalar(f'SELECT count(*) FROM {c}.ops._smoke_fn_balance()')}"

    check("F7 SQL table function using MEASURE()", udf_measure)

    print("[smoke] Genie serialized_space")
    space_id = {"id": None}

    def genie_create():
        hexid = lambda: uuid.uuid4().hex
        ss = {
            "version": 2,
            "config": {"sample_questions": [{"id": hexid(), "question": ["Total balance by client group"]}]},
            "data_sources": {"tables": [{"identifier": f"{c}.ops._smoke_mv", "column_configs": []}]},
            "instructions": {
                "text_instructions": [{"id": hexid(), "content": ["Smoke-test space; delete me."]}],
                "example_question_sqls": [
                    {"id": hexid(), "question": ["Balance for a group"],
                     "sql": [f"SELECT `Client Group`, MEASURE(`Total Balance`) FROM {c}.ops._smoke_mv ",
                             "WHERE `Client Group` = :grp GROUP BY `Client Group`"],
                     "parameters": [{"name": "grp", "type_hint": "STRING",
                                     "default_value": {"values": ["Kinokawa Precision"]}}]},
                    {"id": hexid(), "question": ["Balance as of a month"],
                     "sql": [f"SELECT `Month`, MEASURE(`Balance Month End`) FROM {c}.ops._smoke_mv ",
                             "WHERE `Month` = :m GROUP BY `Month`"],
                     "parameters": [{"name": "m", "type_hint": "DATE",
                                     "default_value": {"values": ["2026-09-01"]}}]},
                ],
            },
            "benchmarks": {"questions": [{"id": hexid(), "question": ["Total balance by client group"],
                           "answer": [{"format": "SQL",
                           "content": [f"SELECT `Client Group`, MEASURE(`Total Balance`) AS total_balance FROM {c}.ops._smoke_mv GROUP BY `Client Group`"]}]}]},
        }
        resp = w.api_client.do("POST", "/api/2.0/genie/spaces", body={
            "warehouse_id": a.warehouse_id,
            "serialized_space": json.dumps(sort_serialized_space(ss)),
            "title": "ZZ Smoke - APAC Genie (delete me)",
            "description": "Phase 2 smoke test.",
        })
        space_id["id"] = resp.get("space_id")
        return f"space_id={space_id['id']} (STRING+DATE param hints accepted)"

    check("F8 Genie create (metric_views + STRING/DATE params)", genie_create)

    def genie_roundtrip():
        sid = space_id["id"]
        assert sid, "no space id"
        got = w.api_client.do("GET", f"/api/2.0/genie/spaces/{sid}", query={"include_serialized_space": "true"})
        ss = json.loads(got["serialized_space"])
        ds = ss.get("data_sources", {})
        ids = [t["identifier"] for t in ds.get("tables", [])] + [t["identifier"] for t in ds.get("metric_views", [])]
        assert any("_smoke_mv" in m for m in ids), f"metric view not round-tripped: {ds}"
        return f"metric view attached under {'tables' if any('_smoke_mv' in t['identifier'] for t in ds.get('tables', [])) else 'metric_views'}: {ids}"

    check("F8 Genie GET round-trip (metric_views placement)", genie_roundtrip)

    # cleanup
    print("[smoke] cleanup")
    if space_id["id"]:
        try:
            w.api_client.do("DELETE", f"/api/2.0/genie/spaces/{space_id['id']}")
            print("  trashed smoke space")
        except Exception as e:  # noqa: BLE001
            print(f"  WARN could not trash space {space_id['id']}: {str(e)[:120]}")
    for obj, kind in [("_smoke_mv", "VIEW"), ("_smoke_mv_pct", "VIEW"), ("_smoke_mv_arr", "VIEW"),
                      ("_smoke_client_core", "VIEW"), ("_smoke_fact", "TABLE"),
                      ("_smoke_client_arr", "TABLE"), ("_smoke_group", "TABLE")]:
        try:
            r.execute(f"DROP {kind} IF EXISTS {c}.ops.{obj}")
        except Exception:  # noqa: BLE001
            pass
    try:
        r.execute(f"DROP FUNCTION IF EXISTS {c}.ops._smoke_fn_balance")
    except Exception:  # noqa: BLE001
        pass

    passed = sum(1 for _, s, _ in RESULTS if s == "PASS")
    print(f"\n[smoke] {passed}/{len(RESULTS)} checks passed")
    for name, status, detail in RESULTS:
        print(f"  {status:4}  {name}")
    if passed < len(RESULTS):
        sys.exit(1)


if __name__ == "__main__":
    main()
