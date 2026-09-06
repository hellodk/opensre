"""Tests for YugabyteDBClusterStatusTool (function-based, @tool decorated).

This tool has no PostgreSQL precedent — it is new, built on the ``yb_servers()``
YSQL function. The ``yb_servers()`` row shape used in the fixture below is
believed correct from YugabyteDB documentation (host, port, num_connections,
node_type, cloud, region, zone, public_ip, uuid) but has not been verified
against a live cluster — see the design doc §8/§6.4 for the residual
verification this needs before merge.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from integrations.yugabytedb import YugabyteDBConfig, get_cluster_status
from integrations.yugabytedb.tools.yugabytedb_cluster_status_tool import (
    get_yugabytedb_cluster_status,
)
from tests.tools.conftest import BaseToolContract

# Believed-correct yb_servers() column order: host, port, num_connections,
# node_type, cloud, region, zone, public_ip, uuid. Not verified against a
# live cluster (design doc §8, item 5's residual verification).
_YB_SERVERS_FIXTURE_ROWS = [
    ("10.0.1.1", 5433, 12, "primary", "aws", "us-east-1", "us-east-1a", "10.0.1.1", "uuid-1"),
    ("10.0.1.2", 5433, 8, "primary", "aws", "us-east-1", "us-east-1b", "10.0.1.2", "uuid-2"),
    ("10.0.1.3", 5433, 5, "primary", "aws", "us-east-1", "us-east-1c", "10.0.1.3", "uuid-3"),
]


class TestYugabyteDBClusterStatusToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_yugabytedb_cluster_status.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_yugabytedb_cluster_status.__opensre_registered_tool__
    assert rt.name == "get_yugabytedb_cluster_status"
    assert rt.source == "yugabytedb"


def test_run_happy_path_with_yb_servers_fixture() -> None:
    fake_result = {
        "source": "yugabytedb",
        "available": True,
        "connected_node": "10.0.1.1",
        "node_count": 3,
        "nodes": [
            {
                "host": row[0],
                "port": row[1],
                "num_connections": row[2],
                "node_type": row[3],
                "cloud": row[4],
                "region": row[5],
                "zone": row[6],
                "public_ip": row[7],
                "uuid": row[8],
            }
            for row in _YB_SERVERS_FIXTURE_ROWS
        ],
        "note": (
            "Reflects live YB-TServer nodes visible to yb_servers(). "
            "Tablet-level replication lag and leader/follower status are "
            "not observable over a SQL connection and are out of scope "
            "for this integration."
        ),
    }
    with patch(
        "integrations.yugabytedb.tools.yugabytedb_cluster_status_tool.get_cluster_status",
        return_value=fake_result,
    ):
        result = get_yugabytedb_cluster_status(host="lb.yugabyte.internal", database="testdb")
    assert result["node_count"] == 3
    zones = {node["zone"] for node in result["nodes"]}
    assert zones == {"us-east-1a", "us-east-1b", "us-east-1c"}
    regions = {node["region"] for node in result["nodes"]}
    assert regions == {"us-east-1"}


def test_run_undefined_function_error() -> None:
    """Pointing this integration at a non-Yugabyte Postgres server."""
    with patch(
        "integrations.yugabytedb.tools.yugabytedb_cluster_status_tool.get_cluster_status",
        return_value={
            "source": "yugabytedb",
            "available": False,
            "error": "function yb_servers() does not exist",
        },
    ):
        result = get_yugabytedb_cluster_status(host="not-yugabyte.internal", database="testdb")
    assert "error" in result
    assert result["available"] is False


def test_run_error_propagated() -> None:
    with patch(
        "integrations.yugabytedb.tools.yugabytedb_cluster_status_tool.get_cluster_status",
        return_value={"source": "yugabytedb", "available": False, "error": "connection refused"},
    ):
        result = get_yugabytedb_cluster_status(host="unreachable", database="testdb")
    assert "error" in result
    assert result["available"] is False


class TestGetClusterStatusRowParsing:
    """One layer deeper: verify get_cluster_status parses yb_servers() rows correctly."""

    def test_parses_yb_servers_fixture_rows(self) -> None:
        mock_cursor = MagicMock()
        # First fetchone() is the connected_node probe; fetchall() returns the
        # yb_servers() fixture rows.
        mock_cursor.fetchone.return_value = ("10.0.1.1",)
        mock_cursor.fetchall.return_value = _YB_SERVERS_FIXTURE_ROWS

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        config = YugabyteDBConfig(host="lb.yugabyte.internal", database="testdb")
        with patch("integrations.yugabytedb._get_connection", return_value=mock_conn):
            result = get_cluster_status(config)

        assert result["available"] is True
        assert result["node_count"] == 3
        assert result["connected_node"] == "10.0.1.1"
        assert result["nodes"][0]["host"] == "10.0.1.1"
        assert result["nodes"][0]["region"] == "us-east-1"
        assert result["nodes"][0]["zone"] == "us-east-1a"
        assert result["nodes"][1]["zone"] == "us-east-1b"
        assert result["nodes"][2]["zone"] == "us-east-1c"
        assert result["nodes"][0]["node_type"] == "primary"

    def test_undefined_function_returns_tool_unavailable(self) -> None:
        # Simulates psycopg2.errors.UndefinedFunction, which the module's
        # generic `except Exception` handling already covers without any
        # special-case code (psycopg2 itself is an optional extra not
        # installed in this dev environment, so a plain Exception subclass
        # stands in for it here).
        class _UndefinedFunction(Exception):
            pass

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = ("10.0.1.1",)
        mock_cursor.execute.side_effect = [
            None,
            _UndefinedFunction("function yb_servers() does not exist"),
        ]

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        config = YugabyteDBConfig(host="not-yugabyte.internal", database="testdb")
        with patch("integrations.yugabytedb._get_connection", return_value=mock_conn):
            result = get_cluster_status(config)

        assert result["available"] is False
        assert "error" in result
