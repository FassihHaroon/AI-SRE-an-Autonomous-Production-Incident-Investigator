"""Fault catalog validation and the seeded sampler."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_sre_simulator.common.determinism import Sampler
from ai_sre_simulator.common.faults import FAULT_SPECS, FaultRequest, FaultType


def test_catalog_covers_every_required_fault_kind() -> None:
    required = {
        FaultType.API_LATENCY,  # increased API latency
        FaultType.DB_POOL_EXHAUSTION,  # pool exhaustion
        FaultType.SLOW_DB_QUERIES,  # slow queries
        FaultType.REDIS_FAILURE,  # redis failure
        FaultType.HTTP_ERRORS,  # elevated 500s
        FaultType.WORKER_BACKLOG,  # queue backlog
        FaultType.FAILED_DEPLOYMENT,  # failed deployment
        FaultType.MEMORY_PRESSURE,  # memory pressure
        FaultType.DEPENDENCY_OUTAGE,  # dependency outage
    }
    assert required <= set(FAULT_SPECS)


def test_defaults_are_filled_in() -> None:
    req = FaultRequest(type=FaultType.REDIS_FAILURE, target="shop-cache")
    assert req.params == {"mode": "timeout", "latency_ms": 800}


def test_unknown_target_lists_valid_ones() -> None:
    with pytest.raises(ValidationError, match="valid targets: api-gateway, orders-service"):
        FaultRequest(type=FaultType.API_LATENCY, target="postgres", params={"delay_ms": 5})


@pytest.mark.parametrize(
    ("fault_type", "target", "params"),
    [
        (FaultType.HTTP_ERRORS, "api-gateway", {"rate": 1.5}),
        (FaultType.API_LATENCY, "api-gateway", {}),
        (FaultType.REDIS_FAILURE, "shop-cache", {"mode": "explode"}),
        (FaultType.MEMORY_PRESSURE, "orders-service", {"max_mb": 10_000}),
        (FaultType.TRAFFIC_SURGE, "loadgen", {"multiplier": 0.5}),
        (FaultType.HTTP_ERRORS, "api-gateway", {"rate": 0.5, "unexpected": 1}),
    ],
)
def test_bad_params_are_rejected(
    fault_type: FaultType, target: str, params: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        FaultRequest(type=fault_type, target=target, params=params)


def test_sampler_is_deterministic_per_seed_and_key() -> None:
    a, b = Sampler(), Sampler()
    seq_a = [a.chance(42, "f-0001", 0.3) for _ in range(500)]
    seq_b = [b.chance(42, "f-0001", 0.3) for _ in range(500)]
    assert seq_a == seq_b
    assert 0.2 < sum(seq_a) / 500 < 0.4
    c = Sampler()
    assert [c.chance(43, "f-0001", 0.3) for _ in range(500)] != seq_a


def test_sampler_keys_are_independent() -> None:
    a, b = Sampler(), Sampler()
    expected = [a.uniform(1, "x") for _ in range(10)]
    got = []
    for _ in range(10):
        b.uniform(1, "other")  # interleaved draws on another key must not shift "x"
        got.append(b.uniform(1, "x"))
    assert got == expected


def test_sampler_forget_and_reset_restart_sequences() -> None:
    s = Sampler()
    first = [s.uniform(1, "f-1") for _ in range(3)]
    s.uniform(1, "f-1:j")
    s.uniform(1, "f-10")
    s.forget("f-1")
    assert [s.uniform(1, "f-1") for _ in range(3)] == first
    assert s.uniform(1, "f-10") != Sampler().uniform(1, "f-10"), "f-10 is not a sub-key of f-1"
    s.reset()
    assert [s.uniform(1, "f-1") for _ in range(3)] == first


def test_jitter_is_bounded() -> None:
    s = Sampler()
    values = [s.jitter(7, "j", 30) for _ in range(1000)]
    assert all(-30 <= v <= 30 for v in values)
    assert s.jitter(7, "j", 0) == 0
