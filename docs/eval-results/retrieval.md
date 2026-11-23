# Retrieval evaluation

Dataset: `evals/retrieval/queries.yaml` (32 queries). K = 5. Embeddings: BAAI/bge-small-en-v1.5 (local).

| Mode | Recall@5 | Precision@5 | MRR | nDCG@5 | Conflicts resolved | No-match abstention |
|------|---------:|------------:|----:|-------:|-------------------:|--------------------:|
| keyword | 0.958 | 0.424 | 0.905 | 0.906 | 0.50 | n/a |
| semantic | 1.000 | 0.386 | 0.946 | 0.944 | 0.00 | 1.00 |
| hybrid | 1.000 | 0.386 | 0.946 | 0.946 | 0.00 | 1.00 |
| hybrid+rerank | 1.000 | 0.386 | 0.982 | 0.973 | 1.00 | 1.00 |

Precision@5 is bounded by the number of relevant documents per query (1-3 of 5 slots).

## Abstention threshold

Top semantic similarity, answerable queries: min 0.623, median 0.775; no-match queries: max 0.666.
Best separating threshold on this set: 0.623 (accuracy 0.94). Configured `min_similarity`: 0.62.
Similarity alone cannot separate the classes, so `confident` also requires that most query terms occur in the corpus (or similarity >= 0.75).
Answerable queries flagged confident (hybrid+rerank): 27/28.

## Per query (hybrid+rerank)

| Query | Kind | Top 3 | RR | Confident |
|---|---|---|---:|---|
| q01 | similar_incident | INC-2026-031, INC-2025-112, INC-2026-063 | 1.00 | True |
| q02 | similar_incident | INC-2026-044, INC-2026-058, INC-2026-031 | 1.00 | True |
| q03 | similar_incident | INC-2026-052, INC-2026-019, INC-2026-044 | 1.00 | True |
| q04 | similar_incident | INC-2026-058, INC-2026-044, INC-2025-112 | 1.00 | True |
| q05 | similar_incident | INC-2026-063, INC-2026-031, INC-2026-085 | 1.00 | True |
| q06 | similar_incident | INC-2026-071, INC-2026-077, INC-2026-019 | 1.00 | True |
| q07 | similar_incident | INC-2026-077, INC-2025-140, INC-2026-058 | 1.00 | True |
| q08 | similar_incident | INC-2026-085, INC-2026-031, INC-2026-063 | 1.00 | True |
| q09 | similar_incident | INC-2025-112, INC-2026-031, INC-2026-058 | 1.00 | True |
| q10 | similar_incident | INC-2026-019, INC-2026-077, INC-2026-044 | 1.00 | True |
| q11 | runbook | rb-db-pool-exhaustion, rb-db-network-latency, rb-memory-leak | 1.00 | True |
| q12 | runbook | rb-redis-outage, rb-worker-backlog, rb-payments-provider | 1.00 | True |
| q13 | runbook | rb-worker-backlog, rb-redis-outage, rb-memory-leak | 1.00 | True |
| q14 | runbook | rb-payments-provider, rb-redis-outage, rb-worker-backlog | 1.00 | True |
| q15 | runbook | rb-memory-leak, rb-db-pool-exhaustion, rb-db-pool-exhaustion-2024 | 1.00 | True |
| q16 | runbook | INC-2026-085, troubleshooting-latency-triage, rb-db-network-latency | 1.00 | True |
| q17 | procedure | rb-rollback-procedure, proc-deploy, rb-db-pool-exhaustion | 1.00 | True |
| q18 | procedure | proc-deploy, INC-2026-077, INC-2025-140 | 1.00 | True |
| q19 | procedure | proc-deploy, rb-rollback-procedure, rb-db-pool-exhaustion | 1.00 | True |
| q20 | architecture | INC-2026-031, svc-orders-service, arch-shop | 0.50 | True |
| q21 | architecture | arch-shop, svc-api-gateway, svc-orders-service | 1.00 | True |
| q22 | architecture | operations-postgres-operations-guide, rb-db-pool-exhaustion, INC-2026-063 | 1.00 | True |
| q23 | architecture | troubleshooting-latency-triage, svc-orders-service, INC-2026-031 | 1.00 | True |
| q24 | service_filter | svc-fulfilment-worker | 1.00 | True |
| q25 | service_filter | svc-api-gateway | 1.00 | True |
| q26 | service_filter | INC-2026-071, INC-2026-044, INC-2026-077 | 1.00 | False |
| q27 | conflict | rb-db-pool-exhaustion, rb-redis-outage, rb-worker-backlog | 1.00 | True |
| q28 | conflict | rb-db-pool-exhaustion, rb-worker-backlog, rb-redis-outage | 1.00 | True |
| q29 | no_match | INC-2025-140, troubleshooting-latency-triage, INC-2026-052 | 0.00 | False |
| q30 | no_match | rb-memory-leak, INC-2026-019, INC-2026-085 | 0.00 | False |
| q31 | no_match | INC-2026-044, INC-2026-071, INC-2026-031 | 0.00 | False |
| q32 | no_match | INC-2026-077, INC-2026-052, rb-redis-outage | 0.00 | False |
