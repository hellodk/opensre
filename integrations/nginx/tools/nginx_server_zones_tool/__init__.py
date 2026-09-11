"""nginx Server Zones Tool."""

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
    get_server_zones,
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


def _map_get_nginx_server_zones(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite zone count, total 5xx, and the worst zone by 5xx."""
    if not output.get("available"):
        return
    zones = output.get("zones") or []
    summary = (
        f"{output.get('zones_total', len(zones))} zone(s), "
        f"{output.get('responses_5xx_total', 0)} 5xx responses"
    )
    if zones and (zones[0].get("responses_5xx") or 0) > 0:
        summary += f", worst: {zones[0].get('zone')} ({zones[0].get('error_rate_pct')}%)"
    record_evidence_entry(
        evidence,
        source="get_nginx_server_zones",
        label="nginx Server Zones",
        summary=summary,
    )


@tool(
    name="get_nginx_server_zones",
    description="Return NGINX Plus per-server-zone traffic: in-flight requests, 1xx–5xx response counts, error rate, discarded requests and bytes in/out, sorted by 5xx count. Requires NGINX Plus; returns available=false on open-source nginx.",
    source="nginx",
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Finding which server zone is emitting 5xx during an outage",
        "Comparing per-zone error rates across virtual hosts",
        "Tracking discarded requests and bytes per server zone",
    ],
    is_available=nginx_is_available,
    injected_params=_NGINX_INJECTED,
    extract_params=nginx_extract_params,
    evidence_mapper=_map_get_nginx_server_zones,
)
def get_nginx_server_zones(
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
    zone: str = "",
) -> dict[str, Any]:
    """Return NGINX Plus per-server-zone traffic."""
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
    return get_server_zones(config, zone)
