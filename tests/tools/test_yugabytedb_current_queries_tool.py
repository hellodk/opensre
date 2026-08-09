"""Tests for YugabyteDBCurrentQueriesTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from integrations.yugabytedb.tools.yugabytedb_current_queries_tool import (
    get_yugabytedb_current_queries,
)
from tests.tools.conftest import BaseToolContract


class TestYugabyteDBCurrentQueriesToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_yugabytedb_current_queries.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_yugabytedb_current_queries.__opensre_registered_tool__
    assert rt.name == "get_yugabytedb_current_queries"
    assert rt.source == "yugabytedb"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "yugabytedb",
        "available": True,
        "connected_node": "10.0.1.5",
        "threshold_seconds": 2,
        "total_queries": 2,
        "queries": [
            {
                "pid": 12345,
                "username": "app_user",
                "application_name": "myapp",
                "client_addr": "192.168.1.100",
                "state": "active",
                "query_start": "2024-01-15 10:30:00",
                "duration_seconds": 15,
                "wait_event_type": "",
                "wait_event": "",
                "query_truncated": "SELECT * FROM large_table WHERE id IN (SELECT...",
            },
            {
                "pid": 12346,
                "username": "analytics",
                "application_name": "report_generator",
                "client_addr": "local",
                "state": "active",
                "query_start": "2024-01-15 10:29:45",
                "duration_seconds": 30,
                "wait_event_type": "IO",
                "wait_event": "DataFileRead",
                "query_truncated": "SELECT COUNT(*) FROM events WHERE created_at...",
            },
        ],
    }
    with patch(
        "integrations.yugabytedb.tools.yugabytedb_current_queries_tool.get_current_queries",
        return_value=fake_result,
    ):
        result = get_yugabytedb_current_queries(
            host="localhost", database="testdb", threshold_seconds=2
        )
    assert result["threshold_seconds"] == 2
    assert result["total_queries"] == 2
    assert len(result["queries"]) == 2
    assert result["queries"][0]["duration_seconds"] == 15
    assert result["connected_node"] == "10.0.1.5"


def test_run_connection_refused_error() -> None:
    with patch(
        "integrations.yugabytedb.tools.yugabytedb_current_queries_tool.get_current_queries",
        return_value={
            "source": "yugabytedb",
            "available": False,
            "error": "connection refused",
        },
    ):
        result = get_yugabytedb_current_queries(host="unreachable", database="testdb")
    assert "error" in result
    assert result["available"] is False


def test_run_auth_failure_error() -> None:
    with patch(
        "integrations.yugabytedb.tools.yugabytedb_current_queries_tool.get_current_queries",
        return_value={
            "source": "yugabytedb",
            "available": False,
            "error": "password authentication failed for user",
        },
    ):
        result = get_yugabytedb_current_queries(host="localhost", database="testdb")
    assert "error" in result
    assert result["available"] is False


def test_default_db_warning_present_when_database_omitted() -> None:
    with patch(
        "integrations.yugabytedb.tools.yugabytedb_current_queries_tool.get_current_queries",
        return_value={"source": "yugabytedb", "available": True, "queries": []},
    ):
        result = get_yugabytedb_current_queries(host="localhost")
    assert "default_db_warning" in result
    assert "yugabyte" in result["default_db_warning"]


def test_no_default_db_warning_when_database_provided() -> None:
    with patch(
        "integrations.yugabytedb.tools.yugabytedb_current_queries_tool.get_current_queries",
        return_value={"source": "yugabytedb", "available": True, "queries": []},
    ):
        result = get_yugabytedb_current_queries(host="localhost", database="mydb")
    assert "default_db_warning" not in result
