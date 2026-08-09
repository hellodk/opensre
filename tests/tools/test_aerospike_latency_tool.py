"""Tests for AerospikeLatencyTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from integrations.aerospike.client import AsinfoTimeoutError
from integrations.aerospike.tools.aerospike_latency_tool import get_aerospike_latency
from tests.tools.conftest import BaseToolContract


class TestAerospikeLatencyToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_aerospike_latency.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_aerospike_latency.__opensre_registered_tool__
    assert rt.name == "get_aerospike_latency"
    assert rt.source == "aerospike"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "aerospike",
        "available": True,
        "histograms": {"read": [{"time_window_sec": 1, "ops_per_sec": 120}]},
    }
    with patch(
        "integrations.aerospike.tools.aerospike_latency_tool.get_latency",
        return_value=fake_result,
    ):
        result = get_aerospike_latency(host="node1")
    assert "histograms" in result


class TestAerospikeLatencyToolPath:
    @patch("integrations.aerospike.send_info_commands")
    def test_tool_path_structured_response(self, mock_send) -> None:
        mock_send.return_value = {"latencies:": "{test}-write:msec,ops/sec;1.000,20"}

        result = get_aerospike_latency(host="node1")

        assert result["available"] is True
        assert result["histograms"]["write"][0]["ops/sec"] == 20

    @patch("integrations.aerospike.send_info_commands")
    def test_tool_path_degrades_to_raw_on_unrecognized_shape(self, mock_send) -> None:
        mock_send.return_value = {"latencies:": "error-no-data-yet-or-back-too-small"}

        result = get_aerospike_latency(host="node1")

        assert result["available"] is True
        assert result["raw"] == "error-no-data-yet-or-back-too-small"
        assert "histograms" not in result

    @patch("integrations.aerospike.send_info_commands")
    def test_tool_path_timeout(self, mock_send) -> None:
        mock_send.side_effect = AsinfoTimeoutError("asinfo command timed out after 5.0s")

        result = get_aerospike_latency(host="node1")

        assert result["available"] is False
        assert "timed out" in result["error"]
