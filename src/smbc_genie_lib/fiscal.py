"""Japanese fiscal calendar (fiscal year runs 1 April - 31 March).

FY2026 = Apr 2026 - Mar 2027. Q1 Apr-Jun, Q2 Jul-Sep, Q3 Oct-Dec, Q4 Jan-Mar.
H1 Apr-Sep, H2 Oct-Mar. See docs/DECISIONS.md D17.

Pure stdlib so it runs in unit tests and (mirrored as SQL) in `fn_fiscal_*`.
"""
from __future__ import annotations

import datetime as _dt
from typing import Union

DateLike = Union[_dt.date, _dt.datetime, str]

FY_START_MONTH = 4  # April


def _as_date(d: DateLike) -> _dt.date:
    if isinstance(d, _dt.datetime):
        return d.date()
    if isinstance(d, _dt.date):
        return d
    if isinstance(d, str):
        return _dt.date.fromisoformat(d[:10])
    raise TypeError(f"unsupported date value: {d!r}")


def fiscal_year(d: DateLike) -> int:
    """Calendar year in which this fiscal year started (FY2026 -> 2026)."""
    dd = _as_date(d)
    return dd.year if dd.month >= FY_START_MONTH else dd.year - 1


def fiscal_year_label(d: DateLike) -> str:
    return f"FY{fiscal_year(d)}"


def fiscal_month_no(d: DateLike) -> int:
    """1..12 with April = 1, March = 12."""
    dd = _as_date(d)
    return (dd.month - FY_START_MONTH) % 12 + 1


def fiscal_quarter(d: DateLike) -> int:
    """1..4 with Apr-Jun = 1 ... Jan-Mar = 4."""
    return (fiscal_month_no(d) - 1) // 3 + 1


def fiscal_quarter_label(d: DateLike) -> str:
    return f"{fiscal_year_label(d)}-Q{fiscal_quarter(d)}"


def fiscal_half(d: DateLike) -> str:
    """'H1' (Apr-Sep) or 'H2' (Oct-Mar)."""
    return "H1" if fiscal_quarter(d) <= 2 else "H2"


def fiscal_half_label(d: DateLike) -> str:
    return f"{fiscal_year_label(d)}-{fiscal_half(d)}"


def same_period_prior_fy(d: DateLike) -> _dt.date:
    """The equivalent calendar day one fiscal year earlier (= one calendar year earlier)."""
    dd = _as_date(d)
    try:
        return dd.replace(year=dd.year - 1)
    except ValueError:  # 29 Feb -> 28 Feb
        return dd.replace(year=dd.year - 1, day=28)
