"""Pure shapers over nats-server monitoring-endpoint JSON payloads."""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any

TOP_N = 10

_GO_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)(ns|µs|us|ms|s|m|h)")
_GO_UNIT_SECONDS = {
    "ns": 1e-9,
    "µs": 1e-6,
    "us": 1e-6,
    "ms": 1e-3,
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
}
_ZERO_TIMES = ("", "0001-01-01T00:00:00Z")


def parse_go_duration(value: str | None) -> float | None:
    """Parse a Go duration string (e.g. "1m52s", "133µs") into seconds."""
    if not value:
        return None
    total = 0.0
    found = False
    for number, unit in _GO_DURATION_RE.findall(value):
        total += float(number) * _GO_UNIT_SECONDS[unit]
        found = True
    if not found:
        return None
    return total


def ns_to_seconds(value: int | float | None) -> float | None:
    """Convert nanoseconds to seconds; None stays None.

    Negative values pass through unchanged: ``-1`` means unlimited, not
    a negative duration.
    """
    if value is None:
        return None
    if value < 0:
        return float(value)
    return float(value) / 1e9


def _active_ms(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value) / 1e6, 1)
    except (TypeError, ValueError):
        return None


def _replica(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": entry.get("name", ""),
        "current": bool(entry.get("current", False)),
        "active_ms": _active_ms(entry.get("active")),
    }


def shape_varz(payload: dict[str, Any]) -> dict[str, Any]:
    """Shape a /varz payload into server status fields."""
    cluster = payload.get("cluster") or None
    shaped_cluster = None
    if cluster:
        shaped_cluster = {
            "name": cluster.get("name", ""),
            "port": cluster.get("cluster_port", 0),
            "urls": list(cluster.get("urls") or []),
            "pool_size": cluster.get("pool_size", 0),
        }
    slow_stats = payload.get("slow_consumer_stats") or {}
    return {
        "server_id": payload.get("server_id", ""),
        "server_name": payload.get("server_name", ""),
        "version": payload.get("version", ""),
        "go": payload.get("go", ""),
        "git_commit": payload.get("git_commit", ""),
        "host": payload.get("host", ""),
        "port": payload.get("port", 0),
        "http_port": payload.get("http_port", 0),
        "start": payload.get("start", ""),
        "now": payload.get("now", ""),
        "uptime": payload.get("uptime", ""),
        "uptime_seconds": parse_go_duration(payload.get("uptime")),
        "cores": payload.get("cores", 0),
        "gomaxprocs": payload.get("gomaxprocs", 0),
        "cpu_pct": payload.get("cpu", 0),
        "mem_bytes": payload.get("mem", 0),
        "connections": payload.get("connections", 0),
        "total_connections": payload.get("total_connections", 0),
        "max_connections": payload.get("max_connections", 0),
        "routes": payload.get("routes", 0),
        "remotes": payload.get("remotes", 0),
        "leafnodes": payload.get("leafnodes", 0),
        "subscriptions": payload.get("subscriptions", 0),
        "in_msgs": payload.get("in_msgs", 0),
        "out_msgs": payload.get("out_msgs", 0),
        "in_bytes": payload.get("in_bytes", 0),
        "out_bytes": payload.get("out_bytes", 0),
        "slow_consumers": payload.get("slow_consumers", 0),
        "slow_consumer_stats": {
            "clients": slow_stats.get("clients", 0),
            "routes": slow_stats.get("routes", 0),
            "gateways": slow_stats.get("gateways", 0),
            "leafs": slow_stats.get("leafs", 0),
        },
        "stale_connections": payload.get("stale_connections", 0),
        "stalled_clients": payload.get("stalled_clients", 0),
        "max_payload": payload.get("max_payload", 0),
        "max_pending": payload.get("max_pending", 0),
        "max_control_line": payload.get("max_control_line", 0),
        "write_deadline_seconds": ns_to_seconds(payload.get("write_deadline")),
        "ping_interval_seconds": ns_to_seconds(payload.get("ping_interval")),
        "ping_max": payload.get("ping_max", 0),
        "system_account": payload.get("system_account", ""),
        "config_load_time": payload.get("config_load_time", ""),
        "cluster": shaped_cluster,
        "clustered": bool(payload.get("cluster")),
        "http_req_stats": dict(payload.get("http_req_stats") or {}),
    }


def shape_jetstream_varz(payload: dict[str, Any]) -> dict[str, Any]:
    """Shape the /varz ``jetstream`` block into JetStream status fields."""
    jetstream = payload.get("jetstream")
    if not jetstream:
        return {"enabled": False, "config": None, "stats": None, "meta": None}
    config = jetstream.get("config") or {}
    stats = jetstream.get("stats") or {}
    api = stats.get("api") or {}
    meta = jetstream.get("meta")
    shaped_meta = None
    if meta:
        shaped_meta = {
            "name": meta.get("name", ""),
            "leader": meta.get("leader", ""),
            "cluster_size": meta.get("cluster_size", 0),
            "pending": meta.get("pending", 0),
            "replicas": [_replica(r) for r in meta.get("replicas") or []],
        }
    return {
        "enabled": True,
        "config": {
            "max_memory": config.get("max_memory", 0),
            "max_storage": config.get("max_storage", 0),
            "store_dir": config.get("store_dir", ""),
            "sync_interval_seconds": ns_to_seconds(config.get("sync_interval")),
            "strict": bool(config.get("strict", False)),
        },
        "stats": {
            "memory": stats.get("memory", 0),
            "storage": stats.get("storage", 0),
            "reserved_memory": stats.get("reserved_memory", 0),
            "reserved_storage": stats.get("reserved_storage", 0),
            "accounts": stats.get("accounts", 0),
            "ha_assets": stats.get("ha_assets", 0),
            "api": {
                "level": api.get("level", 0),
                "total": api.get("total", 0),
                "errors": api.get("errors", 0),
            },
        },
        "meta": shaped_meta,
    }


def shape_healthz(status: int, payload: Any) -> dict[str, Any]:
    """Shape a /healthz response into a health report."""
    body = payload if isinstance(payload, dict) else {}
    status_text = body.get("status", "")
    return {
        "status_code": int(status),
        "status": status_text,
        "error": body.get("error"),
        "ok": int(status) == int(HTTPStatus.OK) and status_text == "ok",
    }


def shape_connection(entry: dict[str, Any]) -> dict[str, Any]:
    """Shape one /connz connection entry."""
    reason = entry.get("reason")
    rtt_seconds = parse_go_duration(entry.get("rtt"))
    return {
        "cid": entry.get("cid", 0),
        "kind": entry.get("kind", ""),
        "type": entry.get("type", ""),
        "name": entry.get("name", ""),
        "lang": entry.get("lang", ""),
        "version": entry.get("version", ""),
        "ip": entry.get("ip", ""),
        "port": entry.get("port", 0),
        "start": entry.get("start", ""),
        "last_activity": entry.get("last_activity", ""),
        "uptime": entry.get("uptime", ""),
        "uptime_seconds": parse_go_duration(entry.get("uptime")),
        "idle": entry.get("idle", ""),
        "idle_seconds": parse_go_duration(entry.get("idle")),
        "rtt": entry.get("rtt", ""),
        "rtt_ms": rtt_seconds * 1000.0 if rtt_seconds is not None else None,
        "pending_bytes": entry.get("pending_bytes", 0),
        "in_msgs": entry.get("in_msgs", 0),
        "out_msgs": entry.get("out_msgs", 0),
        "in_bytes": entry.get("in_bytes", 0),
        "out_bytes": entry.get("out_bytes", 0),
        "subscriptions": entry.get("subscriptions", 0),
        "subscriptions_list": list(entry.get("subscriptions_list") or []),
        "closed": entry.get("stop") is not None,
        "stop": entry.get("stop"),
        "reason": reason,
        "slow_consumer": str(reason or "").lower().startswith("slow consumer"),
    }


def summarize_connections(connections: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize shaped connections."""
    by_kind: Counter[str] = Counter()
    by_lang: Counter[str] = Counter()
    closed_by_reason: Counter[str] = Counter()
    pending_total = 0
    subs_total = 0
    slow_closures = 0
    open_count = 0
    oldest_idle: dict[str, Any] | None = None
    for conn in connections:
        by_kind[conn.get("kind", "")] += 1
        by_lang[conn.get("lang", "")] += 1
        pending_total += int(conn.get("pending_bytes") or 0)
        subs_total += int(conn.get("subscriptions") or 0)
        if conn.get("closed"):
            reason = conn.get("reason") or "unknown"
            closed_by_reason[reason] += 1
            if conn.get("slow_consumer"):
                slow_closures += 1
        else:
            open_count += 1
            idle = conn.get("idle_seconds")
            if idle is not None and (oldest_idle is None or idle > oldest_idle["idle_seconds"]):
                oldest_idle = {
                    "cid": conn.get("cid"),
                    "name": conn.get("name", ""),
                    "idle_seconds": idle,
                }
    top_pending = sorted(
        (c for c in connections if not c.get("closed") and int(c.get("pending_bytes") or 0) > 0),
        key=lambda c: int(c.get("pending_bytes") or 0),
        reverse=True,
    )[:TOP_N]
    return {
        "total": len(connections),
        "open": open_count,
        "closed": len(connections) - open_count,
        "by_kind": dict(by_kind),
        "by_lang": dict(by_lang),
        "pending_bytes_total": pending_total,
        "subscriptions_total": subs_total,
        "slow_consumer_closures": slow_closures,
        "closed_by_reason": dict(closed_by_reason),
        "top_pending": [
            {
                "cid": c.get("cid"),
                "name": c.get("name", ""),
                "ip": c.get("ip", ""),
                "pending_bytes": c.get("pending_bytes", 0),
                "subscriptions": c.get("subscriptions", 0),
            }
            for c in top_pending
        ],
        "oldest_idle": oldest_idle,
    }


def shape_subsz(payload: dict[str, Any]) -> dict[str, Any]:
    """Shape a /subsz payload into subscription statistics."""
    entries = payload.get("subscriptions_list") or []
    subscriptions = [
        {
            "subject": e.get("subject", ""),
            "account": e.get("account", ""),
            "sid": e.get("sid", ""),
            "msgs": e.get("msgs", 0),
            "cid": e.get("cid", 0),
            "queue": e.get("qgroup"),
        }
        for e in entries
    ]
    return {
        "num_subscriptions": payload.get("num_subscriptions", 0),
        "num_cache": payload.get("num_cache", 0),
        "num_inserts": payload.get("num_inserts", 0),
        "num_removes": payload.get("num_removes", 0),
        "num_matches": payload.get("num_matches", 0),
        "cache_hit_rate": payload.get("cache_hit_rate", 0.0),
        "max_fanout": payload.get("max_fanout", 0),
        "avg_fanout": payload.get("avg_fanout", 0.0),
        "subscriptions": subscriptions,
        "listed": len(subscriptions),
    }


def summarize_subscriptions(subs: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize shaped subscriptions by account with busiest subjects."""
    by_account: Counter[str] = Counter()
    for sub in subs:
        by_account[sub.get("account", "")] += 1
    system = by_account.get("$SYS", 0)
    application = sum(n for account, n in by_account.items() if account != "$SYS")
    candidates = [
        s for s in subs if not s.get("subject", "").startswith(("$SYS.", "$NRG.", "$JS."))
    ]
    candidates.sort(key=lambda s: int(s.get("msgs") or 0), reverse=True)
    return {
        "by_account": dict(by_account),
        "system_subscriptions": system,
        "application_subscriptions": application,
        "top_by_msgs": candidates[:TOP_N],
    }


def shape_jsz(payload: dict[str, Any]) -> dict[str, Any]:
    """Shape a /jsz payload into JetStream overview fields."""
    enabled = not payload.get("disabled", False)
    api = payload.get("api") or {}
    config = payload.get("config") or {}
    meta = payload.get("meta_cluster")
    shaped_meta = None
    if meta:
        shaped_meta = {
            "name": meta.get("name", ""),
            "leader": meta.get("leader", ""),
            "cluster_size": meta.get("cluster_size", 0),
            "pending": meta.get("pending", 0),
            "replicas": [_replica(r) for r in meta.get("replicas") or []],
        }
    return {
        "enabled": enabled,
        "server_id": payload.get("server_id", ""),
        "streams": payload.get("streams", 0),
        "consumers": payload.get("consumers", 0),
        "messages": payload.get("messages", 0),
        "bytes": payload.get("bytes", 0),
        "memory": payload.get("memory", 0),
        "storage": payload.get("storage", 0),
        "reserved_memory": payload.get("reserved_memory", 0),
        "reserved_storage": payload.get("reserved_storage", 0),
        "accounts": payload.get("accounts", 0),
        "ha_assets": payload.get("ha_assets", 0),
        "api": {
            "level": api.get("level", 0),
            "total": api.get("total", 0),
            "errors": api.get("errors", 0),
        },
        "config": {
            "max_memory": config.get("max_memory", 0),
            "max_storage": config.get("max_storage", 0),
            "store_dir": config.get("store_dir", ""),
        },
        "meta_cluster": shaped_meta,
    }


def _utilisation_pct(used: Any, limit: Any) -> float | None:
    try:
        limit_int = int(limit)
    except (TypeError, ValueError):
        return None
    if limit_int <= 0:
        return None
    try:
        return round(float(used or 0) / limit_int * 100.0, 1)
    except (TypeError, ValueError):
        return None


def shape_stream(
    detail: dict[str, Any], *, account: str = "", server_name: str = ""
) -> dict[str, Any]:
    """Shape one stream_detail entry with its RAFT leadership context."""
    state = detail.get("state") or {}
    raw_config = detail.get("config")
    shaped_config = None
    if isinstance(raw_config, dict):
        shaped_config = {
            "subjects": list(raw_config.get("subjects") or []),
            "retention": raw_config.get("retention", ""),
            "storage": raw_config.get("storage", ""),
            "num_replicas": raw_config.get("num_replicas", 0),
            "max_msgs": raw_config.get("max_msgs", 0),
            "max_bytes": raw_config.get("max_bytes", 0),
            "max_age_seconds": ns_to_seconds(raw_config.get("max_age")),
            "max_msg_size": raw_config.get("max_msg_size", 0),
            "max_consumers": raw_config.get("max_consumers", 0),
            "discard": raw_config.get("discard", ""),
            "max_msgs_per_subject": raw_config.get("max_msgs_per_subject", 0),
        }
    cluster = detail.get("cluster") or {}
    replicas = [_replica(r) for r in cluster.get("replicas") or []]
    leader = cluster.get("leader", "")
    messages = state.get("messages", 0)
    stream_bytes = state.get("bytes", 0)
    return {
        "name": detail.get("name", ""),
        "account": account,
        "created": detail.get("created", ""),
        "messages": messages,
        "bytes": stream_bytes,
        "first_seq": state.get("first_seq", 0),
        "last_seq": state.get("last_seq", 0),
        "first_ts": state.get("first_ts", ""),
        "last_ts": state.get("last_ts", ""),
        "num_subjects": state.get("num_subjects", 0),
        "num_deleted": state.get("num_deleted", 0),
        "consumer_count": state.get("consumer_count", 0),
        "config": shaped_config,
        "utilisation": {
            "msgs_pct": _utilisation_pct(
                messages, shaped_config["max_msgs"] if shaped_config else None
            ),
            "bytes_pct": _utilisation_pct(
                stream_bytes, shaped_config["max_bytes"] if shaped_config else None
            ),
        },
        "cluster": {
            "name": cluster.get("name", ""),
            "leader": leader,
            "raft_group": cluster.get("raft_group"),
            "leader_since": cluster.get("leader_since"),
            "replicas": replicas,
        },
        "authoritative": leader == server_name,
        "replicas_not_current": sum(1 for r in replicas if not r["current"]),
    }


def _is_paused(config: dict[str, Any] | None) -> bool:
    if not isinstance(config, dict):
        return False
    pause_until = config.get("pause_until") or ""
    if pause_until in _ZERO_TIMES:
        return False
    try:
        moment = datetime.fromisoformat(str(pause_until).replace("Z", "+00:00"))
    except ValueError:
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment > datetime.now(UTC)


def shape_consumer(
    detail: dict[str, Any], *, stream_last_seq: int = 0, server_name: str = ""
) -> dict[str, Any]:
    """Shape one consumer_detail entry with backlog flags and leadership."""
    delivered = detail.get("delivered") or {}
    ack_floor = detail.get("ack_floor") or {}
    num_pending = detail.get("num_pending", 0)
    num_ack_pending = detail.get("num_ack_pending", 0)
    num_redelivered = detail.get("num_redelivered", 0)
    raw_config = detail.get("config")
    paused = _is_paused(raw_config if isinstance(raw_config, dict) else None)
    shaped_config = None
    if isinstance(raw_config, dict):
        shaped_config = {
            "durable_name": raw_config.get("durable_name", ""),
            "deliver_policy": raw_config.get("deliver_policy", ""),
            "ack_policy": raw_config.get("ack_policy", ""),
            "ack_wait_seconds": ns_to_seconds(raw_config.get("ack_wait")),
            "max_deliver": raw_config.get("max_deliver", 0),
            "filter_subject": raw_config.get("filter_subject"),
            "filter_subjects": list(raw_config.get("filter_subjects") or []),
            "max_ack_pending": raw_config.get("max_ack_pending", 0),
            "max_waiting": raw_config.get("max_waiting", 0),
            "replay_policy": raw_config.get("replay_policy", ""),
            "num_replicas": raw_config.get("num_replicas", 0),
            "paused": paused,
        }
    cluster = detail.get("cluster") or {}
    leader = cluster.get("leader", "")
    delivered_stream_seq = delivered.get("stream_seq", 0) or 0
    delivered_consumer_seq = delivered.get("consumer_seq", 0) or 0
    flags: list[str] = []
    if num_pending > 0:
        flags.append("backlog")
    if num_ack_pending > 0:
        flags.append("ack_pending")
    if num_redelivered > 0:
        flags.append("redeliveries")
    if delivered_consumer_seq == 0 and num_pending > 0:
        flags.append("never_delivered")
    if paused:
        flags.append("paused")
    return {
        "name": detail.get("name", ""),
        "stream": detail.get("stream_name", ""),
        "created": detail.get("created", ""),
        "delivered": {
            "consumer_seq": delivered_consumer_seq,
            "stream_seq": delivered_stream_seq,
            "last_active": delivered.get("last_active"),
        },
        "ack_floor": {
            "consumer_seq": ack_floor.get("consumer_seq", 0) or 0,
            "stream_seq": ack_floor.get("stream_seq", 0) or 0,
            "last_active": ack_floor.get("last_active"),
        },
        "num_pending": num_pending,
        "num_ack_pending": num_ack_pending,
        "num_redelivered": num_redelivered,
        "num_waiting": detail.get("num_waiting", 0),
        "lag": max(int(stream_last_seq or 0) - int(delivered_stream_seq), 0),
        "config": shaped_config,
        "cluster": {
            "name": cluster.get("name", ""),
            "leader": leader,
            "raft_group": cluster.get("raft_group"),
            "replicas": [_replica(r) for r in cluster.get("replicas") or []],
        },
        "authoritative": leader == server_name,
        "flags": flags,
    }


def shape_route(entry: dict[str, Any]) -> dict[str, Any]:
    """Shape one /routez route entry."""
    rtt_seconds = parse_go_duration(entry.get("rtt"))
    return {
        "rid": entry.get("rid", 0),
        "remote_name": entry.get("remote_name", ""),
        "remote_id": entry.get("remote_id", ""),
        "ip": entry.get("ip", ""),
        "port": entry.get("port", 0),
        "start": entry.get("start", ""),
        "uptime": entry.get("uptime", ""),
        "uptime_seconds": parse_go_duration(entry.get("uptime")),
        "idle": entry.get("idle", ""),
        "idle_seconds": parse_go_duration(entry.get("idle")),
        "rtt": entry.get("rtt", ""),
        "rtt_ms": rtt_seconds * 1000.0 if rtt_seconds is not None else None,
        "pending_size": entry.get("pending_size", 0),
        "in_msgs": entry.get("in_msgs", 0),
        "out_msgs": entry.get("out_msgs", 0),
        "in_bytes": entry.get("in_bytes", 0),
        "out_bytes": entry.get("out_bytes", 0),
        "subscriptions": entry.get("subscriptions", 0),
        "compression": entry.get("compression", ""),
        "did_solicit": bool(entry.get("did_solicit", False)),
        "is_configured": bool(entry.get("is_configured", False)),
    }


def summarize_routes(routes: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize shaped routes grouped by remote server name."""
    peers: dict[str, dict[str, Any]] = {}
    pending_total = 0
    subs_total = 0
    for route in routes:
        name = route.get("remote_name", "")
        peer = peers.setdefault(
            name,
            {
                "remote_name": name,
                "connections": 0,
                "pending_size_total": 0,
                "subscriptions_total": 0,
                "rtt_ms_max": None,
            },
        )
        peer["connections"] += 1
        peer["pending_size_total"] += int(route.get("pending_size") or 0)
        peer["subscriptions_total"] += int(route.get("subscriptions") or 0)
        rtt = route.get("rtt_ms")
        if rtt is not None and (peer["rtt_ms_max"] is None or rtt > peer["rtt_ms_max"]):
            peer["rtt_ms_max"] = rtt
        pending_total += int(route.get("pending_size") or 0)
        subs_total += int(route.get("subscriptions") or 0)
    return {
        "count": len(routes),
        "peers": [peers[name] for name in sorted(peers)],
        "pending_size_total": pending_total,
        "subscriptions_total": subs_total,
    }


def shape_gatewayz(payload: dict[str, Any]) -> dict[str, Any]:
    """Pass gateway topology through unshaped (no fixture exercises entries)."""
    outbound = dict(payload.get("outbound_gateways") or {})
    inbound = dict(payload.get("inbound_gateways") or {})
    return {
        "outbound": outbound,
        "inbound": inbound,
        "outbound_count": len(outbound),
        "inbound_count": len(inbound),
    }


def shape_leafz(payload: dict[str, Any]) -> dict[str, Any]:
    """Pass leaf-node topology through unshaped."""
    leafs = list(payload.get("leafs") or [])
    return {"count": payload.get("leafnodes", 0), "leafs": leafs}
