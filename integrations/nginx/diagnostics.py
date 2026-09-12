"""Read-only nginx diagnostics: the get_* functions behind the six tools."""

from __future__ import annotations

import logging
from typing import Any

import integrations.nginx.client as nginx_client
from core.tool_framework.utils import tool_unavailable
from integrations._validation_helpers import report_validation_failure
from integrations.nginx.config import NginxConfig
from integrations.nginx.logs import (
    DEFAULT_MIN_ERROR_LEVEL,
    clamp_lines,
    open_log,
    summarize_access_log,
    summarize_error_log,
    tail_lines,
)
from integrations.nginx.plus_api import (
    detect_api_version,
    fetch_plus,
    shape_caches,
    shape_connections,
    shape_nginx_info,
    shape_requests,
    shape_server_zones,
    shape_upstreams,
)
from integrations.nginx.stub_status import parse_stub_status

logger = logging.getLogger(__name__)

PLUS_REQUIRED_MESSAGE = (
    "NGINX Plus API not found at {api_path} — this tool requires NGINX Plus. "
    "Open-source nginx exposes only stub_status; use get_nginx_server_status instead."
)


def _error(message: str, **extra: Any) -> dict[str, Any]:
    return tool_unavailable("nginx", message, **extra)


def _unexpected(fn_name: str, err: Exception) -> dict[str, Any]:
    report_validation_failure(err, logger=logger, integration="nginx", method=fn_name)
    return _error(str(err))


def _neither_answered(config: NginxConfig, stub_reason: str) -> dict[str, Any]:
    return _error(
        f"Neither stub_status at {config.stub_status_path} ({stub_reason}) "
        f"nor the NGINX Plus API at {config.api_path} answered on "
        f"{config.base_url}. Enable `stub_status` (open-source) or the "
        f"`api` directive (NGINX Plus)."
    )


def get_server_status(config: NginxConfig) -> dict[str, Any]:
    """Return nginx edition, version, and live connection/request counters."""
    if not config.is_configured:
        return _error("Not configured.")
    try:
        with nginx_client.build_client(config) as client:
            stub_resp, stub_err = nginx_client.fetch_text(client, config.stub_status_path)
            if stub_err is not None and stub_err.kind in (
                nginx_client.FetchErrorKind.AUTH,
                nginx_client.FetchErrorKind.TRANSPORT,
            ):
                return _error(stub_err.message)
            if stub_err is None and stub_resp is not None:
                parsed = parse_stub_status(stub_resp.text)
                if parsed is not None:
                    result: dict[str, Any] = {
                        "source": "nginx",
                        "available": True,
                        "edition": "oss",
                        "status_source": "stub_status",
                        "version": nginx_client.nginx_version_from_server_header(
                            stub_resp.server_header
                        ),
                        "build": None,
                        "api_version": None,
                        "connections": {
                            "active": parsed.active_connections,
                            "accepted": parsed.accepts,
                            "handled": parsed.handled,
                            "dropped": parsed.dropped,
                            "reading": parsed.reading,
                            "writing": parsed.writing,
                            "waiting": parsed.waiting,
                            "idle": None,
                        },
                        "requests": {"total": parsed.requests, "current": None},
                    }
                    api_version, api_err = detect_api_version(client, config.api_path)
                    if api_err is None and api_version is not None:
                        info, info_err = fetch_plus(client, config.api_path, api_version, "nginx")
                        if info_err is None and isinstance(info, dict):
                            shaped = shape_nginx_info(info)
                            result["edition"] = "plus"
                            result["api_version"] = api_version
                            result["version"] = shaped["version"]
                            result["build"] = shaped["build"]
                    return result
                stub_reason = (
                    f"stub_status at {config.stub_status_path} returned an unrecognised body"
                )
            elif stub_err is not None:
                stub_reason = stub_err.message
            else:
                stub_reason = f"stub_status at {config.stub_status_path} returned no data"
            api_version, api_err = detect_api_version(client, config.api_path)
            if api_err is not None:
                if api_err.kind is nginx_client.FetchErrorKind.NOT_FOUND:
                    return _neither_answered(config, stub_reason)
                return _error(api_err.message)
            assert api_version is not None
            info, info_err = fetch_plus(client, config.api_path, api_version, "nginx")
            if info_err is not None:
                return _error(info_err.message)
            connections, connections_err = fetch_plus(
                client, config.api_path, api_version, "connections"
            )
            if connections_err is not None:
                return _error(connections_err.message)
            requests_data, requests_err = fetch_plus(
                client, config.api_path, api_version, "http/requests"
            )
            if requests_err is not None:
                return _error(requests_err.message)
            shaped_info = shape_nginx_info(info if isinstance(info, dict) else {})
            shaped_connections = shape_connections(
                connections if isinstance(connections, dict) else {}
            )
            shaped_requests = shape_requests(
                requests_data if isinstance(requests_data, dict) else {}
            )
            return {
                "source": "nginx",
                "available": True,
                "edition": "plus",
                "status_source": "plus_api",
                "version": shaped_info["version"],
                "build": shaped_info["build"],
                "api_version": api_version,
                "connections": {
                    "active": shaped_connections["active"],
                    "accepted": shaped_connections["accepted"],
                    "handled": None,
                    "dropped": shaped_connections["dropped"],
                    "reading": None,
                    "writing": None,
                    "waiting": None,
                    "idle": shaped_connections["idle"],
                },
                "requests": {
                    "total": shaped_requests["total"],
                    "current": shaped_requests["current"],
                },
            }
    except Exception as err:
        return _unexpected("get_server_status", err)
    return _error("nginx server status check returned no result.")


def _plus_version(client: Any, config: NginxConfig) -> tuple[int | None, dict[str, Any] | None]:
    """Detect the Plus API version or return an error payload."""
    api_version, api_err = detect_api_version(client, config.api_path)
    if api_err is not None:
        if api_err.kind is nginx_client.FetchErrorKind.NOT_FOUND:
            return None, _error(
                PLUS_REQUIRED_MESSAGE.format(api_path=config.api_path), edition="oss"
            )
        return None, _error(api_err.message)
    return api_version, None


def get_upstream_health(config: NginxConfig, upstream: str = "") -> dict[str, Any]:
    """Return NGINX Plus upstream and peer health."""
    if not config.is_configured:
        return _error("Not configured.")
    upstream_filter = str(upstream or "").strip()
    try:
        with nginx_client.build_client(config) as client:
            api_version, err_payload = _plus_version(client, config)
            if err_payload is not None:
                return err_payload
            assert api_version is not None
            data, fetch_err = fetch_plus(client, config.api_path, api_version, "http/upstreams")
            if fetch_err is not None:
                return _error(fetch_err.message)
            shaped = shape_upstreams(data if isinstance(data, dict) else {}, upstream_filter)
            result: dict[str, Any] = {
                "source": "nginx",
                "available": True,
                "api_version": api_version,
                "upstreams": shaped,
                "upstreams_total": len(shaped),
                "peers_down_total": sum(u["peers_down"] for u in shaped),
                "filter": upstream_filter,
            }
            if upstream_filter and not shaped:
                result["warning"] = f"upstream {upstream_filter} not found"
            return result
    except Exception as err:
        return _unexpected("get_upstream_health", err)


def get_server_zones(config: NginxConfig, zone: str = "") -> dict[str, Any]:
    """Return NGINX Plus per-server-zone traffic."""
    if not config.is_configured:
        return _error("Not configured.")
    zone_filter = str(zone or "").strip()
    try:
        with nginx_client.build_client(config) as client:
            api_version, err_payload = _plus_version(client, config)
            if err_payload is not None:
                return err_payload
            assert api_version is not None
            data, fetch_err = fetch_plus(client, config.api_path, api_version, "http/server_zones")
            if fetch_err is not None:
                return _error(fetch_err.message)
            shaped = shape_server_zones(data if isinstance(data, dict) else {}, zone_filter)
            return {
                "source": "nginx",
                "available": True,
                "api_version": api_version,
                "zones": shaped,
                "zones_total": len(shaped),
                "responses_5xx_total": sum(z["responses_5xx"] for z in shaped),
                "filter": zone_filter,
            }
    except Exception as err:
        return _unexpected("get_server_zones", err)


def get_cache_status(config: NginxConfig) -> dict[str, Any]:
    """Return NGINX Plus content-cache status per cache zone."""
    if not config.is_configured:
        return _error("Not configured.")
    try:
        with nginx_client.build_client(config) as client:
            api_version, err_payload = _plus_version(client, config)
            if err_payload is not None:
                return err_payload
            assert api_version is not None
            data, fetch_err = fetch_plus(client, config.api_path, api_version, "http/caches")
            if fetch_err is not None:
                return _error(fetch_err.message)
            shaped = shape_caches(data if isinstance(data, dict) else {})
            return {
                "source": "nginx",
                "available": True,
                "api_version": api_version,
                "caches": shaped,
                "caches_total": len(shaped),
            }
    except Exception as err:
        return _unexpected("get_cache_status", err)


def get_error_log(
    config: NginxConfig,
    lines: int | None = None,
    min_level: str = DEFAULT_MIN_ERROR_LEVEL,
    contains: str = "",
) -> dict[str, Any]:
    """Tail the local error.log and return parsed entries plus counts."""
    if not config.is_configured:
        return _error("Not configured.")
    try:
        resolved, open_err = open_log(config.error_log_path)
        if open_err is not None or resolved is None:
            return _error(
                open_err or f"log file not found: {config.error_log_path}",
                path=config.error_log_path,
            )
        wanted = clamp_lines(lines)
        summary = summarize_error_log(
            tail_lines(resolved, wanted),
            min_level=min_level,
            contains=contains or "",
        )
        return {
            "source": "nginx",
            "available": True,
            "path": config.error_log_path,
            "lines_requested": wanted,
            **summary,
        }
    except Exception as err:
        return _unexpected("get_error_log", err)


def get_access_log_summary(config: NginxConfig, lines: int | None = None) -> dict[str, Any]:
    """Tail the local access.log and summarize status paths and timings."""
    if not config.is_configured:
        return _error("Not configured.")
    try:
        resolved, open_err = open_log(config.access_log_path)
        if open_err is not None or resolved is None:
            return _error(
                open_err or f"log file not found: {config.access_log_path}",
                path=config.access_log_path,
            )
        wanted = clamp_lines(lines)
        summary = summarize_access_log(tail_lines(resolved, wanted))
        return {
            "source": "nginx",
            "available": True,
            "path": config.access_log_path,
            "lines_requested": wanted,
            **summary,
        }
    except Exception as err:
        return _unexpected("get_access_log_summary", err)
