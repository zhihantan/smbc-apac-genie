"""Helpers that let the same build scripts run on a laptop, in a job and in a notebook."""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

from smbc_genie_lib import config

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def private_environ(monkeypatch):
    """A throw-away copy of os.environ: export_env writes os.environ directly, and a leaked SMBC_SCALE would make
    every later test generate full-scale data."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("SMBC_")}
    monkeypatch.setattr(os, "environ", env)
    return env


def test_export_env_sets_known_fields_and_skips_blanks(private_environ):
    done = config.export_env(scale="1.0", as_of_date="", random_seed=None)
    assert done == {"scale": "1.0"} and private_environ["SMBC_SCALE"] == "1.0"
    cfg = config.load_config()
    assert cfg.scale == 1.0 and cfg.as_of_date == "2026-09-30"
    with pytest.raises(KeyError):
        config.export_env(scael="1.0")


def test_export_env_does_not_leak():
    assert "SMBC_SCALE" not in os.environ


def test_require_warehouse(private_environ):
    assert config.require_warehouse("abc") == "abc"
    with pytest.raises(SystemExit, match="no SQL warehouse"):
        config.require_warehouse("", "[t]")
    private_environ["SMBC_WAREHOUSE_ID"] = "w123"
    assert config.default_warehouse_id("fallback") == "w123"


def _run_bronze():
    spec = importlib.util.spec_from_file_location("run_bronze", REPO / "src" / "10_bronze_synth" / "run_bronze.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("body, code", [
    ("print('ok')", 0),
    ("import sys; sys.exit(0)", 0),
    ("import sys; sys.exit(3)", 3),
    ("raise RuntimeError('boom')", 1),
    ("import sys; assert sys.argv[1:] == ['--catalog', 'c'], sys.argv", 0),
])
def test_run_step_in_process(tmp_path, body, code):
    script = tmp_path / "step.py"
    script.write_text(f"if __name__ == '__main__':\n    {body}\n")
    argv = list(sys.argv)
    assert _run_bronze().run_step(script, ["--catalog", "c"], in_process=True) == code
    assert sys.argv == argv            # restored after the step


def test_run_step_child_process(tmp_path):
    script = tmp_path / "step.py"
    script.write_text("import sys\nsys.exit(4 if sys.argv[1:] == ['--x'] else 9)\n")
    assert _run_bronze().run_step(script, ["--x"], in_process=False) == 4
