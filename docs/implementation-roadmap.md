# AI SRE — Implementation Roadmap

> Status: **Approved** (2026-11-23; decisions recorded inline) · Last updated: 2026-11-23
> Audience: the developer building this, one person. Estimates are in **focused dev-days**
> (about 5–6 productive hours). They are planning estimates, not commitments. Re-estimate
> after Phase 3, when the real velocity is known.

---

## 1. Sequencing strategy

1. **Build the world before the agent.** The simulator and tool contracts come first.
   Without deterministic data the agent cannot be developed or measured.
2. **Walking skeleton early.** A thin end-to-end path from question to report, from the CLI,
   by the end of Phase 3.
3. **Measure before tuning.** The eval harness (Phase 4) comes before the reasoning-quality
   work (Phase 5).
4. **API and UI after the core works.** The UI is a projection of persisted state. Building
   it on an unstable core wastes effort.
5. **Every phase ends green**: CI passes, docs updated, demo-able.

| Phase | Track | Focused days | Cumulative |
|-------|-------|-------------:|-----------:|
| P1 Foundations | Core | 3 | 3 |
| P2 Simulated production environment | Core | 8 | 11 |
| P3 MCP servers + agent core (walking skeleton) | Core | 11 | 22 |
| P4 Evaluation harness v1 | Core | 4 | 26 |
| P5 Reasoning quality | Core | 7 | 33 |
| P6 Knowledge / RAG | Core | 5 | 38 |
| P7 API, worker, auth, SSE | Product | 7 | 45 |
| P8 Frontend | Product | 10 | 55 |
| P9 Remediation + approval | Product | 6 | 61 |
| P10 Observability | Ops | 4 | 65 |
| P11 Hardening + release | Ops | 6 | 71 |

**Total ≈ 71 focused days** (68 originally; +3 when the MCP servers moved into P3).
That is roughly 14–15 weeks full-time or 5–6 months part-time,
plus about 20% buffer for the unknowns listed in § 4.

### Milestones

| Milestone | After | Demo |
|-----------|-------|------|
| **M1 — It reasons** | P4 | CLI: ask the flagship question against the simulator; get a cited report; eval baseline numbers exist. |
| **M2 — It's grounded** | P6 | Re-plan loop, computed confidence, validated citations, RAG; dev-suite numbers improve over the M1 baseline. |
| **M3 — It's a product** | P8 | Log in, start an investigation, watch it live, read the report and timeline in the browser. |
| **M4 — MVP** | P11 | Approve and run a simulated remediation, verify recovery, see traces/dashboards, all from `docker compose up`; holdout targets met; release published. |

---

## 2. Phases

Each phase lists **scope**, **out of scope**, **deliverables** and **acceptance criteria**.
Acceptance criteria are written to be checkable by a test, a command or a demo.

### Phase 1 — Foundations (3 days) — ✅ delivered 2026-11-23

**Scope (as delivered)**
- Repo moved out of OneDrive to `C:\dev\ai-sre`. `.gitattributes` (LF), `.gitignore`,
  `.editorconfig`, `.env.example`, pre-commit (ruff, private-key detection, EOL fixer).
- uv workspace with `backend`, `simulator`, `evals` packages; Python 3.13; ruff + mypy
  (strict).
- Backend skeleton: typed settings (`pydantic-settings`), structlog JSON/console logging,
  request-id middleware, RFC 9457 errors, `/healthz`, `/readyz`, `/api/v1/system/status`.
- LLM provider abstraction (`integrations/llm`) with Gemini (dev default) and scripted
  (tests) providers + `poe llm-smoke`.
- Alembic (async) with the first migration enabling `vector`.
- Frontend skeleton: Vite + React 19 + TS (strict) + Tailwind v4 + TanStack Query + React
  Router, reusable `components/ui` primitives, system status page, Vitest, ESLint, Prettier.
- Docker: backend image (uv, non-root), web image (unprivileged nginx with SPA fallback,
  `/api` proxy, CSP), compose with Postgres, Redis, one-shot `migrate`, API, web, all with
  health checks.
- Task runner: **poethepoet** (`uv run poe <task>`), chosen because make/just are not installed
  on the Windows dev machine and poe needs nothing extra.
- `ci.yml`: Python lint/types/unit + integration tests, frontend checks + build, compose
  build.
- Deferred to Phase 11 hardening: gitleaks hook (needs Go toolchain), ADR-001.

**Acceptance criteria**
- [x] `docker compose up` → `GET /readyz` returns 200 with DB and Redis checks passing.
- [x] `uv run poe test` passes locally. (CI runs on the first push to GitHub.)
- [x] `alembic upgrade head` succeeds on an empty database; `pg_extension` includes `vector`
      (integration test).
- [x] Stopping Redis turns `/readyz` into 503 while `/healthz` stays 200.

---

### Phase 2 — Simulated production environment (8 days) — ✅ delivered 2026-11-23

**Changed from the approved plan (2026-11-23, project lead's request).** Instead of a seeded,
compute-on-query telemetry engine behind MCP servers, Phase 2 delivers a **live simulated
production environment** that the agent will investigate through the interfaces a real SRE uses
(Prometheus, Loki, a deployments API). The MCP tool servers move to Phase 3, where they wrap these
APIs. Reproducibility comes from deterministic scenarios plus recorded tool responses (Phase 4
cassettes) instead of byte-identical generated telemetry. Details: [simulator.md](simulator.md);
decision records in architecture.md A6 and § 15.

**Scope (as delivered)**
- Shop services (one image, package `ai_sre_simulator`): `api-gateway`, `orders-service`,
  `fulfilment-worker` (Redis queue, retries, dead-letter), a third-party `payments-provider`,
  PostgreSQL 16 (`pg_stat_statements`, slow-query log, deterministic seed of 120k orders / 600k
  items), Redis 7, and a seeded open-loop load generator.
- Telemetry: Prometheus metrics for requests/status/latency per endpoint, DB query latency, pool
  utilisation, Redis latency, queue depth/age, worker results/failures, CPU/memory, build info;
  postgres- and redis-exporters; 11 alert rules. Structured JSON logs (timestamp, severity,
  service, environment, version, trace id, request id, endpoint, message, error) shipped by Alloy
  to Loki. W3C trace context propagated gateway → orders → worker. Grafana dashboard.
- `deployer` (simulated CD): deployment records (id, service, version, previous version, kind,
  status, timestamps, changes summary, author, commit), rollouts that succeed or fail, baseline
  history `d-401…d-406`.
- `chaos` control plane + `simctl` CLI: 12 reversible fault types (in-app, Toxiproxy network
  faults, deployment-borne bugs, traffic), 8 scenario timelines (A–E from the brief plus failed
  canary, memory leak, DB network latency), clear/reset, epoch-based determinism.
- Scenario docs (`docs/scenarios/*.md`, one per scenario), `scripts/sim_reset.py`, poe tasks.
- Monitoring, control plane, load generator and third party are separated: fault definitions
  and build flags never appear in metrics, logs or public APIs.

**Moved to Phase 3:** MCP servers (telemetry, changes, infrastructure, database) as adapters over
Prometheus/Loki/deployer, with the response envelope, 4 KB cap and server-side summarisation.
Remediation `list_actions`/`dry_run` moves with them.

**Acceptance criteria**
- [ ] `uv run poe sim-up` starts all 17 containers healthy on a clean machine; seed < 30 s.
- [ ] Every required telemetry signal is exported, every series and log line is labelled
      simulated, route labels are templates (live test `test_telemetry.py`).
- [ ] A trace id from one order appears in api-gateway, orders-service and fulfilment-worker
      logs.
- [ ] Each of the 8 scenarios produces its documented metric, log and deployment signals, and
      `clear` returns the environment to baseline (live test `test_scenarios.py`).
- [ ] Each fault primitive is observable while active and recovers after removal
      (`test_faults.py`).
- [ ] After `reset`, the same scenario yields identical run/deployment/fault records
      (`test_determinism_and_reset.py`).
- [ ] Hidden components (chaos, toxiproxy, loadgen, payments-provider) are absent from
      Prometheus targets and Loki streams.
- [ ] Unit tests, ruff and mypy (strict) pass; CI validates and builds the simulator image.

---

### Phase 3 — MCP servers + agent core: walking skeleton (11 days)

**Added from Phase 2:** MCP servers over the simulated environment's monitoring APIs (telemetry =
Prometheus + Loki, changes = deployer, infrastructure = Prometheus up/alerts/process metrics,
database = postgres-exporter + slow-query log) with the common envelope, 4 KB cap, summarisation,
bearer auth and contract tests that reuse `ai_sre_simulator.testing`. Estimate +3 days.


**Scope**
- `agent/llm.py`: provider abstraction with `anthropic` (official SDK), `scripted` and
  `replay` modes; native structured outputs; per-node model/effort config; token/cost
  accounting; refusal + `max_tokens` handling.
- Tool Gateway: MCP client, context injection, allow-lists, timeouts/retries, size cap,
  redaction (C4 patterns), evidence creation with `ev_*` short ids, persistence, per-run
  budget counters.
- DB models + migrations for investigations, plan_steps, tool_calls, evidence,
  investigation_events.
- Linear graph: `intake → context_snapshot → planner → step_executor (Send fan-out) →
  synthesize_report → finalize`, with `AsyncPostgresSaver`.
- Basic citation filtering in `step_executor` (drop unknown ids).
- CLI: `uv run ai-sre investigate --scenario orders-n-plus-one-deploy "Why …?"` prints plan,
  tool calls and report.
- Structured JSON logging with `investigation_id`.

**Out of scope:** correlate/analyze/re-plan, confidence rubric, RAG, API, UI.

**Acceptance criteria**
- [ ] CLI run on the flagship scenario completes in < 3 min and produces a report whose
      primary hypothesis is `bad_deploy / orders-service` in at least 2 of 3 runs (manual
      check; the harness formalises this in P4).
- [ ] Every evidence id cited in the report exists in the `evidence` table.
- [ ] The run is fully reproducible in `replay` mode after `--record`, with zero network
      calls (test with network disabled).
- [ ] Killing the process mid-run and re-running with the same thread id resumes from the last
      checkpoint without duplicate `tool_calls` rows.
- [ ] Budget exhaustion (set tool cap = 3) yields a completed, partial report with a
      `budget_exhausted` limitation, not a crash.
- [ ] Graph tests with the `scripted` LLM cover: happy path, step failure, structured-output
      repair, refusal handling.
- [ ] Static test: no LLM node has remediation tools bound.

---

### Phase 4 — Evaluation harness v1 (4 days)

**Scope**
- `evals` package: ground-truth schema, suites (`smoke`, `dev`), runner (in-process, parallel,
  repeats), deterministic metrics (RCA@1/@3, trigger, key-evidence recall, citation validity,
  efficiency), JSON + HTML report, `compare` command, cassette recording.
- Ground truth for scenarios 1–4; author scenarios 5–8 (simulator + ground truth).
- `eval_runs` / `eval_results` tables.
- CI: smoke suite in `replay` mode on every PR. `eval.yml` workflow (manual + nightly, skips
  without an API key).
- **Baseline run** of `dev` (N = 3) recorded in `docs/eval-results/`.

**Acceptance criteria**
- [ ] `ai-sre-eval run --suite dev --repeats 3` completes and writes a report with all
      § 3.1–3.2 and § 3.6 metrics from evaluation-plan.md.
- [ ] `ai-sre-eval compare` shows per-scenario flips between two runs.
- [ ] Smoke suite runs in CI in < 5 min with no API key.
- [ ] A deliberately broken prompt (e.g. removing the citation instruction) makes the smoke
      suite fail. This proves the gate has teeth.
- [ ] Baseline numbers and median cost per investigation are documented; the cost target is
      set from them.

---

### Phase 5 — Reasoning quality (7 days)

**Scope**
- Nodes: `correlate` (features + incident timeline), `analyze`, `sufficiency_gate` with
  re-plan loop, `score_confidence` (rubric v1), `validate_report` with one repair pass, and
  the system-generated limitations.
- `hypotheses`, `hypothesis_evidence`, `timeline_events`, `reports` tables.
- Report schema complete (causal chain, impact, recommendations, limitations, follow-ups).
- Scenarios 9–12 authored (holdout). Timeline and calibration metrics added to the harness.
- Tuning loop on `dev` per evaluation-plan § 5.

**Acceptance criteria**
- [ ] Property tests: score monotonic in corroboration; temporal violation always lowers
      score; competing-explanation cap applied when the margin is < 0.10.
- [ ] Final reports have 0 uncited claims and 0 unknown entities across the `dev` suite (gate).
- [ ] `noisy-no-incident` yields band `low`/`insufficient` in ≥ 2 of 3 repeats.
- [ ] Red-herring rejection ≥ 0.8 on `dev`.
- [ ] Dev RCA@1 improves on the P4 baseline by more than 1 std, or the failure analysis is
      documented with a plan.
- [ ] Re-plan loop is exercised (iteration 2 occurs) in at least one dev scenario and never
      exceeds the iteration cap.

---

### Phase 6 — Knowledge and RAG (5 days)

**Scope**
- Corpus: about 30 synthetic postmortems + about 15 runbooks (Markdown, front-matter), some
  matching dev/holdout scenarios and some deliberately not (`history_available` flag in
  ground truth).
- Ingestion CLI (chunk, embed with fastembed `bge-small-en-v1.5`, idempotent upsert), HNSW +
  GIN indexes, hybrid retrieval with RRF.
- `retrieve_knowledge` node, `knowledge` step domain, runbook-linked recommendations.
- Retrieval eval set (~40 labelled queries) + L2 metrics in CI.
- RAG ablation run.

**Acceptance criteria**
- [ ] Re-running ingestion with unchanged files performs 0 writes (content-hash idempotency).
- [ ] Retrieval recall@5 ≥ 0.85; hybrid ≥ max(vector-only, FTS-only) on the labelled set.
- [ ] Embedding model is baked into the image; ingestion works with networking disabled.
- [ ] RAG ablation documented: effect on RCA@1 split by `history_available`.
- [ ] Recommendations reference runbook slugs that exist.

---

### Phase 7 — API, worker, auth, live events (7 days)

**Scope**
- FastAPI routers per architecture § 7 (auth, investigations, knowledge, scenarios, admin,
  evals read-only, health). Remediation endpoints are stubbed until P9.
- Users, refresh tokens, argon2id, JWT, rotation with reuse detection, RBAC dependencies,
  RBAC matrix test.
- arq worker: `run_investigation`, `resume_investigation`, `reindex_knowledge`. Startup sweep
  for orphaned runs. Cooperative cancellation.
- Event publishing: Redis Streams + `investigation_events`; SSE endpoint with `Last-Event-ID`
  replay and heartbeats.
- Rate limits + concurrency caps (Redis). Audit log with hash chain + append-only trigger.
- `migrate` one-shot service; seed demo users from `.env`.
- OpenAPI export script; compose smoke test in CI (replay mode through the API).

**Acceptance criteria**
- [ ] Full flow via HTTP: login → create investigation → SSE stream shows `plan_created`,
      `tool_call_*`, `report_ready` → `GET /report` returns the validated report.
- [ ] Disconnecting the SSE client and reconnecting with `Last-Event-ID` yields no gaps or
      duplicates.
- [ ] Killing the worker container mid-run → a restarted worker completes the investigation
      (orphan sweep + checkpoint resume).
- [ ] RBAC matrix test covers 100% of routes × 4 roles.
- [ ] Refresh-token reuse revokes the token family (test).
- [ ] 3rd concurrent investigation by the same user → 429.
- [ ] `verify-audit` passes after the smoke test; manual `UPDATE audit_log` is rejected.
- [ ] CI compose smoke test passes.

---

### Phase 8 — Frontend (10 days)

**Scope**
- Vite + React + TS + Tailwind + TanStack Query + Router; generated API types; auth flow
  (in-memory access token, silent refresh).
- Screens 1–4 fully (login, list, new, **investigation detail** with Live / Report / Timeline /
  Evidence / Tool calls tabs). Screens 6–8 basic (knowledge browse, eval results, admin users
  + audit). Screen 5 (approvals) and the Actions tab are wired in P9.
- Safe rendering (Markdown without raw HTML, logs as text), CSP via nginx.
- Vitest for components/hooks; Playwright for the critical path + XSS check.

**Acceptance criteria**
- [ ] Playwright: login → new investigation on flagship scenario → live progress visible →
      report shows primary hypothesis, confidence band with breakdown, cited evidence chips
      that open the evidence row → timeline shows both lanes.
- [ ] XSS payload from scenario A5 renders as inert text (Playwright assertion).
- [ ] CI fails if generated API types drift from the backend OpenAPI.
- [ ] Viewer role sees no create/approve controls; the API also rejects these calls.
- [ ] Lighthouse accessibility ≥ 90 on the investigation detail page.

---

### Phase 9 — Remediation and human approval (6 days)

**Scope**
- Remediation catalog + policy, `propose_remediation`, `approval_gate` (interrupt),
  `route_decisions`, `execute_remediation`, `verify_remediation` nodes.
- Simulator: `remediation.execute` with token verification, `jti` single-use, idempotency,
  state mutation per session, settle time, `stale` detection.
- API: approve/reject/close endpoints, token minting, resume job, expiry sweep, kill switch.
- UI: Actions tab (dry-run diff, risk, approve/reject with comment), approvals inbox,
  verification result display.
- Eval harness: auto-approval policy, remediation metrics, adversarial suite A1–A8.

**Acceptance criteria**
- [ ] Flagship scenario end to end in the UI: rollback proposed (high risk) → requester
      cannot self-approve → second user approves → executed → verification shows recovery →
      status `completed`.
- [ ] Security tests C2: missing / expired / tampered / reused token all refused and audited.
- [ ] Approving after the simulator state changed (a scripted extra deploy) → `stale`, no
      execution.
- [ ] Worker crash between execute and checkpoint → no double execution (idempotency test).
- [ ] `REMEDIATION_ENABLED=false` → no proposals; execute refused.
- [ ] Adversarial suite: 0 unjustified proposals, 0 bypasses, 0 secret leakage.
- [ ] `inventory-memory-leak`: if `restart_service` is approved, verification reports
      temporary improvement and the leak returns. The report must not call this "resolved".

---

### Phase 10 — Observability of AI execution (4 days)

**Scope**
- OTel SDK in api/worker/simulator; spans per architecture § 8.1 with GenAI attributes;
  `traceparent` propagated over MCP HTTP; trace id on `tool_calls`; "Open trace" link in UI.
- Prometheus metrics per § 8.2; `/metrics` on all three services.
- Compose `observability` profile: collector, Jaeger, Prometheus, Grafana; three provisioned
  dashboards + alert rules.

**Acceptance criteria**
- [ ] One investigation produces one trace in Jaeger containing graph-node, LLM and tool
      spans, including simulator-side MCP handler spans (cross-service propagation works).
- [ ] Grafana dashboards load with data after one investigation, with no manual setup.
- [ ] Token and cost metrics agree with the `investigations` table totals (±1%).
- [ ] No prompt/completion text in exported spans unless `OTEL_CAPTURE_CONTENT=true` (test).
- [ ] Metric label cardinality check: no investigation/user ids in labels.

---

### Phase 11 — Hardening, CI/CD release, documentation (6 days)

**Scope**
- Security checklist (security-design § 10) fully passing; Trivy, pip-audit, npm audit,
  Dependabot, SHA-pinned actions, minimal workflow permissions, non-root containers.
- `release.yml`: multi-arch images to GHCR, digest-pinned `docker-compose.release.yml`, SBOM.
- Holdout evaluation (N = 5) + adversarial run, written up in
  `docs/eval-results/mvp.md`.
- Docs: README (quick start in ≤ 5 commands, architecture diagram, demo GIF), operations guide
  (config reference, budgets, kill switch, backup), "adding a scenario" guide, "adding a
  tool" guide, ADRs updated, known limitations.
- Data retention cleanup job.

**Acceptance criteria — MVP definition of done**
- [ ] Fresh clone on a clean machine → `cp .env.example .env` (add key or choose `ollama`) →
      `docker compose up` → working demo in < 10 min (excluding image download).
- [ ] Holdout quality targets in evaluation-plan § 4.4 met, or each miss is documented with
      analysis and accepted explicitly.
- [ ] All hard gates green in CI and nightly eval.
- [ ] Tagging `v0.1.0` publishes images + release compose file + SBOM.
- [ ] A reader new to the repo can add a new scenario by following the guide alone.

---

## 3. Scope-control levers (if time runs short)

Cut in this order. Each still leaves a coherent MVP:

1. Screens 6–8 reduced to read-only lists (knowledge, evals, admin).
2. `logs.compare_windows`, `db.locks` and two remediation actions (`flush_cache`,
   `adjust_db_pool`) dropped.
3. Grafana dashboards reduced to one; alert rules dropped.
4. Re-plan loop fixed at 1 iteration (gate always goes to report). Keeps everything else.
5. Holdout reduced to 3 scenarios.

**Never cut:** citation validation, computed confidence, approval tokens, the eval harness,
replay-mode CI.

## 4. Key risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Simulator realism too low → agent "solves" toy problems | Medium | High | Red herrings, noise, gradual onsets, multi-cause and missing-data scenarios. Review scenarios against real postmortem patterns. |
| Simulator scope creep (building a "real" world) | High | Medium | Generators stay statistical; add realism only when an eval failure shows it is needed. Timebox P2. |
| LLM cost during development | Medium | Medium | Replay mode, smoke suite on cassettes, per-run caps, effort tuning, prompt caching. |
| Eval noise hides real changes | High | Medium | Repeats, std reporting, one change at a time, `compare` tooling. |
| LangGraph / LangChain API churn | Medium | Medium | Pin versions. Thin wrappers (our own LLM layer and gateway) limit blast radius. |
| Structured-output incompatibilities in third-party wrappers | Medium | Low | Official SDK in the LLM layer for the default provider. |
| Local model quality (if `ollama` is the only option) | High | High | Treat it as a "runs offline" mode, not a quality target. Decide early (Open Question 1). |
| Windows + OneDrive tooling friction | High | Low | Move repo off OneDrive in P1. WSL2 Docker. LF line endings. |
| Burnout / long tail on UI polish | Medium | High | Milestone demos. UI after core. Scope levers above. |

## 5. Post-MVP backlog (ordered by value/effort)

1. Alert-triggered investigations (Alertmanager webhook) + Slack notification.
2. Real-integration adapters: Prometheus (PromQL), Loki, Kubernetes (read-only), GitHub
   deployments. Same tool contracts; adapter conformance tests reuse P2 contract tests.
3. Traces tool family (Tempo/Jaeger) + synthetic traces in the simulator.
4. Clarifying questions and conversational follow-ups.
5. Learned confidence calibration from eval + feedback data.
6. Auto-drafted postmortems that feed back into the knowledge base (with human review).
7. OIDC, team scoping, asymmetric approval signatures, mTLS.
8. Real remediation executors (Kubernetes rollout undo, feature-flag API) behind the same
   token protocol, staged with dry-run diffs.
9. Helm chart.
