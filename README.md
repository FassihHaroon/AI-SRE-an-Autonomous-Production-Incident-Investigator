# AI SRE: Autonomous Production Incident Investigator

AI SRE investigates production incidents. You ask a question in plain language ("Why has
API latency increased and why are orders failing in the last 15 minutes?"). It gathers
evidence from logs, metrics, deployments, infrastructure, database health, past incidents and
runbooks, then returns an evidence-cited root-cause report with a computed confidence. It can
also propose remediation that runs only after human approval.

Everything runs locally with Docker Compose against a **deterministic simulated production
environment**. The only external dependency is an LLM API (the Gemini free tier during
development).

> **Status: Phase 2 of 11 complete.** Phase 1 delivered the platform skeleton (API, database,
> cache, web UI, health checks, logging, LLM provider layer, tests, CI). Phase 2 adds a
> **simulated production environment**: a small shop under synthetic load with Prometheus, Loki,
> Grafana, deployment records and a reversible fault-injection system with 8 incident scenarios
> ([docs/simulator.md](docs/simulator.md)). The investigation agent arrives in Phase 3; see
> [docs/implementation-roadmap.md](docs/implementation-roadmap.md).

## Documentation

| Document | What's in it |
|----------|--------------|
| [docs/simulator.md](docs/simulator.md) | The simulated production environment: topology, telemetry, fault injection, scenarios, reset |
| [docs/scenarios/](docs/scenarios/) | One page per incident scenario: timeline, expected signals, root cause, clearing |
| [docs/architecture.md](docs/architecture.md) | Components, data model, API, observability, deployment, MVP vs. future |
| [docs/agent-design.md](docs/agent-design.md) | LangGraph workflow, state, nodes, MCP tools, confidence scoring, failure modes |
| [docs/security-design.md](docs/security-design.md) | Threat model, approval tokens, RBAC, redaction, safety tests |
| [docs/evaluation-plan.md](docs/evaluation-plan.md) | Scenarios, metrics, gates, record/replay |
| [docs/implementation-roadmap.md](docs/implementation-roadmap.md) | Phases, acceptance criteria, risks |

---

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Docker Desktop | 24+ with Compose v2 | WSL2 backend on Windows |
| Python | 3.13 | only for running things outside Docker |
| [uv](https://docs.astral.sh/uv/) | 0.9+ | `pip install uv` (or see the uv docs) |
| Node.js | 22.12+ | only for frontend development outside Docker |

On Windows, keep the repository **outside OneDrive** (e.g. `C:\dev\ai-sre`). Syncing
`.venv` and `node_modules` causes file locks.

## Quick start (Docker)

```bash
cp .env.example .env          # PowerShell: Copy-Item .env.example .env
# Optional now, needed from Phase 3: put your Gemini key in .env
#   AI_SRE_GEMINI_API_KEY=...   (free key: https://aistudio.google.com/apikey)
docker compose up -d --build --wait
```

| What | URL |
|------|-----|
| Web UI | <http://127.0.0.1:8090> |
| API docs (OpenAPI) | <http://127.0.0.1:8010/docs> |
| Liveness | <http://127.0.0.1:8010/healthz> |
| Readiness (Postgres + Redis) | <http://127.0.0.1:8010/readyz> |

The web UI's overview page shows live system status (database, cache, LLM key configured).

```bash
docker compose ps             # all services "healthy"; migrate "Exited (0)"
docker compose logs -f api    # structured JSON logs
docker compose down           # stop (data kept in named volumes)
docker compose down -v        # stop and delete all data
```

Ports bind to `127.0.0.1` only. If 5432, 6379, 8010 or 8090 are taken on your machine,
change `POSTGRES_HOST_PORT`, `REDIS_HOST_PORT`, `API_HOST_PORT` or `WEB_HOST_PORT` in `.env`.
The simulated environment uses 3000, 3100, 8200–8202, 8210 and 9090 (overridable with `SIM_*_PORT`).

### What starts

| Service | Purpose | Health check |
|---------|---------|--------------|
| `postgres` | PostgreSQL 16 + pgvector | `pg_isready` |
| `redis` | Queue, live events, caches (later phases) | `redis-cli ping` |
| `migrate` | One-shot `alembic upgrade head`, then exits | exit code 0 |
| `api` | FastAPI backend | `GET /healthz` |
| `web` | React app on unprivileged nginx; proxies `/api` to the backend | `GET /healthz` |

## Simulated production environment

A separate compose project (`docker-compose.sim.yml`) runs a small **simulated** shop (api-gateway,
orders-service, fulfilment-worker, PostgreSQL, Redis) under synthetic traffic, with Prometheus,
Loki and Grafana, a deployment history, and a fault-injection control plane. It is the "production"
that AI SRE will investigate. Full guide: [docs/simulator.md](docs/simulator.md).

```bash
uv run poe sim-up                              # build + start all 17 containers, wait for healthy
uv run simctl scenarios                        # 8 predefined incidents
uv run simctl trigger orders-n-plus-one-deploy --speed 20
#   Grafana http://127.0.0.1:3000 · Prometheus http://127.0.0.1:9090 · Loki http://127.0.0.1:3100
#   Deployments http://127.0.0.1:8202/api/v1/deployments
uv run simctl clear                            # reverse it
uv run poe sim-reset                           # back to the deterministic baseline
uv run poe test-sim                            # live tests: every scenario + fault (~20 min)
uv run poe sim-down
```

| Scenario | What breaks |
|----------|-------------|
| `orders-n-plus-one-deploy` | Deploy adds an N+1, unindexed query; DB pool saturates (red-herring deploy before it) |
| `redis-outage` | Redis unreachable; catalog slower, carts fail, paid orders not queued |
| `worker-deploy-backlog` | Worker deploy breaks shipping labels; fulfilment queue accumulates |
| `payments-provider-errors` | Third-party payments provider returns 503s |
| `db-connection-leak` | Feature flag leaks DB connections until the pool is exhausted |
| `gateway-failed-canary` | Failed rollout leaves a broken canary serving 25% of traffic |
| `gateway-memory-leak` | Deploy adds an unbounded cache; memory grows until OOM restarts |
| `db-network-latency` | +120 ms on the network path to PostgreSQL |

Single faults can be injected too: `uv run simctl fault-types`, then for example
`uv run simctl inject api_latency --target api-gateway --param delay_ms=400`.

## Local development (without Docker for the app)

Run only the data stores in Docker and the app on your machine, with hot reload:

```bash
uv sync                                  # Python deps (creates .venv)
npm --prefix frontend ci                 # frontend deps

docker compose up -d postgres redis      # data stores only
uv run poe migrate                       # apply migrations
uv run poe api                           # API on http://127.0.0.1:8010 (reload)
uv run poe web                           # UI on http://127.0.0.1:5173 (proxies /api → 8010)
```

If the Docker `api`/`web` containers are running, stop them first
(`docker compose stop api web`), because they use the same host ports.

Use `127.0.0.1` rather than `localhost` in URLs. On Windows, `localhost` tries IPv6 first and
each connection to these services stalls about 2 seconds.

### Tasks

All tasks run through [poethepoet](https://poethepoet.natn.io/) (installed with the dev
dependencies), so they work the same on Windows, macOS and Linux:

| Command | What it does |
|---------|--------------|
| `uv run poe lint` | ruff + format check + mypy (strict); ESLint + `tsc` + Prettier check |
| `uv run poe fmt` | Format Python (ruff) and frontend (Prettier) |
| `uv run poe test` | Python unit tests (pytest) + frontend tests (Vitest) |
| `uv run poe test-integration` | Tests against real Postgres/Redis (needs `docker compose up -d postgres redis` + `poe migrate`) |
| `uv run poe check` | `lint` + `test` (what CI runs, minus Docker) |
| `uv run poe api` / `web` | Dev servers |
| `uv run poe migrate` | Apply database migrations |
| `uv run poe llm-smoke` | Send one prompt to the configured LLM (verifies your Gemini key; uses one request of quota) |
| `uv run poe up` / `down` / `logs` / `ps` | Docker Compose shortcuts |
| `uv run poe sim-up` / `sim-down` / `sim-logs` / `sim-ps` | Same for the simulated environment |
| `uv run poe sim-reset` / `sim-reset-hard` | Reset the simulated environment to baseline / recreate it from scratch |
| `uv run poe test-sim` | Live tests against the running simulated environment (~20 min) |
| `uv run simctl …` | Trigger, inspect and clear simulated incidents |
| `uv run poe --help` | List all tasks |

Optional git hooks: `uv run pre-commit install`.

## Configuration

All backend settings are environment variables with the prefix `AI_SRE_`, loaded by
[backend/src/ai_sre/config.py](backend/src/ai_sre/config.py). On the host they come from
`.env`; in Docker, from `docker-compose.yml`. See [.env.example](.env.example) for every
variable.

| Variable | Default | Notes |
|----------|---------|-------|
| `AI_SRE_ENVIRONMENT` | `development` | `development` \| `test` \| `production` (production refuses default DB credentials) |
| `AI_SRE_LOG_LEVEL` | `INFO` | |
| `AI_SRE_LOG_FORMAT` | console in dev, JSON otherwise | Docker always uses JSON |
| `AI_SRE_DATABASE_URL` | `postgresql+asyncpg://…@127.0.0.1:5432/aisre` | |
| `AI_SRE_REDIS_URL` | `redis://127.0.0.1:6379/0` | |
| `AI_SRE_LLM_PROVIDER` | `gemini` | `gemini` \| `scripted` (tests) |
| `AI_SRE_GEMINI_API_KEY` | — | **Secret.** Free key from Google AI Studio |
| `AI_SRE_GEMINI_MODEL` | `gemini-3.5-flash-lite` | Check your per-model free quota in AI Studio |

**Secrets:** `.env` is git-ignored and excluded from Docker build contexts. Secrets are typed
`SecretStr` and never logged; health endpoints only report whether the LLM key is set. The
Gemini **free tier may use prompts to improve Google's products**: only synthetic data goes
through it.

## Repository layout

```
backend/            FastAPI API (later: agent worker), package ai_sre
  src/ai_sre/
    config.py         settings
    main.py           app factory
    api/              routers, dependencies, RFC 9457 errors
    services/         business logic (health checks now)
    db/               Postgres engine + ORM base, Redis client, Alembic migrations
    integrations/llm/ LLM provider interface, Gemini + scripted providers
    observability/    structlog setup, request-id/logging middleware
    agent/ tools/ knowledge/ remediation/ auth/ worker/ events/   (later phases)
  tests/            unit tests; tests/integration needs Postgres + Redis
simulator/          simulated production environment (Phase 2), package ai_sre_simulator
  src/ai_sre_simulator/
    shop/             api-gateway, orders-service, fulfilment-worker, payments-provider, seed
    deployer/         simulated CD system (deployment records, rollouts)
    chaos/            fault-injection control plane, scenario engine, toxiproxy client
    loadgen/          seeded synthetic traffic
    common/           logging, metrics, HTTP middleware, DB/Redis instrumentation, faults
    cli.py            simctl
    testing.py        Prometheus/Loki/chaos clients for live tests
  scenarios/          scenario timelines (YAML)
  tests/              unit tests; tests/live needs the running simulated environment
evals/              evaluation harness, ground truth, suites (Phase 4)
knowledge/          synthetic postmortems and runbooks for RAG (Phase 6)
frontend/           React + TypeScript + Vite + Tailwind
  src/app/            router, query client
  src/components/ui/  reusable primitives (Button, Card, Badge, Spinner)
  src/components/layout/  AppShell, PageHeader
  src/features/<x>/   per-feature API calls, hooks and components
  src/pages/          route-level screens
deploy/docker/      Dockerfiles + nginx config
deploy/sim/         Prometheus (+ alert rules), Loki, Alloy, Grafana, Toxiproxy, Postgres config for the simulator
scripts/            helper scripts
docs/               design documents
.github/workflows/  CI
```

## Health endpoints

| Endpoint | Meaning | Used by |
|----------|---------|---------|
| `GET /healthz` (API) | Process is alive. No dependency checks | Docker health check |
| `GET /readyz` (API) | Postgres and Redis reachable within 2 s; **503** otherwise | Orchestration, monitoring |
| `GET /api/v1/system/status` | Readiness + version + LLM configuration; always 200 | Web UI status panel |
| `GET /healthz` (web) | nginx is serving | Docker health check |

Every response carries an `X-Request-ID` header, which also appears on every log line for
that request.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `ports are not available` on `docker compose up` | Another process uses the port. Change the `*_HOST_PORT` value in `.env`, or stop the other process |
| `/readyz` returns 503 | `docker compose ps`: is `postgres`/`redis` healthy? Check `detail` in the response |
| Host commands slow (~2 s per connection) | Use `127.0.0.1`, not `localhost`, in `.env` URLs |
| `POSTGRES_PASSWORD` error from compose | You have no `.env`: `cp .env.example .env` |
| Changed `POSTGRES_PASSWORD` but the old one still works | The data volume was initialised with the old one: `docker compose down -v` (deletes data) |
| `uv` not found after `pip install uv` | Add Python's user Scripts directory to `PATH` (shown by `python -m site --user-base`) |
