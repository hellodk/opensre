"""Tests for YugabyteDBTableStatsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from integrations.yugabytedb.tools.yugabytedb_table_stats_tool import get_yugabytedb_table_stats
from tests.tools.conftest import BaseToolContract


class TestYugabyteDBTableStatsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_yugabytedb_table_stats.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_yugabytedb_table_stats.__opensre_registered_tool__
    assert rt.name == "get_yugabytedb_table_stats"
    assert rt.source == "yugabytedb"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "yugabytedb",
        "available": True,
        "connected_node": "10.0.1.5",
        "schema": "public",
        "total_tables": 1,
        "tables": [
            {
                "schema": "public",
                "table_name": "events",
                "tuples": {
                    "inserted": 1500000,
                    "updated": 75000,
                    "deleted": 25000,
                    "live": 1450000,
                    "dead": 0,
                },
                "scans": {
                    "sequential": 25,
                    "sequential_tuples": 362500,
                    "index": 15420,
                    "index_tuples": 1450000,
                    "index_usage_percent": 99.8,
                },
                "maintenance": {
                    "last_vacuum": None,
                    "last_autovacuum": None,
                    "last_analyze": None,
                    "last_autoanalyze": None,
                },
                "size": {
                    "total_bytes": 536870912,
                    "table_bytes": 402653184,
                    "indexes_bytes": 134217728,
                    "total_mb": 512.0,
                },
            },
        ],
        "note": (
            "YugabyteDB's DocDB storage engine does not run PostgreSQL's "
            "autovacuum daemon: n_dead_tup, last_vacuum, last_autovacuum, "
            "and last_analyze typically read 0/NULL and are not actionable "
            "maintenance signals. Table size fields reflect the values "
            "reported by pg_total_relation_size()/pg_relation_size() as-is."
        ),
    }
    with patch(
        "integrations.yugabytedb.tools.yugabytedb_table_stats_tool.get_table_stats",
        return_value=fake_result,
    ):
        result = get_yugabytedb_table_stats(
            host="localhost", database="testdb", schema_name="public"
        )
    assert result["schema"] == "public"
    assert result["total_tables"] == 1
    assert result["tables"][0]["table_name"] == "events"
    assert result["tables"][0]["maintenance"]["last_vacuum"] is None
    assert "note" in result


def test_run_custom_schema() -> None:
    fake_result = {
        "source": "yugabytedb",
        "available": True,
        "connected_node": "10.0.1.5",
        "schema": "analytics",
        "total_tables": 1,
        "tables": [
            {
                "schema": "analytics",
                "table_name": "reports",
                "tuples": {
                    "inserted": 10000,
                    "updated": 2000,
                    "deleted": 500,
                    "live": 9500,
                    "dead": 0,
                },
                "scans": {
                    "sequential": 10,
                    "sequential_tuples": 95000,
                    "index": 250,
                    "index_tuples": 23750,
                    "index_usage_percent": 96.2,
                },
                "maintenance": {
                    "last_vacuum": None,
                    "last_autovacuum": None,
                    "last_analyze": None,
                    "last_autoanalyze": None,
                },
                "size": {
                    "total_bytes": 1048576,
                    "table_bytes": 786432,
                    "indexes_bytes": 262144,
                    "total_mb": 1.0,
                },
            },
        ],
    }
    with patch(
        "integrations.yugabytedb.tools.yugabytedb_table_stats_tool.get_table_stats",
        return_value=fake_result,
    ):
        result = get_yugabytedb_table_stats(
            host="localhost", database="testdb", schema_name="analytics"
        )
    assert result["schema"] == "analytics"
    assert result["tables"][0]["table_name"] == "reports"


def test_run_connection_refused_error() -> None:
    with patch(
        "integrations.yugabytedb.tools.yugabytedb_table_stats_tool.get_table_stats",
        return_value={"source": "yugabytedb", "available": False, "error": "connection refused"},
    ):
        result = get_yugabytedb_table_stats(host="unreachable", database="testdb")
    assert "error" in result
    assert result["available"] is False


def test_run_auth_failure_error() -> None:
    with patch(
        "integrations.yugabytedb.tools.yugabytedb_table_stats_tool.get_table_stats",
        return_value={
            "source": "yugabytedb",
            "available": False,
            "error": "password authentication failed for user",
        },
    ):
        result = get_yugabytedb_table_stats(host="localhost", database="testdb")
    assert "error" in result
    assert result["available"] is False
