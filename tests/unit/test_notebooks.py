"""The build notebooks (notebooks/*.py, Databricks source format) call existing scripts with flags they accept."""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
NOTEBOOKS = sorted((REPO / "notebooks").glob("*.py"))
STEPS = [p for p in NOTEBOOKS if re.match(r"0[1-7]_", p.name)]


def test_notebook_set():
    assert [p.stem for p in NOTEBOOKS] == ["00_run_all", "01_setup_catalog", "02_bronze_synthetic_sources",
                                           "03_silver_data_quality", "04_gold_customer_360", "05_metric_views",
                                           "06_genie_agents", "07_validate_data_assets", "_common"]
    for p in NOTEBOOKS:
        assert p.read_text().startswith("# Databricks notebook source\n"), p.name


@pytest.mark.parametrize("nb", STEPS, ids=lambda p: p.stem)
def test_step_runs_a_script_with_known_flags(nb):
    text = nb.read_text()
    assert "# MAGIC %run ./_common" in text
    scripts = re.findall(r'run_script\("([^"]+)"', text)
    assert len(scripts) == 1, scripts
    script = (REPO / scripts[0]).read_text()
    for flag in set(re.findall(r'"(--[a-z][a-z-]*)"', text)):
        assert f'"{flag}"' in script, f"{nb.stem}: {scripts[0]} has no {flag}"


def test_run_all_runs_every_step_in_order():
    text = (REPO / "notebooks" / "00_run_all.py").read_text()
    assert re.findall(r'\("(0[1-7]_[a-z0-9_]+)", \d+\)', text) == [p.stem for p in STEPS]


def test_common_defines_what_steps_use():
    text = (REPO / "notebooks" / "_common.py").read_text()
    for name in ("def run_script", "def need_warehouse", "def widget", "CATALOG =", "SCALE, AS_OF, SEED", "export_env("):
        assert name in text, name
