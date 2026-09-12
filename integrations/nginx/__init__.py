"""Shared nginx integration helpers.

Covers both editions through one integration: open-source nginx is read via
``ngx_http_stub_status_module`` and NGINX Plus via its REST API. All
operations are read-only diagnostics — connection counters, upstream health,
server zones, cache status, and local log tails.
"""

from __future__ import annotations

from integrations.nginx.config import (
    DEFAULT_NGINX_ACCESS_LOG_PATH,
    DEFAULT_NGINX_API_PATH,
    DEFAULT_NGINX_ERROR_LOG_PATH,
    DEFAULT_NGINX_PORT,
    DEFAULT_NGINX_STUB_STATUS_PATH,
    DEFAULT_NGINX_TIMEOUT_SECONDS,
    NginxConfig,
    build_nginx_config,
    classify,
    nginx_config_from_env,
    nginx_extract_params,
    nginx_is_available,
)
from integrations.nginx.diagnostics import (
    get_access_log_summary,
    get_cache_status,
    get_error_log,
    get_server_status,
    get_server_zones,
    get_upstream_health,
)
from integrations.nginx.logs import (
    DEFAULT_LOG_TAIL_LINES,
    DEFAULT_MIN_ERROR_LEVEL,
    ERROR_LOG_LEVELS,
    MAX_LOG_TAIL_LINES,
)
from integrations.nginx.validation import NginxValidationResult, validate_nginx_config

__all__ = [
    "DEFAULT_LOG_TAIL_LINES",
    "DEFAULT_MIN_ERROR_LEVEL",
    "DEFAULT_NGINX_ACCESS_LOG_PATH",
    "DEFAULT_NGINX_API_PATH",
    "DEFAULT_NGINX_ERROR_LOG_PATH",
    "DEFAULT_NGINX_PORT",
    "DEFAULT_NGINX_STUB_STATUS_PATH",
    "DEFAULT_NGINX_TIMEOUT_SECONDS",
    "ERROR_LOG_LEVELS",
    "MAX_LOG_TAIL_LINES",
    "NginxConfig",
    "NginxValidationResult",
    "build_nginx_config",
    "classify",
    "get_access_log_summary",
    "get_cache_status",
    "get_error_log",
    "get_server_status",
    "get_server_zones",
    "get_upstream_health",
    "nginx_config_from_env",
    "nginx_extract_params",
    "nginx_is_available",
    "validate_nginx_config",
]
