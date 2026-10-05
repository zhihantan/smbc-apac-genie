"""Deterministic RNG unit tests — same inputs must always give the same output."""
import statistics

from smbc_genie_lib import rng

SEED = 20260930


def test_unit_is_deterministic_and_in_range():
    a = rng.unit(SEED, "KINOKAWA", "deposit", 1)
    b = rng.unit(SEED, "KINOKAWA", "deposit", 1)
    assert a == b
    assert 0.0 <= a < 1.0


def test_different_keys_differ():
    assert rng.unit(SEED, "A") != rng.unit(SEED, "B")
    assert rng.unit(SEED, "A", 1) != rng.unit(SEED, "A", 2)
    assert rng.unit(SEED, "A") != rng.unit(SEED + 1, "A")


def test_randint_bounds_inclusive():
    seen = {rng.randint(SEED, 1, 6, "die", i) for i in range(2000)}
    assert seen == {1, 2, 3, 4, 5, 6}


def test_choice_and_weighted_choice_deterministic():
    opts = ["SG", "HK", "AU", "IN"]
    assert rng.choice(SEED, opts, "e1") == rng.choice(SEED, opts, "e1")
    assert rng.weighted_choice(SEED, opts, [1, 1, 1, 1], "e2") in opts


def test_weighted_choice_respects_weights():
    opts = ["rare", "common"]
    draws = [rng.weighted_choice(SEED, opts, [1, 99], i) for i in range(3000)]
    commons = draws.count("common")
    assert commons > draws.count("rare")  # ~99:1


def test_normal_mean_is_close():
    xs = [rng.normal(SEED, 10.0, 2.0, i) for i in range(5000)]
    assert abs(statistics.mean(xs) - 10.0) < 0.2


def test_spark_unit_expr_shape():
    expr = rng.spark_unit_expr(SEED, "golden_client_id", "'balance'")
    assert "xxhash64" in expr and str(SEED) in expr
