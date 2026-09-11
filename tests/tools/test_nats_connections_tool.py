"""Tests for get_nats_connections tool."""

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
from integrations.nats.tools.nats_connections_tool import (
    _map_get_nats_connections,
    get_nats_connections,
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
        "/connz": _load("connz_sort_subs_detail.json"),
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


class TestNatsConnectionsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_nats_connections.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_nats_connections.__opensre_registered_tool__
    assert rt.name == "get_nats_connections"
    assert rt.source == "nats"
    assert rt.injected_params == ("url", "username", "password", "verify_ssl")
    assert set(rt.public_input_schema["properties"]) == {"sort", "state", "limit"}


def test_happy_path(patched_client) -> None:
    patched_client(_routes())
    result = get_nats_connections(url="http://nats.example.net:8222")
    assert result["available"] is True
    assert result["returned"] == 1
    assert result["connections"][0]["cid"] == 51


def test_evidence_mapper() -> None:
    evidence: dict[str, Any] = {}
    _map_get_nats_connections(
        evidence,
        {
            "available": True,
            "returned": 1,
            "total": 1,
            "state": "open",
            "summary": {
                "pending_bytes_total": 0,
                "slow_consumer_closures": 0,
                "top_pending": [],
            },
        },
        {},
    )
    assert "1 of 1 open" in evidence["catalog_entries"][0]["summary"]


def test_unavailable_early_return() -> None:
    evidence: dict[str, Any] = {}
    _map_get_nats_connections(evidence, {"source": "nats", "available": False}, {})
    assert "catalog_entries" not in evidence


def test_degraded_bad_sort(patched_client) -> None:
    patched_client(_routes())
    result = get_nats_connections(url="http://nats.example.net:8222", sort="bogus")
    assert result["available"] is False
    assert "Allowed" in result["error"]


def test_run_unavailable_propagated() -> None:
    with patch(
        "integrations.nats.tools.nats_connections_tool.get_connections",
        return_value={"source": "nats", "available": False, "error": "boom"},
    ):
        result = get_nats_connections(url="http://x:8222")
    assert result["available"] is False
