"""Metric-view framework (Phase 6; PLAN §8 / §10; DECISIONS D14, D16, D31-D35): YAML spec -> warehouse YAML -> DDL.

Every metric view is one file ``metrics/<view>.yaml``. Its keys are of two kinds:

* **warehouse keys** - sent to Databricks as metric-view YAML 1.1: ``comment``, ``source``, ``filter``,
  ``joins``, ``dimensions`` (alias ``fields``) and ``measures`` (each field / measure: ``name``, ``expr``,
  ``comment``, ``display_name``, ``synonyms``, ``format``; measures also ``window``);
* **runner-only keys** - stripped before the YAML leaves this module: ``view`` (= file stem), ``space``
  (Genie space slug, brief Appendix D), ``grain``, ``blocks`` (shared blocks + their parameters),
  ``overrides`` (patch comment / synonyms / display_name / format of a block field), ``validate``,
  ``reconcile``, ``glossary``, ``notes``, ``client_block_exempt`` / ``calendar_block_exempt`` (reasons);
  per field / measure ``unit`` and ``caveat`` (glossary), per join nothing.

Shared blocks live in ``metrics/_blocks/<block>.yaml`` (``calendar``, ``client``, ``client_group``): joins and
dimensions with ``${param}`` placeholders filled from the view's ``blocks:`` mapping; calendar fields carry a
runner-only ``grains:`` filter (day | month | fiscal_year) and every block field may carry ``requires:``.
Expansion order is calendar block -> client block -> view fields, so fields may reference earlier fields by
name (the calendar hierarchy is derived from the window order field by name, D33 - the docs warn that a
hierarchy defined on the source column breaks window grouping).

Global placeholders (rendered last): ``${catalog}``, ``${as_of_date}``, ``${as_of_month}``,
``${as_of_fiscal_year}`` (FY2026), ``${as_of_fiscal_year_no}``, ``${fy_start_month}``, ``${history_start}`` and
every config threshold (``${rorwa_hurdle}``, ...). An unknown placeholder fails.

Pure Python (pyyaml only); unit-tested in tests/unit/test_metrics.py. The runner is src/40_metrics/run_metrics.py.
"""
from __future__ import annotations

import copy
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
METRICS_DIR = REPO / "metrics"
BLOCKS_DIR = METRICS_DIR / "_blocks"
ANSWERS_DIR = METRICS_DIR / "_answers"
GLOSSARY_PATH = METRICS_DIR / "_glossary.md"
SCHEMA = "metrics"
YAML_VERSION = 1.1

# Genie spaces (brief §5, Appendix D slugs) and the 43 views of PLAN §8 (D14) with their owning space.
SPACES: Dict[str, Tuple[int, str]] = {
    "account_planning": (1, "APAC Genie - Account Planning"),
    "opportunity_identification": (2, "APAC Genie - Opportunity Identification"),
    "credit_memo_financial_spreading": (3, "APAC Genie - Credit Memo & Financial Spreading"),
    "early_warning_monitoring": (4, "APAC Genie - Early Warning Monitoring"),
    "client_profitability_roe": (5, "APAC Genie - Client Profitability & ROE"),
    "client_onboarding_kyc": (6, "APAC Genie - Client Onboarding & KYC"),
    "tb_cash_payments_liquidity": (7, "APAC Genie - Transactional Banking: Cash, Payments & Liquidity"),
    "tb_trade_scf": (8, "APAC Genie - Transactional Banking: Trade & Supply Chain Finance"),
    "cashflow_forecasting": (9, "APAC Genie - Cashflow Forecasting"),
    "signals_sentiment": (10, "APAC Genie - Signals & Sentiment"),
    "customer360_data_foundation": (11, "APAC Genie - Customer 360 Data Foundation"),
}
PLAN_VIEWS: Dict[str, str] = {
    **{v: "account_planning" for v in ("mv_account_plan_progress", "mv_relationship_footprint",
                                       "mv_global_group_relationship", "mv_rm_engagement")},
    **{v: "opportunity_identification" for v in ("mv_opportunity_signals", "mv_next_best_product", "mv_pipeline",
                                                 "mv_product_penetration")},
    **{v: "credit_memo_financial_spreading" for v in ("mv_financial_spreads", "mv_financial_ratios_vs_peers",
                                                      "mv_facilities_covenants_collateral", "mv_credit_review_workflow")},
    **{v: "early_warning_monitoring" for v in ("mv_ews_scores", "mv_ews_signals", "mv_watchlist", "mv_delinquency")},
    **{v: "client_profitability_roe" for v in ("mv_client_profitability", "mv_roe_waterfall", "mv_deal_pricing")},
    **{v: "client_onboarding_kyc" for v in ("mv_onboarding_funnel", "mv_onboarding_cycle_time", "mv_kyc_health")},
    **{v: "tb_cash_payments_liquidity" for v in ("mv_tb_deposits", "mv_tb_payments", "mv_tb_liquidity_structures",
                                                 "mv_tb_channel_adoption")},
    **{v: "tb_trade_scf" for v in ("mv_tb_trade_finance", "mv_trade_operations", "mv_tb_scf", "mv_trade_corridors")},
    **{v: "cashflow_forecasting" for v in ("mv_client_cashflow", "mv_cashflow_forecast", "mv_forecast_accuracy",
                                           "mv_liquidity_events")},
    **{v: "signals_sentiment" for v in ("mv_news_sentiment", "mv_internal_sentiment", "mv_market_signals",
                                        "mv_signal_feed")},
    **{v: "customer360_data_foundation" for v in ("mv_entity_resolution", "mv_golden_record_coverage", "mv_data_quality",
                                                  "mv_delta_sharing_freshness", "mv_er_exposure_impact")},
}

WAREHOUSE_TOP_KEYS = ("comment", "source", "filter", "joins", "dimensions", "measures")
RUNNER_TOP_KEYS = {"view", "space", "grain", "blocks", "overrides", "validate", "reconcile", "glossary", "notes",
                   "client_block_exempt", "calendar_block_exempt", "also_in"}
FIELD_KEYS = {"name", "expr", "comment", "display_name", "synonyms", "format"}
MEASURE_KEYS = FIELD_KEYS | {"window"}
FIELD_RUNNER_KEYS = {"unit", "caveat"}
BLOCK_FIELD_RUNNER_KEYS = {"grains", "requires"}
JOIN_KEYS = {"name", "source", "on", "using", "joins", "cardinality", "rely"}
WINDOW_KEYS = {"order", "range", "semiadditive", "offset"}
FORMAT_TYPES = {"number", "currency", "percentage", "byte", "date", "date_time"}
FORMAT_KEYS = {"type", "currency_code", "decimal_places", "hide_group_separator", "abbreviation", "date_format",
               "time_format", "leading_zeros"}
DECIMAL_TYPES = {"max", "exact", "all"}
ABBREVIATIONS = {"none", "compact", "scientific"}
GRAINS = ("day", "month", "fiscal_year")
CLIENT_BLOCKS = ("client", "client_group")
MAX_SYNONYMS, MAX_LABEL = 10, 255
DEFAULT_TOLERANCE = 0.0001          # 0.01% relative (PLAN §8)
ARRAY_COLUMNS_FALLBACK = {"aliases", "source_systems_present"}   # dim_client ARRAY columns (D31)

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 %&()/+.\-]*$")
_VIEW_RE = re.compile(r"^mv_[a-z0-9_]+$")
_PLACEHOLDER_RE = re.compile(r"\$\{(\w+)\}")
_SQL_REF_RE = re.compile(r"\$\{catalog\}\.(\w+)\.(\w+)\b(?!\s*\()", re.I)
_LITERAL_CATALOG_RE = re.compile(r"\bsmbc_genie\.(bronze|silver|shared|gold|ops|metrics)\.", re.I)
_MEASURE_REF_RE = re.compile(r"MEASURE\s*\(\s*(`[^`]+`|[A-Za-z_][A-Za-z0-9_]*)\s*\)", re.I)
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_RANGE_RE = re.compile(r"^(current|cumulative|all|(trailing|leading)\s+\d+(\s+(day|month|year))?(\s+(inclusive|exclusive))?)$")
_OFFSET_RE = re.compile(r"^-?\d+(\s+(day|month|year))?$")
_AGG_RE = re.compile(r"\b(SUM|COUNT|AVG|MIN|MAX|MEDIAN|PERCENTILE|PERCENTILE_APPROX|PERCENTILE_CONT|STDDEV|STDDEV_POP|"
                     r"STDDEV_SAMP|VARIANCE|COUNT_IF|ANY_VALUE|FIRST|LAST|MEASURE|APPROX_COUNT_DISTINCT|BOOL_OR|BOOL_AND|"
                     r"MAX_BY|MIN_BY|COLLECT_SET|CORR|COVAR_POP|SKEWNESS|KURTOSIS|MODE)\s*\(", re.I)
_FORBIDDEN_FN_RE = re.compile(r"\b(current_date|current_timestamp|now|getdate|curdate)\s*\(", re.I)


class MetricSpecError(ValueError):
    """A malformed view spec, block or answers file."""


# ---- small helpers ----------------------------------------------------------------------------------
def sql_str(text: str) -> str:
    """SQL string literal with backslash escapes (Databricks concatenates adjacent literals: 'it''s' -> its)."""
    return "'" + str(text).replace("\\", "\\\\").replace("'", "\\'") + "'"


def q(name: str) -> str:
    return f"`{name}`"


def one_line(text: Any) -> str:
    return " ".join(str(text or "").split())


def _fix_on_key(d: Any) -> Any:
    """PyYAML (YAML 1.1) reads an unquoted ``on:`` key as boolean True - map it back to 'on' recursively."""
    if isinstance(d, list):
        return [_fix_on_key(x) for x in d]
    if isinstance(d, dict):
        return {("on" if k is True else k): _fix_on_key(v) for k, v in d.items()}
    return d


def load_yaml(path: Path) -> Any:
    if yaml is None:  # pragma: no cover
        raise RuntimeError("pyyaml is required")
    try:
        return _fix_on_key(yaml.safe_load(Path(path).read_text()))
    except yaml.YAMLError as e:
        raise MetricSpecError(f"{path}: invalid YAML: {str(e).splitlines()[0]}") from e


def substitute(obj: Any, params: Mapping[str, Any], strict: bool = True, only: Optional[Set[str]] = None) -> Any:
    """${name} substitution in every string of a nested structure. With `only`, other placeholders are kept
    (block params first, globals later); with `strict`, an unknown placeholder raises."""
    def sub(m: "re.Match[str]") -> str:
        key = m.group(1)
        if only is not None and key not in only:
            return m.group(0)
        if key not in params:
            if strict:
                raise MetricSpecError(f"unknown placeholder ${{{key}}}")
            return m.group(0)
        return str(params[key])

    if isinstance(obj, str):
        return _PLACEHOLDER_RE.sub(sub, obj)
    if isinstance(obj, list):
        return [substitute(x, params, strict, only) for x in obj]
    if isinstance(obj, dict):
        return {k: substitute(v, params, strict, only) for k, v in obj.items()}
    return obj


def build_params(cfg: Any = None, catalog: Optional[str] = None) -> Dict[str, Any]:
    """Global placeholders from config/smbc_genie.yaml (as-of 2026-09-30, D16; JP fiscal year, D17)."""
    if cfg is None:
        from .config import load_config
        cfg = load_config()
    as_of = _dt.date.fromisoformat(str(cfg.as_of_date)[:10])
    fy_start = int(cfg.raw.get("fiscal_year_start_month", 4))
    fy = as_of.year if as_of.month >= fy_start else as_of.year - 1
    p: Dict[str, Any] = {
        "catalog": catalog or cfg.catalog, "as_of_date": as_of.isoformat(), "as_of_month": as_of.replace(day=1).isoformat(),
        "as_of_fiscal_year": f"FY{fy}", "as_of_fiscal_year_no": fy, "fy_start_month": fy_start,
        "history_start": cfg.history_start,
    }
    for k, v in cfg.thresholds.items():
        p[k] = v
    return p


# ---- spec model ------------------------------------------------------------------------------------
@dataclass
class Block:
    name: str
    comment: str
    params: Dict[str, Dict[str, Any]]
    joins: List[Dict[str, Any]]
    dimensions: List[Dict[str, Any]]
    path: Optional[Path] = None


@dataclass
class ViewSpec:
    name: str
    space: str
    comment: str
    grain: str
    raw: Dict[str, Any]
    joins: List[Dict[str, Any]] = field(default_factory=list)
    dimensions: List[Dict[str, Any]] = field(default_factory=list)
    measures: List[Dict[str, Any]] = field(default_factory=list)
    block_fields: Dict[str, str] = field(default_factory=dict)      # field name -> block it came from
    validate: Dict[str, Any] = field(default_factory=dict)
    reconcile: List[Dict[str, Any]] = field(default_factory=list)
    glossary: Dict[str, Any] = field(default_factory=dict)
    path: Optional[Path] = None

    @property
    def source(self) -> str:
        return str(self.raw.get("source", ""))

    @property
    def filter(self) -> Optional[str]:
        return self.raw.get("filter")

    @property
    def field_names(self) -> List[str]:
        return [d["name"] for d in self.dimensions]

    @property
    def measure_names(self) -> List[str]:
        return [m["name"] for m in self.measures]

    def fqn(self, catalog: str) -> str:
        return f"{catalog}.{SCHEMA}.{self.name}"

    @property
    def full_comment(self) -> str:
        c = one_line(self.comment)
        if self.grain and "grain" not in c.lower():
            c = f"{c.rstrip('. ')}. Grain: {one_line(self.grain).rstrip('.')}."
        return c

    def measure(self, name: str) -> Dict[str, Any]:
        for m in self.measures:
            if m["name"] == name:
                return m
        raise KeyError(name)


# ---- blocks ----------------------------------------------------------------------------------------
def parse_block(data: Any, path: Optional[Path] = None) -> Block:
    if not isinstance(data, Mapping):
        raise MetricSpecError(f"{path}: block must be a mapping")
    unknown = set(map(str, data)) - {"block", "comment", "params", "joins", "dimensions"}
    if unknown:
        raise MetricSpecError(f"{path}: unknown block keys {sorted(unknown)}")
    name = str(data.get("block") or "")
    if path is not None and Path(path).stem != name:
        raise MetricSpecError(f"{path}: `block: {name}` must match the file name")
    params = {str(k): (dict(v) if isinstance(v, Mapping) else {"default": v}) for k, v in (data.get("params") or {}).items()}
    return Block(name=name, comment=one_line(data.get("comment")), params=params,
                 joins=list(data.get("joins") or []), dimensions=list(data.get("dimensions") or []), path=path)


def load_blocks(blocks_dir: Path = BLOCKS_DIR) -> Dict[str, Block]:
    out = {}
    for p in sorted(Path(blocks_dir).glob("*.yaml")):
        b = parse_block(load_yaml(p), p)
        out[b.name] = b
    return out


def _block_params(block: Block, given: Mapping[str, Any], view: str) -> Dict[str, Any]:
    given = dict(given or {})
    unknown = set(given) - set(block.params)
    if unknown:
        raise MetricSpecError(f"{view}: block {block.name} has no params {sorted(unknown)} (allowed: {sorted(block.params)})")
    out: Dict[str, Any] = {}
    for k, meta in block.params.items():
        if k in given and given[k] is not None:
            out[k] = given[k]
        elif "default" in meta:
            out[k] = meta["default"]
        elif meta.get("required"):
            raise MetricSpecError(f"{view}: block {block.name} needs param `{k}` ({meta.get('comment', '')})")
        if k in out and meta.get("allowed") and out[k] not in meta["allowed"]:
            raise MetricSpecError(f"{view}: block {block.name} param {k}={out[k]!r} not in {meta['allowed']}")
    return out


def fiscal_year_no_expr(date_expr: str, fy_start: str = "${fy_start_month}") -> str:
    return f"CASE WHEN MONTH({date_expr}) >= {fy_start} THEN YEAR({date_expr}) ELSE YEAR({date_expr}) - 1 END"


def expand_block(block: Block, given: Mapping[str, Any], view: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(joins, dimensions) of a block for one view: params substituted, grain / requires filters applied,
    runner-only field keys removed."""
    params = _block_params(block, given, view)
    if block.name == "calendar":
        if params.get("grain") == "fiscal_year" and not params.get("fy"):
            if not params.get("date"):
                raise MetricSpecError(f"{view}: calendar block with grain fiscal_year needs `fy` (INT expression) or `date`")
            params["fy"] = fiscal_year_no_expr(str(params["date"]))
        if params.get("grain") in ("day", "month") and not params.get("date"):
            raise MetricSpecError(f"{view}: calendar block with grain {params.get('grain')} needs `date`")
    present = {k for k, v in params.items() if v not in (None, "", "none")}
    names = set(params)
    joins = []
    for j in block.joins:
        req = set(j.get("requires") or [])
        if req - present:
            continue
        jj = {k: v for k, v in j.items() if k != "requires"}
        joins.append(substitute(copy.deepcopy(jj), params, strict=False, only=names))
    dims = []
    grain = params.get("grain")
    for d in block.dimensions:
        grains = d.get("grains")
        if grains and grain not in grains:
            continue
        req = set(d.get("requires") or [])
        if req - present:
            continue
        dd = {k: v for k, v in d.items() if k not in BLOCK_FIELD_RUNNER_KEYS}
        dims.append(substitute(copy.deepcopy(dd), params, strict=False, only=names))
    return joins, dims


# ---- view parsing / expansion ----------------------------------------------------------------------
def parse_view(data: Any, blocks: Mapping[str, Block], path: Optional[Path] = None) -> ViewSpec:
    """Structure + block expansion (no semantic validation: see validate_view)."""
    if not isinstance(data, Mapping):
        raise MetricSpecError(f"{path}: view spec must be a mapping")
    data = dict(data)
    if "fields" in data:            # accepted synonym of dimensions
        if "dimensions" in data:
            raise MetricSpecError(f"{path}: use either `dimensions` or `fields`, not both")
        data["dimensions"] = data.pop("fields")
    unknown = set(map(str, data)) - set(WAREHOUSE_TOP_KEYS) - RUNNER_TOP_KEYS - {"version"}
    if unknown:
        raise MetricSpecError(f"{path}: unknown keys {sorted(unknown)} (warehouse: {list(WAREHOUSE_TOP_KEYS)}; "
                              f"runner-only: {sorted(RUNNER_TOP_KEYS)})")
    name = str(data.get("view") or "")
    if not _VIEW_RE.match(name):
        raise MetricSpecError(f"{path}: `view` must be mv_<lower_snake>, got {name!r}")
    if path is not None and Path(path).stem != name:
        raise MetricSpecError(f"{path}: `view: {name}` must match the file name")
    if "version" in data and str(data["version"]) != str(YAML_VERSION):
        raise MetricSpecError(f"{name}: version is always {YAML_VERSION} (set by the runner); remove it")
    view_joins = list(data.get("joins") or [])
    view_dims = list(data.get("dimensions") or [])
    view_measures = list(data.get("measures") or [])
    blocks_cfg = data.get("blocks") or {}
    if not isinstance(blocks_cfg, Mapping):
        raise MetricSpecError(f"{name}: `blocks` must be a mapping block -> params")
    joins: List[Dict[str, Any]] = []
    dims: List[Dict[str, Any]] = []
    origin: Dict[str, str] = {}
    order = sorted(blocks_cfg, key=lambda b: (b != "calendar", b not in CLIENT_BLOCKS, b))
    for bname in order:
        if bname not in blocks:
            raise MetricSpecError(f"{name}: unknown block {bname!r} (have {sorted(blocks)})")
        bj, bd = expand_block(blocks[bname], blocks_cfg[bname] or {}, name)
        joins += bj
        for d in bd:
            origin[d["name"]] = bname
        dims += bd
    overrides = data.get("overrides") or {}
    for fname, patch in overrides.items():
        target = next((d for d in dims if d["name"] == fname), None)
        if target is None:
            raise MetricSpecError(f"{name}: overrides {fname!r} is not a block field")
        bad = set(patch or {}) - {"comment", "display_name", "synonyms", "format"}
        if bad:
            raise MetricSpecError(f"{name}: overrides may only patch comment / display_name / synonyms / format, got {sorted(bad)}")
        target.update(copy.deepcopy(patch))
    spec = ViewSpec(name=name, space=str(data.get("space") or ""), comment=one_line(data.get("comment")),
                    grain=one_line(data.get("grain")), raw=data, joins=joins + view_joins,
                    dimensions=dims + view_dims, measures=view_measures, block_fields=origin,
                    validate=dict(data.get("validate") or {}), reconcile=list(data.get("reconcile") or []),
                    glossary=dict(data.get("glossary") or {}), path=path)
    return spec


def warehouse_dict(spec: ViewSpec, params: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """The metric-view YAML document sent to the warehouse (runner-only keys stripped, placeholders rendered)."""
    def clean_join(j: Mapping[str, Any]) -> Dict[str, Any]:
        out = {k: copy.deepcopy(v) for k, v in j.items() if k in JOIN_KEYS and k != "joins"}
        if j.get("joins"):
            out["joins"] = [clean_join(x) for x in j["joins"]]
        return out

    doc: Dict[str, Any] = {"version": YAML_VERSION, "comment": spec.full_comment, "source": spec.source}
    if spec.filter:
        doc["filter"] = spec.filter
    if spec.joins:
        doc["joins"] = [clean_join(j) for j in spec.joins]
    doc["dimensions"] = [{k: copy.deepcopy(v) for k, v in d.items() if k in FIELD_KEYS} for d in spec.dimensions]
    doc["measures"] = [{k: copy.deepcopy(v) for k, v in m.items() if k in MEASURE_KEYS} for m in spec.measures]
    for f_ in doc["dimensions"] + doc["measures"]:
        if "comment" in f_:
            f_["comment"] = one_line(f_["comment"])
        if "display_name" in f_:
            f_["display_name"] = one_line(f_["display_name"])
        if isinstance(f_.get("expr"), str):
            f_["expr"] = " ".join(f_["expr"].split()) if "\n" in f_["expr"] else f_["expr"].strip()
    if params is not None:
        doc = substitute(doc, params, strict=True)
    return doc


if yaml is not None:
    class _NoAliasDumper(yaml.SafeDumper):
        """View files may use YAML anchors (&usd / *usd) for repeated formats and windows; the warehouse YAML
        is always written out in full."""

        def ignore_aliases(self, data: Any) -> bool:
            return True


def to_yaml(doc: Mapping[str, Any]) -> str:
    return yaml.dump(dict(doc), Dumper=_NoAliasDumper, sort_keys=False, allow_unicode=True, width=4096,
                     default_flow_style=False)


def render_ddl(spec: ViewSpec, params: Mapping[str, Any]) -> str:
    """CREATE OR REPLACE VIEW <catalog>.metrics.<v> WITH METRICS LANGUAGE YAML COMMENT '...' AS $$ ... $$.
    The DDL COMMENT is what Unity Catalog keeps as the view comment (it overrides the YAML `comment`)."""
    body = to_yaml(warehouse_dict(spec, params))
    if "$$" in body:
        raise MetricSpecError(f"{spec.name}: '$$' inside the YAML would end the dollar-quoted body")
    comment = substitute(spec.full_comment, params)
    return (f"CREATE OR REPLACE VIEW {spec.fqn(params['catalog'])}\nWITH METRICS\nLANGUAGE YAML\n"
            f"COMMENT {sql_str(comment)}\nAS $$\n{body}$$")


def render_tags(spec: ViewSpec, catalog: str) -> str:
    tags = {"smbc_layer": "metrics", "smbc_domain": spec.space, "smbc_synthetic": "true"}
    pairs = ", ".join(f"{sql_str(k)} = {sql_str(v)}" for k, v in tags.items())
    return f"ALTER VIEW {spec.fqn(catalog)} SET TAGS ({pairs})"


# ---- references ------------------------------------------------------------------------------------
def _all_join_sources(joins: Iterable[Mapping[str, Any]]) -> List[str]:
    out = []
    for j in joins:
        out.append(str(j.get("source", "")))
        out += _all_join_sources(j.get("joins") or [])
    return out


def _join_aliases(joins: Iterable[Mapping[str, Any]], prefix: str = "") -> Dict[str, str]:
    """dotted alias path (client, client.grp) -> join source."""
    out = {}
    for j in joins:
        path = f"{prefix}{j.get('name')}"
        out[path] = str(j.get("source", ""))
        out.update(_join_aliases(j.get("joins") or [], prefix=path + "."))
    return out


def sql_refs(spec: ViewSpec) -> Set[Tuple[str, str]]:
    """(schema, table) read by the view: source + every (nested) join source, via ${catalog}.<schema>.<t>."""
    texts = [spec.source] + _all_join_sources(spec.joins)
    return {(s.lower(), t.lower()) for txt in texts for s, t in _SQL_REF_RE.findall(txt)}


def measure_refs(expr: str) -> List[str]:
    return [m.strip("`") for m in _MEASURE_REF_RE.findall(expr or "")]


def is_window(m: Mapping[str, Any]) -> bool:
    return bool(m.get("window"))


def measure_kind(m: Mapping[str, Any]) -> str:
    """point_in_time | prior_period | cumulative | rolling | composed | aggregate (glossary / caveats)."""
    win = m.get("window") or []
    if win:
        if any(w.get("offset") is not None for w in win):
            return "prior_period"
        ranges = [str(w.get("range", "")) for w in win]
        if any(r.startswith("cumulative") for r in ranges):
            return "cumulative"
        if any(r.startswith(("trailing", "leading")) for r in ranges):
            return "rolling"
        return "point_in_time"
    if measure_refs(str(m.get("expr", ""))):
        return "composed"
    return "aggregate"


def unit_of(m: Mapping[str, Any]) -> str:
    if m.get("unit"):
        return str(m["unit"])
    fmt = m.get("format") or {}
    t = fmt.get("type")
    if t == "currency":
        return str(fmt.get("currency_code", "")).upper() or "currency"
    if t == "percentage":
        return "% (stored as a fraction: 0.25 = 25%)"
    if t in ("date", "date_time"):
        return t
    if re.match(r"^\s*COUNT\s*\(", str(m.get("expr", "")), re.I):
        return "count"
    return "number"


# ---- validation ------------------------------------------------------------------------------------
def array_columns_from_gold() -> Dict[str, Set[str]]:
    """gold table -> its ARRAY / MAP columns, from the gold specs (best effort: invalid specs are skipped)."""
    try:
        from . import gold
        errors: List[str] = []
        specs = gold.load_specs(errors=errors)
    except Exception:  # noqa: BLE001 - the metrics framework must not depend on gold spec health
        return {"dim_client": set(ARRAY_COLUMNS_FALLBACK)}
    out = {n: {c.name for c in s.columns if c.type.startswith(("ARRAY", "MAP"))} for n, s in specs.items()}
    out.setdefault("dim_client", set()).update(ARRAY_COLUMNS_FALLBACK)
    return {k: v for k, v in out.items() if v}


def _label_errors(where: str, f_: Mapping[str, Any], is_measure: bool) -> List[str]:
    errs = []
    nm = f_.get("name")
    for req in ("name", "expr", "comment", "display_name"):
        if not one_line(f_.get(req)):
            errs.append(f"{where}: `{req}` is required")
    if nm is not None:
        nm = str(nm)
        if not _NAME_RE.match(nm) or len(nm) > MAX_LABEL:
            errs.append(f"{where}: name {nm!r} must be ASCII letters/digits/space/%&()/+.- (<= {MAX_LABEL} chars, "
                        "no backticks)")
    if len(one_line(f_.get("display_name"))) > MAX_LABEL:
        errs.append(f"{where}: display_name longer than {MAX_LABEL}")
    syn = f_.get("synonyms")
    if syn is not None:
        if not isinstance(syn, list) or not all(isinstance(s, str) and s.strip() for s in syn):
            errs.append(f"{where}: synonyms must be a list of non-empty strings")
        else:
            if len(syn) > MAX_SYNONYMS:
                errs.append(f"{where}: {len(syn)} synonyms (max {MAX_SYNONYMS})")
            if any(len(s) > MAX_LABEL for s in syn):
                errs.append(f"{where}: a synonym is longer than {MAX_LABEL}")
            if len({s.lower() for s in syn}) != len(syn):
                errs.append(f"{where}: duplicate synonyms")
    allowed = (MEASURE_KEYS if is_measure else FIELD_KEYS) | FIELD_RUNNER_KEYS
    extra = set(map(str, f_)) - allowed
    if extra:
        errs.append(f"{where}: unexpected keys {sorted(extra)} (allowed {sorted(allowed)}; quote comments containing ': ')")
    return errs


def format_errors(where: str, fmt: Any) -> List[str]:
    if not isinstance(fmt, Mapping):
        return [f"{where}: format must be a mapping {{type: ...}}"]
    errs = []
    extra = set(fmt) - FORMAT_KEYS
    if extra:
        errs.append(f"{where}: unknown format keys {sorted(extra)}")
    t = fmt.get("type")
    if t not in FORMAT_TYPES:
        errs.append(f"{where}: format type {t!r} not in {sorted(FORMAT_TYPES)} (it is 'percentage', not 'percent')")
    if t == "currency" and not re.match(r"^[A-Z]{3}$", str(fmt.get("currency_code", ""))):
        errs.append(f"{where}: currency format needs currency_code (ISO 4217, e.g. USD)")
    dp = fmt.get("decimal_places")
    if dp is not None:
        if not isinstance(dp, Mapping) or dp.get("type") not in DECIMAL_TYPES:
            errs.append(f"{where}: decimal_places needs type in {sorted(DECIMAL_TYPES)}")
        elif dp.get("type") in ("max", "exact"):
            pl = dp.get("places")
            if not isinstance(pl, int) or not 0 <= pl <= 10:
                errs.append(f"{where}: decimal_places.places must be an int 0-10 for type {dp.get('type')}")
    if fmt.get("abbreviation") is not None and fmt.get("abbreviation") not in ABBREVIATIONS:
        errs.append(f"{where}: abbreviation must be one of {sorted(ABBREVIATIONS)}")
    if t == "percentage" and fmt.get("abbreviation") is not None:
        errs.append(f"{where}: percentage format takes no abbreviation")
    return errs


def _expr_mentions(expr: str, alias_path: str, col: str) -> bool:
    return re.search(rf"(?<![\w.]){re.escape(alias_path)}\.`?{re.escape(col)}`?\b", expr or "", re.I) is not None


def validate_view(spec: ViewSpec, array_cols: Optional[Mapping[str, Set[str]]] = None) -> List[str]:
    """Structural / semantic checks of an expanded view (PLAN §8 conventions). Returns error strings."""
    v = spec.name
    errs: List[str] = []
    if spec.space not in SPACES:
        errs.append(f"{v}: space {spec.space!r} must be a Genie space slug {sorted(SPACES)}")
    elif v in PLAN_VIEWS and PLAN_VIEWS[v] != spec.space:
        errs.append(f"{v}: PLAN §8 puts this view in space {PLAN_VIEWS[v]}, not {spec.space}")
    if not spec.comment:
        errs.append(f"{v}: `comment` is required (what the view answers, grain, point-in-time caveat)")
    if not spec.grain:
        errs.append(f"{v}: `grain` is required (one row of the source = ...)")
    src = spec.source
    if not src:
        errs.append(f"{v}: `source` is required")
    for txt in [src] + _all_join_sources(spec.joins):
        if _LITERAL_CATALOG_RE.search(txt):
            errs.append(f"{v}: write ${{catalog}}.<schema>.<table>, not a literal catalog, in {txt[:80]!r}")
        for s, t in _SQL_REF_RE.findall(txt):
            if s.lower() != "gold":
                errs.append(f"{v}: sources and joins read gold only (brief §3.3), found {s}.{t}")
    if not spec.dimensions:
        errs.append(f"{v}: at least one dimension")
    if not spec.measures:
        errs.append(f"{v}: at least one measure")
    blocks_cfg = spec.raw.get("blocks") or {}
    if not any(b in blocks_cfg for b in CLIENT_BLOCKS) and not one_line(spec.raw.get("client_block_exempt")):
        errs.append(f"{v}: every view carries the standard client block (blocks: client | client_group) "
                    "unless `client_block_exempt: <reason>`")
    if "calendar" not in blocks_cfg and not one_line(spec.raw.get("calendar_block_exempt")):
        errs.append(f"{v}: every view carries the calendar block unless `calendar_block_exempt: <reason>`")

    # joins
    seen_alias: Set[str] = set()

    def check_join(j: Mapping[str, Any], where: str) -> None:
        if not isinstance(j, Mapping):
            errs.append(f"{where}: join must be a mapping")
            return
        extra = set(map(str, j)) - JOIN_KEYS
        if extra:
            errs.append(f"{where}: unknown join keys {sorted(extra)}")
        nm = str(j.get("name") or "")
        if not re.match(r"^[a-z][a-z0-9_]*$", nm):
            errs.append(f"{where}: join name must be a lower-case identifier, got {nm!r}")
        if nm in seen_alias:
            errs.append(f"{where}: duplicate join name {nm!r}")
        seen_alias.add(nm)
        if not j.get("source"):
            errs.append(f"{where}: join needs a source")
        if bool(j.get("on")) == bool(j.get("using")):
            errs.append(f"{where}: join needs exactly one of `on` / `using`")
        for sub in j.get("joins") or []:
            check_join(sub, f"{where}.{sub.get('name') if isinstance(sub, Mapping) else '?'}")

    for j in spec.joins:
        check_join(j, f"{v}: join {j.get('name') if isinstance(j, Mapping) else '?'}")
    # a field named like a join alias (case-insensitive) shadows it for every later field: verified on DBSQL
    # 2026.38 - field `Client` + join `client` -> INVALID_EXTRACT_BASE_FIELD_TYPE on `client.segment`
    labels = {str(x.get("name", "")).lower() for x in list(spec.dimensions) + list(spec.measures) if isinstance(x, Mapping)}
    for alias in sorted(seen_alias):
        if alias.lower() in labels:
            errs.append(f"{v}: join alias {alias!r} equals a field / measure name (the field would shadow the join)")

    # fields
    names_ci: Dict[str, str] = {}
    defined_fields: List[str] = []
    dim_names = set()
    for i, d in enumerate(spec.dimensions):
        where = f"{v}: dimension {d.get('name', f'#{i}')!r}"
        if not isinstance(d, Mapping):
            errs.append(f"{where}: must be a mapping")
            continue
        errs += _label_errors(where, d, is_measure=False)
        if d.get("format") is not None:
            errs += format_errors(where, d["format"])
        nm = str(d.get("name", ""))
        if nm.lower() in names_ci:
            errs.append(f"{where}: duplicate name (names are case-insensitive and shared by dimensions and measures)")
        names_ci[nm.lower()] = nm
        expr = str(d.get("expr", ""))
        if _AGG_RE.search(expr) and not re.search(r"\bOVER\s*\(", expr, re.I):
            errs.append(f"{where}: aggregate functions belong in measures")
        earlier = {x.lower() for x in defined_fields}
        all_fields = {str(x.get("name", "")).lower() for x in spec.dimensions if isinstance(x, Mapping)}
        measure_names_ci = {str(x.get("name", "")).lower() for x in spec.measures if isinstance(x, Mapping)}
        for ref in _BACKTICK_RE.findall(expr):
            if ref.lower() in measure_names_ci:
                errs.append(f"{where}: references measure `{ref}` (dimensions cannot)")
            elif ref.lower() in all_fields and ref.lower() not in earlier:
                errs.append(f"{where}: references field `{ref}` before it is defined (fields may only use earlier fields)")
        defined_fields.append(nm)
        dim_names.add(nm)

    # measures
    measure_index = {str(m.get("name")): i for i, m in enumerate(spec.measures) if isinstance(m, Mapping)}
    window_measures = {str(m.get("name")) for m in spec.measures if isinstance(m, Mapping) and is_window(m)}
    for i, m in enumerate(spec.measures):
        where = f"{v}: measure {m.get('name', f'#{i}')!r}"
        if not isinstance(m, Mapping):
            errs.append(f"{where}: must be a mapping")
            continue
        errs += _label_errors(where, m, is_measure=True)
        nm = str(m.get("name", ""))
        if nm.lower() in names_ci:
            errs.append(f"{where}: duplicate name (names are case-insensitive and shared by dimensions and measures)")
        names_ci[nm.lower()] = nm
        expr = str(m.get("expr", ""))
        if not _AGG_RE.search(expr):
            errs.append(f"{where}: expr must aggregate (SUM / COUNT / AVG / MEDIAN / PERCENTILE / ... or MEASURE())")
        fmt = m.get("format")
        if fmt is None:
            errs.append(f"{where}: every measure needs a `format` (currency / percentage / number)")
        else:
            errs += format_errors(where, fmt)
            t = fmt.get("type") if isinstance(fmt, Mapping) else None
            if re.search(r"\bUSD\b", nm) and (t != "currency" or fmt.get("currency_code") != "USD"):
                errs.append(f"{where}: a '... USD' measure needs format {{type: currency, currency_code: USD}}")
            if "%" in nm and t != "percentage":
                errs.append(f"{where}: a '... %' measure needs format {{type: percentage}} (values as fractions)")
        refs = measure_refs(expr)
        for ref in refs:
            if ref not in measure_index:
                errs.append(f"{where}: MEASURE({ref}) is not a measure of this view")
            elif measure_index[ref] >= i:
                errs.append(f"{where}: MEASURE({ref}) must be defined before it is used")
        win = m.get("window")
        if win is not None:
            if not isinstance(win, list) or not win:
                errs.append(f"{where}: window must be a non-empty list")
            else:
                for w in win:
                    if not isinstance(w, Mapping):
                        errs.append(f"{where}: window entries are mappings")
                        continue
                    extra = set(w) - WINDOW_KEYS
                    if extra:
                        errs.append(f"{where}: unknown window keys {sorted(extra)}")
                    if w.get("order") not in dim_names:
                        errs.append(f"{where}: window order {w.get('order')!r} must be a dimension of the view")
                    if not _RANGE_RE.match(str(w.get("range", ""))):
                        errs.append(f"{where}: window range {w.get('range')!r} (current | cumulative | all | "
                                    "trailing|leading N [day|month|year] [inclusive|exclusive])")
                    if w.get("semiadditive") not in ("first", "last"):
                        errs.append(f"{where}: window semiadditive must be first or last")
                    if w.get("offset") is not None and not _OFFSET_RE.match(str(w.get("offset"))):
                        errs.append(f"{where}: window offset {w.get('offset')!r} (e.g. -1 month, -12 month, -1)")
                bad = [r for r in refs if r in window_measures]
                if bad:
                    errs.append(f"{where}: a window measure cannot reference window measures {bad} "
                                "(METRIC_VIEW_WINDOW_MEASURE_REFERENCES_WINDOW_MEASURE)")

    # expressions: as-of discipline, $$, arrays (D31)
    arrays = array_cols if array_cols is not None else array_columns_from_gold()
    aliases = _join_aliases(spec.joins)
    exprs = [(f"dimension {d.get('name')!r}", str(d.get("expr", ""))) for d in spec.dimensions if isinstance(d, Mapping)]
    exprs += [(f"measure {m.get('name')!r}", str(m.get("expr", ""))) for m in spec.measures if isinstance(m, Mapping)]
    exprs.append(("filter", str(spec.filter or "")))
    for where, expr in exprs:
        if _FORBIDDEN_FN_RE.search(expr):
            errs.append(f"{v}: {where} uses CURRENT_DATE / now() - use DATE'${{as_of_date}}' (D16)")
        if "$$" in expr:
            errs.append(f"{v}: {where} contains '$$'")
    for where, expr in exprs:
        if not where.startswith("dimension"):
            continue
        for path, src in aliases.items():
            m_ = _SQL_REF_RE.search(src)
            table = m_.group(2).lower() if m_ else None
            for col in sorted(arrays.get(table, set())) if table else []:
                if _expr_mentions(expr, path, col):
                    errs.append(f"{v}: {where} exposes ARRAY column {path}.{col} (D31: use its *_text copy)")
        src_m = _SQL_REF_RE.search(src or "")
        if src_m:
            for col in sorted(arrays.get(src_m.group(2).lower(), set())):
                if _expr_mentions(expr, "source", col):
                    errs.append(f"{v}: {where} exposes ARRAY column source.{col} (D31)")

    # runner-only sections
    val = spec.validate
    dims = val.get("dims") or []
    if dims and not (2 <= len(dims) <= 3):
        errs.append(f"{v}: validate.dims takes 2-3 dimensions")
    for d in dims:
        if d not in dim_names:
            errs.append(f"{v}: validate dim {d!r} is not a dimension")
    if len(spec.reconcile) < 2:
        errs.append(f"{v}: at least 2 `reconcile` entries (measure vs direct gold SQL, PLAN §8)")
    rec_names = set()
    for rec in spec.reconcile:
        if not isinstance(rec, Mapping):
            errs.append(f"{v}: reconcile entries are mappings")
            continue
        extra = set(rec) - {"name", "measure", "where", "by", "gold", "tolerance"}
        if extra:
            errs.append(f"{v}: reconcile {rec.get('name')!r} unknown keys {sorted(extra)}")
        if not rec.get("name") or rec["name"] in rec_names:
            errs.append(f"{v}: reconcile entries need a unique name")
        rec_names.add(rec.get("name"))
        if rec.get("measure") not in measure_index:
            errs.append(f"{v}: reconcile {rec.get('name')!r} measure {rec.get('measure')!r} is not a measure")
        if not str(rec.get("gold", "")).strip():
            errs.append(f"{v}: reconcile {rec.get('name')!r} needs `gold` (direct SQL over gold tables)")
        elif _LITERAL_CATALOG_RE.search(str(rec["gold"])):
            errs.append(f"{v}: reconcile {rec.get('name')!r}: write ${{catalog}}.gold.<t> in the gold SQL")
        for b in rec.get("by") or []:
            if b not in dim_names:
                errs.append(f"{v}: reconcile {rec.get('name')!r} by {b!r} is not a dimension")
        if rec.get("tolerance") is not None and not 0 <= float(rec["tolerance"]) <= 0.01:
            errs.append(f"{v}: reconcile {rec.get('name')!r} tolerance is relative (0-0.01; default 0.0001)")
    return errs


@dataclass
class Catalogue:
    views: Dict[str, ViewSpec]
    blocks: Dict[str, Block]
    errors: List[str]


def load_views(metrics_dir: Path = METRICS_DIR, blocks_dir: Optional[Path] = None,
               array_cols: Optional[Mapping[str, Set[str]]] = None, strict: bool = True) -> Catalogue:
    """Every metrics/<view>.yaml (files starting with '_' are not views). strict=True raises on the first
    invalid view; strict=False skips invalid views and collects their errors."""
    blocks = load_blocks(blocks_dir or Path(metrics_dir) / "_blocks")
    arrays = array_cols if array_cols is not None else array_columns_from_gold()
    views: Dict[str, ViewSpec] = {}
    errors: List[str] = []
    for p in sorted(Path(metrics_dir).glob("*.yaml")):
        if p.name.startswith("_"):
            continue
        try:
            spec = parse_view(load_yaml(p), blocks, p)
            errs = validate_view(spec, arrays)
            if errs:
                raise MetricSpecError("; ".join(errs))
            views[spec.name] = spec
        except MetricSpecError as e:
            if strict:
                raise
            errors.append(f"{p.name}: {e}")
    return Catalogue(views=views, blocks=blocks, errors=errors)


def select(views: Mapping[str, ViewSpec], only: Sequence[str] = (), space: Sequence[str] = ()) -> List[str]:
    unknown = [n for n in only if n not in views]
    if unknown:
        raise MetricSpecError(f"unknown views {unknown} (have {sorted(views)})")
    bad = [s for s in space if s not in SPACES]
    if bad:
        raise MetricSpecError(f"unknown spaces {bad} (slugs: {sorted(SPACES)})")
    return sorted(n for n, s in views.items() if (not only and not space) or n in only or s.space in space)


# ---- validation / reconciliation SQL ----------------------------------------------------------------
def validation_sql(spec: ViewSpec, catalog: str) -> str:
    """SELECT <2-3 dims>, MEASURE(<every measure>) ... GROUP BY ALL (PLAN §8)."""
    dims = spec.validate.get("dims") or [d for d in ("Fiscal Year", "Segment") if d in spec.field_names][:2] \
        or spec.field_names[:2]
    sel = [q(d) for d in dims] + [f"MEASURE({q(m)}) AS {q('m_' + str(i))}" for i, m in enumerate(spec.measure_names)]
    where = f"\nWHERE {spec.validate['where']}" if spec.validate.get("where") else ""
    return f"SELECT {', '.join(sel)}\nFROM {spec.fqn(catalog)}{where}\nGROUP BY ALL"


def measure_sql(spec: ViewSpec, catalog: str, measure: str, where: Optional[str] = None,
                by: Sequence[str] = ()) -> str:
    sel = [q(b) for b in by] + [f"MEASURE({q(measure)}) AS v"]
    w = f" WHERE {where}" if where else ""
    g = " GROUP BY ALL" if by else ""
    return f"SELECT {', '.join(sel)} FROM {spec.fqn(catalog)}{w}{g}"


def dims_probe_sql(spec: ViewSpec, catalog: str) -> str:
    """Every dimension selected once (catches a broken dimension expression the validation dims miss)."""
    sel = ", ".join(q(d) for d in spec.field_names)
    first = spec.measure_names[0]
    return f"SELECT {sel}, MEASURE({q(first)}) AS v FROM {spec.fqn(catalog)} GROUP BY ALL LIMIT 1"


def _num(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def within(a: Optional[float], b: Optional[float], tol: float = DEFAULT_TOLERANCE) -> bool:
    """Relative tolerance; NULL equals NULL or 0 (an empty aggregate)."""
    if a is None or b is None:
        return (a or 0.0) == (b or 0.0)
    return abs(a - b) <= tol * max(abs(a), abs(b), 1e-12) or abs(a - b) <= 1e-9


def _key(vals: Sequence[Any]) -> Tuple[str, ...]:
    out = []
    for v in vals:
        s = "" if v is None else str(v)
        if re.match(r"^\d{4}-\d{2}-\d{2}T00:00:00(\.0+)?(Z|\+00:00)?$", s):
            s = s[:10]
        out.append(s.lower() if s.lower() in ("true", "false") else s)
    return tuple(out)


def compare_rows(mv_rows: Sequence[Sequence[Any]], gold_rows: Sequence[Sequence[Any]], n_keys: int,
                 tol: float = DEFAULT_TOLERANCE) -> Tuple[bool, str]:
    """Compare metric-view vs gold results: scalar (n_keys = 0) or keyed rows (last column = value)."""
    if n_keys == 0:
        a = _num(mv_rows[0][0]) if mv_rows else None
        b = _num(gold_rows[0][0]) if gold_rows else None
        ok = within(a, b, tol)
        return ok, f"mv={a!r} gold={b!r}"
    mv = {_key(r[:n_keys]): _num(r[n_keys]) for r in mv_rows}
    gd = {_key(r[:n_keys]): _num(r[n_keys]) for r in gold_rows}
    bad = []
    for k in sorted(set(mv) | set(gd)):
        if not within(mv.get(k), gd.get(k), tol):
            bad.append(f"{'/'.join(k)}: mv={mv.get(k)!r} gold={gd.get(k)!r}")
    return (not bad), (f"{len(gd)} keys match" if not bad else f"{len(bad)} of {len(set(mv) | set(gd))} keys differ: "
                       + "; ".join(bad[:5]))


def coverage_gaps(describe_json: Mapping[str, Any], spec: Optional[ViewSpec] = None) -> List[str]:
    """From DESCRIBE TABLE EXTENDED ... AS JSON: columns without a comment or display_name, measures without
    a format (UC stores them under columns[].comment / metadata.display_name / metadata.format)."""
    gaps = []
    if not one_line(describe_json.get("comment")):
        gaps.append("(view comment)")
    for c in describe_json.get("columns", []):
        md = c.get("metadata") or {}
        if not one_line(c.get("comment")):
            gaps.append(f"{c.get('name')}: comment")
        if not one_line(md.get("display_name")):
            gaps.append(f"{c.get('name')}: display_name")
        if c.get("is_measure") and not md.get("format"):
            gaps.append(f"{c.get('name')}: format")
    if spec is not None:
        want = set(spec.field_names) | set(spec.measure_names)
        got = {c.get("name") for c in describe_json.get("columns", [])}
        missing = sorted(want - got)
        if missing:
            gaps.append(f"columns missing in UC: {missing}")
    return gaps


# ---- glossary --------------------------------------------------------------------------------------
_KIND_TEXT = {
    "point_in_time": "Point-in-time (window: latest period of the selection; never summed across periods)",
    "prior_period": "Prior-period comparison (window offset)",
    "cumulative": "Cumulative / to-date (window)",
    "rolling": "Rolling window",
    "composed": "Composed from other measures (ratio / difference; correct at any grain)",
    "aggregate": "Aggregate",
}


def _md(text: Any) -> str:
    return one_line(text).replace("|", "\\|")


def _window_text(m: Mapping[str, Any]) -> str:
    parts = []
    for w in m.get("window") or []:
        s = f"order {w.get('order')}, {w.get('range')}, {w.get('semiadditive')}"
        if w.get("offset") is not None:
            s += f", offset {w.get('offset')}"
        parts.append(s)
    return "; ".join(parts)


def caveats_of(m: Mapping[str, Any]) -> str:
    out = []
    kind = measure_kind(m)
    if kind in ("point_in_time", "prior_period", "cumulative", "rolling"):
        out.append(f"{_KIND_TEXT[kind]} [{_window_text(m)}]")
    elif kind == "composed":
        out.append(_KIND_TEXT[kind])
    if m.get("caveat"):
        out.append(one_line(m["caveat"]))
    return "; ".join(out)


def render_glossary(views: Mapping[str, ViewSpec], blocks: Mapping[str, Block], params: Mapping[str, Any]) -> str:
    """metrics/_glossary.md: shared block fields once, then per view its grain and every measure / field
    (name, definition, unit, grain, caveats). Generated - do not edit by hand."""
    def r(x: Any) -> str:
        return substitute(one_line(x), params, strict=False)

    lines = ["# Metric-view glossary (generated)", "",
             "Generated by `src/40_metrics/run_metrics.py` from `metrics/*.yaml` and `metrics/_blocks/*.yaml` "
             "- do not edit by hand. Schema `smbc_genie.metrics`; as-of 30-Sep-2026; Japanese fiscal year "
             "(FY2026 = Apr 2026 - Mar 2027). Percentages are stored as fractions (0.25 = 25%).", "",
             f"Views: {len(views)} of {len(PLAN_VIEWS)} planned (PLAN §8).", ""]
    lines += ["## Shared blocks", ""]
    for bname in sorted(blocks):
        b = blocks[bname]
        lines += [f"### Block `{bname}`", "", r(b.comment), "", "| Field | Definition | Synonyms |", "|---|---|---|"]
        seen = set()
        for d in b.dimensions:
            label = d["name"] + (f" ({', '.join(d['grains'])})" if d.get("grains") else "")
            if label in seen:
                continue
            seen.add(label)
            lines.append(f"| {_md(label)} | {_md(r(d.get('comment')))} | {_md(', '.join(d.get('synonyms') or []))} |")
        lines.append("")
    by_space: Dict[str, List[ViewSpec]] = {}
    for s in views.values():
        by_space.setdefault(s.space, []).append(s)
    lines += ["## Views", ""]
    for slug, (no, title) in sorted(SPACES.items(), key=lambda kv: kv[1][0]):
        vs = sorted(by_space.get(slug, []), key=lambda s: s.name)
        if not vs:
            continue
        lines += [f"### Space {no} - {title}", ""]
        for s in vs:
            lines += [f"#### `{s.name}`", "", f"- **Definition:** {_md(r(s.comment))}",
                      f"- **Grain:** {_md(r(s.grain))}", f"- **Source:** `{r(s.source)[:200]}`"]
            blocks_used = ", ".join(f"`{b}`" for b in (s.raw.get("blocks") or {}))
            lines.append(f"- **Blocks:** {blocks_used or 'none'}")
            for c in (s.glossary.get("caveats") or []):
                lines.append(f"- **Caveat:** {_md(r(c))}")
            lines += ["", "| Measure | Definition | Unit | Caveats |", "|---|---|---|---|"]
            for m in s.measures:
                lines.append(f"| {_md(m['name'])} | {_md(r(m.get('comment')))} | {_md(unit_of(m))} | {_md(caveats_of(m))} |")
            own = [d for d in s.dimensions if d["name"] not in s.block_fields]
            if own:
                lines += ["", "| Dimension | Definition | Synonyms |", "|---|---|---|"]
                for d in own:
                    lines.append(f"| {_md(d['name'])} | {_md(r(d.get('comment')))} | {_md(', '.join(d.get('synonyms') or []))} |")
            lines.append("")
    missing = sorted(set(PLAN_VIEWS) - set(views))
    if missing:
        lines += ["## Planned views not built yet", "", ", ".join(f"`{m}`" for m in missing), ""]
    return "\n".join(lines).rstrip() + "\n"


# ---- answers files (metrics/_answers/<space>.sql) ----------------------------------------------------
@dataclass
class Answer:
    qid: str
    question: str
    sql: str
    views: List[str]
    rows: Optional[int] = None


_QID_RE = re.compile(r"^--\s*(Q[\w.-]+)\s*:\s*(.+)$", re.M)
_ROWS_RE = re.compile(r"^--\s*rows\s*:.*$", re.M)
_VIEW_REF_RE = re.compile(r"\b(?:\$\{catalog\}|smbc_genie)\.metrics\.(mv_\w+)", re.I)


def parse_answers(text: str) -> List[Answer]:
    """One statement per must-answer question; its leading comment holds `-- Q<n>: <question>` and
    optionally `-- rows: <n>` (written back by the runner)."""
    from .sql_runner import split_statements

    out, ids = [], set()
    for stmt in split_statements(text):
        m = _QID_RE.search(stmt)
        if not m:
            raise MetricSpecError(f"answer statement without a '-- Q<n>: question' header: {stmt[:120]!r}")
        qid = m.group(1)
        if qid in ids:
            raise MetricSpecError(f"duplicate answer id {qid}")
        ids.add(qid)
        rows_m = _ROWS_RE.search(stmt)
        rows = None
        if rows_m:
            num = re.search(r"\d+", rows_m.group(0))
            rows = int(num.group(0)) if num else None
        body = "\n".join(l for l in stmt.splitlines() if not l.lstrip().startswith("--")).strip()
        if _FORBIDDEN_FN_RE.search(body):
            raise MetricSpecError(f"{qid}: CURRENT_DATE / now() - use DATE'2026-09-30' (D16)")
        views = sorted({v.lower() for v in _VIEW_REF_RE.findall(body)})
        out.append(Answer(qid=qid, question=m.group(2).strip(), sql=body, views=views, rows=rows))
    return out


def write_answer_rows(text: str, counts: Mapping[str, Optional[int]]) -> str:
    """Rewrite / insert the `-- rows: n` line under each `-- Q<n>:` header (idempotent)."""
    lines = text.splitlines()
    out: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = re.match(r"^--\s*(Q[\w.-]+)\s*:", line)
        if m and m.group(1) in counts:
            j = i + 1
            while j < len(lines) and lines[j].lstrip().startswith("--") and not _ROWS_RE.match(lines[j]) \
                    and not re.match(r"^--\s*Q[\w.-]+\s*:", lines[j]):
                out.append(lines[j])
                j += 1
            n = counts[m.group(1)]
            out.append(f"-- rows: {'n/a (not run)' if n is None else n}")
            if j < len(lines) and _ROWS_RE.match(lines[j]):
                j += 1
            i = j
            continue
        i += 1
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")
