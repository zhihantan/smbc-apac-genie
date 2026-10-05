"""Gold framework (Phase 5; PLAN §7; DECISIONS D11, D16, D25, D29, D31, D38): spec -> DDL -> load -> verify.

Every gold object is one YAML spec, ``src/30_gold/specs/<name>.yaml``, plus one SQL body,
``src/30_gold/sql/<name>.sql`` (a single SELECT / WITH query that yields the spec's columns by name,
in any order). ``src/30_gold/run_gold.py`` renders, in dependency order:

  CREATE OR REPLACE TABLE <t> (<col> <TYPE> [NOT NULL] COMMENT '...', ...,
      CONSTRAINT pk_<t> PRIMARY KEY (...) RELY,
      CONSTRAINT fk_<t>_<cols> FOREIGN KEY (...) REFERENCES <ref> (...) NOT ENFORCED RELY)
    CLUSTER BY (...) COMMENT '...'
  INSERT OVERWRITE <t> SELECT <spec columns> FROM (<body>) AS __src
  ANALYZE TABLE <t> COMPUTE STATISTICS FOR ALL COLUMNS

then verifies row counts, PK uniqueness / not-null, FK resolution, SCD2 integrity, reconciliations
and custom checks. Spec keys (only ``table``, ``domain``, ``comment``, ``columns`` and, for tables,
``primary_key`` are required)::

  table: dim_client                  # = file stem
  kind: table                        # table (default) | view | function (body = full CREATE FUNCTION)
  domain: hub                        # --domain filter and the smbc_domain tag
  comment: Golden client ...         # table comment; `grain:` is appended as "Grain: ..."
  grain: one row per golden client version
  primary_key: [golden_client_sk]
  foreign_keys:                      # ref columns default to the referenced spec's primary key
    - {columns: [client_group_id], references: dim_client_group}
    - {columns: [primary_rm_id], references: dim_employee(employee_id)}
  cluster_by: [golden_client_id]     # <= 4 columns, or AUTO
  depends_on: [dim_date]             # extra build-order deps; ${catalog}.gold.<t> refs are detected
  scd2: {key: golden_client_id}      # adds the standard SCD2 integrity checks (see scd2_checks)
  min_rows: 1
  columns:                           # name: TYPE  |  name: {type, comment, not_null}
    golden_client_sk: {type: BIGINT, comment: Surrogate key of the version}
    golden_client_id: STRING         # comment from the dictionaries
  checks:                            # SQL returning one number of violations (must be <= max, default 0)
    - {name: no_orphans, sql: "SELECT count(*) FROM ${table} WHERE ..."}
  reconcile:                         # gold vs silver counts, logged; |diff| <= tolerance
    - {name: rows_vs_silver, silver: "SELECT count(*) FROM ${catalog}.silver.x", tolerance: 0}

Column comments resolve spec > ``src/30_gold/specs/_columns.yaml`` > ``config/column_dictionary.yaml``
(D25); a table or column left without a comment fails the build. SQL bodies use ``${catalog}``,
``${as_of_date}``, ``${history_start}``, ``${calendar_end}`` and the config thresholds (never
CURRENT_DATE, D16). SCD2 facts pick ``golden_client_sk`` with :func:`client_sk_join`.
Pure Python (yaml only), unit-tested in tests/unit/test_gold.py.
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - yaml is a hard dependency of the package
    yaml = None

REPO = Path(__file__).resolve().parents[2]
GOLD_DIR = REPO / "src" / "30_gold"
SPEC_DIR, SQL_DIR = GOLD_DIR / "specs", GOLD_DIR / "sql"
GOLD_DICTIONARY = SPEC_DIR / "_columns.yaml"
CONFIG_DICTIONARY = REPO / "config" / "column_dictionary.yaml"

KINDS = ("table", "view", "function")
OPEN_END = "9999-12-31"                 # valid_to of a current SCD2 version
CALENDAR_END = "2027-03-31"             # dim_date runs to the end of FY2026 (brief §4)
_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_TYPE_RE = re.compile(r"^(STRING|BIGINT|INT|SMALLINT|TINYINT|DOUBLE|FLOAT|BOOLEAN|DATE|TIMESTAMP|"
                      r"DECIMAL\(\d+,\s*\d+\)|ARRAY<.+>|MAP<.+>|STRUCT<.+>)$", re.I)
_REF_RE = re.compile(r"^\s*(?:(\w+)\.)?(\w+)\s*(?:\(([^)]*)\))?\s*$")      # [schema.]table[(c1, c2)]
_SQL_REF_RE = re.compile(r"\$\{catalog\}\.(\w+)\.(\w+)\b(?!\s*\()", re.I)    # tables / views
_SQL_FN_RE = re.compile(r"\$\{catalog\}\.(\w+)\.(\w+)\s*\(", re.I)             # function calls
_PLACEHOLDER_RE = re.compile(r"\$\{(\w+)\}")


class SpecError(ValueError):
    """A malformed spec, SQL body or dictionary."""


# ---- spec model -----------------------------------------------------------------------------------
@dataclass
class Column:
    name: str
    type: str
    comment: Optional[str] = None
    not_null: bool = False


@dataclass
class ForeignKey:
    columns: List[str]
    ref_table: str
    ref_columns: List[str] = field(default_factory=list)   # empty = the referenced spec's PK
    ref_schema: str = "gold"

    def name(self, table: str) -> str:
        return f"fk_{table}_{'_'.join(self.columns)}"


@dataclass
class TableSpec:
    name: str
    domain: str
    comment: str
    columns: List[Column]
    kind: str = "table"
    grain: Optional[str] = None
    primary_key: List[str] = field(default_factory=list)
    foreign_keys: List[ForeignKey] = field(default_factory=list)
    cluster_by: List[str] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)
    scd2: Optional[Dict[str, str]] = None
    checks: List[Dict[str, Any]] = field(default_factory=list)
    reconcile: List[Dict[str, Any]] = field(default_factory=list)
    min_rows: int = 1
    tags: Dict[str, str] = field(default_factory=dict)
    body: str = ""
    spec_path: Optional[Path] = None
    sql_path: Optional[Path] = None

    @property
    def column_names(self) -> List[str]:
        return [c.name for c in self.columns]

    def column(self, name: str) -> Column:
        for c in self.columns:
            if c.name == name:
                return c
        raise KeyError(name)

    @property
    def full_comment(self) -> str:
        if self.grain and "grain" not in self.comment.lower():
            return f"{self.comment.rstrip('. ')}. Grain: {self.grain.rstrip('.')}."
        return self.comment

    def fqn(self, catalog: str) -> str:
        return f"{catalog}.gold.{self.name}"


def _as_list(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [s.strip() for s in v.split(",") if s.strip()]
    return [str(s).strip() for s in v]


def parse_ref(ref: str) -> Tuple[str, str, List[str]]:
    """'dim_employee(employee_id)' -> ('gold', 'dim_employee', ['employee_id'])."""
    m = _REF_RE.match(ref or "")
    if not m:
        raise SpecError(f"bad FK reference {ref!r} (use table or table(col, ...))")
    return (m.group(1) or "gold"), m.group(2), _as_list(m.group(3))


COLUMN_KEYS = {"type", "comment", "not_null"}
SPEC_KEYS = {"table", "kind", "domain", "comment", "grain", "primary_key", "foreign_keys", "cluster_by",
             "depends_on", "scd2", "min_rows", "columns", "checks", "reconcile", "tags"}


def parse_columns(raw: Any, table: str) -> List[Column]:
    if not isinstance(raw, Mapping) or not raw:
        raise SpecError(f"{table}: `columns` must be a non-empty mapping name -> type | {{type, comment}}")
    cols = []
    for name, v in raw.items():
        name = str(name)
        if isinstance(v, str):
            v = {"type": v}
        if not isinstance(v, Mapping) or "type" not in v:
            raise SpecError(f"{table}.{name}: column needs a type")
        extra = set(map(str, v)) - COLUMN_KEYS
        if extra:   # typically an unquoted comment with a comma inside a {flow: mapping}
            raise SpecError(f"{table}.{name}: unexpected keys {sorted(extra)} - quote comments that contain commas")
        typ = str(v["type"]).strip()
        if not _TYPE_RE.match(typ):
            raise SpecError(f"{table}.{name}: unsupported type {typ!r}")
        typ = re.sub(r"\s+", "", typ.upper()) if typ.upper().startswith("DECIMAL") else typ.upper()
        comment = " ".join(str(v.get("comment") or "").split())
        cols.append(Column(name=name, type=typ, comment=comment or None,
                           not_null=bool(v.get("not_null", False))))
    return cols


def parse_spec(data: Mapping[str, Any], path: Optional[Path] = None) -> TableSpec:
    """Validate one spec mapping (structure only; cross-spec references are checked by validate_specs)."""
    if not isinstance(data, Mapping):
        raise SpecError(f"{path}: spec must be a mapping")
    unknown = set(map(str, data)) - SPEC_KEYS
    if unknown:
        raise SpecError(f"{path}: unknown spec keys {sorted(unknown)} (allowed: {sorted(SPEC_KEYS)})")
    name = str(data.get("table") or "")
    if not _NAME_RE.match(name):
        raise SpecError(f"{path}: `table` must be a lower-case identifier, got {name!r}")
    if path is not None and path.stem != name:
        raise SpecError(f"{path}: `table: {name}` must match the file name")
    kind = str(data.get("kind", "table"))
    if kind not in KINDS:
        raise SpecError(f"{name}: kind must be one of {KINDS}")
    for req in ("domain", "comment"):
        if not str(data.get(req) or "").strip():
            raise SpecError(f"{name}: `{req}` is required")
    cols = parse_columns(data.get("columns"), name) if kind != "function" or data.get("columns") else []
    names = [c.name for c in cols]
    bad = [n for n in names if not _NAME_RE.match(n)]
    if bad:
        raise SpecError(f"{name}: bad column names {bad}")
    if len(set(names)) != len(names):
        raise SpecError(f"{name}: duplicate column names")
    pk = _as_list(data.get("primary_key"))
    if kind == "table" and not pk:
        raise SpecError(f"{name}: tables need a primary_key")
    missing = [c for c in pk if c not in names]
    if missing:
        raise SpecError(f"{name}: primary key columns {missing} not in columns")
    for section, allowed in (("foreign_keys", {"columns", "references"}), ("checks", {"name", "sql", "max"}),
                             ("reconcile", {"name", "gold", "silver", "tolerance"})):
        for entry in data.get(section) or []:
            if not isinstance(entry, Mapping) or set(map(str, entry)) - allowed:
                raise SpecError(f"{name}: {section} entries take only {sorted(allowed)}, got {entry!r}")
    fks = []
    for fk in data.get("foreign_keys") or []:
        cols_ = _as_list(fk.get("columns"))
        schema, ref, ref_cols = parse_ref(str(fk.get("references", "")))
        if not cols_ or any(c not in names for c in cols_):
            raise SpecError(f"{name}: FK columns {cols_} must be spec columns")
        if ref_cols and len(ref_cols) != len(cols_):
            raise SpecError(f"{name}: FK {cols_} -> {ref}{ref_cols}: column counts differ")
        fks.append(ForeignKey(columns=cols_, ref_table=ref, ref_columns=ref_cols, ref_schema=schema))
    cluster = data.get("cluster_by") or []
    cluster = ["AUTO"] if str(cluster).upper() == "AUTO" else _as_list(cluster)
    if cluster != ["AUTO"]:
        if len(cluster) > 4 or any(c not in names for c in cluster):
            raise SpecError(f"{name}: cluster_by must be <= 4 spec columns (or AUTO)")
    scd2 = data.get("scd2")
    if scd2 is not None:
        scd2 = {"valid_from": "valid_from", "valid_to": "valid_to", "current": "is_current",
                "version": "version_no", "open_end": OPEN_END, **dict(scd2)}
        need = [scd2.get("key"), scd2["valid_from"], scd2["valid_to"], scd2["current"], scd2["version"]]
        if any(c not in names for c in need):
            raise SpecError(f"{name}: scd2 columns {need} must all be spec columns")
    checks = list(data.get("checks") or [])
    for chk in checks:
        if not chk.get("name") or not chk.get("sql"):
            raise SpecError(f"{name}: every check needs `name` and `sql`")
    rec = list(data.get("reconcile") or [])
    for r in rec:
        if not r.get("name") or not r.get("silver"):
            raise SpecError(f"{name}: every reconcile entry needs `name` and `silver`")
    for c in pk:   # PK columns are NOT NULL by construction
        next(x for x in cols if x.name == c).not_null = True
    return TableSpec(name=name, domain=str(data["domain"]).strip(), comment=" ".join(str(data["comment"]).split()),
                     columns=cols, kind=kind, grain=(" ".join(str(data["grain"]).split()) if data.get("grain") else None),
                     primary_key=pk, foreign_keys=fks, cluster_by=cluster,
                     depends_on=_as_list(data.get("depends_on")), scd2=scd2, checks=checks, reconcile=rec,
                     min_rows=int(data.get("min_rows", 1)), tags={str(k): str(v) for k, v in (data.get("tags") or {}).items()},
                     spec_path=path)


_DOUBLED_QUOTE_RE = re.compile(r"[A-Za-z]''[A-Za-z]")


def lint_body(sql: str, name: str = "?") -> None:
    """Reject `it''s`: Databricks SQL reads it as two adjacent literals ('it' 's' -> its); use it\\'s.
    Also reject CURRENT_DATE / now(): gold is pinned to the as-of date (D16)."""
    code = re.sub(r"--[^\n]*", "", sql)          # line comments may mention anything
    if _DOUBLED_QUOTE_RE.search(code):
        raise SpecError(f"{name}: doubled quote inside a literal ({_DOUBLED_QUOTE_RE.search(code).group(0)}); "
                        "escape it with a backslash")
    if re.search(r"\b(current_date|current_timestamp|now)\s*\(", code, re.I):
        raise SpecError(f"{name}: CURRENT_DATE / CURRENT_TIMESTAMP / now() - use ${{as_of_date}} (D16)")


def clean_body(sql: str, name: str = "?") -> str:
    """Strip trailing semicolons; the body must be one statement."""
    from .sql_runner import split_statements

    lint_body(sql, name)
    stmts = split_statements(sql)
    if len(stmts) != 1:
        raise SpecError(f"{name}: SQL body must hold exactly one statement, found {len(stmts)}")
    return stmts[0].rstrip().rstrip(";").rstrip()


def load_spec(path: Path, sql_dir: Path = SQL_DIR) -> TableSpec:
    """One specs/<name>.yaml with its sql/<name>.sql body."""
    if yaml is None:  # pragma: no cover
        raise RuntimeError("pyyaml is required")
    try:
        data = yaml.safe_load(Path(path).read_text())
    except yaml.YAMLError as e:
        raise SpecError(f"{path}: invalid YAML: {str(e).splitlines()[0]}") from e
    spec = parse_spec(data, Path(path))
    sql_path = Path(sql_dir) / f"{spec.name}.sql"
    if not sql_path.exists():
        raise SpecError(f"{spec.name}: missing SQL body {sql_path}")
    text = sql_path.read_text()
    if spec.kind == "function":
        lint_body(text, spec.name)
        spec.body = text.strip()
    else:
        spec.body = clean_body(text, spec.name)
    spec.sql_path = sql_path
    return spec


def load_specs(spec_dir: Path = SPEC_DIR, sql_dir: Path = SQL_DIR,
               errors: Optional[List[str]] = None) -> Dict[str, TableSpec]:
    """Every specs/<name>.yaml (files starting with '_' are dictionaries) with its SQL body. Raises on the
    first invalid spec, unless `errors` is given: then invalid specs are skipped and their errors collected
    (a spec whose FK / depends_on points at a skipped one is skipped too)."""
    specs: Dict[str, TableSpec] = {}
    for path in sorted(Path(spec_dir).glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        try:
            spec = load_spec(path, sql_dir)
        except SpecError as e:
            if errors is None:
                raise
            errors.append(str(e))
            continue
        specs[spec.name] = spec
    if errors is None:
        validate_specs(specs)
        return specs
    while True:     # drop specs failing cross-spec validation (messages start with the spec name)
        try:
            validate_specs(specs)
            return specs
        except SpecError as e:
            name = str(e).split(":")[0]
            if name not in specs:
                raise
            errors.append(str(e))
            specs.pop(name)


def validate_specs(specs: Mapping[str, TableSpec]) -> None:
    """Cross-spec checks: FK targets, FK column counts, depends_on names."""
    for s in specs.values():
        for fk in s.foreign_keys:
            if fk.ref_schema == "gold" and fk.ref_table in specs:
                target = specs[fk.ref_table]
                ref_cols = fk.ref_columns or target.primary_key
                if ref_cols != target.primary_key:
                    raise SpecError(f"{s.name}: FK {fk.columns} must reference the primary key "
                                    f"{target.primary_key} of {fk.ref_table}")
                if len(ref_cols) != len(fk.columns):
                    raise SpecError(f"{s.name}: FK {fk.columns} -> {fk.ref_table}{ref_cols}: column counts differ")
            elif not fk.ref_columns:
                raise SpecError(f"{s.name}: FK to {fk.ref_schema}.{fk.ref_table} (no spec) needs explicit columns")
        unknown = [d for d in s.depends_on if d not in specs]
        if unknown:
            raise SpecError(f"{s.name}: depends_on unknown tables {unknown}")


def fk_ref_columns(fk: ForeignKey, specs: Mapping[str, TableSpec]) -> List[str]:
    if fk.ref_columns:
        return fk.ref_columns
    return specs[fk.ref_table].primary_key


# ---- comments (D25) -----------------------------------------------------------------------------
def _comment_of(v: Any) -> Optional[str]:
    if isinstance(v, str):
        return " ".join(v.split()) or None
    if isinstance(v, Mapping):
        for k in ("comment", "description", "desc"):
            if v.get(k):
                return " ".join(str(v[k]).split())
    return None


def parse_dictionary(data: Any) -> Dict[str, str]:
    """Column -> comment from a dictionary YAML. Accepts ``columns: {name: text | {comment: text}}``
    or a flat top-level mapping of the same shape (per-table sections are ignored here)."""
    if not isinstance(data, Mapping):
        return {}
    section = data.get("columns") if isinstance(data.get("columns"), Mapping) else data
    out = {}
    for k, v in section.items():
        if k in ("version", "tables", "columns") or not isinstance(k, str):
            continue
        text = _comment_of(v)
        if text:
            out[k] = text
    return out


def load_dictionaries(paths: Sequence[Path] = (GOLD_DICTIONARY, CONFIG_DICTIONARY)) -> List[Dict[str, str]]:
    """Dictionaries in priority order; missing files are skipped."""
    out = []
    for p in paths:
        p = Path(p)
        if p.exists():
            try:
                out.append(parse_dictionary(yaml.safe_load(p.read_text()) or {}))
            except yaml.YAMLError as e:
                raise SpecError(f"{p}: invalid YAML: {str(e).splitlines()[0]}") from e
    return out


def resolve_comments(spec: TableSpec, dictionaries: Sequence[Mapping[str, str]]) -> List[str]:
    """Fill column comments from the dictionaries (spec wins); returns the columns still missing one."""
    missing = []
    for c in spec.columns:
        if not c.comment:
            c.comment = next((d[c.name] for d in dictionaries if d.get(c.name)), None)
        if not c.comment:
            missing.append(c.name)
    return missing


def comment_gaps(specs: Mapping[str, TableSpec], dictionaries: Sequence[Mapping[str, str]]) -> List[str]:
    """'table.column' (or 'table' for a table comment) still lacking a comment, after resolution."""
    gaps = []
    for s in specs.values():
        if not s.comment.strip():
            gaps.append(s.name)
        gaps += [f"{s.name}.{c}" for c in resolve_comments(s, dictionaries)]
    return gaps


# ---- SQL rendering ------------------------------------------------------------------------------
def sql_str(text: str) -> str:
    """SQL string literal. Databricks SQL concatenates adjacent literals ('it''s' -> its), so quotes
    are backslash-escaped instead of doubled."""
    return "'" + str(text).replace("\\", "\\\\").replace("'", "\\'") + "'"


def q(name: str) -> str:
    return f"`{name}`"


def render_create(spec: TableSpec, catalog: str, specs: Optional[Mapping[str, TableSpec]] = None,
                  skip_fks: Iterable[str] = ()) -> str:
    """CREATE OR REPLACE TABLE / VIEW DDL. FKs named in `skip_fks` are left for add_fk_sql (e.g. the
    referenced table does not exist yet, or two dims reference each other)."""
    specs = specs or {spec.name: spec}
    if spec.kind == "view":
        cols = ",\n".join(f"  {q(c.name)} COMMENT {sql_str(c.comment or '')}" for c in spec.columns)
        select = ", ".join(q(c) for c in spec.column_names)
        return (f"CREATE OR REPLACE VIEW {spec.fqn(catalog)} (\n{cols}\n)\nCOMMENT {sql_str(spec.full_comment)}\n"
                f"AS SELECT {select} FROM (\n{spec.body}\n) AS __src")
    if spec.kind != "table":
        raise SpecError(f"{spec.name}: render_create is for tables and views")
    lines = [f"  {q(c.name)} {c.type}{' NOT NULL' if c.not_null else ''} COMMENT {sql_str(c.comment or '')}"
             for c in spec.columns]
    lines.append(f"  CONSTRAINT pk_{spec.name} PRIMARY KEY ({', '.join(q(c) for c in spec.primary_key)}) RELY")
    skip = set(skip_fks)
    for fk in spec.foreign_keys:
        if fk.name(spec.name) in skip:
            continue
        lines.append("  " + fk_clause(spec, fk, catalog, specs))
    ddl = f"CREATE OR REPLACE TABLE {spec.fqn(catalog)} (\n" + ",\n".join(lines) + "\n)"
    if spec.cluster_by:
        ddl += "\nCLUSTER BY " + ("AUTO" if spec.cluster_by == ["AUTO"] else
                                  "(" + ", ".join(q(c) for c in spec.cluster_by) + ")")
    return ddl + f"\nCOMMENT {sql_str(spec.full_comment)}"


def fk_clause(spec: TableSpec, fk: ForeignKey, catalog: str, specs: Mapping[str, TableSpec]) -> str:
    ref_cols = fk_ref_columns(fk, specs)
    return (f"CONSTRAINT {fk.name(spec.name)} FOREIGN KEY ({', '.join(q(c) for c in fk.columns)}) "
            f"REFERENCES {catalog}.{fk.ref_schema}.{fk.ref_table} ({', '.join(q(c) for c in ref_cols)}) "
            f"NOT ENFORCED RELY")


def add_fk_sql(spec: TableSpec, fk: ForeignKey, catalog: str, specs: Mapping[str, TableSpec]) -> str:
    return f"ALTER TABLE {spec.fqn(catalog)} ADD {fk_clause(spec, fk, catalog, specs)}"


def render_insert(spec: TableSpec, catalog: str) -> str:
    """INSERT OVERWRITE selecting the spec columns by name from the body (order-proof)."""
    cols = ", ".join(q(c) for c in spec.column_names)
    return f"INSERT OVERWRITE {spec.fqn(catalog)}\nSELECT {cols} FROM (\n{spec.body}\n) AS __src"


def render_analyze(spec: TableSpec, catalog: str) -> str:
    return f"ANALYZE TABLE {spec.fqn(catalog)} COMPUTE STATISTICS FOR ALL COLUMNS"


def render_tags(spec: TableSpec, catalog: str) -> Optional[str]:
    tags = {"smbc_layer": "gold", "smbc_domain": spec.domain, "smbc_synthetic": "true", **spec.tags}
    pairs = ", ".join(f"{sql_str(k)} = {sql_str(v)}" for k, v in tags.items())
    kind = "VIEW" if spec.kind == "view" else "TABLE"
    return None if spec.kind == "function" else f"ALTER {kind} {spec.fqn(catalog)} SET TAGS ({pairs})"


def render(sql: str, params: Mapping[str, Any]) -> str:
    """${name} substitution that fails loudly on an unknown placeholder."""
    def sub(m: "re.Match[str]") -> str:
        if m.group(1) not in params:
            raise SpecError(f"unknown placeholder ${{{m.group(1)}}}")
        return str(params[m.group(1)])

    return _PLACEHOLDER_RE.sub(sub, sql)


def placeholders(sql: str) -> Set[str]:
    return set(_PLACEHOLDER_RE.findall(sql))


def build_params(cfg: Any, catalog: Optional[str] = None) -> Dict[str, Any]:
    """Placeholders available to SQL bodies and checks (from config/smbc_genie.yaml)."""
    p: Dict[str, Any] = {
        "catalog": catalog or cfg.catalog, "as_of_date": cfg.as_of_date, "history_start": cfg.history_start,
        "calendar_end": CALENDAR_END, "open_end": OPEN_END, "scale": cfg.scale, "seed": cfg.random_seed,
        "fy_start_month": int(cfg.raw.get("fiscal_year_start_month", 4)),
    }
    for k, v in cfg.thresholds.items():
        p[k] = v
    return p


# ---- dependencies ---------------------------------------------------------------------------------
def sql_refs(body: str) -> Set[Tuple[str, str]]:
    """(schema, table) pairs a body reads through ${catalog}.<schema>.<table> (function calls excluded)."""
    return {(s.lower(), t.lower()) for s, t in _SQL_REF_RE.findall(body)}


def sql_fn_refs(body: str) -> Set[Tuple[str, str]]:
    """(schema, function) pairs a body calls through ${catalog}.<schema>.<fn>(...)."""
    return {(s.lower(), t.lower()) for s, t in _SQL_FN_RE.findall(body)}


def hard_deps(spec: TableSpec, specs: Mapping[str, TableSpec]) -> Set[str]:
    """Gold specs this one must be built after: tables it reads, gold functions it calls, depends_on."""
    refs = {t for s, t in sql_refs(spec.body) | sql_fn_refs(spec.body)
            if s == "gold" and t in specs and t != spec.name}
    return refs | set(spec.depends_on)


def fk_deps(spec: TableSpec, specs: Mapping[str, TableSpec]) -> Set[str]:
    return {fk.ref_table for fk in spec.foreign_keys
            if fk.ref_schema == "gold" and fk.ref_table in specs and fk.ref_table != spec.name}


def _toposort(nodes: Sequence[str], edges: Mapping[str, Set[str]]) -> Optional[List[str]]:
    """Kahn's algorithm, alphabetical among ready nodes; None on a cycle."""
    pending = {n: {d for d in edges.get(n, set()) if d in nodes} for n in nodes}
    order: List[str] = []
    while pending:
        ready = sorted(n for n, d in pending.items() if not d)
        if not ready:
            return None
        for n in ready:
            order.append(n)
            del pending[n]
        for d in pending.values():
            d.difference_update(ready)
    return order


def build_order(specs: Mapping[str, TableSpec], names: Optional[Iterable[str]] = None) -> List[str]:
    """Data dependencies first; FK targets are built first too unless that forms a cycle."""
    nodes = sorted(names if names is not None else specs)
    hard = {n: hard_deps(specs[n], specs) for n in nodes}
    both = {n: hard[n] | fk_deps(specs[n], specs) for n in nodes}
    order = _toposort(nodes, both) or _toposort(nodes, hard)
    if order is None:
        raise SpecError("dependency cycle among gold tables: " + ", ".join(nodes))
    return order


def levels(specs: Mapping[str, TableSpec], order: Sequence[str]) -> List[List[str]]:
    """Group an order into waves that can build in parallel (hard deps only)."""
    lvl: Dict[str, int] = {}
    for n in order:
        deps = [d for d in hard_deps(specs[n], specs) if d in lvl]
        lvl[n] = 1 + max((lvl[d] for d in deps), default=-1)
    waves: Dict[int, List[str]] = {}
    for n in order:
        waves.setdefault(lvl[n], []).append(n)
    return [waves[k] for k in sorted(waves)]


def select(specs: Mapping[str, TableSpec], only: Optional[Iterable[str]] = None,
           domain: Optional[Iterable[str]] = None, with_deps: bool = False) -> List[str]:
    """Names selected by --only / --domain (optionally plus their upstream gold deps)."""
    only, domain = list(only or []), list(domain or [])
    unknown = [n for n in only if n not in specs]
    if unknown:
        raise SpecError(f"unknown tables {unknown}")
    chosen = {n for n, s in specs.items() if (not only and not domain) or n in only or s.domain in domain}
    if with_deps:
        stack = list(chosen)
        while stack:
            for d in hard_deps(specs[stack.pop()], specs):
                if d not in chosen:
                    chosen.add(d)
                    stack.append(d)
    return build_order(specs, chosen)


# ---- verification SQL -------------------------------------------------------------------------------
def pk_check_sql(spec: TableSpec, catalog: str) -> str:
    """rows, duplicate keys, rows with a NULL key component."""
    keys = ", ".join(q(c) for c in spec.primary_key)
    nulls = " OR ".join(f"{q(c)} IS NULL" for c in spec.primary_key)
    return (f"SELECT (SELECT count(*) FROM {spec.fqn(catalog)}) AS n_rows, "
            f"(SELECT count(*) FROM (SELECT {keys} FROM {spec.fqn(catalog)} GROUP BY {keys} HAVING count(*) > 1)) AS n_dup_keys, "
            f"(SELECT count(*) FROM {spec.fqn(catalog)} WHERE {nulls}) AS n_null_keys")


def fk_check_sql(spec: TableSpec, fk: ForeignKey, catalog: str, specs: Mapping[str, TableSpec]) -> str:
    """rows with a (non-NULL) FK value, of which unresolved, plus NULL FK rows."""
    ref_cols = fk_ref_columns(fk, specs)
    on = " AND ".join(f"r.{q(rc)} = f.{q(c)}" for c, rc in zip(fk.columns, ref_cols))
    nonnull = " AND ".join(f"f.{q(c)} IS NOT NULL" for c in fk.columns)
    anynull = " OR ".join(f"f.{q(c)} IS NULL" for c in fk.columns)
    ref = f"(SELECT DISTINCT {', '.join(q(c) for c in ref_cols)} FROM {catalog}.{fk.ref_schema}.{fk.ref_table})"
    return (f"SELECT count_if({nonnull}) AS n_fk, count_if({nonnull} AND r.{q(ref_cols[0])} IS NULL) AS n_orphans, "
            f"count_if({anynull}) AS n_null FROM {spec.fqn(catalog)} f LEFT JOIN {ref} r ON {on}")


def scd2_checks(spec: TableSpec) -> List[Dict[str, str]]:
    """Standard SCD2 integrity checks (each SQL returns a violation count; ${table} is filled later):
    versions of one key never overlap and leave no gaps, valid_from <= valid_to, version_no runs
    1..n in valid_from order, exactly one current version per key and it is the open-ended last one."""
    if not spec.scd2:
        return []
    s = spec.scd2
    k, vf, vt, cur, ver, end = (q(s["key"]), q(s["valid_from"]), q(s["valid_to"]), q(s["current"]),
                                q(s["version"]), s["open_end"])
    seq = (f"SELECT {k}, {vf}, {vt}, {cur}, {ver}, lead({vf}) OVER (PARTITION BY {k} ORDER BY {vf}) AS nxt, "
           f"row_number() OVER (PARTITION BY {k} ORDER BY {vf}) AS rn FROM ${{table}}")
    return [
        {"name": "scd2_no_overlap", "sql": f"SELECT count_if(nxt IS NOT NULL AND nxt <= {vt}) FROM ({seq})"},
        {"name": "scd2_no_gap", "sql": f"SELECT count_if(nxt IS NOT NULL AND nxt > date_add({vt}, 1)) FROM ({seq})"},
        {"name": "scd2_from_le_to", "sql": f"SELECT count_if({vf} > {vt}) FROM ${{table}}"},
        {"name": "scd2_version_sequence", "sql": f"SELECT count_if({ver} <> rn) FROM ({seq})"},
        {"name": "scd2_one_current_per_key",
         "sql": f"SELECT count(*) FROM (SELECT {k} FROM ${{table}} GROUP BY {k} HAVING count_if({cur}) <> 1)"},
        {"name": "scd2_current_is_open_last",
         "sql": f"SELECT count_if(({cur} AND ({vt} <> DATE'{end}' OR nxt IS NOT NULL)) "
                f"OR (NOT {cur} AND {vt} = DATE'{end}')) FROM ({seq})"},
    ]


def comment_gate_sql(catalog: str, names: Sequence[str]) -> str:
    """Tables / columns in information_schema without a comment (post-build gate, D25)."""
    lst = ", ".join(sql_str(n) for n in names) or "''"
    return (f"SELECT table_name, NULL AS column_name FROM {catalog}.information_schema.tables "
            f"WHERE table_schema = 'gold' AND table_name IN ({lst}) AND (comment IS NULL OR trim(comment) = '') "
            f"UNION ALL SELECT table_name, column_name FROM {catalog}.information_schema.columns "
            f"WHERE table_schema = 'gold' AND table_name IN ({lst}) AND (comment IS NULL OR trim(comment) = '')")


# ---- SCD2 helpers ----------------------------------------------------------------------------------
def client_sk_join(date_expr: str, fact_alias: str = "f", dim_alias: str = "dc",
                   key_expr: Optional[str] = None, catalog: str = "${catalog}") -> str:
    """Canonical as-was lookup (D11) of dim_client for a client-grain fact: the version valid on the
    fact date; dates before a client's first version resolve to version 1, dates after the as-of date
    to the current (open-ended) version. Select ``{dim_alias}.golden_client_sk``."""
    key = key_expr or f"{fact_alias}.golden_client_id"
    return (f"LEFT JOIN {catalog}.gold.dim_client {dim_alias}\n"
            f"  ON  {dim_alias}.golden_client_id = {key}\n"
            f"  AND {date_expr} <= {dim_alias}.valid_to\n"
            f"  AND ({date_expr} >= {dim_alias}.valid_from OR {dim_alias}.version_no = 1)")


def group_lead_sk_join(date_expr: str, fact_alias: str = "f", group_alias: str = "dg", dim_alias: str = "dl",
                       catalog: str = "${catalog}") -> str:
    """Group-grain facts (D12): the group's lead entity (gold.dim_client_group.lead_golden_client_id) and the
    dim_client version of that entity valid on the fact date. Select ``{dim_alias}.golden_client_sk AS
    lead_golden_client_sk``."""
    return (f"LEFT JOIN {catalog}.gold.dim_client_group {group_alias} ON {group_alias}.client_group_id = {fact_alias}.client_group_id\n"
            + client_sk_join(date_expr, dim_alias=dim_alias, key_expr=f"{group_alias}.lead_golden_client_id", catalog=catalog))


def month_start(d: _dt.date) -> _dt.date:
    return d.replace(day=1)


def month_end(d: _dt.date) -> _dt.date:
    nxt = d.replace(day=28) + _dt.timedelta(days=4)
    return nxt - _dt.timedelta(days=nxt.day)


def month_range(start: _dt.date, end: _dt.date) -> List[_dt.date]:
    """First days of every month from start's month to end's month (inclusive)."""
    out, d = [], month_start(start)
    while d <= end:
        out.append(d)
        d = month_end(d) + _dt.timedelta(days=1)
    return out


def collapse_versions(states: Iterable[Mapping[str, Any]], key: str, period: str, attrs: Sequence[str],
                      open_end: _dt.date = _dt.date(9999, 12, 31)) -> List[Dict[str, Any]]:
    """Reference implementation of the SCD2 collapse dim_client.sql performs in SQL: monthly states
    (one row per key x month, `period` = first day of the month, dense from the key's first month)
    become versions. A version starts at the first month whose attribute tuple differs from the
    previous month's, ends the day before the next version starts, and the last one is current and
    open-ended (valid_to = 9999-12-31), so versions are contiguous and never overlap."""
    by_key: Dict[Any, List[Mapping[str, Any]]] = {}
    for s in states:
        by_key.setdefault(s[key], []).append(s)
    out: List[Dict[str, Any]] = []
    for k in sorted(by_key, key=str):
        versions: List[Dict[str, Any]] = []
        prev = object()
        for r in sorted(by_key[k], key=lambda r: r[period]):
            vals = tuple(r.get(a) for a in attrs)
            if vals != prev:
                versions.append({key: k, "valid_from": r[period], **{a: r.get(a) for a in attrs}})
            prev = vals
        for i, v in enumerate(versions):
            nxt = versions[i + 1]["valid_from"] if i + 1 < len(versions) else None
            v["valid_to"] = (nxt - _dt.timedelta(days=1)) if nxt else open_end
            v["version_no"], v["is_current"] = i + 1, nxt is None
        out += versions
    return out
