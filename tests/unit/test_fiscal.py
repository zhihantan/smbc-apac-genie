"""Japanese fiscal-calendar unit tests (no Spark, no workspace)."""
import datetime as dt

from smbc_genie_lib import fiscal


def test_fy_label_boundaries():
    assert fiscal.fiscal_year_label("2026-04-01") == "FY2026"
    assert fiscal.fiscal_year_label("2027-03-31") == "FY2026"
    assert fiscal.fiscal_year_label("2026-03-31") == "FY2025"
    assert fiscal.fiscal_year_label("2026-09-30") == "FY2026"  # AS_OF_DATE, H1 close


def test_quarter_and_half():
    assert fiscal.fiscal_quarter("2026-04-01") == 1 and fiscal.fiscal_half("2026-04-01") == "H1"
    assert fiscal.fiscal_quarter("2026-09-30") == 2 and fiscal.fiscal_half("2026-09-30") == "H1"
    assert fiscal.fiscal_quarter("2026-10-01") == 3 and fiscal.fiscal_half("2026-10-01") == "H2"
    assert fiscal.fiscal_quarter("2027-01-15") == 4 and fiscal.fiscal_half("2027-01-15") == "H2"


def test_month_no():
    assert fiscal.fiscal_month_no("2026-04-15") == 1
    assert fiscal.fiscal_month_no("2026-03-15") == 12
    assert fiscal.fiscal_month_no("2026-12-15") == 9


def test_labels_compose():
    assert fiscal.fiscal_quarter_label("2026-07-10") == "FY2026-Q2"
    assert fiscal.fiscal_half_label("2027-02-01") == "FY2026-H2"


def test_accepts_date_and_datetime():
    assert fiscal.fiscal_year(dt.date(2026, 5, 1)) == 2026
    assert fiscal.fiscal_year(dt.datetime(2026, 2, 1, 9, 30)) == 2025


def test_same_period_prior_fy_handles_leap_day():
    assert fiscal.same_period_prior_fy("2028-02-29") == dt.date(2027, 2, 28)
    assert fiscal.same_period_prior_fy("2026-09-30") == dt.date(2025, 9, 30)
