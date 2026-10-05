"""Silver standardisation (Phase 4b; PLAN §6; DECISIONS D11, D13, D22, D25): spec + SQL helpers.

config/table_specs.yaml declares, per bronze / shared table: target column types, the business key,
how rows reach a golden client (or a client group) and the column checks the DQ rules are generated
from (smbc_genie_lib.dq). This module turns one spec + the live source schema into a staging SELECT:

  typed      strings trimmed (blank -> NULL), codes upper-cased, ISO strings -> DATE / TIMESTAMP,
             numbers / flags via try_cast (unparseable -> NULL, caught by the type-conformance rule)
  conformed  golden_client_id through silver.xref_client_source from the table's own identity key
             (account-keyed rows via bronze.core_account, ...); client_group_id from
             silver.client_golden_identity; group-grain / shared rows: the group key, if mastered
  ranked     _dedup_rank 1 = the row kept per business key: original deliveries before reprocessed
             batches (batch id pattern), then the earliest _ingest_ts
  flagged    _dq_block = ids of the blocking rules the row fails

rank 1 and no blocking failure -> silver.<name>; rank 1 with a failure -> silver.quarantine_<name>;
rank > 1 -> de-duplicated (counted; logged as the table's uniqueness rule result). No Spark here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - yaml is a hard dependency of the package
    yaml = None

SCALAR_TYPES = {"string": "STRING", "code": "STRING", "date": "DATE", "timestamp": "TIMESTAMP",
                "int": "INT", "bigint": "BIGINT", "double": "DOUBLE", "boolean": "BOOLEAN"}
_PASSTHROUGH_RE = re.compile(r"^(decimal\(\d+,\s*\d+\)|array<.+>|map<.+>|struct<.+>)$", re.I)
META_COLUMNS = ["_source_system", "_source_file", "_batch_id", "_ingest_ts"]
CONFORMED = ["golden_client_id", "client_group_id"]
TRUE_WORDS, FALSE_WORDS = ("true", "t", "yes", "y", "1"), ("false", "f", "no", "n", "0")
# name heuristics for bronze columns a spec does not list (carried through, reported as drift)
_DATE_NAME_RE = re.compile(r"(^|_)(date|month|since|day)$|_date_|^(valid|effective)_(from|to)$")
_TS_NAME_RE = re.compile(r"(_ts|_at|_timestamp)$")


def default_specs_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "table_specs.yaml"


def q(col: str) -> str:
    """Back-quoted identifier."""
    return f"`{col}`"


def sql_type(t: str) -> str:
    """Spec type -> Spark SQL type."""
    low = t.strip().lower()
    if low in SCALAR_TYPES:
        return SCALAR_TYPES[low]
    if _PASSTHROUGH_RE.match(low):
        return low.upper()
    raise ValueError(f"unknown spec type {t!r}")


def norm_source_type(t: str) -> str:
    """Spark simpleString / DESCRIBE type -> comparable lower-case form (int, bigint, date, ...)."""
    low = t.strip().lower()
    return {"integer": "int", "long": "bigint", "float": "double", "short": "int", "smallint": "int",
            "tinyint": "int", "byte": "int"}.get(low, low)


def infer_type(name: str, source_type: str) -> str:
    """Spec type for a bronze column the spec does not declare."""
    st = norm_source_type(source_type)
    if st != "string":
        return {"float": "double"}.get(st, st)
    if name in ("_ingest_ts", "_shared_at") or _TS_NAME_RE.search(name):
        return "timestamp"
    if _DATE_NAME_RE.search(name):
        return "date"
    return "string"


# ---- specs --------------------------------------------------------------------------------------
@dataclass
class TableSpec:
    name: str
    source: str                      # bronze.<t> | shared.<t>
    domain: str
    comment: str
    keys: List[str]
    columns: Dict[str, str]          # ordered column -> spec type
    client: Optional[Dict[str, Any]] = None   # {key, system | via | system_column, required, scope}
    group: Optional[Dict[str, Any]] = None    # {key, via, required}
    date_column: Optional[str] = None
    checks: Dict[str, Any] = field(default_factory=dict)
    optional_columns: List[str] = field(default_factory=list)

    @property
    def target(self) -> str:
        return f"silver.{self.name}"

    @property
    def quarantine(self) -> str:
        return f"silver.quarantine_{self.name}"

    @property
    def schema(self) -> str:
        return self.source.split(".")[0]

    @property
    def client_grain(self) -> bool:
        return self.client is not None


@dataclass
class Conform:
    xref: str
    golden: str
    group_master: str                # schema.table.column of the global group master key
    identity_keys: Dict[str, str]    # identity column -> ER source system
    lookups: Dict[str, Dict[str, str]]  # name -> {table, key, identity}
    reprocessed_batch_pattern: Optional[str] = None
    dedup_order: List[str] = field(default_factory=lambda: ["_ingest_ts ASC", "_batch_id ASC"])


@dataclass
class Specs:
    conform: Conform
    tables: Dict[str, TableSpec]
    core_sources: List[str]
    attribute_sources: Dict[str, Any]
    tags: Dict[str, Any] = field(default_factory=dict)


def load_specs(path: Optional[Path] = None) -> Specs:
    data = yaml.safe_load(Path(path or default_specs_path()).read_text())
    return parse_specs(data)


def parse_specs(data: Dict[str, Any]) -> Specs:
    c = data["conform"]
    conform = Conform(xref=c["xref"], golden=c["golden"], group_master=c["group_master"],
                      identity_keys=dict(c["identity_keys"]), lookups=dict(c.get("lookups", {})),
                      reprocessed_batch_pattern=c.get("reprocessed_batch_pattern"),
                      dedup_order=list(c.get("dedup_order", ["_ingest_ts ASC", "_batch_id ASC"])))
    tables = {}
    for name, t in data["tables"].items():
        client = t.get("client")
        if client:
            client = dict(client)
            if "via" not in client and "system_column" not in client:
                client.setdefault("system", conform.identity_keys.get(client["key"]))
            client.setdefault("required", True)
        spec = TableSpec(name=name, source=t["source"], domain=t["domain"], comment=t["comment"],
                         keys=list(t["keys"]), columns=dict(t["columns"]), client=client,
                         group=dict(t["group"]) if t.get("group") else None, date_column=t.get("date"),
                         checks=dict(t.get("checks") or {}), optional_columns=list(t.get("optional", [])))
        validate_spec(spec, conform)
        tables[name] = spec
    return Specs(conform=conform, tables=tables, core_sources=list(data.get("core_sources", [])),
                 attribute_sources=dict(data.get("attribute_sources", {})), tags=dict(data.get("tags", {})))


def table_tags(name: str, tags: Dict[str, Any]) -> Dict[str, str]:
    """UC tags of a silver table (D37): fixed keys + four-quadrant class + source region."""
    out = {k: str(v) for k, v in (tags.get("fixed") or {}).items()}
    quad = (tags.get("quadrant_overrides") or {}).get(name)
    if quad is None:
        quad = next((v for p, v in sorted((tags.get("quadrant_by_prefix") or {}).items(), key=lambda kv: -len(kv[0]))
                     if name.startswith(p)), None)
    if quad:
        out["smbc_quadrant"] = quad
    region = next((v for p, v in (tags.get("source_region_by_prefix") or {}).items() if name.startswith(p)),
                  tags.get("source_region_default"))
    if region:
        out["smbc_source_region"] = region
    return out


def validate_spec(spec: TableSpec, conform: Conform) -> None:
    """Fail fast on a malformed spec (types, keys, conform references)."""
    for col, t in spec.columns.items():
        sql_type(t)
    missing = [k for k in spec.keys if k not in spec.columns]
    if missing:
        raise ValueError(f"{spec.name}: key columns {missing} not in columns")
    if not spec.keys:
        raise ValueError(f"{spec.name}: a business key is required")
    if spec.client:
        if spec.client["key"] not in spec.columns:
            raise ValueError(f"{spec.name}: client key {spec.client['key']} not in columns")
        via, syscol = spec.client.get("via"), spec.client.get("system_column")
        if via and via not in conform.lookups:
            raise ValueError(f"{spec.name}: unknown lookup {via}")
        if syscol and syscol not in spec.columns:
            raise ValueError(f"{spec.name}: system column {syscol} not in columns")
        if not via and not syscol and not spec.client.get("system"):
            raise ValueError(f"{spec.name}: client key {spec.client['key']} has no identity system")
    if spec.group:
        if spec.group["key"] not in spec.columns:
            raise ValueError(f"{spec.name}: group key {spec.group['key']} not in columns")
        if spec.group.get("via") and spec.group["via"] not in conform.lookups:
            raise ValueError(f"{spec.name}: unknown lookup {spec.group['via']}")
    if spec.date_column and spec.date_column not in spec.columns:
        raise ValueError(f"{spec.name}: date column {spec.date_column} not in columns")
    clash = [c for c in CONFORMED if c in spec.columns]
    if clash and (spec.client or spec.group):
        raise ValueError(f"{spec.name}: bronze columns {clash} clash with conformed keys")


# ---- typed projection ---------------------------------------------------------------------------
def cast_expr(col: str, target: str, source_type: str) -> str:
    """Typed, trimmed expression for one source column."""
    src, t, ref = norm_source_type(source_type), target.strip().lower(), q(col)
    if src == "string":
        s = f"NULLIF(trim({ref}), '')"
        if t == "string":
            return s
        if t == "code":
            return f"upper({s})"
        if t == "boolean":
            return (f"CASE WHEN lower({s}) IN {TRUE_WORDS} THEN true "
                    f"WHEN lower({s}) IN {FALSE_WORDS} THEN false END")
        return f"try_cast({s} AS {sql_type(t)})"
    if t in ("string", "code"):
        e = f"CAST({ref} AS STRING)"
        return f"upper({e})" if t == "code" else e
    if src == norm_source_type(sql_type(t).lower()):
        return ref
    return f"try_cast({ref} AS {sql_type(t)})"


def needs_conformance(target: str, source_type: str) -> bool:
    """A string landing parsed into a non-string type (can fail -> NULL)."""
    return norm_source_type(source_type) == "string" and target.strip().lower() not in ("string", "code")


def resolve_columns(spec: TableSpec, src_schema: Dict[str, str]) -> Tuple[Dict[str, str], Dict[str, List[str]]]:
    """Columns silver will carry (spec order, then undeclared bronze columns) + schema drift."""
    cols: Dict[str, str] = {}
    missing = [c for c in spec.columns if c not in src_schema]
    hard = [c for c in missing if c not in spec.optional_columns]
    if hard:
        raise ValueError(f"{spec.name}: spec columns missing in {spec.source}: {hard}")
    for c, t in spec.columns.items():
        if c in src_schema:
            cols[c] = t
    extra = [c for c in src_schema if c not in spec.columns]
    for c in extra:
        cols[c] = infer_type(c, src_schema[c])
    return cols, {"missing": missing, "undeclared": extra}


def typed_select(cols: Dict[str, str], src_schema: Dict[str, str]) -> Tuple[List[str], List[str]]:
    """(typed select items, raw items kept for the type-conformance check)."""
    typed, raw = [], []
    for c, t in cols.items():
        typed.append(f"{cast_expr(c, t, src_schema[c])} AS {q(c)}")
        if needs_conformance(t, src_schema[c]):
            raw.append(f"NULLIF(trim({q(c)}), '') AS {q('_raw_' + c)}")
    return typed, raw


def conformance_expr(cols: Dict[str, str], src_schema: Dict[str, str]) -> Optional[str]:
    """Pass expression: every parsed column that had a value still has one."""
    parts = [f"({q('_raw_' + c)} IS NULL OR {q(c)} IS NOT NULL)"
             for c, t in cols.items() if needs_conformance(t, src_schema[c])]
    return " AND ".join(parts) if parts else None


# ---- conformed keys -----------------------------------------------------------------------------
def fqn(catalog: str, name: str) -> str:
    return name if name.count(".") == 2 else f"{catalog}.{name}"


def xref_view(catalog: str, conform: Conform) -> str:
    return (f"(SELECT source_system, source_id, min(golden_client_id) AS golden_client_id "
            f"FROM {fqn(catalog, conform.xref)} GROUP BY source_system, source_id)")


def golden_view(catalog: str, conform: Conform) -> str:
    return (f"(SELECT golden_client_id, min(client_group_id) AS client_group_id "
            f"FROM {fqn(catalog, conform.golden)} GROUP BY golden_client_id)")


def group_master_view(catalog: str, conform: Conform) -> str:
    schema, table, col = conform.group_master.split(".")
    return (f"(SELECT DISTINCT {q(col)} AS client_group_id FROM {catalog}.{schema}.{table} "
            f"WHERE {q(col)} IS NOT NULL)")


def lookup_view(catalog: str, lk: Dict[str, str]) -> str:
    """key -> identity id (min when a key repeats), from the lookup's bronze table."""
    k, i = q(lk["key"]), q(lk["identity"])
    return (f"(SELECT NULLIF(trim(CAST({k} AS STRING)), '') AS __k, min(NULLIF(trim(CAST({i} AS STRING)), '')) AS __id "
            f"FROM {fqn(catalog, lk['table'])} GROUP BY NULLIF(trim(CAST({k} AS STRING)), ''))")


def derived_key(spec: TableSpec, conform: Conform) -> Optional[str]:
    """Identity column a `via` lookup adds to the table (e.g. obligor_id on collateral), if new."""
    via = (spec.client or {}).get("via")
    if not via:
        return None
    ident = conform.lookups[via]["identity"]
    return None if ident in spec.columns else ident


def client_ref(spec: TableSpec, conform: Conform) -> str:
    """Column holding the identity the row resolves through."""
    return derived_key(spec, conform) or spec.client["key"]


def client_scope(spec: TableSpec, conform: Conform) -> Optional[str]:
    """Rows on which the golden key must resolve (None = every row): rows with an identity, minus
    any configured scope (e.g. prospects' screening hits carry no golden client)."""
    parts = [] if spec.client.get("required", True) else [f"{q(client_ref(spec, conform))} IS NOT NULL"]
    if spec.client.get("scope"):
        parts.append(f"({spec.client['scope']})")
    return " AND ".join(parts) if parts else None


def golden_pass(spec: TableSpec, conform: Conform) -> str:
    scope = client_scope(spec, conform)
    return f"(NOT ({scope}) OR golden_client_id IS NOT NULL)" if scope else "golden_client_id IS NOT NULL"


def group_pass(spec: TableSpec) -> str:
    if spec.group.get("required", True):
        return "client_group_id IS NOT NULL"
    return f"({q(spec.group['key'])} IS NULL OR client_group_id IS NOT NULL)"


def conform_joins(spec: TableSpec, catalog: str, conform: Conform) -> Tuple[List[str], List[str]]:
    """(LEFT JOIN clauses over the typed CTE `t`, conformed select items). With both a client and a
    group key (shared support letters, account plans) the group key gives client_group_id."""
    joins, items = [], []
    if spec.client:
        key = f"t.{q(spec.client['key'])}"
        via, syscol = spec.client.get("via"), spec.client.get("system_column")
        if via:
            lk = conform.lookups[via]
            joins.append(f"LEFT JOIN {lookup_view(catalog, lk)} __lk ON __lk.__k = CAST({key} AS STRING)")
            ident, system = "__lk.__id", f"'{lk['system']}'"
            if derived_key(spec, conform):
                items.append(f"__lk.__id AS {q(derived_key(spec, conform))}")
        else:
            ident = f"CAST({key} AS STRING)"
            system = f"t.{q(syscol)}" if syscol else f"'{spec.client['system']}'"
        cond = f"__x.source_system = {system} AND __x.source_id = {ident}"
        if spec.client.get("scope"):
            cond += f" AND ({spec.client['scope']})"
        joins.append(f"LEFT JOIN {xref_view(catalog, conform)} __x ON {cond}")
        joins.append(f"LEFT JOIN {golden_view(catalog, conform)} __g ON __g.golden_client_id = __x.golden_client_id")
        items.append("__x.golden_client_id AS golden_client_id")
        if not spec.group:
            items.append("__g.client_group_id AS client_group_id")
    if spec.group:
        key = f"t.{q(spec.group['key'])}"
        if spec.group.get("via"):
            lk = conform.lookups[spec.group["via"]]
            joins.append(f"LEFT JOIN {lookup_view(catalog, lk)} __lkg ON __lkg.__k = CAST({key} AS STRING)")
            key = "__lkg.__id"
        joins.append(f"LEFT JOIN {group_master_view(catalog, conform)} __gm ON __gm.client_group_id = {key}")
        items.append("__gm.client_group_id AS client_group_id")
    return joins, items


def conformed_columns(spec: TableSpec, conform: Optional[Conform] = None) -> List[str]:
    """Columns silver adds: the looked-up identity (via lookups), golden_client_id, client_group_id."""
    out = []
    if spec.client and conform is not None and derived_key(spec, conform):
        out.append(derived_key(spec, conform))
    if spec.client:
        out.append("golden_client_id")
    if spec.client or spec.group:
        out.append("client_group_id")
    return out


# ---- de-duplication + staging -------------------------------------------------------------------
def dedup_order(cols: Iterable[str], conform: Conform, keys: Sequence[str]) -> str:
    """ORDER BY for the survivor: original deliveries first, earliest ingest, then a content hash."""
    cols = list(cols)
    parts = []
    if conform.reprocessed_batch_pattern and "_batch_id" in cols:
        parts.append(f"CASE WHEN `_batch_id` RLIKE '{conform.reprocessed_batch_pattern}' THEN 1 ELSE 0 END")
    for o in conform.dedup_order:
        if o.split()[0] in cols:
            parts.append(o)
    content = [q(c) for c in cols if c not in keys and not c.startswith("_")]
    if content:
        parts.append(f"xxhash64({', '.join(content)})")
    return ", ".join(parts) if parts else "1"


def key_expr(keys: Sequence[str], alias: str = "") -> str:
    """Printable business key (sample keys in DQ results)."""
    pre = f"{alias}." if alias else ""
    return "concat_ws('|', " + ", ".join(f"CAST({pre}{q(k)} AS STRING)" for k in keys) + ")"


def staged_sql(spec: TableSpec, src_schema: Dict[str, str], catalog: str, conform: Conform,
               blocking: Sequence[Tuple[str, str, Sequence[str], Sequence[str]]] = ()
               ) -> Tuple[str, Dict[str, str], Dict[str, List[str]]]:
    """(staging SELECT, silver column -> spec type, drift).
    `blocking` = [(rule_id, pass_expr, joins over t, exposed columns)]: the rules whose failures are quarantined."""
    cols, drift = resolve_columns(spec, src_schema)
    typed, raw = typed_select(cols, src_schema)
    joins, conf_items = conform_joins(spec, catalog, conform)
    for _, _, rj, rx in blocking:
        joins += [j.replace("{catalog}", catalog) for j in rj]
        conf_items += list(rx)
    keys = spec.keys
    any_null = " OR ".join(f"{q(k)} IS NULL" for k in keys)
    rank = (f"CASE WHEN {any_null} THEN 1 ELSE row_number() OVER (PARTITION BY {', '.join(q(k) for k in keys)} "
            f"ORDER BY {dedup_order(cols, conform, keys)}) END AS _dedup_rank")
    block = ", ".join(f"CASE WHEN NOT coalesce({expr}, true) THEN '{rid}' END" for rid, expr, _, _ in blocking)
    block_item = f"filter(array({block}), x -> x IS NOT NULL)" if blocking else "CAST(array() AS ARRAY<STRING>)"
    sql = "\n".join([
        "WITH t AS (",
        f"  SELECT {', '.join(typed + raw)}",
        f"  FROM {catalog}.{spec.source}",
        "), c AS (",
        f"  SELECT t.*{''.join(', ' + i for i in conf_items)}",
        "  FROM t",
        *[f"  {j}" for j in joins],
        "), r AS (",
        f"  SELECT c.*, {key_expr(keys)} AS _dq_key, {rank}",
        "  FROM c",
        ")",
        f"SELECT r.*, {block_item} AS _dq_block FROM r",
    ])
    return sql, cols, drift


def silver_select(cols: Dict[str, str], spec: TableSpec, conform: Optional[Conform] = None) -> List[str]:
    """Silver column order: business columns, conformed keys, then bronze lineage metadata."""
    business = [c for c in cols if not c.startswith("_")]
    meta = [c for c in cols if c.startswith("_")]
    return business + conformed_columns(spec, conform) + meta


def reason_map_sql(reasons: Dict[str, str]) -> str:
    """SQL map(rule_id -> reason) for quarantine rows."""
    if not reasons:
        return "map()"
    items = ", ".join(f"'{rid}', '{text.replace(chr(39), chr(39) * 2)}'" for rid, text in sorted(reasons.items()))
    return f"map({items})"


def load_order(specs: Specs) -> List[List[str]]:
    """Tables in load levels: a table whose blocking foreign keys or code lists reference silver.<parent>
    loads after the parent (soft references are checked after every table is loaded)."""
    deps = {}
    for name, s in specs.tables.items():
        fks = s.checks.get("fk") or {}
        refs = [fks[c] for c in (s.checks.get("fk_blocking") or []) if c in fks] + \
            list((s.checks.get("enum_ref") or {}).values())
        deps[name] = {r.split(".")[1] for r in refs if r.startswith("silver.")} - {name}
    levels: List[List[str]] = []
    done: set = set()
    while len(done) < len(deps):
        ready = sorted(n for n, d in deps.items() if n not in done and d <= done)
        if not ready:
            raise ValueError(f"cyclic or unknown silver dependencies: {sorted(set(deps) - done)}")
        levels.append(ready)
        done |= set(ready)
    return levels


# ---- golden-record coverage + attribute completeness ---------------------------------------------
IDENTITY_FIELDS = {"name": "source_name_as_recorded", "country": "country_as_recorded",
                   "parent": "parent_ref_as_recorded", "lei": "lei_as_recorded", "tax": "tax_id_as_recorded"}


def _src_list(sources: Sequence[str]) -> str:
    return ", ".join(f"'{s}'" for s in sources)


def record_completeness_expr(source_fields: Dict[str, Iterable[str]]) -> str:
    """Share of a record's identity fields populated, over the fields its source system carries."""
    whens = []
    for src, fields in source_fields.items():
        fs = [IDENTITY_FIELDS[f] for f in fields if f in IDENTITY_FIELDS]
        num = " + ".join(f"CASE WHEN r.{c} IS NOT NULL THEN 1 ELSE 0 END" for c in fs)
        whens.append(f"WHEN '{src}' THEN ({num}) / {float(len(fs))}")
    return f"CASE r.source_system {' '.join(whens)} END"


def coverage_sql(catalog: str, specs: Specs, sources: Sequence[str], source_fields: Dict[str, Iterable[str]],
                 start: str, end: str) -> str:
    """Golden client x month x source system: present (a record available by month-end), records,
    first / latest record dates, identity-field completeness, core-source flags (latest ER resolution)."""
    core = _src_list(specs.core_sources)
    return f"""
WITH rec AS (
  SELECT x.golden_client_id, r.source_system, r.source_id, r.record_available_from AS avail,
         {record_completeness_expr(source_fields)} AS field_pct
  FROM {catalog}.silver.client_source_record r
  JOIN {catalog}.silver.xref_client_source x ON x.source_system = r.source_system AND x.source_id = r.source_id
), months AS (
  SELECT explode(sequence(DATE'{start}', DATE'{end}', INTERVAL 1 MONTH)) AS month
), srcs AS (
  SELECT explode(array({_src_list(sources)})) AS source_system
), clients AS (
  SELECT golden_client_id, min(avail) AS first_seen FROM rec GROUP BY golden_client_id
), cov AS (
  SELECT c.golden_client_id, m.month, s.source_system, count(r.source_id) AS n_records,
         min(r.avail) AS first_available_date, max(r.avail) AS last_record_added_date, max(r.field_pct) AS field_pct
  FROM clients c CROSS JOIN months m CROSS JOIN srcs s
  LEFT JOIN rec r ON r.golden_client_id = c.golden_client_id AND r.source_system = s.source_system
                 AND r.avail <= last_day(m.month)
  WHERE c.first_seen <= last_day(m.month)
  GROUP BY c.golden_client_id, m.month, s.source_system
)
SELECT cov.month, last_day(cov.month) AS month_end_date, cov.golden_client_id, g.client_group_id, cov.source_system,
       cov.n_records > 0 AS is_present, CAST(cov.n_records AS INT) AS n_records, cov.first_available_date,
       cov.last_record_added_date, round(cov.field_pct, 4) AS source_field_completeness,
       cov.source_system IN ({core}) AS is_core_source,
       CAST(sum(CASE WHEN cov.n_records > 0 THEN 1 ELSE 0 END) OVER (PARTITION BY cov.golden_client_id, cov.month) AS INT)
         AS n_sources_present,
       coalesce(bool_and(CASE WHEN cov.source_system IN ({core}) THEN cov.n_records > 0 END)
         OVER (PARTITION BY cov.golden_client_id, cov.month), false) AS in_all_core_sources,
       CAST(sum(CASE WHEN cov.n_records > 0 THEN 1 ELSE 0 END)
         OVER (PARTITION BY cov.golden_client_id, cov.source_system ORDER BY cov.month) AS INT) AS months_present
FROM cov LEFT JOIN {catalog}.silver.client_golden_identity g ON g.golden_client_id = cov.golden_client_id"""


def _fact_latest(catalog: str, specs: Specs, ref: str, alias: str) -> str:
    """Latest non-null value of <table>.<column> per golden client (ordered by the table's date + keys)."""
    table, col = ref.rsplit(".", 1)
    spec = specs.tables[table]
    order = ", ".join(q(c) for c in ([spec.date_column] if spec.date_column else []) + spec.keys)
    return (f"{alias} AS (SELECT golden_client_id, max_by(CAST({q(col)} AS STRING), struct({order})) AS v "
            f"FROM {catalog}.silver.{table} WHERE golden_client_id IS NOT NULL AND {q(col)} IS NOT NULL "
            f"GROUP BY golden_client_id)")


def attribute_sql(catalog: str, specs: Specs, priority: Sequence[str]) -> str:
    """One row per current golden client: survived key attributes, where each came from, has_<attr>
    flags and the completeness share (golden -> records -> facts -> group, per attribute_sources)."""
    rank = "CASE r.source_system " + " ".join(f"WHEN '{s}' THEN {i}" for i, s in enumerate(priority)) + " END"
    attrs = specs.attribute_sources
    rec_items, ctes, joins, outs = [], [], [], []
    for a, cfg in attrs.items():
        if "records" in cfg:
            c = cfg["records"]
            rec_items.append(f"min_by(CAST(r.{c} AS STRING), __rank) FILTER (WHERE r.{c} IS NOT NULL) AS {a}__rv")
            rec_items.append(f"min_by(r.source_system, __rank) FILTER (WHERE r.{c} IS NOT NULL) AS {a}__rs")
        for i, ref in enumerate(cfg.get("facts") or []):
            ctes.append(_fact_latest(catalog, specs, ref, f"f_{a}_{i}"))
            joins.append(f"LEFT JOIN f_{a}_{i} ON f_{a}_{i}.golden_client_id = g.golden_client_id")
    ctes.insert(0, f"""rec AS (
  SELECT x.golden_client_id, r.*, {rank} AS __rank
  FROM {catalog}.silver.client_source_record r
  JOIN {catalog}.silver.xref_client_source x ON x.source_system = r.source_system AND x.source_id = r.source_id)""")
    ctes.insert(1, "recs AS (SELECT golden_client_id, " + ", ".join(rec_items or ["1 AS __none"]) +
                " FROM rec r GROUP BY golden_client_id)")
    direct, src = {}, {}
    for a, cfg in attrs.items():
        vals, srcs = [], []
        if "golden" in cfg:
            vals.append(f"CAST(g.{q(cfg['golden'])} AS STRING)")
            srcs.append(("g." + q(cfg["golden"]), "'golden_identity'"))
        if "records" in cfg:
            vals.append(f"recs.{a}__rv")
            srcs.append((f"recs.{a}__rv", f"recs.{a}__rs"))
        for i, ref in enumerate(cfg.get("facts") or []):
            vals.append(f"f_{a}_{i}.v")
            srcs.append((f"f_{a}_{i}.v", f"'{ref.rsplit('.', 1)[0]}'"))
        direct[a] = f"coalesce({', '.join(vals)})" if len(vals) > 1 else vals[0]
        src[a] = "CASE " + " ".join(f"WHEN {v} IS NOT NULL THEN {s}" for v, s in srcs) + " END"
    base = (f"base AS (SELECT g.golden_client_id, g.client_group_id AS __group, g.n_source_records, "
            f"g.source_systems_present, " + ", ".join(f"{direct[a]} AS {a}__d, {src[a]} AS {a}__s" for a in attrs) +
            f" FROM {catalog}.silver.client_golden_identity g LEFT JOIN recs ON recs.golden_client_id = g.golden_client_id "
            + " ".join(joins) + ")")
    ctes.append(base)
    final = []
    for a, cfg in attrs.items():
        name = cfg.get("as", a)
        if cfg.get("group_fallback"):
            ctes.append(f"grp_{a} AS (SELECT __group, v FROM (SELECT __group, v, row_number() OVER "
                        f"(PARTITION BY __group ORDER BY n DESC, v) AS rn FROM (SELECT __group, {a}__d AS v, count(*) AS n "
                        f"FROM base WHERE __group IS NOT NULL AND {a}__d IS NOT NULL GROUP BY __group, {a}__d)) WHERE rn = 1)")
            joins_g = f"LEFT JOIN grp_{a} ON grp_{a}.__group = b.__group"
            val, srce = f"coalesce(b.{a}__d, grp_{a}.v)", f"CASE WHEN b.{a}__d IS NOT NULL THEN b.{a}__s WHEN grp_{a}.v IS NOT NULL THEN 'client_group' END"
        else:
            joins_g, val, srce = "", f"b.{a}__d", f"b.{a}__s"
        final.append((a, name, val, srce, joins_g))
    sel = ["b.golden_client_id", "b.__group AS client_group_id", "b.n_source_records", "b.source_systems_present"]
    for a, name, val, srce, _ in final:
        if name != "client_group_id":
            sel.append(f"{val} AS {q(name)}")
        sel.append(f"{srce} AS {q(name + '_source')}")
        sel.append(f"{val} IS NOT NULL AS {q('has_' + a)}")
    flags = [f"CASE WHEN {val} IS NOT NULL THEN 1 ELSE 0 END" for _, _, val, _, _ in final]
    sel.append(f"CAST({' + '.join(flags)} AS INT) AS n_attributes_populated")
    sel.append(f"{len(final)} AS n_attributes")
    sel.append(f"round(({' + '.join(flags)}) / {float(len(final))}, 4) AS attribute_completeness_pct")
    sel.append("filter(array(" + ", ".join(f"CASE WHEN {val} IS NULL THEN '{a}' END" for a, _, val, _, _ in final)
               + "), x -> x IS NOT NULL) AS missing_attributes")
    return ("WITH " + ",\n".join(ctes) + "\nSELECT " + ",\n       ".join(sel) + "\nFROM base b " +
            " ".join(j for *_, j in final if j))
