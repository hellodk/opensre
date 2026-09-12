"""Tests for the Keycloak server status tool."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from integrations.keycloak import client as keycloak_client
from integrations.keycloak.tools.keycloak_server_status_tool import (
    _KEYCLOAK_INJECTED,
    get_keycloak_server_status,
)
from tests.integrations.keycloak import load_fixture
from tests.tools.conftest import BaseToolContract

BASE_URL = "http://keycloak.test"
MANAGEMENT_URL = "http://keycloak.test:9000"
TOKEN_PATH = "/realms/opensre-demo/protocol/openid-connect/token"

INJECTED = {
    "url": BASE_URL,
    "management_url": MANAGEMENT_URL,
    "realm": "opensre-demo",
    "auth_realm": "",
    "client_id": "opensre",
    "client_secret": "service-secret-value",
    "verify_ssl": True,
}


class TestKeycloakServerStatusToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_keycloak_server_status.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_keycloak_server_status.__opensre_registered_tool__
    assert rt.name == "get_keycloak_server_status"
    assert rt.source == "keycloak"
    assert rt.injected_params == _KEYCLOAK_INJECTED
    assert set(rt.public_input_schema.get("properties") or {}) == set()


def _install(monkeypatch: pytest.MonkeyPatch, routes: dict[str, Any]) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in routes:
            payload = routes[path]
            if isinstance(payload, httpx.Response):
                return payload
            if isinstance(payload, str):
                return httpx.Response(HTTPStatus.OK, text=payload)
            return httpx.Response(HTTPStatus.OK, json=payload)
        return httpx.Response(HTTPStatus.NOT_FOUND, json={"error": "Realm not found."})

    transport = httpx.MockTransport(_handler)
    monkeypatch.setattr(
        keycloak_client,
        "build_client",
        lambda config: httpx.Client(base_url=config.url, transport=transport),
    )
    monkeypatch.setattr(
        keycloak_client,
        "build_management_client",
        lambda config: httpx.Client(
            base_url=config.management_url or config.url, transport=transport
        ),
    )


def test_run_happy_path() -> None:
    fake_result = {"source": "keycloak", "available": True, "version": "26.7.3"}
    with patch(
        "integrations.keycloak.tools.keycloak_server_status_tool.get_server_status",
        return_value=fake_result,
    ):
        result = get_keycloak_server_status(**INJECTED)
    assert result["available"] is True
    assert result["version"] == "26.7.3"


def test_full_path(monkeypatch: pytest.MonkeyPatch) -> None:
    token = dict(load_fixture("token_client_credentials.json"))
    token["access_token"] = "test-access-token"
    _install(
        monkeypatch,
        {
            TOKEN_PATH: token,
            "/admin/serverinfo": load_fixture("serverinfo_sa.json"),
            "/health/ready": load_fixture("mgmt_health_ready.json"),
            "/health/live": load_fixture("mgmt_health_live.json"),
            "/metrics": load_fixture("mgmt_metrics.txt"),
        },
    )
    result = get_keycloak_server_status(**INJECTED)
    assert result["available"] is True
    assert result["version_visible"] is False
    assert result["management"]["reachable"] is True


def test_unavailable_on_bad_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("token_error_bad_secret.json")
    _install(
        monkeypatch,
        {TOKEN_PATH: httpx.Response(HTTPStatus.UNAUTHORIZED, json=body)},
    )
    result = get_keycloak_server_status(**INJECTED)
    assert result["available"] is False
    assert "opensre" in result["error"]
    assert "service-secret-value" not in result["error"]
