"""remediation MCP server: state-changing actions on the SIMULATED environment.

Separated from the read-only servers: its own path (``/mcp/remediation``) and its own bearer
token. Every tool:

* refuses unless ``AI_SRE_MCP_REMEDIATION_ENABLED=true``,
* never executes without a valid, single-use human approval for the exact parameters
  (see :mod:`ai_sre.mcp_servers.approvals`),
* reports ``executed`` only if the target system confirmed the action; failures are tool errors.
"""

import time
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from ai_sre.mcp_servers.approvals import ApprovalError, ApprovalStore
from ai_sre.mcp_servers.backends import BackendError, Backends
from ai_sre.mcp_servers.core import AppService, ToolFailure, ToolResult, ToolRuntime, iso, tool
from ai_sre.mcp_servers.settings import McpSettings

Reason = Annotated[
    str, Field(min_length=5, max_length=300, description="Why (recorded with the approval)")
]
Token = Annotated[
    str | None,
    Field(
        max_length=2000,
        description="Approval token from a human approver; omit to request approval",
    ),
]


class RemediationResult(ToolResult):
    status: Literal["approval_required", "executed"]
    action: str
    params: dict[str, Any]
    simulated: bool = True
    approval_request_id: str
    approval_expires_at: str | None = None
    approved_by: str | None = None
    how_to_approve: str | None = None
    result: dict[str, Any] | None = Field(default=None, description="Response of the target system")


def build_remediation_server(
    backends: Backends, runtime: ToolRuntime, settings: McpSettings, approvals: ApprovalStore
) -> MCPServer:
    server = MCPServer(
        "remediation",
        instructions="State-changing actions on the SIMULATED environment. Disabled unless enabled "
        "in configuration; every call needs a human approval token for its exact parameters.",
    )

    async def run(
        action: str, params: dict[str, Any], token: str | None, execute: Any
    ) -> RemediationResult:
        if not settings.remediation_enabled:
            raise ToolFailure(
                "remediation_disabled",
                "remediation tools are disabled",
                "an operator must set AI_SRE_MCP_REMEDIATION_ENABLED=true",
            )
        if not token:
            req = approvals.request(action, params)
            return RemediationResult(
                summary=f"approval required for {action} {params}; request {req.id}",
                status="approval_required",
                action=action,
                params=params,
                approval_request_id=req.id,
                approval_expires_at=iso_ts(req.expires_at),
                how_to_approve=f"a human runs: python -m ai_sre.mcp_servers approve {req.id} --approver <name>; "
                "then call this tool again with the same parameters and approval_token",
            )
        try:
            req = approvals.consume(token, action, params)
        except ApprovalError as exc:
            raise ToolFailure("approval_invalid", str(exc), "request a new approval") from exc
        try:
            result = await execute()
        except BackendError as exc:
            req.executed_result = {"error": str(exc)}
            raise ToolFailure(
                "action_failed",
                f"{action} was approved but FAILED: {exc}",
                "nothing was changed; check the target system",
            ) from exc
        req.executed_result = result
        return RemediationResult(
            summary=f"{action} executed (simulated) for {params}",
            status="executed",
            action=action,
            params=params,
            approval_request_id=req.id,
            approved_by=req.approver,
            result=result,
        )

    orchestrator, deployer = backends.orchestrator, backends.deployer

    @tool(
        server,
        runtime,
        read_only=False,
        destructive=True,
        idempotent=False,
        title="Restart service (simulation)",
    )
    async def restart_service_simulation(
        service: AppService, reason: Reason, approval_token: Token = None
    ) -> RemediationResult:
        """Restart a service's container (the process exits and is started again: memory and
        connections are released; in-flight requests fail). Requires human approval."""
        return await run(
            "restart_service",
            {"service": service, "reason": reason},
            approval_token,
            lambda: orchestrator.request(
                "POST", "/api/v1/actions/restart", json={"service": service}
            ),
        )

    @tool(
        server,
        runtime,
        read_only=False,
        destructive=True,
        idempotent=False,
        title="Roll back deployment (simulation)",
    )
    async def rollback_deployment_simulation(
        service: AppService, reason: Reason, approval_token: Token = None
    ) -> RemediationResult:
        """Roll a service back to the version and configuration it ran before its latest
        deployment (recorded as a `rollback` deployment). Requires human approval."""
        return await run(
            "rollback_deployment",
            {"service": service, "reason": reason},
            approval_token,
            lambda: deployer.request(
                "POST",
                f"/api/v1/deployments/{service}/rollback",
                json={"reason": reason, "author": "ai-sre (approved)"},
            ),
        )

    @tool(
        server,
        runtime,
        read_only=False,
        destructive=True,
        idempotent=False,
        title="Clear queue (simulation)",
    )
    async def clear_queue_simulation(
        queue: Literal["fulfilment", "fulfilment-dead-letter"],
        reason: Reason,
        approval_token: Token = None,
    ) -> RemediationResult:
        """Delete every job in a queue. IRREVERSIBLE: the jobs are lost. Requires human approval."""
        return await run(
            "clear_queue",
            {"queue": queue, "reason": reason},
            approval_token,
            lambda: orchestrator.request(
                "POST", "/api/v1/actions/clear-queue", json={"queue": queue}
            ),
        )

    @tool(
        server,
        runtime,
        read_only=False,
        destructive=False,
        idempotent=True,
        title="Scale service (simulation)",
    )
    async def scale_service_simulation(
        service: AppService,
        replicas: Annotated[int, Field(ge=1, le=10)],
        reason: Reason,
        approval_token: Token = None,
    ) -> RemediationResult:
        """Set desired replicas. In the simulator only fulfilment-worker capacity changes
        (consumers = 2 x replicas); other services record the value. Requires human approval."""
        return await run(
            "scale_service",
            {"service": service, "replicas": replicas, "reason": reason},
            approval_token,
            lambda: orchestrator.request(
                "POST", "/api/v1/actions/scale", json={"service": service, "replicas": replicas}
            ),
        )

    return server


def iso_ts(ts: float) -> str:
    from datetime import UTC, datetime

    return iso(datetime.fromtimestamp(ts, UTC))


_ = time  # imported for type-checkers of callers that patch time in tests
