"""smbc_genie_lib — shared library for the SMBC APAC Genie build.

Kept import-light: pulling in the package does not require Spark or the Databricks SDK.
Import submodules explicitly (e.g. `from smbc_genie_lib import fiscal`).
"""
__version__ = "0.1.0"

__all__ = ["config", "fiscal", "rng", "naming", "er"]
