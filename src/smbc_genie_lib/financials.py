"""Spread financial statements + ratios (brief §5.3; DECISIONS D17).

Coherent P&L / balance sheet / cash-flow for credit-analysed borrowers across three fiscal
years, driven by latent health and group size. Each client uses its own fiscal year-end (March
for Japanese corporates, December otherwise). Statements are internally consistent (the balance
sheet balances; leverage aligns with the covenant ratios), and ratios are computed from the
statements, not drawn. Pure Python/deterministic; peer percentiles are computed in Spark from the
ratio table. Stored long (one row per statement line) under bronze.credit_financial_statement.
"""
from __future__ import annotations

import datetime as _dt
from typing import Dict, List, Tuple

from . import rng, storyline_injectors
from .reference import FX_BASE

FISCAL_YEARS = [2023, 2024, 2025]  # latest spread year is FY2025 as of 2026-09-30


def fye(entity: Dict, fy: int) -> _dt.date:
    """Fiscal year-end date for a client's FY (JC = 31 Mar of fy+1, else 31 Dec of fy)."""
    return _dt.date(fy + 1, 3, 31) if entity["segment"] == "Japanese Corporate" else _dt.date(fy, 12, 31)


def _ebitda_margin(entity: Dict, h: float) -> float:
    sector = entity["industry_sector"]
    base = {"Trading Houses": 0.04, "Financial Institution": 0.30, "Technology": 0.18,
            "Energy": 0.22, "Materials": 0.15, "Utilities": 0.28}.get(sector, 0.13)
    return max(0.03, base + 0.10 * (h - 0.5))


def _statement(cfg, entity: Dict, fy: int, h: float, prev_rev_usd: float) -> Tuple[Dict, float]:
    """Return (financials-in-USD dict, revenue_usd) for one client-year."""
    seed = cfg.random_seed
    eid = entity["entity_id"]
    wealth = entity.get("group_wealth", 1.0)
    size = rng.lognormal(seed, 17.6, 0.6, "finsize", eid) * wealth
    growth = (1.05 + 0.08 * (h - 0.5)) ** (fy - 2023)
    revenue = size * growth * (0.85 + 0.3 * h)
    story = storyline_injectors.statement_override(cfg, entity, fy)   # Sunda / Tanaka FY scripts
    ebitda_m = story.get("ebitda_margin") or _ebitda_margin(entity, h)
    ebitda = revenue * ebitda_m
    gross_margin = min(0.65, ebitda_m + 0.12 + 0.06 * rng.unit(seed, "gm", eid, fy))
    cogs = revenue * (1 - gross_margin)
    gross_profit = revenue - cogs
    opex = gross_profit - ebitda
    da = revenue * 0.04
    ebit = ebitda - da
    leverage = story.get("leverage") or 1.5 + (1 - h) * 2.5  # Net Debt / EBITDA, 1.5x (strong) .. 4.0x (weak)
    net_debt = leverage * ebitda
    cash = revenue * (0.05 + 0.10 * h)
    total_debt = net_debt + cash
    st_debt, lt_debt = total_debt * 0.30, total_debt * 0.70
    interest = total_debt * 0.045
    pretax = ebit - interest
    tax = max(0.0, pretax) * 0.22
    net_income = pretax - tax
    dso = 45 + (1 - h) * 40
    dio = 40 + (1 - h) * 35
    dpo = 40 + (1 - h) * 30
    receivables = revenue * dso / 365.0
    inventory = cogs * dio / 365.0
    payables = cogs * dpo / 365.0
    cur_assets = cash + receivables + inventory
    # enforce the accounting identity: Total Assets = Total Debt + Payables + Equity.
    # Fixed-asset base ~1.3x revenue keeps equity substantial so ROE is realistic (~8-15%).
    equity = max(revenue * 0.15, (cur_assets + revenue * 1.3) - total_debt - payables)
    total_assets = total_debt + payables + equity
    fixed_assets = total_assets - cur_assets
    cfo = net_income + da
    capex = revenue * 0.05
    dividends = max(0.0, net_income * 0.20)
    fcf = cfo - capex
    f = dict(revenue=revenue, cogs=cogs, gross_profit=gross_profit, opex=opex, ebitda=ebitda,
             da=da, ebit=ebit, interest=interest, pretax=pretax, tax=tax, net_income=net_income,
             cash=cash, receivables=receivables, inventory=inventory, cur_assets=cur_assets,
             fixed_assets=fixed_assets, total_assets=total_assets, payables=payables,
             st_debt=st_debt, lt_debt=lt_debt, total_debt=total_debt, net_debt=net_debt,
             equity=equity, cfo=cfo, capex=capex, fcf=fcf, dividends=dividends,
             dso=dso, dio=dio, dpo=dpo, ebitda_margin=ebitda_m, gross_margin=gross_margin,
             prev_rev=prev_rev_usd)
    return f, revenue


PNL = [("Revenue", "revenue"), ("COGS", "cogs"), ("Gross Profit", "gross_profit"),
       ("Operating Expenses", "opex"), ("EBITDA", "ebitda"), ("Depreciation & Amortisation", "da"),
       ("EBIT", "ebit"), ("Interest Expense", "interest"), ("Pre-Tax Profit", "pretax"),
       ("Tax", "tax"), ("Net Income", "net_income")]
BS = [("Cash", "cash"), ("Receivables", "receivables"), ("Inventory", "inventory"),
      ("Total Current Assets", "cur_assets"), ("Fixed Assets", "fixed_assets"),
      ("Total Assets", "total_assets"), ("Payables", "payables"), ("Short-Term Debt", "st_debt"),
      ("Long-Term Debt", "lt_debt"), ("Total Debt", "total_debt"), ("Equity", "equity")]
CF = [("Cash Flow from Operations", "cfo"), ("Capex", "capex"), ("Free Cash Flow", "fcf"),
      ("Dividends Paid", "dividends")]


def build_statements(cfg, entities: List[Dict], health_lut: Dict, entity_obligor: Dict[str, str]):
    seed = cfg.random_seed
    as_of = _dt.date.fromisoformat(cfg.as_of_date)
    lines: List[Dict] = []
    ratios: List[Dict] = []
    for e in entities:
        obligor = entity_obligor.get(e["entity_id"])
        if not obligor:
            continue
        ccy = _ccy(e)
        fx = FX_BASE.get(ccy, 1.0)
        prev_rev = 0.0
        for fy in FISCAL_YEARS:
            ye = fye(e, fy)
            h = health_lut.get((e["entity_id"], _dt.date(ye.year, ye.month, 1)), 0.6)
            f, rev = _statement(cfg, e, fy, h, prev_rev)
            spread_dt = ye + _dt.timedelta(days=int(55 + 40 * rng.unit(seed, "spread", e["entity_id"], fy)))
            # FY<2025 always spread; ~10% of FY2025 is a spreading backlog (the "not yet spread" question)
            is_spread = fy < 2025 or rng.unit(seed, "unspread", e["entity_id"]) >= 0.10
            audited = fy < 2025 or rng.unit(seed, "aud", e["entity_id"], fy) < 0.6
            for stmt, items in (("P&L", PNL), ("Balance Sheet", BS), ("Cash Flow", CF)):
                for label, k in items:
                    amt_usd = round(f[k], 2)
                    lines.append({
                        "obligor_id": obligor, "fiscal_year": fy,
                        "fiscal_year_label": f"FY{fy}", "fiscal_year_end": ye, "statement_type": stmt,
                        "line_item": label, "amount_usd": amt_usd, "amount_lcy": round(amt_usd * fx, 2),
                        "currency": ccy, "is_audited": audited, "is_spread": is_spread,
                        "spread_date": spread_dt if is_spread else None,
                    })
            ratios.extend(_ratios(e, obligor, fy, ye, f))
            prev_rev = rev
    return lines, ratios


def _ratios(e: Dict, obligor: str, fy: int, ye: _dt.date, f: Dict) -> List[Dict]:
    def r(name, val):
        return {"obligor_id": obligor, "fiscal_year": fy, "fiscal_year_label": f"FY{fy}",
                "fiscal_year_end": ye, "ratio_name": name,
                "ratio_value": round(val, 4) if val is not None else None,
                "peer_group_id": _peer(e)}
    out = [
        r("Net Debt/EBITDA", f["net_debt"] / f["ebitda"] if f["ebitda"] else 0),
        r("ICR", f["ebit"] / f["interest"] if f["interest"] else 0),
        r("DSCR", f["cfo"] / (f["interest"] + f["st_debt"]) if (f["interest"] + f["st_debt"]) else 0),
        r("Current Ratio", f["cur_assets"] / (f["payables"] + f["st_debt"]) if (f["payables"] + f["st_debt"]) else 0),
        r("Quick Ratio", (f["cur_assets"] - f["inventory"]) / (f["payables"] + f["st_debt"]) if (f["payables"] + f["st_debt"]) else 0),
        r("Gross Margin", f["gross_margin"]),
        r("EBITDA Margin", f["ebitda_margin"]),
        r("Net Margin", f["net_income"] / f["revenue"] if f["revenue"] else 0),
        r("ROE", f["net_income"] / f["equity"] if f["equity"] else 0),
        r("ROA", f["net_income"] / f["total_assets"] if f["total_assets"] else 0),
        r("DSO", f["dso"]), r("DIO", f["dio"]), r("DPO", f["dpo"]),
        r("Cash Conversion Cycle", f["dso"] + f["dio"] - f["dpo"]),
        r("Debt/Equity", f["total_debt"] / f["equity"] if f["equity"] else 0),
        r("Free Cash Flow", f["fcf"]),
        r("Revenue YoY Growth", (f["revenue"] / f["prev_rev"] - 1) if f["prev_rev"] else None),
    ]
    return out


def _ccy(e: Dict) -> str:
    from .accounts import CCY_BY_CC
    return CCY_BY_CC.get(e["booking_country"], "USD")


def _peer(e: Dict) -> str:
    # peer group id is derived the same way as reference.build_industry_peers (by subsector order)
    from .truth import INDUSTRY_MAP
    seen = []
    for _, (_, sub, _) in INDUSTRY_MAP.items():
        if sub not in seen:
            seen.append(sub)
    sub = e["industry_subsector"]
    return f"PG-{seen.index(sub) + 1:03d}" if sub in seen else "PG-000"
