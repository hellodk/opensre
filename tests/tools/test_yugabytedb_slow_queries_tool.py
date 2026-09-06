"""Tests for YugabyteDBSlowQueriesTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from integrations.yugabytedb.tools.yugabytedb_slow_queries_tool import get_yugabytedb_slow_queries
from tests.tools.conftest import BaseToolContract


class TestYugabyteDBSlowQueriesToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_yugabytedb_slow_queries.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_yugabytedb_slow_queries.__opensre_registered_tool__
    assert rt.name == "get_yugabytedb_slow_queries"
    assert rt.source == "yugabytedb"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "yugabytedb",
        "available": True,
        "connected_node": "10.0.1.5",
        "extension_available": True,
        "threshold_ms": 500,
        "total_queries": 2,
        "queries": [
            {
                "queryid": "1234567890123456789",
                "query_truncated": "SELECT * FROM large_table lt JOIN other_table ot ON lt.id = ot.large_id",
                "calls": 145,
                "total_time_ms": 72500,
                "mean_time_ms": 500,
                "min_time_ms": 200,
                "max_time_ms": 2500,
                "stddev_time_ms": 150,
                "total_rows": 14500,
                "cache_hit_percent": 85.2,
            },
            {
                "queryid": "9876543210987654321",
                "query_truncated": "UPDATE users SET last_login = $1 WHERE id = $2",
                "calls": 1023,
                "total_time_ms": 1534500,
                "mean_time_ms": 1500,
                "min_time_ms": 800,
                "max_time_ms": 5000,
                "stddev_time_ms": 250,
                "total_rows": 1023,
                "cache_hit_percent": 99.1,
            },
        ],
    }
    with patch(
        "integrations.yugabytedb.tools.yugabytedb_slow_queries_tool.get_slow_queries",
        return_value=fake_result,
    ):
        result = get_yugabytedb_slow_queries(host="localhost", database="testdb", threshold_ms=500)
    assert result["extension_available"] is True
    assert result["threshold_ms"] == 500
    assert result["total_queries"] == 2
    assert len(result["queries"]) == 2
    assert result["queries"][0]["mean_time_ms"] == 500
    assert result["connected_node"] == "10.0.1.5"


def test_run_extension_not_available() -> None:
    fake_result = {
        "source": "yugabytedb",
        "available": True,
        "connected_node": "10.0.1.5",
        "extension_available": False,
        "note": (
            "pg_stat_statements extension is not installed. "
            "Install it with CREATE EXTENSION pg_stat_statements; "
            "and add 'pg_stat_statements' to shared_preload_libraries."
        ),
        "queries": [],
    }
    with patch(
        "integrations.yugabytedb.tools.yugabytedb_slow_queries_tool.get_slow_queries",
        return_value=fake_result,
    ):
        result = get_yugabytedb_slow_queries(host="localhost", database="testdb")
    assert result["extension_available"] is False
    assert "note" in result
    assert len(result["queries"]) == 0
    assert "error" not in result


def test_run_error_propagated() -> None:
    with patch(
        "integrations.yugabytedb.tools.yugabytedb_slow_queries_tool.get_slow_queries",
        return_value={
            "source": "yugabytedb",
            "available": False,
            "error": "database does not exist",
        },
    ):
        result = get_yugabytedb_slow_queries(host="localhost", database="invalid_db")
    assert "error" in result
    assert result["available"] is False


def test_default_db_warning_present_when_database_omitted() -> None:
    with patch(
        "integrations.yugabytedb.tools.yugabytedb_slow_queries_tool.get_slow_queries",
        return_value={"source": "yugabytedb", "available": True, "queries": []},
    ):
        result = get_yugabytedb_slow_queries(host="localhost")
    assert "default_db_warning" in result
    assert "yugabyte" in result["default_db_warning"]


def test_no_default_db_warning_when_database_provided() -> None:
    with patch(
        "integrations.yugabytedb.tools.yugabytedb_slow_queries_tool.get_slow_queries",
        return_value={"source": "yugabytedb", "available": True, "queries": []},
    ):
        result = get_yugabytedb_slow_queries(host="localhost", database="mydb")
    assert "default_db_warning" not in result
