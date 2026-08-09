"""Tests for YugabyteDBServerStatusTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from integrations.yugabytedb.tools.yugabytedb_server_status_tool import get_yugabytedb_server_status
from tests.tools.conftest import BaseToolContract


class TestYugabyteDBServerStatusToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_yugabytedb_server_status.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_yugabytedb_server_status.__opensre_registered_tool__
    assert rt.name == "get_yugabytedb_server_status"
    assert rt.source == "yugabytedb"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "yugabytedb",
        "available": True,
        "version": "2.20.0.0 (PG 11.2 compat, build 0)",
        "connected_node": "10.0.1.5",
        "connections": {"total": 25, "active": 15, "idle": 10, "max_connections": 100},
        "database_stats": {
            "backends": 25,
            "transactions": {"committed": 12345, "rolled_back": 123},
            "cache_hit_ratio_percent": 95.5,
            "tuples": {
                "returned": 987654,
                "fetched": 543210,
                "inserted": 1234,
                "updated": 567,
                "deleted": 89,
            },
        },
    }
    with patch(
        "integrations.yugabytedb.tools.yugabytedb_server_status_tool.get_server_status",
        return_value=fake_result,
    ):
        result = get_yugabytedb_server_status(host="localhost", database="testdb")
    assert result["version"] == "2.20.0.0 (PG 11.2 compat, build 0)"
    assert result["connected_node"] == "10.0.1.5"
    assert result["connections"]["total"] == 25
    assert result["database_stats"]["cache_hit_ratio_percent"] == 95.5


def test_run_error_propagated() -> None:
    with patch(
        "integrations.yugabytedb.tools.yugabytedb_server_status_tool.get_server_status",
        return_value={"source": "yugabytedb", "available": False, "error": "connection timeout"},
    ):
        result = get_yugabytedb_server_status(host="invalid", database="testdb")
    assert "error" in result
    assert result["available"] is False
