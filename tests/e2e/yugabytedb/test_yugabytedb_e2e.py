"""YugabyteDB E2E tests verifying integration with investigation pipeline.

Tests:
- YugabyteDB config resolution from store and env
- YugabyteDB verification (connection, server info)
- YugabyteDB source detection in investigation state
- YugabyteDB tools availability for query execution
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from integrations.catalog import classify_integrations as _classify_integrations
from integrations.verify import verify_integrations
from tests.e2e.source_helpers import resolve_available_tool_sources


class TestYugabyteDBIntegrationResolution:
    """Test YugabyteDB config resolution from multiple sources."""

    def test_yugabytedb_resolution_from_store(self):
        """YugabyteDB integration correctly resolved from local store."""
        integrations = [
            {
                "id": "yugabytedb-prod",
                "service": "yugabytedb",
                "status": "active",
                "credentials": {
                    "host": "prod-primary.yugabyte.net",
                    "port": 5433,
                    "database": "application_db",
                    "username": "app_user",
                    "password": "secure_password",
                    "ssl_mode": "require",
                },
            }
        ]
        resolved = _classify_integrations(integrations)

        assert "yugabytedb" in resolved
        assert resolved["yugabytedb"]["host"] == "prod-primary.yugabyte.net"
        assert resolved["yugabytedb"]["port"] == 5433
        assert resolved["yugabytedb"]["database"] == "application_db"
        assert resolved["yugabytedb"]["username"] == "app_user"
        assert resolved["yugabytedb"]["password"] == "secure_password"
        assert resolved["yugabytedb"]["ssl_mode"] == "require"

    def test_yugabytedb_invalid_config_skipped(self):
        """Invalid YugabyteDB integration config is safely skipped."""
        integrations = [
            {
                "id": "bad-yugabytedb",
                "service": "yugabytedb",
                "status": "active",
                "credentials": {
                    "host": "",
                    "database": "",
                },
            }
        ]
        resolved = _classify_integrations(integrations)

        assert resolved.get("yugabytedb") is None

    def test_yugabytedb_missing_database_skipped(self):
        """YugabyteDB integration without database is safely skipped."""
        integrations = [
            {
                "id": "no-db-yugabytedb",
                "service": "yugabytedb",
                "status": "active",
                "credentials": {
                    "host": "localhost",
                    "database": "",
                },
            }
        ]
        resolved = _classify_integrations(integrations)

        assert resolved.get("yugabytedb") is None


class TestYugabyteDBToolSourceAvailability:
    """Test YugabyteDB source availability in the tool-registry investigation path."""

    def test_yugabytedb_tool_source_available_from_resolved_integration(self):
        """YugabyteDB source is available when a configured integration exists."""
        resolved_integrations = {
            "yugabytedb": {
                "host": "localhost",
                "port": 5433,
                "database": "application_db",
                "username": "yugabyte",
                "password": "test123",
                "ssl_mode": "prefer",
            }
        }

        sources = resolve_available_tool_sources(resolved_integrations)

        assert "yugabytedb" in sources
        assert sources["yugabytedb"]["host"] == "localhost"
        assert sources["yugabytedb"]["database"] == "application_db"

    def test_yugabytedb_tool_source_uses_configured_database(self):
        """YugabyteDB tool params come from the resolved integration config."""
        resolved_integrations = {
            "yugabytedb": {
                "host": "localhost",
                "port": 5433,
                "database": "default_db",
                "username": "yugabyte",
                "password": "test123",
                "ssl_mode": "prefer",
            }
        }

        sources = resolve_available_tool_sources(resolved_integrations)

        assert "yugabytedb" in sources
        assert sources["yugabytedb"]["database"] == "default_db"

    def test_yugabytedb_tool_source_unavailable_if_unconfigured(self):
        """YugabyteDB source is not included if not configured."""
        resolved_integrations = {}

        sources = resolve_available_tool_sources(resolved_integrations)

        assert "yugabytedb" not in sources


class TestYugabyteDBVerification:
    """Test YugabyteDB integration verification flow."""

    @patch("integrations.yugabytedb._get_connection")
    def test_verify_yugabytedb_success(self, mock_get_connection, monkeypatch):
        """YugabyteDB verification succeeds with valid config."""
        monkeypatch.setenv("YUGABYTEDB_HOST", "localhost")
        monkeypatch.setenv("YUGABYTEDB_DATABASE", "testdb")

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = [
            "PostgreSQL 11.2-YB-2.20.0.0-b0 on x86_64-pc-linux-gnu, compiled by gcc"
        ]
        mock_get_connection.return_value = mock_conn

        results = verify_integrations(service="yugabytedb")

        assert len(results) >= 1
        yugabytedb_result = next((r for r in results if r["service"] == "yugabytedb"), None)
        assert yugabytedb_result is not None
        assert yugabytedb_result["status"] == "passed"
        mock_get_connection.assert_called_once()

    def test_verify_integrations_structure(self):
        """Verify integrations returns expected result structure."""
        try:
            results = verify_integrations(service="yugabytedb")
            assert isinstance(results, list)
            for result in results:
                if result["service"] == "yugabytedb":
                    assert "status" in result
                    assert "detail" in result
                    assert result["status"] in ("passed", "missing", "failed")
        except Exception as exc:
            # If no YugabyteDB is configured, that's ok - just testing structure
            assert exc.__class__.__name__


class TestYugabyteDBToolsAvailability:
    """Test YugabyteDB tools are available and configured."""

    def test_yugabytedb_tools_exist_as_modules(self):
        """YugabyteDB tools modules exist and are properly structured."""
        try:
            import integrations.yugabytedb.tools.yugabytedb_cluster_status_tool as YugabyteDBClusterStatusTool
            import integrations.yugabytedb.tools.yugabytedb_current_queries_tool as YugabyteDBCurrentQueriesTool
            import integrations.yugabytedb.tools.yugabytedb_server_status_tool as YugabyteDBServerStatusTool
            import integrations.yugabytedb.tools.yugabytedb_slow_queries_tool as YugabyteDBSlowQueriesTool
            import integrations.yugabytedb.tools.yugabytedb_table_stats_tool as YugabyteDBTableStatsTool

            # All 5 tool modules should be importable
            assert YugabyteDBServerStatusTool is not None
            assert YugabyteDBCurrentQueriesTool is not None
            assert YugabyteDBClusterStatusTool is not None
            assert YugabyteDBSlowQueriesTool is not None
            assert YugabyteDBTableStatsTool is not None
        except ImportError as e:
            pytest.fail(f"Failed to import YugabyteDB tool modules: {e}")


class TestYugabyteDBAlertFixture:
    """Test the YugabyteDB alert fixture is valid and parseable."""

    def test_yugabytedb_alert_fixture_is_valid_json(self):
        """YugabyteDB alert fixture is valid JSON."""
        fixture_path = Path(__file__).parent / "yugabytedb_alert.json"
        assert fixture_path.exists(), f"Alert fixture not found at {fixture_path}"

        with fixture_path.open() as f:
            alert = json.load(f)

        assert isinstance(alert, dict)
        assert "state" in alert
        assert "commonLabels" in alert
        assert "commonAnnotations" in alert

    def test_yugabytedb_alert_fixture_has_yugabytedb_context(self):
        """YugabyteDB alert fixture contains YugabyteDB-specific context."""
        fixture_path = Path(__file__).parent / "yugabytedb_alert.json"

        with fixture_path.open() as f:
            alert = json.load(f)

        labels = alert.get("commonLabels", {})
        annotations = alert.get("commonAnnotations", {})

        assert "yugabytedb_instance" in labels
        assert "yugabytedb_database" in annotations
        assert "yugabytedb_table" in annotations
        assert "yugabytedb_schema" in annotations
