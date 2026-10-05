"""Load and resolve the build configuration (config/smbc_genie.yaml + overrides).

Precedence (low -> high): YAML file -> environment variables (SMBC_*) -> explicit overrides
(e.g. job/task parameters). Kept dependency-light (pyyaml only).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - yaml always present in the serverless env
    yaml = None

_ENV_PREFIX = "SMBC_"


@dataclass
class BuildConfig:
    catalog: str = "smbc_genie"
    warehouse_id: str = ""
    warehouse_name: str = "smbc-genie-wh"
    as_of_date: str = "2026-09-30"
    history_start: str = "2023-04-01"
    scale: float = 0.1
    random_seed: int = 20260930
    spaces_to_build: str = "all"
    use_ai_functions: str = "auto"
    simulate_delta_sharing: bool = True
    owner_group: str = ""
    consumer_group: str = ""
    storage_root: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def booking_locations(self) -> list:
        return list(self.raw.get("booking_locations", []))

    @property
    def volumes(self) -> dict:
        return dict(self.raw.get("volumes", {}))

    @property
    def segments(self) -> dict:
        return dict(self.raw.get("segments", {}))

    @property
    def thresholds(self) -> dict:
        return dict(self.raw.get("thresholds", {}))

    @property
    def realism(self) -> dict:
        return dict(self.raw.get("realism", {}))

    @property
    def entity_resolution(self) -> dict:
        return dict(self.raw.get("entity_resolution", {}))

    @property
    def storylines(self) -> dict:
        return dict(self.raw.get("storylines", {}))

    def schema(self, name: str) -> str:
        return f"{self.catalog}.{name}"

    def table(self, schema: str, name: str) -> str:
        return f"{self.catalog}.{schema}.{name}"


def _coerce(value: str) -> Any:
    low = value.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def default_config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "smbc_genie.yaml"


def export_env(**values: Any) -> Dict[str, str]:
    """Set SMBC_<FIELD> for every non-empty value (e.g. scale=1.0 -> SMBC_SCALE=1.0), so every later
    load_config() in this process and in its child processes sees it. Returns what was set."""
    known = {f for f in BuildConfig.__dataclass_fields__ if f != "raw"}
    done = {}
    for key, val in values.items():
        if key not in known:
            raise KeyError(f"unknown config field: {key}")
        if val is not None and str(val) != "":
            os.environ[_ENV_PREFIX + key.upper()] = done[key] = str(val)
    return done


def default_warehouse_id(fallback: str = "") -> str:
    """The SQL warehouse to use when no --warehouse-id is given: SMBC_WAREHOUSE_ID, else warehouse_id in
    config/smbc_genie.yaml, else the fallback."""
    return load_config().warehouse_id or fallback


def require_warehouse(warehouse_id: Optional[str], tag: str = "") -> str:
    if not warehouse_id:
        raise SystemExit(f"{tag} no SQL warehouse: pass --warehouse-id, or set SMBC_WAREHOUSE_ID "
                         "(or warehouse_id in config/smbc_genie.yaml)".strip())
    return warehouse_id


def load_config(path: Optional[os.PathLike] = None, **overrides: Any) -> BuildConfig:
    data: Dict[str, Any] = {}
    cfg_path = Path(path) if path else default_config_path()
    if cfg_path.exists() and yaml is not None:
        loaded = yaml.safe_load(cfg_path.read_text()) or {}
        data.update(loaded)

    known = {f for f in BuildConfig.__dataclass_fields__ if f != "raw"}
    for key in list(data.keys()):
        if key.upper() in {k.upper() for k in known}:
            data[key.lower()] = data[key]

    for env_key, env_val in os.environ.items():
        if env_key.startswith(_ENV_PREFIX):
            field_name = env_key[len(_ENV_PREFIX):].lower()
            if field_name in known:
                data[field_name] = _coerce(env_val)

    for key, val in overrides.items():
        if val is not None:
            data[key] = val

    init_kwargs = {k: v for k, v in data.items() if k in known}
    cfg = BuildConfig(**init_kwargs)
    cfg.raw = data
    return cfg
