"""nginx Upstream Health Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from integrations.nginx import (
    DEFAULT_NGINX_ACCESS_LOG_PATH,
    DEFAULT_NGINX_API_PATH,
    DEFAULT_NGINX_ERROR_LOG_PATH,
    DEFAULT_NGINX_PORT,
    DEFAULT_NGINX_STUB_STATUS_PATH,
    NginxConfig,
    get_upstream_health,
    nginx_extract_params,
    nginx_is_available,
)

_NGINX_INJECTED = (
    "host",
    "port",
    "ssl",
    "verify_ssl",
    "username",
    "password",
    "stub_status_path",
    "api_path",
    "access_log_path",
    "error_log_path",
)

_DOWN_PEER_STATES = ("down", "unavail", "unhealthy", "checking")


def _map_get_nginx_upstream_health(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite upstream count, down peers, and the first down peer names."""
    if not output.get("available"):
        return
    upstreams = output.get("upstreams") or []
    down_names: list[str] = []
    for upstream in upstreams:
        for peer in upstream.get("peers") or []:
            if peer.get("state") in _DOWN_PEER_STATES:
                down_names.append(str(peer.get("server") or peer.get("name") or ""))
            if len(down_names) >= 3:
                break
        if len(down_names) >= 3:
            break
    summary = (
        f"{output.get('upstreams_total', len(upstreams))} upstream(s), "
        f"{output.get('peers_down_total', len(down_names))} peer(s) down"
    )
    if down_names:
        summary += f": {', '.join(down_names)}"
    record_evidence_entry(
        evidence,
        source="get_nginx_upstream_health",
        label="nginx Upstream Health",
        summary=summary,
    )


@tool(
    name="get_nginx_upstream_health",
    description="Return NGINX Plus upstream health: per upstream and per peer state (up/down/unavail/unhealthy), fails, unavailability count, health-check failures, 5xx responses and response times. Requires NGINX Plus; returns available=false on open-source nginx.",
    source="nginx",
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Investigating 502/504 Bad Gateway from an nginx reverse proxy",
        "Spotting a downed upstream peer before it drains traffic",
        "Correlating health-check failures with 5xx spikes per peer",
    ],
    is_available=nginx_is_available,
    injected_params=_NGINX_INJECTED,
    extract_params=nginx_extract_params,
    evidence_mapper=_map_get_nginx_upstream_health,
)
def get_nginx_upstream_health(
    host: str,
    port: int = DEFAULT_NGINX_PORT,
    ssl: bool = False,
    verify_ssl: bool = True,
    username: str = "",
    password: str = "",
    stub_status_path: str = DEFAULT_NGINX_STUB_STATUS_PATH,
    api_path: str = DEFAULT_NGINX_API_PATH,
    access_log_path: str = DEFAULT_NGINX_ACCESS_LOG_PATH,
    error_log_path: str = DEFAULT_NGINX_ERROR_LOG_PATH,
    upstream: str = "",
) -> dict[str, Any]:
    """Return NGINX Plus upstream and peer health."""
    config = NginxConfig(
        host=host,
        port=port,
        ssl=ssl,
        verify_ssl=verify_ssl,
        username=username,
        password=password,
        stub_status_path=stub_status_path,
        api_path=api_path,
        access_log_path=access_log_path,
        error_log_path=error_log_path,
    )
    return get_upstream_health(config, upstream)
