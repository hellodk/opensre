"""Unit tests for the Aerospike integration."""

from __future__ import annotations

import os
from unittest.mock import patch

from integrations.aerospike import (
    AerospikeConfig,
    aerospike_config_from_env,
    aerospike_extract_params,
    build_aerospike_config,
    get_latency,
    get_namespace_stats,
    get_node_status,
    validate_aerospike_config,
)
from integrations.aerospike import _parse_latency as parse_latency
from integrations.aerospike import _parse_semicolon_kv as parse_semicolon_kv
from integrations.aerospike import _parse_semicolon_list as parse_semicolon_list
from integrations.aerospike.client import (
    AsinfoBinaryNotFoundError,
    AsinfoConnectionError,
    AsinfoTimeoutError,
)
from integrations.catalog import classify_integrations as _classify_integrations


class TestAerospikeConfig:
    def test_default_values(self):
        config = AerospikeConfig(host="localhost")
        assert config.port == 3000
        assert config.username == ""
        assert config.password == ""
        assert config.timeout_seconds == 5.0
        assert config.tls_enabled is False
        assert config.max_results == 50

    def test_normalization(self):
        config = AerospikeConfig(host="  localhost  ", username="  admin  ", password="  hunter2  ")
        assert config.host == "localhost"
        assert config.username == "admin"
        assert config.password == "hunter2"

    def test_is_configured(self):
        assert AerospikeConfig(host="localhost").is_configured is True
        assert AerospikeConfig(host="").is_configured is False

    def test_port_bounds_rejected(self):
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AerospikeConfig(host="localhost", port=0)
        with pytest.raises(ValidationError):
            AerospikeConfig(host="localhost", port=70000)


class TestAerospikeBuild:
    def test_build_aerospike_config(self):
        raw = {
            "host": "aerospike.example.net",
            "port": 3010,
            "username": "monitor",
            "password": "p",
        }
        config = build_aerospike_config(raw)
        assert config.host == "aerospike.example.net"
        assert config.port == 3010
        assert config.username == "monitor"
        assert config.password == "p"

    def test_build_aerospike_config_none(self):
        config = build_aerospike_config(None)
        assert config.host == ""
        assert config.is_configured is False

    @patch.dict(
        os.environ,
        {
            "AEROSPIKE_HOST": "env-host",
            "AEROSPIKE_PORT": "3010",
            "AEROSPIKE_USERNAME": "env-user",
            "AEROSPIKE_PASSWORD": "env-pass",
        },
    )
    def test_aerospike_config_from_env(self):
        config = aerospike_config_from_env()
        assert config is not None
        assert config.host == "env-host"
        assert config.port == 3010
        assert config.username == "env-user"
        assert config.password == "env-pass"

    @patch.dict(os.environ, {}, clear=True)
    def test_aerospike_config_from_env_missing(self):
        assert aerospike_config_from_env() is None

    @patch("integrations.aerospike.resolve_env_credential")
    @patch.dict(os.environ, {"AEROSPIKE_HOST": "env-host"}, clear=True)
    def test_aerospike_config_from_env_reads_password_via_resolver(self, mock_resolve):
        mock_resolve.return_value = "resolved-secret"
        config = aerospike_config_from_env()
        assert config is not None
        assert config.password == "resolved-secret"
        mock_resolve.assert_called_once_with("AEROSPIKE_PASSWORD")


class TestAerospikeExtractParams:
    def test_extract_params(self):
        sources = {
            "aerospike": {
                "host": "cache",
                "port": 3010,
                "username": "u",
                "password": "p",
            },
        }
        params = aerospike_extract_params(sources)
        assert params == {
            "host": "cache",
            "port": 3010,
            "username": "u",
            "password": "p",
        }

    def test_extract_params_missing_source(self):
        params = aerospike_extract_params({})
        assert params["host"] == ""
        assert params["port"] == 3000


class TestAerospikeValidation:
    @patch("integrations.aerospike.send_info_commands")
    def test_validate_success(self, mock_send):
        mock_send.return_value = {"status": "ok"}

        result = validate_aerospike_config(AerospikeConfig(host="node1", port=3000))

        assert result.ok is True
        assert "node1:3000" in result.detail

    @patch("integrations.aerospike.send_info_commands")
    def test_validate_unexpected_status(self, mock_send):
        mock_send.return_value = {"status": "not-ok"}

        result = validate_aerospike_config(AerospikeConfig(host="node1"))

        assert result.ok is False
        assert "unexpected result" in result.detail

    def test_validate_missing_host(self):
        result = validate_aerospike_config(AerospikeConfig(host=""))
        assert result.ok is False
        assert "required" in result.detail

    @patch("integrations.aerospike.send_info_commands")
    def test_validate_binary_not_found(self, mock_send):
        mock_send.side_effect = AsinfoBinaryNotFoundError("asinfo binary not found")

        result = validate_aerospike_config(AerospikeConfig(host="node1"))

        assert result.ok is False
        assert "not found" in result.detail

    @patch("integrations.aerospike.send_info_commands")
    def test_validate_timeout(self, mock_send):
        mock_send.side_effect = AsinfoTimeoutError("asinfo command timed out after 5.0s")

        result = validate_aerospike_config(AerospikeConfig(host="node1"))

        assert result.ok is False
        assert "timed out" in result.detail

    @patch("integrations.aerospike.send_info_commands")
    def test_validate_connection_error(self, mock_send):
        mock_send.side_effect = AsinfoConnectionError(
            "asinfo exited with code 1: connection refused", returncode=1, stderr="refused"
        )

        result = validate_aerospike_config(AerospikeConfig(host="node1"))

        assert result.ok is False
        assert "connection refused" in result.detail


class TestAerospikeNodeStatus:
    @patch("integrations.aerospike.send_info_commands")
    def test_get_node_status(self, mock_send):
        mock_send.return_value = {
            "status": "ok",
            "statistics": "cluster_size=3;cluster_key=BB9;uptime=48213;cluster_integrity=true;",
        }

        result = get_node_status(AerospikeConfig(host="node1"))

        assert result["available"] is True
        assert result["node_status"] == "ok"
        assert result["cluster_size"] == 3
        assert result["cluster_key"] == "BB9"
        assert result["uptime_seconds"] == 48213
        assert result["cluster_integrity"] is True

    def test_get_node_status_not_configured(self):
        result = get_node_status(AerospikeConfig(host=""))
        assert result["available"] is False

    @patch("integrations.aerospike.send_info_commands")
    def test_get_node_status_binary_missing(self, mock_send):
        mock_send.side_effect = AsinfoBinaryNotFoundError("asinfo binary not found")

        result = get_node_status(AerospikeConfig(host="node1"))

        assert result["available"] is False
        assert "not found" in result["error"]

    @patch("integrations.aerospike.report_validation_failure")
    @patch("integrations.aerospike.send_info_commands")
    def test_get_node_status_unexpected_error_reports_sentry(self, mock_send, mock_report):
        mock_send.side_effect = Exception("boom")

        result = get_node_status(AerospikeConfig(host="node1"))

        assert result["available"] is False
        assert "boom" in result["error"]
        mock_report.assert_called_once()


class TestAerospikeNamespaceStats:
    @patch("integrations.aerospike.send_info_commands")
    def test_get_namespace_stats_all_namespaces(self, mock_send):
        def _fake_send(_config, commands):
            if commands == ["namespaces"]:
                return {"namespaces": "test;bar"}
            return {
                "namespace/test": (
                    "objects=10432;memory_used_bytes=5242880;device_used_bytes=0;"
                    "effective_replication_factor=2;stop_writes=false;"
                ),
                "namespace/bar": "objects=5;memory_used_bytes=1024;stop_writes=true;",
            }

        mock_send.side_effect = _fake_send

        result = get_namespace_stats(AerospikeConfig(host="node1"))

        assert result["available"] is True
        assert result["scope"] == "single_node"
        assert result["namespace_count"] == 2
        assert result["namespaces"]["test"]["objects"] == 10432
        assert result["namespaces"]["test"]["replication_factor"] == 2
        assert result["namespaces"]["bar"]["stop_writes"] is True
        assert result["truncated"] is False

    @patch("integrations.aerospike.send_info_commands")
    def test_get_namespace_stats_single_namespace_skips_namespaces_command(self, mock_send):
        mock_send.return_value = {"namespace/test": "objects=1;"}
        config = AerospikeConfig(host="node1")

        result = get_namespace_stats(config, namespace="test")

        assert result["namespace_count"] == 1
        assert "test" in result["namespaces"]
        # Only the targeted namespace/<ns> command is issued — the broad
        # "namespaces" discovery call must be skipped when a namespace filter
        # is given.
        mock_send.assert_called_once_with(config, ["namespace/test"])

    @patch("integrations.aerospike.send_info_commands")
    def test_namespace_stats_truncates_when_exceeding_max_results(self, mock_send):
        names = [f"ns{i}" for i in range(5)]

        def _fake_send(_config, commands):
            if commands == ["namespaces"]:
                return {"namespaces": ";".join(names)}
            return {f"namespace/{n}": "objects=1;" for n in commands}

        mock_send.side_effect = _fake_send

        result = get_namespace_stats(AerospikeConfig(host="node1", max_results=2))

        assert result["namespace_count"] == 2
        assert result["truncated"] is True

    def test_get_namespace_stats_not_configured(self):
        result = get_namespace_stats(AerospikeConfig(host=""))
        assert result["available"] is False


class TestAerospikeLatency:
    @patch("integrations.aerospike.send_info_commands")
    def test_get_latency_structured(self, mock_send):
        mock_send.return_value = {
            "latencies:": "{test}-read:msec,ops/sec,>1ms;1.000,120,0.4",
        }

        result = get_latency(AerospikeConfig(host="node1"))

        assert result["available"] is True
        assert "histograms" in result
        assert result["histograms"]["read"][0]["ops/sec"] == 120

    @patch("integrations.aerospike.send_info_commands")
    def test_get_latency_degrades_to_raw(self, mock_send):
        mock_send.return_value = {"latencies:": "error-no-data-yet-or-back-too-small"}

        result = get_latency(AerospikeConfig(host="node1"))

        assert result["available"] is True
        assert result["raw"] == "error-no-data-yet-or-back-too-small"
        assert "parse_warning" in result

    def test_get_latency_not_configured(self):
        result = get_latency(AerospikeConfig(host=""))
        assert result["available"] is False


class TestAerospikeParsers:
    def test_parse_semicolon_kv_realistic_statistics_fixture(self):
        raw = "cluster_size=3;cluster_key=BB9;uptime=48213;cluster_integrity=true;"
        parsed = parse_semicolon_kv(raw)
        assert parsed == {
            "cluster_size": "3",
            "cluster_key": "BB9",
            "uptime": "48213",
            "cluster_integrity": "true",
        }

    def test_parse_semicolon_kv_handles_value_without_trailing_semicolon(self):
        parsed = parse_semicolon_kv("cluster_size=3;cluster_key=BB9")
        assert parsed == {"cluster_size": "3", "cluster_key": "BB9"}

    def test_parse_semicolon_kv_ignores_malformed_segment(self):
        parsed = parse_semicolon_kv("cluster_size=3;garbage-no-equals;cluster_key=BB9;")
        assert parsed == {"cluster_size": "3", "cluster_key": "BB9"}

    def test_parse_semicolon_list_realistic_namespaces_fixture(self):
        assert parse_semicolon_list("test;bar") == ["test", "bar"]

    def test_parse_semicolon_list_single_namespace_no_delimiter(self):
        assert parse_semicolon_list("test") == ["test"]

    def test_parse_latency_degrades_on_unrecognized_shape(self):
        parsed = parse_latency("error-no-data-yet-or-back-too-small")
        assert parsed == {"raw": "error-no-data-yet-or-back-too-small"}

    def test_parse_latency_degrades_on_empty_input(self):
        assert parse_latency("") == {"raw": ""}

    def test_parse_latency_structured_shape(self):
        raw = "{test}-read:msec,ops/sec,>1ms;1.000,120,0.4;2.000,80,0.1"
        parsed = parse_latency(raw)
        assert "histograms" in parsed
        rows = parsed["histograms"]["read"]
        assert rows[0] == {"msec": 1.0, "ops/sec": 120, ">1ms": 0.4}
        assert rows[1] == {"msec": 2.0, "ops/sec": 80, ">1ms": 0.1}


class TestResolveIntegrations:
    def test_classify_aerospike(self):
        integrations = [
            {
                "id": "123",
                "service": "aerospike",
                "status": "active",
                "credentials": {
                    "host": "aerospike.example.net",
                    "port": 3010,
                    "password": "secret",
                },
            }
        ]
        resolved = _classify_integrations(integrations)
        assert "aerospike" in resolved
        assert resolved["aerospike"].host == "aerospike.example.net"
        assert resolved["aerospike"].port == 3010
        assert resolved["aerospike"].integration_id == "123"

    def test_classify_aerospike_missing_host(self):
        integrations = [
            {
                "id": "456",
                "service": "aerospike",
                "status": "active",
                "credentials": {"host": ""},
            }
        ]
        resolved = _classify_integrations(integrations)
        assert resolved.get("aerospike") is None

    @patch("integrations.aerospike.report_classify_failure")
    def test_classify_aerospike_malformed_reports_failure(self, mock_report):
        from integrations.aerospike import classify

        result = classify({"port": "not-an-int"}, "rec-1")

        assert result == (None, None)
        mock_report.assert_called_once()
