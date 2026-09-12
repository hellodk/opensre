"""Tests for get_nats_subscriptions tool."""

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
from integrations.nats.tools.nats_subscriptions_tool import (
    _map_get_nats_subscriptions,
    get_nats_subscriptions,
)
from tests.tools.conftest import BaseToolContract

FIXTURES = Path("tests/integrations/nats/fixtures")


def _load(name: str) -> Any:
    path = FIXTURES / name
    text = path.read_text(encoding="utf-8")
    return json.loads(text) if path.suffix == ".json" else text


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


class TestNatsSubscriptionsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_nats_subscriptions.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_nats_subscriptions.__opensre_registered_tool__
    assert rt.name == "get_nats_subscriptions"
    assert rt.source == "nats"
    assert rt.injected_params == ("url", "username", "password", "verify_ssl")
    assert set(rt.public_input_schema["properties"]) == {"subject"}


def test_happy_path(patched_client) -> None:
    patched_client({"/subsz": _load("subsz_detail.json")})
    result = get_nats_subscriptions(url="http://nats.example.net:8222")
    assert result["available"] is True
    assert result["stats"]["num_subscriptions"] == 311


def test_evidence_mapper() -> None:
    evidence: dict[str, Any] = {}
    _map_get_nats_subscriptions(
        evidence,
        {
            "available": True,
            "subject": "",
            "stats": {"num_subscriptions": 311, "cache_hit_rate": 0.44},
            "summary": {"top_by_msgs": [{"subject": "telemetry.>", "msgs": 16000}]},
        },
        {},
    )
    assert "311 subscription(s)" in evidence["catalog_entries"][0]["summary"]
    assert "telemetry.>" in evidence["catalog_entries"][0]["summary"]


def test_evidence_mapper_with_subject() -> None:
    evidence: dict[str, Any] = {}
    _map_get_nats_subscriptions(
        evidence,
        {
            "available": True,
            "subject": "telemetry.cpu",
            "match_count": 1,
            "matches": [],
        },
        {},
    )
    assert "telemetry.cpu" in evidence["catalog_entries"][0]["summary"]


def test_unavailable_early_return() -> None:
    evidence: dict[str, Any] = {}
    _map_get_nats_subscriptions(evidence, {"source": "nats", "available": False}, {})
    assert "catalog_entries" not in evidence


def test_degraded_nomatch(patched_client) -> None:
    patched_client({"/subsz": _load("subsz_test_nomatch.json")})
    result = get_nats_subscriptions(url="http://nats.example.net:8222", subject="nothing.here")
    assert result["available"] is True
    assert result["match_count"] == 0


def test_run_unavailable_propagated() -> None:
    with patch(
        "integrations.nats.tools.nats_subscriptions_tool.get_subscriptions",
        return_value={"source": "nats", "available": False, "error": "boom"},
    ):
        result = get_nats_subscriptions(url="http://x:8222")
    assert result["available"] is False
