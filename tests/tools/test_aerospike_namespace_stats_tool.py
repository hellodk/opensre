"""Tests for AerospikeNamespaceStatsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from integrations.aerospike.client import AsinfoConnectionError
from integrations.aerospike.tools.aerospike_namespace_stats_tool import (
    get_aerospike_namespace_stats,
)
from tests.tools.conftest import BaseToolContract


class TestAerospikeNamespaceStatsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_aerospike_namespace_stats.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_aerospike_namespace_stats.__opensre_registered_tool__
    assert rt.name == "get_aerospike_namespace_stats"
    assert rt.source == "aerospike"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "aerospike",
        "available": True,
        "scope": "single_node",
        "namespace_count": 1,
        "namespaces": {"test": {"objects": 10}},
        "truncated": False,
    }
    with patch(
        "integrations.aerospike.tools.aerospike_namespace_stats_tool.get_namespace_stats",
        return_value=fake_result,
    ):
        result = get_aerospike_namespace_stats(host="node1")
    assert result["scope"] == "single_node"
    assert result["namespaces"]["test"]["objects"] == 10


class TestAerospikeNamespaceStatsToolPath:
    @patch("integrations.aerospike.send_info_commands")
    def test_tool_path_all_namespaces(self, mock_send) -> None:
        def _fake_send(_config, commands):
            if commands == ["namespaces"]:
                return {"namespaces": "test;bar"}
            return dict.fromkeys(commands, "objects=1;")

        mock_send.side_effect = _fake_send

        result = get_aerospike_namespace_stats(host="node1")

        assert result["available"] is True
        assert result["namespace_count"] == 2

    @patch("integrations.aerospike.send_info_commands")
    def test_tool_path_single_namespace_filter(self, mock_send) -> None:
        mock_send.return_value = {"namespace/test": "objects=42;"}

        result = get_aerospike_namespace_stats(host="node1", namespace="test")

        assert result["namespace_count"] == 1
        assert result["namespaces"]["test"]["objects"] == 42

    @patch("integrations.aerospike.send_info_commands")
    def test_namespace_stats_truncates_when_exceeding_max_results(self, mock_send) -> None:
        names = [f"ns{i}" for i in range(60)]

        def _fake_send(_config, commands):
            if commands == ["namespaces"]:
                return {"namespaces": ";".join(names)}
            return dict.fromkeys(commands, "objects=1;")

        mock_send.side_effect = _fake_send

        result = get_aerospike_namespace_stats(host="node1")

        # AerospikeConfig's default max_results is 50.
        assert result["namespace_count"] == 50
        assert result["truncated"] is True

    @patch("integrations.aerospike.send_info_commands")
    def test_tool_path_connection_failure(self, mock_send) -> None:
        mock_send.side_effect = AsinfoConnectionError(
            "asinfo exited with code 1: connection refused", returncode=1, stderr="refused"
        )

        result = get_aerospike_namespace_stats(host="node1")

        assert result["available"] is False
        assert "refused" in result["error"]
