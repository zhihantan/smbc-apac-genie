"""Small helpers for building deterministic Spark SQL expressions used by the event-fact
generators (payments, FX, trade, ...). Pure string builders so they are unit-testable.
"""
from __future__ import annotations

from typing import List, Sequence, Tuple


def weighted_case(u_expr: str, pairs: Sequence[Tuple[str, float]]) -> str:
    """A SQL CASE that maps a [0,1) uniform expression to a weighted categorical.

    weighted_case("u", [("A", 2), ("B", 1), ("C", 1)]) picks A 50%, B 25%, C 25%.
    The last value is the ELSE branch so the probabilities always sum to 1.
    """
    if not pairs:
        raise ValueError("pairs must be non-empty")
    total = float(sum(w for _, w in pairs))
    if total <= 0:
        raise ValueError("weights must sum to a positive number")
    whens: List[str] = []
    cum = 0.0
    for val, w in pairs[:-1]:
        cum += w / total
        whens.append(f"WHEN {u_expr} < {cum:.6f} THEN '{val}'")
    whens.append(f"ELSE '{pairs[-1][0]}'")
    return "CASE " + " ".join(whens) + " END"


def pick_from_array(u_expr: str, values: Sequence[str]) -> str:
    """Uniformly pick one of `values` using the [0,1) uniform expression."""
    arr = ", ".join(f"'{v}'" for v in values)
    return f"element_at(array({arr}), CAST({u_expr} * {len(values)} AS INT) + 1)"
