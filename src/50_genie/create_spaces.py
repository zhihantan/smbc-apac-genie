"""Phase 7 create / update (PLAN §9; DECISIONS D06, D42).

Per space:
  1. deploy genie/<slug>/functions.sql (CREATE OR REPLACE FUNCTION in <catalog>.gold, each with a COMMENT),
     then run its `-- test:` probes (each must return >= 1 row) and check the UC comments;
  2. find OUR space: the id tracked in genie/<slug>/space_id, else exactly one space with the exact title;
     create it when there is none (POST) or replace its content (PATCH, full serialized_space);
  3. GET it back (include_serialized_space) and diff it against what we sent.
Safety: only spaces titled exactly like the space (`APAC Genie - ...`) are touched; a tracked id whose title
changed, or several spaces with our title and no tracked id, stop the run. Permissions are never changed.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (str(REPO / "src"), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from smbc_genie_lib import genie as g  # noqa: E402
from genie_ws import TAG, Workspace, first_line  # noqa: E402

TITLE_PREFIX = "APAC Genie - "


class SpaceSafetyError(RuntimeError):
    """Refusing to touch a space we cannot prove is ours."""


def space_id_path(slug: str) -> Path:
    return g.GENIE_DIR / slug / "space_id"


def tracked_id(slug: str) -> Optional[str]:
    p = space_id_path(slug)
    if p.exists():
        sid = p.read_text().strip()
        return sid or None
    return None


def deploy_functions(ws: Workspace, built: g.BuiltSpace, dry_run: bool = False) -> List[str]:
    problems = []
    for f in built.functions:
        if dry_run:
            print(f"{TAG}   [dry-run] would CREATE OR REPLACE FUNCTION {f.identifier}")
            continue
        try:
            ws.execute(f.sql)
            print(f"{TAG}   function {f.identifier} deployed")
        except Exception as e:  # noqa: BLE001
            problems.append(f"{f.identifier}: deploy failed: {first_line(e, 400)}")
    if dry_run:
        return problems
    for t in built.function_tests:
        try:
            _, _, rows = ws.sql(t, row_limit=50)
            ok = len(rows) > 0
            print(f"{TAG}   function test {'ok ' if ok else 'EMPTY'} {len(rows):>3} rows  {t[:90]}")
            if not ok:
                problems.append(f"function test returned 0 rows: {t[:160]}")
        except Exception as e:  # noqa: BLE001
            problems.append(f"function test failed: {t[:120]}: {first_line(e)}")
    return problems


def resolve_space(ws: Workspace, built: g.BuiltSpace) -> Tuple[Optional[str], str]:
    """(space_id or None, how) for OUR space; raises SpaceSafetyError when ownership is unclear."""
    if not built.title.startswith(TITLE_PREFIX):
        raise SpaceSafetyError(f"title '{built.title}' does not start with '{TITLE_PREFIX}'")
    hits = [s for s in ws.list_spaces() if s.get("title") == built.title]
    sid = tracked_id(built.slug)
    if sid:
        if any(h["space_id"] == sid for h in hits):
            if len(hits) > 1:
                print(f"{TAG}   warn: {len(hits)} spaces titled '{built.title}'; updating the tracked one {sid}")
            return sid, "tracked"
        try:
            cur = ws.get_space(sid)
        except Exception:  # noqa: BLE001 - gone (trashed / deleted)
            cur = None
        if cur is not None:
            raise SpaceSafetyError(f"tracked space {sid} is now titled '{cur.get('title')}' - refusing to touch it")
        if hits:
            raise SpaceSafetyError(f"tracked space {sid} is gone but {len(hits)} other space(s) carry our title: "
                                   f"{[h['space_id'] for h in hits]} - resolve by hand")
        return None, "tracked id gone; will create"
    if len(hits) == 1:
        return hits[0]["space_id"], "adopted by exact title (no space_id file)"
    if len(hits) > 1:
        raise SpaceSafetyError(f"{len(hits)} spaces titled '{built.title}' and no genie/{built.slug}/space_id: "
                               f"{[h['space_id'] for h in hits]}")
    return None, "new"


def round_trip(ws: Workspace, sid: str, built: g.BuiltSpace) -> List[str]:
    """Content diff of GET vs sent. Both sides are id-sorted first: the server returns benchmarks.questions in
    descending id order (verified 2026-10-04) although it requires every list ascending on input."""
    got = ws.get_space(sid)
    live = json.loads(got.get("serialized_space") or "{}")
    sent_joins = (built.serialized.get("instructions") or {}).get("join_specs")
    auto = (live.get("instructions") or {}).get("join_specs") if not sent_joins else None
    if auto:   # the server derives join specs from UC PK / FK constraints (PLAN §2.1): expected, not a diff
        print(f"{TAG}   note: server added {len(auto)} join spec(s) from UC foreign keys")
        live["instructions"].pop("join_specs")
    raw_order = [q.get("id") for q in (live.get("benchmarks") or {}).get("questions") or []]
    diffs = g.diff_json(g.sort_serialized_space(copy.deepcopy(built.serialized)), g.sort_serialized_space(live))
    for k in ("title", "description", "warehouse_id"):
        if got.get(k) != getattr(built, k):
            diffs.append(f"{k}: sent {getattr(built, k)!r} != got {got.get(k)!r}")
    if raw_order and raw_order != sorted(raw_order):
        print(f"{TAG}   note: GET returns benchmarks.questions in {'descending' if raw_order == sorted(raw_order, reverse=True) else 'non-sorted'} id order (content compared id-sorted)")
    return diffs


def smoke_question(ws: Workspace, sid: str, question: str, timeout_s: int = 240) -> Optional[str]:
    """Ask one question through the Conversation API; return a problem string when the space cannot answer.
    The create / update API accepts configurations that Genie later rejects at question time for EVERY
    question (e.g. a SQL function with a DATE argument), so this is the only reliable post-deploy check."""
    import time

    r = ws.api("POST", f"/api/2.0/genie/spaces/{sid}/start-conversation", body={"content": question})
    cid, mid = r.get("conversation_id"), r.get("message_id")
    t0, msg = time.time(), r.get("message") or {}
    while msg.get("status") not in ("COMPLETED", "FAILED", "CANCELLED", "QUERY_RESULT_EXPIRED"):
        if time.time() - t0 > timeout_s:
            return f"smoke question still {msg.get('status')} after {timeout_s}s"
        time.sleep(3)
        msg = ws.api("GET", f"/api/2.0/genie/spaces/{sid}/conversations/{cid}/messages/{mid}")
    has_sql = any((a.get("query") or {}).get("query") for a in msg.get("attachments") or [])
    print(f"{TAG}   smoke question: {msg.get('status')} in {time.time() - t0:.0f}s, "
          f"{'SQL answer' if has_sql else 'no SQL'}  ({question[:70]})")
    if msg.get("status") != "COMPLETED":
        err = msg.get("error") or {}
        return f"smoke question {msg.get('status')}: {g.one_line(err.get('error') if isinstance(err, dict) else err)[:400]}"
    return None


def create_or_update(ws: Workspace, built: g.BuiltSpace, dry_run: bool = False, smoke: bool = True) -> Dict[str, Any]:
    """Deploy functions, create / update the space, round-trip it. Returns a summary dict."""
    out: Dict[str, Any] = {"slug": built.slug, "problems": []}
    out["problems"] += deploy_functions(ws, built, dry_run)
    sid, how = resolve_space(ws, built)
    out["how"] = how
    if dry_run:
        if sid:
            diffs = round_trip(ws, sid, built)
            print(f"{TAG}   [dry-run] would PATCH {sid} ({how}); {len(diffs)} differences vs live:")
            for d in diffs[:25]:
                print(f"{TAG}     ~ {d}")
        else:
            print(f"{TAG}   [dry-run] would POST a new space '{built.title}' ({how})")
        out["space_id"] = sid
        return out
    body = built.body()
    if sid:
        ws.api("PATCH", f"/api/2.0/genie/spaces/{sid}", body=body)
        action = "updated"
    else:
        resp = ws.api("POST", "/api/2.0/genie/spaces", body=body)
        sid = resp["space_id"]
        action = "created"
    space_id_path(built.slug).write_text(sid + "\n")
    diffs = round_trip(ws, sid, built)
    out.update(space_id=sid, action=action, diffs=diffs)
    print(f"{TAG}   space {action}: {sid} ({how}); round-trip GET: "
          f"{'identical' if not diffs else f'{len(diffs)} differences'}")
    for d in diffs[:25]:
        print(f"{TAG}     ~ {d}")
    if smoke and built.serialized["config"]["sample_questions"]:
        prob = smoke_question(ws, sid, built.serialized["config"]["sample_questions"][0]["question"][0])
        if prob:
            out["problems"].append(prob)
    return out
