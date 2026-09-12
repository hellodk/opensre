"""Every nginx connection/log param is injected, never set by the LLM.

``host``/``port``/credentials/paths are resolved from integration config
(env/store) like other vendors' credentials. When the model can set them it
can clobber the resolved value with a schema default and the tool connects
to the wrong endpoint (mirrors the live YugabyteDB incident where the agent
passed ``{"port": <default>}``).
"""

from __future__ import annotations

import inspect

import pytest

from integrations.nginx.tools.nginx_access_log_summary_tool import (
    get_nginx_access_log_summary,
)
from integrations.nginx.tools.nginx_cache_status_tool import get_nginx_cache_status
from integrations.nginx.tools.nginx_error_log_tool import get_nginx_error_log
from integrations.nginx.tools.nginx_server_status_tool import get_nginx_server_status
from integrations.nginx.tools.nginx_server_zones_tool import get_nginx_server_zones
from integrations.nginx.tools.nginx_upstream_health_tool import (
    get_nginx_upstream_health,
)

_NGINX_TOOLS = [
    get_nginx_server_status,
    get_nginx_upstream_health,
    get_nginx_server_zones,
    get_nginx_cache_status,
    get_nginx_error_log,
    get_nginx_access_log_summary,
]

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

_LLM_VISIBLE = {
    "get_nginx_server_status": set(),
    "get_nginx_upstream_health": {"upstream"},
    "get_nginx_server_zones": {"zone"},
    "get_nginx_cache_status": set(),
    "get_nginx_error_log": {"lines", "min_level", "contains"},
    "get_nginx_access_log_summary": {"lines"},
}


@pytest.mark.parametrize("fn", _NGINX_TOOLS, ids=lambda fn: fn.__name__)
def test_connection_params_not_exposed_to_llm(fn) -> None:
    rt = fn.__opensre_registered_tool__
    properties = rt.public_input_schema.get("properties") or {}
    for name in _NGINX_INJECTED:
        assert name in rt.injected_params
        assert name not in properties
        assert name in inspect.signature(fn).parameters


@pytest.mark.parametrize("fn", _NGINX_TOOLS, ids=lambda fn: fn.__name__)
def test_llm_visible_params_match_contract(fn) -> None:
    rt = fn.__opensre_registered_tool__
    properties = rt.public_input_schema.get("properties") or {}
    assert set(properties) == _LLM_VISIBLE[fn.__name__]
