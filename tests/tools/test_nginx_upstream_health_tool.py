"""Tests for get_nginx_upstream_health (function-based, @tool decorated)."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from integrations.nginx import NginxConfig
from integrations.nginx import client as nginx_client
from integrations.nginx.tools.nginx_upstream_health_tool import (
    _NGINX_INJECTED,
    get_nginx_upstream_health,
)
from tests.tools.conftest import BaseToolContract


def _mock_transport(routes: dict[str, Any]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in routes:
            payload = routes[path]
            if isinstance(payload, httpx.Response):
                return payload
            return httpx.Response(HTTPStatus.OK, json=payload)
        return httpx.Response(HTTPStatus.NOT_FOUND, text="not found")

    return httpx.MockTransport(handler)


@pytest.fixture
def patched_client(monkeypatch: pytest.MonkeyPatch):
    def install(routes: dict[str, Any]) -> None:
        def _fake_client(config: NginxConfig) -> httpx.Client:
            return httpx.Client(base_url=config.base_url, transport=_mock_transport(routes))

        monkeypatch.setattr(nginx_client, "build_client", _fake_client)

    return install


PLUS_ROUTES = {
    "/api/": [1, 2, 3, 4, 5, 6, 7, 8, 9],
    "/api/9/http/upstreams": {
        "backend": {
            "peers": [
                {
                    "id": 0,
                    "server": "10.0.0.41:8084",
                    "name": "10.0.0.41:8084",
                    "backup": False,
                    "weight": 1,
                    "state": "up",
                    "active": 0,
                    "requests": 100,
                    "responses": {"5xx": 0, "total": 100},
                    "fails": 0,
                    "unavail": 0,
                    "downtime": 0,
                    "selected": "2026-09-09T20:10:21Z",
                }
            ],
            "keepalive": 0,
            "zombies": 0,
            "zone": "backend",
        }
    },
}


class TestNginxUpstreamHealthToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_nginx_upstream_health.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_nginx_upstream_health.__opensre_registered_tool__
    assert rt.name == "get_nginx_upstream_health"
    assert rt.source == "nginx"
    assert rt.injected_params == _NGINX_INJECTED
    properties = rt.public_input_schema.get("properties") or {}
    assert set(properties) == {"upstream"}


def test_run_happy_path() -> None:
    fake_result = {"source": "nginx", "available": True, "upstreams": []}
    with patch(
        "integrations.nginx.tools.nginx_upstream_health_tool.get_upstream_health",
        return_value=fake_result,
    ):
        result = get_nginx_upstream_health(host="nginx.test")
    assert result["available"] is True


def test_full_path_plus(patched_client) -> None:  # type: ignore[no-untyped-def]
    patched_client(PLUS_ROUTES)
    result = get_nginx_upstream_health(host="nginx.test")
    assert result["available"] is True
    assert result["upstreams_total"] == 1
    assert result["upstreams"][0]["peers_up"] == 1


def test_open_source_unavailable(patched_client) -> None:  # type: ignore[no-untyped-def]
    patched_client({})
    result = get_nginx_upstream_health(host="nginx.test")
    assert result["available"] is False
    assert "NGINX Plus" in result["error"]
