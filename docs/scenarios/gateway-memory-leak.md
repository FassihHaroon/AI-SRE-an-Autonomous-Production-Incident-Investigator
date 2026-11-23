# Scenario G: api-gateway memory leak after a deploy

> **Simulated.** Category `bad_deploy` (resource exhaustion) · seed 512 · definition
> [simulator/scenarios/gateway-memory-leak.yaml](../../simulator/scenarios/gateway-memory-leak.yaml)

## What happens

| t | Event |
|---|-------|
| 0 s | `d-407` api-gateway 3.8.0 → 3.8.2 ("Product detail: cache recommendations in-process"). |
| ~8 s | Each `GET /api/products/{id}` adds ~768 KB to an in-process cache that never evicts. At normal traffic (~2 product views/s) resident memory grows ~1.5 MB/s, linearly with traffic. |
| ~3 min | The process reaches the container's 320 MiB limit and is **OOM-killed**. Docker restarts it, requests fail for a few seconds, and the leak starts again (sawtooth). |

## Trigger

```bash
uv run simctl trigger gateway-memory-leak
```

## Expected signals

| Where | Signal |
|-------|--------|
| Deployer | `d-407` api-gateway 3.8.2 |
| Prometheus | `process_resident_memory_bytes{service="api-gateway"}` grows linearly (`deriv` > 0); alert `HighMemoryUsage` |
| Prometheus | at the limit: `up{service="api-gateway"} == 0` briefly, `process_start_time_seconds` resets, alert `ServiceDown`, a burst of failed requests; then the climb repeats |
| Prometheus | CPU, DB and Redis metrics normal |
| Loki | after each restart: `api-gateway started` and `now running version 3.8.2` |

## Root cause

- **Category:** `bad_deploy` (resource exhaustion). **Component:** `api-gateway`. **Trigger:**
  `d-407` (3.8.2).
- **Trap:** a restart helps only until memory fills up again. The report must not call a
  restart a fix.
- **Correct remediation:** roll back to 3.8.0 (then bound the cache).

## Clearing

`uv run simctl clear` records a rollback to 3.8.0. The service drops the cache on its next poll and
resident memory falls back toward baseline (the containers set `MALLOC_MMAP_THRESHOLD_`, so freed
buffers are returned to the OS).
