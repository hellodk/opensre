"""NGINX Plus REST API helpers: version detection, fetch, response shapers."""

from __future__ import annotations

from typing import Any

import httpx

from infrastructure.text.coercion import safe_int
from integrations.nginx.client import FetchError, FetchErrorKind, fetch_json

#: Peer states the Plus API reports for an unavailable peer.
_DOWN_PEER_STATES = ("down", "unavail", "unhealthy", "checking")


def detect_api_version(client: httpx.Client, api_path: str) -> tuple[int | None, FetchError | None]:
    """Detect the highest Plus API version from ``GET <api_path>/``."""
    data, err = fetch_json(client, f"{api_path}/")
    if err is not None:
        return None, err
    if not isinstance(data, list) or not data:
        return None, FetchError(
            FetchErrorKind.BODY,
            f"nginx Plus API did not return a version list for {api_path}/",
        )
    versions = [safe_int(item, 0) for item in data]
    highest = max(versions)
    if highest <= 0:
        return None, FetchError(
            FetchErrorKind.BODY,
            f"nginx Plus API did not return a version list for {api_path}/",
        )
    return highest, None


def plus_path(api_path: str, version: int, endpoint: str) -> str:
    """Build a versioned Plus API path."""
    return f"{api_path}/{version}/{endpoint}"


def fetch_plus(
    client: httpx.Client, api_path: str, version: int, endpoint: str
) -> tuple[Any | None, FetchError | None]:
    """Fetch one versioned Plus API endpoint."""
    return fetch_json(client, plus_path(api_path, version, endpoint))


def shape_nginx_info(payload: dict[str, Any]) -> dict[str, Any]:
    """Shape the Plus ``/nginx`` identity payload."""
    return {
        "version": str(payload.get("version") or "unknown"),
        "build": str(payload.get("build") or "unknown"),
        "address": str(payload.get("address") or ""),
        "pid": safe_int(payload.get("pid"), 0),
        "generation": safe_int(payload.get("generation"), 0),
        "load_timestamp": str(payload.get("load_timestamp") or ""),
        "timestamp": str(payload.get("timestamp") or ""),
    }


def shape_connections(payload: dict[str, Any]) -> dict[str, Any]:
    """Shape the Plus ``/connections`` payload."""
    return {
        "accepted": safe_int(payload.get("accepted"), 0),
        "dropped": safe_int(payload.get("dropped"), 0),
        "active": safe_int(payload.get("active"), 0),
        "idle": safe_int(payload.get("idle"), 0),
    }


def shape_requests(payload: dict[str, Any]) -> dict[str, Any]:
    """Shape the Plus ``/http/requests`` payload."""
    return {
        "total": safe_int(payload.get("total"), 0),
        "current": safe_int(payload.get("current"), 0),
    }


def shape_server_zones(payload: dict[str, Any], zone_filter: str = "") -> list[dict[str, Any]]:
    """Shape the Plus ``/http/server_zones`` object into a sorted zone list."""
    zones: list[dict[str, Any]] = []
    for name, stats in payload.items():
        if zone_filter and name != zone_filter:
            continue
        stats = stats or {}
        responses = stats.get("responses") or {}
        total = safe_int(responses.get("total"), 0)
        five_xx = safe_int(responses.get("5xx"), 0)
        ssl = stats.get("ssl") or {}
        zones.append(
            {
                "zone": name,
                "processing": safe_int(stats.get("processing"), 0),
                "requests": safe_int(stats.get("requests"), 0),
                "responses_1xx": safe_int(responses.get("1xx"), 0),
                "responses_2xx": safe_int(responses.get("2xx"), 0),
                "responses_3xx": safe_int(responses.get("3xx"), 0),
                "responses_4xx": safe_int(responses.get("4xx"), 0),
                "responses_5xx": five_xx,
                "responses_total": total,
                "error_rate_pct": round(100 * five_xx / total, 2) if total else 0.0,
                "discarded": safe_int(stats.get("discarded"), 0),
                "received_bytes": safe_int(stats.get("received"), 0),
                "sent_bytes": safe_int(stats.get("sent"), 0),
                "ssl_handshakes_failed": safe_int(ssl.get("handshakes_failed"), 0),
            }
        )
    zones.sort(key=lambda z: (-z["responses_5xx"], z["zone"]))
    return zones


def _shape_peer(peer: dict[str, Any]) -> dict[str, Any]:
    """Shape one Plus upstream peer entry."""
    responses = peer.get("responses") or {}
    checks = peer.get("health_checks") or {}
    has_checks = isinstance(peer.get("health_checks"), dict)
    return {
        "id": safe_int(peer.get("id"), 0),
        "server": str(peer.get("server") or ""),
        "name": str(peer.get("name") or ""),
        "backup": bool(peer.get("backup", False)),
        "weight": safe_int(peer.get("weight"), 0),
        "state": str(peer.get("state") or ""),
        "active": safe_int(peer.get("active"), 0),
        "requests": safe_int(peer.get("requests"), 0),
        "responses_5xx": safe_int(responses.get("5xx"), 0),
        "responses_total": safe_int(responses.get("total"), 0),
        "fails": safe_int(peer.get("fails"), 0),
        "unavail": safe_int(peer.get("unavail"), 0),
        "health_checks": safe_int(checks.get("checks"), 0),
        "health_check_fails": safe_int(checks.get("fails"), 0),
        "health_check_unhealthy": safe_int(checks.get("unhealthy"), 0),
        "health_check_last_passed": checks.get("last_passed") if has_checks else None,
        "header_time_ms": safe_int(peer.get("header_time"), 0),
        "response_time_ms": safe_int(peer.get("response_time"), 0),
        "downtime_ms": safe_int(peer.get("downtime"), 0),
        "selected": str(peer.get("selected") or ""),
    }


def shape_upstreams(payload: dict[str, Any], upstream_filter: str = "") -> list[dict[str, Any]]:
    """Shape the Plus ``/http/upstreams`` object into a sorted upstream list."""
    upstreams: list[dict[str, Any]] = []
    for name, stats in payload.items():
        if upstream_filter and name != upstream_filter:
            continue
        stats = stats or {}
        peers = [_shape_peer(peer or {}) for peer in stats.get("peers") or []]
        peers_up = sum(1 for peer in peers if peer["state"] == "up")
        peers_down = sum(1 for peer in peers if peer["state"] in _DOWN_PEER_STATES)
        upstreams.append(
            {
                "upstream": name,
                "zone": str(stats.get("zone") or ""),
                "keepalive": safe_int(stats.get("keepalive"), 0),
                "zombies": safe_int(stats.get("zombies"), 0),
                "peers_total": len(peers),
                "peers_up": peers_up,
                "peers_down": peers_down,
                "peers": peers,
            }
        )
    upstreams.sort(key=lambda u: (-u["peers_down"], u["upstream"]))
    return upstreams


def shape_caches(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Shape the Plus ``/http/caches`` object into a cache-zone list."""
    caches: list[dict[str, Any]] = []
    for name, stats in payload.items():
        stats = stats or {}
        size = safe_int(stats.get("size"), 0)
        max_size = safe_int(stats.get("max_size"), 0)
        hit = safe_int((stats.get("hit") or {}).get("responses"), 0)
        miss = safe_int((stats.get("miss") or {}).get("responses"), 0)
        expired = safe_int((stats.get("expired") or {}).get("responses"), 0)
        bypass = safe_int((stats.get("bypass") or {}).get("responses"), 0)
        denominator = hit + miss + expired + bypass
        caches.append(
            {
                "cache": name,
                "size_bytes": size,
                "max_size_bytes": max_size,
                "utilization_pct": round(100 * size / max_size, 2) if max_size else None,
                "cold": bool(stats.get("cold", False)),
                "hit_responses": hit,
                "miss_responses": miss,
                "expired_responses": expired,
                "stale_responses": safe_int((stats.get("stale") or {}).get("responses"), 0),
                "updating_responses": safe_int((stats.get("updating") or {}).get("responses"), 0),
                "revalidated_responses": safe_int(
                    (stats.get("revalidated") or {}).get("responses"), 0
                ),
                "bypass_responses": bypass,
                "hit_ratio_pct": round(100 * hit / denominator, 2) if denominator else 0.0,
            }
        )
    return caches
