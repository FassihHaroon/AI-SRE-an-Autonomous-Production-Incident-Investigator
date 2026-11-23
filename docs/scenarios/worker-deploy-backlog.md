# Scenario C: Worker deploy breaks shipping labels; the queue backs up

> **Simulated.** Category `bad_deploy` · seed 170 · definition
> [simulator/scenarios/worker-deploy-backlog.yaml](../../simulator/scenarios/worker-deploy-backlog.yaml)

## What happens

| t | Event |
|---|-------|
| 0 s | `d-407` fulfilment-worker 1.6.2 → 1.7.0 ("Shipping labels: migrate to labels API v2 client"). |
| ~8 s | Rollout completes. Every job now calls the labels API v2 endpoint, which hangs for 2 s, and 85% of calls time out (`LabelServiceTimeoutError`). Failed jobs are re-queued at the back (up to 5 attempts, then dead-lettered). |
| +1 min… | With 2 consumers × ~2 s per job, throughput falls to ≤ 1 job/s, below the ~1 order/s arrival rate plus retries, so the queue grows steadily. |

The public API stays **healthy**: orders are accepted and paid, but not fulfilled.

## Trigger

```bash
uv run simctl trigger worker-deploy-backlog
```

## Expected signals

| Where | Signal |
|-------|--------|
| Deployer | `d-407` fulfilment-worker 1.7.0 |
| Prometheus | `queue_depth{queue="fulfilment"}` and `queue_oldest_job_age_seconds` rise steadily; later `queue_depth{queue="fulfilment-dead-letter"}` grows; alert `FulfilmentQueueBacklog` |
| Prometheus | `worker_job_failures_total{reason="LabelServiceTimeoutError"}`; `worker_jobs_processed_total{result="retried"}` up, `{result="success"}` down; `worker_job_duration_seconds` ≈ 2 s; `worker_busy_consumers` pinned at 2 |
| Prometheus | gateway and orders-service error rate and latency **unchanged** |
| Loki | fulfilment-worker ERROR `fulfilment of order N failed (attempt k/5), re-queued` with `error.message` `POST https://labels.shipping.internal/v2/labels timed out after 2.0s` |

## Root cause

- **Category:** `bad_deploy`. **Component:** `fulfilment-worker`. **Trigger:** `d-407` (1.7.0).
- **Mechanism:** the new label client targets a wrong/unreachable endpoint; per-job latency rises
  ~40× and most attempts fail, so processing capacity drops below the arrival rate.
- **Not** a traffic surge and not a database problem (DB metrics normal).
- **Correct remediation:** roll back fulfilment-worker to 1.6.2. Scaling the worker would only add
  more timeouts. Afterwards, re-drive the dead-letter queue.

## Clearing

`uv run simctl clear` records a rollback to 1.6.2. The backlog drains in seconds (≈ 30 jobs/s with
the healthy build). Dead-lettered jobs stay in `queue:fulfilment:dead` until `simctl reset`.
