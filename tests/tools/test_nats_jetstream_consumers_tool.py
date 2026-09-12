"""Tests for get_nats_jetstream_consumers tool."""

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
from integrations.nats.tools.nats_jetstream_consumers_tool import (
    _map_get_nats_jetstream_consumers,
    get_nats_jetstream_consumers,
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
        "/jsz": _load("jsz_streams_consumers_config.json"),
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


class TestNatsJetstreamConsumersToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_nats_jetstream_consumers.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_nats_jetstream_consumers.__opensre_registered_tool__
    assert rt.name == "get_nats_jetstream_consumers"
    assert rt.source == "nats"
    assert rt.injected_params == ("url", "username", "password", "verify_ssl")
    assert set(rt.public_input_schema["properties"]) == {"stream", "consumer"}


def test_happy_path(patched_client) -> None:
    patched_client(_routes())
    result = get_nats_jetstream_consumers(url="http://nats.example.net:8222")
    assert result["available"] is True
    assert len(result["consumers"]) == 3
    assert result["consumers"][0]["name"] == "billing"


def test_evidence_mapper() -> None:
    evidence: dict[str, Any] = {}
    _map_get_nats_jetstream_consumers(
        evidence,
        {
            "available": True,
            "server_name": "n1",
            "consumers": [{"stream": "ORDERS", "name": "billing", "num_pending": 330}],
            "summary": {
                "consumers": 1,
                "pending_total": 330,
                "ack_pending_total": 20,
                "with_redeliveries": 1,
            },
        },
        {},
    )
    summary = evidence["catalog_entries"][0]["summary"]
    assert "330 pending" in summary
    assert "redelivering" in summary


def test_unavailable_early_return() -> None:
    evidence: dict[str, Any] = {}
    _map_get_nats_jetstream_consumers(evidence, {"source": "nats", "available": False}, {})
    assert "catalog_entries" not in evidence


def test_degraded_jetstream_disabled(patched_client) -> None:
    patched_client(
        {
            "/varz": _load("varz_solo.json"),
            "/jsz": _load("jsz_solo_streams.json"),
        }
    )
    result = get_nats_jetstream_consumers(url="http://nats.example.net:8222")
    assert result["available"] is True
    assert result["jetstream_enabled"] is False


def test_run_unavailable_propagated() -> None:
    with patch(
        "integrations.nats.tools.nats_jetstream_consumers_tool.get_jetstream_consumers",
        return_value={"source": "nats", "available": False, "error": "boom"},
    ):
        result = get_nats_jetstream_consumers(url="http://x:8222")
    assert result["available"] is False
