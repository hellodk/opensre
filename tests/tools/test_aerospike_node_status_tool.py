"""Tests for AerospikeNodeStatusTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from integrations.aerospike.client import AsinfoBinaryNotFoundError, AsinfoTimeoutError
from integrations.aerospike.tools.aerospike_node_status_tool import get_aerospike_node_status
from tests.tools.conftest import BaseToolContract


class TestAerospikeNodeStatusToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_aerospike_node_status.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_aerospike_node_status.__opensre_registered_tool__
    assert rt.name == "get_aerospike_node_status"
    assert rt.source == "aerospike"
    assert rt.injected_params == ("host",)


def test_run_happy_path() -> None:
    fake_result = {"source": "aerospike", "available": True, "node_status": "ok"}
    with patch(
        "integrations.aerospike.tools.aerospike_node_status_tool.get_node_status",
        return_value=fake_result,
    ):
        result = get_aerospike_node_status(host="node1")
    assert result["node_status"] == "ok"
    assert result["available"] is True


def test_run_binary_not_found_propagated() -> None:
    with patch(
        "integrations.aerospike.tools.aerospike_node_status_tool.get_node_status",
        return_value={
            "source": "aerospike",
            "available": False,
            "error": "asinfo binary not found",
        },
    ):
        result = get_aerospike_node_status(host="node1")
    assert result["available"] is False
    assert "asinfo" in result["error"]


class TestAerospikeNodeStatusToolPath:
    """Exercise tool fn -> helper -> client -> shape via a mocked send_info_commands."""

    @patch("integrations.aerospike.send_info_commands")
    def test_tool_path_success(self, mock_send) -> None:
        mock_send.return_value = {
            "status": "ok",
            "statistics": "cluster_size=3;cluster_key=BB9;uptime=100;cluster_integrity=true;",
        }

        result = get_aerospike_node_status(host="node1", port=3000)

        assert result["available"] is True
        assert result["cluster_size"] == 3

    @patch("integrations.aerospike.send_info_commands")
    def test_tool_path_binary_not_found(self, mock_send) -> None:
        mock_send.side_effect = AsinfoBinaryNotFoundError("asinfo binary not found")

        result = get_aerospike_node_status(host="node1")

        assert result["available"] is False
        assert "asinfo" in result["error"]

    @patch("integrations.aerospike.send_info_commands")
    def test_tool_path_timeout(self, mock_send) -> None:
        mock_send.side_effect = AsinfoTimeoutError("asinfo command timed out after 5.0s")

        result = get_aerospike_node_status(host="node1")

        assert result["available"] is False
        assert "timed out" in result["error"]
