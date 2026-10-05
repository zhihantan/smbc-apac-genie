"""Teardown of the SMBC APAC Genie demo: the 11 Genie Agents and the catalog (docs/TEARDOWN.md; DECISIONS D07).

Genie Agents were called Genie spaces until Jul 2026; the API paths, the genie/<slug>/space_id files and the
names in this script keep the old word. The default is a dry run. It is read-only (GET calls and
information_schema SELECTs) and changes nothing:
  agents    the 11 agents tracked in genie/<slug>/space_id, each fetched by id. An agent counts as ours only when
            its title starts with "APAC Genie - "; a different title under that prefix is reported as renamed
  others    any other agent in the workspace whose title has that prefix (listed, never touched)
  catalog   owner, storage root, isolation mode, and every schema with its object counts
  bundle    whether the bundle in databricks.yml is deployed (its workspace folder, its "[smbc-genie]" jobs)
  plan      exactly what --execute would do
--execute runs the same checks and refuses if any tracked agent is not provably ours. It then asks you to type
the catalog name, moves the agents to the trash (DELETE /api/2.0/genie/spaces/{id}; the workspace Trash keeps
them for 30 days) and, only when every agent is gone, runs DROP CATALOG <catalog> CASCADE (there is no UNDROP
for a catalog). It never changes permissions, warehouses, the IP access list, other agents, the bundle, jobs or
local files.

Run:  .venv/bin/python scripts/teardown.py --profile my-workspace              # dry run (read-only)
      .venv/bin/python scripts/teardown.py --profile my-workspace --execute    # asks for the catalog name
Exit: 0 ok, 1 problems found or a step failed, 2 not confirmed (nothing changed).
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

REPO = Path(__file__).resolve().parents[1]
for p in (str(REPO / "src"), str(REPO / "src" / "50_genie")):
    if p not in sys.path:
        sys.path.insert(0, p)

import yaml  # noqa: E402

from smbc_genie_lib import genie as g  # noqa: E402
from smbc_genie_lib.metrics import SPACES  # noqa: E402

TAG = "[teardown]"
TITLE_PREFIX = "APAC Genie - "
BUILD_SCHEMAS = ("bronze", "shared", "silver", "gold", "metrics", "ops")   # a catalog without these is not ours
JOB_MARKER = "[smbc-genie]"           # resources/jobs.yml job names (dev mode adds a "[dev <user>] " prefix)
SPACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
CATALOG_RE = re.compile(r"^[a-z][a-z0-9_]*$")
KINDS = {"MANAGED": "tables", "EXTERNAL": "tables", "VIEW": "views", "METRIC_VIEW": "metric views",
         "MATERIALIZED_VIEW": "materialized views", "STREAMING_TABLE": "streaming tables"}
KIND_ORDER = ["tables", "views", "metric views", "materialized views", "streaming tables", "functions", "volumes"]
_NOT_FOUND = ("notfound", "resourcedoesnotexist", "does not exist", "doesn't exist", "not found", "does_not_exist")
_IP_BLOCKED = "blocked by databricks ip acl"


def describe(e: BaseException) -> str:
    """One line: exception type and message (the SDK type name carries NotFound / ResourceDoesNotExist)."""
    return f"{type(e).__name__}: {' '.join(str(e).split())}"[:300]


def is_not_found(err: str) -> bool:
    s = err.lower()
    return any(m in s for m in _NOT_FOUND)


def is_ip_blocked(err: str) -> bool:
    return _IP_BLOCKED in err.lower()


# ---- spaces ----------------------------------------------------------------------------------------
@dataclass
class SpaceTarget:
    slug: str
    expected_title: str
    space_id: Optional[str]           # genie/<slug>/space_id; None = never created here
    live_title: Optional[str] = None
    state: str = "unchecked"          # ok | renamed | foreign | missing | untracked | error
    note: str = ""

    @property
    def deletable(self) -> bool:
        return self.state in ("ok", "renamed")


def tracked_spaces(genie_dir: Path = g.GENIE_DIR) -> List[SpaceTarget]:
    """The 11 spaces of this build (smbc_genie_lib.metrics.SPACES, in space order) with their tracked ids."""
    out = []
    for slug, (_, title) in sorted(SPACES.items(), key=lambda kv: kv[1][0]):
        p = genie_dir / slug / "space_id"
        sid = p.read_text().strip() if p.exists() else ""
        out.append(SpaceTarget(slug, title, sid or None))
    return out


def classify(t: SpaceTarget, live: Optional[Dict[str, Any]] = None, err: Optional[str] = None) -> SpaceTarget:
    """Set t.state from the GET of its tracked id: the live space, or the error the GET raised."""
    if not t.space_id:
        t.state, t.note = "untracked", f"no genie/{t.slug}/space_id - nothing to remove"
    elif not SPACE_ID_RE.match(t.space_id):
        t.state, t.note = "error", f"genie/{t.slug}/space_id holds {t.space_id[:40]!r}, not a space id"
    elif err is not None:
        if is_not_found(err):
            t.state, t.note = "missing", "not found (already trashed or deleted) - nothing to remove"
        else:
            t.state, t.note = "error", f"lookup failed: {err}"
    else:
        t.live_title = (live or {}).get("title") or ""
        if not t.live_title.startswith(TITLE_PREFIX):
            t.state, t.note = "foreign", f"title {t.live_title!r} does not start with {TITLE_PREFIX!r} - refusing"
        elif t.live_title != t.expected_title:
            t.state, t.note = "renamed", f"title {t.live_title!r} differs from {t.expected_title!r} (prefix ok)"
        else:
            t.state, t.note = "ok", ""
    return t


def inspect_spaces(ws: Any, targets: Sequence[SpaceTarget]) -> List[SpaceTarget]:
    for t in targets:
        if not t.space_id or not SPACE_ID_RE.match(t.space_id):
            classify(t)
            continue
        try:
            classify(t, ws.api("GET", f"/api/2.0/genie/spaces/{t.space_id}"))
        except Exception as e:  # noqa: BLE001 - recorded on the target
            if is_ip_blocked(str(e)):
                raise
            classify(t, err=describe(e))
    return list(targets)


def untracked_prefixed(spaces: Sequence[Dict[str, Any]], targets: Sequence[SpaceTarget]) -> List[Dict[str, Any]]:
    """Spaces titled with our prefix that no genie/<slug>/space_id tracks (reported, never touched)."""
    ids = {t.space_id for t in targets if t.space_id}
    hits = [s for s in spaces if (s.get("title") or "").startswith(TITLE_PREFIX) and s.get("space_id") not in ids]
    return sorted(hits, key=lambda s: (s.get("title") or "", s.get("space_id") or ""))


# ---- catalog ---------------------------------------------------------------------------------------
@dataclass
class CatalogInfo:
    name: str
    exists: bool = False
    owner: Optional[str] = None
    storage_root: Optional[str] = None
    isolation_mode: Optional[str] = None
    schemas: Dict[str, Dict[str, int]] = field(default_factory=dict)   # schema -> {kind: count}
    error: Optional[str] = None


def count_objects(schemas: Sequence[str], tables: Sequence[Sequence[Any]], routines: Sequence[Sequence[Any]],
                  volumes: Sequence[Sequence[Any]]) -> Dict[str, Dict[str, int]]:
    """schema -> {kind: n} from information_schema rows: tables (schema, table_type, n), routines and volumes
    (schema, n). Every schema is listed, an empty one too; information_schema itself is left out."""
    out: Dict[str, Dict[str, int]] = {s: {} for s in schemas if s != "information_schema"}
    for s, typ, n in tables:
        if s in out:
            kind = KINDS.get(str(typ).upper(), str(typ).lower())
            out[s][kind] = out[s].get(kind, 0) + int(n)
    for kind, rows in (("functions", routines), ("volumes", volumes)):
        for s, n in rows:
            if s in out:
                out[s][kind] = out[s].get(kind, 0) + int(n)
    return out


def inspect_catalog(ws: Any, name: str) -> CatalogInfo:
    info = CatalogInfo(name)
    try:
        c = ws.api("GET", f"/api/2.1/unity-catalog/catalogs/{name}")
    except Exception as e:  # noqa: BLE001
        if is_ip_blocked(str(e)):
            raise
        if not is_not_found(describe(e)):
            info.error = f"lookup failed: {describe(e)}"
        return info
    info.exists, info.owner = True, c.get("owner")
    info.storage_root, info.isolation_mode = c.get("storage_root"), c.get("isolation_mode")
    q = f"{name}.information_schema"
    try:
        _, _, schemas = ws.sql(f"SELECT schema_name FROM {q}.schemata ORDER BY 1")
        _, _, tables = ws.sql(f"SELECT table_schema, table_type, count(*) FROM {q}.tables GROUP BY ALL")
        _, _, routines = ws.sql(f"SELECT routine_schema, count(*) FROM {q}.routines GROUP BY ALL")
        _, _, volumes = ws.sql(f"SELECT volume_schema, count(*) FROM {q}.volumes GROUP BY ALL")
    except Exception as e:  # noqa: BLE001
        info.error = f"information_schema query failed: {describe(e)}"
        return info
    info.schemas = count_objects([r[0] for r in schemas], tables, routines, volumes)
    return info


# ---- bundle ----------------------------------------------------------------------------------------
@dataclass
class BundleInfo:
    name: str
    root: str
    targets: List[str] = field(default_factory=list)   # deployed targets (folders under the bundle root)
    jobs: List[str] = field(default_factory=list)      # jobs named "... [smbc-genie] ..."
    error: Optional[str] = None

    @property
    def deployed(self) -> bool:
        return bool(self.targets or self.jobs)


def bundle_name(path: Path = REPO / "databricks.yml") -> str:
    doc = yaml.safe_load(path.read_text()) or {}
    return str((doc.get("bundle") or {}).get("name") or "smbc-apac-genie")


def inspect_bundle(ws: Any, user: str, name: str) -> BundleInfo:
    """The default bundle root (/Workspace/Users/<user>/.bundle/<name>/<target>) and the jobs it deploys."""
    info = BundleInfo(name, f"/Workspace/Users/{user}/.bundle/{name}")
    try:
        r = ws.api("GET", "/api/2.0/workspace/list", query={"path": info.root})
        info.targets = sorted(Path(o["path"]).name for o in r.get("objects") or [] if o.get("object_type") == "DIRECTORY")
    except Exception as e:  # noqa: BLE001
        if is_ip_blocked(str(e)):
            raise
        if not is_not_found(describe(e)):
            info.error = f"bundle folder lookup failed: {describe(e)}"
    try:
        tok: Optional[str] = None
        while True:
            q: Dict[str, Any] = {"limit": 100, **({"page_token": tok} if tok else {})}
            r = ws.api("GET", "/api/2.2/jobs/list", query=q)
            info.jobs += [(j.get("settings") or {}).get("name") or "" for j in r.get("jobs") or []
                          if JOB_MARKER in ((j.get("settings") or {}).get("name") or "")]
            tok = r.get("next_page_token")
            if not tok:
                break
    except Exception as e:  # noqa: BLE001
        if is_ip_blocked(str(e)):
            raise
        info.error = f"jobs lookup failed: {describe(e)}"
    info.jobs.sort()
    return info


# ---- plan ------------------------------------------------------------------------------------------
def drop_statement(catalog: str) -> str:
    if not CATALOG_RE.match(catalog):
        raise ValueError(f"refusing unexpected catalog name {catalog!r}")
    return f"DROP CATALOG {catalog} CASCADE"


def plan(targets: Sequence[SpaceTarget], cat: CatalogInfo) -> List[str]:
    """The ordered actions of --execute: trash every space that is provably ours, then drop the catalog."""
    steps = [f"DELETE /api/2.0/genie/spaces/{t.space_id}  (to trash: {t.live_title})" for t in targets if t.deletable]
    if cat.exists:
        steps.append(drop_statement(cat.name))
    return steps


def blockers(targets: Sequence[SpaceTarget], cat: CatalogInfo) -> List[str]:
    """Reasons --execute refuses to start (it never runs a partial teardown on a doubtful state)."""
    out = [f"agent {t.slug}: {t.note}" for t in targets if t.state in ("foreign", "error")]
    if cat.error:
        out.append(f"catalog {cat.name}: {cat.error}")
    elif cat.exists:
        missing = [s for s in BUILD_SCHEMAS if s not in cat.schemas]
        if missing:
            out.append(f"catalog {cat.name} has no {', '.join(missing)} schema(s) - is it this build's catalog?")
    return out


def confirmed(catalog: str, ask: Callable[[str], str] = input) -> bool:
    """True only when the user types the catalog name exactly (surrounding spaces ignored)."""
    try:
        typed = ask(f"{TAG} Type the catalog name ({catalog}) to trash the agents above and drop the catalog: ")
    except (EOFError, KeyboardInterrupt):
        return False
    return typed.strip() == catalog


def execute(ws: Any, targets: Sequence[SpaceTarget], cat: CatalogInfo, log: Callable[[str], None] = print) -> int:
    """Trash the agents, stopping at the first failure with the catalog left in place; then drop the catalog."""
    for t in targets:
        if not t.deletable:
            continue
        try:
            ws.api("DELETE", f"/api/2.0/genie/spaces/{t.space_id}")
            log(f"{TAG}   trashed  {t.space_id}  {t.live_title}")
        except Exception as e:  # noqa: BLE001
            if is_not_found(describe(e)) and str(t.space_id) in describe(e):   # this space, not e.g. a bad path
                log(f"{TAG}   gone     {t.space_id}  (already trashed or deleted)")
                continue
            log(f"{TAG}   FAILED   {t.space_id}: {describe(e)}")
            log(f"{TAG} stopped: catalog {cat.name} was NOT dropped. Fix the error and re-run "
                f"(agents already trashed stay in the Trash).")
            return 1
    if not cat.exists:
        log(f"{TAG}   catalog {cat.name} does not exist - nothing to drop")
        return 0
    stmt = drop_statement(cat.name)
    try:
        ws.execute(stmt)
    except Exception as e:  # noqa: BLE001
        if is_not_found(describe(e)) and cat.name in describe(e).lower():   # the catalog, not e.g. the warehouse
            log(f"{TAG}   catalog {cat.name} already dropped")
            return 0
        log(f"{TAG}   FAILED   {stmt}: {describe(e)}")
        return 1
    log(f"{TAG}   done     {stmt}")
    return 0


# ---- report ----------------------------------------------------------------------------------------
def report(host: str, user: str, profile: Optional[str], warehouse: str, targets: Sequence[SpaceTarget],
           others: Sequence[Dict[str, Any]], cat: CatalogInfo, bundle: BundleInfo,
           log: Callable[[str], None] = print) -> None:
    log(f"{TAG} workspace {host} as {user} (profile {profile or 'default auth'}; warehouse {warehouse})")
    states = {s: sum(1 for t in targets if t.state == s) for s in ("ok", "renamed", "foreign", "missing", "untracked", "error")}
    log(f"{TAG} Genie Agents tracked in genie/<slug>/space_id: {len(targets)} ("
        + ", ".join(f"{n} {s}" for s, n in states.items() if n) + ")")
    for t in targets:
        title = t.live_title if t.live_title is not None else t.expected_title
        log(f"{TAG}   {t.state:9} {t.space_id or '-':32}  {title}" + (f"  <- {t.note}" if t.note else ""))
    log(f"{TAG} other Genie Agents titled '{TITLE_PREFIX}...' (not tracked, never touched): {len(others) or 'none'}")
    for s in others:
        log(f"{TAG}   {s.get('space_id')}  {s.get('title')}")
    if cat.error:
        log(f"{TAG} catalog {cat.name}: {cat.error}")
    elif not cat.exists:
        log(f"{TAG} catalog {cat.name}: does not exist - nothing to drop")
    else:
        log(f"{TAG} catalog {cat.name}: owner {cat.owner}, isolation {cat.isolation_mode}, storage root {cat.storage_root}")
        kinds = [k for k in KIND_ORDER if any(k in c for c in cat.schemas.values())]
        kinds += sorted({k for c in cat.schemas.values() for k in c} - set(kinds))
        log(f"{TAG}   {'schema':10}" + "".join(f"{k:>14}" for k in kinds))
        for s, c in sorted(cat.schemas.items()):
            log(f"{TAG}   {s:10}" + "".join(f"{c.get(k, 0):>14}" for k in kinds))
        total = sum(sum(c.values()) for c in cat.schemas.values())
        log(f"{TAG}   {len(cat.schemas)} schemas, {total} objects (all dropped by CASCADE)")
    if bundle.error:
        log(f"{TAG} bundle {bundle.name}: {bundle.error}")
    if bundle.deployed:
        log(f"{TAG} bundle {bundle.name}: DEPLOYED - targets {', '.join(bundle.targets) or '-'} under {bundle.root}; "
            f"{len(bundle.jobs)} job(s): {'; '.join(bundle.jobs) or '-'}")
        log(f"{TAG}   this script does not touch the bundle: run `databricks bundle destroy -t <target> "
            f"-p {profile or '<profile>'}` first (docs/TEARDOWN.md)")
    elif not bundle.error:
        log(f"{TAG} bundle {bundle.name}: not deployed (no {bundle.root}, no '{JOB_MARKER}' jobs)")


# ---- main ------------------------------------------------------------------------------------------
def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SMBC APAC Genie - teardown of the 11 Genie Agents and the catalog "
                                            "(dry run unless --execute)")
    p.add_argument("--profile", default=None, help="CLI profile, e.g. my-workspace")
    p.add_argument("--catalog", default=None, help="catalog to drop (default: the catalog in genie/_shared.yaml)")
    p.add_argument("--warehouse-id", default=None, help="SQL warehouse for the information_schema queries and the DROP")
    p.add_argument("--execute", action="store_true",
                   help="trash the agents and drop the catalog (asks you to type the catalog name)")
    return p.parse_args(argv)


def run(a: argparse.Namespace, ws: Any, ask: Callable[[str], str] = input, genie_dir: Path = g.GENIE_DIR,
        log: Callable[[str], None] = print) -> int:
    shared = g.load_shared()
    catalog = a.catalog or shared["catalog"]
    warehouse = a.warehouse_id or shared["warehouse_id"]
    if not CATALOG_RE.match(catalog):
        log(f"{TAG} refusing unexpected catalog name {catalog!r}")
        return 1
    try:
        user = ws.api("GET", "/api/2.0/preview/scim/v2/Me").get("userName") or "?"
        targets = inspect_spaces(ws, tracked_spaces(genie_dir))
        others = untracked_prefixed(ws.list_spaces(), targets)
        cat = inspect_catalog(ws, catalog)
        bundle = inspect_bundle(ws, user, bundle_name())
    except Exception as e:  # noqa: BLE001
        log(f"{TAG} cannot read the workspace: {describe(e)}")
        if is_ip_blocked(str(e)):
            log(f"{TAG} the workspace IP access list blocks this network (VPN off?). Nothing changed.")
        log(f"{TAG} check the login: databricks auth login --profile {a.profile or '<profile>'}")
        return 1
    host = getattr(getattr(getattr(ws, "w", None), "config", None), "host", None) or "?"
    report(host, user, a.profile, warehouse, targets, others, cat, bundle, log)
    steps, blocks = plan(targets, cat), blockers(targets, cat)
    log(f"{TAG} plan for --execute ({len(steps)} steps):" if steps else f"{TAG} plan for --execute: nothing to tear down")
    for i, s in enumerate(steps, 1):
        log(f"{TAG}   {i:>2}. {s}")
    log(f"{TAG} never touched: permissions and sharing, warehouse {warehouse}, the IP access list, agents not "
        f"listed above, the bundle and its jobs, local files (genie/<slug>/space_id stays as a record)")
    for b in blocks:
        log(f"{TAG} BLOCKER {b}")
    for t in targets:
        if t.state == "renamed":
            log(f"{TAG} WARNING {t.slug}: {t.note}")
    if not a.execute:
        log(f"{TAG} DRY RUN - nothing changed. To execute: .venv/bin/python scripts/teardown.py "
            f"--profile {a.profile or '<profile>'} --execute")
        return 1 if blocks else 0
    if blocks:
        log(f"{TAG} REFUSED: resolve the blockers above first. Nothing changed.")
        return 1
    if not steps:
        log(f"{TAG} nothing to tear down")
        return 0
    if not confirmed(catalog, ask):
        log(f"{TAG} not confirmed - nothing changed")
        return 2
    log(f"{TAG} executing {len(steps)} steps")
    return execute(ws, targets, cat, log)


def main(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)   # progress visible when redirected to a log
    except Exception:  # noqa: BLE001
        pass
    a = parse_args(argv)
    from genie_ws import Workspace   # deferred: the databricks-sdk import stays out of the unit tests

    try:
        ws = Workspace(a.warehouse_id or g.load_shared()["warehouse_id"], a.profile)
    except Exception as e:  # noqa: BLE001 - e.g. an expired login or an unknown profile
        print(f"{TAG} cannot connect: {describe(e)}")
        print(f"{TAG} check the login: databricks auth login --profile {a.profile or '<profile>'}")
        return 1
    return run(a, ws)


if __name__ == "__main__":
    sys.exit(main())
