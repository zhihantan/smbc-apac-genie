"""Phase 9 handover - the Genie question bank: every question each agent answers, with verdicts and answers.

Renders docs/GENIE_QUESTIONS.md and docs/genie_questions.csv from, per agent (Genie space):
  genie/<slug>/space.yaml            the sample (starter) questions in display order, the data sources
  genie/<slug>/benchmarks.json       benchmark questions, expected SQL, variants
  genie/<slug>/eval_report.json      the last benchmark evaluation (verdict per benchmark)
  metrics/_answers/<slug>.sql        the brief's must-answer wording (`-- Qn: question` headers)
  genie/_question_bank.yaml          curated text: audience, what the agent knows, starter -> benchmark map,
                                     themes, safer wordings of the flaky questions, follow-ups, routing hints
  genie/<slug>/question_bank.json    the live-capture cache (written by --live; the default render reads it)

--live [all|answers|questions] (re)captures the cache. Read-only on data; Genie conversations are test artefacts:
  answers    every benchmark's expected SQL on the warehouse: row count, columns and the first rows
             (+ the period each metric view covers: min / max of its calendar field)
  questions  every starter question, the curated safer wordings and follow-ups through the Conversation API -
             one question in flight, >= 12 s between starts, back-off on 429 / 5xx (genie_ws) - graded against
             the benchmark each one paraphrases, then the conversation is deleted. Conversation ids go to the
             ledger scratch/question_bank_conversations.jsonl first; --cleanup deletes any left behind.
Full result sets (Genie's and the expected ones) stay in scratch/question_bank_raw/<slug>.json for --regrade.

Grading a starter question (genie/README "Comparison rule", relaxed because a starter is a shorter paraphrase):
  match    Genie's rows equal the benchmark's expected rows (same row count; values to 4 significant digits;
           columns matched by their values, names and order ignored) on every column the two answers share.
           Genie may add columns, or leave out benchmark columns (listed in the note) - but a left-out benchmark
           column next to an unexplained Genie column of the same kind (a figure, or a label) counts as differing
           values, and showing none of the benchmark's figures is no match. `need` names the benchmark columns
           the starter really asks for (e.g. [Client] for "which clients ..."): then only those must be shown.
  differs  Genie answered with SQL but the rows or values differ (the note says how)
  error    Genie failed, timed out or answered without SQL
Follow-ups with a `benchmark` are graded the same way; the others count as a match when Genie answers with SQL
that keeps the context (`expect_in_sql`, e.g. the same client).

Run:  .venv/bin/python src/50_genie/build_question_bank.py                                    # offline render
      .venv/bin/python src/50_genie/build_question_bank.py --profile my-workspace --live [--only <slug>]
      .venv/bin/python src/50_genie/build_question_bank.py --profile my-workspace --live questions \
          --ids tb_trade_scf:S4,tb_trade_scf:F1                                # re-ask some questions only
Exit: 0 ok, 1 live-capture problems, 2 curation / input errors.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (str(REPO / "src"), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import yaml  # noqa: E402

from smbc_genie_lib import genie as g  # noqa: E402
from smbc_genie_lib.config import default_warehouse_id, require_warehouse  # noqa: E402
from smbc_genie_lib.metrics import SPACES  # noqa: E402
from genie_ws import first_line, sql_str  # noqa: E402  (the SDK import inside Workspace stays deferred)

TAG = "[p09]"
CURATION_PATH = g.GENIE_DIR / "_question_bank.yaml"
DOC_PATH = REPO / "docs" / "GENIE_QUESTIONS.md"
CSV_PATH = REPO / "docs" / "genie_questions.csv"
METRICS_DIR = REPO / "metrics"
SCRATCH = REPO / "scratch"
LEDGER_PATH = SCRATCH / "question_bank_conversations.jsonl"
RAW_DIR = SCRATCH / "question_bank_raw"
IP_FLAG = SCRATCH / "handover_ip_blocked.flag"
CACHE_NAME = "question_bank.json"
TITLE_PREFIX = "APAC Genie - "

TOP_KEYS = {"workspace_url", "guide", "tables", "agents"}
AGENT_KEYS = {"tier", "audience", "job", "knows", "starters", "themes", "more", "flaky", "follow_ups", "routing",
              "summary_columns", "formats"}
STARTER_KEYS = {"benchmark", "also", "need", "filter", "note", "note_sql"}
THEME_KEYS = {"name", "keys"}
FLAKY_KEYS = {"why", "safer"}
FOLLOW_KEYS = {"after", "ask", "expect_in_sql", "benchmark", "need", "why"}
ROUTE_KEYS = {"ask", "agent"}
UNITS = {"usd", "pct", "pts", "bps", "days", "hours", "months", "count", "num", "multiple", "text", "date", "month"}
MARK = {"match": "✅", "differs": "⚠️", "error": "❌", None: "–"}
CAL_FIELDS = ("Date", "Month", "Fiscal Year")          # calendar block fields, finest first (metric views)
_MSG_DONE = {"COMPLETED", "FAILED", "CANCELLED", "QUERY_RESULT_EXPIRED"}


class CurationError(ValueError):
    """genie/_question_bank.yaml does not fit the spaces."""


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def sha(text: str) -> str:
    return hashlib.blake2b(str(text).encode("utf-8"), digest_size=8).hexdigest()


# ---- inputs -------------------------------------------------------------------------------------------
@dataclass
class Agent:
    slug: str
    number: int                                 # brief §5 order (metrics.SPACES)
    title: str
    space_id: Optional[str]
    samples: List[str]                          # space.yaml sample_questions, display order
    data_sources: List[str]
    benchmarks: List[Dict[str, Any]]            # benchmarks.json, authoring order
    evals: Dict[str, Dict[str, Any]]            # benchmark key -> eval_report.json entry
    eval_summary: Dict[str, Any]
    answers: Dict[str, Any]                     # answers-file qid -> metrics.Answer (the brief's wording)
    cur: Dict[str, Any]                         # this agent's curation
    cache: Dict[str, Any]                       # question_bank.json

    @property
    def short_title(self) -> str:
        return self.title[len(TITLE_PREFIX):] if self.title.startswith(TITLE_PREFIX) else self.title

    @property
    def by_key(self) -> Dict[str, Dict[str, Any]]:
        return {b["key"]: b for b in self.benchmarks}

    def variants(self, key: str) -> List[Dict[str, Any]]:
        return [b for b in self.benchmarks if b.get("variant_of") == key]

    def url(self, host: str) -> str:
        return f"{host.rstrip('/')}/genie/rooms/{self.space_id}" if self.space_id else ""


def load_curation(path: Path = CURATION_PATH) -> Dict[str, Any]:
    d = yaml.safe_load(Path(path).read_text()) or {}
    if not isinstance(d, dict) or not isinstance(d.get("agents"), dict):
        raise CurationError(f"{path}: needs a mapping 'agents'")
    return d


def _read_json(path: Path, default: Any) -> Any:
    return json.loads(path.read_text()) if path.exists() else default


def load_agent(slug: str, cur: Mapping[str, Any], genie_dir: Path = g.GENIE_DIR,
               answers_dir: Path = g.ANSWERS_DIR) -> Agent:
    src = g.load_space(slug, genie_dir)
    d = Path(genie_dir) / slug
    rep = _read_json(d / "eval_report.json", {})
    sid_path = d / "space_id"
    sources = [g.render(ds.get("identifier", ""), {"catalog": "smbc_genie"}).replace("`", "").lower()
               for ds in src.raw.get("data_sources") or []]
    return Agent(slug=slug, number=SPACES.get(slug, (99, ""))[0], title=g.one_line(src.raw.get("title")),
                 space_id=(sid_path.read_text().strip() if sid_path.exists() else "")
                 or (rep.get("summary") or {}).get("space_id") or None,   # no space_id file: the evaluated space
                 samples=[g.one_line(q) for q in src.raw.get("sample_questions") or []], data_sources=sources,
                 benchmarks=_read_json(d / "benchmarks.json", []),
                 evals={b["key"]: b for b in rep.get("benchmarks") or []}, eval_summary=rep.get("summary") or {},
                 answers=g.load_answers(slug, answers_dir), cur=dict((cur.get("agents") or {}).get(slug) or {}),
                 cache=_read_json(d / CACHE_NAME, {}))


def load_agents(cur: Mapping[str, Any], slugs: Optional[Sequence[str]] = None, genie_dir: Path = g.GENIE_DIR,
                answers_dir: Path = g.ANSWERS_DIR) -> List[Agent]:
    slugs = list(slugs) if slugs else g.space_slugs(genie_dir)
    return sorted((load_agent(s, cur, genie_dir, answers_dir) for s in slugs), key=lambda a: (a.number, a.slug))


def _unknown(where: str, d: Any, allowed: set) -> List[str]:
    if not isinstance(d, dict):
        return [f"{where}: must be a mapping"]
    return [f"{where}: unknown key '{k}' (allowed: {', '.join(sorted(allowed))})" for k in d if k not in allowed]


def check_curation(cur: Mapping[str, Any], agents: Sequence[Agent]) -> List[str]:
    """Problems that would make the document wrong or incomplete (empty list = ok)."""
    errs = _unknown("_question_bank.yaml", cur, TOP_KEYS)
    for slug in cur.get("agents") or {}:
        if slug not in SPACES:
            errs.append(f"agents.{slug}: not a Genie space slug")
    for a in agents:
        c, w = a.cur, f"agents.{a.slug}"
        if not c:
            errs.append(f"{w}: missing")
            continue
        errs += _unknown(w, c, AGENT_KEYS)
        keys = a.by_key
        for k in ("tier", "audience", "job", "knows"):
            if not g.one_line(c.get(k)):
                errs.append(f"{w}.{k}: empty")
        st = c.get("starters") or []
        if len(st) != len(a.samples):
            errs.append(f"{w}.starters: {len(st)} entries for {len(a.samples)} sample questions in space.yaml")
        for i, s in enumerate(st):
            errs += _unknown(f"{w}.starters[{i}]", s, STARTER_KEYS)
            bk = (s or {}).get("benchmark") if isinstance(s, dict) else None
            for k in [bk] + list((s or {}).get("also") or []):
                if k not in keys:
                    errs.append(f"{w}.starters[{i}]: '{k}' is not a benchmark key")
            if bk in keys:
                exp_cols = (a.cache.get("answers") or {}).get(bk, {}).get("columns")
                for col in list((s or {}).get("need") or []) + list(((s or {}).get("filter") or {})):
                    if exp_cols is not None and col not in exp_cols:
                        errs.append(f"{w}.starters[{i}]: need / filter column '{col}' is not a column of {bk} "
                                    f"({', '.join(exp_cols)})")
        listed = [k for t in c.get("themes") or [] for k in (t or {}).get("keys") or []] + list(c.get("more") or [])
        for i, t in enumerate(c.get("themes") or []):
            errs += _unknown(f"{w}.themes[{i}]", t, THEME_KEYS)
            if not g.one_line((t or {}).get("name")):
                errs.append(f"{w}.themes[{i}]: needs a name")
        for k, n in Counter(listed).items():
            if n > 1:
                errs.append(f"{w}: benchmark {k} listed {n} times in themes / more")
        for k in listed:
            if k not in keys:
                errs.append(f"{w}: themes / more list '{k}', which is not a benchmark key")
            elif keys[k].get("variant_of"):
                errs.append(f"{w}: '{k}' is a variant of {keys[k]['variant_of']} - list only its base")
        for b in a.benchmarks:
            if not b.get("variant_of") and b["key"] not in listed:
                errs.append(f"{w}: base benchmark {b['key']} is in no theme (and not under more)")
        flaky = c.get("flaky") or {}
        for k, f in flaky.items():
            errs += _unknown(f"{w}.flaky.{k}", f, FLAKY_KEYS)
            if k not in keys:
                errs.append(f"{w}.flaky: '{k}' is not a benchmark key")
            if not g.one_line((f or {}).get("safer")) or not g.one_line((f or {}).get("why")):
                errs.append(f"{w}.flaky.{k}: needs why and safer")
        for k, e in a.evals.items():
            if not e.get("pass") and k not in flaky:
                errs.append(f"{w}: benchmark {k} failed its last evaluation - add it under flaky (why + safer wording)")
        for i, f in enumerate(c.get("follow_ups") or []):
            errs += _unknown(f"{w}.follow_ups[{i}]", f, FOLLOW_KEYS)
            m = re.match(r"^S(\d+)$", str((f or {}).get("after")))
            if not m or not 1 <= int(m.group(1)) <= len(a.samples):
                errs.append(f"{w}.follow_ups[{i}]: after must be S1..S{len(a.samples)}")
            if not g.one_line((f or {}).get("ask")):
                errs.append(f"{w}.follow_ups[{i}]: empty ask")
            if (f or {}).get("benchmark") is not None and f["benchmark"] not in keys:
                errs.append(f"{w}.follow_ups[{i}]: '{f['benchmark']}' is not a benchmark key")
        for i, r in enumerate(c.get("routing") or []):
            errs += _unknown(f"{w}.routing[{i}]", r, ROUTE_KEYS)
            if (r or {}).get("agent") not in SPACES or (r or {}).get("agent") == a.slug:
                errs.append(f"{w}.routing[{i}]: agent must be another space slug")
        for k, fm in (c.get("formats") or {}).items():
            if k not in keys:
                errs.append(f"{w}.formats: '{k}' is not a benchmark key")
            for col, unit in (fm or {}).items():
                if unit not in UNITS:
                    errs.append(f"{w}.formats.{k}.{col}: unit '{unit}' not in {sorted(UNITS)}")
        for k in c.get("summary_columns") or {}:
            if k not in keys:
                errs.append(f"{w}.summary_columns: '{k}' is not a benchmark key")
    return errs


# ---- units and formatting ------------------------------------------------------------------------------
def load_measure_units(metrics_dir: Path = METRICS_DIR) -> Dict[str, Dict[str, str]]:
    """view -> measure name -> unit, from the `format` of every measure in metrics/<view>.yaml."""
    out: Dict[str, Dict[str, str]] = {}
    for p in sorted(Path(metrics_dir).glob("mv_*.yaml")):
        spec = yaml.safe_load(p.read_text()) or {}
        units = {}
        for m in spec.get("measures") or []:
            fmt, name = m.get("format") or {}, str(m.get("name") or "")
            kind = fmt.get("type")
            if kind == "currency":
                units[name] = "usd"
            elif kind == "percentage":
                units[name] = "pct"
            elif kind == "number":
                places = fmt.get("decimal_places") or {}
                units[name] = _name_unit(name) or \
                    ("count" if places.get("type") == "exact" and int(places.get("places") or 0) == 0 else "num")
        out[p.stem] = units
    return out


_MEASURE_ALIAS_RE = re.compile(r"MEASURE\(\s*`([^`]+)`\s*\)\s*(?:AS\s+)?`?([A-Za-z_]\w*)`?", re.I)
_VIEW_RE = re.compile(r"\bmetrics`?\s*\.\s*`?(mv_\w+)", re.I)
_TOKENS = {"h1": "H1", "h2": "H2", "rm": "RM", "rms": "RMs", "casa": "CASA", "td": "TD", "rorwa": "RoRWA",
           "roe": "ROE", "raroc": "RAROC", "ews": "EWS", "dpd": "DPD", "scf": "SCF", "lc": "LC", "lcs": "LCs",
           "kyc": "KYC", "er": "ER", "rcf": "RCF", "mape": "MAPE", "ltv": "LTV", "icr": "ICR", "dscr": "DSCR",
           "yoy": "YoY", "ytd": "YTD", "fytd": "FYTD", "apac": "APAC", "rwa": "RWA", "ecl": "ECL", "ead": "EAD",
           "fx": "FX", "tb": "TB", "stp": "STP", "jp": "JP", "sg": "SG", "hk": "HK", "dq": "DQ", "cv": "CV",
           "p10": "P10", "p90": "P90", "p25": "P25", "p75": "P75", "sla": "SLA", "nii": "NII", "ebitda": "EBITDA",
           "id": "id", "pct": "", "usd": "", "12m": "12M", "30d": "30D", "90d": "90D", "nbp": "NBP", "jc": "JC",
           "cn": "CN", "vn": "VN", "stage3": "Stage 3", "sblc": "SBLC", "sblcs": "SBLCs", "dso": "DSO", "dio": "DIO",
           "dpo": "DPO"}


def _name_unit(name: str) -> Optional[str]:
    n = name.lower()
    if "bps" in n:
        return "bps"
    if re.search(r"(^|[_\s])(days?|dso|dio|dpo)([_\s]|$)", n) or "days" in n:
        return "days"
    if "hours" in n or re.search(r"(^|[_\s])hrs?([_\s]|$)", n):
        return "hours"
    if re.search(r"(^|[_\s])months?([_\s]|$)", n) and "month_end" not in n:
        return "months"
    return None


def _num(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def infer_units(sql: str, cols: Sequence[str], types: Sequence[str], head: Sequence[Sequence[Any]],
                measure_units: Mapping[str, Mapping[str, str]], overrides: Optional[Mapping[str, str]] = None) -> List[str]:
    """One unit per result column: curated override > the metric view measure's format > the column name >
    the SQL type. Percentages are fractions (0.25 = 25%), as in the metric views."""
    views = [v.lower() for v in _VIEW_RE.findall(sql or "")]
    alias_measure = {a.lower(): m for m, a in _MEASURE_ALIAS_RE.findall(sql or "")}
    out = []
    for j, col in enumerate(cols):
        t = (types[j] if j < len(types) else "").upper()
        lc = str(col).lower()
        unit = (overrides or {}).get(col)
        if unit is None and t in ("DATE", "TIMESTAMP", "TIMESTAMP_NTZ"):
            unit = "month" if re.search(r"month", lc) and all(str(r[j] or "")[8:10] in ("", "01") for r in head) \
                else "date"
        if unit is None and t in ("STRING", "BOOLEAN", "BINARY", "ARRAY", "MAP", "STRUCT"):
            unit = "text"
        if unit is None and lc in alias_measure:
            m = alias_measure[lc]
            for v in views + [v for v in measure_units if v not in views]:
                if m in measure_units.get(v, {}):
                    unit = measure_units[v][m]
                    break
            if unit in ("num", "count"):            # a plain number measure: the alias may name the unit
                unit = _name_unit(lc) or unit
        if unit is None:
            vals = [x for x in (_num(r[j]) for r in head if j < len(r)) if x is not None]
            unit = _name_unit(lc)
            if unit is None and re.search(r"(^|_)usd($|_)", lc):
                unit = "usd"
            if unit is None and re.search(r"(^|_)rank($|_)", lc):
                unit = "count"
            if unit is None and re.search(r"(^|_)pts($|_)", lc) and all(abs(x) <= 1 for x in vals):
                unit = "pts"
            if unit is None and re.search(r"pct|percent|rate|share|ratio|attainment|yoy|growth|mape|bias|coverage|"
                                          r"utili[sz]ation|penetration|completeness|precision|recall|rorwa|raroc|roe|"
                                          r"ltv|headroom|concentration|conversion|margin|propensity", lc) \
                    and lc not in ("ratio_value",) and all(abs(x) <= 5 for x in vals):
                unit = "pct"
            if unit is None:
                unit = "count" if t in ("INT", "LONG", "BIGINT", "SHORT", "TINYINT", "BYTE") else "num"
        out.append(unit)
    return out


def fmt_value(v: Any, unit: str) -> str:
    if v is None or (isinstance(v, str) and v.strip() == ""):
        return "–"
    if unit in ("text",):
        s = g.one_line(v)
        return {"true": "yes", "false": "no"}.get(s.lower(), s if len(s) <= 70 else s[:67] + "...")
    if unit in ("date", "month"):
        s = str(v)[:10]
        try:
            d = dt.date.fromisoformat(s)
        except ValueError:
            return g.one_line(v)
        return d.strftime("%b %Y") if unit == "month" else f"{d.day} {d.strftime('%b %Y')}"
    x = _num(v)
    if x is None:
        return g.one_line(v)
    if unit == "usd":
        a, sign = abs(x), "-" if x < 0 else ""
        if a >= 1e9:
            return f"USD {sign}{a / 1e9:.2f}bn"
        if a >= 1e6:
            return f"USD {sign}{a / 1e6:.{2 if a < 1e7 else 1}f}m"
        if a >= 1e3:
            return f"USD {sign}{a / 1e3:.0f}k"
        return f"USD {sign}{a:,.0f}"
    if unit == "pct":
        return f"{x * 100:.1f}%"
    if unit == "pts":
        return f"{x * 100:+.1f} pts"
    if unit == "bps":
        return f"{x:,.0f} bps"
    if unit == "days":
        n = f"{x:,.0f}" if x == int(x) or abs(x) >= 100 else f"{x:,.1f}"
        return f"{n} day" if n == "1" else f"{n} days"
    if unit == "hours":
        return f"{x:,.1f} h"
    if unit == "months":
        return f"{x:,.1f} months"
    if unit == "multiple":
        return f"{x:.2f}x"
    if unit == "count":
        return f"{x:,.0f}"
    a = abs(x)
    if a >= 1000:
        return f"{x:,.0f}"
    if a >= 10 or x == int(x):
        return f"{x:,.1f}".rstrip("0").rstrip(".")
    return f"{x:.3g}"


def col_label(name: str, keep_units: bool = False) -> str:
    """Readable column label: metric-view names stay as they are, snake_case aliases become words (the unit words
    usd / pct are dropped where the value shows the unit, kept in grader notes)."""
    if not re.match(r"^[a-z0-9_]+$", str(name)):
        return str(name)
    tokens = {**_TOKENS, "usd": "USD", "pct": "%"} if keep_units else _TOKENS
    words = [tokens.get(w, w.upper() if re.match(r"^(fy\d{2,4}|q\d|h\d|crm|kpi)$", w) else w)
             for w in str(name).split("_")]
    return " ".join(w for w in words if w) or str(name)


def summary_columns(cols: Sequence[str], units: Sequence[str], pick: Optional[Sequence[str]] = None,
                    n_rows: int = 0) -> List[int]:
    if pick:
        return [cols.index(c) for c in pick if c in cols]
    if len(cols) <= 6 or (n_rows <= 3 and len(cols) <= 10):
        return list(range(len(cols)))
    labels = [j for j, u in enumerate(units) if u in ("text", "date", "month")][:2]
    nums = [j for j, u in enumerate(units) if u not in ("text", "date", "month")][:4]
    return sorted(labels + nums)


def _value_in_label(col: str, v: Any, unit: str) -> str:
    """The value without its unit word when the column label already says it ("days open 237", "margin bps 185")."""
    word = {"days": "day", "hours": "hour", "months": "month", "bps": "bps"}.get(unit)
    s = fmt_value(v, unit)
    if word and word in str(col).lower():
        s = re.sub(r"\s*(days?|h|months|bps)$", "", s)
    return s


def summarize_answer(ans: Mapping[str, Any], units: Sequence[str], pick: Optional[Sequence[str]] = None,
                     max_rows: int = 3) -> str:
    """'n rows: first row; second row; ...' - labels first, then 'measure value' pairs."""
    if ans.get("error"):
        return f"expected SQL failed: {ans['error']}"
    n = int(ans.get("rows") or 0)
    if n == 0:
        return "no rows"
    cols, head = ans.get("columns") or [], ans.get("head") or []
    idx = summary_columns(cols, units, pick, n)
    parts = []
    for r in head[:max_rows]:
        lab = [fmt_value(r[j], units[j]) for j in idx if units[j] in ("text", "date", "month")]
        val = [f"{col_label(cols[j])} {_value_in_label(cols[j], r[j], units[j])}" for j in idx
               if units[j] not in ("text", "date", "month")]
        lead = " · ".join(lab)
        parts.append(f"{lead}: {', '.join(val)}" if lead and val else (lead or ", ".join(val)))
    more = "; …" if n > max_rows else ""
    by = f" (largest {col_label(ans['sorted_by'])} first)" if ans.get("sorted_by") and n > max_rows else ""
    return f"{n:,} row{'s' if n != 1 else ''}{by}: " + "; ".join(parts) + more


# ---- grading -------------------------------------------------------------------------------------------
def _norm(v: Any) -> Tuple[int, Any]:
    """genie.normalize_value, with booleans as 1 / 0 (a flag answers the same question as a 0/1 count)."""
    k, x = g.normalize_value(v)
    return (2, 1.0 if x == "true" else 0.0) if k == 1 else (k, x)


def _colmajor(cols: Sequence[str], rows: Sequence[Sequence[Any]]) -> List[List[Tuple[int, Any]]]:
    return [[_norm(r[j] if j < len(r) else None) for r in rows] for j in range(len(cols))]


def _numeric(col: Sequence[Tuple[int, Any]]) -> bool:
    kinds = {k for k, _ in col if k != 0}
    return kinds == {2}


def _div100(col: Sequence[Tuple[int, Any]]) -> List[Tuple[int, Any]]:
    return [(k, float(f"{x / 100:.{g.SIG_DIGITS - 1}e}")) if k == 2 and x else (k, x) for k, x in col]


def assign_columns(E: Sequence[Sequence[Tuple[int, Any]]], A: Sequence[Sequence[Tuple[int, Any]]],
                   contain: bool = False, scaled: Optional[set] = None) -> Optional[Dict[int, int]]:
    """Injective map expected column -> actual column such that the multiset of expected row tuples equals
    (contain=True: is contained in) the multiset of actual row tuples on the mapped columns; None if none.
    With `scaled` (a set to fill), an actual column may also match a fraction column x 100 (a percentage shown
    0-100); the expected columns matched that way are added to the set."""
    k = len(E)
    if k == 0:
        return {}
    A100 = [_div100(c) for c in A] if scaled is not None else None
    fraction = [_numeric(c) and all(abs(x) <= 10 for kk, x in c if kk == 2)          # ratio-like, not counts
                and any(x != int(x) for kk, x in c if kk == 2) for c in E]

    def fits_col(e: Sequence[Tuple[int, Any]], col: Sequence[Tuple[int, Any]]) -> bool:
        return not (Counter(e) - Counter(col)) if contain else sorted(e) == sorted(col)

    cands = [[(a, False) for a in range(len(A)) if fits_col(E[j], A[a])] +
             ([(a, True) for a in range(len(A)) if fraction[j] and fits_col(E[j], A100[a])] if A100 else [])
             for j in range(k)]
    if any(not c for c in cands):
        return None
    n_e, n_a = len(E[0]), len(A[0]) if A else 0
    target = Counter(tuple(E[j][i] for j in range(k)) for i in range(n_e))
    order = sorted(range(k), key=lambda j: len(cands[j]))
    chosen: Dict[int, Tuple[int, bool]] = {}

    def fits() -> bool:
        cols = {j: (A100[a] if s else A[a]) for j, (a, s) in chosen.items()}
        got = Counter(tuple(cols[j][i] for j in range(k)) for i in range(n_a))
        return not (target - got) if contain else got == target

    def search(t: int) -> bool:
        if t == k:
            return fits()
        j = order[t]
        for a, s in cands[j]:
            if a in {x for x, _ in chosen.values()}:
                continue
            chosen[j] = (a, s)
            if search(t + 1):
                return True
            del chosen[j]
        return False

    if not search(0):
        return None
    if scaled is not None:
        scaled.update(j for j, (_, s) in chosen.items() if s)
    return {j: a for j, (a, _) in chosen.items()}


def _rows(n: int) -> str:
    return f"{n} row{'' if n == 1 else 's'}"


def _cols(n: int) -> str:
    return f"{n} column{'' if n == 1 else 's'}"


def grade_result(exp_cols: Sequence[str], exp_rows: Sequence[Sequence[Any]], act_cols: Sequence[str],
                 act_rows: Sequence[Sequence[Any]], need: Sequence[str] = ()) -> Tuple[str, str]:
    """('match' | 'differs', one-line note) for Genie's result against a benchmark's expected result."""
    E, A = _colmajor(exp_cols, exp_rows), _colmajor(act_cols, act_rows)
    ne, na = len(exp_rows), len(act_rows)
    exp_cols, act_cols = [col_label(c, True) for c in exp_cols], [col_label(c, True) for c in act_cols]  # for notes
    need = [col_label(c, True) for c in need]
    if na == 0:
        return "differs", f"Genie's query returned no rows (benchmark: {_rows(ne)})"
    pct: set = set()

    def pct_note(cols: Sequence[str]) -> str:
        return f"; Genie shows {', '.join(cols)} on a 0-100 scale" if cols else ""

    if ne == na:
        if assign_columns(E, A, scaled=pct) is not None:
            extra = len(act_cols) - len(exp_cols)
            return "match", ("same rows as the benchmark" + (f"; Genie adds {_cols(extra)}" if extra else "")
                             + pct_note([exp_cols[j] for j in sorted(pct)]))
        se, sa, s100 = [sorted(c) for c in E], [sorted(c) for c in A], [sorted(_div100(c)) for c in A]
        shared = [j for j in range(len(E)) if any(sa[a] == se[j] or (_numeric(E[j]) and s100[a] == se[j])
                                                  for a in range(len(A)))]   # (x100 confirmed by assign_columns)
        mm = assign_columns([E[j] for j in shared], A, scaled=pct) if shared else None
        if mm is not None:                      # the rows agree on every column both answers show
            pct_cols = [exp_cols[shared[j]] for j in sorted(pct)]
            used = set(mm.values())
            left = [j for j in range(len(E)) if j not in shared]
            figs_left = [exp_cols[j] for j in left if _numeric(E[j])]
            genie_figs = [act_cols[a] for a in range(len(A)) if a not in used and _numeric(A[a])]
            labels_left = [exp_cols[j] for j in left if not _numeric(E[j])]
            genie_labels = [act_cols[a] for a in range(len(A)) if a not in used and not _numeric(A[a])]
            missing = [c for c in need if c in exp_cols and exp_cols.index(c) in left]
            if missing:
                return "differs", f"same rows, but without {', '.join(missing)}"
            if not need and labels_left and genie_labels:    # an unmatched label next to an unexplained one
                return "differs", f"same {_rows(ne)}; {', '.join(labels_left[:3])} differ " \
                                  f"(Genie: {', '.join(genie_labels[:3])})"
            if not need and figs_left and genie_figs:
                return "differs", f"same {_rows(ne)}; values differ for {', '.join(figs_left[:4])} " \
                                  f"(Genie: {', '.join(genie_figs[:4])})"
            if not need and figs_left and not any(_numeric(E[j]) for j in shared):
                return "differs", f"same {_rows(ne)}, but none of the benchmark's figures ({', '.join(figs_left[:4])})"
            extra = len(act_cols) - len(used)
            return "match", (f"same rows; Genie shows {len(shared)} of the benchmark's {len(exp_cols)} columns"
                             + (f" (not shown: {', '.join(exp_cols[j] for j in left)})" if left else "")
                             + (f"; adds {_cols(extra)}" if extra else "") + pct_note(pct_cols))
    labels = [j for j in range(len(E)) if not _numeric(E[j])]
    if na > ne:
        if assign_columns(E, [c[:ne] for c in A]) is not None:
            return "differs", f"Genie lists {_rows(na)}; its first {ne} are exactly the benchmark's answer"
        if assign_columns(E, A, contain=True) is not None:
            return "differs", f"Genie returns {_rows(na)}, the benchmark {ne}; all benchmark rows are included"
        if labels and assign_columns([E[j] for j in labels], A, contain=True) is not None:
            return "differs", f"Genie returns {_rows(na)}, the benchmark {ne}; the benchmark's rows are included " \
                              f"but some figures differ"
        return "differs", f"Genie returns {_rows(na)}, the benchmark {ne}"
    if na < ne:
        if assign_columns(A, E, contain=True) is not None:
            return "differs", f"Genie returns {na} of the benchmark's {ne} rows"
        return "differs", f"Genie returns {_rows(na)}, the benchmark {ne}"
    se, sa = [sorted(c) for c in E], [sorted(c) for c in A]
    unmatched = [exp_cols[j] for j in range(len(E)) if not any(sa[a] == se[j] for a in range(len(A)))]
    if unmatched:
        return "differs", f"same row count ({ne}); no Genie column matches {', '.join(unmatched[:4])}"
    return "differs", f"same row count ({ne}); each column matches but the rows pair up differently"


def filter_rows(cols: Sequence[str], rows: Sequence[Sequence[Any]], where: Mapping[str, Any]) -> List[Sequence[Any]]:
    """Expected rows restricted to a starter's narrower ask, e.g. {Relationship Tier: Strategic}."""
    idx = {cols.index(c): {str(x) for x in (v if isinstance(v, list) else [v])} for c, v in (where or {}).items()
           if c in cols}
    return [r for r in rows if all(str(r[j]) in vs for j, vs in idx.items())]


def split_sections(cols: Sequence[str], rows: Sequence[Sequence[Any]]) -> Optional[List[Tuple[str, List[str], List[List[Any]]]]]:
    """Genie answers some two-part questions with one UNION table and a label column naming the part
    ('lowest_stp' / 'repair_concentration'). Returns [(part, columns, rows)] - each part without the label column
    and without the columns it leaves empty - or None when the result is not such a table."""
    for j, _ in enumerate(cols):
        vals = [r[j] for r in rows]
        names = sorted({str(v) for v in vals if v is not None})
        if not 2 <= len(names) <= 4 or any(v is None for v in vals) or all(_num(v) is not None for v in vals):
            continue
        parts: Dict[str, List[Sequence[Any]]] = {}
        for r in rows:
            parts.setdefault(str(r[j]), []).append(r)
        out = []
        for name, rs in parts.items():
            keep = [k for k in range(len(cols)) if k != j and any(r[k] is not None for r in rs)]
            out.append((name, [cols[k] for k in keep], [[r[k] for k in keep] for r in rs]))
        if len({tuple(c) for _, c, _ in out}) == len(out):      # a real union: every part has its own columns
            return out
    return None


def grade_item(raw_q: Mapping[str, Any], expected: Mapping[str, Mapping[str, Any]],
               keys: Sequence[str], need: Sequence[str] = (),
               where: Optional[Mapping[str, Any]] = None) -> Tuple[str, str, Optional[str]]:
    """(verdict, note, matched benchmark key) for one captured question against its benchmark(s); `need` and
    `where` (a row filter on the expected result) apply to the first benchmark only."""
    if raw_q.get("status") != "COMPLETED" or not raw_q.get("sql"):
        what = raw_q.get("error") or raw_q.get("text") or raw_q.get("status") or "no answer"
        return "error", f"{raw_q.get('status') or 'ERROR'}, no SQL: {g.one_line(what)[:160]}", None
    if raw_q.get("cols") is None:
        return "error", f"Genie's SQL could not be re-run: {g.one_line(raw_q.get('result_error'))[:160]}", None
    first = None
    for i, k in enumerate(keys):
        ex = expected.get(k)
        if not ex or ex.get("cols") is None:
            continue
        rows = filter_rows(ex["cols"], ex["rows"], where) if i == 0 and where else ex["rows"]
        v, note = grade_result(ex["cols"], rows, raw_q["cols"], raw_q["rows"], need if i == 0 else ())
        if i == 0 and where:
            cond = ", ".join(f"{c} = {' or '.join(map(str, v)) if isinstance(v, list) else v}" for c, v in where.items())
            note = f"{note} (benchmark rows where {cond})"
        if v == "match":
            return v, note if i == 0 else f"matches {k}: {note}", k
        if first is None:
            first = (v, note, None)
    parts = split_sections(raw_q["cols"], raw_q["rows"]) if len(keys) > 1 else None
    if parts:                                   # one block per part of the question: each must match a benchmark
        hits: Dict[str, str] = {}
        for name, pc, pr in parts:
            for k in keys:
                ex = expected.get(k)
                if k not in hits.values() and ex and ex.get("cols") is not None and \
                        grade_result(ex["cols"], ex["rows"], pc, pr)[0] == "match":
                    hits[name] = k
                    break
        if len(hits) == len(parts):
            return "match", "Genie answers each part in one table: " + "; ".join(
                f"its '{col_label(n, True)}' rows match {k}" for n, k in hits.items()), "+".join(hits.values())
    return first or ("error", "no expected result to compare with", None)


def grade_follow_up(raw_q: Mapping[str, Any], expect_in_sql: Sequence[str]) -> Tuple[str, str]:
    if raw_q.get("status") != "COMPLETED" or not raw_q.get("sql"):
        what = raw_q.get("error") or raw_q.get("text") or raw_q.get("status") or "no answer"
        return "error", f"{raw_q.get('status') or 'ERROR'}, no SQL: {g.one_line(what)[:160]}"
    low = bind_parameters(raw_q["sql"], raw_q.get("parameters") or []).lower()   # trusted queries bind values
    miss = [s for s in expect_in_sql if s.lower() not in low]
    n = raw_q.get("row_count")
    rows = f"{n} row{'' if n == 1 else 's'}"
    if miss:
        return "differs", f"answered ({rows}) but the SQL lacks {', '.join(repr(s) for s in miss)}"
    if not n:
        return "differs", "answered in the same conversation, but the query returned no rows"
    return "match", f"answered in the same conversation ({rows}), context kept"


# ---- live capture --------------------------------------------------------------------------------------
_IP_MARKERS = ("ip acl", "ip access list", "blocked by databricks ip")


def guarded(fn: Callable[..., Any], *args: Any, waits: int = 6, wait_s: int = 300, **kw: Any) -> Any:
    """Call a workspace function; on an IP access-list block write the flag and retry every 5 min (<= 30 min)."""
    for attempt in range(waits + 1):
        try:
            return fn(*args, **kw)
        except Exception as e:  # noqa: BLE001
            if attempt == waits or not any(m in str(e).lower() for m in _IP_MARKERS):
                raise
            SCRATCH.mkdir(exist_ok=True)
            IP_FLAG.write_text(f"{now_iso()} build_question_bank.py: blocked by Databricks IP ACL "
                               f"(attempt {attempt + 1}); retrying every {wait_s // 60} min: {first_line(e)}\n")
            print(f"{TAG}   IP ACL block - waiting {wait_s}s ({attempt + 1}/{waits})")
            time.sleep(wait_s)
    return None


class Ledger:
    """Append-only log of the conversations this script creates and deletes (rule: delete only our own)."""

    def __init__(self, path: Path = LEDGER_PATH):
        self.path = Path(path)

    def log(self, event: str, space_id: str, conversation_id: str, **kw: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a") as f:
            f.write(json.dumps({"event": event, "space_id": space_id, "conversation_id": conversation_id,
                                "at": now_iso(), **kw}, ensure_ascii=False) + "\n")

    def entries(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(x) for x in self.path.read_text().splitlines() if x.strip()]

    def pending(self) -> List[Tuple[str, str]]:
        live: Dict[Tuple[str, str], bool] = {}
        for e in self.entries():
            k = (e["space_id"], e["conversation_id"])
            if e["event"] == "created":
                live[k] = True
            elif e["event"] == "deleted":
                live.pop(k, None)
        return list(live)


def bind_parameters(sql: str, params: Sequence[Mapping[str, Any]]) -> str:
    """Genie's trusted-query SQL with its :parameters replaced by literals (outside quotes / comments)."""
    vals = {}
    for p in params or []:
        t, v = str(p.get("sql_type") or "STRING").upper(), p.get("value")
        if v is None:
            lit = "NULL"
        elif t == "STRING":
            lit = sql_str(v)
        elif t in ("DATE", "TIMESTAMP"):
            lit = f"{t}'{v}'"
        else:
            lit = str(v)
        vals[str(p.get("keyword"))] = lit
    out, i, n = [], 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in ("'", '"', "`"):
            j = i + 1
            while j < n and sql[j] != ch:
                j += 2 if sql[j] == "\\" and ch != "`" else 1
            out.append(sql[i:j + 1])
            i = j + 1
            continue
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j == -1 else j
            out.append(sql[i:j])
            i = j
            continue
        m = re.match(r":([A-Za-z_]\w*)", sql[i:])
        if m and (i == 0 or not re.match(r"[:\w]", sql[i - 1])) and m.group(1) in vals:
            out.append(vals[m.group(1)])
            i += m.end()
            continue
        out.append(ch)
        i += 1
    return "".join(out)


class Asker:
    """Conversation API client: one question in flight, >= spacing_s between question starts."""

    def __init__(self, ws: Any, ledger: Ledger, spacing_s: float = 13.0, timeout_s: int = 420):
        self.ws, self.ledger, self.spacing_s, self.timeout_s = ws, ledger, spacing_s, timeout_s
        self._last_start = 0.0

    def ask(self, sid: str, question: str, cid: Optional[str] = None, label: str = "") -> Dict[str, Any]:
        wait = self.spacing_s - (time.time() - self._last_start)
        if wait > 0:
            time.sleep(wait)
        self._last_start = t0 = time.time()
        out: Dict[str, Any] = {"question": question, "asked_at": now_iso(), "conversation_id": cid}
        try:
            if cid is None:
                r = guarded(self.ws.api, "POST", f"/api/2.0/genie/spaces/{sid}/start-conversation",
                            body={"content": question})
                cid = r.get("conversation_id") or (r.get("conversation") or {}).get("id")
                out["conversation_id"] = cid
                self.ledger.log("created", sid, cid, question=question, label=label)
                msg = r.get("message") or {}
                mid = r.get("message_id") or msg.get("id")
            else:
                r = guarded(self.ws.api, "POST", f"/api/2.0/genie/spaces/{sid}/conversations/{cid}/messages",
                            body={"content": question})
                msg = r.get("message") or r
                mid = r.get("message_id") or msg.get("message_id") or msg.get("id")
            base = f"/api/2.0/genie/spaces/{sid}/conversations/{cid}/messages/{mid}"
            while str(msg.get("status")).upper() not in _MSG_DONE:
                if time.time() - t0 > self.timeout_s:
                    raise RuntimeError(f"message still {msg.get('status')} after {self.timeout_s}s")
                time.sleep(3)
                msg = guarded(self.ws.api, "GET", base)
            out.update(self._read(base, msg))
        except Exception as e:  # noqa: BLE001
            out.update(status=out.get("status") or "ERROR", error=first_line(e, 300))
        out["seconds"] = round(time.time() - t0, 1)
        return out

    def _read(self, base: str, msg: Mapping[str, Any]) -> Dict[str, Any]:
        err = msg.get("error")
        res: Dict[str, Any] = {"status": msg.get("status"), "message_id": msg.get("message_id") or msg.get("id"),
                               "error": g.one_line(err.get("error") if isinstance(err, dict) else err) or None,
                               "sql": None, "parameters": [], "text": None, "suggested": [], "cols": None,
                               "rows": None, "row_count": None}
        query_att = None
        for att in msg.get("attachments") or []:
            if (att.get("query") or {}).get("query"):
                query_att = att
            elif (att.get("text") or {}).get("content"):
                res["text"] = att["text"]["content"]
            elif (att.get("suggested_questions") or {}).get("questions"):
                res["suggested"] = list(att["suggested_questions"]["questions"])
        if not query_att:
            return res
        q = query_att["query"]
        res.update(sql=q["query"], parameters=q.get("parameters") or [], description=q.get("description"),
                   row_count=(q.get("query_result_metadata") or {}).get("row_count"))
        typed = None
        try:
            qr = guarded(self.ws.api, "GET", f"{base}/attachments/{query_att['attachment_id']}/query-result")
            sr = qr.get("statement_response") or {}
            rr = sr.get("result") or {}
            if rr.get("data_array") is not None and "data_typed_array" not in rr:
                rr["data_typed_array"] = [{"values": [{"str": v} if v is not None else {} for v in row]}
                                          for row in rr["data_array"]]
            typed = g.typed_result({"status": sr.get("status"), "manifest": sr.get("manifest"), "result": rr})
        except Exception as e:  # noqa: BLE001 - fall back to re-running the SQL
            res["result_error"] = first_line(e)
        if typed is None:                       # partial / chunked result: re-run Genie's SQL (parameters bound)
            try:
                cols, _, rows = guarded(self.ws.sql, bind_parameters(res["sql"], res["parameters"]))
                typed, res["rerun"] = (cols, rows), True
            except Exception as e:  # noqa: BLE001
                res["result_error"] = first_line(e)
        if typed is not None:
            res["cols"], res["rows"] = list(typed[0]), [list(r) for r in typed[1]]
            res["row_count"] = len(res["rows"])
        return res

    def delete(self, sid: str, cid: Optional[str]) -> bool:
        if not cid:
            return True
        try:
            guarded(self.ws.api, "DELETE", f"/api/2.0/genie/spaces/{sid}/conversations/{cid}")
            self.ledger.log("deleted", sid, cid)
            return True
        except Exception as e:  # noqa: BLE001
            print(f"{TAG}   WARN could not delete conversation {cid}: {first_line(e)}")
            return False


def raw_path(slug: str) -> Path:
    return RAW_DIR / f"{slug}.json"


def load_raw(slug: str) -> Dict[str, Any]:
    return _read_json(raw_path(slug), {"expected": {}, "questions": {}})


def save_raw(slug: str, raw: Mapping[str, Any]) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    tmp = raw_path(slug).with_suffix(".tmp")
    tmp.write_text(json.dumps(raw, ensure_ascii=False) + "\n")
    tmp.replace(raw_path(slug))                    # atomic: a concurrent --regrade never reads half a file


def save_cache(a: Agent, genie_dir: Path = g.GENIE_DIR) -> None:
    a.cache["slug"], a.cache["space_id"] = a.slug, a.space_id
    (Path(genie_dir) / a.slug / CACHE_NAME).write_text(json.dumps(a.cache, indent=2, ensure_ascii=False) + "\n")


_ORDER_RE = re.compile(r"\border\s+by\b", re.I)


def answer_head(sql: str, cols: Sequence[str], types: Sequence[str], rows: Sequence[Sequence[Any]],
                n: int = 3) -> Tuple[List[List[Any]], Optional[str]]:
    """The first n rows of an expected result. Without an ORDER BY the warehouse order is arbitrary, so the rows
    are taken largest first on the first figure column instead (returned as the sort column)."""
    if _ORDER_RE.search(g._strip_sql_noise(sql or "")) or len(rows) <= n:
        return [list(r) for r in rows[:n]], None
    num = [j for j, t in enumerate(types) if str(t).upper() in ("DOUBLE", "DECIMAL", "FLOAT", "LONG", "INT", "BIGINT",
                                                                 "SHORT", "INTEGER")]
    if not num:
        return [list(r) for r in rows[:n]], None
    j = num[0]
    key = lambda r: (_num(r[j]) is None, -(_num(r[j]) or 0.0), [str(x) for x in r])  # noqa: E731
    return [list(r) for r in sorted(rows, key=key)[:n]], cols[j]


def capture_answers(ws: Any, a: Agent, raw: Dict[str, Any]) -> int:
    """Expected SQL of every benchmark -> cache['answers'] (+ full rows in raw); metric-view periods."""
    answers, memo, problems = {}, {}, 0
    for b in a.benchmarks:
        sql, t0 = b["expected_sql"], time.time()
        h = sha(sql)
        if h not in memo:                   # variants often share their base's expected SQL: run it once
            try:
                cols, types, rows = guarded(ws.sql, sql)
                head, by = answer_head(sql, cols, types, rows)
                memo[h] = ({"rows": len(rows), "columns": cols, "types": types, "head": head, "sorted_by": by,
                            "error": None}, {"cols": cols, "rows": rows})
            except Exception as e:  # noqa: BLE001
                memo[h] = ({"rows": None, "columns": [], "types": [], "head": [], "error": first_line(e)}, None)
                problems += 1
        summ, full = memo[h]
        answers[b["key"]] = {**summ, "sql_sha": h}
        if full is not None:
            raw["expected"][b["key"]] = full
        r = answers[b["key"]]
        print(f"{TAG}   answer {a.slug}:{b['key']:12} {'ERR ' + r['error'][:80] if r['error'] else str(r['rows']) + ' rows'}"
              f"  ({time.time() - t0:.1f}s)")
    coverage = []
    for ident in a.data_sources:
        dg = g.load_schema([ident]).get(ident) or {}
        names = [c["name"] for c in dg.get("columns") or []]
        fld = next((f for f in CAL_FIELDS if f in names), None) if dg.get("type") == "METRIC_VIEW" else None
        ent: Dict[str, Any] = {"asset": ident, "type": dg.get("type"), "field": fld}
        if fld:
            try:
                _, _, rows = guarded(ws.sql, f"SELECT min(`{fld}`) AS first, max(`{fld}`) AS last, count(*) AS periods "
                                              f"FROM (SELECT `{fld}` FROM {ident} GROUP BY ALL)")
                ent.update(first=rows[0][0], last=rows[0][1], periods=int(rows[0][2]))
            except Exception as e:  # noqa: BLE001
                ent["error"] = first_line(e)
                problems += 1
        coverage.append(ent)
    a.cache.update(answers=answers, coverage=coverage, answers_captured_at=now_iso())
    return problems


def question_plan(a: Agent) -> List[Dict[str, Any]]:
    """Starter questions, safer wordings and follow-ups in asking order (follow-ups right after their starter)."""
    plan, fups = [], {}
    for i, f in enumerate(a.cur.get("follow_ups") or []):
        fups.setdefault(str(f["after"]), []).append({"kind": "follow_up", "id": f"F{i + 1}", "after": str(f["after"]),
                                                     "question": g.one_line(f["ask"]), "benchmark": f.get("benchmark"),
                                                     "also": [], "need": list(f.get("need") or []),
                                                     "expect_in_sql": list(f.get("expect_in_sql") or [])})
    for i, q in enumerate(a.samples):
        st = (a.cur.get("starters") or [])[i] if i < len(a.cur.get("starters") or []) else {}
        sid = f"S{i + 1}"
        plan.append({"kind": "starter", "id": sid, "question": q, "benchmark": (st or {}).get("benchmark"),
                     "also": list((st or {}).get("also") or []), "need": list((st or {}).get("need") or []),
                     "filter": dict((st or {}).get("filter") or {}), "follow_ups": fups.get(sid, [])})
    for k, f in (a.cur.get("flaky") or {}).items():
        plan.append({"kind": "safer", "id": k, "question": g.one_line(f["safer"]), "benchmark": k, "also": [],
                     "need": [], "follow_ups": []})
    return plan


def regrade(a: Agent, raw: Mapping[str, Any]) -> None:
    """Recompute every verdict (and the answer heads) in the cache from the raw results (offline)."""
    for k, ans in (a.cache.get("answers") or {}).items():
        ex = (raw.get("expected") or {}).get(k)
        if k in a.by_key and ex and ex.get("cols") == ans.get("columns") and len(ex.get("rows") or []) == ans.get("rows"):
            ans["head"], ans["sorted_by"] = answer_head(a.by_key[k]["expected_sql"], ex["cols"], ans.get("types") or [],
                                                        ex["rows"])
    items = []
    for it in question_plan(a):
        for x in [it] + it.get("follow_ups", []):
            rq = (raw.get("questions") or {}).get(x["id"])
            if not rq or rq.get("question") != x["question"]:
                continue                                   # not captured, or captured for an older wording
            if x["kind"] == "follow_up" and not x.get("benchmark"):
                v, note, hit = *grade_follow_up(rq, x["expect_in_sql"]), None
            else:
                keys = [k for k in [x.get("benchmark")] + x.get("also", []) if k]
                v, note, hit = grade_item(rq, raw.get("expected") or {}, keys, x.get("need", []), x.get("filter"))
            items.append({"kind": x["kind"], "id": x["id"], "question": x["question"], "after": x.get("after"),
                          "benchmark": x.get("benchmark"), "matched": hit, "verdict": v, "note": note,
                          "status": rq.get("status"), "genie_sql": rq.get("sql"),
                          "genie_parameters": rq.get("parameters") or [], "genie_columns": rq.get("cols"),
                          "genie_rows": rq.get("row_count"), "seconds": rq.get("seconds"),
                          "asked_at": rq.get("asked_at"), "conversation_deleted": rq.get("deleted")})
    a.cache["questions"] = items


def capture_questions(ws: Any, asker: Asker, a: Agent, raw: Dict[str, Any], ids: Sequence[str] = ()) -> int:
    """Ask the planned questions (all, or those in ids), keep the raw results, delete the conversations."""
    if not a.space_id:
        print(f"{TAG}   {a.slug}: no space_id - skipped")
        return 1
    want = set(ids)
    problems = 0
    for it in question_plan(a):
        todo = [x for x in [it] + it.get("follow_ups", []) if not want or x["id"] in want]
        if not todo:
            continue
        host = it["kind"] == "starter" and any(x["kind"] == "follow_up" for x in todo) and it not in todo
        if host:                                           # a follow-up needs its starter's conversation; this
            todo.insert(0, it)                             # re-ask keeps the starter's own capture untouched
        key = lambda x: f"{x['id']}~host" if host and x is it else x["id"]  # noqa: E731
        cid, seen = None, []
        for x in todo:
            rq = asker.ask(a.space_id, x["question"], cid=cid if x["kind"] == "follow_up" else None,
                           label=f"{a.slug}:{x['id']}")
            if x["kind"] != "follow_up":
                cid = rq.get("conversation_id")
            if rq.get("conversation_id") and rq["conversation_id"] not in seen:
                seen.append(rq["conversation_id"])
            raw["questions"][key(x)] = rq
            mark = "SQL" if rq.get("sql") else (rq.get("status") or "ERROR")
            print(f"{TAG}   ask {a.slug}:{x['id']:5} {mark:9} {rq.get('row_count')!s:>5} rows {rq['seconds']:5.0f}s  "
                  f"{x['question'][:70]}")
            if rq.get("status") != "COMPLETED":
                problems += 1
        for c in seen:                                     # every conversation this batch opened
            ok = asker.delete(a.space_id, c)
            for x in todo:
                if raw["questions"].get(key(x), {}).get("conversation_id") == c:
                    raw["questions"][key(x)]["deleted"] = ok
        save_raw(a.slug, raw)
    a.cache["questions_captured_at"] = now_iso()
    return problems


def ensure_expected(ws: Any, a: Agent, raw: Dict[str, Any]) -> None:
    """Expected rows for every benchmark a planned question is graded against (when answers were not re-run)."""
    need = {k for it in question_plan(a) for k in [it.get("benchmark")] + it.get("also", []) if k}
    for k in sorted(need - set(raw["expected"])):
        try:
            cols, _, rows = guarded(ws.sql, a.by_key[k]["expected_sql"])
            raw["expected"][k] = {"cols": cols, "rows": rows}
        except Exception as e:  # noqa: BLE001
            print(f"{TAG}   WARN expected SQL of {a.slug}:{k} failed: {first_line(e)}")


def cleanup(ws: Any, ledger: Ledger) -> int:
    left = 0
    for sid, cid in ledger.pending():
        try:
            guarded(ws.api, "DELETE", f"/api/2.0/genie/spaces/{sid}/conversations/{cid}")
            ledger.log("deleted", sid, cid, by="cleanup")
            print(f"{TAG}   deleted leftover conversation {cid} ({sid})")
        except Exception as e:  # noqa: BLE001
            left += 1
            print(f"{TAG}   WARN {cid}: {first_line(e)}")
    return left


# ---- rendering -----------------------------------------------------------------------------------------
def gh_anchor(text: str) -> str:
    return re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")


def _md(text: Any) -> str:
    return g.one_line(text).replace("|", "\\|")


def _date(iso: Optional[str]) -> str:
    if not iso:
        return "not captured"
    d = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    return f"{d.day} {d.strftime('%b %Y')}"


def _period(c: Mapping[str, Any], tables: Mapping[str, str],
            metric_specs: Optional[Mapping[str, Mapping[str, Any]]] = None) -> str:
    if c.get("type") != "METRIC_VIEW":                 # gold drill-through tables: curated (no calendar field)
        return g.one_line(tables.get(c["asset"].split(".", 1)[-1])) or "–"
    if c.get("error") or not c.get("field"):
        return "not captured"
    f, lo, hi, n = c["field"], c.get("first"), c.get("last"), c.get("periods")
    spec = (metric_specs or {}).get(c["asset"].split(".")[-1]) or {}
    what = ((spec.get("blocks") or {}).get("calendar") or {}).get("what")
    by = f", dated by {g.one_line(what)}" if what else ""
    if f == "Fiscal Year":
        return f"{lo} – {hi}; {n} fiscal years{by}"
    if f == "Month":
        return f"{fmt_value(lo, 'month')} – {fmt_value(hi, 'month')}; {n} distinct months{by}"
    return f"{fmt_value(lo, 'date')} – {fmt_value(hi, 'date')}; {n} distinct dates{by}"


def _grain(ident: str, metric_specs: Mapping[str, Mapping[str, Any]]) -> str:
    name = ident.split(".")[-1]
    spec = metric_specs.get(name)
    if spec and spec.get("grain"):
        text = re.sub(r"\s*\((?:gold|silver)\.[^)]*\)", "", str(spec["grain"]))
    else:
        dg = g.load_schema([ident]).get(ident) or {}
        cm = g.one_line(dg.get("comment"))
        m = re.search(r"Grain:\s*([^.]+)", cm)
        text = m.group(1) if m else re.split(r"[:;(]|\. ", cm)[0]
    text = re.sub(r"^one row per\s+", "", text.strip().rstrip("."), flags=re.I)
    if len(text) > 1 and text[0].isupper() and text[1].islower():
        text = text[0].lower() + text[1:]
    return text.replace(" x ", " × ")


def answer_units(a: Agent, key: str, measure_units: Mapping[str, Mapping[str, str]]) -> List[str]:
    ans = (a.cache.get("answers") or {}).get(key) or {}
    return infer_units(a.by_key[key]["expected_sql"], ans.get("columns") or [], ans.get("types") or [],
                       ans.get("head") or [], measure_units, (a.cur.get("formats") or {}).get(key))


def answer_text(a: Agent, key: str, measure_units: Mapping[str, Mapping[str, str]]) -> str:
    ans = (a.cache.get("answers") or {}).get(key)
    if not ans:
        return "not captured yet (run --live answers)"
    if ans.get("sql_sha") != sha(a.by_key[key]["expected_sql"]):
        return "stale (the expected SQL changed since the capture; run --live answers)"
    return summarize_answer(ans, answer_units(a, key, measure_units), (a.cur.get("summary_columns") or {}).get(key))


def answer_views(a: Agent, key: str) -> List[str]:
    """Metric views (or SQL functions) behind a benchmark's expected answer: the answers file's `-- views:` line
    when the benchmark comes from metrics/_answers, else the objects its expected SQL reads."""
    b = a.by_key[key]
    ans = a.answers.get(b.get("answer_ref") or "")
    if ans is not None and getattr(ans, "views", None):
        return list(ans.views)
    objs, fns = g.sql_references(b["expected_sql"], "smbc_genie")
    return sorted({o.split(".")[-1] for o in objs if ".metrics." in o} | {f.split(".")[-1] for f in fns}) or \
        sorted(o.split(".")[-1] for o in objs)


def bench_mark(a: Agent, key: str) -> str:
    e = a.evals.get(key)
    return "–" if e is None else ("✅" if e.get("pass") else "⚠️")


def captured(a: Agent, kind: str, qid: str, question: str) -> Optional[Dict[str, Any]]:
    """The cached result of a question, if it was captured for this exact wording."""
    for q in a.cache.get("questions") or []:
        if q["kind"] == kind and q["id"] == qid and q["question"] == question:
            return q
    return None


def reader_note(verdict: Optional[str], note: str) -> str:
    """The grader's note as a sentence for the document (a plain match needs no note)."""
    if verdict == "match":
        note = re.sub(r"^(matches \S+: )?same rows( as the benchmark)?;? ?", "", note).strip()
        note = note or "same rows and figures"
    note = note.strip()
    return (note[:1].upper() + note[1:] + ("" if note.endswith(".") else ".")) if note else ""


def starter_rows(a: Agent) -> List[Dict[str, Any]]:
    out = []
    for i, q in enumerate(a.samples):
        st = (a.cur.get("starters") or [{}] * len(a.samples))[i] or {}
        c = captured(a, "starter", f"S{i + 1}", q)
        v = c["verdict"] if c else None
        curated = st.get("note") if c and v != "match" and (not st.get("note_sql") or
                                                             st["note_sql"] == sha(c.get("genie_sql") or "")) else None
        note = curated or (reader_note(v, c["note"]) if c else "Not tested yet.")
        out.append({"id": f"S{i + 1}", "question": q, "benchmark": st.get("benchmark"), "verdict": v,
                    "note": g.one_line(note), "detail": c["note"] if c else "", "matched": (c or {}).get("matched"),
                    "captured": c})
    return out


def _counts(rows: Sequence[Mapping[str, Any]]) -> str:
    c = Counter(r["verdict"] for r in rows)
    parts = [f"{c[v]} {MARK[v]}" for v in ("match", "differs", "error") if c.get(v)]
    if c.get(None):
        parts.append(f"{c[None]} not tested")
    return " / ".join(parts) or "–"


def render_markdown(agents: Sequence[Agent], cur: Mapping[str, Any],
                    measure_units: Mapping[str, Mapping[str, str]],
                    metric_specs: Mapping[str, Mapping[str, Any]]) -> str:
    host = cur.get("workspace_url") or ""
    guide = cur.get("guide") or {}
    caps = [x for a in agents for x in (a.cache.get("answers_captured_at"), a.cache.get("questions_captured_at")) if x]
    n_bench = sum(len(a.benchmarks) for a in agents)
    n_pass = sum(1 for a in agents for e in a.evals.values() if e.get("pass"))
    starters = [r for a in agents for r in starter_rows(a)]
    L = ["# Genie question bank — SMBC APAC Genie Agents", "",
         f"Generated by `src/50_genie/build_question_bank.py` from the agents' authoring files "
         f"(`genie/<slug>/space.yaml`, benchmarks, evaluation reports), the curated text in "
         f"`genie/_question_bank.yaml` and a live capture on the demo workspace "
         f"({_date(min(caps)) if caps else 'not captured'}"
         f"{'' if not caps or _date(min(caps)) == _date(max(caps)) else ' – ' + _date(max(caps))}). "
         "All data is **synthetic** (no real clients, people or figures) and as of **30 Sep 2026**, the close of "
         "H1 FY2026. Amounts are USD unless a question asks for local currency.", "",
         f"In total: **{len(agents)} agents**, **{len(starters)} starter questions** "
         f"({_counts(starters)}) and **{n_bench} tested benchmark questions** ({n_pass}/{n_bench} passed in the last "
         "evaluation). The same list is in `docs/genie_questions.csv`.", ""]
    L += ["## How to use this guide", ""]
    for sec, items in (("Asking good questions", guide.get("phrasing")),
                       ("Genie One and the agents", guide.get("genie_one")),
                       ("Reading the results", guide.get("markers"))):
        if items:
            L += [f"### {sec}", ""] + [f"- {g.one_line(x)}" for x in items] + [""]
    L += ["### The 11 agents", "",
          "| # | Agent | Tier | Who it is for | Questions | Benchmarks passed | Starter questions |",
          "|---|---|---|---|---|---|---|"]
    for a in agents:
        s = a.eval_summary
        rows = starter_rows(a)
        sec = f"{a.number}. {a.short_title}"
        L.append(f"| {a.number} | [{_md(a.short_title)}](#{gh_anchor(sec)}) | {_md(a.cur.get('tier'))} | "
                 f"{_md(a.cur.get('audience'))} | {len(a.samples) + len(a.benchmarks)} | "
                 f"{s.get('passed', '–')}/{s.get('n', len(a.benchmarks))} | {_counts(rows)} |")
    L += ["", "Questions = starter questions + benchmark questions (including their rephrased variants).", ""]
    for a in agents:
        L += render_agent(a, host, measure_units, metric_specs, {x.slug: x for x in agents}, cur.get("tables") or {})
    return "\n".join(L).rstrip() + "\n"


def render_agent(a: Agent, host: str, measure_units: Mapping[str, Mapping[str, str]],
                 metric_specs: Mapping[str, Mapping[str, Any]], all_agents: Mapping[str, Agent],
                 tables: Mapping[str, str]) -> List[str]:
    c, s = a.cur, a.eval_summary
    rows = starter_rows(a)
    L = [f"## {a.number}. {a.short_title}", "",
         (f"[Open the agent]({a.url(host)}) · " if a.url(host) else "") + f"{_md(c.get('tier'))} · benchmarks {s.get('passed', '–')}/"
         f"{s.get('n', len(a.benchmarks))} passed ({_date(s.get('at'))}) · starter questions {_counts(rows)} "
         f"(asked {_date(a.cache.get('questions_captured_at'))})", "",
         f"**Who it is for.** {g.one_line(c.get('audience'))}", "",
         f"**What it is for.** {g.one_line(c.get('job'))}", "",
         f"**What it knows.** {g.one_line(c.get('knows'))}", "",
         "| Data asset | One row is | Period covered |", "|---|---|---|"]
    cov = {x["asset"]: x for x in a.cache.get("coverage") or []}
    for ident in a.data_sources:
        kind = "metric view" if ".metrics." in ident else ("view" if ident.endswith("vw_client_360") else "table")
        cv = cov.get(ident) or {"asset": ident, "type": "METRIC_VIEW" if kind == "metric view" else kind}
        L.append(f"| `{ident.split('.', 1)[1]}` ({kind}) | {_md(_grain(ident, metric_specs))} | "
                 f"{_md(_period(cv, tables, metric_specs))} |")
    L += ["", "### Starter questions", "",
          "The questions shown when you open the agent, asked live and compared with the benchmark each one "
          "paraphrases.", "", "| # | Starter question | Result | Graded against | Note |", "|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['id'][1:]} | {_md(r['question'])} | {MARK[r['verdict']]} | {r['matched'] or r['benchmark'] or '–'} "
                 f"| {_md(r['note'])} |")
    L += ["", "### Tested questions by theme", "",
          "Each question is a benchmark: graded in the agent's last evaluation (✅ passed, ⚠️ missed). "
          "*Answer* is the result of the benchmark's expected SQL, re-run for this guide.", ""]
    flaky = c.get("flaky") or {}
    groups = [(t["name"], t["keys"]) for t in c.get("themes") or []]
    if c.get("more"):
        groups.append(("More tested questions (storyline checks)", c["more"]))
    for name, keys in groups:
        L += [f"#### {g.one_line(name)}", ""]
        for k in keys:
            b = a.by_key[k]
            L.append(f"- {bench_mark(a, k)} **{k}** — {_md(b['question'])}")
            src = ", ".join(f"`{v}`" for v in answer_views(a, k)) or "gold"
            L.append(f"  - Answer ({src}): {_md(answer_text(a, k, measure_units))}")
            vs = a.variants(k)
            if vs:
                bits = []
                for v in vs:
                    extra = "" if v["expected_sql"] == b["expected_sql"] else \
                        f" (answer: {_md(answer_text(a, v['key'], measure_units))})"
                    bits.append(f"\"{_md(v['question'])}\" {bench_mark(a, v['key'])}{extra}")
                L.append(f"  - Also works when asked as: {'; '.join(bits)}")
            for kk in [k] + [v["key"] for v in vs]:
                if kk in flaky:
                    L.append(f"  - ⚠️ {kk} sometimes misses — see *Phrase with care* below.")
        L.append("")
    if flaky:
        L += ["### Phrase with care", ""]
        for k, f in flaky.items():
            cq = captured(a, "safer", k, g.one_line(f["safer"]))
            tested = (f"{MARK[cq['verdict']]} " + (f"matches {k}" if cq["verdict"] == "match" else cq["note"])
                      + f" (asked live on {_date(cq['asked_at'])})") if cq else "not tested yet"
            L += [f"- ⚠️ **{k}** \"{_md(a.by_key[k]['question'])}\" — {_md(f['why'])}",
                  f"  - Safer wording: \"{_md(f['safer'])}\" — {_md(tested)}"]
        L.append("")
    if c.get("follow_ups"):
        L += ["### Good follow-ups", "", "Ask these in the same conversation, after the starter question named.", ""]
        for i, f in enumerate(c["follow_ups"]):
            cq = captured(a, "follow_up", f"F{i + 1}", g.one_line(f["ask"]))
            note = cq["note"] if cq else ""
            if cq and f.get("benchmark"):              # graded like a starter: name the benchmark
                detail = reader_note(cq["verdict"], cq["note"]).rstrip(".")
                detail = "" if detail == "Same rows and figures" else detail[:1].lower() + detail[1:]
                note = f"matches {f['benchmark']}" + (f" ({detail})" if detail else "") if cq["verdict"] == "match" \
                    else f"differs from {f['benchmark']}: {cq['note']}"
            res = f"{MARK[cq['verdict']]} {note}" if cq else "not tested yet"
            why = f" {_md(f['why'])}" if f.get("why") else ""
            L.append(f"- After starter {str(f['after'])[1:]}: \"{_md(f['ask'])}\" — {_md(res)}.{why}")
        L.append("")
    if c.get("routing"):
        L += ["### Ask another agent for", ""]
        for r in c["routing"]:
            o = all_agents.get(r["agent"])
            name = o.short_title if o else SPACES[r["agent"]][1][len(TITLE_PREFIX):]
            num = o.number if o else SPACES[r["agent"]][0]
            L.append(f"- {g.one_line(r['ask'])} → [{name}](#{gh_anchor(f'{num}. {name}')})")
        L.append("")
    return L


CSV_COLUMNS = ["agent", "agent_title", "tier", "kind", "key", "theme", "question", "result", "note", "benchmark",
               "expected_answer", "agent_url"]


def render_csv(agents: Sequence[Agent], cur: Mapping[str, Any], measure_units: Mapping[str, Mapping[str, str]]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, lineterminator="\n")
    w.writeheader()
    word = {"match": "matches", "differs": "answered but differs", "error": "error or no SQL", None: "not tested"}
    host = cur.get("workspace_url") or ""
    for a in agents:
        base = {"agent": a.slug, "agent_title": a.title, "tier": g.one_line(a.cur.get("tier")), "agent_url": a.url(host)}
        for r in starter_rows(a):
            w.writerow({**base, "kind": "starter", "key": r["id"], "theme": "", "question": r["question"],
                        "result": word[r["verdict"]], "note": r["note"], "benchmark": r["matched"] or r["benchmark"],
                        "expected_answer": answer_text(a, r["benchmark"], measure_units) if r["benchmark"] else ""})
        theme_of = {k: t["name"] for t in a.cur.get("themes") or [] for k in t["keys"]}
        theme_of.update({k: "More tested questions (storyline checks)" for k in a.cur.get("more") or []})
        more = set(a.cur.get("more") or [])
        for b in a.benchmarks:
            e = a.evals.get(b["key"]) or {}
            kind = "variant" if b.get("variant_of") else ("storyline check" if b["key"] in more else "must-answer")
            w.writerow({**base, "kind": kind, "key": b["key"], "theme": theme_of.get(b.get("variant_of") or b["key"], ""),
                        "question": b["question"],
                        "result": "passed" if e.get("pass") else ("missed" if e else "not evaluated"),
                        "note": "" if e.get("pass") or not e else "sometimes misses: see the safer wording",
                        "benchmark": b.get("variant_of") or "", "expected_answer": answer_text(a, b["key"], measure_units)})
        for k, f in (a.cur.get("flaky") or {}).items():
            cq = captured(a, "safer", k, g.one_line(f["safer"]))
            w.writerow({**base, "kind": "safer wording", "key": k, "theme": "", "question": g.one_line(f["safer"]),
                        "result": word[cq["verdict"] if cq else None], "note": cq["note"] if cq else g.one_line(f["why"]),
                        "benchmark": k, "expected_answer": answer_text(a, k, measure_units)})
        for i, f in enumerate(a.cur.get("follow_ups") or []):
            cq = captured(a, "follow_up", f"F{i + 1}", g.one_line(f["ask"]))
            w.writerow({**base, "kind": "follow-up", "key": f"F{i + 1}", "theme": f"after {f['after']}",
                        "question": g.one_line(f["ask"]), "result": word[cq["verdict"] if cq else None],
                        "note": cq["note"] if cq else "", "benchmark": "", "expected_answer": ""})
    return buf.getvalue()


def load_metric_specs(metrics_dir: Path = METRICS_DIR) -> Dict[str, Dict[str, Any]]:
    return {p.stem: yaml.safe_load(p.read_text()) or {} for p in sorted(Path(metrics_dir).glob("mv_*.yaml"))}


def write_if_changed(path: Path, text: str) -> bool:
    if path.exists() and path.read_text() == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return True


# ---- main ----------------------------------------------------------------------------------------------
def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SMBC APAC Genie - phase 9: Genie question bank")
    p.add_argument("--profile", default=None, help="CLI profile for --live / --cleanup")
    p.add_argument("--warehouse-id", default=None)
    p.add_argument("--only", default="", help="comma-separated slugs to capture (the document always covers all)")
    p.add_argument("--live", nargs="?", const="all", choices=["all", "answers", "questions"], default=None,
                   help="(re)capture the cache: expected answers, Genie questions, or both")
    p.add_argument("--ids", default="", help="with --live questions: only these ids (S1..S8, F1.., flaky benchmark keys); "
                                            "<slug>:<id> limits an id to one agent")
    p.add_argument("--regrade", action="store_true", help="re-grade the cached questions from scratch/question_bank_raw")
    p.add_argument("--cleanup", action="store_true", help="delete ledger conversations not deleted yet")
    p.add_argument("--no-render", action="store_true", help="capture only; do not write the document / CSV")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass
    a_ = parse_args(argv)
    try:
        cur = load_curation()
        agents = load_agents(cur)
    except (CurationError, g.GenieSpecError, yaml.YAMLError) as e:
        print(f"{TAG} ERROR {e}")
        return 2
    only = [s for s in a_.only.split(",") if s]
    bad = [s for s in only if s not in {x.slug for x in agents}]
    if bad:
        print(f"{TAG} unknown slug(s): {', '.join(bad)}")
        return 2
    ids: Dict[str, List[str]] = {}
    for item in [i for i in a_.ids.split(",") if i]:
        slug, _, qid = item.rpartition(":")
        ids.setdefault(slug or "*", []).append(qid)
    targets = [x for x in agents if (not only or x.slug in only) and (not ids or "*" in ids or x.slug in ids)]
    rc = 0
    if a_.live or a_.cleanup:
        from genie_ws import Workspace

        ws = Workspace(require_warehouse(a_.warehouse_id or g.load_shared()["warehouse_id"] or default_warehouse_id(), TAG),
                       a_.profile)
        ledger = Ledger()
        if a_.live:
            asker = Asker(ws, ledger)
            for x in targets:
                print(f"{TAG} ==== {x.slug} ({x.space_id})")
                raw = load_raw(x.slug)
                if a_.live in ("all", "answers"):
                    rc = max(rc, 1 if capture_answers(ws, x, raw) else 0)
                    save_raw(x.slug, raw)
                if a_.live in ("all", "questions"):
                    ensure_expected(ws, x, raw)
                    rc = max(rc, 1 if capture_questions(ws, asker, x, raw, ids.get(x.slug, []) + ids.get("*", []))
                             else 0)
                regrade(x, raw)
                save_cache(x)
        if cleanup(ws, ledger):
            rc = max(rc, 1)
    elif a_.regrade:
        for x in targets:
            regrade(x, load_raw(x.slug))
            save_cache(x)
    if a_.no_render:
        return rc
    errs = check_curation(cur, agents)
    if errs:
        for e in errs:
            print(f"{TAG} CURATION {e}")
        print(f"{TAG} {len(errs)} curation problem(s) - document not written")
        return 2
    mu, specs = load_measure_units(), load_metric_specs()
    for path, text in ((DOC_PATH, render_markdown(agents, cur, mu, specs)), (CSV_PATH, render_csv(agents, cur, mu))):
        print(f"{TAG} {path.relative_to(REPO)}: {'written' if write_if_changed(path, text) else 'unchanged'}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
