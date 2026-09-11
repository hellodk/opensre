"""Tests for get_nats_cluster_status tool."""

from __future__ import annotations

import json
from http import HTTPStatus
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from integrations.nats import NatsConfig
from integrations.nats import client as nats_client
from integrations.nats.tools.nats_cluster_status_tool import (
    _map_get_nats_cluster_status,
    get_nats_cluster_status,
)
from tests.tools.conftest import BaseToolContract

FIXTURES = Path("tests/integrations/nats/fixtures")


def _load(name: str) -> Any:
    path = FIXTURES / name
    text = path.read_text(encoding="utf-8")
    return json.loads(text) if path.suffix == ".json" else text


def _routes() -> dict[str, Any]:
    return {
        "/varz": _load("varz.json"),
        "/routez": _load("routez.json"),
        "/gatewayz": _load("gatewayz.json"),
        "/leafz": _load("leafz.json"),
        "/jsz": _load("jsz.json"),
    }


def _mock_transport(routes: dict[str, Any]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in routes:
            payload = routes[request.url.path]
            if isinstance(payload, httpx.Response):
                return payload
            return httpx.Response(HTTPStatus.OK, json=payload)
        return httpx.Response(HTTPStatus.NOT_FOUND, text="404 page not found\n")

    return httpx.MockTransport(handler)


@pytest.fixture
def patched_client(monkeypatch: pytest.MonkeyPatch):
    def install(routes: dict[str, Any]) -> None:
        def _fake_build(config: NatsConfig) -> httpx.Client:
            return httpx.Client(
                base_url="http://nats.example.net:8222",
                transport=_mock_transport(routes),
            )

        monkeypatch.setattr(nats_client, "build_client", _fake_build)

    return install


class TestNatsClusterStatusToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_nats_cluster_status.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_nats_cluster_status.__opensre_registered_tool__
    assert rt.name == "get_nats_cluster_status"
    assert rt.source == "nats"
    assert rt.injected_params == ("url", "username", "password", "verify_ssl")
    assert set(rt.public_input_schema["properties"]) == set()


def test_happy_path(patched_client) -> None:
    patched_client(_routes())
    result = get_nats_cluster_status(url="http://nats.example.net:8222")
    assert result["available"] is True
    assert result["routes"]["summary"]["count"] == 8


def test_evidence_mapper() -> None:
    evidence: dict[str, Any] = {}
    _map_get_nats_cluster_status(
        evidence,
        {
            "available": True,
            "clustered": True,
            "server_name": "n1",
            "cluster": {"name": "opensre-demo"},
            "routes": {"summary": {"count": 8, "peers": [{}, {}]}},
            "jetstream_meta": {"leader": "n1", "cluster_size": 3},
        },
        {},
    )
    assert "opensre-demo" in evidence["catalog_entries"][0]["summary"]


def test_evidence_mapper_standalone() -> None:
    evidence: dict[str, Any] = {}
    _map_get_nats_cluster_status(
        evidence,
        {"available": True, "clustered": False, "server_name": "solo"},
        {},
    )
    assert "standalone" in evidence["catalog_entries"][0]["summary"]


def test_unavailable_early_return() -> None:
    evidence: dict[str, Any] = {}
    _map_get_nats_cluster_status(evidence, {"source": "nats", "available": False}, {})
    assert "catalog_entries" not in evidence


def test_degraded_standalone(patched_client) -> None:
    patched_client(
        {
            "/varz": _load("varz_solo.json"),
            "/routez": _load("routez_solo.json"),
            "/gatewayz": _load("gatewayz_solo.json"),
            "/leafz": _load("leafz_solo.json"),
            "/jsz": _load("jsz_solo.json"),
        }
    )
    result = get_nats_cluster_status(url="http://nats.example.net:8222")
    assert result["available"] is True
    assert result["clustered"] is False


def test_run_unavailable_propagated() -> None:
    with patch(
        "integrations.nats.tools.nats_cluster_status_tool.get_cluster_status",
        return_value={"source": "nats", "available": False, "error": "boom"},
    ):
        result = get_nats_cluster_status(url="http://x:8222")
    assert result["available"] is False
