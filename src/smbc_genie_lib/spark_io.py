"""Spark session + table-write helpers that work both locally (Databricks Connect, serverless)
and inside a serverless job (ambient session). Generators call `get_spark()` and `write_table()`
so the identical code path runs in dev and prod (DECISIONS D03, D04).
"""
from __future__ import annotations

import os
from typing import Any, Iterable, Optional


def sql_escape(text: str) -> str:
    """Body of a SQL string literal: backslash-escape \\ and ' (Databricks reads 'it''s' as two
    adjacent literals, i.e. "its")."""
    return str(text).replace("\\", "\\\\").replace("'", "\\'")


def on_databricks() -> bool:
    """True inside Databricks compute (notebook or job task), where a SparkSession is ambient."""
    return bool(os.environ.get("DATABRICKS_RUNTIME_VERSION"))


def get_spark(profile: Optional[str] = None):
    """Return the ambient SparkSession in a job or notebook, else a serverless Databricks Connect session."""
    try:
        from pyspark.sql import SparkSession

        active = SparkSession.getActiveSession()
        if active is not None:
            return active
        if on_databricks():
            return SparkSession.builder.getOrCreate()
    except Exception:  # noqa: BLE001
        pass
    from databricks.connect import DatabricksSession

    builder = DatabricksSession.builder
    if profile:
        builder = builder.profile(profile)
    return builder.serverless().getOrCreate()


def write_table(
    df,
    fqn: str,
    *,
    mode: str = "overwrite",
    comment: Optional[str] = None,
    cluster_by: Optional[Iterable[str]] = None,
    partition_hint: Optional[int] = None,
) -> int:
    """Write a DataFrame as a managed Delta table and return its row count.

    overwriteSchema is set so re-runs are idempotent even when columns change during development.
    Clustering/comments are applied with follow-up SQL (Connect writer support varies).
    """
    spark = df.sparkSession
    if partition_hint:
        df = df.repartition(partition_hint)
    (df.write.mode(mode).format("delta").option("overwriteSchema", "true").saveAsTable(fqn))
    if comment:
        spark.sql(f"COMMENT ON TABLE {fqn} IS '{sql_escape(comment)}'")
    if cluster_by:
        cols = ", ".join(cluster_by)
        try:
            spark.sql(f"ALTER TABLE {fqn} CLUSTER BY ({cols})")
        except Exception:  # noqa: BLE001 - clustering is best-effort on bronze/truth
            pass
    return spark.table(fqn).count()


def create_rows_df(spark, rows: list, schema: Any = None):
    """Build a DataFrame from a list of dicts/Rows (small dim/truth tables)."""
    if not rows:
        raise ValueError("no rows to create a DataFrame from")
    return spark.createDataFrame(rows, schema=schema) if schema else spark.createDataFrame(rows)
