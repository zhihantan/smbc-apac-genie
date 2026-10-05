"""Deterministic, hash-based randomness (docs/DECISIONS.md D08).

Every random draw is a pure function of (global seed, salt, business keys), so output
never depends on partitioning, row order or process state. The same primitive is used:

  * driver side  -> these stdlib functions (and numpy seeded from `seed_sequence`);
  * Spark side    -> the equivalent `xxhash64`/`hash` SQL expression from `spark_unit_expr`.

Never use Python's builtin hash() for data: it is salted per process.
"""
from __future__ import annotations

import hashlib
import math
from typing import Sequence, TypeVar

T = TypeVar("T")

_U64 = 1 << 64


def _digest64(*parts: object) -> int:
    """Stable 64-bit int from the parts, joined with a separator that can't collide."""
    payload = "\x1f".join(str(p) for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "big")


def hash64(seed: int, *keys: object) -> int:
    """Deterministic 64-bit unsigned int keyed by (seed, *keys)."""
    return _digest64(seed, *keys)


def unit(seed: int, *keys: object) -> float:
    """Deterministic float in [0.0, 1.0)."""
    return _digest64(seed, *keys) / _U64


def randint(seed: int, lo: int, hi: int, *keys: object) -> int:
    """Deterministic int in [lo, hi] inclusive."""
    if hi < lo:
        raise ValueError("hi must be >= lo")
    span = hi - lo + 1
    return lo + _digest64(seed, "randint", *keys) % span


def choice(seed: int, options: Sequence[T], *keys: object) -> T:
    if not options:
        raise ValueError("options must be non-empty")
    return options[_digest64(seed, "choice", *keys) % len(options)]


def weighted_choice(seed: int, options: Sequence[T], weights: Sequence[float], *keys: object) -> T:
    if len(options) != len(weights) or not options:
        raise ValueError("options and weights must be the same non-zero length")
    total = float(sum(weights))
    if total <= 0:
        raise ValueError("weights must sum to a positive number")
    target = unit(seed, "weighted", *keys) * total
    acc = 0.0
    for opt, w in zip(options, weights):
        acc += w
        if target < acc:
            return opt
    return options[-1]


def normal(seed: int, mu: float, sigma: float, *keys: object) -> float:
    """Deterministic normal draw via Box-Muller on two independent uniforms."""
    u1 = max(unit(seed, "n1", *keys), 1e-12)
    u2 = unit(seed, "n2", *keys)
    z = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
    return mu + sigma * z


def lognormal(seed: int, mu: float, sigma: float, *keys: object) -> float:
    return math.exp(normal(seed, mu, sigma, *keys))


def seed_sequence(seed: int, *keys: object) -> int:
    """A 64-bit seed for numpy's default_rng, keyed deterministically."""
    return _digest64(seed, "numpy", *keys)


def spark_unit_expr(seed: int, *key_cols: str) -> str:
    """SQL expression (string) giving a [0,1) uniform keyed by the given columns.

    Uses xxhash64 so it matches across Spark versions and never depends on partitioning.
    Example: spark_unit_expr(20260930, "golden_client_id", "'balance'").
    """
    cols = ", ".join(key_cols)
    # xxhash64 returns a signed long; fold to unsigned [0,1) with abs + modulo.
    return f"(pmod(xxhash64({seed}L, {cols}), 1000000000) / 1000000000.0)"
