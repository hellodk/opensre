"""nginx Cache Status Tool."""

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
    get_cache_status,
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


def _map_get_nginx_cache_status(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite cache count and the hit ratio of the first two caches."""
    if not output.get("available"):
        return
    caches = output.get("caches") or []
    summary = f"{output.get('caches_total', len(caches))} cache(s)"
    parts = [
        f"{cache.get('cache')} {cache.get('hit_ratio_pct')}% hit ratio" for cache in caches[:2]
    ]
    if parts:
        summary += ": " + ", ".join(parts)
    record_evidence_entry(
        evidence,
        source="get_nginx_cache_status",
        label="nginx Cache Status",
        summary=summary,
    )


@tool(
    name="get_nginx_cache_status",
    description="Return NGINX Plus content-cache status per cache zone: size vs max size, cold flag, hit/miss/expired/stale/bypass counts and hit ratio. Requires NGINX Plus; returns available=false on open-source nginx.",
    source="nginx",
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Checking cache hit ratio drops behind an nginx cache tier",
        "Spotting a cold cache zone after a deploy or restart",
        "Comparing size vs max size across nginx cache zones",
    ],
    is_available=nginx_is_available,
    injected_params=_NGINX_INJECTED,
    extract_params=nginx_extract_params,
    evidence_mapper=_map_get_nginx_cache_status,
)
def get_nginx_cache_status(
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
    """Return NGINX Plus content-cache status per cache zone."""
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
    return get_cache_status(config)
