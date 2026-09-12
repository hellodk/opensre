"""Minimal Prometheus text parser and Keycloak metric shaper."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_SAMPLE_RE = re.compile(
    r"^(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)(?:\{(?P<labels>.*)\})?\s+(?P<value>\S+)"
)
_LABEL_RE = re.compile(r'(?P<key>[A-Za-z_][A-Za-z0-9_]*)="(?P<value>(?:[^"\\]|\\.)*)"')


@dataclass(frozen=True)
class Sample:
    """One parsed Prometheus sample: metric name, labels and float value."""

    name: str
    labels: dict[str, str]
    value: float


def _unescape_label(value: str) -> str:
    return (
        value.replace("\\\\", "\x00").replace('\\"', '"').replace("\\n", "\n").replace("\x00", "\\")
    )


def parse_prometheus_text(text: str) -> list[Sample]:
    """Parse Prometheus exposition text into samples; never raises."""
    samples: list[Sample] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _SAMPLE_RE.match(line)
        if match is None:
            continue
        try:
            value = float(match.group("value"))
        except ValueError:
            continue
        labels: dict[str, str] = {}
        raw_labels = match.group("labels")
        if raw_labels:
            for label in _LABEL_RE.finditer(raw_labels):
                labels[label.group("key")] = _unescape_label(label.group("value"))
        samples.append(Sample(name=match.group("name"), labels=labels, value=value))
    return samples


def _select(samples: list[Sample], name: str, **label_match: str) -> list[Sample]:
    return [
        s
        for s in samples
        if s.name == name and all(s.labels.get(key) == value for key, value in label_match.items())
    ]


def _sum(samples: list[Sample]) -> float:
    return sum(s.value for s in samples)


def shape_metrics(samples: list[Sample], realm: str) -> dict[str, Any]:
    """Shape parsed management /metrics samples into operational counters."""
    uptime = _select(samples, "process_uptime_seconds")
    process_cpu = _select(samples, "process_cpu_usage")
    system_cpu = _select(samples, "system_cpu_usage")
    heap_used = [
        s for s in _select(samples, "jvm_memory_used_bytes") if s.labels.get("area") == "heap"
    ]
    heap_max = [
        s
        for s in _select(samples, "jvm_memory_max_bytes")
        if s.labels.get("area") == "heap" and s.value >= 0
    ]
    threads = _select(samples, "jvm_threads_live_threads")
    gc_count = _select(samples, "jvm_gc_pause_seconds_count")
    gc_sum = _select(samples, "jvm_gc_pause_seconds_sum")
    java_info = _select(samples, "jvm_info_total")
    http_active = _select(samples, "http_server_active_requests")
    http_count = _select(samples, "http_server_requests_seconds_count")
    db_active = _select(samples, "agroal_active_count")
    db_available = _select(samples, "agroal_available_count")
    db_awaiting = _select(samples, "agroal_awaiting_count")
    db_max_used = _select(samples, "agroal_max_used_count")
    cluster = _select(samples, "vendor_cluster_size")
    worker_rejected = _select(samples, "worker_pool_rejected_total")
    realm_events = [
        s for s in _select(samples, "keycloak_user_events_total") if s.labels.get("realm") == realm
    ]

    heap_used_bytes = _sum(heap_used) if heap_used else None
    heap_max_bytes = _sum(heap_max) if heap_max else None
    heap_used_pct = (
        round(100 * heap_used_bytes / heap_max_bytes, 1)
        if heap_used_bytes is not None and heap_max_bytes
        else None
    )

    pools: dict[str, dict[str, Any]] = {}
    for sample in db_active + db_available + db_awaiting + db_max_used:
        datasource = sample.labels.get("datasource", "")
        pools.setdefault(
            datasource,
            {"datasource": datasource, "active": 0, "available": 0, "awaiting": 0, "max_used": 0},
        )
    for sample in db_active:
        pools[sample.labels.get("datasource", "")]["active"] = int(sample.value)
    for sample in db_available:
        pools[sample.labels.get("datasource", "")]["available"] = int(sample.value)
    for sample in db_awaiting:
        pools[sample.labels.get("datasource", "")]["awaiting"] = int(sample.value)
    for sample in db_max_used:
        pools[sample.labels.get("datasource", "")]["max_used"] = int(sample.value)

    by_status_class = {"2xx": 0.0, "3xx": 0.0, "4xx": 0.0, "5xx": 0.0, "other": 0.0}
    top_5xx: dict[tuple[str, str, str], float] = {}
    for sample in http_count:
        status = sample.labels.get("status", "")
        bucket = f"{status[0]}xx" if len(status) == 3 and status[0].isdigit() else "other"
        if bucket not in by_status_class:
            bucket = "other"
        by_status_class[bucket] += sample.value
        if bucket == "5xx":
            key = (
                sample.labels.get("uri", ""),
                sample.labels.get("method", ""),
                status,
            )
            top_5xx[key] = top_5xx.get(key, 0.0) + sample.value
    top_5xx_list = [
        {"uri": uri, "method": method, "status": status, "count": count}
        for (uri, method, status), count in sorted(top_5xx.items(), key=lambda item: -item[1])[:10]
    ]

    login_errors = [
        s for s in realm_events if s.labels.get("event") == "login" and s.labels.get("error")
    ]
    by_reason: dict[str, float] = {}
    for sample in login_errors:
        reason = sample.labels.get("error", "")
        by_reason[reason] = by_reason.get(reason, 0.0) + sample.value
    client_login_errors = _sum(
        [
            s
            for s in realm_events
            if s.labels.get("event") == "client_login" and s.labels.get("error")
        ]
    )
    lockouts = _sum(
        [
            s
            for s in realm_events
            if s.labels.get("event")
            in ("user_disabled_by_temporary_lockout", "user_disabled_by_permanent_lockout")
        ]
    )

    return {
        "uptime_seconds": uptime[0].value if uptime else None,
        "cpu": {
            "process_usage": process_cpu[0].value if process_cpu else None,
            "system_usage": system_cpu[0].value if system_cpu else None,
        },
        "jvm": {
            "heap_used_bytes": heap_used_bytes,
            "heap_max_bytes": heap_max_bytes,
            "heap_used_pct": heap_used_pct,
            "threads_live": threads[0].value if threads else None,
            "gc_pause_count": _sum(gc_count) if gc_count else None,
            "gc_pause_seconds_total": round(_sum(gc_sum), 3) if gc_sum else None,
            "java_version": java_info[0].labels.get("version") if java_info else None,
        },
        "db_pool": [pools[key] for key in sorted(pools)],
        "http": {
            "active_requests": _sum(http_active) if http_active else None,
            "requests_total": _sum(http_count) if http_count else None,
            "by_status_class": by_status_class,
            "top_5xx": top_5xx_list,
        },
        "cluster_size": cluster[0].value if cluster else None,
        "worker_pool_rejected_total": _sum(worker_rejected) if worker_rejected else None,
        "realm_user_events": {
            "present": bool(realm_events),
            "login_success": _sum(
                [
                    s
                    for s in realm_events
                    if s.labels.get("event") == "login" and not s.labels.get("error")
                ]
            ),
            "login_errors": _sum(login_errors),
            "login_errors_by_reason": by_reason,
            "client_login_errors": client_login_errors,
            "lockouts": lockouts,
        },
        "metric_names_seen": len({s.name for s in samples}),
    }
