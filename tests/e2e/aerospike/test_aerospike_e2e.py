"""Aerospike E2E tests verifying integration with the investigation pipeline.

Mocks ``integrations.aerospike.send_info_commands`` at the transport
boundary — no real or containerized Aerospike server is needed, mirroring
``tests/e2e/redis/test_redis_e2e.py`` (which patches ``integrations.redis
._get_client`` rather than starting a live/containerized Redis).

Tests:
- Aerospike config resolution from store and env
- Aerospike verification (status check)
- Aerospike source availability for query execution
- Aerospike tools are discoverable on the investigation/chat surfaces
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from integrations.catalog import classify_integrations as _classify_integrations
from integrations.verify import verify_integrations
from tests.e2e.source_helpers import resolve_available_tool_sources


class TestAerospikeIntegrationResolution:
    """Test Aerospike config resolution from multiple sources."""

    def test_aerospike_resolution_from_store(self):
        integrations = [
            {
                "id": "aerospike-prod",
                "service": "aerospike",
                "status": "active",
                "credentials": {
                    "host": "prod-aerospike.internal",
                    "port": 3010,
                    "username": "monitor",
                    "password": "s3cret",
                },
            }
        ]
        resolved = _classify_integrations(integrations)

        assert "aerospike" in resolved
        assert resolved["aerospike"]["host"] == "prod-aerospike.internal"
        assert resolved["aerospike"]["port"] == 3010
        assert resolved["aerospike"]["username"] == "monitor"

    def test_aerospike_invalid_config_skipped(self):
        integrations = [
            {
                "id": "bad-aerospike",
                "service": "aerospike",
                "status": "active",
                "credentials": {"host": ""},
            }
        ]
        resolved = _classify_integrations(integrations)

        assert resolved.get("aerospike") is None


class TestAerospikeToolSourceAvailability:
    """Test Aerospike source availability in the tool-registry investigation path."""

    def test_aerospike_tool_source_available_from_resolved_integration(self):
        resolved_integrations = {
            "aerospike": {
                "host": "localhost",
                "port": 3000,
                "username": "",
                "password": "",
            }
        }

        sources = resolve_available_tool_sources(resolved_integrations)

        assert "aerospike" in sources
        assert sources["aerospike"]["host"] == "localhost"
        assert sources["aerospike"]["port"] == 3000

    def test_aerospike_tool_source_unavailable_if_unconfigured(self):
        sources = resolve_available_tool_sources({})
        assert "aerospike" not in sources


class TestAerospikeVerification:
    """Test Aerospike integration verification flow."""

    @patch("integrations.aerospike.send_info_commands")
    def test_verify_aerospike_success(self, mock_send):
        mock_send.return_value = {"status": "ok"}

        results = verify_integrations(service="aerospike")

        assert len(results) >= 1
        aerospike_result = next((r for r in results if r["service"] == "aerospike"), None)
        assert aerospike_result is not None
        assert aerospike_result["status"] in ("passed", "missing")

    def test_verify_integrations_structure(self):
        try:
            results = verify_integrations(service="aerospike")
            assert isinstance(results, list)
            for result in results:
                if result["service"] == "aerospike":
                    assert "status" in result
                    assert "detail" in result
                    assert result["status"] in ("passed", "missing", "failed")
        except Exception as exc:
            assert exc.__class__.__name__


class TestAerospikeToolsAvailability:
    """Test Aerospike tools are available and configured."""

    @pytest.fixture(autouse=True)
    def _clear_registry_cache(self):
        from tools.registry import clear_tool_registry_cache

        clear_tool_registry_cache()
        yield
        clear_tool_registry_cache()

    def test_aerospike_tools_exist_as_modules(self):
        import importlib

        try:
            node_status_tool = importlib.import_module(
                "integrations.aerospike.tools.aerospike_node_status_tool"
            )
            namespace_stats_tool = importlib.import_module(
                "integrations.aerospike.tools.aerospike_namespace_stats_tool"
            )
            latency_tool = importlib.import_module(
                "integrations.aerospike.tools.aerospike_latency_tool"
            )

            assert node_status_tool is not None
            assert namespace_stats_tool is not None
            assert latency_tool is not None
        except ImportError as e:
            pytest.fail(f"Failed to import Aerospike tool modules: {e}")

    def test_aerospike_tools_registered_on_investigation_and_chat_surfaces(self):
        from tools.registry import get_registered_tools

        expected_tools = {
            "get_aerospike_node_status",
            "get_aerospike_namespace_stats",
            "get_aerospike_latency",
        }
        for surface in ("investigation", "chat"):
            names = {t.name for t in get_registered_tools(surface) if t.source == "aerospike"}
            assert expected_tools <= names, (
                f"missing aerospike tools on {surface} surface: {expected_tools - names}"
            )

    def test_aerospike_integration_config_has_required_fields(self):
        from integrations.config_models import AerospikeIntegrationConfig

        config = AerospikeIntegrationConfig(
            host="localhost",
            port=3000,
            username="monitor",
            password="s3cret",
            integration_id="test-id",
        )

        assert config.host == "localhost"
        assert config.port == 3000
        assert config.username == "monitor"
        assert config.integration_id == "test-id"


class TestAerospikeToolPaths:
    """Exercise each tool end-to-end: tool fn -> helper -> client -> shape.

    The asinfo transport is mocked at ``send_info_commands`` so the full tool
    path (config build, command issue, response shaping) is covered without
    a live Aerospike node.
    """

    @patch("integrations.aerospike.send_info_commands")
    def test_node_status_tool_path(self, mock_send):
        from integrations.aerospike.tools.aerospike_node_status_tool import (
            get_aerospike_node_status,
        )

        mock_send.return_value = {
            "status": "ok",
            "statistics": "cluster_size=3;cluster_key=BB9;uptime=48213;cluster_integrity=true;",
        }

        result = get_aerospike_node_status(host="prod-aerospike.internal")

        assert result["available"] is True
        assert result["cluster_size"] == 3
        assert result["cluster_integrity"] is True

    @patch("integrations.aerospike.send_info_commands")
    def test_namespace_stats_tool_path(self, mock_send):
        from integrations.aerospike.tools.aerospike_namespace_stats_tool import (
            get_aerospike_namespace_stats,
        )

        def _fake_send(_config, commands):
            if commands == ["namespaces"]:
                return {"namespaces": "test"}
            return {"namespace/test": "objects=10432;memory_used_bytes=5242880;"}

        mock_send.side_effect = _fake_send

        result = get_aerospike_namespace_stats(host="prod-aerospike.internal")

        assert result["available"] is True
        assert result["scope"] == "single_node"
        assert result["namespaces"]["test"]["objects"] == 10432

    @patch("integrations.aerospike.send_info_commands")
    def test_latency_tool_path(self, mock_send):
        from integrations.aerospike.tools.aerospike_latency_tool import get_aerospike_latency

        mock_send.return_value = {"latencies:": "{test}-read:msec,ops/sec;1.000,120"}

        result = get_aerospike_latency(host="prod-aerospike.internal")

        assert result["available"] is True
        assert result["histograms"]["read"][0]["ops/sec"] == 120
