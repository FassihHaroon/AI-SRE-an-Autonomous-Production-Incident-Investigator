"""Scenario definitions: schema, validation errors, documentation coverage."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_sre_simulator.chaos.scenarios import (
    DEFAULT_SCENARIO_DIR,
    ScenarioError,
    load_scenario,
    load_scenarios,
)
from ai_sre_simulator.common.faults import FAULT_SPECS

REPO = Path(__file__).resolve().parents[2]
REQUIRED = {  # the five incidents from the Phase 2 brief, A-E
    "orders-n-plus-one-deploy",
    "redis-outage",
    "worker-deploy-backlog",
    "payments-provider-errors",
    "db-connection-leak",
}


def test_all_scenarios_load_and_include_the_required_five() -> None:
    scenarios = load_scenarios()
    assert len(scenarios) >= 5
    assert scenarios.keys() >= REQUIRED


@pytest.mark.parametrize("scenario_id", sorted(load_scenarios()))
def test_every_scenario_is_documented(scenario_id: str) -> None:
    doc = REPO / "docs" / "scenarios" / f"{scenario_id}.md"
    assert doc.is_file(), f"missing {doc}"
    text = doc.read_text(encoding="utf-8")
    assert f"simctl trigger {scenario_id}" in text
    for heading in ("## What happens", "## Expected signals", "## Root cause", "## Clearing"):
        assert heading in text, f"{doc.name} lacks '{heading}'"


@pytest.mark.parametrize("scenario_id", sorted(load_scenarios()))
def test_scenario_steps_are_valid(scenario_id: str) -> None:
    scenario = load_scenarios()[scenario_id]
    for step in scenario.steps:
        if step.fault is not None:
            assert step.fault.target in FAULT_SPECS[step.fault.type].targets


def test_seeds_are_unique() -> None:
    seeds = [s.seed for s in load_scenarios().values()]
    assert len(seeds) == len(set(seeds))


def test_validation_errors_name_the_file_and_field(tmp_path: Path) -> None:
    bad = tmp_path / "broken.yaml"
    bad.write_text(
        "id: broken\ntitle: t\ncategory: c\nsummary: s\nseed: 1\nsignals: [x]\n"
        "steps:\n  - at_s: 0\n    description: d\n"
        "    fault: {type: redis_failure, target: api-gateway}\n",
        encoding="utf-8",
    )
    with pytest.raises(ScenarioError) as err:
        load_scenario(bad)
    assert "broken.yaml" in str(err.value)
    assert "steps.0.fault" in str(err.value)
    assert "valid targets: shop-cache" in str(err.value)


def test_step_needs_exactly_one_action(tmp_path: Path) -> None:
    bad = tmp_path / "empty-step.yaml"
    bad.write_text(
        "id: empty-step\ntitle: t\ncategory: c\nsummary: s\nseed: 1\nsignals: [x]\n"
        "steps:\n  - at_s: 0\n    description: nothing\n",
        encoding="utf-8",
    )
    with pytest.raises(ScenarioError, match="exactly one"):
        load_scenario(bad)


def test_file_name_must_match_id(tmp_path: Path) -> None:
    src = (DEFAULT_SCENARIO_DIR / "redis-outage.yaml").read_text(encoding="utf-8")
    other = tmp_path / "renamed.yaml"
    other.write_text(src, encoding="utf-8")
    with pytest.raises(ScenarioError, match="must match the file name"):
        load_scenario(other)
