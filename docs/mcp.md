# MCP tool servers

Phase 3 deliverable. The AI SRE agent never receives telemetry pasted into its prompt: it calls
**tools** over the [Model Context Protocol](https://modelcontextprotocol.io) (official `mcp`
Python SDK 2.x, Streamable HTTP transport). The tools read the same monitoring interfaces an SRE
uses on the [simulated environment](simulator.md) (Prometheus, Loki, the deployer, PostgreSQL)
and can, with human approval, act on it.

Code: [backend/src/ai_sre/mcp_servers/](../backend/src/ai_sre/mcp_servers/).

## 1. Run it

```bash
uv run poe sim-up                 # the simulated environment the tools investigate
uv run poe mcp                    # MCP servers on http://127.0.0.1:8300  (or: docker compose up -d mcp)
uv run python -m ai_sre.mcp_servers registry          # list servers + tools (uses the read token)
```

| Endpoint | Server | Scope (bearer token) |
|----------|--------|----------------------|
| `/mcp/registry/` | tool discovery | read |
| `/mcp/logs/` | logs (Loki) | read |
| `/mcp/metrics/` | metrics (Prometheus) | read |
| `/mcp/deployments/` | deployment history (deployer) | read |
| `/mcp/database/` | PostgreSQL diagnostics | read |
| `/mcp/infrastructure/` | services, containers, resources, dependencies | read |
| `/mcp/incidents/` | historical incidents (`knowledge/incidents/*.md`) | read |
| `/mcp/remediation/` | **state-changing**, approval-gated | remediation |
| `GET /registry` | JSON registry (`?schemas=true` for JSON Schemas) | read |
| `GET /approvals`, `POST /approvals/{id}/approve\|reject` | human decisions | admin |
| `GET /healthz` | liveness | none |

Note the trailing slash on MCP endpoints (`/mcp/logs/`).

Connect from any MCP client, e.g. the SDK:

```python
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

http = httpx2.AsyncClient(headers={"Authorization": "Bearer dev-mcp-read-token"})
async with Client(streamable_http_client("http://127.0.0.1:8300/mcp/metrics/", http_client=http)) as client:
    tools = await client.list_tools()
    result = await client.call_tool("get_service_metrics", {"service": "orders-service"})
    print(result.structured_content)
```

### Configuration (`AI_SRE_MCP_*`, see [.env.example](../.env.example))

| Variable | Default | Meaning |
|----------|---------|---------|
| `AI_SRE_MCP_HOST` / `_PORT` | `127.0.0.1` / `8300` | Bind address |
| `AI_SRE_MCP_PROMETHEUS_URL` | `http://127.0.0.1:9090` | Simulated environment's Prometheus |
| `AI_SRE_MCP_LOKI_URL` | `http://127.0.0.1:3100` | …Loki |
| `AI_SRE_MCP_DEPLOYER_URL` | `http://127.0.0.1:8202` | …deployment API |
| `AI_SRE_MCP_ORCHESTRATOR_URL` | `http://127.0.0.1:8210` | …orchestrator (restart/scale/clear queue) |
| `AI_SRE_MCP_DATABASE_DSN` | `postgresql://sre_readonly:…@127.0.0.1:55432/shop` | Read-only role (`pg_monitor`), read-only transactions |
| `AI_SRE_MCP_READ_TOKEN` / `_REMEDIATION_TOKEN` / `_ADMIN_TOKEN` | dev values | Bearer tokens per scope (**production refuses the dev defaults**) |
| `AI_SRE_MCP_APPROVAL_SECRET` | dev value | HMAC key for approval tokens |
| `AI_SRE_MCP_REMEDIATION_ENABLED` | `false` | Remediation tools refuse to run unless `true` |
| `AI_SRE_MCP_APPROVAL_TTL_S` | `900` | Approval request/token lifetime |
| `AI_SRE_MCP_TOOL_TIMEOUT_S` | `20` | Per-call timeout (some tools override) |
| `AI_SRE_MCP_BACKEND_TIMEOUT_S` | `10` | Per backend request / SQL `statement_timeout` |
| `AI_SRE_MCP_MAX_RESULT_BYTES` | `16000` | Result size cap (lists trimmed, `truncated: true`) |

## 2. Tool contract

Every tool:

| Requirement | How |
|-------------|-----|
| Name + description | Function name; docstring is the MCP `description` (shown to the model) |
| Input schema + validation | Type hints with pydantic `Field` bounds (`ge/le`, `pattern`, `Literal` enums) → MCP `inputSchema`; invalid arguments are rejected by the SDK **before** the tool runs |
| Typed output | Return type is a pydantic model → MCP `outputSchema` + `structuredContent` (JSON). Common fields: `tool`, `summary`, `truncated` |
| Error handling | Failures are MCP tool errors (`isError: true`) whose text is JSON `{"code", "message", "hint"}`. Codes: `invalid_argument`, `not_found`, `backend_unavailable`, `timeout`, `remediation_disabled`, `approval_invalid`, `action_failed`, `internal_error` |
| Timeouts | `asyncio.timeout` around every call (`AI_SRE_MCP_TOOL_TIMEOUT_S`, per-tool overrides) + per-backend HTTP/SQL timeouts |
| Bounded results | Time windows (`since_minutes`, max 24 h), `limit`/`top_k` caps, pagination (`before` cursor for logs, `offset` for incidents), server-side aggregation (error signatures, statistics, anomaly summaries), a byte cap |
| Invocation log | One `mcp_tool_call` log line per call: tool, server, arguments (approval tokens redacted), outcome, error code, duration, result size, truncation |
| Read-only vs state-changing | Separate server, path and token for remediation. MCP annotations `readOnlyHint` / `destructiveHint` / `idempotentHint` on every tool |

## 3. Tools

### logs (Loki)

| Tool | Arguments | Returns |
|------|-----------|---------|
| `search_logs` | `service?`, `contains?` (substring), `level?`, `since_minutes=15`, `before?` (cursor), `limit≤200` | Parsed entries (timestamp, service, severity, message, endpoint, version, trace/request id, error type/message), `next_before` |
| `get_error_logs` | `service?`, `since_minutes`, `limit≤100` | Latest ERROR/CRITICAL entries |
| `aggregate_errors` | `service?`, `since_minutes=30`, `top_k≤25` | Exact error counts per service + signatures (numbers/ids normalised) with count, first/last seen, example |
| `get_logs_by_trace_id` | `trace_id` (32 hex), `since_minutes`, `limit` | One request across gateway → orders → worker, oldest first |

### metrics (Prometheus)

| Tool | Arguments | Returns |
|------|-----------|---------|
| `get_metric_timeseries` | `promql`, `since_minutes`, `max_points≤120`, `max_series≤20` | Series with stats (min/max/avg/last) and downsampled points |
| `get_service_metrics` | `service` | Golden signals now: rate, error ratio, p50/p95/p99, CPU, memory, pool utilisation/waiters, DB p95, Redis/upstream errors, queue, version |
| `compare_metric_windows` | `service`, `signal` or `promql`, `window_minutes`, `baseline_offset_minutes`, or `split_at` (after vs immediately before a moment, e.g. a deployment) | Current vs baseline window stats, change %, direction |
| `detect_metric_anomalies` | `services?`, `since_minutes=10`, `baseline_minutes=30`, `z_threshold=3` | Signals departing from a robust baseline (median/MAD, absolute floors) with the onset of the **current** episode (start of the last contiguous anomalous run) |

Named signals: `request_rate`, `error_rate`, `latency_p50/p95/p99`, `cpu`, `memory_bytes`,
`db_pool_utilization`, `db_pool_pending`, `db_query_p95`, `redis_error_rate`,
`upstream_error_rate`, `queue_depth`, `worker_failure_rate`.

### deployments (deployer)

| Tool | Arguments | Returns |
|------|-----------|---------|
| `list_recent_deployments` | `service?`, `since_hours=24`, `status?`, `limit≤50` | Deploys, rollbacks and config changes, newest first |
| `get_deployment_details` | `deployment_id` (`d-408`) | Record, previous deployment of that service, age, whether its version is running |
| `compare_deployment_versions` | `service`, `from_version`, `to_version` | Deployments between the versions with change summaries and commits |

### database (PostgreSQL, read-only role)

| Tool | Arguments | Returns |
|------|-----------|---------|
| `get_database_health` | — | Connections by application and state (oldest transaction, longest query, sample), utilisation, lock waits, cache hit ratio, deadlocks |
| `get_slow_queries` | `top_k≤25`, `order_by` (`mean_time`/`total_time`/`calls`), `min_calls` | `pg_stat_statements` top statements (normalised text, calls, mean/max/total ms) |
| `get_connection_pool_status` | `service?` | App pools (max/in use/idle/waiters/timeouts/acquire p95) + server sessions per service |
| `get_database_metrics` | `window_minutes`, `top_queries` | Commits/rollbacks/rows per second, active and idle-in-transaction sessions, slowest client-side queries |

### infrastructure (Prometheus)

| Tool | Arguments | Returns |
|------|-----------|---------|
| `get_service_status` | `service?` | Up, version, uptime, restarts/h, firing alerts, health verdict |
| `get_container_health` | `service?` | Memory vs container limit (OOM risk), start time, restarts, open fds |
| `get_resource_usage` | `service?`, `window_minutes` | CPU avg/max, memory avg/max, memory growth rate (leak indicator) |
| `get_dependency_status` | — | orders-service, PostgreSQL, Redis, payments provider, fulfilment queue: health + evidence |

### incidents (`knowledge/incidents/*.md`)

| Tool | Arguments | Returns |
|------|-----------|---------|
| `list_historical_incidents` | `service?`, `category?`, `limit`, `offset` | Paginated summaries |
| `get_incident_details` | `incident_id` (`INC-2026-031`) | What happened, diagnosis, resolution |
| `search_incident_history` | `query`, `service?`, `top_k≤10` | Ranked similar incidents (weighted keyword match; hybrid RAG in Phase 6) |

### remediation (state-changing, SIMULATED environment only)

| Tool | Arguments | Effect | Reversible? |
|------|-----------|--------|-------------|
| `restart_service_simulation` | `service`, `reason`, `approval_token?` | The process exits and Docker restarts it (memory, connections, in-process state really reset) | Yes (no data loss; in-flight requests fail) |
| `rollback_deployment_simulation` | `service`, `reason`, `approval_token?` | Deployer records a `rollback` to the previous version/config | Yes (roll forward again) |
| `clear_queue_simulation` | `queue`, `reason`, `approval_token?` | Deletes all jobs in the queue | **No** (jobs are lost) |
| `scale_service_simulation` | `service`, `replicas≤10`, `reason`, `approval_token?` | Desired replicas; fulfilment-worker consumers = 2 × replicas | Yes |

### knowledge (PostgreSQL + pgvector, see [knowledge.md](knowledge.md))

| Tool | Arguments | Returns |
|------|-----------|---------|
| `search_knowledge` | `query`, `doc_types?`, `services?`, `top_k≤10`, `mode` (hybrid/semantic/keyword) | Hits with citation (document id, source file, section, incident id), snippet, scores, `superseded_by`, `confident` |
| `find_similar_incidents` | `description`, `services?` (boost), `top_k≤10` | Similar past incidents with citation, category, severity; `confident=false` when none is clearly similar |
| `get_knowledge_document` | `document_id`, `section?` | Sections of one document with its citation |
| `compare_with_incident` | `incident_id`, `observations[≤15]`, `services?` | Which observations the past incident also describes, shared services, match ratio |

### registry

`list_available_tools(server_name?, include_schemas?)`: every server with path, read-only flag, auth
scope, enabled flag and its tools (descriptions, annotations, optional schemas). Also served as
`GET /registry`.

## 4. Remediation safety

1. **Disabled by default.** Unless `AI_SRE_MCP_REMEDIATION_ENABLED=true`, every remediation tool
   returns `remediation_disabled` and calls nothing.
2. **Separate authorization.** `/mcp/remediation/` accepts only the remediation token; read tokens
   get HTTP 403.
3. **Human approval for every action.** A call without `approval_token` only creates an approval
   request (exact action and parameters) and returns `status: "approval_required"`.
4. **Approval is human-only.** Approving needs the admin token (`POST /approvals/{id}/approve` or
   `python -m ai_sre.mcp_servers approve <id> --approver <name>`). The agent never holds it, and
   approvers named `agent`/`ai-sre`/`mcp` are refused.
5. **Tokens bind everything.** `base64(payload).HMAC-SHA256` over request id, action, SHA-256 of the
   canonical parameters, approver and expiry. Any change to the parameters, a different action, a
   forged signature, an expired or rejected request, or a **second use** → `approval_invalid`.
6. **Honest results.** `executed` is returned only when the target system confirmed the action;
   any failure is `action_failed` with "FAILED" in the message.

## 5. Sample calls and outputs

Captured from the running environment during scenario A (`orders-n-plus-one-deploy`). Lists are
shortened to two items.

**`logs.aggregate_errors`**

```json
{
  "service": "orders-service",
  "since_minutes": 10,
  "top_k": 3
}
```

→ structuredContent:

```json
{
  "tool": "aggregate_errors",
  "summary": "280 errors in 10 min across 1 services; top: 'timed out after <n>.0s waiting for a database connection (pool size=<n>, in use=<n>, waiting=<n>)' x139",
  "truncated": false,
  "window_start": "2026-11-23T11:10:01Z",
  "window_end": "2026-11-23T11:20:01Z",
  "total_errors": 280,
  "analysed": 280,
  "by_service": {
    "orders-service": 280
  },
  "signatures": [
    {
      "service": "orders-service",
      "template": "timed out after <n>.0s waiting for a database connection (pool size=<n>, in use=<n>, waiting=<n>)",
      "error_type": "PoolTimeoutError",
      "count": 139,
      "first_seen": "2026-11-23T11:10:05.956Z",
      "last_seen": "2026-11-23T11:19:59.923Z",
      "example": "timed out after 2.0s waiting for a database connection (pool size=8, in use=8, waiting=5)"
    },
    {
      "service": "orders-service",
      "template": "GET /customers/<n>/orders <n>",
      "error_type": null,
      "count": 63,
      "first_seen": "2026-11-23T11:10:03.425Z",
      "last_seen": "2026-11-23T11:19:59.924Z",
      "example": "GET /customers/270/orders 503"
    },
    "... 1 more"
  ]
}
```

**`deployments.list_recent_deployments`**

```json
{
  "since_hours": 2,
  "limit": 3
}
```

→ structuredContent:

```json
{
  "tool": "list_recent_deployments",
  "summary": "2 deployments in the last 2 h; latest d-408 orders-service 2.4.0 (succeeded)",
  "truncated": false,
  "since": "2026-11-23T09:20:01Z",
  "count": 2,
  "deployments": [
    {
      "id": "d-408",
      "service": "orders-service",
      "kind": "deploy",
      "version": "2.4.0",
      "previous_version": "2.3.1",
      "status": "succeeded",
      "started_at": "2026-11-23T11:18:48.707315Z",
      "finished_at": "2026-11-23T11:18:49.115560Z",
      "changes_summary": "Order history: include line items for each order (mobile app v5)",
      "author": "m.okafor",
      "commit_sha": "61b9cff98fd3",
      "failure_reason": null
    },
    {
      "id": "d-407",
      "service": "api-gateway",
      "kind": "deploy",
      "version": "3.8.1",
      "previous_version": "3.8.0",
      "status": "succeeded",
      "started_at": "2026-11-23T11:18:36.723574Z",
      "finished_at": "2026-11-23T11:18:37.164289Z",
      "changes_summary": "Checkout banner copy update; bump httpx to 0.28.1",
      "author": "a.rahman",
      "commit_sha": "b4ade8ad59b9",
      "failure_reason": null
    }
  ]
}
```

**`database.get_connection_pool_status`**

```json
{
  "service": "orders-service"
}
```

→ structuredContent:

```json
{
  "tool": "get_connection_pool_status",
  "summary": "pools in use: orders-service 8/8",
  "truncated": false,
  "pools": [
    {
      "service": "orders-service",
      "max_connections": 8.0,
      "in_use": 8.0,
      "idle": 0.0,
      "pending_acquires": 11.0,
      "utilization": 1.0,
      "acquire_timeouts_per_s": 1.563835397232375,
      "acquire_p95_s": 2.392708333333333,
      "server_sessions": {
        "active": 14
      }
    }
  ]
}
```

**`database.get_slow_queries`**

```json
{
  "top_k": 2,
  "order_by": "mean_time"
}
```

→ structuredContent:

```json
{
  "tool": "get_slow_queries",
  "summary": "2 statements by mean_time; top: 'SELECT * FROM order_items WHERE order_id::text = $1' mean 517.38 ms",
  "truncated": false,
  "order_by": "mean_time",
  "stats_since": "2026-11-23T11:18:33Z",
  "queries": [
    {
      "queryid": "-8835910436758491810",
      "query": "SELECT * FROM order_items WHERE order_id::text = $1",
      "calls": 1015,
      "mean_ms": 517.38,
      "max_ms": 2765.56,
      "total_ms": 525136.3,
      "rows": 4939
    },
    {
      "queryid": "1865204274262027728",
      "query": "SELECT name, setting, COALESCE(unit, $1), short_desc, vartype FROM pg_settings WHERE vartype IN ($2, $3, $4) AND name != $5",
      "calls": 17,
      "mean_ms": 31.76,
      "max_ms": 196.23,
      "total_ms": 539.8,
      "rows": 4403
    }
  ]
}
```

**`infrastructure.get_dependency_status`**

```json
{}
```

→ structuredContent:

```json
{
  "tool": "get_dependency_status",
  "summary": "orders-service=down; postgres=degraded",
  "truncated": false,
  "dependencies": [
    {
      "name": "orders-service",
      "kind": "service",
      "used_by": [
        "api-gateway"
      ],
      "health": "down",
      "evidence": [
        "gateway->orders error ratio 76.0% (3.11 req/s)"
      ]
    },
    {
      "name": "postgres",
      "kind": "database",
      "used_by": [
        "api-gateway",
        "orders-service",
        "... 1 more"
      ],
      "health": "degraded",
      "evidence": [
        "pg_up=1.0",
        "pool acquire timeouts 1.64/s",
        "... 1 more"
      ]
    },
    "... 3 more"
  ]
}
```

**`incidents.search_incident_history`**

```json
{
  "query": "connection pool timeouts after deploy",
  "top_k": 2
}
```

→ structuredContent:

```json
{
  "tool": "search_incident_history",
  "summary": "2 matching incidents; best: INC-2026-031 'Order history N+1 query after orders-service 2.1.0 saturated the DB pool'",
  "truncated": false,
  "query": "connection pool timeouts after deploy",
  "hits": [
    {
      "incident": {
        "id": "INC-2026-031",
        "title": "Order history N+1 query after orders-service 2.1.0 saturated the DB pool",
        "date": "2026-03-14",
        "severity": "SEV2",
        "services": [
          "orders-service"
        ],
        "category": "bad_deploy",
        "duration_minutes": 47,
        "tags": [
          "n+1",
          "connection pool",
          "... 4 more"
        ]
      },
      "score": 13.981,
      "snippet": "orders-service 2.1.0 loaded order lines in a loop (one query per order). Under normal traffic the 8-connection pool stayed fully checked out and requests timed out waiting for connections ('timed out waiting for a database connection')."
    },
    {
      "incident": {
        "id": "INC-2026-063",
        "title": "Feature flag leaked database connections until the pool was exhausted",
        "date": "2026-06-17",
        "severity": "SEV2",
        "services": [
          "orders-service"
        ],
        "category": "config_change",
        "duration_minutes": 64,
        "tags": [
          "connection leak",
          "idle in transaction",
          "... 2 more"
        ]
      },
      "score": 9.201,
      "snippet": "Enabling an audit feature flag opened a second connection per lookup that was never released on one code path. Sessions piled up 'idle in transaction' and the pool emptied over ~2 minutes."
    }
  ]
}
```

**`logs.get_logs_by_trace_id`**

```json
{
  "trace_id": "not-a-trace"
}
```

→ error:

```text
Error executing tool get_logs_by_trace_id: 1 validation error for get_logs_by_trace_idArguments
trace_id
  String should match pattern '^[0-9a-f]{32}$' [type=string_pattern_mismatch, input_value='not-a-trace', input_type=str]
    For further information visit https://errors.pydantic.dev/2.14/v/string_pattern_mismatch
```

**`remediation.rollback_deployment_simulation`**

```json
{
  "service": "orders-service",
  "reason": "N+1 query shipped in d-408"
}
```

→ structuredContent:

```json
{
  "tool": "rollback_deployment_simulation",
  "summary": "approval required for rollback_deployment {'service': 'orders-service', 'reason': 'N+1 query shipped in d-408'}; request apr-645c30989aef",
  "truncated": false,
  "status": "approval_required",
  "action": "rollback_deployment",
  "params": {
    "service": "orders-service",
    "reason": "N+1 query shipped in d-408"
  },
  "simulated": true,
  "approval_request_id": "apr-645c30989aef",
  "approval_expires_at": "2026-11-23T11:35:02Z",
  "approved_by": null,
  "how_to_approve": "a human runs: python -m ai_sre.mcp_servers approve apr-645c30989aef --approver <name>; then call this tool again with the same parameters and approval_token",
  "result": null
}
```

**Human approval** (`POST /approvals/{id}/approve`, admin token):

```json
{
  "request": {
    "id": "apr-645c30989aef",
    "action": "rollback_deployment",
    "params": {
      "service": "orders-service",
      "reason": "N+1 query shipped in d-408"
    },
    "status": "approved",
    "requested_at": 1795432802.7004747,
    "expires_at": 1795433702.7004747,
    "approver": "alice",
    "decided_at": 1795432803.6592867
  },
  "approval_token": "eyJyaWQiOiJhcHItNjQ1YzMw..."
}
```

**`remediation.rollback_deployment_simulation`**

```json
{
  "service": "orders-service",
  "reason": "N+1 query shipped in d-408",
  "approval_token": "eyJyaWQiOiJhcHItNjQ1YzMw..."
}
```

→ structuredContent:

```json
{
  "tool": "rollback_deployment_simulation",
  "summary": "rollback_deployment executed (simulated) for {'service': 'orders-service', 'reason': 'N+1 query shipped in d-408'}",
  "truncated": false,
  "status": "executed",
  "action": "rollback_deployment",
  "params": {
    "service": "orders-service",
    "reason": "N+1 query shipped in d-408"
  },
  "simulated": true,
  "approval_request_id": "apr-645c30989aef",
  "approval_expires_at": null,
  "approved_by": "alice",
  "how_to_approve": null,
  "result": {
    "id": "d-409",
    "service": "orders-service",
    "kind": "rollback",
    "version": "2.3.1",
    "previous_version": "2.4.0",
    "status": "in_progress",
    "started_at": "2026-11-23T11:20:03.716353Z",
    "finished_at": null,
    "changes_summary": "Rollback 2.4.0 -> 2.3.1: N+1 query shipped in d-408",
    "author": "ai-sre (approved)",
    "commit_sha": "72a024fd7604",
    "failure_reason": null
  }
}
```

## 6. Tests

| Command | What | Time |
|---------|------|------|
| `uv run poe test-py` (includes `backend/tests/mcp/test_mcp_offline.py`) | In-memory MCP protocol: every server exposes exactly the required tools with descriptions, input/output schemas and annotations; invalid input rejected before any backend call (10 cases); backend outage, DB unreachable and timeout → structured errors; incidents tools; remediation disabled by default; approval required → approve → execute once → tampered and reused tokens refused; failed actions never reported as success; approval token unit tests; log templates; LogQL injection guard; result size cap | ~7 s |
| `uv run poe test-mcp` (`test_mcp_live.py`, needs `sim-up`) | **Real Streamable HTTP**: uvicorn hosts the app and the official MCP client connects with bearer tokens. Auth scopes (401/403); tools listed on all 8 servers; **every read tool** called against the live environment with results validated against its `outputSchema`; semantic checks on baseline data; a live scenario visible through the tools; rollback with human approval restoring the version; real restart (new process start time), scaling (4 consumers) and queue clearing | ~3 min |
