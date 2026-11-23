"""Load generator determinism, CLI parsing and public deployment model."""

from __future__ import annotations

import random
from collections import Counter
from datetime import UTC, datetime

from ai_sre_simulator import cli
from ai_sre_simulator.deployer.models import BASELINE, Deployment, DeployRequest, commit_sha_for
from ai_sre_simulator.loadgen.main import MIX, plan_request


def test_traffic_sequence_is_deterministic_for_a_seed() -> None:
    a, b = random.Random(1337), random.Random(1337)  # noqa: S311
    assert [plan_request(a) for _ in range(200)] == [plan_request(b) for _ in range(200)]


def test_traffic_mix_matches_weights() -> None:
    rng = random.Random(7)  # noqa: S311
    counts = Counter(plan_request(rng).name for _ in range(20_000))
    for name, weight in MIX:
        assert abs(counts[name] / 20_000 - weight / 100) < 0.02, name


def test_cli_parses_params_as_json_where_possible() -> None:
    assert cli._params(["delay_ms=400", "route_prefix=/api", 'queries=["a","b"]']) == {
        "delay_ms": 400,
        "route_prefix": "/api",
        "queries": ["a", "b"],
    }


def test_cli_parser_knows_every_command() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["trigger", "redis-outage", "--speed", "10"])
    assert (args.command, args.scenario, args.speed) == ("trigger", "redis-outage", 10.0)
    args = parser.parse_args(
        ["inject", "api_latency", "--target", "api-gateway", "--param", "delay_ms=5"]
    )
    assert args.param == ["delay_ms=5"]
    assert parser.parse_args(["reset", "--data"]).data is True


def test_public_deployment_model_hides_build_flags() -> None:
    assert "flags" not in Deployment.model_fields
    assert "runtime_flags" not in Deployment.model_fields
    req = DeployRequest(
        service="orders-service",
        version="9.9.9",
        changes_summary="x",
        flags={"order_history_query": "per_order_items"},
    )
    assert req.flags  # the request may carry flags; the public record never shows them
    d = Deployment(
        id="d-1",
        service="orders-service",
        kind="deploy",
        version="9.9.9",
        previous_version="2.3.1",
        status="succeeded",
        started_at=datetime.now(UTC),
        finished_at=None,
        changes_summary="x",
        author="a",
        commit_sha=None,
        failure_reason=None,
    )
    assert "flags" not in d.model_dump()


def test_baseline_history_is_deterministic() -> None:
    assert [(b.service, b.version) for b in BASELINE][-3:] == [
        ("api-gateway", "3.8.0"),
        ("orders-service", "2.3.0"),
        ("orders-service", "2.3.1"),
    ]
    assert commit_sha_for("orders-service", "2.4.0") == commit_sha_for("orders-service", "2.4.0")
    assert len(commit_sha_for("orders-service", "2.4.0")) == 12
