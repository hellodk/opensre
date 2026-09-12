"""nginx Access Log Summary Tool."""

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
    get_access_log_summary,
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


def _map_get_nginx_access_log_summary(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite request counts by error class and the p95 request time."""
    if not output.get("available"):
        return
    classes = output.get("status_classes") or {}
    summary = (
        f"{output.get('parsed', 0)} requests: "
        f"{classes.get('5xx', 0)} 5xx, {classes.get('4xx', 0)} 4xx"
    )
    request_time = output.get("request_time") or {}
    if request_time.get("present"):
        summary += f", p95 {request_time.get('p95')}s"
    record_evidence_entry(
        evidence,
        source="get_nginx_access_log_summary",
        label="nginx Access Log Summary",
        summary=summary,
    )


@tool(
    name="get_nginx_access_log_summary",
    description="Tail the local nginx access.log (bounded, from the end) and summarize it: status-class distribution, top paths by 5xx/4xx, top client IPs and user agents, bytes sent, and request-time percentiles when the log format includes $request_time. Reads a file on the host running OpenSRE; for nginx in Kubernetes use kubernetes_get_pod_logs instead.",
    source="nginx",
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Finding which paths return the most 5xx behind nginx",
        "Spotting abusive client IPs by request volume in access.log",
        "Reading p50/p95 latency when the log format has $request_time",
    ],
    is_available=nginx_is_available,
    injected_params=_NGINX_INJECTED,
    extract_params=nginx_extract_params,
    evidence_mapper=_map_get_nginx_access_log_summary,
)
def get_nginx_access_log_summary(
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
) -> dict[str, Any]:
    """Tail the local access.log and summarize status paths and timings."""
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
    return get_access_log_summary(config, lines)
