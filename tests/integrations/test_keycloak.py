"""Unit tests for the Keycloak integration module.

Config layer + client error mapping + shapers + validation, all against the
captured fixtures in ``tests/integrations/keycloak/fixtures/`` — no live
Keycloak server is needed.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from integrations.keycloak import (
    ALLOWED_USER_EVENT_TYPES,
    DEFAULT_KEYCLOAK_TIMEOUT_SECONDS,
    KeycloakConfig,
    KeycloakValidationResult,
    admin_api,
    build_keycloak_config,
    get_admin_events,
    get_client_sessions,
    get_login_failures,
    get_realm_overview,
    get_server_status,
    get_user_status,
    keycloak_config_from_env,
    keycloak_extract_params,
    keycloak_is_available,
    validate_keycloak_config,
)
from integrations.keycloak import client as keycloak_client
from integrations.keycloak.client import FetchErrorKind
from tests.integrations.keycloak import load_fixture

BASE_URL = "http://keycloak.test"
MANAGEMENT_URL = "http://keycloak.test:9000"
REALM = "opensre-demo"
CLIENT_ID = "opensre"
CLIENT_SECRET = "service-secret-value"
TOKEN_PATH = "/realms/opensre-demo/protocol/openid-connect/token"
REALM_PATH = "/admin/realms/opensre-demo"


def _token_ok() -> dict[str, Any]:
    payload = dict(load_fixture("token_client_credentials.json"))
    payload["access_token"] = "test-access-token"
    return payload


def _mock_transport(routes: Any) -> httpx.MockTransport:
    """Build an httpx MockTransport keyed by URL path (query string ignored)."""

    def handler(request: httpx.Request) -> httpx.Response:
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
                    HTTPStatus.OK, text=payload, headers={"content-type": "text/plain"}
                )
            return httpx.Response(HTTPStatus.OK, json=payload)
        return httpx.Response(HTTPStatus.NOT_FOUND, json={"error": "Realm not found."})

    return httpx.MockTransport(handler)


@pytest.fixture
def patched_client(monkeypatch: pytest.MonkeyPatch):
    """Patch both client builders so get_*/validate use a MockTransport."""

    def install(
        routes: Any,
        *,
        management_url: str = MANAGEMENT_URL,
    ) -> list[httpx.Request]:
        received: list[httpx.Request] = []

        def _recording(routes_arg: Any) -> Any:
            def _handler(request: httpx.Request) -> httpx.Response:
                received.append(request)
                routes_map = routes_arg(request) if callable(routes_arg) else routes_arg
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

            return httpx.MockTransport(_handler)

        transport = _recording(routes)

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
        "realm": REALM,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    base.update(overrides)
    return KeycloakConfig(**base)


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestKeycloakConfig:
    def test_defaults(self) -> None:
        config = KeycloakConfig()
        assert config.url == ""
        assert config.management_url == ""
        assert config.realm == ""
        assert config.auth_realm == ""
        assert config.client_id == ""
        assert config.client_secret == ""
        assert config.verify_ssl is True
        assert config.timeout_seconds == DEFAULT_KEYCLOAK_TIMEOUT_SECONDS
        assert config.is_configured is False
        assert config.has_management_url is False

    def test_auth_realm_falls_back_to_realm(self) -> None:
        assert _config().auth_realm == REALM
        assert _config(auth_realm="master").auth_realm == "master"

    def test_normalization(self) -> None:
        config = KeycloakConfig(
            url="http://sso.example.net/",
            management_url="http://sso.example.net:9000/",
            realm="  opensre-demo  ",
            client_id="  opensre  ",
            client_secret="  spaced-secret  ",
        )
        assert config.url == "http://sso.example.net"
        assert config.management_url == "http://sso.example.net:9000"
        assert config.realm == REALM
        assert config.client_id == CLIENT_ID
        # The secret is never stripped — whitespace can be significant.
        assert config.client_secret == "  spaced-secret  "

    def test_scheme_validation_rejects_bare_hostname(self) -> None:
        with pytest.raises(ValidationError):
            KeycloakConfig(url="sso.example.net")

    def test_is_configured_requires_all_four(self) -> None:
        assert _config().is_configured is True
        assert _config(url="").is_configured is False
        assert _config(realm="").is_configured is False
        assert _config(client_id="").is_configured is False
        assert _config(client_secret="").is_configured is False

    def test_paths(self) -> None:
        config = _config()
        assert config.token_path == ("/realms/opensre-demo/protocol/openid-connect/token")
        assert config.admin_realm_path == "/admin/realms/opensre-demo"

    def test_timeout_falls_back_on_garbage(self) -> None:
        assert _config(timeout_seconds="abc").timeout_seconds == (DEFAULT_KEYCLOAK_TIMEOUT_SECONDS)

    def test_build_keycloak_config(self) -> None:
        assert build_keycloak_config(None).is_configured is False
        assert build_keycloak_config({}).is_configured is False


class TestKeycloakEnv:
    _ENV = {
        "KEYCLOAK_URL": BASE_URL,
        "KEYCLOAK_MANAGEMENT_URL": MANAGEMENT_URL,
        "KEYCLOAK_REALM": REALM,
        "KEYCLOAK_AUTH_REALM": "",
        "KEYCLOAK_CLIENT_ID": CLIENT_ID,
        "KEYCLOAK_CLIENT_SECRET": CLIENT_SECRET,
        "KEYCLOAK_VERIFY_SSL": "true",
        "KEYCLOAK_TIMEOUT_SECONDS": "10",
    }

    def test_returns_none_without_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key, value in self._ENV.items():
            monkeypatch.setenv(key, value)
        monkeypatch.delenv("KEYCLOAK_URL")
        assert keycloak_config_from_env() is None

    def test_returns_none_without_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key, value in self._ENV.items():
            monkeypatch.setenv(key, value)
        monkeypatch.delenv("KEYCLOAK_CLIENT_SECRET")
        assert keycloak_config_from_env() is None

    def test_loads_every_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key, value in self._ENV.items():
            monkeypatch.setenv(key, value)
        import integrations.keycloak.config as kc_config

        monkeypatch.setattr(kc_config, "resolve_env_credential", lambda _env: CLIENT_SECRET)
        config = keycloak_config_from_env()
        assert config is not None
        assert config.url == BASE_URL
        assert config.management_url == MANAGEMENT_URL
        assert config.realm == REALM
        assert config.client_id == CLIENT_ID
        assert config.client_secret == CLIENT_SECRET
        assert config.verify_ssl is True

    def test_secret_goes_through_resolve_env_credential(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for key, value in self._ENV.items():
            monkeypatch.setenv(key, value)
        import integrations.keycloak.config as kc_config

        seen: list[str] = []

        def _fake_resolve(env_var: str, **_kwargs: Any) -> str:
            seen.append(env_var)
            return CLIENT_SECRET

        monkeypatch.setattr(kc_config, "resolve_env_credential", _fake_resolve)
        assert keycloak_config_from_env() is not None
        assert seen == ["KEYCLOAK_CLIENT_SECRET"]

    def test_verify_ssl_no_means_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key, value in self._ENV.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("KEYCLOAK_VERIFY_SSL", "no")
        import integrations.keycloak.config as kc_config

        monkeypatch.setattr(kc_config, "resolve_env_credential", lambda _env: CLIENT_SECRET)
        config = keycloak_config_from_env()
        assert config is not None
        assert config.verify_ssl is False


class TestKeycloakExtractParams:
    def test_full_dict(self) -> None:
        sources = {
            "keycloak": {
                "url": BASE_URL,
                "management_url": MANAGEMENT_URL,
                "realm": REALM,
                "auth_realm": "",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "verify_ssl": True,
            }
        }
        assert keycloak_is_available(sources) is True
        params = keycloak_extract_params(sources)
        assert params["url"] == BASE_URL
        assert params["realm"] == REALM
        assert params["client_id"] == CLIENT_ID
        assert params["client_secret"] == CLIENT_SECRET

    def test_empty_sources_not_available(self) -> None:
        assert keycloak_is_available({}) is False

    def test_missing_secret_not_available(self) -> None:
        sources = {"keycloak": {"url": BASE_URL, "realm": REALM, "client_id": CLIENT_ID}}
        assert keycloak_is_available(sources) is False


class TestClassify:
    def test_store_record_resolves(self) -> None:
        from integrations.catalog import classify_integrations

        resolved = classify_integrations(
            [
                {
                    "id": "kc-1",
                    "service": "keycloak",
                    "status": "active",
                    "credentials": {
                        "url": BASE_URL,
                        "realm": REALM,
                        "client_id": CLIENT_ID,
                        "client_secret": CLIENT_SECRET,
                    },
                }
            ]
        )
        entry = resolved["keycloak"]
        assert entry["url"] == BASE_URL
        assert entry["realm"] == REALM
        assert entry["client_id"] == CLIENT_ID
        assert entry["client_secret"] == CLIENT_SECRET

    def test_record_without_secret_skipped(self) -> None:
        from integrations.catalog import classify_integrations

        resolved = classify_integrations(
            [
                {
                    "id": "kc-bad",
                    "service": "keycloak",
                    "status": "active",
                    "credentials": {"url": BASE_URL, "realm": REALM, "client_id": CLIENT_ID},
                }
            ]
        )
        assert resolved.get("keycloak") is None


# ---------------------------------------------------------------------------
# Client tests
# ---------------------------------------------------------------------------


class TestClient:
    def _raw_client(self, routes: Any) -> httpx.Client:
        return httpx.Client(base_url=BASE_URL, transport=_mock_transport(routes))

    def test_fetch_token_ok(self) -> None:
        client = self._raw_client({TOKEN_PATH: _token_ok()})
        token, err = keycloak_client.fetch_token(client, _config())
        assert err is None
        assert token == "test-access-token"

    def test_fetch_token_bad_secret(self) -> None:
        body = load_fixture("token_error_bad_secret.json")
        client = self._raw_client({TOKEN_PATH: httpx.Response(HTTPStatus.UNAUTHORIZED, json=body)})
        token, err = keycloak_client.fetch_token(client, _config())
        assert token is None
        assert err is not None
        assert err.kind is FetchErrorKind.AUTH
        assert CLIENT_ID in err.message
        assert CLIENT_SECRET not in err.message

    def test_fetch_token_unknown_client(self) -> None:
        body = load_fixture("token_error_unknown_client.json")
        client = self._raw_client({TOKEN_PATH: httpx.Response(HTTPStatus.UNAUTHORIZED, json=body)})
        _, err = keycloak_client.fetch_token(client, _config())
        assert err is not None
        assert err.kind is FetchErrorKind.AUTH
        assert CLIENT_SECRET not in err.message

    def test_fetch_token_public_client(self) -> None:
        body = load_fixture("token_error_public_client.json")
        client = self._raw_client({TOKEN_PATH: httpx.Response(HTTPStatus.UNAUTHORIZED, json=body)})
        _, err = keycloak_client.fetch_token(client, _config())
        assert err is not None
        assert err.kind is FetchErrorKind.AUTH
        assert "public client" in err.message

    def test_fetch_token_unknown_realm(self) -> None:
        body = load_fixture("token_error_unknown_realm.json")
        client = self._raw_client({TOKEN_PATH: httpx.Response(HTTPStatus.NOT_FOUND, json=body)})
        _, err = keycloak_client.fetch_token(client, _config())
        assert err is not None
        assert err.kind is FetchErrorKind.NOT_FOUND
        assert REALM in err.message

    def test_fetch_token_transport_error(self) -> None:
        client = self._raw_client({TOKEN_PATH: httpx.ConnectError("connection refused")})
        _, err = keycloak_client.fetch_token(client, _config())
        assert err is not None
        assert err.kind is FetchErrorKind.TRANSPORT

    def test_fetch_token_missing_access_token(self) -> None:
        client = self._raw_client({TOKEN_PATH: {"token_type": "Bearer"}})
        _, err = keycloak_client.fetch_token(client, _config())
        assert err is not None
        assert err.kind is FetchErrorKind.BODY

    def test_admin_get_sends_bearer_token(self) -> None:
        seen: list[httpx.Request] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(HTTPStatus.OK, json={"realm": REALM})

        client = httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(_handler))
        data, err = keycloak_client.admin_get(client, "abc", _config(), REALM_PATH)
        assert err is None
        assert data == {"realm": REALM}
        assert seen[0].headers["authorization"] == "Bearer abc"

    def test_admin_get_unauthorized(self) -> None:
        body = load_fixture("realm_unauthorized.json")
        client = self._raw_client({REALM_PATH: httpx.Response(HTTPStatus.UNAUTHORIZED, json=body)})
        _, err = keycloak_client.admin_get(client, "abc", _config(), REALM_PATH)
        assert err is not None
        assert err.kind is FetchErrorKind.AUTH

    def test_admin_get_forbidden_names_roles(self) -> None:
        body = load_fixture("realm_forbidden_master.json")
        client = self._raw_client({REALM_PATH: httpx.Response(HTTPStatus.FORBIDDEN, json=body)})
        _, err = keycloak_client.admin_get(client, "abc", _config(), REALM_PATH)
        assert err is not None
        assert err.kind is FetchErrorKind.FORBIDDEN
        for role in ("view-realm", "view-users", "view-clients", "view-events"):
            assert role in err.message

    def test_admin_get_not_found(self) -> None:
        body = load_fixture("realm_not_found.json")
        client = self._raw_client({REALM_PATH: httpx.Response(HTTPStatus.NOT_FOUND, json=body)})
        _, err = keycloak_client.admin_get(client, "abc", _config(), REALM_PATH)
        assert err is not None
        assert err.kind is FetchErrorKind.NOT_FOUND
        assert "Realm not found." in err.message

    def test_admin_get_server_error(self) -> None:
        body = load_fixture("events_bad_type.json")
        client = self._raw_client(
            {REALM_PATH + "/events": httpx.Response(HTTPStatus.INTERNAL_SERVER_ERROR, json=body)}
        )
        _, err = keycloak_client.admin_get(client, "abc", _config(), REALM_PATH + "/events")
        assert err is not None
        assert err.kind is FetchErrorKind.HTTP

    def test_admin_get_non_json_body(self) -> None:
        client = self._raw_client(
            {REALM_PATH: httpx.Response(HTTPStatus.OK, text="<html>nope</html>")}
        )
        _, err = keycloak_client.admin_get(client, "abc", _config(), REALM_PATH)
        assert err is not None
        assert err.kind is FetchErrorKind.BODY


# ---------------------------------------------------------------------------
# Shaper tests
# ---------------------------------------------------------------------------


class TestShapers:
    def test_shape_server_info_service_account(self) -> None:
        payload = load_fixture("serverinfo_sa.json")
        info = admin_api.shape_server_info(payload)
        assert info["version"] is None
        assert info["version_visible"] is False
        assert info["features_total"] == 6
        assert info["profile"] == "default"
        assert info["disabled_features_count"] == len(payload["profileInfo"]["disabledFeatures"])

    def test_shape_server_info_admin(self) -> None:
        info = admin_api.shape_server_info(load_fixture("serverinfo_admin.json"))
        assert info["version"] == "26.7.3"
        assert info["version_visible"] is True
        assert info["uptime_ms"] == 21844
        assert info["processor_count"] == 20
        assert info["memory"] is not None
        assert info["memory"]["free_pct"] == 99

    def test_shape_realm(self) -> None:
        realm = admin_api.shape_realm(load_fixture("realm.json"))
        assert realm["failure_factor"] == 3
        assert realm["brute_force_protected"] is True
        assert realm["events_enabled"] is True
        assert realm["access_token_lifespan_seconds"] == 300

    def test_shape_realm_missing_expiration(self) -> None:
        payload = dict(load_fixture("realm.json"))
        payload.pop("eventsExpiration", None)
        assert admin_api.shape_realm(payload)["events_expiration_seconds"] is None

    def test_shape_clients(self) -> None:
        payload = load_fixture("clients_brief.json")
        clients = admin_api.shape_clients(payload)
        summary = admin_api.summarize_clients(clients)
        assert summary["total"] == 8
        assert summary["public"] == 5
        assert summary["bearer_only"] == 2
        assert summary["service_accounts"] == 1
        demo = next(c for c in clients if c["client_id"] == "demo-app")
        assert demo["direct_access_grants"] is True

    def test_shape_session_stats(self) -> None:
        stats = admin_api.shape_session_stats(load_fixture("client_session_stats.json"))
        assert stats["demo-app"] == {
            "active": 1,
            "offline": 0,
            "id": "5c4cf4bb-cd1a-4e18-b048-d8bb888d9ce5",
        }

    def test_shape_user_event(self) -> None:
        events = load_fixture("events_login_error.json")
        mallory = next(e for e in events if e.get("error") == "user_not_found")
        shaped = admin_api.shape_user_event(mallory)
        assert shaped["user_id"] is None
        assert shaped["username"] == "mallory"

        first = admin_api.shape_user_event(events[0])
        assert first["username"] == "dave"

        client_event = load_fixture("events_all.json")[0]
        assert client_event["type"] == "CLIENT_LOGIN_ERROR"
        shaped_client = admin_api.shape_user_event(client_event)
        assert shaped_client["reason"] == ("Public client not allowed to retrieve service account")
        assert shaped_client["grant_type"] == "client_credentials"

    def test_summarize_user_events(self) -> None:
        events = [admin_api.shape_user_event(e) for e in load_fixture("events_login_error.json")]
        summary = admin_api.summarize_user_events(events)
        assert summary["total"] == 7
        assert summary["by_error"] == {
            "resolve_required_actions": 1,
            "user_disabled": 1,
            "user_not_found": 1,
            "user_temporarily_disabled": 1,
            "invalid_user_credentials": 3,
        }
        assert summary["lockouts"] == 1
        assert summary["unknown_users"] == 1
        assert summary["disabled_users"] == 1
        assert summary["by_username"][0] == {"username": "carol", "count": 4}

    def test_shape_admin_event(self) -> None:
        import json as _json

        events = load_fixture("admin_events.json")
        shaped = [admin_api.shape_admin_event(e) for e in events]
        assert all(s["has_representation"] is True for s in shaped)
        assert '"username"' not in _json.dumps(shaped)
        summary = admin_api.summarize_admin_events(shaped)
        assert summary["total"] == 7
        assert summary["by_operation"] == {"CREATE": 7}
        assert summary["by_resource_type"] == {
            "USER": 4,
            "CLIENT": 2,
            "CLIENT_ROLE_MAPPING": 1,
        }

    def test_shape_user(self) -> None:
        user = admin_api.shape_user(load_fixture("user_get_dave.json"))
        assert user["required_actions"] == ["VERIFY_EMAIL", "UPDATE_PASSWORD"]
        assert user["email_verified"] is False
        assert user["created_at"].startswith("2026-")

    def test_shape_brute_force(self) -> None:
        locked = admin_api.shape_brute_force(load_fixture("brute_force_carol.json"))
        assert locked["locked"] is True
        assert locked["failures"] == 3
        assert isinstance(locked["locked_until"], str)
        assert locked["last_failure_ip"] == "172.17.0.1"

        clean = admin_api.shape_brute_force(load_fixture("brute_force_alice.json"))
        assert clean["locked"] is False
        assert clean["locked_until"] is None
        assert clean["last_failure_ip"] is None

    def test_shape_user_session(self) -> None:
        payload = load_fixture("user_sessions_alice.json")
        session = admin_api.shape_user_session(payload[0])
        assert session["clients"] == ["demo-app"]

    def test_allowed_event_types(self) -> None:
        assert "LOGIN_ERROR" in ALLOWED_USER_EVENT_TYPES
        assert "BOGUS" not in ALLOWED_USER_EVENT_TYPES


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------


class TestValidate:
    def _routes(self, extra: dict[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
        routes: dict[str, Any] = {
            TOKEN_PATH: _token_ok(),
            REALM_PATH: load_fixture("realm.json"),
        }
        if extra:
            routes.update(extra)
        routes.update(overrides)
        return routes

    def test_success(self, patched_client: Any) -> None:
        patched_client(self._routes())
        result = validate_keycloak_config(_config())
        assert isinstance(result, KeycloakValidationResult)
        assert result.ok is True
        assert REALM in result.detail
        assert BASE_URL in result.detail
        assert CLIENT_ID in result.detail
        assert "brute-force protection=on" in result.detail

    def test_bad_secret(self, patched_client: Any) -> None:
        body = load_fixture("token_error_bad_secret.json")
        patched_client(
            self._routes({TOKEN_PATH: httpx.Response(HTTPStatus.UNAUTHORIZED, json=body)})
        )
        result = validate_keycloak_config(_config())
        assert result.ok is False
        assert CLIENT_SECRET not in result.detail

    def test_forbidden_names_roles(self, patched_client: Any) -> None:
        body = load_fixture("realm_forbidden_master.json")
        patched_client(self._routes({REALM_PATH: httpx.Response(HTTPStatus.FORBIDDEN, json=body)}))
        result = validate_keycloak_config(_config())
        assert result.ok is False
        assert "view-realm" in result.detail

    def test_realm_not_found(self, patched_client: Any) -> None:
        body = load_fixture("realm_not_found.json")
        patched_client(self._routes({REALM_PATH: httpx.Response(HTTPStatus.NOT_FOUND, json=body)}))
        result = validate_keycloak_config(_config())
        assert result.ok is False
        assert "Realm not found." in result.detail

    def test_management_unset(self, patched_client: Any) -> None:
        patched_client(self._routes())
        result = validate_keycloak_config(_config())
        assert result.ok is True
        assert "management URL not set" in result.detail

    def test_management_refused(self, patched_client: Any) -> None:
        routes = self._routes()
        routes["/health/ready"] = httpx.ConnectError("connection refused")
        patched_client(routes)
        result = validate_keycloak_config(_config(management_url=MANAGEMENT_URL))
        assert result.ok is True
        assert "not reachable" in result.detail

    def test_token_connection_refused(self, patched_client: Any) -> None:
        patched_client({TOKEN_PATH: httpx.ConnectError("connection refused")})
        result = validate_keycloak_config(_config())
        assert result.ok is False


class TestDiagnosticsSmoke:
    """One call per get_* through the mock so import/attribute errors surface here."""

    def test_get_server_status_unconfigured(self) -> None:
        out = get_server_status(KeycloakConfig())
        assert out["available"] is False

    def test_get_realm_overview_unconfigured(self) -> None:
        out = get_realm_overview(KeycloakConfig())
        assert out["available"] is False

    def test_get_login_failures_rejects_unknown_type_without_network(
        self, patched_client: Any
    ) -> None:
        received = patched_client({})
        out = get_login_failures(_config(), event_type="bogus")
        assert out["available"] is False
        assert received == []

    def test_get_admin_events_unconfigured(self) -> None:
        out = get_admin_events(KeycloakConfig())
        assert out["available"] is False

    def test_get_client_sessions_unconfigured(self) -> None:
        out = get_client_sessions(KeycloakConfig())
        assert out["available"] is False

    def test_get_user_status_requires_username(self, patched_client: Any) -> None:
        received = patched_client({})
        out = get_user_status(_config(), "")
        assert out["available"] is False
        assert received == []
