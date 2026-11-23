"""Tool registry: every MCP server, its endpoint, auth scope and tools (with schemas).

Exposed three ways: ``GET /registry`` (HTTP), the ``registry`` MCP server's
``list_available_tools`` tool, and :func:`build_registry` in-process.
"""

from dataclasses import dataclass
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from ai_sre.mcp_servers.core import ToolResult, ToolRuntime, tool


@dataclass(frozen=True)
class ServerEntry:
    name: str
    server: MCPServer
    read_only: bool
    description: str

    @property
    def path(self) -> str:
        return f"/mcp/{self.name}/"


class ToolInfo(BaseModel):
    name: str
    title: str | None
    description: str
    read_only: bool
    destructive: bool
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None


class ServerInfo(BaseModel):
    name: str
    path: str
    read_only: bool
    auth: str = Field(description="Bearer token scope required to call this server")
    enabled: bool
    description: str
    tools: list[ToolInfo]


class RegistryResult(ToolResult):
    servers: list[ServerInfo]
    tool_count: int


async def build_registry(
    entries: list[ServerEntry],
    *,
    remediation_enabled: bool,
    include_schemas: bool,
    server: str | None = None,
) -> RegistryResult:
    servers = []
    for entry in entries:
        if server and entry.name != server:
            continue
        tools = []
        for t in await entry.server.list_tools():
            ann = t.annotations
            tools.append(
                ToolInfo(
                    name=t.name,
                    title=t.title,
                    description=t.description or "",
                    read_only=bool(ann and ann.read_only_hint),
                    destructive=bool(ann and ann.destructive_hint),
                    input_schema=t.input_schema if include_schemas else None,
                    output_schema=t.output_schema if include_schemas else None,
                )
            )
        servers.append(
            ServerInfo(
                name=entry.name,
                path=entry.path,
                read_only=entry.read_only,
                auth="remediation" if not entry.read_only else "read",
                enabled=entry.read_only or remediation_enabled,
                description=entry.description,
                tools=tools,
            )
        )
    count = sum(len(s.tools) for s in servers)
    return RegistryResult(
        summary=f"{len(servers)} servers, {count} tools", servers=servers, tool_count=count
    )


def build_registry_server(
    entries: list[ServerEntry], runtime: ToolRuntime, *, remediation_enabled: bool
) -> MCPServer:
    server = MCPServer("registry", instructions="Discover the AI SRE MCP servers and their tools.")

    @tool(server, runtime, read_only=True, title="List available tools", max_bytes=250_000)
    async def list_available_tools(
        server_name: Annotated[
            str | None, Field(max_length=40, description="Only this server")
        ] = None,
        include_schemas: bool = False,
    ) -> RegistryResult:
        """All MCP servers (path, read-only or state-changing, auth scope, enabled) and their
        tools with descriptions; set include_schemas for input/output JSON Schemas."""
        return await build_registry(
            entries,
            remediation_enabled=remediation_enabled,
            include_schemas=include_schemas,
            server=server_name,
        )

    return server
