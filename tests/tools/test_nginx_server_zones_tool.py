"""Tests for get_nginx_server_zones (function-based, @tool decorated)."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from integrations.nginx import NginxConfig
from integrations.nginx import client as nginx_client
from integrations.nginx.tools.nginx_server_zones_tool import (
    _NGINX_INJECTED,
    get_nginx_server_zones,
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
    "/api/9/http/server_zones": {
        "hg.nginx.org": {
            "processing": 0,
            "requests": 55331,
            "responses": {"1xx": 0, "2xx": 55330, "3xx": 0, "4xx": 0, "5xx": 0, "total": 55330},
            "discarded": 1,
            "received": 2904825,
            "sent": 7176625425,
        }
    },
}


class TestNginxServerZonesToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_nginx_server_zones.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_nginx_server_zones.__opensre_registered_tool__
    assert rt.name == "get_nginx_server_zones"
    assert rt.source == "nginx"
    assert rt.injected_params == _NGINX_INJECTED
    properties = rt.public_input_schema.get("properties") or {}
    assert set(properties) == {"zone"}


def test_run_happy_path() -> None:
    fake_result = {"source": "nginx", "available": True, "zones": []}
    with patch(
        "integrations.nginx.tools.nginx_server_zones_tool.get_server_zones",
        return_value=fake_result,
    ):
        result = get_nginx_server_zones(host="nginx.test")
    assert result["available"] is True


def test_full_path_plus(patched_client) -> None:  # type: ignore[no-untyped-def]
    patched_client(PLUS_ROUTES)
    result = get_nginx_server_zones(host="nginx.test")
    assert result["available"] is True
    assert result["zones_total"] == 1
    assert result["zones"][0]["zone"] == "hg.nginx.org"


def test_open_source_unavailable(patched_client) -> None:  # type: ignore[no-untyped-def]
    patched_client({})
    result = get_nginx_server_zones(host="nginx.test")
    assert result["available"] is False
    assert "NGINX Plus" in result["error"]
