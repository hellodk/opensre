"""Unit tests for the NATS integration module.

Config layer + client error mapping + validation against mocked httpx
responses, replaying fixtures captured from nats-server 2.14.6. No real
server connections.
"""

from __future__ import annotations

import json
from http import HTTPStatus
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from integrations.nats import (
    NatsConfig,
    build_nats_config,
    nats_config_from_env,
    nats_extract_params,
    nats_is_available,
    validate_nats_config,
)
from integrations.nats import client as nats_client
from integrations.nats.client import FetchErrorKind, FetchResult, get_json
from integrations.nats.monitoring import ns_to_seconds, parse_go_duration

FIXTURES = Path(__file__).resolve().parent / "nats" / "fixtures"


def load_fixture(name: str) -> Any:
    """Read a fixture payload (JSON dict/list for .json, text for .txt)."""
    path = FIXTURES / name
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return json.loads(text)
    return text


def _response_for(payload: Any) -> httpx.Response:
    if isinstance(payload, httpx.Response):
        return payload
    if isinstance(payload, (dict, list)):
        return httpx.Response(HTTPStatus.OK, json=payload)
    return httpx.Response(
        HTTPStatus.OK,
        text=str(payload),
        headers={"content-type": "text/plain; charset=utf-8"},
    )


def _mock_transport(routes: dict[str, Any]) -> httpx.MockTransport:
    """Build an httpx MockTransport that returns fixed payloads per URL path."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in routes:
            return _response_for(routes[path])
        return httpx.Response(
            HTTPStatus.NOT_FOUND,
            text="404 page not found\n",
            headers={"content-type": "text/plain; charset=utf-8"},
        )

    return httpx.MockTransport(handler)


@pytest.fixture
def patched_client(monkeypatch: pytest.MonkeyPatch):
    """Patch build_client so diagnostics/validation use a MockTransport."""

    def install(
        routes: dict[str, Any],
        base_url: str = "http://nats.example.net:8222",
    ) -> None:
        def _fake_build_client(config: NatsConfig) -> httpx.Client:
            return httpx.Client(
                base_url=base_url,
                transport=_mock_transport(routes),
            )

        monkeypatch.setattr(nats_client, "build_client", _fake_build_client)

    return install


def _nats_config(url: str = "http://nats.example.net:8222") -> NatsConfig:
    return build_nats_config({"url": url})


CLUSTER_ROUTES: dict[str, Any] = {}
SOLO_ROUTES: dict[str, Any] = {}


def _cluster_routes() -> dict[str, Any]:
    return {
        "/varz": load_fixture("varz.json"),
        "/healthz": load_fixture("healthz.json"),
        "/connz": load_fixture("connz_sort_subs_detail.json"),
        "/subsz": load_fixture("subsz_detail.json"),
        "/jsz": load_fixture("jsz_streams_consumers_config.json"),
        "/routez": load_fixture("routez.json"),
        "/gatewayz": load_fixture("gatewayz.json"),
        "/leafz": load_fixture("leafz.json"),
    }


def _solo_routes() -> dict[str, Any]:
    return {
        "/varz": load_fixture("varz_solo.json"),
        "/healthz": load_fixture("healthz_solo.json"),
        "/connz": load_fixture("connz_solo.json"),
        "/subsz": load_fixture("subsz.json"),
        "/jsz": load_fixture("jsz_solo_streams.json"),
        "/routez": load_fixture("routez_solo.json"),
        "/gatewayz": load_fixture("gatewayz_solo.json"),
        "/leafz": load_fixture("leafz_solo.json"),
    }


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestNatsConfig:
    def test_defaults(self) -> None:
        config = NatsConfig()
        assert config.url == ""
        assert config.username == ""
        assert config.password == ""
        assert config.verify_ssl is True
        assert config.timeout_seconds == 10
        assert config.has_auth is False
        assert config.is_configured is False

    def test_url_trailing_slash_stripped(self) -> None:
        config = build_nats_config({"url": "http://nats.example.net:8222/"})
        assert config.url == "http://nats.example.net:8222"

    def test_username_stripped_password_not_stripped(self) -> None:
        config = build_nats_config({"username": "  monitor  ", "password": " s3cret "})
        assert config.username == "monitor"
        assert config.password == " s3cret "

    def test_scheme_validation_rejects_bare_host(self) -> None:
        with pytest.raises(ValidationError):
            build_nats_config({"url": "nats.example.net:8222"})

    def test_is_configured_requires_url(self) -> None:
        assert build_nats_config({}).is_configured is False
        assert build_nats_config({"url": "http://x:8222"}).is_configured is True

    def test_timeout_falls_back_on_bad_input(self) -> None:
        assert build_nats_config({"timeout_seconds": "abc"}).timeout_seconds == 10

    def test_verify_ssl_string_coercion(self) -> None:
        assert build_nats_config({"verify_ssl": "no"}).verify_ssl is False
        assert build_nats_config({"verify_ssl": "false"}).verify_ssl is False
        assert build_nats_config({"verify_ssl": "true"}).verify_ssl is True

    def test_has_auth(self) -> None:
        assert build_nats_config({"username": "u"}).has_auth is True
        assert build_nats_config({}).has_auth is False


class TestNatsEnv:
    def test_returns_none_without_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NATS_MONITOR_URL", raising=False)
        assert nats_config_from_env() is None

    def test_loads_every_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NATS_MONITOR_URL", "http://nats.example.net:8222")
        monkeypatch.setenv("NATS_MONITOR_USERNAME", "monitor")
        monkeypatch.setenv("NATS_MONITOR_PASSWORD", "s3cret")
        monkeypatch.setenv("NATS_VERIFY_SSL", "false")
        monkeypatch.setenv("NATS_TIMEOUT_SECONDS", "5")
        config = nats_config_from_env()
        assert config is not None
        assert config.url == "http://nats.example.net:8222"
        assert config.username == "monitor"
        assert config.password == "s3cret"
        assert config.verify_ssl is False
        assert config.timeout_seconds == 5

    def test_password_read_through_resolve_env_credential(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import integrations.nats.config as nats_config_module

        monkeypatch.setenv("NATS_MONITOR_URL", "http://nats.example.net:8222")
        calls: list[str] = []

        def _fake_resolve(env_var: str, *, default: str = "") -> str:
            calls.append(env_var)
            return "s3cret"

        monkeypatch.setattr(nats_config_module, "resolve_env_credential", _fake_resolve)
        config = nats_config_from_env()
        assert config is not None
        assert config.password == "s3cret"
        assert calls == ["NATS_MONITOR_PASSWORD"]


class TestNatsExtractParams:
    def test_full_dict(self) -> None:
        sources = {
            "nats": {
                "url": "http://nats.example.net:8222",
                "username": "u",
                "password": "p",
                "verify_ssl": False,
            }
        }
        assert nats_extract_params(sources) == {
            "url": "http://nats.example.net:8222",
            "username": "u",
            "password": "p",
            "verify_ssl": False,
        }

    def test_missing_keys_default(self) -> None:
        assert nats_extract_params({}) == {
            "url": "",
            "username": "",
            "password": "",
            "verify_ssl": True,
        }

    def test_is_available(self) -> None:
        assert nats_is_available({"nats": {"url": "http://x:8222"}}) is True
        assert nats_is_available({}) is False
        assert nats_is_available({"nats": {}}) is False


class TestClassify:
    def test_store_record_resolves(self) -> None:
        from integrations.catalog import classify_integrations

        resolved = classify_integrations(
            [
                {
                    "id": "nats-prod",
                    "service": "nats",
                    "status": "active",
                    "credentials": {"url": "http://nats.example.net:8222"},
                }
            ]
        )
        assert resolved["nats"]["url"] == "http://nats.example.net:8222"

    def test_record_without_url_skipped(self) -> None:
        from integrations.catalog import classify_integrations

        resolved = classify_integrations(
            [
                {
                    "id": "bad-nats",
                    "service": "nats",
                    "status": "active",
                    "credentials": {"url": ""},
                }
            ]
        )
        assert resolved.get("nats") is None


# ---------------------------------------------------------------------------
# Client tests (§1.3 rows)
# ---------------------------------------------------------------------------


def _client_for(routes: dict[str, Any]) -> httpx.Client:
    return httpx.Client(
        base_url="http://nats.example.net:8222",
        transport=_mock_transport(routes),
    )


class TestClient:
    def test_wrong_port_on_remote_protocol_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")

        client = httpx.Client(
            base_url="http://nats.example.net:8222",
            transport=httpx.MockTransport(handler),
        )
        result, err = get_json(client, _nats_config(), "/varz")
        assert result is None
        assert err is not None
        assert err.kind == FetchErrorKind.WRONG_PORT
        assert "8222" in err.message

    def test_transport_on_connect_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("[Errno 111] Connection refused")

        client = httpx.Client(
            base_url="http://nats.example.net:8222",
            transport=httpx.MockTransport(handler),
        )
        result, err = get_json(client, _nats_config(), "/varz")
        assert result is None
        assert err is not None
        assert err.kind == FetchErrorKind.TRANSPORT

    def test_auth_on_401_and_403(self) -> None:
        for code in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
            client = _client_for({"/varz": httpx.Response(code, text="forbidden")})
            result, err = get_json(client, _nats_config(), "/varz")
            assert result is None
            assert err is not None
            assert err.kind == FetchErrorKind.AUTH
            assert "NATS_MONITOR_USERNAME" in err.message

    def test_not_found(self) -> None:
        # not_found.txt is the 404 body; serve it with a 404 status.
        client = httpx.Client(
            base_url="http://nats.example.net:8222",
            transport=_mock_transport(
                {
                    "/nonexistent": httpx.Response(
                        HTTPStatus.NOT_FOUND, text=load_fixture("not_found.txt")
                    )
                }
            ),
        )
        result, err = get_json(client, _nats_config(), "/nonexistent")
        assert result is None
        assert err is not None
        assert err.kind == FetchErrorKind.NOT_FOUND
        assert "404 page not found" in err.message

    def test_http_on_bad_sort(self) -> None:
        client = httpx.Client(
            base_url="http://nats.example.net:8222",
            transport=_mock_transport(
                {
                    "/connz": httpx.Response(
                        HTTPStatus.BAD_REQUEST,
                        text=load_fixture("connz_bad_sort.txt"),
                    )
                }
            ),
        )
        result, err = get_json(client, _nats_config(), "/connz")
        assert result is None
        assert err is not None
        assert err.kind == FetchErrorKind.HTTP
        assert "invalid sorting option: bogus" in err.message

    def test_http_on_500(self) -> None:
        client = _client_for(
            {"/varz": httpx.Response(HTTPStatus.INTERNAL_SERVER_ERROR, text="boom")}
        )
        result, err = get_json(client, _nats_config(), "/varz")
        assert result is None
        assert err is not None
        assert err.kind == FetchErrorKind.HTTP

    def test_body_on_non_json_200(self) -> None:
        client = httpx.Client(
            base_url="http://nats.example.net:8222",
            transport=_mock_transport(
                {
                    "/": httpx.Response(
                        HTTPStatus.OK,
                        text=load_fixture("root.txt"),
                        headers={"content-type": "text/html; charset=utf-8"},
                    )
                }
            ),
        )
        result, err = get_json(client, _nats_config(), "/")
        assert result is None
        assert err is not None
        assert err.kind == FetchErrorKind.BODY
        assert "8222" in err.message

    def test_accepted_503_returns_result(self) -> None:
        payload = {"status": "unavailable", "error": "JetStream not current"}
        client = _client_for(
            {"/healthz": httpx.Response(HTTPStatus.SERVICE_UNAVAILABLE, json=payload)}
        )
        result, err = get_json(
            client,
            _nats_config(),
            "/healthz",
            accept=(HTTPStatus.OK, HTTPStatus.SERVICE_UNAVAILABLE),
        )
        assert err is None
        assert isinstance(result, FetchResult)
        assert result.status == HTTPStatus.SERVICE_UNAVAILABLE
        assert result.payload == payload

    def test_unaccepted_503_is_http_error(self) -> None:
        client = _client_for(
            {
                "/healthz": httpx.Response(
                    HTTPStatus.SERVICE_UNAVAILABLE, json={"status": "unavailable"}
                )
            }
        )
        result, err = get_json(client, _nats_config(), "/healthz")
        assert result is None
        assert err is not None
        assert err.kind == FetchErrorKind.HTTP

    def test_basic_auth_header_sent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict[str, str | None] = {}
        real_client = httpx.Client

        def handler(request: httpx.Request) -> httpx.Response:
            seen["authorization"] = request.headers.get("authorization")
            return httpx.Response(HTTPStatus.OK, json={"status": "ok"})

        def _recording_client(*args: Any, **kwargs: Any) -> httpx.Client:
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_client(*args, **kwargs)

        monkeypatch.setattr(httpx, "Client", _recording_client)
        config = build_nats_config(
            {"url": "http://nats.example.net:8222", "username": "u", "password": "p"}
        )
        with nats_client.build_client(config) as client:
            result, err = get_json(client, config, "/healthz")
        assert err is None
        assert result is not None
        assert (seen["authorization"] or "").startswith("Basic ")

    def test_no_auth_header_without_username(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict[str, str | None] = {}
        real_client = httpx.Client

        def handler(request: httpx.Request) -> httpx.Response:
            seen["authorization"] = request.headers.get("authorization")
            return httpx.Response(HTTPStatus.OK, json={"status": "ok"})

        def _recording_client(*args: Any, **kwargs: Any) -> httpx.Client:
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_client(*args, **kwargs)

        monkeypatch.setattr(httpx, "Client", _recording_client)
        config = _nats_config()
        with nats_client.build_client(config) as client:
            get_json(client, config, "/healthz")
        assert seen["authorization"] is None

    def test_params_serialized_as_strings(self) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(dict(request.url.params))
            return httpx.Response(HTTPStatus.OK, json={"status": "ok"})

        client = httpx.Client(
            base_url="http://nats.example.net:8222",
            transport=httpx.MockTransport(handler),
        )
        get_json(client, _nats_config(), "/subsz", params={"subs": "true"})
        assert seen.get("subs") == "true"


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_parse_go_duration(self) -> None:
        assert parse_go_duration("133µs") == pytest.approx(0.000133)
        assert parse_go_duration("1m52s") == 112.0
        assert parse_go_duration("0s") == 0.0
        assert parse_go_duration("") is None
        assert parse_go_duration(None) is None
        assert parse_go_duration("weird") is None

    def test_ns_to_seconds(self) -> None:
        assert ns_to_seconds(30000000000) == 30.0
        assert ns_to_seconds(-1) == -1.0
        assert ns_to_seconds(None) is None


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------


class TestValidate:
    def test_cluster_ok(self, patched_client) -> None:
        patched_client(_cluster_routes())
        result = validate_nats_config(_nats_config())
        assert result.ok is True
        assert "2.14.6" in result.detail
        assert "'n1'" in result.detail
        assert "JetStream enabled" in result.detail
        assert "cluster opensre-demo" in result.detail

    def test_solo_ok(self, patched_client) -> None:
        patched_client(_solo_routes())
        result = validate_nats_config(_nats_config())
        assert result.ok is True
        assert "JetStream disabled" in result.detail
        assert "standalone" in result.detail

    def test_healthz_503_fails(self, patched_client) -> None:
        routes = _cluster_routes()
        routes["/healthz"] = httpx.Response(
            HTTPStatus.SERVICE_UNAVAILABLE,
            json={"status": "unavailable", "error": "JetStream not current"},
        )
        patched_client(routes)
        result = validate_nats_config(_nats_config())
        assert result.ok is False
        assert "unavailable" in result.detail
        assert "JetStream not current" in result.detail

    def test_connection_refused_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import integrations.nats.client as client_module

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("[Errno 111] Connection refused")

        def _fake_build(config: NatsConfig) -> httpx.Client:
            return httpx.Client(
                base_url="http://nats.example.net:8222",
                transport=httpx.MockTransport(handler),
            )

        monkeypatch.setattr(client_module, "build_client", _fake_build)
        result = validate_nats_config(_nats_config())
        assert result.ok is False

    def test_wrong_port_mentions_8222(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")

        client = httpx.Client(
            base_url="http://nats.example.net:8222",
            transport=httpx.MockTransport(handler),
        )
        result, err = get_json(client, _nats_config(), "/healthz")
        assert err is not None
        assert "8222" in err.message

    def test_401_hides_password(self, patched_client) -> None:
        routes = _cluster_routes()
        routes["/healthz"] = httpx.Response(HTTPStatus.UNAUTHORIZED, text="forbidden")
        patched_client(routes)
        config = build_nats_config(
            {
                "url": "http://nats.example.net:8222",
                "username": "u",
                "password": "supersecretpw",
            }
        )
        result = validate_nats_config(config)
        assert result.ok is False
        assert "supersecretpw" not in result.detail
