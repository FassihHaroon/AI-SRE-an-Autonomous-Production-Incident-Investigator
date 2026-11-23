"""Agent configurations compared by the evaluation (reasoner, prompt, limits, tool ablations)."""

from __future__ import annotations

from dataclasses import dataclass, field

from ai_sre.agent.models import Limits


@dataclass(frozen=True)
class AgentConfig:
    name: str
    description: str
    reasoner: str = "rules"  # rules | llm
    prompt: str = "default"  # LLM planning prompt variant (llm reasoner only)
    limits: Limits = field(default_factory=Limits)
    disabled_tools: frozenset[str] = frozenset()

    @property
    def uses_llm(self) -> bool:
        return self.reasoner == "llm"


NO_KNOWLEDGE = frozenset(
    {
        "knowledge.find_similar_incidents",
        "knowledge.search_knowledge",
        "knowledge.compare_with_incident",
        "incidents.search_incident_history",
    }
)

CONFIGS: dict[str, AgentConfig] = {
    c.name: c
    for c in [
        AgentConfig(
            "rules-baseline",
            "Rule-based planner, default limits (3 rounds, 24 tool calls, stop at 0.75)",
        ),
        AgentConfig(
            "rules-single-round",
            "Triage round only: no targeted follow-up evidence",
            limits=Limits(max_rounds=1),
        ),
        AgentConfig(
            "rules-eager-stop",
            "Stops as soon as the leading hypothesis reaches 0.55",
            limits=Limits(sufficient_confidence=0.55),
        ),
        AgentConfig(
            "rules-no-knowledge",
            "Ablation: knowledge base / incident history tools unavailable",
            disabled_tools=NO_KNOWLEDGE,
        ),
        AgentConfig(
            "rules-tight-budget",
            "At most 10 tool calls",
            limits=Limits(max_tool_calls=10),
        ),
        AgentConfig(
            "llm-planner",
            "LLM intake + planning (default prompt), rubric confidence",
            reasoner="llm",
        ),
        AgentConfig(
            "llm-planner-hypothesis-first",
            "LLM planning with a prompt that asks for one disconfirming check per hypothesis",
            reasoner="llm",
            prompt="hypothesis_first",
        ),
    ]
}

DEFAULT_CONFIGS = [
    "rules-baseline",
    "rules-single-round",
    "rules-eager-stop",
    "rules-no-knowledge",
    "rules-tight-budget",
]
