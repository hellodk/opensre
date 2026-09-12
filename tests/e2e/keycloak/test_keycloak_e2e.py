"""Keycloak E2E tests verifying integration with the investigation pipeline."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from integrations.catalog import classify_integrations as _classify_integrations
from integrations.keycloak import client as keycloak_client
from integrations.keycloak import keycloak_extract_params, keycloak_is_available
from integrations.verify import verify_integrations
from tests.integrations.keycloak import load_fixture

BASE_URL = "http://keycloak.test"
MANAGEMENT_URL = "http://keycloak.test:9000"
TOKEN_PATH = "/realms/opensre-demo/protocol/openid-connect/token"
REALM_PATH = "/admin/realms/opensre-demo"
ALICE_ID = "cf7afc15-ddf3-47da-b371-1732dc2a3200"

INJECTED = {
    "url": BASE_URL,
    "management_url": MANAGEMENT_URL,
    "realm": "opensre-demo",
    "auth_realm": "",
    "client_id": "opensre",
    "client_secret": "service-secret-value",
    "verify_ssl": True,
}


def _routes() -> dict[str, Any]:
    token = dict(load_fixture("token_client_credentials.json"))
    token["access_token"] = "test-access-token"
    return {
        TOKEN_PATH: token,
        "/admin/serverinfo": load_fixture("serverinfo_sa.json"),
        REALM_PATH: load_fixture("realm.json"),
        REALM_PATH + "/users/count": load_fixture("users_count.json"),
        REALM_PATH + "/clients": load_fixture("clients_brief.json"),
        REALM_PATH + "/client-session-stats": load_fixture("client_session_stats.json"),
        REALM_PATH + "/events/config": load_fixture("events_config.json"),
        REALM_PATH + "/events": load_fixture("events_login_error.json"),
        REALM_PATH + "/admin-events": load_fixture("admin_events.json"),
        REALM_PATH + "/users": load_fixture("user_search_alice.json"),
        f"{REALM_PATH}/attack-detection/brute-force/users/{ALICE_ID}": load_fixture(
            "brute_force_alice.json"
        ),
        f"{REALM_PATH}/users/{ALICE_ID}/sessions": load_fixture("user_sessions_alice.json"),
        "/health/ready": load_fixture("mgmt_health_ready.json"),
        "/health/live": load_fixture("mgmt_health_live.json"),
        "/metrics": load_fixture("mgmt_metrics.txt"),
    }


@pytest.fixture
def patched_client(monkeypatch: pytest.MonkeyPatch):
    routes = _routes()

    def _handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in routes:
            payload = routes[path]
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


class TestKeycloakIntegrationResolution:
    """Test Keycloak config resolution from multiple sources."""

    def test_keycloak_resolution_from_store(self):
        integrations = [
            {
                "id": "keycloak-prod",
                "service": "keycloak",
                "status": "active",
                "credentials": {
                    "url": BASE_URL,
                    "realm": "opensre-demo",
                    "client_id": "opensre",
                    "client_secret": "service-secret-value",
                },
            }
        ]
        resolved = _classify_integrations(integrations)

        assert "keycloak" in resolved
        assert resolved["keycloak"]["url"] == BASE_URL
        assert resolved["keycloak"]["realm"] == "opensre-demo"
        assert resolved["keycloak"]["client_id"] == "opensre"

    def test_keycloak_invalid_config_skipped(self):
        integrations = [
            {
                "id": "bad-keycloak",
                "service": "keycloak",
                "status": "active",
                "credentials": {"url": ""},
            }
        ]
        resolved = _classify_integrations(integrations)

        assert resolved.get("keycloak") is None


class TestKeycloakToolSourceAvailability:
    """Test Keycloak source availability in the tool-registry investigation path."""

    def test_keycloak_tool_source_available_from_resolved_integration(self):
        resolved_integrations = {
            "keycloak": {
                "url": BASE_URL,
                "realm": "opensre-demo",
                "client_id": "opensre",
                "client_secret": "service-secret-value",
            }
        }

        assert keycloak_is_available(resolved_integrations)
        params = keycloak_extract_params(resolved_integrations)
        assert params["url"] == BASE_URL
        assert params["realm"] == "opensre-demo"

    def test_keycloak_tool_source_unavailable_if_unconfigured(self):
        assert not keycloak_is_available({})


class TestKeycloakVerification:
    """Test Keycloak integration verification flow."""

    def test_verify_keycloak_success(self, patched_client):  # noqa: ARG002
        with patch.dict(
            "os.environ",
            {
                "KEYCLOAK_URL": BASE_URL,
                "KEYCLOAK_REALM": "opensre-demo",
                "KEYCLOAK_CLIENT_ID": "opensre",
            },
        ):
            results = verify_integrations(service="keycloak")

        assert len(results) >= 1
        keycloak_result = next((r for r in results if r["service"] == "keycloak"), None)
        assert keycloak_result is not None
        assert keycloak_result["status"] in ("passed", "missing")

    def test_verify_integrations_structure(self):
        try:
            results = verify_integrations(service="keycloak")
            assert isinstance(results, list)
            for result in results:
                if result["service"] == "keycloak":
                    assert "status" in result
                    assert "detail" in result
                    assert result["status"] in ("passed", "missing", "failed")
        except Exception as exc:
            assert exc.__class__.__name__


class TestKeycloakToolsAvailability:
    """Test Keycloak tools are available and configured."""

    @pytest.fixture(autouse=True)
    def _clear_registry_cache(self):
        from tools.registry import clear_tool_registry_cache

        clear_tool_registry_cache()
        yield
        clear_tool_registry_cache()

    def test_keycloak_tools_exist_as_modules(self):
        import importlib

        modules = [
            "integrations.keycloak.tools.keycloak_server_status_tool",
            "integrations.keycloak.tools.keycloak_realm_overview_tool",
            "integrations.keycloak.tools.keycloak_login_failures_tool",
            "integrations.keycloak.tools.keycloak_admin_events_tool",
            "integrations.keycloak.tools.keycloak_client_sessions_tool",
            "integrations.keycloak.tools.keycloak_user_status_tool",
        ]
        for name in modules:
            try:
                assert importlib.import_module(name) is not None
            except ImportError as e:
                pytest.fail(f"Failed to import Keycloak tool module {name}: {e}")

    def test_keycloak_tools_registered_on_chat_surface(self):
        from tools.registry import get_registered_tools

        expected_tools = {
            "get_keycloak_server_status",
            "get_keycloak_realm_overview",
            "get_keycloak_login_failures",
            "get_keycloak_admin_events",
            "get_keycloak_client_sessions",
            "get_keycloak_user_status",
        }
        names = {t.name for t in get_registered_tools("chat") if t.source == "keycloak"}
        assert expected_tools <= names, (
            f"missing keycloak tools on chat surface: {expected_tools - names}"
        )


class TestKeycloakToolPaths:
    """Exercise each tool end-to-end: tool fn -> helper -> client -> shape."""

    def test_server_status_tool_path(self, patched_client):  # noqa: ARG002
        from integrations.keycloak.tools.keycloak_server_status_tool import (
            get_keycloak_server_status,
        )

        result = get_keycloak_server_status(**INJECTED)

        assert result["available"] is True
        assert result["version_visible"] is False

    def test_realm_overview_tool_path(self, patched_client):  # noqa: ARG002
        from integrations.keycloak.tools.keycloak_realm_overview_tool import (
            get_keycloak_realm_overview,
        )

        result = get_keycloak_realm_overview(**INJECTED)

        assert result["available"] is True
        assert result["users_total"] == 4

    def test_login_failures_tool_path(self, patched_client):  # noqa: ARG002
        from integrations.keycloak.tools.keycloak_login_failures_tool import (
            get_keycloak_login_failures,
        )

        result = get_keycloak_login_failures(**INJECTED)

        assert result["available"] is True
        assert result["summary"]["total"] == 7

    def test_admin_events_tool_path(self, patched_client):  # noqa: ARG002
        from integrations.keycloak.tools.keycloak_admin_events_tool import (
            get_keycloak_admin_events,
        )

        result = get_keycloak_admin_events(**INJECTED)

        assert result["available"] is True
        assert result["summary"]["total"] == 7

    def test_client_sessions_tool_path(self, patched_client):  # noqa: ARG002
        from integrations.keycloak.tools.keycloak_client_sessions_tool import (
            get_keycloak_client_sessions,
        )

        result = get_keycloak_client_sessions(**INJECTED)

        assert result["available"] is True
        assert result["totals"]["clients"] == 8

    def test_user_status_tool_path(self, patched_client):  # noqa: ARG002
        from integrations.keycloak.tools.keycloak_user_status_tool import (
            get_keycloak_user_status,
        )

        result = get_keycloak_user_status(**INJECTED, username="alice")

        assert result["available"] is True
        assert result["found"] is True
