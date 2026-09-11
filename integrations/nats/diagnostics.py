"""Read-only diagnostic queries against the nats-server monitoring endpoint."""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

import integrations.nats.client as nats_client
from core.tool_framework.utils import tool_unavailable
from integrations._validation_helpers import report_validation_failure
from integrations.nats.config import NatsConfig
from integrations.nats.monitoring import (
    TOP_N,
    shape_connection,
    shape_consumer,
    shape_gatewayz,
    shape_healthz,
    shape_jetstream_varz,
    shape_jsz,
    shape_leafz,
    shape_route,
    shape_stream,
    shape_subsz,
    shape_varz,
    summarize_connections,
    summarize_routes,
    summarize_subscriptions,
)

logger = logging.getLogger(__name__)

DEFAULT_CONNECTION_LIMIT = 25
MAX_CONNECTION_LIMIT = 1024
ALLOWED_CONNZ_SORT: frozenset[str] = frozenset(
    {
        "cid",
        "start",
        "subs",
        "pending",
        "msgs_to",
        "msgs_from",
        "bytes_to",
        "bytes_from",
        "last",
        "idle",
        "uptime",
        "stop",
        "reason",
        "rtt",
    }
)
CLOSED_ONLY_SORTS: frozenset[str] = frozenset({"stop", "reason"})
ALLOWED_CONNZ_STATE: frozenset[str] = frozenset({"open", "closed", "all"})
JETSTREAM_DISABLED_HINT = (
    "JetStream is disabled on server '{server_name}'. Start nats-server with -js (or jetstream {{}} in the "
    "config) or point NATS_MONITOR_URL at a JetStream-enabled server."
)
PER_SERVER_NOTE = (
    "/jsz reports only the streams and consumers that have a replica on server '{server_name}'; "
    "counters are authoritative only for assets whose RAFT leader is this server. Query each "
    "cluster member's monitoring port for the full picture."
)


def _error(message: str, **extra: Any) -> dict[str, Any]:
    return tool_unavailable("nats", message, **extra)


def clamp_limit(value: int | None) -> int:
    """Clamp a connection limit into the server-supported range."""
    if value is None or value <= 0:
        return DEFAULT_CONNECTION_LIMIT
    return min(value, MAX_CONNECTION_LIMIT)


def get_server_status(config: NatsConfig) -> dict[str, Any]:
    """Return one nats-server's status, health and JetStream overview."""
    if not config.is_configured:
        return _error("NATS is not configured (NATS_MONITOR_URL is required).")
    try:
        warnings: list[str] = []
        with nats_client.build_client(config) as client:
            result, err = nats_client.get_json(client, config, "/varz")
            if err is not None or result is None:
                message = err.message if err is not None else "empty /varz response"
                kind = err.kind if err is not None else None
                return _error(message, error_kind=kind)
            server = shape_varz(result.payload)
            jetstream = shape_jetstream_varz(result.payload)
            health_result, health_err = nats_client.get_json(
                client,
                config,
                "/healthz",
                accept=(HTTPStatus.OK, HTTPStatus.SERVICE_UNAVAILABLE),
            )
            health = None
            if health_err is not None or health_result is None:
                warnings.append(
                    health_err.message if health_err is not None else "empty /healthz response"
                )
            else:
                health = shape_healthz(health_result.status, health_result.payload)
        return {
            "source": "nats",
            "available": True,
            "url": config.url,
            "server": server,
            "jetstream": jetstream,
            "health": health,
            "warnings": warnings,
        }
    except Exception as err:
        report_validation_failure(
            err, logger=logger, integration="nats", method="get_server_status"
        )
        return _error(str(err))


def get_connections(
    config: NatsConfig,
    sort: str = "pending",
    state: str = "open",
    limit: int = DEFAULT_CONNECTION_LIMIT,
) -> dict[str, Any]:
    """List client connections with server-reported totals and a summary."""
    if not config.is_configured:
        return _error("NATS is not configured (NATS_MONITOR_URL is required).")
    sort = sort.strip().lower() or "pending"
    if sort not in ALLOWED_CONNZ_SORT:
        return _error(f"Unsupported sort '{sort}'. Allowed: {sorted(ALLOWED_CONNZ_SORT)}")
    state = state.strip().lower() or "open"
    if state not in ALLOWED_CONNZ_STATE:
        return _error(f"Unsupported state '{state}'. Allowed: {sorted(ALLOWED_CONNZ_STATE)}")
    if sort in CLOSED_ONLY_SORTS and state != "closed":
        return _error(f"sort '{sort}' is only valid with state='closed'.")
    limit = clamp_limit(limit)
    try:
        warnings: list[str] = []
        with nats_client.build_client(config) as client:
            result, err = nats_client.get_json(
                client,
                config,
                "/connz",
                params={
                    "sort": sort,
                    "state": state,
                    "limit": str(limit),
                    "subs": "true",
                },
            )
            if err is not None or result is None:
                message = err.message if err is not None else "empty /connz response"
                kind = err.kind if err is not None else None
                return _error(message, error_kind=kind)
            payload = result.payload
            connections = [shape_connection(c) for c in payload.get("connections", [])]
            varz_result, varz_err = nats_client.get_json(client, config, "/varz")
            server_name: str | None = None
            slow_consumers: int | None = None
            max_connections: int | None = None
            current_connections: int | None = None
            if varz_err is not None or varz_result is None:
                warnings.append(
                    varz_err.message if varz_err is not None else "empty /varz response"
                )
            else:
                varz = shape_varz(varz_result.payload)
                server_name = varz["server_name"]
                slow_consumers = varz["slow_consumers"]
                max_connections = varz["max_connections"]
                current_connections = varz["connections"]
        return {
            "source": "nats",
            "available": True,
            "url": config.url,
            "server_name": server_name,
            "sort": sort,
            "state": state,
            "limit": limit,
            "total": payload.get("total", 0),
            "returned": payload.get("num_connections", 0),
            "connections": connections,
            "summary": summarize_connections(connections),
            "slow_consumers": slow_consumers,
            "max_connections": max_connections,
            "current_connections": current_connections,
            "warnings": warnings,
        }
    except Exception as err:
        report_validation_failure(err, logger=logger, integration="nats", method="get_connections")
        return _error(str(err))


def get_subscriptions(config: NatsConfig, subject: str = "") -> dict[str, Any]:
    """Return subscription statistics, or the matches for one subject."""
    if not config.is_configured:
        return _error("NATS is not configured (NATS_MONITOR_URL is required).")
    try:
        subject = subject.strip()
        params: dict[str, Any] = {"subs": "true"}
        if subject:
            params["test"] = subject
        with nats_client.build_client(config) as client:
            result, err = nats_client.get_json(client, config, "/subsz", params=params)
            if err is not None or result is None:
                message = err.message if err is not None else "empty /subsz response"
                kind = err.kind if err is not None else None
                return _error(message, error_kind=kind)
            shaped = shape_subsz(result.payload)
        if subject:
            return {
                "source": "nats",
                "available": True,
                "url": config.url,
                "subject": subject,
                "matches": shaped["subscriptions"],
                "match_count": len(shaped["subscriptions"]),
            }
        stats = {k: v for k, v in shaped.items() if k != "subscriptions"}
        summary = summarize_subscriptions(shaped["subscriptions"])
        top = sorted(
            shaped["subscriptions"],
            key=lambda s: int(s.get("msgs") or 0),
            reverse=True,
        )[:TOP_N]
        return {
            "source": "nats",
            "available": True,
            "url": config.url,
            "subject": "",
            "stats": stats,
            "listed": shaped["listed"],
            "summary": summary,
            "subscriptions": top,
        }
    except Exception as err:
        report_validation_failure(
            err, logger=logger, integration="nats", method="get_subscriptions"
        )
        return _error(str(err))


def get_jetstream_streams(config: NatsConfig, stream: str = "") -> dict[str, Any]:
    """List JetStream streams visible on one server."""
    if not config.is_configured:
        return _error("NATS is not configured (NATS_MONITOR_URL is required).")
    try:
        with nats_client.build_client(config) as client:
            varz_result, varz_err = nats_client.get_json(client, config, "/varz")
            if varz_err is not None or varz_result is None:
                message = varz_err.message if varz_err is not None else "empty /varz response"
                kind = varz_err.kind if varz_err is not None else None
                return _error(message, error_kind=kind)
            server = shape_varz(varz_result.payload)
            jetstream_varz = shape_jetstream_varz(varz_result.payload)
            server_name = server["server_name"]
            clustered = server["clustered"]
            meta = jetstream_varz.get("meta") or {}
            meta_leader = meta.get("leader")
            result, err = nats_client.get_json(
                client,
                config,
                "/jsz",
                params={"streams": "true", "config": "true"},
            )
            if err is not None or result is None:
                message = err.message if err is not None else "empty /jsz response"
                kind = err.kind if err is not None else None
                return _error(message, error_kind=kind)
            js = shape_jsz(result.payload)
            if not js["enabled"]:
                return {
                    "source": "nats",
                    "available": True,
                    "url": config.url,
                    "server_name": server_name,
                    "jetstream_enabled": False,
                    "streams": [],
                    "hint": JETSTREAM_DISABLED_HINT.format(server_name=server_name),
                    "note": None,
                }
            shaped: list[dict[str, Any]] = []
            for account in result.payload.get("account_details", []):
                for detail in account.get("stream_detail", []):
                    shaped.append(
                        shape_stream(
                            detail,
                            account=account.get("name", ""),
                            server_name=server_name,
                        )
                    )
            wanted = stream.strip()
            warnings: list[str] = []
            if wanted:
                shaped = [s for s in shaped if s["name"] == wanted]
                if not shaped:
                    warnings.append(f"stream '{wanted}' not found on server '{server_name}'")
            shaped.sort(key=lambda s: (not s["authoritative"], -(s["messages"] or 0), s["name"]))
            api = js["api"]
            return {
                "source": "nats",
                "available": True,
                "url": config.url,
                "server_name": server_name,
                "view": "server",
                "clustered": clustered,
                "meta_leader": meta_leader,
                "jetstream_enabled": True,
                "filter": wanted,
                "totals": {
                    "streams": js["streams"],
                    "consumers": js["consumers"],
                    "messages": js["messages"],
                    "bytes": js["bytes"],
                    "storage_bytes": js["storage"],
                    "memory_bytes": js["memory"],
                    "api_errors": api["errors"],
                },
                "streams": shaped,
                "note": PER_SERVER_NOTE.format(server_name=server_name) if clustered else None,
                "warnings": warnings,
            }
    except Exception as err:
        report_validation_failure(
            err, logger=logger, integration="nats", method="get_jetstream_streams"
        )
        return _error(str(err))


def get_jetstream_consumers(
    config: NatsConfig, stream: str = "", consumer: str = ""
) -> dict[str, Any]:
    """List JetStream consumers visible on one server."""
    if not config.is_configured:
        return _error("NATS is not configured (NATS_MONITOR_URL is required).")
    try:
        with nats_client.build_client(config) as client:
            varz_result, varz_err = nats_client.get_json(client, config, "/varz")
            if varz_err is not None or varz_result is None:
                message = varz_err.message if varz_err is not None else "empty /varz response"
                kind = varz_err.kind if varz_err is not None else None
                return _error(message, error_kind=kind)
            server = shape_varz(varz_result.payload)
            jetstream_varz = shape_jetstream_varz(varz_result.payload)
            server_name = server["server_name"]
            clustered = server["clustered"]
            meta = jetstream_varz.get("meta") or {}
            meta_leader = meta.get("leader")
            result, err = nats_client.get_json(
                client,
                config,
                "/jsz",
                params={"streams": "true", "consumers": "true", "config": "true"},
            )
            if err is not None or result is None:
                message = err.message if err is not None else "empty /jsz response"
                kind = err.kind if err is not None else None
                return _error(message, error_kind=kind)
            js = shape_jsz(result.payload)
            if not js["enabled"]:
                return {
                    "source": "nats",
                    "available": True,
                    "url": config.url,
                    "server_name": server_name,
                    "jetstream_enabled": False,
                    "consumers": [],
                    "hint": JETSTREAM_DISABLED_HINT.format(server_name=server_name),
                    "note": None,
                }
            shaped: list[dict[str, Any]] = []
            for account in result.payload.get("account_details", []):
                for stream_detail in account.get("stream_detail", []):
                    last_seq = int((stream_detail.get("state") or {}).get("last_seq", 0))
                    for detail in stream_detail.get("consumer_detail", []):
                        shaped.append(
                            shape_consumer(
                                detail,
                                stream_last_seq=last_seq,
                                server_name=server_name,
                            )
                        )
            wanted_stream = stream.strip()
            wanted_consumer = consumer.strip()
            warnings: list[str] = []
            if wanted_stream:
                shaped = [c for c in shaped if c["stream"] == wanted_stream]
            if wanted_consumer:
                shaped = [c for c in shaped if c["name"] == wanted_consumer]
            if (wanted_stream or wanted_consumer) and not shaped:
                warnings.append(
                    "no consumers match stream "
                    f"'{wanted_stream}' consumer '{wanted_consumer}' "
                    f"on server '{server_name}'"
                )
            shaped.sort(
                key=lambda c: (
                    not c["authoritative"],
                    -((c["num_pending"] or 0) + (c["num_ack_pending"] or 0)),
                    c["stream"],
                    c["name"],
                )
            )
            summary = {
                "consumers": len(shaped),
                "authoritative": sum(1 for c in shaped if c["authoritative"]),
                "with_backlog": sum(1 for c in shaped if (c["num_pending"] or 0) > 0),
                "with_ack_pending": sum(1 for c in shaped if (c["num_ack_pending"] or 0) > 0),
                "with_redeliveries": sum(1 for c in shaped if (c["num_redelivered"] or 0) > 0),
                "never_delivered": sum(1 for c in shaped if "never_delivered" in c["flags"]),
                "pending_total": sum(int(c["num_pending"] or 0) for c in shaped),
                "ack_pending_total": sum(int(c["num_ack_pending"] or 0) for c in shaped),
            }
            return {
                "source": "nats",
                "available": True,
                "url": config.url,
                "server_name": server_name,
                "view": "server",
                "clustered": clustered,
                "meta_leader": meta_leader,
                "jetstream_enabled": True,
                "filters": {"stream": wanted_stream, "consumer": wanted_consumer},
                "consumers": shaped,
                "summary": summary,
                "note": PER_SERVER_NOTE.format(server_name=server_name) if clustered else None,
                "warnings": warnings,
            }
    except Exception as err:
        report_validation_failure(
            err, logger=logger, integration="nats", method="get_jetstream_consumers"
        )
        return _error(str(err))


def get_cluster_status(config: NatsConfig) -> dict[str, Any]:
    """Return cluster topology from one nats-server."""
    if not config.is_configured:
        return _error("NATS is not configured (NATS_MONITOR_URL is required).")
    try:
        warnings: list[str] = []
        with nats_client.build_client(config) as client:
            varz_result, varz_err = nats_client.get_json(client, config, "/varz")
            if varz_err is not None or varz_result is None:
                message = varz_err.message if varz_err is not None else "empty /varz response"
                kind = varz_err.kind if varz_err is not None else None
                return _error(message, error_kind=kind)
            server = shape_varz(varz_result.payload)
            jetstream = shape_jetstream_varz(varz_result.payload)

            routes: dict[str, Any] | None = None
            routez_result, routez_err = nats_client.get_json(client, config, "/routez")
            if routez_err is not None or routez_result is None:
                warnings.append(
                    routez_err.message if routez_err is not None else "empty /routez response"
                )
            else:
                shaped_routes = [shape_route(r) for r in routez_result.payload.get("routes", [])]
                routes = {
                    "summary": summarize_routes(shaped_routes),
                    "routes": shaped_routes,
                }

            gateways: dict[str, Any] | None = None
            gatewayz_result, gatewayz_err = nats_client.get_json(client, config, "/gatewayz")
            if gatewayz_err is not None or gatewayz_result is None:
                warnings.append(
                    gatewayz_err.message if gatewayz_err is not None else "empty /gatewayz response"
                )
            else:
                gateways = shape_gatewayz(gatewayz_result.payload)

            leafnodes: dict[str, Any] | None = None
            leafz_result, leafz_err = nats_client.get_json(client, config, "/leafz")
            if leafz_err is not None or leafz_result is None:
                warnings.append(
                    leafz_err.message if leafz_err is not None else "empty /leafz response"
                )
            else:
                leafnodes = shape_leafz(leafz_result.payload)

            meta = None
            jsz_result, jsz_err = nats_client.get_json(client, config, "/jsz")
            if jsz_err is not None or jsz_result is None:
                warnings.append(jsz_err.message if jsz_err is not None else "empty /jsz response")
            else:
                meta = shape_jsz(jsz_result.payload)["meta_cluster"]
        is_meta_leader = meta is not None and meta.get("leader") == server["server_name"]
        return {
            "source": "nats",
            "available": True,
            "url": config.url,
            "server_name": server["server_name"],
            "clustered": server["clustered"],
            "cluster": server["cluster"],
            "routes": routes,
            "gateways": gateways,
            "leafnodes": leafnodes,
            "jetstream_meta": meta,
            "is_meta_leader": is_meta_leader,
            "jetstream_enabled": jetstream["enabled"],
            "warnings": warnings,
        }
    except Exception as err:
        report_validation_failure(
            err, logger=logger, integration="nats", method="get_cluster_status"
        )
        return _error(str(err))
