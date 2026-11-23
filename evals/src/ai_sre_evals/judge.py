"""Optional model-based judge for the qualities a program cannot check well.

The programmatic scorer decides correctness (category, component, trigger) exactly. The judge
adds an opinion on the *explanation*: is the reasoning coherent with the ground truth, is it
specific and actionable for on-call? It sees the ground truth and the report summary only (no
raw telemetry), returns structured JSON, and its verdict is compared with the programmatic one
to measure agreement. It is off by default (``--judge``) because it spends LLM tokens.

Limitations: one judge model, single sample, no calibration set; scores are ordinal opinions,
not measurements. Use them to spot explanation problems, never to override the program.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ai_sre.agent.models import Report
from ai_sre.integrations.llm.base import (
    LLMError,
    LLMMessage,
    LLMProvider,
    LLMRequest,
    generate_structured,
)
from ai_sre_evals.dataset import Scenario


class JudgeVerdict(BaseModel):
    verdict: Literal["correct", "partially_correct", "incorrect", "abstained"]
    explanation_quality: int = Field(ge=1, le=5, description="Coherent, specific, causal (5 best)")
    actionability: int = Field(ge=1, le=5, description="Would on-call know what to do next")
    unsupported_statements: int = Field(
        ge=0, description="Statements not backed by the report's evidence"
    )
    rationale: str = Field(max_length=600)


def _prompt(scenario: Scenario, report: Report | None) -> str:
    exp = scenario.expected
    truth = (
        f"{exp.root_cause.category} in {exp.root_cause.component}"
        + (f" triggered by {exp.root_cause.trigger_ref}" if exp.root_cause.trigger_ref else "")
        if exp.root_cause
        else "NO identifiable root cause (the right answer is to abstain / ask for more data)"
    )
    # Simultaneous failures: naming any of the true causes is correct. (Run 20261123-151159
    # omitted these and the judge marked a correct S16 answer as incorrect.)
    if exp.also_correct:
        truth += "; ALSO a true, independent cause (equally correct): " + "; ".join(
            f"{c.category} in {c.component}" for c in exp.also_correct
        )
    if exp.acceptable_alternatives:
        truth += "; partially correct: " + "; ".join(
            f"{c.category} in {c.component}" for c in exp.acceptable_alternatives
        )
    if report is None:
        body = "(the investigation produced no report)"
    else:
        rc = report.root_cause
        alts = "; ".join(f"{a.title} ({a.confidence:.2f})" for a in report.alternatives[:3])
        recs = "; ".join(p.description for p in report.recommendations[:3])
        body = (
            f"Summary: {report.summary}\n"
            f"Root cause: {rc.title if rc else 'none'}\n"
            f"Explanation: {(rc.explanation if rc else '')[:1200]}\n"
            f"Alternatives: {alts or 'none'}\nRecommended actions: {recs or 'none'}"
        )
    return (
        "You grade an automated incident investigation against the known ground truth.\n"
        f"Incident: {scenario.description}\nGround truth root cause: {truth}\n"
        f"Expected remediation: {exp.remediation.action} {exp.remediation.target or ''} {' '.join(exp.remediation.keywords)}\n\n"
        f"Investigation report:\n{body}\n\n"
        "Grade strictly. 'correct' only if the identified cause matches the ground truth cause and "
        "component; a symptom (e.g. 'slow queries') instead of its trigger is 'partially_correct'."
    )


async def judge(
    provider: LLMProvider, scenario: Scenario, report: Report | None
) -> JudgeVerdict | None:
    try:
        verdict, _ = await generate_structured(
            provider,
            LLMRequest(
                messages=[LLMMessage(role="user", content=_prompt(scenario, report))],
                node="eval_judge",
                max_output_tokens=400,
            ),
            JudgeVerdict,
        )
    except LLMError:
        return None
    return verdict
