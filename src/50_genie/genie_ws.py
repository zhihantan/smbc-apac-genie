"""Workspace access for Phase 7 (Genie REST + SQL warehouse), with exponential back-off on rate limits.

Used by build_space_specs / metadata_gate / create_spaces / run_benchmarks / run_genie. The databricks-sdk
import is deferred so `--build-only` runs without the SDK or a login.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

TAG = "[p07]"
_RETRY_MARKERS = ("429", "too many requests", "resource_exhausted", "rate limit", "ratelimit", "temporarily_unavailable",
                  "503", "502", "504", "service unavailable", "timed out", "timeout", "connection reset")


def _retryable(e: Exception) -> bool:
    s = f"{type(e).__name__} {e}".lower()
    return any(m in s for m in _RETRY_MARKERS)


class Workspace:
    """One WorkspaceClient + one SQL warehouse."""

    def __init__(self, warehouse_id: str, profile: Optional[str] = None, max_tries: int = 7):
        from databricks.sdk import WorkspaceClient

        self.warehouse_id = warehouse_id
        self.w = WorkspaceClient(profile=profile) if profile else WorkspaceClient()
        self.max_tries = max_tries

    # ---- REST ------------------------------------------------------------------------------------------
    def api(self, method: str, path: str, body: Optional[Dict[str, Any]] = None,
            query: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        delay = 2.0
        for attempt in range(1, self.max_tries + 1):
            try:
                kw: Dict[str, Any] = {}
                if body is not None:
                    kw["body"] = body
                if query is not None:
                    kw["query"] = query
                return self.w.api_client.do(method, path, **kw) or {}
            except Exception as e:  # noqa: BLE001
                if attempt == self.max_tries or not _retryable(e):
                    raise
                print(f"{TAG}   back-off {delay:.0f}s after {type(e).__name__}: {str(e)[:120]}")
                time.sleep(delay)
                delay = min(delay * 2, 120)
        return {}

    # ---- SQL -------------------------------------------------------------------------------------------
    def sql(self, statement: str, row_limit: int = 20000) -> Tuple[List[str], List[str], List[List[Any]]]:
        """Run one statement; return (column names, type names, all rows as strings / None). Raises on failure."""
        from databricks.sdk.service.sql import Disposition, Format, StatementState

        delay = 2.0
        for attempt in range(1, self.max_tries + 1):
            try:
                resp = self.w.statement_execution.execute_statement(
                    warehouse_id=self.warehouse_id, statement=statement, wait_timeout="50s",
                    disposition=Disposition.INLINE, format=Format.JSON_ARRAY, row_limit=row_limit)
                break
            except Exception as e:  # noqa: BLE001
                if attempt == self.max_tries or not _retryable(e):
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 120)
        sid = resp.statement_id
        while resp.status and resp.status.state in (StatementState.PENDING, StatementState.RUNNING):
            time.sleep(2)
            resp = self.w.statement_execution.get_statement(sid)
        state = resp.status.state if resp.status else None
        if state != StatementState.SUCCEEDED:
            err = getattr(resp.status, "error", None)
            raise RuntimeError(f"SQL {state}: {getattr(err, 'message', None) or err}")
        cols = [c.name for c in (resp.manifest.schema.columns or [])] if resp.manifest and resp.manifest.schema else []
        types = [str(getattr(c.type_name, "value", c.type_name) or "") for c in (resp.manifest.schema.columns or [])] \
            if resp.manifest and resp.manifest.schema else []
        rows: List[List[Any]] = list(resp.result.data_array or []) if resp.result else []
        nxt = resp.result.next_chunk_index if resp.result else None
        while nxt is not None:
            chunk = self.w.statement_execution.get_statement_result_chunk_n(sid, nxt)
            rows += list(chunk.data_array or [])
            nxt = chunk.next_chunk_index
        return cols, types, rows

    def execute(self, statement: str) -> None:
        self.sql(statement, row_limit=1)

    # ---- Genie ------------------------------------------------------------------------------------------
    def list_spaces(self) -> List[Dict[str, Any]]:
        out, tok = [], None
        while True:
            q: Dict[str, Any] = {"page_size": 100}
            if tok:
                q["page_token"] = tok
            r = self.api("GET", "/api/2.0/genie/spaces", query=q)
            out += r.get("spaces", []) or []
            tok = r.get("next_page_token")
            if not tok:
                return out

    def get_space(self, space_id: str) -> Dict[str, Any]:
        return self.api("GET", f"/api/2.0/genie/spaces/{space_id}", query={"include_serialized_space": "true"})


def sql_str(text: Any) -> str:
    """SQL string literal with backslash escapes; None -> NULL."""
    if text is None:
        return "NULL"
    return "'" + str(text).replace("\\", "\\\\").replace("'", "\\'") + "'"


def first_line(e: BaseException, n: int = 240) -> str:
    return " ".join(str(e).split())[:n]


def chunks(seq: Sequence[Any], n: int) -> List[Sequence[Any]]:
    return [seq[i:i + n] for i in range(0, len(seq), n)]
