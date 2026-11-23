"""MCP integration layer: the tools the AI SRE agent investigates and remediates with.

Eight MCP servers (official ``mcp`` SDK, Streamable HTTP) are mounted in one ASGI app:

======================  =======================  ==========================================
Path                    Server                   Backed by
======================  =======================  ==========================================
``/mcp/logs``           logs (read-only)         Loki (LogQL)
``/mcp/metrics``        metrics (read-only)      Prometheus (PromQL)
``/mcp/deployments``    deployments (read-only)  deployer API
``/mcp/database``       database (read-only)     PostgreSQL (read-only role) + Prometheus
``/mcp/infrastructure`` infrastructure (r/o)     Prometheus (up, process, alerts, exporters)
``/mcp/incidents``      incidents (read-only)    knowledge/incidents/*.md
``/mcp/registry``       registry (read-only)     the other servers' tool lists
``/mcp/remediation``    remediation (WRITES)     chaos orchestrator + deployer; disabled by
                                                 default; human approval per action
======================  =======================  ==========================================

See docs/mcp.md.
"""
