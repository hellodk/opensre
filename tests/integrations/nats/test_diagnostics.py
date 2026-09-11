"""Unit tests for NATS diagnostics get_* functions over mocked transports."""

from __future__ import annotations

import json
from http import HTTPStatus
from pathlib import Path
from typing import Any

import httpx
import pytest

from integrations.nats import NatsConfig, build_nats_config
from integrations.nats import client as nats_client
from integrations.nats.diagnostics import (
    get_cluster_status,
    get_connections,
    get_jetstream_consumers,
    get_jetstream_streams,
    get_server_status,
    get_subscriptions,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str) -> Any:
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
    return httpx.Response(HTTPStatus.OK, text=str(payload))


def _mock_transport(routes: dict[str, Any]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in routes:
            return _response_for(routes[request.url.path])
        return httpx.Response(HTTPStatus.NOT_FOUND, text="404 page not found\n")

    return httpx.MockTransport(handler)


@pytest.fixture
def patched_client(monkeypatch: pytest.MonkeyPatch):
    def install(
        routes: dict[str, Any],
        base_url: str = "http://nats.example.net:8222",
    ) -> None:
        def _fake_build_client(config: NatsConfig) -> httpx.Client:
            return httpx.Client(base_url=base_url, transport=_mock_transport(routes))

        monkeypatch.setattr(nats_client, "build_client", _fake_build_client)

    return install


def _config() -> NatsConfig:
    return build_nats_config({"url": "http://nats.example.net:8222"})


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


class TestGetServerStatus:
    def test_cluster(self, patched_client) -> None:
        patched_client(_cluster_routes())
        out = get_server_status(_config())
        assert out["available"] is True
        assert out["server"]["version"] == "2.14.6"
        assert out["health"]["ok"] is True
        assert out["jetstream"]["enabled"] is True
        assert out["warnings"] == []

    def test_healthz_503_degraded(self, patched_client) -> None:
        routes = _cluster_routes()
        routes["/healthz"] = httpx.Response(
            HTTPStatus.SERVICE_UNAVAILABLE,
            json={"status": "unavailable", "error": "JetStream not current"},
        )
        patched_client(routes)
        out = get_server_status(_config())
        assert out["available"] is True
        assert out["health"]["ok"] is False
        assert out["health"]["error"] == "JetStream not current"

    def test_healthz_failure_warns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        routes = _cluster_routes()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/healthz":
                raise httpx.ConnectError("refused")
            return _response_for(routes[request.url.path])

        def _fake_build(config: NatsConfig) -> httpx.Client:
            return httpx.Client(
                base_url="http://nats.example.net:8222",
                transport=httpx.MockTransport(handler),
            )

        monkeypatch.setattr(nats_client, "build_client", _fake_build)
        out = get_server_status(_config())
        assert out["available"] is True
        assert out["health"] is None
        assert len(out["warnings"]) == 1

    def test_varz_404(self, patched_client) -> None:
        routes = _cluster_routes()
        routes["/varz"] = httpx.Response(HTTPStatus.NOT_FOUND, text="404 page not found\n")
        patched_client(routes)
        out = get_server_status(_config())
        assert out["available"] is False
        assert out["error_kind"] == "not_found"

    def test_unconfigured_no_network(self, monkeypatch: pytest.MonkeyPatch) -> None:
        called = {"n": 0}

        def _fake_build(config: NatsConfig) -> httpx.Client:
            called["n"] += 1
            raise AssertionError("must not build a client")

        monkeypatch.setattr(nats_client, "build_client", _fake_build)
        out = get_server_status(build_nats_config({}))
        assert out["available"] is False
        assert called["n"] == 0


class TestGetConnections:
    def test_bad_sort_no_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        called = {"n": 0}

        def _fake_build(config: NatsConfig) -> httpx.Client:
            called["n"] += 1
            raise AssertionError("must not build a client")

        monkeypatch.setattr(nats_client, "build_client", _fake_build)
        out = get_connections(_config(), sort="bogus")
        assert out["available"] is False
        assert "Allowed" in out["error"]
        assert called["n"] == 0

    def test_bad_state_no_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        called = {"n": 0}

        def _fake_build(config: NatsConfig) -> httpx.Client:
            called["n"] += 1
            raise AssertionError("must not build a client")

        monkeypatch.setattr(nats_client, "build_client", _fake_build)
        out = get_connections(_config(), state="bogus")
        assert out["available"] is False
        assert called["n"] == 0

    def test_stop_requires_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        called = {"n": 0}

        def _fake_build(config: NatsConfig) -> httpx.Client:
            called["n"] += 1
            raise AssertionError("must not build a client")

        monkeypatch.setattr(nats_client, "build_client", _fake_build)
        out = get_connections(_config(), sort="stop", state="open")
        assert out["available"] is False
        assert "closed" in out["error"]
        assert called["n"] == 0

    def test_stop_with_closed_makes_request(self, patched_client) -> None:
        routes = {
            "/varz": load_fixture("varz.json"),
            "/connz": load_fixture("connz_state_closed.json"),
        }
        patched_client(routes)
        out = get_connections(_config(), sort="stop", state="closed")
        assert out["available"] is True

    def test_limit_clamp(self, patched_client) -> None:
        patched_client(_cluster_routes())
        out = get_connections(_config(), limit=0)
        assert out["limit"] == 25
        out = get_connections(_config(), limit=5000)
        assert out["limit"] == 1024

    def test_query_params(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict[str, str] = {}
        routes = _cluster_routes()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/connz":
                seen.update(dict(request.url.params))
            if request.url.path in routes:
                return _response_for(routes[request.url.path])
            return httpx.Response(HTTPStatus.NOT_FOUND, text="404 page not found\n")

        def _fake_build(config: NatsConfig) -> httpx.Client:
            return httpx.Client(
                base_url="http://nats.example.net:8222",
                transport=httpx.MockTransport(handler),
            )

        monkeypatch.setattr(nats_client, "build_client", _fake_build)
        get_connections(_config())
        assert seen == {
            "sort": "pending",
            "state": "open",
            "limit": "25",
            "subs": "true",
        }

    def test_closed_summary(self, patched_client) -> None:
        patched_client(
            {
                "/varz": load_fixture("varz.json"),
                "/connz": load_fixture("connz_state_closed.json"),
            }
        )
        out = get_connections(_config(), state="closed")
        assert out["total"] == 19
        assert out["summary"]["closed_by_reason"]["Protocol Violation"] == 1

    def test_varz_failure_warns(self, patched_client) -> None:
        routes = _cluster_routes()
        routes["/varz"] = httpx.Response(HTTPStatus.NOT_FOUND, text="404 page not found\n")
        patched_client(routes)
        out = get_connections(_config())
        assert out["available"] is True
        assert out["slow_consumers"] is None
        assert len(out["warnings"]) == 1


class TestGetSubscriptions:
    def test_no_subject(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict[str, str] = {}
        routes = _cluster_routes()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/subsz":
                seen.update(dict(request.url.params))
            return _response_for(routes[request.url.path])

        def _fake_build(config: NatsConfig) -> httpx.Client:
            return httpx.Client(
                base_url="http://nats.example.net:8222",
                transport=httpx.MockTransport(handler),
            )

        monkeypatch.setattr(nats_client, "build_client", _fake_build)
        out = get_subscriptions(_config())
        assert out["subject"] == ""
        assert out["listed"] == 111
        assert out["summary"]["application_subscriptions"] == 14
        assert len(out["subscriptions"]) <= 10
        assert "test" not in seen

    def test_subject_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/subsz":
                seen.update(dict(request.url.params))
            return _response_for(load_fixture("subsz_test_match.json"))

        def _fake_build(config: NatsConfig) -> httpx.Client:
            return httpx.Client(
                base_url="http://nats.example.net:8222",
                transport=httpx.MockTransport(handler),
            )

        monkeypatch.setattr(nats_client, "build_client", _fake_build)
        out = get_subscriptions(_config(), subject="telemetry.cpu")
        assert seen.get("test") == "telemetry.cpu"
        assert seen.get("subs") == "true"
        assert out["match_count"] == 1

    def test_subject_nomatch(self, patched_client) -> None:
        patched_client({"/subsz": load_fixture("subsz_test_nomatch.json")})
        out = get_subscriptions(_config(), subject="nothing.here")
        assert out["match_count"] == 0
        assert out["matches"] == []


class TestGetJetstreamStreams:
    def test_cluster(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict[str, str] = {}
        routes = _cluster_routes()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/jsz":
                seen.update(dict(request.url.params))
            return _response_for(routes[request.url.path])

        def _fake_build(config: NatsConfig) -> httpx.Client:
            return httpx.Client(
                base_url="http://nats.example.net:8222",
                transport=httpx.MockTransport(handler),
            )

        monkeypatch.setattr(nats_client, "build_client", _fake_build)
        out = get_jetstream_streams(_config())
        assert out["jetstream_enabled"] is True
        assert out["view"] == "server"
        assert out["server_name"] == "n1"
        assert out["clustered"] is True
        assert out["meta_leader"] == "n1"
        assert [s["name"] for s in out["streams"]] == ["ORDERS", "JOBS"]
        assert out["totals"]["messages"] == 625
        assert out["totals"]["api_errors"] == 4
        assert "n1" in (out["note"] or "")
        assert seen == {"streams": "true", "config": "true"}

    def test_filter(self, patched_client) -> None:
        patched_client(_cluster_routes())
        out = get_jetstream_streams(_config(), stream="JOBS")
        assert [s["name"] for s in out["streams"]] == ["JOBS"]

    def test_unknown_stream(self, patched_client) -> None:
        patched_client(_cluster_routes())
        out = get_jetstream_streams(_config(), stream="NOPE")
        assert out["streams"] == []
        assert "NOPE" in out["warnings"][0]

    def test_solo_disabled(self, patched_client) -> None:
        patched_client(_solo_routes())
        out = get_jetstream_streams(_config())
        assert out["jetstream_enabled"] is False
        assert "-js" in out["hint"]
        assert out["note"] is None


class TestGetJetstreamConsumers:
    def test_cluster(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict[str, str] = {}
        routes = _cluster_routes()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/jsz":
                seen.update(dict(request.url.params))
            return _response_for(routes[request.url.path])

        def _fake_build(config: NatsConfig) -> httpx.Client:
            return httpx.Client(
                base_url="http://nats.example.net:8222",
                transport=httpx.MockTransport(handler),
            )

        monkeypatch.setattr(nats_client, "build_client", _fake_build)
        out = get_jetstream_consumers(_config())
        assert len(out["consumers"]) == 3
        first = out["consumers"][0]
        assert (first["stream"], first["name"]) == ("ORDERS", "billing")
        assert first["authoritative"] is True
        assert out["summary"] == {
            "consumers": 3,
            "authoritative": 1,
            "with_backlog": 1,
            "with_ack_pending": 2,
            "with_redeliveries": 1,
            "never_delivered": 0,
            "pending_total": 330,
            "ack_pending_total": 21,
        }
        assert seen == {"streams": "true", "consumers": "true", "config": "true"}

    def test_filters(self, patched_client) -> None:
        patched_client(_cluster_routes())
        out = get_jetstream_consumers(_config(), stream="JOBS")
        assert [c["name"] for c in out["consumers"]] == ["worker"]
        out = get_jetstream_consumers(_config(), consumer="billing")
        assert len(out["consumers"]) == 1
        out = get_jetstream_consumers(_config(), consumer="nope")
        assert out["consumers"] == []
        assert len(out["warnings"]) == 1

    def test_solo_disabled(self, patched_client) -> None:
        patched_client(_solo_routes())
        out = get_jetstream_consumers(_config())
        assert out["jetstream_enabled"] is False
        assert out["consumers"] == []


class TestGetClusterStatus:
    def test_cluster(self, patched_client) -> None:
        patched_client(_cluster_routes())
        out = get_cluster_status(_config())
        assert out["clustered"] is True
        assert out["cluster"]["name"] == "opensre-demo"
        assert out["routes"]["summary"]["count"] == 8
        peers = {p["remote_name"]: p["connections"] for p in out["routes"]["summary"]["peers"]}
        assert peers == {"n2": 4, "n3": 4}
        assert out["jetstream_meta"]["leader"] == "n1"
        assert out["jetstream_meta"]["cluster_size"] == 3
        assert out["is_meta_leader"] is True

    def test_solo(self, patched_client) -> None:
        patched_client(_solo_routes())
        out = get_cluster_status(_config())
        assert out["clustered"] is False
        assert out["cluster"] is None
        assert out["routes"]["summary"]["count"] == 0
        assert out["jetstream_meta"] is None
        assert out["is_meta_leader"] is False
        assert out["jetstream_enabled"] is False

    def test_routez_failure_warns(self, patched_client) -> None:
        routes = _cluster_routes()
        routes["/routez"] = httpx.Response(HTTPStatus.NOT_FOUND, text="404 page not found\n")
        patched_client(routes)
        out = get_cluster_status(_config())
        assert out["available"] is True
        assert out["routes"] is None
        assert len(out["warnings"]) == 1
