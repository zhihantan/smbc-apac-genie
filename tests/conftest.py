"""pytest configuration.

Puts `src/` on sys.path so `import smbc_genie_lib` works without an editable install,
and defines the `unit`/`integration` markers. Integration tests (which need a live
warehouse) are skipped unless SMBC_RUN_INTEGRATION=1.
"""
import os
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: pure-python tests, no workspace")
    config.addinivalue_line("markers", "integration: needs a live Databricks warehouse")


def pytest_collection_modifyitems(config, items):
    if os.environ.get("SMBC_RUN_INTEGRATION") == "1":
        return
    skip = pytest.mark.skip(reason="integration test; set SMBC_RUN_INTEGRATION=1 to run")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
