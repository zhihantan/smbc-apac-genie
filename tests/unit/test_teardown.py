"""Unit tests for scripts/teardown.py: ownership checks, the plan, the typed confirmation and the execution order.

No workspace: a fake records every call, so the tests can prove the dry run is read-only and that nothing is
deleted before the catalog name is typed.
"""
import argparse
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from smbc_genie_lib.metrics import SPACES

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("smbc_teardown", REPO / "scripts" / "teardown.py")
td = importlib.util.module_from_spec(_spec)
sys.modules["smbc_teardown"] = td          # dataclasses resolve string annotations through sys.modules
_spec.loader.exec_module(td)

SIDS = {slug: f"{n:032x}" for slug, (n, _) in SPACES.items()}          # 32-hex ids, one per space
TITLES = {SIDS[slug]: title for slug, (_, title) in SPACES.items()}


class NotFound(Exception):            # same type names as databricks.sdk.errors
    pass


class ResourceDoesNotExist(Exception):
    pass


class FakeWorkspace:
    """Canned GET / SELECT answers; DELETE and DROP are recorded and can be made to fail."""

    def __init__(self, titles=None, catalog=True, schemas=td.BUILD_SCHEMAS + ("default",), jobs=(), extra=(),
                 fail_delete=(), fail_drop=None, me_error=None):
        self.titles = dict(TITLES if titles is None else titles)       # live spaces: id -> title
        self.catalog, self.schemas, self.jobs, self.extra = catalog, schemas, list(jobs), list(extra)
        self.fail_delete, self.fail_drop, self.me_error = set(fail_delete), fail_drop, me_error
        self.calls = []
        self.w = SimpleNamespace(config=SimpleNamespace(host="https://example.cloud.databricks.com"))

    def api(self, method, path, body=None, query=None):
        self.calls.append((method, path))
        if path == "/api/2.0/preview/scim/v2/Me":
            if self.me_error:
                raise self.me_error
            return {"userName": "someone@example.com"}
        if path.startswith("/api/2.0/genie/spaces/"):
            sid = path.rsplit("/", 1)[1]
            if sid in self.fail_delete and method == "DELETE":
                raise RuntimeError("INTERNAL_ERROR: try again later")
            if sid not in self.titles:
                raise NotFound(f"Space with id {sid} not found")
            if method == "DELETE":
                del self.titles[sid]
                return {}
            return {"space_id": sid, "title": self.titles[sid]}
        if path.startswith("/api/2.1/unity-catalog/catalogs/"):
            name = path.rsplit("/", 1)[1]
            if not self.catalog:
                raise NotFound(f"Catalog '{name}' does not exist.")
            return {"name": name, "owner": "someone@example.com", "storage_root": "s3://bucket/root",
                    "isolation_mode": "ISOLATED"}
        if path == "/api/2.0/workspace/list":
            raise ResourceDoesNotExist(f"Path ({query['path']}) doesn't exist.")
        if path == "/api/2.2/jobs/list":
            return {"jobs": [{"job_id": i, "settings": {"name": n}} for i, n in enumerate(self.jobs)]}
        raise AssertionError(f"unexpected call {method} {path}")

    def list_spaces(self):
        self.calls.append(("GET", "/api/2.0/genie/spaces"))
        return [{"space_id": k, "title": v} for k, v in self.titles.items()] + self.extra

    def sql(self, statement, row_limit=20000):
        self.calls.append(("SQL", statement))
        assert statement.startswith("SELECT"), statement                # the dry run only reads
        if ".schemata" in statement:
            return ["schema_name"], ["STRING"], [[s] for s in self.schemas] + [["information_schema"]]
        if ".tables" in statement:
            return [], [], [["gold", "MANAGED", "93"], ["gold", "VIEW", "1"], ["metrics", "METRIC_VIEW", "43"],
                            ["information_schema", "VIEW", "30"]]
        if ".routines" in statement:
            return [], [], [["gold", "40"]]
        return [], [], []                                                 # volumes

    def execute(self, statement):
        self.calls.append(("EXEC", statement))
        if self.fail_drop:
            raise self.fail_drop

    def destructive(self):
        return [c for c in self.calls if c[0] in ("DELETE", "EXEC")]


@pytest.fixture
def genie_dir(tmp_path):
    for slug, sid in SIDS.items():
        (tmp_path / slug).mkdir()
        (tmp_path / slug / "space_id").write_text(sid + "\n")
    return tmp_path


def _args(execute=False, catalog=None):
    return argparse.Namespace(profile="test-profile", catalog=catalog, warehouse_id="wh0", execute=execute)


def _run(ws, genie_dir, execute=False, typed=None, catalog=None):
    out = []

    def ask(prompt):
        if typed is None:
            raise AssertionError("must not ask for confirmation")
        return typed

    rc = td.run(_args(execute, catalog), ws, ask=ask, genie_dir=genie_dir, log=out.append)
    return rc, "\n".join(out)


def _target(state="ok", title=None, sid="0" * 31 + "1"):
    t = td.SpaceTarget("account_planning", "APAC Genie - Account Planning", sid)
    return td.classify(t, {"title": title or "APAC Genie - Account Planning"}) if state == "ok" else t


# ---- spaces ----------------------------------------------------------------------------------------
def test_tracked_spaces_reads_ids_in_space_order(tmp_path):
    (tmp_path / "early_warning_monitoring").mkdir()
    (tmp_path / "early_warning_monitoring" / "space_id").write_text("  " + "a" * 32 + "\n")
    ts = td.tracked_spaces(tmp_path)
    assert [t.slug for t in ts] == [s for s, _ in sorted(SPACES.items(), key=lambda kv: kv[1][0])]
    assert {t.slug: t.space_id for t in ts if t.space_id} == {"early_warning_monitoring": "a" * 32}
    assert all(t.expected_title == SPACES[t.slug][1] for t in ts)


def test_tracked_spaces_of_this_repo():
    ts = td.tracked_spaces()
    assert len(ts) == 11 and all(t.expected_title.startswith(td.TITLE_PREFIX) for t in ts)
    assert all(td.SPACE_ID_RE.match(t.space_id) for t in ts if t.space_id)


def test_classify_states():
    def t(sid="b" * 32):
        return td.SpaceTarget("tb_trade_scf", SPACES["tb_trade_scf"][1], sid)

    assert td.classify(t(), {"title": SPACES["tb_trade_scf"][1]}).state == "ok"
    assert td.classify(t(), {"title": "APAC Genie - Trade (old)"}).state == "renamed"
    foreign = td.classify(t(), {"title": "KYC Analyst Genie"})
    assert foreign.state == "foreign" and "refusing" in foreign.note
    assert td.classify(t(), err="NotFound: Space with id x not found").state == "missing"
    assert td.classify(t(), err="PermissionDenied: no access").state == "error"
    assert td.classify(t(None)).state == "untracked"
    assert td.classify(t("../../etc/passwd")).state == "error"           # never sent to the API
    assert [s for s in ("ok", "renamed", "foreign", "missing", "untracked", "error")
            if td.SpaceTarget("x", "y", None, state=s).deletable] == ["ok", "renamed"]


def test_untracked_prefixed_lists_only_untracked_spaces_with_our_prefix():
    ours = [_target()]
    spaces = [{"space_id": ours[0].space_id, "title": "APAC Genie - Account Planning"},
              {"space_id": "c" * 32, "title": "APAC Genie - Account Planning"},
              {"space_id": "d" * 32, "title": "Credit Risk Genie"}]
    assert [s["space_id"] for s in td.untracked_prefixed(spaces, ours)] == ["c" * 32]


# ---- catalog and plan ------------------------------------------------------------------------------
def test_count_objects():
    got = td.count_objects(["bronze", "default", "gold", "information_schema"],
                           [["bronze", "MANAGED", "81"], ["gold", "MANAGED", "93"], ["gold", "VIEW", "1"],
                            ["information_schema", "VIEW", "30"]], [["gold", "40"]], [])
    assert got == {"bronze": {"tables": 81}, "default": {}, "gold": {"tables": 93, "views": 1, "functions": 40}}


def test_drop_statement_only_for_plain_catalog_names():
    assert td.drop_statement("smbc_genie") == "DROP CATALOG smbc_genie CASCADE"
    for bad in ("smbc_genie; DROP CATALOG main", "Main", "`smbc_genie`", "", "1abc"):
        with pytest.raises(ValueError):
            td.drop_statement(bad)


def test_plan_trashes_our_spaces_then_drops_the_catalog():
    ok, renamed = _target(), _target(sid="e" * 32)
    td.classify(renamed, {"title": "APAC Genie - Account Planning v2"})
    gone = td.classify(td.SpaceTarget("tb_trade_scf", "t", "f" * 32), err="NotFound: not found")
    cat = td.CatalogInfo("smbc_genie", exists=True)
    steps = td.plan([ok, gone, renamed], cat)
    assert len(steps) == 3 and steps[-1] == "DROP CATALOG smbc_genie CASCADE"
    assert ok.space_id in steps[0] and renamed.space_id in steps[1]
    assert td.plan([gone], td.CatalogInfo("smbc_genie")) == []


def test_blockers():
    good = td.CatalogInfo("smbc_genie", exists=True, schemas={s: {} for s in td.BUILD_SCHEMAS})
    assert td.blockers([_target()], good) == []
    foreign = td.classify(td.SpaceTarget("tb_trade_scf", "t", "f" * 32), {"title": "UNO Bank Genie"})
    assert len(td.blockers([foreign], good)) == 1
    other = td.CatalogInfo("main", exists=True, schemas={"default": {}})
    assert "is it this build's catalog" in td.blockers([], other)[0]
    assert td.blockers([], td.CatalogInfo("smbc_genie", error="lookup failed: boom"))
    assert td.blockers([], td.CatalogInfo("smbc_genie")) == []          # already gone: nothing to block


def test_confirmed_needs_the_exact_catalog_name():
    assert td.confirmed("smbc_genie", lambda p: "smbc_genie")
    assert td.confirmed("smbc_genie", lambda p: "  smbc_genie \n")
    for typed in ("", "y", "yes", "SMBC_GENIE", "smbc_genie2", "smbc-genie"):
        assert not td.confirmed("smbc_genie", lambda p, t=typed: t)

    def eof(p):
        raise EOFError

    def interrupt(p):
        raise KeyboardInterrupt

    assert not td.confirmed("smbc_genie", eof) and not td.confirmed("smbc_genie", interrupt)


# ---- execute ---------------------------------------------------------------------------------------
def _targets(ws):
    return td.inspect_spaces(ws, [td.SpaceTarget(s, SPACES[s][1], sid) for s, sid in SIDS.items()])


def test_execute_trashes_every_space_before_dropping():
    ws = FakeWorkspace()
    targets = _targets(ws)
    assert td.execute(ws, targets, td.CatalogInfo("smbc_genie", exists=True), log=lambda m: None) == 0
    done = ws.destructive()
    assert [c[0] for c in done] == ["DELETE"] * 11 + ["EXEC"]
    assert done[-1] == ("EXEC", "DROP CATALOG smbc_genie CASCADE") and not ws.titles


def test_execute_stops_at_a_failed_delete_and_keeps_the_catalog():
    second = SIDS["opportunity_identification"]
    ws = FakeWorkspace(fail_delete={second})
    out = []
    assert td.execute(ws, _targets(ws), td.CatalogInfo("smbc_genie", exists=True), log=out.append) == 1
    assert [c[1].rsplit("/", 1)[1] for c in ws.destructive()] == [SIDS["account_planning"], second]
    assert "NOT dropped" in "\n".join(out)


def test_execute_treats_a_vanished_space_as_gone():
    ws = FakeWorkspace()
    targets = _targets(ws)
    del ws.titles[SIDS["account_planning"]]                   # trashed by someone between check and execute
    assert td.execute(ws, targets, td.CatalogInfo("smbc_genie", exists=True), log=lambda m: None) == 0
    assert ws.destructive()[-1][0] == "EXEC"


def test_execute_drop_failures():
    ws = FakeWorkspace(fail_drop=RuntimeError("SQL FAILED: PERMISSION_DENIED: User is not an owner of Catalog 'smbc_genie'"))
    assert td.execute(ws, [], td.CatalogInfo("smbc_genie", exists=True), log=lambda m: None) == 1
    ws = FakeWorkspace(fail_drop=NotFound("Warehouse wh0 not found"))     # a generic not-found is a failure
    assert td.execute(ws, [], td.CatalogInfo("smbc_genie", exists=True), log=lambda m: None) == 1
    ws = FakeWorkspace(fail_drop=RuntimeError("SQL FAILED: [NO_SUCH_CATALOG_EXCEPTION] Catalog 'smbc_genie' not found"))
    assert td.execute(ws, [], td.CatalogInfo("smbc_genie", exists=True), log=lambda m: None) == 0
    ws = FakeWorkspace()
    assert td.execute(ws, [], td.CatalogInfo("smbc_genie"), log=lambda m: None) == 0 and not ws.destructive()


# ---- run (dry run and --execute) -------------------------------------------------------------------
def test_dry_run_is_read_only(genie_dir):
    ws = FakeWorkspace()
    rc, out = _run(ws, genie_dir)
    assert rc == 0 and not ws.destructive()
    assert {c[0] for c in ws.calls} == {"GET", "SQL"}
    assert "DRY RUN - nothing changed" in out and "12. DROP CATALOG smbc_genie CASCADE" in out
    assert "11 ok" in out and "bundle smbc-apac-genie: not deployed" in out


def test_execute_requires_the_typed_catalog_name(genie_dir):
    ws = FakeWorkspace()
    assert _run(ws, genie_dir, execute=True, typed="yes")[0] == 2 and not ws.destructive()
    ws = FakeWorkspace()
    rc, out = _run(ws, genie_dir, execute=True, typed="smbc_genie")
    assert rc == 0 and len(ws.destructive()) == 12 and "done     DROP CATALOG smbc_genie CASCADE" in out


def test_execute_refuses_when_a_tracked_space_is_not_ours(genie_dir):
    titles = dict(TITLES, **{SIDS["signals_sentiment"]: "KYC Analyst Genie"})
    ws = FakeWorkspace(titles=titles)
    rc, out = _run(ws, genie_dir, execute=True)                # typed=None: asking would fail the test
    assert rc == 1 and not ws.destructive() and "REFUSED" in out and "BLOCKER" in out
    assert _run(FakeWorkspace(titles=titles), genie_dir)[0] == 1     # the dry run reports it too


def test_execute_refuses_a_catalog_that_is_not_the_build(genie_dir):
    ws = FakeWorkspace(schemas=("default",))
    rc, out = _run(ws, genie_dir, execute=True, catalog="main")
    assert rc == 1 and not ws.destructive() and "is it this build's catalog" in out


def test_untracked_spaces_and_a_deployed_bundle_are_reported_not_touched(genie_dir):
    stray = {"space_id": "9" * 32, "title": "APAC Genie - Account Planning"}
    ws = FakeWorkspace(extra=[stray], jobs=["[dev someone] [smbc-genie] p02 setup", "UNO Bank nightly"])
    rc, out = _run(ws, genie_dir, execute=True, typed="smbc_genie")
    assert rc == 0 and "9" * 32 in out and "DEPLOYED" in out and "[smbc-genie] p02 setup" in out
    assert all("9" * 32 not in c[1] for c in ws.destructive())


def test_nothing_to_do_when_already_torn_down(genie_dir):
    ws = FakeWorkspace(titles={}, catalog=False)
    rc, out = _run(ws, genie_dir, execute=True)
    assert rc == 0 and "nothing to tear down" in out and not ws.destructive()


def test_ip_acl_block_stops_before_anything(genie_dir):
    ws = FakeWorkspace(me_error=RuntimeError("Source IP address: 1.2.3.4 is blocked by Databricks IP ACL"))
    rc, out = _run(ws, genie_dir, execute=True)
    assert rc == 1 and "IP access list" in out and not ws.destructive()


def test_makefile_teardown_target_is_a_dry_run():
    text = (REPO / "Makefile").read_text()
    recipe = text[text.index("\nteardown:") + 1:].split("\n\n")[0].splitlines()[1:]
    commands = [line.strip() for line in recipe if not line.strip().startswith("@echo")]
    assert commands and all("scripts/teardown.py" in c and "--execute" not in c for c in commands)
    assert "teardown.sql" not in "\n".join(recipe)
