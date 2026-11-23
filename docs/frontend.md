# Frontend

> Status: Phase 6 (delivered 2026-11-23). Code: [`frontend/`](../frontend/).

The web console is a React 19 + TypeScript + Vite + Tailwind CSS 4 single-page app with a
dark, dense layout. Every number and every sentence on screen comes from the backend API. The
UI holds no fixtures, demo data or canned AI text; when a source is down it says so.

## Running it

```bash
# 1. MCP tool servers (remediation is off unless enabled)
AI_SRE_MCP_REMEDIATION_ENABLED=true uv run python -m ai_sre.mcp_servers serve   # :8300
# 2. API (uses a selector event loop on Windows for the Postgres checkpointer)
uv run python -m ai_sre.api                                                       # :8010
# 3. Frontend dev server (proxies /api to :8010)
npm --prefix frontend run dev                                                      # :5173
```

In Docker, nginx serves the built bundle and proxies `/api` with buffering off, so SSE
streams through unchanged (`deploy/docker/nginx.conf`).

## Screens

| Route | Screen | Backend endpoints |
|-------|--------|-------------------|
| `/` | **Overview**: KPI tiles, ask box, service health with golden signals, dependencies, error/latency charts, recent investigations and deployments, system status | `GET /overview`, `/metrics/series`, `/investigations`, `/system/status` |
| `/investigations` | **AI investigation interface**: question box with examples, searchable/filterable history | `POST /investigations`, `GET /investigations` |
| `/investigations/:id` | **Investigation workspace**: progress stepper, scope, leading hypothesis with confidence, plan, live activity feed, ranked hypotheses with rubric breakdown, evidence/tool history, incident timeline. **Report** tab: summary, root cause, alternatives, citations, remediation, verification, limitations. Follow-up questions | `GET /investigations/{id}`, `GET …/events` (SSE), `POST …/follow-ups`, `POST …/cancel` |
| `/investigations/:id/tools/:evidenceId` | **Tool execution details**: server, tool, arguments, status, duration, error, full structured response | `GET …/tool-calls/{evidence_id}` |
| `/approvals` | **Remediation approval**: every pending proposal with action, reason, risk, expected outcome, arguments and runbooks; approve/reject with confirmation | `GET /investigations`, `GET /investigations/{id}`, `POST …/approval` |
| `/services`, `/services/:name` | **Service health**: health table and dependencies; per-service signal tiles, 60-minute charts, container state, deployments, error/all logs | `GET /overview`, `GET /services/{name}` |
| `/metrics` | **Metrics explorer**: signal, up to 3 services, window 15 m–24 h, compare with 1 h / 1 d / 1 w earlier (dashed overlay), stats table. State lives in the URL | `GET /metrics/catalog`, `GET /metrics/series` |
| `/incidents`, `/incidents/:id` | **Incident history**: hybrid search over postmortems with service/severity/category filters, agent-investigation history, postmortem view with similar incidents | `GET /knowledge/incidents`, `GET /knowledge/incidents/{id}` |
| `/settings` | **Settings**: agent limits (validated, saved server-side), LLM provider, environment, MCP servers and telemetry integrations | `GET /settings`, `PUT /settings/agent` |

## Structure

```
src/
  app/              router, query client
  components/
    ui/             Button, Badge, Card, Input/Select/Textarea/Field, Tabs, Segmented, Modal,
                    Stat, StatusDot, ConfidenceMeter, JsonView, KeyValue,
                    Skeleton/LoadingBlock, EmptyState, ErrorState, QueryState
    charts/         TimeSeriesChart (Recharts; merges series, overlays shifted baselines)
    layout/         AppShell (sidebar, API indicator, pending-approval count), PageHeader
  features/
    investigations/ API hooks, SSE hook, workspace components, report, approval panel
    platform/       overview/services/metrics/logs hooks and tables
    knowledge/      incident search and settings hooks
    system/         system status panel
  lib/              api client, response types, formatting, status → tone mapping
  pages/            one component per route
  test/             render helpers, fetch router mock, EventSource double, fixtures
```

Server state lives in TanStack Query. Live views poll at 15 s (overview, services), 30 s
(series, deployments) or 5 s (the investigation list while anything is running).

## Live progress (SSE)

`useInvestigationEvents(id)` opens an `EventSource` on `/api/v1/investigations/{id}/events`.

- The browser reconnects by itself after a drop and sends `Last-Event-ID`; the server replays
  only the missed events. The hook also de-duplicates by event id.
- Connection state is shown in the header: *Live*, *Connection lost, reconnecting…*,
  *Finished* / *Paused*, or *Live updates unavailable (polling)* with a **Reconnect** button.
  While SSE is unavailable the page polls the investigation every 4 s.
- The server ends the stream with `event: end` when the run stops (finished, or waiting for
  approval). After an approval resumes the run, the page reopens the stream with
  `?after=<last id>`.
- Events drive the progress stepper and the activity feed directly. State-changing events
  (plan, tool call, hypotheses, root cause, proposals, report…) trigger a coalesced refetch
  of `GET /investigations/{id}`, so panels always render the persisted state.

## States

Every data panel goes through `QueryState`: skeleton while loading, `ErrorState` with the
problem title/detail and a **Retry** button on failure (network errors, 502 "a data source is
unavailable", 503 "service not ready"), an explicit empty state, and a "showing last known
data" banner when a background refresh fails. Partial overview data (one telemetry source
down) shows a warning instead of failing the page. Unknown investigations, services, tool calls
and incidents render not-found states.

## Remediation approval

Nothing executes without a human decision:

1. A proposal shows action, reason, risk, reversibility, expected outcome, arguments, runbook
   references and the diagnosis with its confidence.
2. The approver enters their name (remembered in `localStorage` only for convenience) and an
   optional comment.
3. **Approve…/Reject…** opens a confirmation dialog that requires ticking "I have reviewed…".
4. The request carries `confirm: true`; the API rejects decisions without it (422) and stale
   ones (409). Errors are shown in the dialog.
5. After approval the run resumes. Execution, verification (before/after signals) and the
   final report stream into the workspace.

## Tests

`npm --prefix frontend test` (Vitest + Testing Library, jsdom). 48 tests cover:

- the SSE hook: ordering, de-duplication, reconnecting/failed states, `end`, resume with `after`;
- the investigation workspace: live events and refetch, polling fallback and reconnect, the
  approval confirmation flow and request body, API conflict errors, the completed report
  (confidence, alternatives, citations, verification), follow-ups, not-found;
- every other page against a routed `fetch` mock with real response shapes: overview
  (including partial data and error → retry → recovery), new investigation, approvals, tool
  call details (success, failure, missing), service detail and unknown service, metrics
  explorer (query params and baseline overlay), incident search/filters/detail and KB-not-ready,
  settings (validation and save);
- shared UI (error/query states, confidence meter, modal) and formatting helpers.

Quality gates: `npm run lint` (typescript-eslint strict-type-checked + React hooks/compiler
rules), `npm run typecheck`, `npm run format:check`, `npm run build`.

## Not in this phase

Authentication and roles (Phase 7 in the roadmap), generated OpenAPI types, Playwright
end-to-end tests and a light theme.
