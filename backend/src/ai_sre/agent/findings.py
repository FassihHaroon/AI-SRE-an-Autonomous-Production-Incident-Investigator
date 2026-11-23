"""Evidence normalisation: tool results -> typed findings (deterministic, no LLM).

``extract_findings`` is a pure function of the evidence list, so finding ids are stable across
analysis passes and checkpoints.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any

from ai_sre.agent.models import Evidence, Finding

MIB = 2**20


def _ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


Extractor = Callable[
    [dict[str, Any]], Iterator[tuple[str, str | None, datetime | None, str, dict[str, Any]]]
]


def _anomalies(
    d: dict[str, Any],
) -> Iterator[tuple[str, str | None, datetime | None, str, dict[str, Any]]]:
    for a in d.get("anomalies", []):
        yield (
            "metric_anomaly",
            a["service"],
            _ts(a.get("onset")),
            f"{a['service']} {a['signal']} {a['baseline_avg']:.4g} -> {a['recent_avg']:.4g} {a['unit']} "
            f"({a.get('change_pct')}%)",
            {
                "signal": a["signal"],
                "baseline": a["baseline_avg"],
                "recent": a["recent_avg"],
                "change_pct": a.get("change_pct"),
                "z": a.get("z_score"),
            },
        )


def _deployments(
    d: dict[str, Any],
) -> Iterator[tuple[str, str | None, datetime | None, str, dict[str, Any]]]:
    records = d.get("deployments") or ([d["deployment"]] if "deployment" in d else [])
    for r in records:
        yield (
            "deployment",
            r["service"],
            _ts(r.get("finished_at")) or _ts(r.get("started_at")),
            f"{r['id']} {r['service']} {r.get('previous_version')} -> {r['version']} ({r['kind']}, "
            f"{r['status']}): {r['changes_summary']}",
            {
                "id": r["id"],
                "kind": r["kind"],
                "status": r["status"],
                "version": r["version"],
                "previous_version": r.get("previous_version"),
                "started_at": r.get("started_at"),
                "failure_reason": r.get("failure_reason"),
                "author": r.get("author"),
            },
        )


def _error_signatures(
    d: dict[str, Any],
) -> Iterator[tuple[str, str | None, datetime | None, str, dict[str, Any]]]:
    for svc, count in (d.get("by_service") or {}).items():
        yield ("error_volume", svc, None, f"{count} error log lines from {svc}", {"count": count})
    for s in d.get("signatures", []):
        yield (
            "error_signature",
            s["service"],
            _ts(s.get("first_seen")),
            f"{s['service']}: {s['template']} x{s['count']}",
            {
                "template": s["template"],
                "count": s["count"],
                "error_type": s.get("error_type"),
                "example": s.get("example"),
                "last_seen": s.get("last_seen"),
            },
        )


def _log_entries(
    d: dict[str, Any],
) -> Iterator[tuple[str, str | None, datetime | None, str, dict[str, Any]]]:
    entries = d.get("entries", [])
    if not entries:
        return
    by_svc: dict[str, Counter[str]] = {}
    for e in entries:
        by_svc.setdefault(e["service"], Counter())[e.get("version") or "?"] += 1
    for svc, versions in by_svc.items():
        yield (
            "log_versions",
            svc,
            None,
            f"{svc} log lines by version: {dict(versions)}",
            {"versions": dict(versions)},
        )
    types = Counter(e.get("error_type") for e in entries if e.get("error_type"))
    if types:
        yield ("log_error_types", None, None, f"error types: {dict(types)}", {"types": dict(types)})
    if d.get("trace_id"):
        yield (
            "trace",
            None,
            _ts(entries[0].get("timestamp")),
            f"trace {d['trace_id']} spans {', '.join(d.get('services', []))}",
            {"services": d.get("services", [])},
        )


def _dependencies(
    d: dict[str, Any],
) -> Iterator[tuple[str, str | None, datetime | None, str, dict[str, Any]]]:
    for dep in d.get("dependencies", []):
        kind = "dependency_healthy" if dep["health"] == "healthy" else "dependency_unhealthy"
        yield (
            kind,
            dep["name"],
            None,
            f"{dep['name']} {dep['health']}: {'; '.join(dep['evidence'])}",
            {"health": dep["health"], "evidence": dep["evidence"]},
        )


def _service_status(
    d: dict[str, Any],
) -> Iterator[tuple[str, str | None, datetime | None, str, dict[str, Any]]]:
    for s in d.get("services", []):
        if s["health"] != "healthy":
            yield (
                "service_unhealthy",
                s["service"],
                None,
                f"{s['service']} {s['health']} (alerts: {', '.join(s['firing_alerts']) or 'none'})",
                {"health": s["health"], "alerts": s["firing_alerts"], "version": s.get("version")},
            )
        if s.get("restarts_last_hour"):
            yield (
                "restarts",
                s["service"],
                None,
                f"{s['service']} restarted {s['restarts_last_hour']}x in 1h",
                {"restarts": s["restarts_last_hour"]},
            )


def _pools(
    d: dict[str, Any],
) -> Iterator[tuple[str, str | None, datetime | None, str, dict[str, Any]]]:
    for p in d.get("pools", []):
        saturated = (
            (p.get("utilization") or 0) >= 0.9
            or (p.get("pending_acquires") or 0) > 0
            or (p.get("acquire_timeouts_per_s") or 0) > 0
        )
        idle_tx = p.get("server_sessions", {}).get("idle in transaction", 0)
        values = {
            "utilization": p.get("utilization"),
            "pending": p.get("pending_acquires"),
            "timeouts_per_s": p.get("acquire_timeouts_per_s"),
            "idle_in_transaction": idle_tx,
        }
        kind = "pool_saturation" if saturated else "pool_ok"
        yield (
            kind,
            p["service"],
            None,
            f"{p['service']} pool {p.get('in_use')}/{p.get('max_connections')} in use, {p.get('pending_acquires')} waiting, "
            f"{idle_tx} idle in transaction",
            values,
        )


def _db_health(
    d: dict[str, Any],
) -> Iterator[tuple[str, str | None, datetime | None, str, dict[str, Any]]]:
    idle = d.get("idle_in_transaction", 0)
    apps: Counter[str] = Counter()
    long_query = 0.0
    for s in d.get("sessions", []):
        if s["state"].startswith("idle in transaction"):
            apps[s["application"]] += s["count"]
        long_query = max(long_query, s.get("longest_query_s") or 0.0)
    if idle >= 3:
        yield (
            "idle_in_transaction",
            max(apps, key=apps.__getitem__) if apps else None,
            None,
            f"{idle} sessions idle in transaction ({dict(apps)})",
            {"count": idle, "by_app": dict(apps)},
        )
    active = sum(s["count"] for s in d.get("sessions", []) if s["state"] == "active")
    if long_query > 0.5:
        yield (
            "long_running_query",
            None,
            None,
            f"longest running query {long_query:.1f}s",
            {"seconds": long_query},
        )
    if idle < 3 and long_query <= 0.5:
        yield (
            "db_healthy",
            "postgres",
            None,
            f"database healthy: {active} active, {idle} idle in transaction",
            {"active": active, "idle_in_transaction": idle},
        )


def _slow_queries(
    d: dict[str, Any],
) -> Iterator[tuple[str, str | None, datetime | None, str, dict[str, Any]]]:
    for q in d.get("queries", []):
        if q["mean_ms"] >= 20 and q["calls"] >= 5:
            yield (
                "slow_query",
                "postgres",
                None,
                f"{q['mean_ms']} ms mean x{q['calls']}: {q['query'][:140]}",
                {"query": q["query"], "mean_ms": q["mean_ms"], "calls": q["calls"]},
            )


def _db_metrics(
    d: dict[str, Any],
) -> Iterator[tuple[str, str | None, datetime | None, str, dict[str, Any]]]:
    slow = [q for q in d.get("client_query_latency", []) if q["p95_s"] >= 0.1]
    for q in slow[:5]:
        yield (
            "client_query_slow",
            q["service"],
            None,
            f"{q['service']}/{q['query']} p95 {q['p95_s']}s",
            {"query": q["query"], "p95_s": q["p95_s"], "calls_per_s": q["calls_per_s"]},
        )
    services = {q["service"] for q in slow}
    queries = {q["query"] for q in slow}
    if len(services) >= 2 and len(queries) >= 3:
        yield (
            "uniform_query_latency",
            "postgres",
            None,
            f"{len(queries)} query types slow across {', '.join(sorted(services))}",
            {"services": sorted(services), "queries": len(queries)},
        )


def _service_metrics(
    d: dict[str, Any],
) -> Iterator[tuple[str, str | None, datetime | None, str, dict[str, Any]]]:
    svc = d.get("service")
    sig = {s["signal"]: s["value"] for s in d.get("signals", [])}
    yield ("service_metrics", svc, None, d.get("summary", ""), sig)
    checks = (
        ("queue_depth", 20, "queue_backlog"),
        ("worker_failure_rate", 0.1, "worker_failures"),
        ("redis_error_rate", 0.1, "redis_errors"),
        ("upstream_error_rate", 0.1, "upstream_errors"),
        ("db_pool_utilization", 0.9, "pool_saturation"),
    )
    for signal, threshold, kind in checks:
        value = sig.get(signal)
        if value is not None and value >= threshold:
            yield (kind, svc, None, f"{svc} {signal} = {value:.3g}", {signal: value})


def _resources(
    d: dict[str, Any],
) -> Iterator[tuple[str, str | None, datetime | None, str, dict[str, Any]]]:
    for u in d.get("usage", []):
        growth = u.get("memory_growth_bytes_per_min") or 0
        if growth >= 15 * MIB:
            yield (
                "memory_growth",
                u["service"],
                None,
                f"{u['service']} memory +{growth / MIB:.0f} MiB/min",
                {"growth_mib_per_min": round(growth / MIB, 1)},
            )
    for c in d.get("containers", []):
        if c.get("oom_risk") or c.get("restarts_last_hour"):
            yield (
                "container_pressure",
                c["service"],
                None,
                f"{c['service']} memory {c.get('memory_utilization')} of limit, restarts/h {c.get('restarts_last_hour')}",
                {
                    "memory_utilization": c.get("memory_utilization"),
                    "restarts": c.get("restarts_last_hour"),
                },
            )


def _incidents(
    d: dict[str, Any],
) -> Iterator[tuple[str, str | None, datetime | None, str, dict[str, Any]]]:
    for h in d.get("hits", []):
        inc = h["incident"]
        yield (
            "similar_incident",
            None,
            None,
            f"{inc['id']} ({inc['category']}): {inc['title']}",
            {
                "id": inc["id"],
                "category": inc["category"],
                "services": inc["services"],
                "score": h["score"],
            },
        )


def _knowledge_incidents(
    d: dict[str, Any],
) -> Iterator[tuple[str, str | None, datetime | None, str, dict[str, Any]]]:
    confident = bool(d.get("confident"))
    for h in d.get("hits", []):
        c = h["citation"]
        if not c.get("incident_id"):
            continue
        yield (
            "similar_incident",
            None,
            None,
            f"{c['incident_id']} ({h.get('category')}): {c['title']} [{c['source']} > {c['section']}]",
            {
                "id": c["incident_id"],
                "category": h.get("category"),
                "services": h.get("services", []),
                "score": h.get("score", 0),
                "similarity": h.get("similarity"),
                "confident": confident,
                "source": c["source"],
                "section": c["section"],
            },
        )


def _compare(
    d: dict[str, Any],
) -> Iterator[tuple[str, str | None, datetime | None, str, dict[str, Any]]]:
    """Before/after comparison: did the signal rise in the current window (e.g. since a deploy)?"""
    base, cur = d["baseline"]["stats"], d["current"]["stats"]
    rose = (
        d.get("direction") == "up"
        and (cur.get("avg") or 0) > 0
        and (base.get("avg") in (None, 0) or (d.get("change_pct") or 0) >= 50)
    )
    yield (
        "change_after" if rose else "no_change_after",
        d.get("service"),
        _ts(d["current"]["start"]),
        f"{d.get('service')} {d.get('signal')}: {base.get('avg')} -> {cur.get('avg')} ({d.get('direction')})",
        {
            "signal": d.get("signal"),
            "baseline_avg": base.get("avg"),
            "current_avg": cur.get("avg"),
            "change_pct": d.get("change_pct"),
        },
    )


EXTRACTORS: dict[str, Extractor] = {
    "detect_metric_anomalies": _anomalies,
    "list_recent_deployments": _deployments,
    "get_deployment_details": _deployments,
    "aggregate_errors": _error_signatures,
    "get_error_logs": _log_entries,
    "search_logs": _log_entries,
    "get_logs_by_trace_id": _log_entries,
    "get_dependency_status": _dependencies,
    "get_service_status": _service_status,
    "get_connection_pool_status": _pools,
    "get_database_health": _db_health,
    "get_slow_queries": _slow_queries,
    "get_database_metrics": _db_metrics,
    "get_service_metrics": _service_metrics,
    "get_resource_usage": _resources,
    "get_container_health": _resources,
    "search_incident_history": _incidents,
    "find_similar_incidents": _knowledge_incidents,
    "compare_metric_windows": _compare,
}


def extract_findings(evidence: list[Evidence]) -> list[Finding]:
    findings: list[Finding] = []
    seen_deploys: set[str] = set()
    for ev in evidence:
        extractor = EXTRACTORS.get(ev.tool)
        if not ev.ok or ev.data is None or extractor is None:
            continue
        for kind, service, at, detail, values in extractor(ev.data):
            if kind == "deployment":  # the same deployment can come from several tools
                if values["id"] in seen_deploys:
                    continue
                seen_deploys.add(values["id"])
            findings.append(
                Finding(
                    id=f"f-{len(findings) + 1:03d}",
                    evidence_id=ev.id,
                    kind=kind,
                    service=service,
                    at=at,
                    detail=detail,
                    values=values,
                )
            )
    return findings
