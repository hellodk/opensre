"""nginx Server Status Tool."""

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
    get_server_status,
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


def _map_get_nginx_server_status(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite edition, version, active connections, and total requests."""
    if not output.get("available"):
        return
    connections = output.get("connections") or {}
    requests = output.get("requests") or {}
    summary = (
        f"nginx {output.get('version', 'unknown')} ({output.get('edition', 'unknown')}): "
        f"{connections.get('active')} active connections, "
        f"{requests.get('total')} total requests"
    )
    dropped = connections.get("dropped") or 0
    if dropped > 0:
        summary += f", {dropped} dropped"
    record_evidence_entry(
        evidence,
        source="get_nginx_server_status",
        label="nginx Server Status",
        summary=summary,
    )


@tool(
    name="get_nginx_server_status",
    description="Return nginx edition (open-source or Plus), version, and live connection/request counters: active, accepted, handled, dropped connections; reading/writing/waiting (stub_status) or idle (Plus); total requests. Works on any nginx exposing stub_status or the NGINX Plus API.",
    source="nginx",
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Checking whether nginx is dropping connections (accepts != handled)",
        "Confirming the nginx edition and version during an incident",
        "Reading live connection and request counters from stub_status",
    ],
    is_available=nginx_is_available,
    injected_params=_NGINX_INJECTED,
    extract_params=nginx_extract_params,
    evidence_mapper=_map_get_nginx_server_status,
)
def get_nginx_server_status(
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
) -> dict[str, Any]:
    """Return nginx edition, version, and live connection/request counters."""
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
    return get_server_status(config)
