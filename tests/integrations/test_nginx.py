"""Unit tests for the nginx integration module.

Mirrors the test_rabbitmq.py pattern: config layer + stub_status parser +
httpx client mapping + Plus API shapers + validation, all against a mocked
transport. No real nginx connections.
"""

from __future__ import annotations

import json
from http import HTTPStatus
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from integrations.nginx import (
    DEFAULT_NGINX_ACCESS_LOG_PATH,
    DEFAULT_NGINX_API_PATH,
    DEFAULT_NGINX_ERROR_LOG_PATH,
    DEFAULT_NGINX_PORT,
    DEFAULT_NGINX_STUB_STATUS_PATH,
    DEFAULT_NGINX_TIMEOUT_SECONDS,
    NginxConfig,
    NginxValidationResult,
    build_nginx_config,
    get_server_status,
    nginx_config_from_env,
    nginx_extract_params,
    nginx_is_available,
    validate_nginx_config,
)
from integrations.nginx import client as nginx_client
from integrations.nginx.client import (
    FetchErrorKind,
    fetch_json,
    fetch_text,
    nginx_version_from_server_header,
)
from integrations.nginx.plus_api import (
    detect_api_version,
    plus_path,
    shape_caches,
    shape_connections,
    shape_nginx_info,
    shape_requests,
    shape_server_zones,
    shape_upstreams,
)
from integrations.nginx.stub_status import parse_stub_status

FIXTURES = Path(__file__).parent / "nginx" / "fixtures"
PLUS_FIXTURES = FIXTURES / "plus"


def _load_json(name: str) -> Any:
    return json.loads((PLUS_FIXTURES / name).read_text())


# ---------------------------------------------------------------------------
# Mock transport helper
# ---------------------------------------------------------------------------


def _mock_transport(routes: dict[str, httpx.Response | str | dict]) -> httpx.MockTransport:
    """Build an httpx MockTransport that returns fixed payloads per URL path."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in routes:
            payload = routes[path]
            if isinstance(payload, httpx.Response):
                return payload
            if isinstance(payload, str):
                return httpx.Response(
                    HTTPStatus.OK,
                    text=payload,
                    headers={"server": "nginx/1.27.5"},
                )
            return httpx.Response(HTTPStatus.OK, json=payload)
        return httpx.Response(HTTPStatus.NOT_FOUND, text="not found")

    return httpx.MockTransport(handler)


@pytest.fixture
def patched_client(monkeypatch: pytest.MonkeyPatch):
    """Patch ``build_client`` so diagnostics/validation use a MockTransport."""

    def install(routes: dict[str, Any]) -> None:
        def _fake_client(config: NginxConfig) -> httpx.Client:
            return httpx.Client(
                base_url=config.base_url,
                transport=_mock_transport(routes),
            )

        monkeypatch.setattr(nginx_client, "build_client", _fake_client)

    return install


@pytest.fixture
def stub_status_body() -> str:
    return (FIXTURES / "stub_status.txt").read_text()


@pytest.fixture
def configured() -> NginxConfig:
    return NginxConfig(host="nginx.test")


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestNginxConfig:
    def test_defaults(self) -> None:
        config = NginxConfig()
        assert config.host == ""
        assert config.port == DEFAULT_NGINX_PORT
        assert config.port == DEFAULT_NGINX_PORT
        assert config.ssl is False
        assert config.verify_ssl is True
        assert config.username == ""
        assert config.password == ""
        assert config.stub_status_path == DEFAULT_NGINX_STUB_STATUS_PATH
        assert config.api_path == DEFAULT_NGINX_API_PATH
        assert config.access_log_path == DEFAULT_NGINX_ACCESS_LOG_PATH
        assert config.error_log_path == DEFAULT_NGINX_ERROR_LOG_PATH
        assert config.timeout_seconds == DEFAULT_NGINX_TIMEOUT_SECONDS
        assert config.is_configured is False

    def test_host_stripped(self) -> None:
        assert NginxConfig(host="  nginx.internal  ").host == "nginx.internal"

    def test_username_stripped(self) -> None:
        assert NginxConfig(username="  monitor  ").username == "monitor"

    def test_password_never_stripped(self) -> None:
        assert NginxConfig(password="  s3cret  ").password == "  s3cret  "

    def test_api_path_normalized(self) -> None:
        assert NginxConfig(api_path="/api/").api_path == "/api"
        assert NginxConfig(api_path="api").api_path == "/api"
        assert NginxConfig(api_path="").api_path == DEFAULT_NGINX_API_PATH
        assert NginxConfig(api_path="   ").api_path == DEFAULT_NGINX_API_PATH

    def test_stub_status_path_normalized(self) -> None:
        assert NginxConfig(stub_status_path="status").stub_status_path == "/status"
        assert NginxConfig(stub_status_path="").stub_status_path == (DEFAULT_NGINX_STUB_STATUS_PATH)

    def test_log_paths_default_when_blank(self) -> None:
        config = NginxConfig(access_log_path="", error_log_path="  ")
        assert config.access_log_path == DEFAULT_NGINX_ACCESS_LOG_PATH
        assert config.error_log_path == DEFAULT_NGINX_ERROR_LOG_PATH

    def test_is_configured_requires_host(self) -> None:
        assert NginxConfig(host="h").is_configured is True
        assert NginxConfig().is_configured is False

    def test_base_url_http_by_default(self) -> None:
        assert NginxConfig(host="h").base_url == "http://h:80"

    def test_base_url_https_when_ssl(self) -> None:
        config = NginxConfig(host="h", port=8443, ssl=True)
        assert config.base_url == "https://h:8443"

    def test_auth_none_without_username(self) -> None:
        assert NginxConfig(host="h").auth is None
        assert NginxConfig(host="h", password="pw").auth is None

    def test_auth_tuple_with_username(self) -> None:
        assert NginxConfig(host="h", username="u", password="pw").auth == ("u", "pw")

    def test_port_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            NginxConfig(host="h", port=0)

    def test_port_too_large_rejected(self) -> None:
        with pytest.raises(ValidationError):
            NginxConfig(host="h", port=70000)

    def test_port_non_numeric_falls_back(self) -> None:
        config = NginxConfig(host="h", port="abc")  # type: ignore[arg-type]
        assert config.port == DEFAULT_NGINX_PORT

    def test_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            NginxConfig(host="h", bogus_field="x")  # type: ignore[call-arg]


class TestBuildNginxConfig:
    def test_from_dict(self) -> None:
        config = build_nginx_config({"host": "nginx.test", "port": 8080})
        assert config.host == "nginx.test"
        assert config.port == 8080

    def test_from_none_yields_defaults(self) -> None:
        config = build_nginx_config(None)
        assert config.host == ""
        assert config.port == DEFAULT_NGINX_PORT


class TestNginxConfigFromEnv:
    def test_returns_none_without_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NGINX_HOST", raising=False)
        assert nginx_config_from_env() is None

    def test_loads_every_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NGINX_HOST", "nginx.internal")
        monkeypatch.setenv("NGINX_PORT", "8080")
        monkeypatch.setenv("NGINX_SSL", "true")
        monkeypatch.setenv("NGINX_VERIFY_SSL", "false")
        monkeypatch.setenv("NGINX_USERNAME", "monitor")
        monkeypatch.setenv("NGINX_PASSWORD", "s3cret")
        monkeypatch.setenv("NGINX_STUB_STATUS_PATH", "/status")
        monkeypatch.setenv("NGINX_API_PATH", "/api/")
        monkeypatch.setenv("NGINX_ACCESS_LOG_PATH", "/tmp/access.log")
        monkeypatch.setenv("NGINX_ERROR_LOG_PATH", "/tmp/error.log")
        monkeypatch.setenv("NGINX_TIMEOUT_SECONDS", "5")

        config = nginx_config_from_env()
        assert config is not None
        assert config.host == "nginx.internal"
        assert config.port == 8080
        assert config.ssl is True
        assert config.verify_ssl is False
        assert config.username == "monitor"
        assert config.password == "s3cret"
        assert config.stub_status_path == "/status"
        assert config.api_path == "/api"
        assert config.access_log_path == "/tmp/access.log"
        assert config.error_log_path == "/tmp/error.log"
        assert config.timeout_seconds == 5

    def test_password_resolved_through_credential_store(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import integrations.nginx.config as nginx_config_module

        monkeypatch.setenv("NGINX_HOST", "nginx.internal")
        calls: list[str] = []

        def _fake_resolve(env_var: str, *, default: str = "") -> str:
            calls.append(env_var)
            return "stored-secret"

        monkeypatch.setattr(nginx_config_module, "resolve_env_credential", _fake_resolve)
        config = nginx_config_from_env()
        assert config is not None
        assert config.password == "stored-secret"
        assert calls == ["NGINX_PASSWORD"]

    def test_ssl_truthy_forms(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NGINX_HOST", "h")
        monkeypatch.setenv("NGINX_SSL", "1")
        config = nginx_config_from_env()
        assert config is not None
        assert config.ssl is True

    def test_verify_ssl_falsy_forms(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NGINX_HOST", "h")
        monkeypatch.setenv("NGINX_VERIFY_SSL", "no")
        config = nginx_config_from_env()
        assert config is not None
        assert config.verify_ssl is False


class TestNginxExtractParams:
    def test_full_dict_with_defaults(self) -> None:
        params = nginx_extract_params({"nginx": {"host": "nginx.test"}})
        assert params["host"] == "nginx.test"
        assert params["port"] == DEFAULT_NGINX_PORT
        assert params["ssl"] is False
        assert params["verify_ssl"] is True
        assert params["stub_status_path"] == DEFAULT_NGINX_STUB_STATUS_PATH
        assert params["api_path"] == DEFAULT_NGINX_API_PATH
        assert params["access_log_path"] == DEFAULT_NGINX_ACCESS_LOG_PATH
        assert params["error_log_path"] == DEFAULT_NGINX_ERROR_LOG_PATH
        assert "integration_id" not in params
        assert "timeout_seconds" not in params

    def test_is_available(self) -> None:
        assert nginx_is_available({"nginx": {"host": "h"}}) is True
        assert nginx_is_available({}) is False
        assert nginx_is_available({"nginx": {}}) is False


class TestClassify:
    def test_store_record_resolves(self) -> None:
        from integrations.catalog import classify_integrations

        integrations = [
            {
                "id": "nginx-prod",
                "service": "nginx",
                "status": "active",
                "credentials": {
                    "host": "nginx.internal",
                    "port": 8080,
                    "username": "monitor",
                    "password": "s3cret",
                    "stub_status_path": "/status",
                    "api_path": "/api",
                },
            }
        ]
        resolved = classify_integrations(integrations)
        assert "nginx" in resolved
        assert resolved["nginx"]["host"] == "nginx.internal"
        assert resolved["nginx"]["port"] == 8080
        assert resolved["nginx"]["username"] == "monitor"
        assert resolved["nginx"]["stub_status_path"] == "/status"

    def test_empty_host_skipped(self) -> None:
        from integrations.catalog import classify_integrations

        integrations = [
            {
                "id": "bad-nginx",
                "service": "nginx",
                "status": "active",
                "credentials": {"host": ""},
            }
        ]
        resolved = classify_integrations(integrations)
        assert resolved.get("nginx") is None


# ---------------------------------------------------------------------------
# stub_status parser tests
# ---------------------------------------------------------------------------


class TestStubStatusParser:
    def test_fixture_parses_to_exact_numbers(self, stub_status_body: str) -> None:
        parsed = parse_stub_status(stub_status_body)
        assert parsed is not None
        assert parsed.active_connections == 1
        assert parsed.accepts == 7
        assert parsed.handled == 7
        assert parsed.requests == 7
        assert parsed.reading == 0
        assert parsed.writing == 1
        assert parsed.waiting == 0
        assert parsed.dropped == 0

    def test_dropped_counts_accepts_minus_handled(self) -> None:
        body = (
            "Active connections: 2\n"
            "server accepts handled requests\n"
            " 10 8 15\n"
            "Reading: 0 Writing: 1 Waiting: 1\n"
        )
        parsed = parse_stub_status(body)
        assert parsed is not None
        assert parsed.dropped == 2

    def test_html_body_returns_none(self) -> None:
        assert parse_stub_status("<html><body>catch-all</body></html>") is None

    def test_truncated_body_returns_none(self, stub_status_body: str) -> None:
        first_two = "\n".join(stub_status_body.splitlines()[:2])
        assert parse_stub_status(first_two) is None

    def test_crlf_line_ends_parse(self, stub_status_body: str) -> None:
        crlf = stub_status_body.replace("\n", "\r\n")
        parsed = parse_stub_status(crlf)
        assert parsed is not None
        assert parsed.active_connections == 1
        assert parsed.requests == 7


# ---------------------------------------------------------------------------
# Client tests (§1.3 rows)
# ---------------------------------------------------------------------------


def _direct_client(handler) -> httpx.Client:  # type: ignore[no-untyped-def]
    return httpx.Client(base_url="http://nginx.test", transport=httpx.MockTransport(handler))


class TestClient:
    def test_fetch_text_ok(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(HTTPStatus.OK, text="ok", headers={"server": "nginx/1.27.5"})

        data, err = fetch_text(_direct_client(handler), "/nginx_status")
        assert err is None
        assert data is not None
        assert data.text == "ok"
        assert data.server_header == "nginx/1.27.5"

    def test_fetch_text_unauthorized(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(HTTPStatus.UNAUTHORIZED, text="auth required")

        _data, err = fetch_text(_direct_client(handler), "/nginx_status")
        assert err is not None
        assert err.kind is FetchErrorKind.AUTH
        assert "authentication failed" in err.message

    def test_fetch_text_forbidden(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(HTTPStatus.FORBIDDEN, text="denied")

        _data, err = fetch_text(_direct_client(handler), "/nginx_status")
        assert err is not None
        assert err.kind is FetchErrorKind.FORBIDDEN
        assert "/nginx_status" in err.message

    def test_fetch_json_not_found(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(HTTPStatus.NOT_FOUND, text="nope")

        _data, err = fetch_json(_direct_client(handler), "/api/")
        assert err is not None
        assert err.kind is FetchErrorKind.NOT_FOUND

    def test_fetch_json_server_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(HTTPStatus.INTERNAL_SERVER_ERROR, text="boom")

        _data, err = fetch_json(_direct_client(handler), "/api/9/nginx")
        assert err is not None
        assert err.kind is FetchErrorKind.HTTP
        assert str(int(HTTPStatus.INTERNAL_SERVER_ERROR)) in err.message

    def test_fetch_transport_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        _data, err = fetch_text(_direct_client(handler), "/nginx_status")
        assert err is not None
        assert err.kind is FetchErrorKind.TRANSPORT

    def test_fetch_json_non_json_body(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(HTTPStatus.OK, text="<html>not json</html>")

        _data, err = fetch_json(_direct_client(handler), "/api/")
        assert err is not None
        assert err.kind is FetchErrorKind.BODY

    def test_version_from_server_header(self) -> None:
        assert nginx_version_from_server_header("nginx/1.27.5") == "1.27.5"
        assert nginx_version_from_server_header("nginx") == "unknown"
        assert nginx_version_from_server_header("") == "unknown"
        assert nginx_version_from_server_header("openresty/1.25.3.1") == "1.25.3.1"


# ---------------------------------------------------------------------------
# Plus API tests
# ---------------------------------------------------------------------------


class TestPlusApi:
    def test_plus_path(self) -> None:
        assert plus_path("/api", 9, "nginx") == "/api/9/nginx"

    def test_detect_api_version_returns_max(self, patched_client, configured: NginxConfig) -> None:
        patched_client({"/api/": _load_json("root.json")})
        with nginx_client.build_client(configured) as client:
            version, err = detect_api_version(client, "/api")
        assert err is None
        assert version == 9

    def test_detect_api_version_rejects_non_list(
        self, patched_client, configured: NginxConfig
    ) -> None:
        patched_client({"/api/": {"not": "a list"}})
        with nginx_client.build_client(configured) as client:
            version, err = detect_api_version(client, "/api")
        assert version is None
        assert err is not None
        assert err.kind is FetchErrorKind.BODY

    def test_detect_api_version_not_found(self, patched_client, configured: NginxConfig) -> None:
        patched_client({})
        with nginx_client.build_client(configured) as client:
            version, err = detect_api_version(client, "/api")
        assert version is None
        assert err is not None
        assert err.kind is FetchErrorKind.NOT_FOUND

    def test_shape_nginx_info(self) -> None:
        shaped = shape_nginx_info(_load_json("nginx.json"))
        assert shaped["version"] == "1.29.8"
        assert shaped["build"] == "nginx-plus-r37.0.2"
        assert shaped["address"] == "18.193.151.235"
        assert shaped["pid"] == 708
        assert shaped["generation"] == 1

    def test_shape_connections(self) -> None:
        shaped = shape_connections(_load_json("connections.json"))
        assert shaped == {"accepted": 12629085, "dropped": 0, "active": 3, "idle": 9}

    def test_shape_requests(self) -> None:
        shaped = shape_requests(_load_json("requests.json"))
        assert shaped == {"total": 64798700, "current": 1}

    def test_shape_server_zones(self) -> None:
        zones = shape_server_zones(_load_json("server_zones.json"))
        assert len(zones) == 1
        zone = zones[0]
        assert zone["zone"] == "hg.nginx.org"
        assert zone["processing"] == 0
        assert zone["requests"] == 55331
        assert zone["responses_2xx"] == 55330
        assert zone["responses_5xx"] == 0
        assert zone["responses_total"] == 55330
        assert zone["error_rate_pct"] == 0.0
        assert zone["discarded"] == 1
        assert zone["received_bytes"] == 2904825
        assert zone["sent_bytes"] == 7176625425
        assert zone["ssl_handshakes_failed"] == 0

    def test_shape_server_zones_error_rate_and_filter(self) -> None:
        payload = {
            "bad.example": {
                "processing": 2,
                "requests": 200,
                "responses": {"1xx": 0, "2xx": 180, "3xx": 0, "4xx": 10, "5xx": 10, "total": 200},
                "discarded": 0,
                "received": 100,
                "sent": 200,
            },
            "good.example": {
                "processing": 0,
                "requests": 100,
                "responses": {"1xx": 0, "2xx": 100, "3xx": 0, "4xx": 0, "5xx": 0, "total": 100},
                "discarded": 0,
                "received": 50,
                "sent": 60,
            },
        }
        zones = shape_server_zones(payload)
        assert [z["zone"] for z in zones] == ["bad.example", "good.example"]
        assert zones[0]["error_rate_pct"] == 5.0
        filtered = shape_server_zones(payload, zone_filter="good.example")
        assert [z["zone"] for z in filtered] == ["good.example"]
        assert shape_server_zones(payload, zone_filter="missing") == []
        # Absent ssl section tolerates to zero.
        assert zones[0]["ssl_handshakes_failed"] == 0

    def test_shape_upstreams(self) -> None:
        upstreams = shape_upstreams(_load_json("upstreams.json"))
        assert len(upstreams) == 2
        # Sorted by peers_down desc: api_backend (1 down) before legacy (0 down).
        assert upstreams[0]["upstream"] == "api_backend"
        assert upstreams[0]["peers_total"] == 2
        assert upstreams[0]["peers_up"] == 1
        assert upstreams[0]["peers_down"] == 1
        assert upstreams[0]["keepalive"] == 16
        assert upstreams[0]["zombies"] == 0
        peer = upstreams[0]["peers"][0]
        assert peer["server"] == "10.0.0.41:8084"
        assert peer["state"] == "up"
        assert peer["responses_5xx"] == 0
        assert peer["responses_total"] == 2829285
        assert peer["health_checks"] == 1609501
        assert peer["health_check_fails"] == 1
        assert peer["health_check_last_passed"] is True
        assert peer["header_time_ms"] == 34
        down_peer = upstreams[0]["peers"][1]
        assert down_peer["state"] == "down"
        assert down_peer["responses_5xx"] == 25
        # Null numerics coerce to zero.
        assert down_peer["header_time_ms"] == 0
        assert down_peer["response_time_ms"] == 0
        # Absent health_checks tolerates to zeros/None.
        legacy_peer = upstreams[1]["peers"][0]
        assert legacy_peer["health_checks"] == 0
        assert legacy_peer["health_check_last_passed"] is None

    def test_shape_upstreams_filter(self) -> None:
        upstreams = shape_upstreams(_load_json("upstreams.json"), upstream_filter="legacy")
        assert [u["upstream"] for u in upstreams] == ["legacy"]
        assert shape_upstreams(_load_json("upstreams.json"), upstream_filter="nope") == []

    def test_shape_caches(self) -> None:
        caches = shape_caches(_load_json("caches.json"))
        assert len(caches) == 2
        cache = next(c for c in caches if c["cache"] == "http_cache")
        assert cache["size_bytes"] == 65536
        assert cache["max_size_bytes"] == 67108864
        assert cache["utilization_pct"] == round(100 * 65536 / 67108864, 2)
        assert cache["cold"] is False
        assert cache["hit_responses"] == 55177
        assert cache["miss_responses"] == 0
        assert cache["expired_responses"] == 153
        expected_ratio = round(100 * 55177 / (55177 + 0 + 153 + 0), 2)
        assert cache["hit_ratio_pct"] == expected_ratio
        unbounded = next(c for c in caches if c["cache"] == "unbounded_cache")
        assert unbounded["utilization_pct"] is None
        assert unbounded["size_bytes"] == 0
        assert unbounded["hit_responses"] == 0


# ---------------------------------------------------------------------------
# Validation tests (§8.2 outcomes)
# ---------------------------------------------------------------------------


class TestValidate:
    def _plus_routes(self) -> dict[str, Any]:
        return {
            "/api/": _load_json("root.json"),
            "/api/9/nginx": _load_json("nginx.json"),
            "/api/9/connections": _load_json("connections.json"),
            "/api/9/http/requests": _load_json("requests.json"),
        }

    def test_no_host_fails(self, patched_client, stub_status_body: str) -> None:
        patched_client({"/nginx_status": stub_status_body})
        result = validate_nginx_config(NginxConfig())
        assert isinstance(result, NginxValidationResult)
        assert result.ok is False
        assert "required" in result.detail

    def test_stub_only_reports_open_source(
        self, patched_client, configured: NginxConfig, stub_status_body: str
    ) -> None:
        patched_client({"/nginx_status": stub_status_body})
        result = validate_nginx_config(configured)
        assert result.ok is True
        assert "nginx 1.27.5" in result.detail
        assert "stub_status OK" in result.detail
        assert "open-source" in result.detail
        assert "/api" in result.detail

    def test_plus_only_reports_plus(self, patched_client, configured: NginxConfig) -> None:
        patched_client(self._plus_routes())
        result = validate_nginx_config(configured)
        assert result.ok is True
        assert "NGINX Plus" in result.detail
        assert "nginx-plus-r37.0.2" in result.detail
        assert "API v9" in result.detail
        assert "stub_status not exposed" in result.detail

    def test_both_reports_plus_with_stub(
        self, patched_client, configured: NginxConfig, stub_status_body: str
    ) -> None:
        routes = {"/nginx_status": stub_status_body, **self._plus_routes()}
        patched_client(routes)
        result = validate_nginx_config(configured)
        assert result.ok is True
        assert "NGINX Plus" in result.detail
        assert "stub_status also available" in result.detail

    def test_plus_probe_error_falls_back_to_stub_only(
        self, patched_client, configured: NginxConfig, stub_status_body: str
    ) -> None:
        # A catch-all location proxying /api/ answers 502, not 404: the Plus
        # API is still absent, so validation reports open-source (live 1.27.5).
        patched_client(
            {
                "/nginx_status": stub_status_body,
                "/api/": httpx.Response(HTTPStatus.BAD_GATEWAY, text="bad gateway"),
            }
        )
        result = validate_nginx_config(configured)
        assert result.ok is True
        assert "open-source" in result.detail

    def test_neither_fails_with_both_paths(self, patched_client, configured: NginxConfig) -> None:
        patched_client({})
        result = validate_nginx_config(configured)
        assert result.ok is False
        assert "Neither stub_status" in result.detail
        assert "/nginx_status" in result.detail
        assert "/api" in result.detail

    def test_auth_failure_fails(self, patched_client, configured: NginxConfig) -> None:
        patched_client({"/nginx_status": httpx.Response(HTTPStatus.UNAUTHORIZED, text="no")})
        result = validate_nginx_config(configured)
        assert result.ok is False
        assert "authentication failed" in result.detail

    def test_connection_refused_fails(
        self, monkeypatch: pytest.MonkeyPatch, configured: NginxConfig
    ) -> None:
        def _raise(config: NginxConfig) -> httpx.Client:
            def handler(request: httpx.Request) -> httpx.Response:
                raise httpx.ConnectError("connection refused")

            return httpx.Client(base_url=config.base_url, transport=httpx.MockTransport(handler))

        monkeypatch.setattr(nginx_client, "build_client", _raise)
        result = validate_nginx_config(configured)
        assert result.ok is False


# ---------------------------------------------------------------------------
# Server status diagnostics
# ---------------------------------------------------------------------------


class TestGetServerStatus:
    def test_oss_status(
        self, patched_client, configured: NginxConfig, stub_status_body: str
    ) -> None:
        patched_client({"/nginx_status": stub_status_body})
        result = get_server_status(configured)
        assert result["available"] is True
        assert result["edition"] == "oss"
        assert result["status_source"] == "stub_status"
        assert result["version"] == "1.27.5"
        assert result["connections"]["active"] == 1
        assert result["connections"]["accepted"] == 7
        assert result["connections"]["dropped"] == 0
        assert result["connections"]["idle"] is None
        assert result["requests"] == {"total": 7, "current": None}

    def test_oss_upgraded_to_plus_when_api_answers(
        self, patched_client, configured: NginxConfig, stub_status_body: str
    ) -> None:
        routes = {
            "/nginx_status": stub_status_body,
            "/api/": _load_json("root.json"),
            "/api/9/nginx": _load_json("nginx.json"),
        }
        patched_client(routes)
        result = get_server_status(configured)
        assert result["available"] is True
        assert result["edition"] == "plus"
        assert result["api_version"] == 9
        assert result["version"] == "1.29.8"

    def test_unconfigured(self) -> None:
        result = get_server_status(NginxConfig())
        assert result["available"] is False
        assert result["source"] == "nginx"

    def test_neither_endpoint_names_both_paths(
        self, patched_client, configured: NginxConfig
    ) -> None:
        patched_client({})
        result = get_server_status(configured)
        assert result["available"] is False
        assert "/nginx_status" in result["error"]
        assert "/api" in result["error"]
