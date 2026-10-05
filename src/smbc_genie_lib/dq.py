"""Data-quality rules, evaluation SQL, scores and DQ history (Phase 4b; PLAN §6, §11; brief §5.11).

Rules come from two places (5 dimensions: completeness, validity, uniqueness, timeliness, consistency):
  generated     config/table_specs.yaml, one rule per table and check type: key_present and
                golden_key / group_key (blocking), key_unique (de-duplicated), not_future and
                parent_exists (blocking), valid_values (type conformance + enums + code lists + ranges),
                required, date_order, references (soft foreign keys), freshness
  hand-written  config/dq_rules.yaml: cross-table consistency (drawn <= 1.1 x limit, trade events replay
                to outstanding, cash-flow actuals tie to payments, KYC stages contiguous, ...), process
                timeliness (KYC reviews, spreading, share refresh lag) and the identity-feed checks

A rule is a pass expression (true = pass, NULL = not applicable) over a relation, evaluated as one
aggregate: total rows, failed rows = count_if(NOT coalesce(pass, true)), five sorted sample keys.
Composite rules (valid_values, date_order, ...) also report failures per part (the result `detail`).
Generated rules are batched per table and measured on two layers: `bronze` = the typed landing before
de-duplication and quarantine (dirtiness), `silver` = the published table.
History: monthly score per layer x table x dimension, simulated deterministically (D08) around the
current run's actual score with the configured incidents, ending in the actual results.
"""
from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import rng
from .silver import Specs, TableSpec, golden_pass, group_pass, key_expr, q

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

DIMENSIONS = ("completeness", "validity", "uniqueness", "timeliness", "consistency")
LAYERS = ("bronze", "silver")
DEFAULT_THRESHOLDS = {"completeness": 0.0, "validity": 0.001, "uniqueness": 0.0, "timeliness": 0.05,
                      "consistency": 0.001}
SAMPLE_KEYS = 5
Part = Tuple[str, str, Tuple[str, ...]]  # (label, pass expression, layers it applies to)


def default_rules_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "dq_rules.yaml"


@dataclass(frozen=True)
class Rule:
    rule_id: str
    table: str                       # logical table: silver name; bronze.<t> for bronze-only rules
    dimension: str
    kind: str
    description: str
    pass_expr: str = "true"          # simple rules: SQL boolean over the relation (true = pass)
    column: str = "*"
    severity: str = "warn"           # error | warn | info
    threshold: float = 0.0           # max failure fraction for status pass
    blocking: bool = False           # failing rows are quarantined
    action: str = "flag"             # quarantine | deduplicate | flag
    layers: Tuple[str, ...] = LAYERS
    origin: str = "generated"        # generated | hand_written
    parts: Tuple[Part, ...] = ()     # composite rules: AND of the parts that apply to the layer
    joins: Tuple[str, ...] = ()      # LEFT JOINs over alias t ({catalog} placeholder) ...
    exposes: Tuple[str, ...] = ()    # ... and the columns they add (alias names start with __)
    grain: str = "row"               # row | table (freshness: one check per table)
    from_sql: Optional[str] = None   # hand-written: FROM clause ({catalog}, {as_of}, thresholds)
    key_sql: Optional[str] = None    # hand-written: sample key expression
    where: Optional[str] = None      # scope: only rows where this holds are evaluated
    storyline: Optional[int] = None

    @property
    def target_layer(self) -> str:
        return "bronze" if self.layers == ("bronze",) else "silver"

    def expr_for(self, layer: str) -> Optional[str]:
        """Pass expression on a layer; None = not evaluated there."""
        if layer not in self.layers:
            return None
        if self.kind == "key_unique" and layer == "bronze":
            return "_dedup_rank = 1"
        if self.parts:
            ps = [f"coalesce({e}, true)" for _, e, ls in self.parts if layer in ls]
            return " AND ".join(ps) if ps else None
        return self.pass_expr

    def parts_for(self, layer: str) -> List[Part]:
        return [p for p in self.parts if layer in p[2]]


# ---- generation from table specs ----------------------------------------------------------------
def _lit(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"


def bound_sql(v: Any, as_of: str) -> str:
    """Range bound literal: numbers, ISO dates, '@as_of' / '@as_of+N' / '@as_of-N' (days)."""
    if isinstance(v, str) and v.startswith("@as_of"):
        d = _dt.date.fromisoformat(as_of) + _dt.timedelta(days=int(v[6:] or 0))
        return f"DATE'{d.isoformat()}'"
    if isinstance(v, str) and len(v) == 10 and v[4] == "-" and v[7] == "-":
        return f"DATE'{v}'"
    return _lit(v)


def range_expr(col: str, lo: Any, hi: Any, as_of: str) -> str:
    parts = [f"{q(col)} {op} {bound_sql(v, as_of)}" for op, v in ((">=", lo), ("<=", hi)) if v is not None]
    return " AND ".join(parts) or "true"


def _range_label(col: str, lo: Any, hi: Any) -> str:
    return f"{col} in [{'-inf' if lo is None else lo}, {'+inf' if hi is None else hi}]"


def _fk_join(col: str, ref: str, alias: str) -> Tuple[str, str]:
    rt, rc = ref.rsplit(".", 1)
    return (f"LEFT JOIN (SELECT DISTINCT CAST({q(rc)} AS STRING) AS __v FROM {{catalog}}.{rt}) {alias}_j "
            f"ON {alias}_j.__v = CAST(t.{q(col)} AS STRING)", f"{alias}_j.__v AS {alias}")


def _rid(table: str, kind: str) -> str:
    return f"{table}:{kind}"


def table_rules(spec: TableSpec, specs: Specs, as_of: str, thresholds: Optional[Dict[str, float]] = None,
                conformance: Optional[str] = None) -> List[Rule]:
    """The generated rules of one silver table (ids <table>:<kind>). `conformance` = the table's
    type-conformance expression over its _raw_ columns (bronze layer only), if any."""
    t, ch, out = spec.name, spec.checks, []
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    keys = ", ".join(spec.keys)
    out.append(Rule(_rid(t, "key_present"), t, "completeness", "key_present",
                    f"Business key ({keys}) is populated; rows without it are quarantined",
                    " AND ".join(f"{q(k)} IS NOT NULL" for k in spec.keys), keys, "error", 0.0, True, "quarantine"))
    out.append(Rule(_rid(t, "key_unique"), t, "uniqueness", "key_unique",
                    f"One row per business key ({keys}); repeated deliveries are de-duplicated (the original "
                    f"delivery, then the earliest ingest, is kept)", f"count(*) = 1 per ({keys})", keys, "error",
                    th["uniqueness"], False, "deduplicate"))
    if spec.client:
        sysname = spec.client.get("via") or spec.client.get("system") or f"per-row {spec.client.get('system_column')}"
        out.append(Rule(_rid(t, "golden_key"), t, "consistency", "golden_key",
                        f"{spec.client['key']} resolves to a golden client ({sysname} -> silver.xref_client_source); "
                        f"unresolved rows are quarantined", golden_pass(spec, specs.conform), spec.client["key"],
                        "error", 0.0, True, "quarantine"))
    if spec.group:
        out.append(Rule(_rid(t, "group_key"), t, "consistency", "group_key",
                        f"{spec.group['key']} is a client group of the global group master; unmastered rows are "
                        f"quarantined", group_pass(spec), spec.group["key"], "error", 0.0, True, "quarantine"))
    nf = ch.get("not_future") or []
    if nf:
        out.append(Rule(_rid(t, "not_future"), t, "validity", "not_future",
                        f"{', '.join(nf)} not after the as-of date ({as_of}); future-dated snapshots are quarantined",
                        " AND ".join(f"{q(c)} <= DATE'{as_of}'" for c in nf), ",".join(nf), "error", 0.0, True,
                        "quarantine"))
    fks, blocking_fk = ch.get("fk") or {}, set(ch.get("fk_blocking") or [])
    for name, cols, blocking in (("parent_exists", [c for c in fks if c in blocking_fk], True),
                                 ("references", [c for c in fks if c not in blocking_fk], False)):
        if not cols:
            continue
        joins, exposes, parts = [], [], []
        for c in cols:
            j, x = _fk_join(c, fks[c], f"__fk_{c}")
            joins.append(j)
            exposes.append(x)
            parts.append((f"{c} in {fks[c]}", f"({q(c)} IS NULL OR __fk_{c} IS NOT NULL)", LAYERS))
        desc = ("Parent record exists: " if blocking else "Referenced records exist: ") + \
            "; ".join(f"{c} -> {fks[c]}" for c in cols) + ("; orphans are quarantined" if blocking else "")
        out.append(Rule(_rid(t, name), t, "consistency", name, desc, column=",".join(cols),
                        severity="error" if blocking else "warn", threshold=0.0 if blocking else th["consistency"],
                        blocking=blocking, action="quarantine" if blocking else "flag",
                        layers=LAYERS if blocking else ("silver",), parts=tuple(parts),
                        joins=tuple(joins), exposes=tuple(exposes)))
    parts: List[Part] = []
    vjoins, vexposes = [], []
    if conformance:
        parts.append(("values parse into their silver types", conformance, ("bronze",)))
    for c, allowed in (ch.get("enum") or {}).items():
        parts.append((f"{c} in ({', '.join(map(str, allowed))})", f"{q(c)} IN ({', '.join(_lit(v) for v in allowed)})", LAYERS))
    for c, ref in (ch.get("enum_ref") or {}).items():
        j, x = _fk_join(c, ref, f"__ref_{c}")
        vjoins.append(j)
        vexposes.append(x)
        parts.append((f"{c} is a code of {ref}", f"({q(c)} IS NULL OR __ref_{c} IS NOT NULL)", LAYERS))
    for c, bounds in (ch.get("range") or {}).items():
        lo, hi = (list(bounds) + [None, None])[:2]
        parts.append((_range_label(c, lo, hi), range_expr(c, lo, hi, as_of), LAYERS))
    if parts:
        out.append(Rule(_rid(t, "valid_values"), t, "validity", "valid_values",
                        "Valid values: " + "; ".join(p[0] for p in parts), column="*", severity="warn",
                        threshold=float(ch.get("validity_threshold", th["validity"])), parts=tuple(parts),
                        joins=tuple(vjoins), exposes=tuple(vexposes)))
    req = ch.get("required") or []
    if req:
        out.append(Rule(_rid(t, "required"), t, "completeness", "required",
                        f"Required attributes populated: {', '.join(req)}",
                        " AND ".join(f"{q(c)} IS NOT NULL" for c in req), ",".join(req), "warn",
                        float(ch.get("required_threshold", 0.001))))
    pairs = ch.get("date_order") or []
    if pairs:
        dparts = tuple((f"{a} <= {b}", f"{q(a)} <= {q(b)}", LAYERS) for a, b in pairs)
        out.append(Rule(_rid(t, "date_order"), t, "consistency", "date_order",
                        "Logical order: " + "; ".join(p[0] for p in dparts), column=",".join(sorted({c for p in pairs for c in p})),
                        severity="warn", threshold=th["consistency"], parts=dparts))
    fresh = ch.get("fresh")
    col = (fresh or {}).get("column", spec.date_column)
    if fresh and col:
        if "min" in fresh:
            lim, desc = _lit(fresh["min"]), f"latest {col} is at least {fresh['min']}"
        else:
            d = _dt.date.fromisoformat(as_of) - _dt.timedelta(days=int(fresh.get("lag_days", 0)))
            lim, desc = f"DATE'{d.isoformat()}'", f"latest {col} within {fresh.get('lag_days', 0)} days of the as-of date"
        out.append(Rule(_rid(t, "freshness"), t, "timeliness", "freshness", f"Table is current: {desc}",
                        f"max({q(col)}) >= {lim}", col, "warn", 0.0, grain="table"))
    return out


def generate_rules(specs: Specs, as_of: str, thresholds: Optional[Dict[str, float]] = None,
                   conformance: Optional[Dict[str, Optional[str]]] = None) -> List[Rule]:
    rules = []
    for name, spec in specs.tables.items():
        rules += table_rules(spec, specs, as_of, thresholds, (conformance or {}).get(name))
    return rules


def hand_rules(cfg: Dict[str, Any], thresholds: Optional[Dict[str, float]] = None) -> List[Rule]:
    """Hand-written rules from config/dq_rules.yaml (`rules:`), with `expand:` templates applied."""
    th = {**DEFAULT_THRESHOLDS, **(cfg.get("thresholds") or {}), **(thresholds or {})}
    out = []
    for r in expand_templates(cfg):
        dim = r["dimension"]
        if dim not in DIMENSIONS:
            raise ValueError(f"{r['id']}: unknown dimension {dim}")
        out.append(Rule(rule_id=r["id"], table=r["table"], dimension=dim, kind=r.get("kind", "cross_table"),
                        description=r["description"], pass_expr=r["pass"], column=r.get("column", "*"),
                        severity=r.get("severity", "warn"), threshold=float(r.get("threshold", th[dim])),
                        layers=(r.get("layer", "silver"),), origin="hand_written", from_sql=r["from"],
                        key_sql=r.get("key", "'*'"), where=r.get("where"), storyline=r.get("storyline")))
    return out


def expand_templates(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Rules, plus one copy of each template rule per `for_each` item ({item} / {<field>} substituted)."""
    out = list(cfg.get("rules") or [])
    for tpl in cfg.get("templates") or []:
        for item in tpl["for_each"]:
            subs = item if isinstance(item, dict) else {"item": item}
            out.append({k: _substitute(v, subs) for k, v in tpl["rule"].items()})
    return out


def _substitute(v: Any, subs: Dict[str, Any]) -> Any:
    if not isinstance(v, str):
        return v
    for k, x in subs.items():
        v = v.replace("{" + k + "}", str(x))
    return v


def load_rules_config(path: Optional[Path] = None) -> Dict[str, Any]:
    return yaml.safe_load(Path(path or default_rules_path()).read_text())


def check_catalogue(rules: Sequence[Rule]) -> None:
    seen: Set[str] = set()
    for r in rules:
        if r.rule_id in seen:
            raise ValueError(f"duplicate rule id {r.rule_id}")
        seen.add(r.rule_id)
        if r.dimension not in DIMENSIONS:
            raise ValueError(f"{r.rule_id}: unknown dimension {r.dimension}")


def blocking_rules(rules: Sequence[Rule], table: str) -> List[Rule]:
    return [r for r in rules if r.table == table and r.blocking and r.origin == "generated"]


def dimension_counts(rules: Sequence[Rule]) -> Dict[str, int]:
    return {d: sum(1 for r in rules if r.dimension == d) for d in DIMENSIONS}


# ---- evaluation SQL -----------------------------------------------------------------------------
def fill(s: Optional[str], params: Dict[str, Any]) -> Optional[str]:
    """Substitute {catalog}, {as_of} and threshold placeholders."""
    if s is None:
        return None
    for k, v in params.items():
        s = s.replace("{" + k + "}", str(v))
    return s


def _sample(cond: str, key: str) -> str:
    return f"slice(array_sort(collect_list(CASE WHEN {cond} THEN {key} END)), 1, {SAMPLE_KEYS})"


def batch_sql(relation: str, keys: Sequence[str], rules: Sequence[Rule], layer: str, catalog: str,
              have_cols: Iterable[str] = (), extra: Sequence[str] = ()) -> str:
    """One aggregate over a table for its row-grain rules on a layer: __total, then per rule i
    f_i (failed), n_i (evaluated), s_i (samples) and p_i_j (failures per composite part), plus any
    `extra` aggregate items."""
    have = set(have_cols)
    joins, exposes = [], []
    for r in rules:
        for j, x in zip(r.joins, r.exposes):
            alias = x.split(" AS ")[-1].strip()
            if alias not in have and x not in exposes:
                joins.append(j.replace("{catalog}", catalog))
                exposes.append(x)
    items = ["count(*) AS __total"]
    for i, r in enumerate(rules):
        expr = r.expr_for(layer)
        cond = f"NOT coalesce({expr}, true)"
        scope = f"coalesce({r.where}, false)" if r.where else None
        cond_s = f"({scope}) AND {cond}" if scope else cond
        items += [f"count_if({cond_s}) AS f_{i}", f"count_if({scope}) AS n_{i}" if scope else f"count(*) AS n_{i}",
                  f"{_sample(cond_s, '__key')} AS s_{i}"]
        items += [f"count_if(NOT coalesce({e}, true)) AS p_{i}_{j}" for j, (_, e, _) in enumerate(r.parts_for(layer))]
    items += list(extra)
    inner = (f"SELECT t.*, {key_expr(keys, 't')} AS __key{''.join(', ' + e for e in exposes)} "
             f"FROM {relation} t {' '.join(joins)}")
    return f"SELECT {', '.join(items)} FROM ({inner}) s"


def unique_sql(relation: str, keys: Sequence[str]) -> str:
    """Silver-layer uniqueness: rows beyond the first per key (+ sample keys)."""
    k = ", ".join(q(c) for c in keys)
    return (f"SELECT coalesce(sum(n), 0) AS total, coalesce(sum(n - 1), 0) AS failed, "
            f"{_sample('n > 1', '__key')} AS samples FROM (SELECT {key_expr(keys)} AS __key, count(*) AS n "
            f"FROM {relation} GROUP BY {k})")


def table_rule_sql(relation: str, r: Rule) -> str:
    """Table-grain rule (freshness): one check; the sample is the observed value."""
    obs = r.pass_expr.split(">=")[0].strip()
    return (f"SELECT 1 AS total, CASE WHEN coalesce({r.pass_expr}, false) THEN 0 ELSE 1 END AS failed, "
            f"array(CAST({obs} AS STRING)) AS samples FROM {relation}")


def hand_sql(r: Rule, params: Dict[str, Any]) -> str:
    """Hand-written rule: aggregate over its own FROM clause."""
    cond = f"NOT coalesce({fill(r.pass_expr, params)}, true)"
    where = f" WHERE {fill(r.where, params)}" if r.where else ""
    key = f"CAST({fill(r.key_sql, params)} AS STRING)"
    return (f"SELECT count(*) AS total, count_if({cond}) AS failed, {_sample(cond, key)} AS samples "
            f"FROM {fill(r.from_sql, params)}{where}")


# ---- results + scores ---------------------------------------------------------------------------
def status(failed: int, total: int, threshold: float, severity: str) -> str:
    """pass within threshold; else fail (severity error) or warn; info rules are measured, never failed."""
    if total == 0 or failed == 0 or failed / total <= threshold or severity == "info":
        return "pass"
    return "fail" if severity == "error" else "warn"


def result_row(r: Rule, layer: str, table_name: str, total: int, failed: int, samples: Optional[List[str]],
               run_id: str, run_ts: _dt.datetime, detail: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    total, failed = int(total or 0), int(failed or 0)
    return {"run_id": run_id, "run_ts": run_ts, "rule_id": r.rule_id, "layer": layer, "table_name": table_name,
            "column_name": r.column, "dimension": r.dimension, "rule_kind": r.kind, "severity": r.severity,
            "is_blocking": r.blocking, "action": r.action if failed else "none", "total_rows": total,
            "failed_rows": failed, "pass_rate": 1.0 if total == 0 else 1.0 - failed / total,
            "threshold": r.threshold, "status": status(failed, total, r.threshold, r.severity),
            "sample_keys": [str(s) for s in (samples or [])][:SAMPLE_KEYS],
            "detail": json.dumps(detail, sort_keys=True) if detail else None, "storyline": r.storyline}


def scores(results: Iterable[Dict[str, Any]]) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    """(layer, table, dimension) -> score = mean rule pass rate, rule and row counts."""
    acc: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for r in results:
        k = (r["layer"], r["table_name"], r["dimension"])
        a = acc.setdefault(k, {"rules": 0, "failed_rules": 0, "rows": 0, "failed_rows": 0, "rate_sum": 0.0})
        a["rules"] += 1
        a["failed_rules"] += r["status"] != "pass"
        a["rows"] += r["total_rows"]
        a["failed_rows"] += r["failed_rows"]
        a["rate_sum"] += r["pass_rate"]
    for a in acc.values():
        a["score"] = a["rate_sum"] / a["rules"]
    return acc


# ---- DQ history ---------------------------------------------------------------------------------
def month_starts(start: str, end: str) -> List[_dt.date]:
    y, m = map(int, start.split("-")[:2])
    ey, em = map(int, end.split("-")[:2])
    out = []
    while (y, m) <= (ey, em):
        out.append(_dt.date(y, m, 1))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _ym(s: str) -> Tuple[int, int]:
    y, m = str(s).split("-")[:2]
    return int(y), int(m)


def incident_for(incidents: Sequence[Dict[str, Any]], layer: str, table: str, dim: str, month: _dt.date,
                 latest: _dt.date) -> Optional[Dict[str, Any]]:
    for i in incidents:
        if dim != i["dimension"] or table not in i["tables"] or (i.get("layers") and layer not in i["layers"]):
            continue
        hi = (latest.year, latest.month) if i.get("to", "latest") == "latest" else _ym(i["to"])
        if _ym(i["from"]) <= (month.year, month.month) <= hi:
            return i
    return None


def simulate_history(seed: int, actual: Dict[Tuple[str, str, str], Dict[str, Any]], months: List[_dt.date],
                     starts: Dict[str, _dt.date], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Monthly layer x table x dimension scores. Before the latest month: the current actual score
    (+ any base_offset) minus a gap that closes over the history (the DQ programme matures) and a small
    deterministic wiggle, floored; or the configured incident value; the latest month is the actual."""
    base = cfg.get("baseline", {})
    gap_lo, gap_hi = base.get("initial_gap", [0.004, 0.025])
    noise_sd, floor, cap = base.get("noise_sd", 0.0015), base.get("floor", 0.90), base.get("cap", 0.9995)
    incidents, latest, n = cfg.get("incidents", []), months[-1], len(months)
    rows = []
    for (layer, table, dim), a in sorted(actual.items()):
        logical = table.split(".")[-1]
        first = max(starts.get(logical, months[0]), months[0]).replace(day=1)
        gap0 = gap_lo + (gap_hi - gap_lo) * rng.unit(seed, "dqgap", layer, logical, dim)
        mine = [i for i in incidents if i["dimension"] == dim and logical in i["tables"]
                and (not i.get("layers") or layer in i["layers"])]
        anchor = min(cap, a["score"] + max([float(i.get("base_offset", 0.0)) for i in mine] or [0.0]))
        for idx, m in enumerate(months):
            if m < first:
                continue
            inc = incident_for(mine, layer, logical, dim, m, latest)
            u = rng.unit(seed, "dqinc", layer, logical, dim, m.isoformat())
            if m == latest:
                score, basis = a["score"], "actual"
            elif inc and inc.get("anchor") == "actual":
                wiggle = rng.normal(seed, 0.0, noise_sd / 3, "dqinc", layer, logical, dim, m.isoformat())
                score, basis = min(cap, max(0.0, a["score"] + wiggle)), "incident"
            elif inc and "delta" in inc:  # deliberate dips are not floored
                lo, hi = inc["delta"]
                score, basis = min(cap, max(0.0, a["score"] + lo + (hi - lo) * u)), "incident"
            elif inc:
                lo, hi = inc["score"]
                score, basis = lo + (hi - lo) * u, "incident"
            else:
                drift = gap0 * (1.0 - idx / max(n - 1, 1)) ** 1.5
                wiggle = abs(rng.normal(seed, 0.0, noise_sd, "dqnoise", layer, logical, dim, m.isoformat()))
                # the floor never lifts history above a low actual (e.g. a 0.68 process SLA): no false drop
                lo = min(floor, anchor - gap_hi - 3 * noise_sd)
                score, basis = min(cap, max(lo, anchor - drift - wiggle)), "simulated"
            span = max(1, (latest.year - first.year) * 12 + latest.month - first.month + 1)
            elapsed = ((m.year - first.year) * 12 + m.month - first.month + 1) / span
            evaluated = a["rows"] if basis == "actual" else max(1, round(a["rows"] * elapsed))
            rows.append({
                "month": m, "layer": layer, "table_name": table, "dimension": dim, "score": float(score),
                "rules_evaluated": a["rules"],
                "rules_failed": int(a["failed_rules"] if basis == "actual" else
                                    round(a["rules"] * min(1.0, max(0.0, (0.995 - score) / 0.03)))),
                "rows_evaluated": int(evaluated),
                "rows_failed": int(a["failed_rows"] if basis == "actual" else round(evaluated * (1.0 - score))),
                "basis": basis, "incident_key": inc["key"] if inc and basis != "actual" else None,
                "incident_note": inc["note"] if inc and basis != "actual" else None,
                "storyline": inc.get("storyline") if inc and basis != "actual" else None})
    return rows


# ---- catalogue rows (ops.dq_rules) --------------------------------------------------------------
def rule_table_name(r: Rule, specs: Specs) -> str:
    if r.origin == "generated":
        return specs.tables[r.table].target
    return r.table if "." in r.table else f"{r.target_layer}.{r.table}"


def catalogue_rows(rules: Sequence[Rule], specs: Specs, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for r in rules:
        expr = r.pass_expr if r.kind == "key_unique" else (r.expr_for("silver") or r.expr_for("bronze"))
        out.append({"rule_id": r.rule_id, "layer": r.target_layer, "table_name": rule_table_name(r, specs),
                    "column_name": r.column, "dq_dimension": r.dimension, "rule_expr": fill(expr, params),
                    "severity": r.severity, "threshold": r.threshold, "description": r.description,
                    "rule_kind": r.kind, "origin": r.origin, "is_blocking": r.blocking, "action_on_fail": r.action,
                    "evaluated_layers": ",".join(r.layers), "rule_grain": r.grain,
                    "rule_parts": [p[0] for p in r.parts] or None, "rule_from": fill(r.from_sql, params),
                    "rule_where": fill(r.where, params), "storyline": r.storyline})
    return out
