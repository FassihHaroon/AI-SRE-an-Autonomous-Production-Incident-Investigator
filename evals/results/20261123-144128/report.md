# Evaluation run 20261123-144128

- **Dataset:** ai-sre-incidents v1.0.0 (25 of 25 scenarios)
- **Mode:** replay (deterministic scenario worlds; see docs/evaluation.md)
- **Commit:** d794617 · Python 3.13.7
- **Created:** 2026-11-23T14:41:48.252818+00:00

## Configurations compared

| Config | n | RC acc | RC top-3 | Ev. prec | Ev. recall | Tool sel. | Tool eff. | Severity | Remediation | Halluc. | Complete | Calls | Latency | Tokens | Cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `rules-baseline` | 25 | 92% | 91% | 59% | 80% | 90% | 41% | 76% | 88% | 0% | 100% | 14.0 | 78 ms | 0.0 | $0.0000 |
| `rules-single-round` | 25 | 88% | 87% | 60% | 70% | 77% | 45% | 80% | 84% | 0% | 100% | 10.5 | 50 ms | 0.0 | $0.0000 |
| `rules-eager-stop` | 25 | 92% | 91% | 60% | 74% | 82% | 43% | 76% | 88% | 0% | 100% | 11.8 | 61 ms | 0.0 | $0.0000 |
| `rules-no-knowledge` | 25 | 92% | 91% | 57% | 86% | 97% | 41% | 76% | 88% | 0% | 100% | 15.4 | 84 ms | 0.0 | $0.0000 |
| `rules-tight-budget` | 25 | 88% | 87% | 59% | 70% | 84% | 45% | 80% | 84% | 0% | 100% | 11.4 | 65 ms | 0.0 | $0.0000 |

Latency is wall-clock time of the agent; in replay mode tool calls are answered in-process, so it excludes telemetry-backend latency. Cost is estimated from token counts and list prices (`ai_sre.observability.tracing.PRICES_PER_MTOK`).

- `rules-baseline`: Rule-based planner, default limits (3 rounds, 24 tool calls, stop at 0.75)
- `rules-single-round`: Triage round only: no targeted follow-up evidence
- `rules-eager-stop`: Stops as soon as the leading hypothesis reaches 0.55
- `rules-no-knowledge`: Ablation: knowledge base / incident history tools unavailable
- `rules-tight-budget`: At most 10 tool calls

## By scenario type (`rules-baseline`)

| Tag | n | RC acc | Severity | Remediation | Ev. recall | Halluc. |
|---|---|---|---|---|---|---|
| capacity | 1 | 100% | 100% | 0% | 100% | 0% |
| conflicting_evidence | 3 | 100% | 100% | 100% | 89% | 0% |
| database_bottleneck | 8 | 75% | 88% | 75% | 59% | 0% |
| dependency_failure | 4 | 100% | 100% | 100% | 100% | 0% |
| misleading_symptoms | 8 | 88% | 88% | 75% | 77% | 0% |
| missing_evidence | 2 | 100% | 100% | 100% | 100% | 0% |
| multiple_failures | 2 | 100% | 100% | 100% | 100% | 0% |
| network | 2 | 50% | 50% | 50% | 50% | 0% |
| no_obvious_root_cause | 2 | 100% | 50% | 100% | — | 0% |
| queue_failure | 4 | 100% | 75% | 100% | 67% | 0% |
| recent_deployment | 14 | 100% | 71% | 100% | 90% | 0% |
| redis_failure | 5 | 100% | 80% | 100% | 93% | 0% |
| resource_exhaustion | 2 | 100% | 100% | 100% | 100% | 0% |

## Per scenario (`rules-baseline`)

| Scenario | Expected | Predicted | RC | Sev (got/exp) | Fix | Calls | Note |
|---|---|---|---|---|---|---|---|
| S01-orders-n-plus-one-deploy | bad_deploy:orders-service | bad_deploy:orders-service (0.83) | 1.0 | SEV2/SEV2 | ✓ | 12 | correct |
| S02-redis-outage | cache_failure:redis | cache_failure:redis (0.70) | 1.0 | SEV2/SEV2 | ✓ | 12 | correct |
| S03-payments-provider-errors | dependency_failure:payments-provider | dependency_failure:payments-provider (0.70) | 1.0 | SEV2/SEV2 | ✓ | 12 | correct |
| S04-db-network-latency | infrastructure:postgres | none | 0.0 | SEV3/SEV3 | ✗ | 9 | no root cause reported |
| S05-db-connection-leak | config_change:orders-service | config_change:orders-service (0.83) | 1.0 | SEV2/SEV2 | ✓ | 11 | correct |
| S06-gateway-failed-canary | failed_deploy:api-gateway | failed_deploy:api-gateway (0.75) | 1.0 | SEV3/SEV2 | ✓ | 17 | correct |
| S07-gateway-memory-leak | bad_deploy:api-gateway | bad_deploy:api-gateway (0.75) | 1.0 | SEV3/SEV3 | ✓ | 19 | correct |
| S08-worker-deploy-backlog | bad_deploy:fulfilment-worker | bad_deploy:fulfilment-worker (0.75) | 1.0 | SEV3/SEV2 | ✓ | 12 | correct |
| S09-worker-backlog-no-deploy | worker_degradation:fulfilment-worker | worker_degradation:fulfilment-worker (0.38) | 1.0 | SEV3/SEV3 | ✓ | 11 | correct |
| S10-traffic-surge | capacity:api-gateway | capacity:api-gateway (0.38) | 1.0 | SEV3/SEV3 | ✗ | 11 | correct |
| S11-slow-queries-no-deploy | database_degradation:postgres | none | 0.0 | SEV2/SEV2 | ✗ | 9 | no root cause reported |
| S12-n-plus-one-missing-logs-db | bad_deploy:orders-service | bad_deploy:orders-service (0.68) | 1.0 | SEV2/SEV2 | ✓ | 20 | correct |
| S13-redis-outage-missing-infra | cache_failure:redis | cache_failure:redis (0.38) | 1.0 | SEV2/SEV2 | ✓ | 12 | correct |
| S14-payments-with-late-deploy | dependency_failure:payments-provider | dependency_failure:payments-provider (0.70) | 1.0 | SEV2/SEV2 | ✓ | 19 | correct |
| S15-gateway-errors-from-orders-leak | config_change:orders-service | config_change:orders-service (0.83) | 1.0 | SEV2/SEV2 | ✓ | 11 | correct |
| S16-redis-and-payments | dependency_failure:payments-provider | cache_failure:redis (0.70) | 1.0 | SEV2/SEV2 | ✓ | 13 | correct |
| S17-memory-leak-and-worker-backlog | bad_deploy:api-gateway | bad_deploy:api-gateway (0.75) | 1.0 | SEV2/SEV2 | ✓ | 21 | correct |
| S18-vague-slowness-no-cause | none (abstain) | none | 1.0 | SEV3/SEV4 | ✓ | 9 | correctly abstained |
| S19-innocent-deploy-no-cause | none (abstain) | none | 1.0 | SEV4/SEV4 | ✓ | 16 | correctly abstained |
| S20-db-latency-with-worker-config-change | infrastructure:postgres | infrastructure:postgres (0.60) | 1.0 | SEV2/SEV3 | ✓ | 20 | correct |
| S21-redis-outage-after-gateway-deploy | cache_failure:redis | cache_failure:redis (0.70) | 1.0 | SEV2/SEV2 | ✓ | 20 | correct |
| S22-payments-provider-down | dependency_failure:payments-provider | dependency_failure:payments-provider (0.70) | 1.0 | SEV1/SEV1 | ✓ | 12 | correct |
| S23-n-plus-one-flaky-telemetry | bad_deploy:orders-service | bad_deploy:orders-service (0.83) | 1.0 | SEV2/SEV2 | ✓ | 11 | correct |
| S24-orders-500s-after-deploy | bad_deploy:orders-service | bad_deploy:orders-service (0.63) | 1.0 | SEV4/SEV2 | ✓ | 18 | correct |
| S25-redis-latency | cache_failure:redis | cache_failure:redis (0.70) | 1.0 | SEV2/SEV3 | ✓ | 12 | correct |

## Misses to investigate

- **S04-db-network-latency**: no root cause reported; remediation: description lacks ['network']
- **S06-gateway-failed-canary**: severity SEV3 vs SEV2
- **S08-worker-deploy-backlog**: severity SEV3 vs SEV2
- **S10-traffic-surge**: remediation: description lacks ['scale']
- **S11-slow-queries-no-deploy**: no root cause reported; remediation: description lacks ['postgres']
- **S18-vague-slowness-no-cause**: severity SEV3 vs SEV4
- **S20-db-latency-with-worker-config-change**: severity SEV2 vs SEV3
- **S24-orders-500s-after-deploy**: severity SEV4 vs SEV2
- **S25-redis-latency**: severity SEV2 vs SEV3

## Limitations

- Scenario worlds are synthetic telemetry derived from injected faults. They are deterministic and comparable run to run, but simpler than production noise.
- The rule-based agent was developed alongside the simulator scenarios that several dataset entries are modelled on; scores on those are optimistic. The edge-case scenarios (missing/conflicting evidence, multiple failures, no root cause) were written for this evaluation and were not used to tune the agent.
- LLM configurations, when included, run on a subset (free-tier token limits) with one sample per scenario; differences of one or two scenarios are within noise.
- Hallucination is measured as claims in the report not traceable to tool results (components, deployment ids, cited evidence, citations, affected services). It does not judge free-text wording; the optional model-based judge covers that, subjectively.
