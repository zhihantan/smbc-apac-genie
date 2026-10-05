"""Genie spaces (Phase 7; PLAN §9 / §2.1; brief §3.4 / §8; DECISIONS D06, D16, D30, D35, D42).

Authoring source -> Genie ``serialized_space`` v2. Each space is written by hand as
``genie/<slug>/space.yaml`` (+ ``functions.sql``); the format is documented in ``genie/README.md``.
This module is pure Python (pyyaml only, unit-tested in tests/unit/test_genie*.py):

* ``load_shared`` / ``load_space``: read ``genie/_shared.yaml`` and one space source, rejecting unknown keys;
* ``build_space``: validate against the limits and render the payload - deterministic ordered ids
  (``ordered_id``), metric views in ``data_sources.tables`` (D42), column configs for every column from the
  ``genie/_schema`` snapshot, the one text-instruction block (shared base + as-of + domain + response),
  parameterised example SQL, SQL functions, sample questions and benchmarks (expected SQL optionally pulled
  from ``metrics/_answers/<slug>.sql``);
* ``sort_serialized_space``: the API rejects unsorted lists - tables / metric_views by ``identifier``,
  ``column_configs`` by ``column_name``, every id list by ``id`` (Phase-2 smoke test);
* ``diff_json``: round-trip diff of what we sent against what GET returns;
* ``check_result`` / ``compare_results``: benchmark checks on the expected SQL and the result comparison
  rule used when Genie's answer is not graded GOOD by the eval-runs API (README "Comparison rule").
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - yaml is a hard dependency of the package
    yaml = None

REPO = Path(__file__).resolve().parents[2]
GENIE_DIR = REPO / "genie"
SHARED_PATH = GENIE_DIR / "_shared.yaml"
SCHEMA_DIR = GENIE_DIR / "_schema"
ANSWERS_DIR = REPO / "metrics" / "_answers"

# Lists keyed by "id" that must be sorted by id.
_ID_LISTS = [
    ("config", "sample_questions"),
    ("instructions", "text_instructions"),
    ("instructions", "example_question_sqls"),
    ("instructions", "sql_functions"),
    ("instructions", "join_specs"),
    ("benchmarks", "questions"),
]
_SNIPPET_LISTS = ["filters", "expressions", "measures"]

SPACE_KEYS = {"slug", "title", "description", "data_sources", "instructions", "example_sqls", "sql_functions",
              "sample_questions", "benchmarks", "notes"}
SOURCE_KEYS = {"identifier", "columns", "include_columns", "notes"}
COLUMN_KEYS = {"synonyms", "description", "entity_matching", "format_assistance", "exclude"}
INSTRUCTION_KEYS = {"domain", "response"}
EXAMPLE_KEYS = {"question", "usage_guidance", "parameters", "sql", "notes"}
PARAM_KEYS = {"name", "type", "description", "default"}
BENCHMARK_KEYS = {"key", "question", "answer", "expected_sql", "variant_of", "checks", "allow_extra_columns", "notes"}
CHECK_KEYS = {"rows", "min_rows", "max_rows", "must_contain_columns", "top_row_contains"}
_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,40}$")
_FQN_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")
_FORBIDDEN_FN_RE = re.compile(r"\b(current_date|current_timestamp|now|getdate|curdate)\s*\(", re.I)
_PLACEHOLDER_RE = re.compile(r"\$\{(\w+)\}")


class GenieSpecError(ValueError):
    """A malformed shared file, space source or functions file."""


# ---- ids and sorting --------------------------------------------------------------------------------
def stable_id(*parts: object) -> str:
    """Deterministic 32-char lowercase hex id (Genie requires 32-hex, unique within its list)."""
    payload = "\x1f".join(str(p) for p in parts).encode("utf-8")
    return hashlib.blake2b(payload, digest_size=16).hexdigest()


def ordered_id(slug: str, kind: str, index: int) -> str:
    """32-hex id = 24-hex hash of (slug, kind) + 8-hex position: unique within a list, stable across
    rebuilds and sorted in authoring order (the API wants id-sorted lists; the UI shows them in that order)."""
    if not 0 <= int(index) < 16 ** 8:
        raise ValueError(f"index out of range: {index}")
    prefix = hashlib.blake2b(f"{slug}\x1f{kind}".encode("utf-8"), digest_size=12).hexdigest()
    return f"{prefix}{int(index):08x}"


def _sort_by(seq: Any, key: str) -> None:
    if isinstance(seq, list) and all(isinstance(x, dict) for x in seq):
        seq.sort(key=lambda d: str(d.get(key, "")))


def sort_serialized_space(ss: Dict[str, Any]) -> Dict[str, Any]:
    """Sort every list the Genie API expects pre-sorted. Mutates and returns ``ss``."""
    ds = ss.get("data_sources", {})
    for coll in ("tables", "metric_views"):
        _sort_by(ds.get(coll), "identifier")
        for item in ds.get(coll, []) or []:
            _sort_by(item.get("column_configs"), "column_name")

    for section, name in _ID_LISTS:
        _sort_by(ss.get(section, {}).get(name), "id")

    snippets = ss.get("instructions", {}).get("sql_snippets", {})
    for name in _SNIPPET_LISTS:
        _sort_by(snippets.get(name), "id")

    return ss


def new_space(title: str, description: str, *, metric_views: List[str] | None = None,
              tables: List[str] | None = None) -> Dict[str, Any]:
    """Start a minimal, valid v2 serialized_space with the given data sources.

    Metric views and tables both go in ``data_sources.tables`` (sorted by identifier). The
    Phase-2 smoke test showed the server normalises metric views sent under ``metric_views``
    into ``tables`` on read, so putting them there directly keeps create->get round-trips
    diff-free (DECISIONS D42).
    """
    identifiers = sorted(set((metric_views or []) + (tables or [])))
    ss: Dict[str, Any] = {
        "version": 2,
        "config": {"sample_questions": []},
        "data_sources": {
            "tables": [{"identifier": i, "column_configs": []} for i in identifiers],
        },
        "instructions": {"text_instructions": [], "example_question_sqls": [], "sql_functions": []},
        "benchmarks": {"questions": []},
    }
    return ss


# ---- small helpers ----------------------------------------------------------------------------------
def one_line(text: Any) -> str:
    return " ".join(str(text or "").split())


def to_lines(text: str) -> List[str]:
    """Multi-line string -> Genie string array: every line keeps its newline except the last."""
    body = str(text).strip("\n")
    lines = body.split("\n")
    return [ln + "\n" for ln in lines[:-1]] + [lines[-1]]


def from_lines(lines: Sequence[str]) -> str:
    return "".join(lines)


def render(text: str, params: Mapping[str, Any]) -> str:
    """Substitute ``${name}`` placeholders; an unknown placeholder fails."""
    def sub(m: "re.Match[str]") -> str:
        if m.group(1) not in params:
            raise GenieSpecError(f"unknown placeholder ${{{m.group(1)}}}")
        return str(params[m.group(1)])
    return _PLACEHOLDER_RE.sub(sub, str(text))


def _yaml_load(path: Path) -> Any:
    if yaml is None:  # pragma: no cover
        raise RuntimeError("pyyaml is required")
    try:
        return yaml.safe_load(Path(path).read_text())
    except yaml.YAMLError as e:
        raise GenieSpecError(f"{path}: invalid YAML: {e}") from e


def _unknown(where: str, d: Mapping[str, Any], allowed: Set[str]) -> List[str]:
    return [f"{where}: unknown key '{k}' (allowed: {', '.join(sorted(allowed))})" for k in d if k not in allowed]


def _strip_sql_noise(sql: str) -> str:
    """SQL without string literals, quoted identifiers' contents kept, and without -- / /* */ comments."""
    out, i, n = [], 0, len(sql)
    while i < n:
        ch, two = sql[i], sql[i:i + 2]
        if two == "--":
            j = sql.find("\n", i)
            i = n if j == -1 else j
            continue
        if two == "/*":
            j = sql.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if ch in ("'", '"'):
            j = i + 1
            while j < n and sql[j] != ch:
                j += 2 if sql[j] == "\\" else 1
            out.append(" '' ")
            i = j + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


_PARAM_RE = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")
_REF_RE = re.compile(r"`?([A-Za-z_]\w*)`?\s*\.\s*`?([A-Za-z_]\w*)`?\s*\.\s*`?([A-Za-z_]\w*)`?(\s*\()?")


def sql_parameters(sql: str) -> Set[str]:
    """Named parameter markers (``:name``) outside literals and comments (``::`` casts excluded)."""
    return set(_PARAM_RE.findall(_strip_sql_noise(sql)))


def sql_references(sql: str, catalog: str) -> Tuple[Set[str], Set[str]]:
    """(objects, functions) referenced as ``<catalog>.<schema>.<name>`` (backticks allowed)."""
    objs, fns = set(), set()
    for m in _REF_RE.finditer(_strip_sql_noise(sql)):
        if m.group(1).lower() != catalog.lower():
            continue
        fqn = f"{m.group(1)}.{m.group(2)}.{m.group(3)}".lower()
        (fns if m.group(4) else objs).add(fqn)
    return objs, fns


# ---- shared settings ---------------------------------------------------------------------------------
def load_shared(path: Path = SHARED_PATH) -> Dict[str, Any]:
    d = _yaml_load(path) or {}
    for k in ("catalog", "warehouse_id", "instructions", "limits", "shared_functions"):
        if k not in d:
            raise GenieSpecError(f"{path}: missing '{k}'")
    ins = d["instructions"]
    if len(ins.get("base") or []) != 10:
        raise GenieSpecError(f"{path}: instructions.base must hold the 10 lines of the brief §8 base block")
    for k in ("as_of", "response"):
        if not one_line(ins.get(k)):
            raise GenieSpecError(f"{path}: instructions.{k} is empty")
    d["shared_functions"] = [str(f).lower() for f in d["shared_functions"]]
    return d


# ---- functions.sql -----------------------------------------------------------------------------------
@dataclass
class FunctionDef:
    identifier: str            # fully-qualified, lower case
    sql: str                   # the CREATE statement (placeholders rendered)
    has_comment: bool
    args: List[Tuple[str, str]] = field(default_factory=list)   # (name, TYPE) of the parameters


# Genie rejects these SQL-function argument types at question time ("certified answer argument type is not
# supported: date", verified 2026-10-04) - and one bad function breaks every question of the space.
FUNCTION_ARG_TYPES_REJECTED = {"DATE", "TIMESTAMP", "TIMESTAMP_NTZ", "INTERVAL", "ARRAY", "MAP", "STRUCT", "BINARY", "VARIANT"}
FUNCTION_ARG_TYPES_VERIFIED = {"STRING"}


@dataclass
class FunctionsFile:
    functions: List[FunctionDef]
    tests: List[str]           # `-- test: SELECT ...` probes, each must return >= 1 row after deploy


_CREATE_FN_RE = re.compile(r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+([`\w.]+)\s*\(", re.I | re.M)
_TEST_RE = re.compile(r"^--\s*test\s*:\s*(.+)$", re.I | re.M)


def _skip_parens(s: str, i: int) -> int:
    """Index just after the balanced (...) group that starts at s[i] == '(' (quotes respected)."""
    depth, n, quote = 0, len(s), None
    while i < n:
        ch = s[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise GenieSpecError("unbalanced parentheses in function definition")


def function_args(stmt: str) -> List[Tuple[str, str]]:
    """(name, TYPE) of every parameter of a CREATE FUNCTION statement."""
    m = _CREATE_FN_RE.search(stmt)
    if not m:
        return []
    start = m.end() - 1
    inner = stmt[start + 1:_skip_parens(stmt, start) - 1]
    parts, depth, buf, quote = [], 0, [], None
    for ch in inner:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch in "(<":
            depth += 1
        elif ch in ")>":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if "".join(buf).strip():
        parts.append("".join(buf))
    out = []
    for part in parts:
        toks = part.split()
        if len(toks) >= 2:
            out.append((toks[0].strip("`"), re.split(r"[(<]", toks[1])[0].upper()))
    return out


def _mask_strings(sql: str) -> str:
    """Same-length copy of `sql` with the contents of '...' / "..." literals blanked, so keyword searches
    can't match words inside comments or values (e.g. a COMMENT mentioning 'return' or 'now(')."""
    out, i, n = list(sql), 0, len(sql)
    while i < n:
        if sql[i] in ("'", '"'):
            q, j = sql[i], i + 1
            while j < n and sql[j] != q:
                step = 2 if sql[j] == "\\" else 1
                for k in range(j, min(j + step, n)):
                    out[k] = " "
                j += step
            i = j + 1
            continue
        i += 1
    return "".join(out)


def _function_has_comment(stmt: str) -> bool:
    """True when the function itself (not only its parameters / columns) carries a COMMENT clause:
    CREATE FUNCTION f(<params>) RETURNS [TABLE (<cols>)|<type>] ... COMMENT '...' ... RETURN ..."""
    m = _CREATE_FN_RE.search(stmt)
    if not m:
        return False
    i = _skip_parens(stmt, m.end() - 1)
    rest = stmt[i:]
    rm = re.search(r"\bRETURNS\s+TABLE\s*\(", rest, re.I)
    if rm:
        rest = rest[_skip_parens(rest, rm.end() - 1):]
    ret = re.search(r"\bRETURN\b", _mask_strings(rest), re.I)   # the body keyword, not a word in a COMMENT
    head = rest[:ret.start()] if ret else rest
    return re.search(r"\bCOMMENT\s+'(?:[^'\\]|\\.)+'", head, re.I) is not None


def parse_functions_sql(text: str, params: Mapping[str, Any]) -> FunctionsFile:
    from .sql_runner import split_statements

    rendered = render(text, params)
    fns: List[FunctionDef] = []
    for stmt in split_statements(rendered):
        body = "\n".join(l for l in stmt.splitlines() if not l.lstrip().startswith("--")).strip()
        m = _CREATE_FN_RE.search(body)
        if not m:
            raise GenieSpecError(f"functions.sql: only CREATE OR REPLACE FUNCTION statements allowed: {body[:100]!r}")
        if _FORBIDDEN_FN_RE.search(_strip_sql_noise(body)):   # code only: prose in COMMENTs may say "now ("
            raise GenieSpecError(f"functions.sql: {m.group(1)} uses CURRENT_DATE / now() - use DATE'2026-09-30' (D16)")
        ident = m.group(1).replace("`", "").lower()
        fns.append(FunctionDef(identifier=ident, sql=body, has_comment=_function_has_comment(body),
                               args=function_args(body)))
    tests = [t.strip().rstrip(";") for t in _TEST_RE.findall(rendered)]
    return FunctionsFile(functions=fns, tests=tests)


# ---- space source ------------------------------------------------------------------------------------
@dataclass
class SpaceSource:
    slug: str
    raw: Dict[str, Any]
    path: Path
    functions_text: str = ""


def load_space(slug: str, genie_dir: Path = GENIE_DIR) -> SpaceSource:
    path = Path(genie_dir) / slug / "space.yaml"
    if not path.exists():
        raise GenieSpecError(f"{path} not found")
    raw = _yaml_load(path) or {}
    if not isinstance(raw, dict):
        raise GenieSpecError(f"{path}: top level must be a mapping")
    fpath = Path(genie_dir) / slug / "functions.sql"
    return SpaceSource(slug=slug, raw=raw, path=path, functions_text=fpath.read_text() if fpath.exists() else "")


def space_slugs(genie_dir: Path = GENIE_DIR) -> List[str]:
    return sorted(p.parent.name for p in Path(genie_dir).glob("*/space.yaml"))


# ---- schema snapshot (genie/_schema/<fqn>.json, written by the metadata gate) ---------------------------
def schema_digest(describe_json: Mapping[str, Any], identifier: str) -> Dict[str, Any]:
    """Compact, deterministic digest of DESCRIBE TABLE EXTENDED ... AS JSON (what the builder needs)."""
    cols = []
    for c in describe_json.get("columns", []):
        t = c.get("type")
        tname = (t.get("name") if isinstance(t, dict) else t) or ""
        cols.append({"name": c.get("name"), "type": str(tname).lower(), "is_measure": bool(c.get("is_measure")),
                     "comment": one_line(c.get("comment"))})
    return {"identifier": identifier.lower(), "type": describe_json.get("type"),
            "comment": one_line(describe_json.get("comment")), "columns": cols}


def schema_path(identifier: str, schema_dir: Path = SCHEMA_DIR) -> Path:
    return Path(schema_dir) / f"{identifier.lower()}.json"


def load_schema(identifiers: Iterable[str], schema_dir: Path = SCHEMA_DIR) -> Dict[str, Dict[str, Any]]:
    out = {}
    for ident in identifiers:
        p = schema_path(ident, schema_dir)
        if p.exists():
            out[ident.lower()] = json.loads(p.read_text())
    return out


def metadata_gaps(digest: Mapping[str, Any]) -> List[str]:
    """Brief §8 hygiene gate: the asset and every column / measure need a non-empty comment."""
    gaps = []
    if not one_line(digest.get("comment")):
        gaps.append(f"{digest.get('identifier')}: (asset comment)")
    for c in digest.get("columns", []):
        if not one_line(c.get("comment")):
            gaps.append(f"{digest.get('identifier')}.{c.get('name')}: {'measure' if c.get('is_measure') else 'column'} comment")
    return gaps


# ---- answers ---------------------------------------------------------------------------------------
def load_answers(slug: str, answers_dir: Path = ANSWERS_DIR) -> Dict[str, Any]:
    """qid -> metrics.Answer from metrics/_answers/<slug>.sql (empty if the file is missing)."""
    from .metrics import parse_answers

    p = Path(answers_dir) / f"{slug}.sql"
    if not p.exists():
        return {}
    return {a.qid: a for a in parse_answers(p.read_text())}


# ---- build -----------------------------------------------------------------------------------------
@dataclass
class BuiltSpace:
    slug: str
    title: str
    description: str
    warehouse_id: str
    serialized: Dict[str, Any]
    instruction_lines: List[str]
    benchmarks: List[Dict[str, Any]]
    functions: List[FunctionDef]
    function_tests: List[str]
    assets: List[str]
    entity_matching: List[str]
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def body(self) -> Dict[str, Any]:
        """Create / update request body (serialized_space as a JSON string, as the API wants)."""
        return {"title": self.title, "description": self.description, "warehouse_id": self.warehouse_id,
                "serialized_space": json.dumps(self.serialized)}

    def geniespace_json(self) -> Dict[str, Any]:
        """``<slug>.geniespace.json``: the same body with serialized_space as an object (readable, diffable)."""
        return {"title": self.title, "description": self.description, "warehouse_id": self.warehouse_id,
                "serialized_space": self.serialized}

    def instructions_md(self) -> str:
        return (f"# {self.title} - general instructions\n\n"
                f"Generated from genie/{self.slug}/space.yaml + genie/_shared.yaml ({len(self.instruction_lines)} lines; "
                f"the space's single text instruction). Do not edit.\n\n" + "\n".join(self.instruction_lines) + "\n")

    def space_spec(self) -> Dict[str, Any]:
        """Brief Appendix B shape (a readable summary of the space)."""
        ins = self.serialized["instructions"]
        return {
            "slug": self.slug,
            "title": self.title,
            "description": self.description,
            "warehouse_id": self.warehouse_id,
            "assets": self.assets,
            "entity_matching_columns": self.entity_matching,
            "general_instructions": "\n".join(self.instruction_lines),
            "sample_questions": [q["question"][0] for q in self.serialized["config"]["sample_questions"]],
            "trusted_queries": [{
                "name": e["question"][0],
                "usage_guidance": from_lines(e.get("usage_guidance") or []),
                "parameters": [{"name": p["name"], "type": p["type_hint"]} for p in e.get("parameters") or []],
                "sql": from_lines(e["sql"]),
            } for e in ins["example_question_sqls"]],
            "sql_functions": [f["identifier"] for f in ins["sql_functions"]],
            "benchmarks_file": "benchmarks.json",
            "counts": self.counts(),
        }

    def counts(self) -> Dict[str, int]:
        ins = self.serialized["instructions"]
        return {"assets": len(self.assets), "example_sqls": len(ins["example_question_sqls"]),
                "sql_functions": len(ins["sql_functions"]), "text_instructions": len(ins["text_instructions"]),
                "instruction_lines": len(self.instruction_lines),
                "instructions_total": len(ins["example_question_sqls"]) + len(ins["sql_functions"]) + len(ins["text_instructions"]),
                "sample_questions": len(self.serialized["config"]["sample_questions"]),
                "benchmarks": len(self.serialized["benchmarks"]["questions"]),
                "entity_matching_columns": len(self.entity_matching)}


def _range_msg(name: str, n: int, lim: Sequence[int], errors: List[str], warnings: List[str], hard_max: Optional[int] = None) -> None:
    lo, hi = int(lim[0]), int(lim[1])
    if n > hi:
        errors.append(f"{name}: {n} > {hi} (brief standard)")
    elif n < lo:
        warnings.append(f"{name}: {n} < {lo} (brief standard)")
    if hard_max is not None and n > hard_max:
        errors.append(f"{name}: {n} > API limit {hard_max}")


def _column_configs(ident: str, src: Mapping[str, Any], digest: Optional[Mapping[str, Any]], where: str,
                    limits: Mapping[str, Any], errors: List[str], warnings: List[str]) -> Tuple[List[Dict[str, Any]], List[str]]:
    cols_cfg = src.get("columns") or {}
    include = src.get("include_columns")
    if not isinstance(cols_cfg, dict):
        errors.append(f"{where}.columns must be a mapping of column name -> config")
        cols_cfg = {}
    known: Optional[Dict[str, str]] = None
    if digest is not None:
        known = {c["name"]: c["type"] for c in digest.get("columns", [])}
    else:
        warnings.append(f"{where}: no schema snapshot for {ident} (run the gate); only the listed column configs are emitted")
    for name, cfg in cols_cfg.items():
        if known is not None and name not in known:
            near = [k for k in known if k.lower() == str(name).lower()]
            errors.append(f"{where}.columns: '{name}' is not a column of {ident}" + (f" (did you mean '{near[0]}'?)" if near else ""))
        if not isinstance(cfg, dict):
            errors.append(f"{where}.columns.{name}: must be a mapping")
            continue
        errors.extend(_unknown(f"{where}.columns.{name}", cfg, COLUMN_KEYS))
        syn = cfg.get("synonyms") or []
        if len(syn) > int(limits.get("synonyms_per_column_max", 10)):
            errors.append(f"{where}.columns.{name}: {len(syn)} synonyms > {limits.get('synonyms_per_column_max', 10)}")
    if include is not None:
        if known is None:
            errors.append(f"{where}.include_columns needs the schema snapshot of {ident}")
        else:
            for name in include:
                if name not in known:
                    errors.append(f"{where}.include_columns: '{name}' is not a column of {ident}")
            for name in cols_cfg:
                if name not in include and not (cols_cfg[name] or {}).get("exclude"):
                    errors.append(f"{where}.columns.{name}: configured but not in include_columns")
    names = list(known) if known is not None else list(cols_cfg)
    out, em = [], []
    for name in names:
        cfg = cols_cfg.get(name) or {}
        ctype = (known or {}).get(name, "")
        exclude = bool(cfg.get("exclude")) or (include is not None and name not in include)
        entry: Dict[str, Any] = {"column_name": name}
        if exclude:
            entry["exclude"] = True
            if cfg.get("entity_matching"):
                errors.append(f"{where}.columns.{name}: entity_matching on an excluded column")
        else:
            if one_line(cfg.get("description")):
                entry["description"] = [one_line(cfg["description"])]
            if cfg.get("synonyms"):
                entry["synonyms"] = [str(s) for s in cfg["synonyms"]]
            matching = bool(cfg.get("entity_matching"))
            if matching and known is not None and ctype != "string":
                errors.append(f"{where}.columns.{name}: entity_matching needs a STRING column (is {ctype})")
            fa = cfg.get("format_assistance")
            if fa is None:
                fa = ctype == "string"
            if matching:
                fa = True
            if fa:
                entry["enable_format_assistance"] = True
            if matching:
                entry["enable_entity_matching"] = True
                em.append(f"{ident}.{name}")
        if len(entry) > 1:
            out.append(entry)
    return out, em


def _checks(raw: Any, where: str, errors: List[str]) -> Dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        errors.append(f"{where}.checks must be a mapping")
        return {}
    errors.extend(_unknown(f"{where}.checks", raw, CHECK_KEYS))
    out: Dict[str, Any] = {}
    for k in ("rows", "min_rows", "max_rows"):
        if k in raw:
            out[k] = int(raw[k])
    if "must_contain_columns" in raw:
        mc = raw["must_contain_columns"]
        out["must_contain_columns"] = [mc] if isinstance(mc, str) else [str(c) for c in mc]
    if "top_row_contains" in raw:
        tr = raw["top_row_contains"]
        out["top_row_contains"] = [tr] if isinstance(tr, str) else [str(c) for c in tr]
    return out


def build_space(src: SpaceSource, shared: Mapping[str, Any], schema: Mapping[str, Mapping[str, Any]],
                answers: Mapping[str, Any], spaces: Mapping[str, Tuple[int, str]], *,
                catalog: Optional[str] = None, warehouse_id: Optional[str] = None) -> BuiltSpace:
    """Validate a space source and render its serialized_space v2 (+ benchmark catalogue)."""
    raw, slug = src.raw, src.slug
    catalog = catalog or shared["catalog"]
    params = {"catalog": catalog, "as_of_date": shared.get("as_of_date", "2026-09-30")}
    limits = shared["limits"]
    errors: List[str] = _unknown("space.yaml", raw, SPACE_KEYS)
    warnings: List[str] = []

    def r(text: Any) -> str:
        try:
            return render(str(text), params)
        except GenieSpecError as e:
            errors.append(str(e))
            return str(text)

    if raw.get("slug") != slug:
        errors.append(f"slug '{raw.get('slug')}' != folder '{slug}'")
    title = one_line(raw.get("title"))
    want = spaces.get(slug, (None, None))[1]
    if want is None:
        errors.append(f"unknown space slug '{slug}' (brief Appendix D)")
    elif title != want:
        errors.append(f"title '{title}' != '{want}' (metrics.SPACES)")
    description = one_line(raw.get("description"))
    if not description:
        errors.append("description is empty")

    # -- data sources
    assets, tables, em_cols = [], [], []
    for i, ds in enumerate(raw.get("data_sources") or []):
        where = f"data_sources[{i}]"
        if not isinstance(ds, dict) or not ds.get("identifier"):
            errors.append(f"{where}: needs an identifier")
            continue
        errors.extend(_unknown(where, ds, SOURCE_KEYS))
        ident = r(ds["identifier"]).replace("`", "").lower()
        if not _FQN_RE.match(ident):
            errors.append(f"{where}: '{ident}' is not catalog.schema.name")
        elif ident.split(".")[1] not in ("gold", "metrics"):
            errors.append(f"{where}: {ident} - only gold and metrics objects are allowed (brief §3.4)")
        if ident in assets:
            errors.append(f"{where}: duplicate asset {ident}")
            continue
        assets.append(ident)
        cfgs, em = _column_configs(ident, ds, schema.get(ident), where, limits, errors, warnings)
        em_cols += em
        tables.append({"identifier": ident, "column_configs": cfgs})
    if not assets:
        errors.append("data_sources is empty")
    if len(assets) > int(limits["assets_max"]):
        errors.append(f"assets: {len(assets)} > {limits['assets_max']} (brief §3.4)")
    if len(em_cols) > int(limits["api_entity_matching_columns_max"]):
        errors.append(f"entity matching on {len(em_cols)} columns > {limits['api_entity_matching_columns_max']}")
    asset_set = set(assets)

    # -- functions
    listed = [r(f).replace("`", "").lower() for f in (raw.get("sql_functions") or [])]
    try:
        ff = parse_functions_sql(src.functions_text, params) if src.functions_text.strip() else FunctionsFile([], [])
    except GenieSpecError as e:
        errors.append(str(e))
        ff = FunctionsFile([], [])
    defined = {f.identifier for f in ff.functions}
    shared_fns = set(shared.get("shared_functions") or [])
    for f in ff.functions:
        if not re.match(rf"^{re.escape(catalog.lower())}\.gold\.fn_[a-z0-9_]+$", f.identifier):
            errors.append(f"functions.sql: {f.identifier} must be {catalog}.gold.fn_<name>")
        if not f.has_comment:
            errors.append(f"functions.sql: {f.identifier} has no function-level COMMENT (Genie cannot see the body)")
        if f.identifier in shared_fns:
            errors.append(f"functions.sql: {f.identifier} is a shared helper - never redefine it")
        if f.identifier not in listed:
            errors.append(f"functions.sql: {f.identifier} is defined but not listed under sql_functions")
        for aname, atype in f.args:
            if atype in FUNCTION_ARG_TYPES_REJECTED:
                errors.append(f"functions.sql: {f.identifier}({aname} {atype}) - Genie rejects {atype} function "
                              f"arguments at question time; use STRING and cast inside")
            elif atype not in FUNCTION_ARG_TYPES_VERIFIED:
                warnings.append(f"functions.sql: {f.identifier}({aname} {atype}) - only STRING arguments are verified with Genie")
    for f in listed:
        if f not in defined and f not in shared_fns:
            errors.append(f"sql_functions: {f} is neither defined in functions.sql nor a shared helper")
    if len(set(listed)) != len(listed):
        errors.append("sql_functions: duplicates")
    _range_msg("space SQL functions", len(defined), limits["space_functions"], errors, warnings)
    fn_set = set(listed)

    def check_sql(sql: str, where: str) -> None:
        if _FORBIDDEN_FN_RE.search(_strip_sql_noise(sql)):
            errors.append(f"{where}: CURRENT_DATE / now() - use DATE'2026-09-30' (D16)")
        objs, fns = sql_references(sql, catalog)
        for o in sorted(objs - asset_set):
            errors.append(f"{where}: references {o}, which is not an asset of this space")
        for f in sorted(fns - fn_set):
            errors.append(f"{where}: calls {f}, which is not listed under sql_functions")

    # -- instructions
    ins = raw.get("instructions") or {}
    if not isinstance(ins, dict):
        errors.append("instructions must be a mapping with 'domain' (and optional 'response')")
        ins = {}
    errors.extend(_unknown("instructions", ins, INSTRUCTION_KEYS))
    domain = [one_line(x) for x in (ins.get("domain") or [])]
    if len(domain) > int(limits["domain_lines_max"]):
        errors.append(f"instructions.domain: {len(domain)} lines > {limits['domain_lines_max']}")
    for j, line in enumerate(domain):
        if not line.startswith("- "):
            errors.append(f"instructions.domain[{j}] must start with '- '")
        if _FORBIDDEN_FN_RE.search(line) and "never" not in line.lower():
            errors.append(f"instructions.domain[{j}]: mentions CURRENT_DATE / now()")
    sh = shared["instructions"]
    lines = [one_line(x) for x in sh["base"]] + [one_line(sh["as_of"])] + domain + \
            [one_line(ins.get("response") or sh["response"])]
    lines = [r(x) for x in lines]
    if len(lines) > int(limits["instruction_lines_max"]):
        errors.append(f"instructions: {len(lines)} lines > {limits['instruction_lines_max']}")
    text_instructions = [{"id": ordered_id(slug, "text_instruction", 0), "content": to_lines("\n".join(lines))}]

    # -- example SQL
    hints = set(limits["param_type_hints"])
    examples = []
    for i, ex in enumerate(raw.get("example_sqls") or []):
        where = f"example_sqls[{i}]"
        if not isinstance(ex, dict):
            errors.append(f"{where}: must be a mapping")
            continue
        errors.extend(_unknown(where, ex, EXAMPLE_KEYS))
        q, sql = one_line(ex.get("question")), r(ex.get("sql") or "").strip()
        if not q or not sql:
            errors.append(f"{where}: needs question and sql")
            continue
        if not one_line(ex.get("usage_guidance")):
            errors.append(f"{where}: needs usage_guidance")
        check_sql(sql, where)
        declared = []
        params_out = []
        for p in ex.get("parameters") or []:
            errors.extend(_unknown(f"{where}.parameters", p, PARAM_KEYS))
            name, th = str(p.get("name") or ""), str(p.get("type") or "STRING").upper()
            if not re.match(r"^[a-z_][a-z0-9_]*$", name):
                errors.append(f"{where}: parameter name '{name}' must be snake_case")
            if th not in hints:
                errors.append(f"{where}.{name}: type '{th}' not accepted by the API (use {', '.join(sorted(hints))})")
            declared.append(name)
            po: Dict[str, Any] = {"name": name, "type_hint": th}
            if one_line(p.get("description")):
                po["description"] = [one_line(p["description"])]
            else:
                errors.append(f"{where}.{name}: parameter needs a description")
            if p.get("default") is not None:
                dv = p["default"]
                po["default_value"] = {"values": [str(dv).lower() if isinstance(dv, bool) else str(dv)]}
            params_out.append(po)
        used = sql_parameters(sql)
        for name in sorted(used - set(declared)):
            errors.append(f"{where}: :{name} used in the SQL but not declared")
        for name in sorted(set(declared) - used):
            errors.append(f"{where}: parameter '{name}' declared but not used in the SQL")
        if len(set(declared)) != len(declared):
            errors.append(f"{where}: duplicate parameter names")
        item: Dict[str, Any] = {"id": ordered_id(slug, "example_sql", i), "question": [q], "sql": to_lines(sql)}
        if params_out:
            item["parameters"] = params_out
        if one_line(ex.get("usage_guidance")):
            item["usage_guidance"] = [one_line(ex["usage_guidance"])]
        examples.append(item)
    _range_msg("example SQLs", len(examples), limits["example_sqls"], errors, warnings)
    n_instr = len(examples) + len(listed) + 1
    if n_instr > int(limits["api_instructions_max"]):
        errors.append(f"instructions: {n_instr} > API limit {limits['api_instructions_max']}")

    # -- sample questions
    sqs = []
    for i, q in enumerate(raw.get("sample_questions") or []):
        if not one_line(q):
            errors.append(f"sample_questions[{i}] is empty")
            continue
        sqs.append({"id": ordered_id(slug, "sample_question", i), "question": [one_line(q)]})
    _range_msg("sample questions", len(sqs), limits["sample_questions"], errors, warnings)

    # -- benchmarks
    bm_raw = raw.get("benchmarks") or []
    by_key: Dict[str, Dict[str, Any]] = {}
    for i, b in enumerate(bm_raw):
        if isinstance(b, dict) and b.get("key"):
            if str(b["key"]) in by_key:
                errors.append(f"benchmarks: duplicate key '{b['key']}'")
            by_key[str(b["key"])] = b
    catalogue, questions = [], []
    seen_q: Set[str] = set()
    for i, b in enumerate(bm_raw):
        where = f"benchmarks[{i}]"
        if not isinstance(b, dict):
            errors.append(f"{where}: must be a mapping")
            continue
        errors.extend(_unknown(where, b, BENCHMARK_KEYS))
        key, q = str(b.get("key") or ""), one_line(b.get("question"))
        if not _KEY_RE.match(key):
            errors.append(f"{where}: key '{key}' must match {_KEY_RE.pattern}")
        if not q:
            errors.append(f"{where}: empty question")
            continue
        if q.lower() in seen_q:
            errors.append(f"{where}: duplicate question text")
        seen_q.add(q.lower())
        base = by_key.get(str(b.get("variant_of"))) if b.get("variant_of") else None
        if b.get("variant_of") and base is None:
            errors.append(f"{where}: variant_of '{b.get('variant_of')}' is not a benchmark key")
        if base is not None and base.get("variant_of"):
            errors.append(f"{where}: variant_of must point at a base benchmark, not at another variant")
        answer = b.get("answer") if "answer" in b or "expected_sql" in b else (base or {}).get("answer")
        sql = b.get("expected_sql") if "answer" in b or "expected_sql" in b else (base or {}).get("expected_sql")
        if b.get("answer") and b.get("expected_sql"):
            errors.append(f"{where}: give either answer or expected_sql, not both")
        rows_default = None
        if answer:
            a = answers.get(str(answer))
            if a is None:
                errors.append(f"{where}: answer '{answer}' not found in metrics/_answers/{slug}.sql")
                continue
            sql, rows_default = a.sql, a.rows
        sql = r(sql or "").strip().rstrip(";").strip()
        if not sql:
            errors.append(f"{where}: needs answer or expected_sql (directly or via variant_of)")
            continue
        check_sql(sql, where)
        checks = _checks(b["checks"] if "checks" in b else (base or {}).get("checks"), where, errors)
        if not checks:
            checks = {"rows": rows_default} if rows_default is not None else {"min_rows": 1}
        bid = ordered_id(slug, "benchmark", i)
        questions.append({"id": bid, "question": [q], "answer": [{"format": "SQL", "content": to_lines(sql)}]})
        catalogue.append({
            "id": bid, "key": key, "question": q, "expected_sql": sql, "expected_checks": checks,
            "answer_ref": str(answer) if answer else None,
            "variant_of": str(b["variant_of"]) if b.get("variant_of") else None,
            "allow_extra_columns": bool(b.get("allow_extra_columns", (base or {}).get("allow_extra_columns", False))),
        })
    ids_by_key = {c["key"]: c["id"] for c in catalogue}
    for c in catalogue:
        c["variant_of_id"] = ids_by_key.get(c["variant_of"]) if c["variant_of"] else None
    _range_msg("benchmarks", len(catalogue), limits["benchmarks"], errors, warnings, int(limits["api_benchmarks_max"]))

    ss = {
        "version": 2,
        "config": {"sample_questions": sqs},
        "data_sources": {"tables": tables},
        "instructions": {"text_instructions": text_instructions, "example_question_sqls": examples,
                         "sql_functions": [{"id": ordered_id(slug, "sql_function", i), "identifier": f}
                                           for i, f in enumerate(listed)]},
        "benchmarks": {"questions": questions},
    }
    sort_serialized_space(ss)
    for section, name in _ID_LISTS:
        ids = [x["id"] for x in ss.get(section, {}).get(name, []) or []]
        if len(set(ids)) != len(ids) or any(not re.match(r"^[0-9a-f]{32}$", x) for x in ids):
            errors.append(f"{section}.{name}: ids must be unique 32-hex")
    return BuiltSpace(slug=slug, title=title, description=description,
                      warehouse_id=str(warehouse_id or shared["warehouse_id"]), serialized=ss,
                      instruction_lines=lines, benchmarks=catalogue, functions=ff.functions,
                      function_tests=ff.tests, assets=assets, entity_matching=em_cols,
                      errors=errors, warnings=warnings)


# ---- round-trip diff -----------------------------------------------------------------------------------
def diff_json(sent: Any, got: Any, path: str = "") -> List[str]:
    """Paths where two JSON values differ (dict key order ignored; list order significant)."""
    out: List[str] = []
    if isinstance(sent, dict) and isinstance(got, dict):
        for k in sorted(set(sent) | set(got)):
            p = f"{path}.{k}" if path else str(k)
            if k not in got:
                out.append(f"{p}: missing in GET (sent {json.dumps(sent[k])[:80]})")
            elif k not in sent:
                out.append(f"{p}: added by server ({json.dumps(got[k])[:80]})")
            else:
                out += diff_json(sent[k], got[k], p)
    elif isinstance(sent, list) and isinstance(got, list):
        if len(sent) != len(got):
            out.append(f"{path}: {len(sent)} items sent, {len(got)} returned")
        for i, (a, b) in enumerate(zip(sent, got)):
            out += diff_json(a, b, f"{path}[{i}]")
    elif sent != got:
        out.append(f"{path}: sent {json.dumps(sent)[:80]} != got {json.dumps(got)[:80]}")
    return out


# ---- benchmark checks and result comparison ------------------------------------------------------------
_NUM_RE = re.compile(r"^[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?$")
_TS_ZERO_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ]00:00(:00(\.0+)?)?(Z|[+-]00:?00)?$")
SIG_DIGITS = 4


def normalize_value(v: Any, sig: int = SIG_DIGITS) -> Tuple[int, Any]:
    """Comparable, hashable form: (kind, value). Numbers to ``sig`` significant digits, booleans as
    true/false, midnight timestamps as dates, strings stripped; NULL sorts first."""
    if v is None:
        return (0, "")
    if isinstance(v, bool):
        return (1, "true" if v else "false")
    if isinstance(v, (int, float)):
        x = float(v)
    else:
        s = str(v).strip()
        if s.lower() in ("true", "false"):
            return (1, s.lower())
        if not _NUM_RE.match(s):
            m = _TS_ZERO_RE.match(s)
            return (3, m.group(1) if m else s)
        x = float(s)
    if x != x:  # NaN
        return (4, "nan")
    if x == 0:
        return (2, 0.0)
    return (2, float(f"{x:.{sig - 1}e}"))


def check_result(columns: Sequence[str], rows: Sequence[Sequence[Any]], checks: Mapping[str, Any]) -> List[str]:
    """Benchmark checks on the expected SQL's result (brief §8 expected_checks). Returns failures."""
    fails = []
    n = len(rows)
    if "rows" in checks and n != int(checks["rows"]):
        fails.append(f"rows {n} != {checks['rows']}")
    if "min_rows" in checks and n < int(checks["min_rows"]):
        fails.append(f"rows {n} < min_rows {checks['min_rows']}")
    if "max_rows" in checks and n > int(checks["max_rows"]):
        fails.append(f"rows {n} > max_rows {checks['max_rows']}")
    low = [c.lower() for c in columns]
    for c in checks.get("must_contain_columns") or []:
        if c.lower() not in low:
            fails.append(f"column '{c}' missing (have {', '.join(columns)})")
    tops = checks.get("top_row_contains") or []
    if tops:
        top = " | ".join("" if v is None else str(v) for v in rows[0]).lower() if rows else ""
        for t in tops:
            if t.lower() not in top:
                fails.append(f"top row lacks '{t}'")
    return fails


def compare_results(exp_cols: Sequence[str], exp_rows: Sequence[Sequence[Any]],
                    act_cols: Sequence[str], act_rows: Sequence[Sequence[Any]], *,
                    allow_extra_columns: bool = False, sig: int = SIG_DIGITS) -> Tuple[bool, str]:
    """Our grading rule (README "Comparison rule"): the same multiset of rows (order-insensitive), values
    to ``sig`` significant digits, columns matched by their values (names and column order ignored); extra
    rows always fail; extra columns fail unless ``allow_extra_columns`` (ambiguous questions only)."""
    k, m = len(exp_cols), len(act_cols)
    if len(act_rows) != len(exp_rows):
        return False, f"row count {len(act_rows)} != expected {len(exp_rows)}"
    if m < k:
        return False, f"{m} columns < expected {k}"
    if m > k and not allow_extra_columns:
        return False, f"{m - k} extra column(s) ({m} vs {k}); not allowed for this question"
    E = [[normalize_value(r[j], sig) for r in exp_rows] for j in range(k)]
    A = [[normalize_value(r[j], sig) for r in act_rows] for j in range(m)]
    sortedA = [sorted(col) for col in A]
    cands = []
    for j in range(k):
        se = sorted(E[j])
        c = [a for a in range(m) if sortedA[a] == se]
        if not c:
            return False, f"no Genie column matches expected column '{exp_cols[j]}'"
        cands.append(c)
    target = Counter(tuple(E[j][i] for j in range(k)) for i in range(len(exp_rows)))
    order = sorted(range(k), key=lambda j: len(cands[j]))
    chosen: Dict[int, int] = {}

    def search(t: int) -> bool:
        if t == k:
            got = Counter(tuple(A[chosen[j]][i] for j in range(k)) for i in range(len(act_rows)))
            return got == target
        j = order[t]
        for a in cands[j]:
            if a in chosen.values():
                continue
            chosen[j] = a
            if search(t + 1):
                return True
            del chosen[j]
        return False

    if search(0):
        extra = m - k
        return True, "match" + (f" (+{extra} extra column(s) allowed)" if extra else "")
    return False, "columns match individually but the rows differ"


def typed_result(ser: Mapping[str, Any]) -> Optional[Tuple[List[str], List[List[Any]]]]:
    """(columns, rows) from an eval result's sql_execution_result when it is complete, else None.
    Genie answers with a trusted example SQL keep their :parameters in the text (Genie binds them itself), so
    this inline result is the only way to grade them; partial (chunked / truncated) results return None."""
    man, res = ser.get("manifest") or {}, ser.get("result") or {}
    if (ser.get("status") or {}).get("state") != "SUCCEEDED" or man.get("truncated"):
        return None
    cols = [c.get("name") for c in (man.get("schema") or {}).get("columns") or []]
    rows = []
    for r in res.get("data_typed_array") or []:
        vals = []
        for v in r.get("values") or []:
            if not isinstance(v, dict) or not v or v.get("null_value") is not None:
                vals.append(None)
            elif "str" in v:
                vals.append(v["str"])
            else:
                vals.append(next(iter(v.values())))
        rows.append(vals)
    if int(man.get("total_row_count") or 0) != len(rows):
        return None
    return cols, rows
