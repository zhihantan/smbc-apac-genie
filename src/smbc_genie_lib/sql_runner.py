"""Templated SQL runner (docs/DECISIONS.md D05).

Executes ``.sql`` files on a Databricks SQL warehouse through the Statement Execution API,
substituting ``${...}`` placeholders (catalog, as_of_date, scale, ...). Statements are split
on top-level ``;`` so one file can hold a whole phase's DDL. Also usable locally for tests
and metric-view validation.

The databricks-sdk import is deferred so importing this module (or the package) never
requires the SDK — unit tests that only touch the pure helpers stay dependency-free.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List, Optional

_PLACEHOLDER_RE = re.compile(r"\$\{(\w+)\}")


def render(sql_text: str, params: Dict[str, object]) -> str:
    """Replace ${name} placeholders; a missing name is left intact (and should fail loudly)."""
    def _sub(m: "re.Match[str]") -> str:
        key = m.group(1)
        return str(params[key]) if key in params else m.group(0)

    return _PLACEHOLDER_RE.sub(_sub, sql_text)


def _is_executable(stmt: str) -> bool:
    """True if, after removing -- line comments and whitespace, any SQL remains."""
    body = "\n".join(
        line for line in stmt.splitlines() if not line.lstrip().startswith("--")
    ).strip()
    return bool(body)


def split_statements(sql_text: str) -> List[str]:
    """Split on ``;`` that are not inside quotes or $$...$$ (metric-view YAML) blocks.

    Leading comments stay attached to the statement that follows them (Databricks accepts
    them); statements that are *only* comments/whitespace are dropped.
    """
    statements: List[str] = []
    buf: List[str] = []
    i, n = 0, len(sql_text)
    in_single = in_double = in_dollar = False

    def flush() -> None:
        stmt = "".join(buf).strip()
        if stmt and _is_executable(stmt):
            statements.append(stmt)

    while i < n:
        ch = sql_text[i]
        two = sql_text[i : i + 2]
        if not (in_single or in_double) and two == "$$":
            in_dollar = not in_dollar
            buf.append(two)
            i += 2
            continue
        if not in_dollar:
            if ch == "'" and not in_double:
                in_single = not in_single
            elif ch == '"' and not in_single:
                in_double = not in_double
            elif ch == "-" and two == "--" and not (in_single or in_double):
                nl = sql_text.find("\n", i)
                nl = n if nl == -1 else nl
                buf.append(sql_text[i:nl])
                i = nl
                continue
            elif ch == ";" and not (in_single or in_double):
                flush()
                buf = []
                i += 1
                continue
        buf.append(ch)
        i += 1
    flush()
    return statements


class SqlRunner:
    """Thin wrapper over the SDK's statement execution against one warehouse."""

    def __init__(self, warehouse_id: str, params: Optional[Dict[str, object]] = None, profile: Optional[str] = None):
        from databricks.sdk import WorkspaceClient  # deferred import

        self.warehouse_id = warehouse_id
        self.params = dict(params or {})
        self._w = WorkspaceClient(profile=profile) if profile else WorkspaceClient()

    def execute(self, sql: str) -> object:
        """Run one statement and RAISE if it does not reach SUCCEEDED.

        The Statement Execution API reports SQL errors in the response body (state=FAILED),
        not as an exception, so a caller that ignores the state would silently swallow
        failures. We surface them. For statements longer than the 50s inline wait we poll.
        """
        from databricks.sdk.service.sql import StatementState

        resp = self._w.statement_execution.execute_statement(
            warehouse_id=self.warehouse_id, statement=sql, wait_timeout="50s"
        )
        stmt_id = resp.statement_id
        while resp.status and resp.status.state in (StatementState.PENDING, StatementState.RUNNING):
            import time

            time.sleep(2)
            resp = self._w.statement_execution.get_statement(stmt_id)

        state = resp.status.state if resp.status else None
        if state != StatementState.SUCCEEDED:
            err = getattr(resp.status, "error", None)
            detail = getattr(err, "message", None) or str(err) or str(state)
            raise RuntimeError(f"SQL {state}: {detail}\n  statement: {sql[:200]}")
        return resp

    def run_text(self, sql_text: str) -> int:
        rendered = render(sql_text, self.params)
        count = 0
        for stmt in split_statements(rendered):
            self.execute(stmt)
            count += 1
        return count

    def run_file(self, path: "str | os.PathLike[str]") -> int:
        return self.run_text(Path(path).read_text())
