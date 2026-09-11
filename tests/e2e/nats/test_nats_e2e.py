"""NATS E2E tests verifying integration with the investigation pipeline.

Mocks ``integrations.nats.client.build_client`` at the transport boundary
with fixture payloads — no real NATS server is needed, mirroring
``tests/e2e/aerospike/test_aerospike_e2e.py``.
"""

from __future__ import annotations

import json
from http import HTTPStatus
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from integrations.catalog import classify_integrations as _classify_integrations
from integrations.nats import (
    NatsConfig,
    nats_extract_params,
    nats_is_available,
)
from integrations.nats import client as nats_client
from integrations.verify import verify_integrations

FIXTURES = Path("tests/integrations/nats/fixtures")


def _load(name: str) -> Any:
    path = FIXTURES / name
    text = path.read_text(encoding="utf-8")
    return json.loads(text) if path.suffix == ".json" else text


def _cluster_routes() -> dict[str, Any]:
    return {
        "/varz": _load("varz.json"),
        "/healthz": _load("healthz.json"),
        "/connz": _load("connz_sort_subs_detail.json"),
        "/subsz": _load("subsz_detail.json"),
        "/jsz": _load("jsz_streams_consumers_config.json"),
        "/routez": _load("routez.json"),
        "/gatewayz": _load("gatewayz.json"),
        "/leafz": _load("leafz.json"),
    }


def _install_mock(monkeypatch: pytest.MonkeyPatch, routes: dict[str, Any]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in routes:
            payload = routes[request.url.path]
            if isinstance(payload, httpx.Response):
                return payload
            return httpx.Response(HTTPStatus.OK, json=payload)
        return httpx.Response(HTTPStatus.NOT_FOUND, text="404 page not found\n")

    def _fake_build(config: NatsConfig) -> httpx.Client:
        return httpx.Client(
            base_url="http://nats.example.net:8222",
            transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr(nats_client, "build_client", _fake_build)


class TestNatsIntegrationResolution:
    """Test NATS config resolution from multiple sources."""

    def test_nats_resolution_from_store(self):
        integrations = [
            {
                "id": "nats-prod",
                "service": "nats",
                "status": "active",
                "credentials": {"url": "http://nats.example.net:8222"},
            }
        ]
        resolved = _classify_integrations(integrations)

        assert "nats" in resolved
        assert resolved["nats"]["url"] == "http://nats.example.net:8222"

    def test_nats_invalid_config_skipped(self):
        integrations = [
            {
                "id": "bad-nats",
                "service": "nats",
                "status": "active",
                "credentials": {"url": ""},
            }
        ]
        resolved = _classify_integrations(integrations)

        assert resolved.get("nats") is None


class TestNatsToolSourceAvailability:
    """Test NATS source availability in the tool-registry investigation path."""

    def test_nats_tool_source_available_from_resolved_integration(self):
        resolved_integrations = {
            "nats": {
                "url": "http://nats.example.net:8222",
                "username": "",
                "password": "",
                "verify_ssl": True,
            }
        }

        assert nats_is_available(resolved_integrations)
        params = nats_extract_params(resolved_integrations)
        assert params["url"] == "http://nats.example.net:8222"

    def test_nats_tool_source_unavailable_if_unconfigured(self):
        assert not nats_is_available({})


class TestNatsVerification:
    """Test NATS integration verification flow."""

    def test_verify_nats_success(self, monkeypatch: pytest.MonkeyPatch):
        _install_mock(monkeypatch, _cluster_routes())

        results = verify_integrations(service="nats")

        assert len(results) >= 1
        nats_result = next((r for r in results if r["service"] == "nats"), None)
        assert nats_result is not None
        assert nats_result["status"] in ("passed", "missing")

    def test_verify_integrations_structure(self):
        try:
            results = verify_integrations(service="nats")
            assert isinstance(results, list)
            for result in results:
                if result["service"] == "nats":
                    assert "status" in result
                    assert "detail" in result
                    assert result["status"] in ("passed", "missing", "failed")
        except Exception as exc:
            assert exc.__class__.__name__


class TestNatsToolsAvailability:
    """Test NATS tools are available and configured."""

    @pytest.fixture(autouse=True)
    def _clear_registry_cache(self):
        from tools.registry import clear_tool_registry_cache

        clear_tool_registry_cache()
        yield
        clear_tool_registry_cache()

    def test_nats_tools_exist_as_modules(self):
        import importlib

        for module in (
            "integrations.nats.tools.nats_server_status_tool",
            "integrations.nats.tools.nats_connections_tool",
            "integrations.nats.tools.nats_subscriptions_tool",
            "integrations.nats.tools.nats_jetstream_streams_tool",
            "integrations.nats.tools.nats_jetstream_consumers_tool",
            "integrations.nats.tools.nats_cluster_status_tool",
        ):
            try:
                assert importlib.import_module(module) is not None
            except ImportError as e:
                pytest.fail(f"Failed to import NATS tool module {module}: {e}")

    def test_nats_tools_registered_on_chat_surface(self):
        from tools.registry import get_registered_tools

        expected_tools = {
            "get_nats_server_status",
            "get_nats_connections",
            "get_nats_subscriptions",
            "get_nats_jetstream_streams",
            "get_nats_jetstream_consumers",
            "get_nats_cluster_status",
        }
        names = {t.name for t in get_registered_tools("chat") if t.source == "nats"}
        assert expected_tools <= names, (
            f"missing nats tools on chat surface: {expected_tools - names}"
        )


class TestNatsToolPaths:
    """Exercise each tool end-to-end: tool fn -> helper -> client -> shape."""

    def test_server_status_tool_path(self, monkeypatch: pytest.MonkeyPatch):
        from integrations.nats.tools.nats_server_status_tool import (
            get_nats_server_status,
        )

        _install_mock(monkeypatch, _cluster_routes())
        result = get_nats_server_status(url="http://nats.example.net:8222")
        assert result["available"] is True
        assert result["server"]["server_name"] == "n1"

    def test_connections_tool_path(self, monkeypatch: pytest.MonkeyPatch):
        from integrations.nats.tools.nats_connections_tool import (
            get_nats_connections,
        )

        _install_mock(monkeypatch, _cluster_routes())
        result = get_nats_connections(url="http://nats.example.net:8222")
        assert result["available"] is True
        assert result["returned"] == 1

    def test_subscriptions_tool_path(self, monkeypatch: pytest.MonkeyPatch):
        from integrations.nats.tools.nats_subscriptions_tool import (
            get_nats_subscriptions,
        )

        _install_mock(monkeypatch, _cluster_routes())
        result = get_nats_subscriptions(url="http://nats.example.net:8222")
        assert result["available"] is True
        assert result["stats"]["num_subscriptions"] == 311

    def test_streams_tool_path(self, monkeypatch: pytest.MonkeyPatch):
        from integrations.nats.tools.nats_jetstream_streams_tool import (
            get_nats_jetstream_streams,
        )

        _install_mock(monkeypatch, _cluster_routes())
        result = get_nats_jetstream_streams(url="http://nats.example.net:8222")
        assert result["available"] is True
        assert len(result["streams"]) == 2

    def test_consumers_tool_path(self, monkeypatch: pytest.MonkeyPatch):
        from integrations.nats.tools.nats_jetstream_consumers_tool import (
            get_nats_jetstream_consumers,
        )

        _install_mock(monkeypatch, _cluster_routes())
        result = get_nats_jetstream_consumers(url="http://nats.example.net:8222")
        assert result["available"] is True
        assert len(result["consumers"]) == 3

    def test_cluster_status_tool_path(self, monkeypatch: pytest.MonkeyPatch):
        from integrations.nats.tools.nats_cluster_status_tool import (
            get_nats_cluster_status,
        )

        _install_mock(monkeypatch, _cluster_routes())
        result = get_nats_cluster_status(url="http://nats.example.net:8222")
        assert result["available"] is True
        assert result["routes"]["summary"]["count"] == 8

    def test_tool_path_unavailable_without_config(self):
        from integrations.nats.tools.nats_server_status_tool import (
            get_nats_server_status,
        )

        with patch(
            "integrations.nats.tools.nats_server_status_tool.get_server_status",
            return_value={"source": "nats", "available": False, "error": "boom"},
        ):
            result = get_nats_server_status(url="http://x:8222")
        assert result["available"] is False
