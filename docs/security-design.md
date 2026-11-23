# AI SRE — Security & Safety Design

> Status: **Approved** (2026-11-23; decisions recorded inline) · Last updated: 2026-11-23
> Scope: the MVP running locally with Docker Compose against the simulator. Some controls are
> stricter than a local demo strictly needs. That is deliberate: the same design must hold
> when real integrations are added.

---

## 1. Assets and what we protect them from

| Asset | Why it matters | Primary threats |
|-------|----------------|-----------------|
| **Ability to change production** (remediation) | Highest-impact capability in the system | Prompt injection, privilege escalation, replayed or forged approvals, confused-deputy bugs |
| Telemetry content (logs may contain PII/secrets) | Confidentiality; LLM provider egress | Leakage to LLM provider, to UI users without access, to traces |
| Investigation reports | Drive human decisions | Hallucinated or manipulated conclusions, XSS via rendered content |
| Credentials (JWT secret, approval-token key, LLM API key, MCP service token, DB password) | Full compromise | Commit to git, log exposure, image layers |
| Audit log | Accountability | Tampering, deletion |
| LLM budget | Money | Abuse, runaway loops |

## 2. Trust boundaries

```
 [Browser] ──TLS*──► [web/nginx] ──► [API] ──► [Postgres] [Redis]
                                     │
                                     └─(queue)─► [Worker] ──HTTPS──► [LLM provider]  ← boundary: data leaves machine
                                                   │
                                                   └─MCP (service token)─► [Simulator]
                                                                            ├ read-only servers
                                                                            └ remediation server (approval token required)
 Untrusted data sources feeding the LLM: tool results (logs!), knowledge documents, the user's query.
 * TLS not enforced on localhost in MVP; required for any non-local deployment.
```

Principles:

1. **The LLM is an untrusted component.** Its output is treated like user input: validated,
   bounded and never directly executed.
2. **Data is not instructions.** Tool results and documents are data even when they say
   otherwise.
3. **Privilege lives in deterministic code paths with human approval**, enforced at the
   *point of execution* (the remediation server), not only at the point of decision.
4. **Deny by default**: tool allow-lists per node, RBAC per endpoint, network exposure
   minimal.

## 3. Threat model (STRIDE + LLM-specific)

| ID | Threat | Vector | Control(s) | Residual risk |
|----|--------|--------|------------|---------------|
| T1 | **Indirect prompt injection** leading to harmful action | Log line / runbook / incident text containing instructions | No write tools bound to LLM (C1) · approval tokens (C2) · catalog-derived risk + bounded params (C3) · fencing (C6) · adversarial evals | Injection may still **skew the narrative** of a report → mitigated by citations + human review; measured in evals |
| T2 | Injection causing **data exfiltration** | "Include all env vars in your report" | LLM has no network/egress tools. Redaction before LLM (C4). Report is shown only to authenticated users. | Low |
| T3 | **Approval forgery / replay** | Re-POST approve; reuse token; tamper params | HMAC-signed token bound to action_id + params_hash + approver + exp; single-use `jti` in Redis + DB unique constraint; verified by remediation server | Low |
| T4 | **Privilege escalation** | Responder calls approve endpoint; self-approval of high risk | RBAC dependency on every route; policy check on approve (role, self-approval rule, risk); tests per route × role | Low |
| T5 | **Stale/unsafe execution** | Approve rollback after state changed | Precondition hash check; proposal expiry; token expiry | Low |
| T6 | **Secret / PII leakage to LLM provider** | Logs with tokens, emails, card numbers | Redaction in Tool Gateway before LLM *and* before persistence (C4); synthetic secrets in simulator to test it; `ollama` mode for fully local | Regex redaction is imperfect → documented limitation |
| T7 | **XSS via telemetry/LLM text** | Log line `<img onerror=…>` rendered in SPA | React text rendering; Markdown renderer with HTML disabled + sanitiser; strict CSP | Low |
| T8 | **SQL injection** | Search/filter params | SQLAlchemy parameterised queries only; no string-built SQL; ruff/bandit rules | Low |
| T9 | **SSRF via tools** | LLM supplies URL in tool args | No tool accepts URLs or hosts; MCP endpoints fixed in config | Low |
| T10 | **Denial of wallet / service** | Many investigations, huge queries | Per-user rate limit + concurrency cap; per-investigation budgets; global daily token cap; request size limits | Low |
| T11 | **Credential exposure** | `.env` committed; secrets in logs/traces/images | `.env` gitignored, `.env.example` only; gitleaks in CI + pre-commit; secrets not baked into images; structlog processor drops `authorization`/`password`/`token` keys; prompt bodies off spans by default | Low |
| T12 | **Audit tampering** | Delete audit rows | Append-only trigger; app DB role lacks UPDATE/DELETE on `audit_log`; hash chain | Superuser can still tamper locally (accepted for MVP) |
| T13 | **Session hijack / CSRF** | Stolen token; cross-site POST | Short-lived access token in memory; refresh in httpOnly SameSite=Strict cookie with rotation + reuse detection; CORS allow-list; refresh endpoint requires custom header | Low |
| T14 | **Supply-chain compromise** | Malicious dependency/image | Locked deps (`uv.lock`, `package-lock.json`), pip-audit / npm audit, Trivy, Dependabot, pinned image tags, SBOM on release | Medium (industry-wide) |
| T15 | **Misleading report causes harmful human action** | Confidently wrong RCA | Computed confidence, limitations, citations, evals measuring calibration; UI language "probable cause" | Medium; inherent in decision support |
| T16 | **Ground-truth leakage into agent** | Simulator exposes scenario fault definitions | Ground truth stored in `evals/` only; tools return derived telemetry; test asserts no tool response contains fault-definition fields | Low |

## 4. Safety controls (the core of the design)

### C1 — Capability separation

- The only LLM-facing tools are **read-only**. Each LLM node gets an explicit allow-list
  (agent-design § 5.8), enforced by the Tool Gateway. Any other call returns `denied` and is
  logged and counted.
- `remediation.dry_run` and `remediation.execute` are called **only** by deterministic nodes.
  There is no code path that turns LLM output into an `execute` call without passing through
  `approval_gate` → API approval → token.
- A unit test enumerates every LLM node's bound tools and asserts that none is on the
  remediation server. This test is a CI gate.

### C2 — Approval tokens (enforced at the point of execution)

```
claims = {
  "iss": "ai-sre-api", "aud": "remediation",
  "jti": <uuid>, "action_id": <uuid>, "investigation_id": <uuid>,
  "action_type": "rollback_deployment",
  "params_hash": sha256(canonical_json(params)),
  "approver": <user_id>, "risk": "high",
  "iat": now, "exp": now + 10 min
}
token = HS256(claims, APPROVAL_SIGNING_KEY)   # distinct key from the user-JWT key
```

The remediation server verifies the signature, `aud`, expiry and `params_hash` against the
actual params received, and atomically claims the `jti` (`SET NX` in Redis + a unique column
in Postgres). Any failure → refuse, and the refusal is audited. MVP uses a symmetric key
shared by API and simulator. **Future:** asymmetric (Ed25519) so the executor holds only the
public key.

### C3 — Remediation policy

- **Catalog-only actions.** Each `action_type` has a JSON schema with bounds (agent-design
  § 5.6). Unknown types are rejected.
- **Risk comes from the catalog**, never from the LLM.
- **Who may approve:**

  | Risk | Approver role | Self-approval (requester = approver) | Comment required |
  |------|---------------|--------------------------------------|------------------|
  | low | approver | allowed | no |
  | medium | approver | allowed | yes |
  | high | approver | **allowed** (`ALLOW_SELF_APPROVAL_HIGH=true`, decided 2026-11-23) | yes |

  Self-approval is allowed for solo development. A second seeded approver user exists so the
  four-eyes flow can be demonstrated, and setting `ALLOW_SELF_APPROVAL_HIGH=false` restores
  it for any shared deployment. Self-approvals are flagged in the audit log
  (`details.self_approved=true`).

- **No auto-execution in MVP**, whatever the risk level. "Low" only means looser approval
  rules.
- **Proposal gating:** only for the primary hypothesis with confidence band ≥ medium.
- **Global kill switch:** `REMEDIATION_ENABLED=false` disables proposal creation and makes the
  remediation server refuse all executes.
- **Simulation boundary:** in the MVP, the remediation server's executor implementation can
  only mutate simulator session state. No code for real executors exists, so there is nothing
  to misconfigure.
- One action at a time; stop on first failure; verification after each.

### C4 — Redaction

Applied in the Tool Gateway to every tool result **before** it is (a) shown to the LLM and
(b) persisted:

| Pattern | Replacement |
|---------|-------------|
| Bearer/JWT tokens, `Authorization:` headers | `[REDACTED:token]` |
| AWS-style keys, generic `api[_-]?key=…`, `password=…`, `secret=…` | `[REDACTED:secret]` |
| Connection strings with credentials (`postgres://user:pass@`) | password part redacted |
| Email addresses | `[REDACTED:email]` (domain kept optionally) |
| Card-like numbers passing Luhn | `[REDACTED:pan]` |
| IPv4 addresses | kept (operationally relevant); configurable |

- Counted per pattern (`aisre_redactions_total`). The simulator deliberately seeds such values
  so the eval suite can assert **zero leakage** into LLM requests (captured in replay
  cassettes) and into stored rows.
- Limitation (documented): regex redaction misses novel formats. For sensitive real data, use
  `ollama` mode or add a stronger detector (future: Presidio).

### C5 — Budgets and rate limits

| Limit | Default |
|-------|---------|
| Investigations per user | 10 / hour, 2 concurrent |
| Per-investigation input / output tokens | 400k / 40k |
| Per-investigation tool calls | 40 |
| Per-investigation wall clock | 5 min (excluding approval wait) |
| Global LLM spend cap | configurable USD/day; new investigations rejected when exceeded |
| Login attempts | 5 / min per IP + per username, exponential lockout |
| Request body size | 64 KB (API), query text ≤ 2,000 chars |

### C6 — Prompt-injection defence-in-depth

C1–C3 make injection **non-escalating**. On top of that:

- Untrusted content is wrapped in `<tool_result untrusted="true">` / `<document
  untrusted="true">`, and the system prompt states that content inside is data.
- Tool output is summarised server-side, which shrinks the surface for long injected
  passages.
- `validate_report` ensures recommendations cite evidence and use catalog actions.
- The adversarial eval suite (evaluation-plan § 4.4) measures: unjustified action proposals,
  instruction echoing, narrative manipulation. **Unjustified proposal rate must be 0.**
- The UI marks evidence from logs as "raw telemetry", so humans see where text came from.

## 5. Authentication and authorisation

### 5.1 Authentication (MVP)

- Local accounts; passwords hashed with **argon2id** (`argon2-cffi`), minimum length 12.
- Access token: JWT HS256, 15 min, claims `sub`, `role`, `jti`; held in SPA memory only.
- Refresh token: random 256-bit, stored **hashed**, 7 days, rotated on every use; reuse of a
  rotated token revokes the whole family. Delivered as `httpOnly; Secure; SameSite=Strict;
  Path=/api/v1/auth` cookie.
- Seed users are created by the `migrate` job **only** when `SEED_DEMO_USERS=true`, with
  passwords from `.env` (no hard-coded defaults; startup fails if unset while seeding is on).
- **Future:** OIDC (Keycloak in compose for local, Entra/Okta in real deployments). The RBAC
  layer is built so that roles can come from token claims.

### 5.2 Authorisation (RBAC)

| Capability | viewer | responder | approver | admin |
|------------|:-----:|:-----:|:-----:|:-----:|
| View investigations, reports, evidence, timeline | ✓ | ✓ | ✓ | ✓ |
| Create / cancel / close investigations; feedback | | ✓ | ✓ | ✓ |
| Approve / reject remediation | | | ✓ | ✓* |
| Manage knowledge base; reindex | | | | ✓ |
| Manage users; read audit log | | | | ✓ |
| View eval results | ✓ | ✓ | ✓ | ✓ |

\* Admin is **not** implicitly an approver for high-risk actions unless also granted
`approver`. This prevents "admin can do everything" from bypassing separation of duties.
(Implementation: roles are a set, not a strict hierarchy, for this one capability.)

- Enforced by FastAPI dependencies (`require(Permission.APPROVE_ACTION)`), never by checks
  scattered through handlers.
- **Test matrix:** every route × every role is generated and asserted in `tests/api/test_rbac.py`.
- Object-level rule (MVP): all investigations are visible to all authenticated users (single
  team). Future: team scoping.

### 5.3 Service-to-service

- Worker → simulator MCP: static bearer token (`MCP_SERVICE_TOKEN`) over the internal compose
  network. The simulator port is not published outside dev mode.
- API → simulator admin API: same token, separate scope header.
- `/metrics` endpoints: internal network only (not proxied by nginx).
- **Future:** mTLS between services.

## 6. Data protection and privacy

- **LLM egress:** in `gemini` mode (the development default), redacted telemetry excerpts,
  the user's query and knowledge excerpts are sent to Google. **On the free tier Google may
  use prompts and responses to improve its products**, so only synthetic simulator data may
  ever flow through it. This is documented in the README and `.env.example`. Before any real
  data: switch to a paid tier/provider with acceptable retention terms, or a local model
  (`ollama`, future).
- The Gemini API key lives only in `.env` (git-ignored) as `AI_SRE_GEMINI_API_KEY`. It is
  held as `SecretStr` and never logged. Health endpoints report only whether it is set.
- **Retention (configurable):** investigations and evidence 90 days; Redis event streams 24 h;
  audit log indefinite. A cleanup job deletes expired investigation data (cascade) but not
  audit entries.
- **Traces/logs:** prompt/completion bodies are not exported to OTel by default; structured
  logs exclude tool result bodies.
- Synthetic data only in MVP. No real PII expected, but controls are exercised as if it were
  present.

## 7. Web application security

- CSP: `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'
  data:; connect-src 'self'; frame-ancestors 'none'` set by nginx.
- Headers: `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`,
  `Strict-Transport-Security` when TLS is enabled.
- CORS: only the SPA origin; credentials allowed only for `/auth/refresh`.
- Markdown: `react-markdown` without `rehype-raw`, plus link sanitisation (only `http(s)`;
  `rel="noopener noreferrer"`). Log lines rendered in `<pre>` as text.
- Error responses never include stack traces outside `DEBUG=true`.

## 8. Audit logging

Audited events (non-exhaustive): login success/failure, token refresh reuse detected,
investigation created/cancelled/closed, proposals created, approval/rejection (with comment),
token minted, execute attempted/succeeded/refused (with reason), kill switch state, user and
role changes, knowledge edits, denied tool calls, budget exhaustion.

Each row stores `prev_hash` and `row_hash = sha256(prev_hash || canonical(row))`. A
`verify-audit` CLI command checks the chain.

## 9. Secure development lifecycle

| Control | Where |
|---------|-------|
| Pre-commit: ruff, gitleaks, end-of-file/CRLF fixer | `.pre-commit-config.yaml` |
| SAST: ruff security rules (`S`), bandit-equivalent | CI |
| Dependency audit: `pip-audit`, `npm audit --omit=dev` | CI (fail on high/critical) |
| Container/IaC scan: `trivy fs` + image scan on release | CI / release |
| Secret scan: gitleaks | pre-commit + CI |
| Dependabot (pip, npm, GitHub Actions, Docker) | `.github/dependabot.yml` |
| Actions pinned by SHA; `permissions:` minimal per workflow | workflows |
| SBOM (syft) attached to releases | release |
| Non-root containers, read-only root FS where feasible, no secrets in build args | Dockerfiles |

## 10. Safety acceptance tests (must pass before MVP sign-off)

1. No LLM node has any remediation tool bound (static test).
2. `remediation.execute` without token / with expired / tampered params / reused `jti` →
   refused (4 tests) and audited.
3. Responder cannot approve. With `ALLOW_SELF_APPROVAL_HIGH=false`, an approver cannot
   self-approve a high-risk action (default is `true` for solo development).
   Admin without `approver` cannot approve.
4. Adversarial suite: 0 unjustified proposals, 0 executions without approval, 0 seeded
   secrets in LLM requests or DB rows.
5. XSS payloads in log lines render inert in the SPA (Playwright test).
6. RBAC matrix test passes for all routes.
7. Kill switch blocks proposal creation and execution.
8. Audit chain verifies after a full demo run.

## 11. Known limitations (accepted for MVP)

- Symmetric approval-signing key shared by API and simulator.
- Regex-based redaction.
- No TLS on localhost; no mTLS between services.
- Single-tenant visibility model.
- Local Postgres superuser can tamper with the audit log. The hash chain detects but cannot
  prevent this.
