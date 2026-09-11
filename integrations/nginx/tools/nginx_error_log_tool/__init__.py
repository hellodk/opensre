"""nginx Error Log Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from integrations.nginx import (
    DEFAULT_MIN_ERROR_LEVEL,
    DEFAULT_NGINX_ACCESS_LOG_PATH,
    DEFAULT_NGINX_API_PATH,
    DEFAULT_NGINX_ERROR_LOG_PATH,
    DEFAULT_NGINX_PORT,
    DEFAULT_NGINX_STUB_STATUS_PATH,
    NginxConfig,
    get_error_log,
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


def _map_get_nginx_error_log(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite matched entries and the most repeated error message."""
    if not output.get("available"):
        return
    summary = (
        f"{output.get('matched', 0)} entries at {output.get('min_level')}+ "
        f"in last {output.get('lines_read', 0)} lines"
    )
    top_messages = output.get("top_messages") or []
    if top_messages:
        summary += f", top: {str(top_messages[0].get('message', ''))[:80]}"
    record_evidence_entry(
        evidence,
        source="get_nginx_error_log",
        label="nginx Error Log",
        summary=summary,
    )


@tool(
    name="get_nginx_error_log",
    description="Tail the local nginx error.log (bounded, from the end) and return parsed entries with level, pid, client, request, upstream and host context, plus per-level counts and the most repeated messages. Reads a file on the host running OpenSRE; for nginx in Kubernetes use kubernetes_get_pod_logs instead.",
    source="nginx",
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Investigating 502/504 Bad Gateway from an nginx reverse proxy",
        "Finding repeated upstream connection failures in error.log",
        "Counting emerg/alert/crit lines before a nginx restart",
    ],
    is_available=nginx_is_available,
    injected_params=_NGINX_INJECTED,
    extract_params=nginx_extract_params,
    evidence_mapper=_map_get_nginx_error_log,
)
def get_nginx_error_log(
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
    lines: int = 200,
    min_level: str = DEFAULT_MIN_ERROR_LEVEL,
    contains: str = "",
) -> dict[str, Any]:
    """Tail the local error.log and return parsed entries plus counts."""
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
    return get_error_log(config, lines, min_level, contains)
