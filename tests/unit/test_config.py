"""Config loader unit tests (reads the real config/smbc_genie.yaml)."""
import os

from smbc_genie_lib.config import BuildConfig, load_config


def test_defaults_without_file(tmp_path):
    cfg = load_config(path=tmp_path / "missing.yaml")
    assert cfg.catalog == "smbc_genie"
    assert cfg.scale == 0.1
    assert cfg.table("gold", "dim_client") == "smbc_genie.gold.dim_client"


def test_loads_repo_config():
    cfg = load_config()  # the committed config/smbc_genie.yaml
    assert cfg.catalog == "smbc_genie"
    assert cfg.as_of_date == "2026-09-30"
    assert cfg.random_seed == 20260930
    assert len(cfg.booking_locations) == 13


def test_overrides_win(tmp_path):
    cfg = load_config(path=tmp_path / "missing.yaml", scale=1.0, catalog="smbc_demo")
    assert cfg.scale == 1.0
    assert cfg.catalog == "smbc_demo"


def test_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("SMBC_SCALE", "1.0")
    monkeypatch.setenv("SMBC_SIMULATE_DELTA_SHARING", "false")
    cfg = load_config(path=tmp_path / "missing.yaml")
    assert cfg.scale == 1.0
    assert cfg.simulate_delta_sharing is False
