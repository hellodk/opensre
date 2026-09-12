"""Tests for get_nginx_server_status (function-based, @tool decorated)."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from integrations.nginx import NginxConfig
from integrations.nginx import client as nginx_client
from integrations.nginx.tools.nginx_server_status_tool import (
    _NGINX_INJECTED,
    get_nginx_server_status,
)
from tests.tools.conftest import BaseToolContract


def _mock_transport(routes: dict[str, Any]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in routes:
            payload = routes[path]
            if isinstance(payload, httpx.Response):
                return payload
            if isinstance(payload, str):
                return httpx.Response(
                    HTTPStatus.OK, text=payload, headers={"server": "nginx/1.27.5"}
                )
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


STUB_BODY = (
    "Active connections: 1 \n"
    "server accepts handled requests\n"
    " 7 7 7 \n"
    "Reading: 0 Writing: 1 Waiting: 0 \n"
)


class TestNginxServerStatusToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_nginx_server_status.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_nginx_server_status.__opensre_registered_tool__
    assert rt.name == "get_nginx_server_status"
    assert rt.source == "nginx"
    assert rt.injected_params == _NGINX_INJECTED
    properties = rt.public_input_schema.get("properties") or {}
    assert properties == {}


def test_run_happy_path() -> None:
    fake_result = {"source": "nginx", "available": True, "edition": "oss"}
    with patch(
        "integrations.nginx.tools.nginx_server_status_tool.get_server_status",
        return_value=fake_result,
    ):
        result = get_nginx_server_status(host="nginx.test")
    assert result["available"] is True
    assert result["edition"] == "oss"


def test_full_path_oss(patched_client) -> None:  # type: ignore[no-untyped-def]
    patched_client({"/nginx_status": STUB_BODY})
    result = get_nginx_server_status(host="nginx.test")
    assert result["available"] is True
    assert result["edition"] == "oss"
    assert result["version"] == "1.27.5"
    assert result["connections"]["active"] == 1


def test_neither_endpoint_unavailable(patched_client) -> None:  # type: ignore[no-untyped-def]
    patched_client({})
    result = get_nginx_server_status(host="nginx.test")
    assert result["available"] is False
    assert "/nginx_status" in result["error"]
    assert "/api" in result["error"]
