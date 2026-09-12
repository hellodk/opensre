"""Unit tests for the Keycloak get_* diagnostics through a mocked transport."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import httpx
import pytest

from integrations.keycloak import (
    ADMIN_EVENTS_DISABLED_HINT,
    EVENTS_DISABLED_HINT,
    MANAGEMENT_URL_UNSET,
    KeycloakConfig,
    get_admin_events,
    get_client_sessions,
    get_login_failures,
    get_realm_overview,
    get_server_status,
    get_user_status,
)
from integrations.keycloak import client as keycloak_client
from integrations.keycloak.diagnostics import clamp_max_events
from tests.integrations.keycloak import load_fixture

BASE_URL = "http://keycloak.test"
MANAGEMENT_URL = "http://keycloak.test:9000"
REALM = "opensre-demo"
TOKEN_PATH = "/realms/opensre-demo/protocol/openid-connect/token"
REALM_PATH = "/admin/realms/opensre-demo"
ALICE_ID = "cf7afc15-ddf3-47da-b371-1732dc2a3200"
DAVE_ID = "d1779eaf-716f-4430-8d6b-7ef2d06e1701"


def _token_ok() -> dict[str, Any]:
    payload = dict(load_fixture("token_client_credentials.json"))
    payload["access_token"] = "test-access-token"
    return payload


def _carol_id() -> str:
    return str(load_fixture("user_search_by_email.json")[0]["id"])


def _base_routes(**overrides: Any) -> dict[str, Any]:
    routes: dict[str, Any] = {
        TOKEN_PATH: _token_ok(),
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
    routes.update(overrides)
    return routes


@pytest.fixture
def patched_client(monkeypatch: pytest.MonkeyPatch):
    def install(routes: Any) -> list[httpx.Request]:
        received: list[httpx.Request] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            received.append(request)
            routes_map = routes(request) if callable(routes) else routes
            path = request.url.path
            if path in routes_map:
                payload = routes_map[path]
                if isinstance(payload, BaseException):
                    raise payload
                if isinstance(payload, httpx.Response):
                    return payload
                if isinstance(payload, str):
                    return httpx.Response(
                        HTTPStatus.OK,
                        text=payload,
                        headers={"content-type": "text/plain"},
                    )
                return httpx.Response(HTTPStatus.OK, json=payload)
            return httpx.Response(HTTPStatus.NOT_FOUND, json={"error": "Realm not found."})

        transport = httpx.MockTransport(_handler)

        def _fake_build(config: KeycloakConfig) -> httpx.Client:
            return httpx.Client(base_url=config.url, transport=transport)

        def _fake_mgmt(config: KeycloakConfig) -> httpx.Client:
            return httpx.Client(base_url=config.management_url or config.url, transport=transport)

        monkeypatch.setattr(keycloak_client, "build_client", _fake_build)
        monkeypatch.setattr(keycloak_client, "build_management_client", _fake_mgmt)
        return received

    return install


def _config(**overrides: Any) -> KeycloakConfig:
    base: dict[str, Any] = {
        "url": BASE_URL,
        "management_url": MANAGEMENT_URL,
        "realm": REALM,
        "client_id": "opensre",
        "client_secret": "service-secret-value",
    }
    base.update(overrides)
    return KeycloakConfig(**base)


class TestGetServerStatus:
    def test_full(self, patched_client: Any) -> None:
        patched_client(_base_routes())
        out = get_server_status(_config())
        assert out["available"] is True
        assert out["version"] is None
        assert "version_note" in out
        assert out["management"]["reachable"] is True
        assert out["management"]["health"]["database_status"] == "UP"
        assert out["management"]["metrics"]["db_pool"] != []

    def test_management_unset(self, patched_client: Any) -> None:
        patched_client(_base_routes())
        out = get_server_status(_config(management_url=""))
        assert out["available"] is True
        assert out["management"]["configured"] is False
        assert out["management"]["error"] == MANAGEMENT_URL_UNSET

    def test_management_refused(self, patched_client: Any) -> None:
        routes = _base_routes()
        routes["/health/ready"] = httpx.ConnectError("connection refused")
        routes["/health/live"] = httpx.ConnectError("connection refused")
        routes["/metrics"] = httpx.ConnectError("connection refused")
        patched_client(routes)
        out = get_server_status(_config())
        assert out["available"] is True
        assert out["management"]["reachable"] is False
        assert out["management"]["errors"] != []

    def test_token_rejected(self, patched_client: Any) -> None:
        body = load_fixture("token_error_bad_secret.json")
        patched_client({TOKEN_PATH: httpx.Response(HTTPStatus.UNAUTHORIZED, json=body)})
        out = get_server_status(_config())
        assert out["available"] is False
        assert out["error_kind"] == "auth"

    def test_serverinfo_forbidden(self, patched_client: Any) -> None:
        body = load_fixture("realm_forbidden_master.json")
        patched_client(
            _base_routes(**{"/admin/serverinfo": httpx.Response(HTTPStatus.FORBIDDEN, json=body)})
        )
        out = get_server_status(_config())
        assert out["available"] is False
        assert "view-realm" in out["error"]


class TestGetRealmOverview:
    def test_full(self, patched_client: Any) -> None:
        patched_client(_base_routes())
        out = get_realm_overview(_config())
        assert out["available"] is True
        assert out["users_total"] == 4
        assert out["clients"]["total"] == 8
        assert out["sessions"]["active"] == 1
        assert out["warnings"] == []

    def test_count_forbidden_warns(self, patched_client: Any) -> None:
        body = load_fixture("realm_forbidden_master.json")
        patched_client(
            _base_routes(
                **{REALM_PATH + "/users/count": httpx.Response(HTTPStatus.FORBIDDEN, json=body)}
            )
        )
        out = get_realm_overview(_config())
        assert out["available"] is True
        assert out["users_total"] is None
        assert len(out["warnings"]) == 1


class TestGetLoginFailures:
    def test_default(self, patched_client: Any) -> None:
        patched_client(_base_routes())
        out = get_login_failures(_config())
        assert out["available"] is True
        assert out["events_enabled"] is True
        assert out["summary"]["total"] == 7

    def test_unknown_type_makes_no_request(self, patched_client: Any) -> None:
        received = patched_client(_base_routes())
        out = get_login_failures(_config(), event_type="bogus")
        assert out["available"] is False
        assert received == []

    def test_lowercase_type_accepted(self, patched_client: Any) -> None:
        patched_client(_base_routes())
        out = get_login_failures(_config(), event_type="login_error")
        assert out["available"] is True
        assert out["event_type"] == "LOGIN_ERROR"

    def test_max_events_clamped(self, patched_client: Any) -> None:
        received = patched_client(_base_routes())
        get_login_failures(_config(), max_events=9999)
        events_calls = [r for r in received if r.url.path == REALM_PATH + "/events"]
        assert events_calls[-1].url.params["max"] == "500"
        assert clamp_max_events(0) == 100

    def test_zero_max_events_uses_default(self, patched_client: Any) -> None:
        received = patched_client(_base_routes())
        get_login_failures(_config(), max_events=0)
        events_calls = [r for r in received if r.url.path == REALM_PATH + "/events"]
        assert events_calls[-1].url.params["max"] == "100"

    def test_username_scopes_to_user(self, patched_client: Any) -> None:
        carol = load_fixture("user_search_by_email.json")
        received = patched_client(_base_routes(**{REALM_PATH + "/users": carol}))
        out = get_login_failures(_config(), username="carol")
        assert out["found_user"] is True
        events_calls = [r for r in received if r.url.path == REALM_PATH + "/events"]
        assert events_calls[-1].url.params["user"] == _carol_id()

    def test_unknown_username(self, patched_client: Any) -> None:
        patched_client(_base_routes(**{REALM_PATH + "/users": []}))
        out = get_login_failures(_config(), username="nobody")
        assert out["found_user"] is False
        assert out["events"] == []

    def test_events_disabled(self, patched_client: Any) -> None:
        patched_client(
            _base_routes(
                **{REALM_PATH + "/events/config": load_fixture("master_events_config.json")}
            )
        )
        out = get_login_failures(_config())
        assert out["available"] is True
        assert out["events_enabled"] is False
        assert "Realm settings" in out["hint"]
        assert EVENTS_DISABLED_HINT.format(realm=REALM) == out["hint"]


class TestGetAdminEvents:
    def test_full(self, patched_client: Any) -> None:
        patched_client(_base_routes())
        out = get_admin_events(_config())
        assert out["available"] is True
        assert out["summary"]["total"] == 7

    def test_resource_filter(self, patched_client: Any) -> None:
        patched_client(_base_routes())
        out = get_admin_events(_config(), resource_type="user")
        assert out["summary"]["total"] == 4
        assert out["fetched"] == 7

    def test_operation_filter_no_match(self, patched_client: Any) -> None:
        patched_client(_base_routes())
        out = get_admin_events(_config(), operation_type="DELETE")
        assert out["summary"]["total"] == 0

    def test_admin_events_disabled(self, patched_client: Any) -> None:
        patched_client(
            _base_routes(
                **{REALM_PATH + "/events/config": load_fixture("master_events_config.json")}
            )
        )
        out = get_admin_events(_config())
        assert out["admin_events_enabled"] is False
        assert "Realm settings" in out["hint"]
        assert ADMIN_EVENTS_DISABLED_HINT.format(realm=REALM) == out["hint"]


class TestGetClientSessions:
    def test_full(self, patched_client: Any) -> None:
        patched_client(_base_routes())
        out = get_client_sessions(_config())
        assert out["available"] is True
        assert out["totals"]["clients"] == 8
        assert out["totals"]["active_sessions"] == 1
        assert out["clients"][0]["client_id"] == "demo-app"
        assert out["clients"][0]["active_sessions"] == 1

    def test_filter_one(self, patched_client: Any) -> None:
        patched_client(_base_routes())
        out = get_client_sessions(_config(), client_id="demo-app")
        assert len(out["clients"]) == 1

    def test_filter_missing(self, patched_client: Any) -> None:
        patched_client(_base_routes())
        out = get_client_sessions(_config(), client_id="ghost")
        assert out["clients"] == []
        assert "ghost" in out["warning"]


class TestGetUserStatus:
    def _carol_routes(self) -> dict[str, Any]:
        carol_id = _carol_id()
        return _base_routes(
            **{
                REALM_PATH + "/users": load_fixture("user_search_by_email.json"),
                f"{REALM_PATH}/attack-detection/brute-force/users/{carol_id}": (
                    load_fixture("brute_force_carol.json")
                ),
                f"{REALM_PATH}/users/{carol_id}/sessions": load_fixture("user_sessions_carol.json"),
                REALM_PATH + "/events": load_fixture("events_for_carol.json"),
            }
        )

    def test_alice_healthy(self, patched_client: Any) -> None:
        patched_client(_base_routes(**{REALM_PATH + "/events": []}))
        out = get_user_status(_config(), "alice")
        assert out["found"] is True
        assert out["lockout"]["locked"] is False
        assert out["sessions"]["count"] == 1
        assert out["diagnosis"] == []

    def test_carol_locked(self, patched_client: Any) -> None:
        patched_client(self._carol_routes())
        out = get_user_status(_config(), "carol")
        assert out["found"] is True
        assert out["diagnosis"][0].startswith("temporarily locked")
        assert any("3 failed login(s)" in line for line in out["diagnosis"])
        assert "no active sessions" in out["diagnosis"]
        assert (
            "recent login errors: invalid_user_credentials, user_temporarily_disabled"
            in out["diagnosis"]
        )

    def test_dave_required_actions(self, patched_client: Any) -> None:
        patched_client(
            _base_routes(
                **{
                    REALM_PATH + "/users": [load_fixture("user_get_dave.json")],
                    f"{REALM_PATH}/attack-detection/brute-force/users/{DAVE_ID}": (
                        load_fixture("brute_force_alice.json")
                    ),
                    f"{REALM_PATH}/users/{DAVE_ID}/sessions": [],
                }
            )
        )
        out = get_user_status(_config(), "dave")
        assert out["found"] is True
        assert any(
            "required actions pending: VERIFY_EMAIL, UPDATE_PASSWORD" in line
            for line in out["diagnosis"]
        )
        assert "email not verified" in out["diagnosis"]

    def test_email_fallback(self, patched_client: Any) -> None:
        carol = load_fixture("user_search_by_email.json")

        def _routes(request: httpx.Request) -> dict[str, Any]:
            params = request.url.params
            if request.url.path == REALM_PATH + "/users":
                if params.get("email") == "carol@example.com":
                    return {REALM_PATH + "/users": carol}
                return {REALM_PATH + "/users": []}
            return self._carol_routes()

        patched_client(_routes)
        out = get_user_status(_config(), "carol@example.com")
        assert out["found"] is True

    def test_nobody_not_found(self, patched_client: Any) -> None:
        patched_client(_base_routes(**{REALM_PATH + "/users": []}))
        out = get_user_status(_config(), "nobody")
        assert out["found"] is False

    def test_empty_username_no_request(self, patched_client: Any) -> None:
        received = patched_client(_base_routes())
        out = get_user_status(_config(), "")
        assert out["available"] is False
        assert received == []

    def test_events_disabled(self, patched_client: Any) -> None:
        patched_client(
            _base_routes(
                **{REALM_PATH + "/events/config": load_fixture("master_events_config.json")}
            )
        )
        out = get_user_status(_config(), "alice")
        assert out["found"] is True
        assert out["events_enabled"] is False
        assert out["recent_events"] == []
