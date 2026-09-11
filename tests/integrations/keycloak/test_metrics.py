"""Unit tests for the Keycloak Prometheus text parser and metric shaper."""

from __future__ import annotations

import pytest

from integrations.keycloak.metrics import parse_prometheus_text, shape_metrics
from tests.integrations.keycloak import load_fixture


@pytest.fixture(scope="module")
def metric_text() -> str:
    text = load_fixture("mgmt_metrics.txt")
    assert isinstance(text, str)
    return text


class TestParsePrometheusText:
    def test_sample_count_matches_fixture_lines(self, metric_text: str) -> None:
        expected = sum(
            1 for line in metric_text.splitlines() if line.strip() and not line.startswith("#")
        )
        assert len(parse_prometheus_text(metric_text)) == expected

    def test_exponent_float_and_labels(self, metric_text: str) -> None:
        samples = parse_prometheus_text(metric_text)
        match = next(
            s
            for s in samples
            if s.name == "jvm_memory_used_bytes"
            and s.labels.get("area") == "heap"
            and s.labels.get("id") == "G1 Old Gen"
        )
        assert match.value == pytest.approx(93513592.0)

    def test_negative_sentinel_parsed(self, metric_text: str) -> None:
        samples = parse_prometheus_text(metric_text)
        match = next(
            s
            for s in samples
            if s.name == "jvm_memory_max_bytes" and s.labels.get("id") == "G1 Eden Space"
        )
        assert match.value == -1.0

    def test_labels_with_spaces_round_trip(self, metric_text: str) -> None:
        samples = parse_prometheus_text(metric_text)
        match = next(
            s
            for s in samples
            if s.name == "jvm_gc_pause_seconds_count"
            and s.labels.get("cause") == "G1 Evacuation Pause"
        )
        assert match.labels["gc"] == "G1 Young Generation"
        assert match.value == 6.0

    def test_garbage_line_skipped(self) -> None:
        samples = parse_prometheus_text("not a metric line\njvm_threads_live_threads 48.0\n")
        assert len(samples) == 1
        assert samples[0].name == "jvm_threads_live_threads"


class TestShapeMetrics:
    @pytest.fixture(scope="class")
    def shaped(self, metric_text: str) -> dict:
        return shape_metrics(parse_prometheus_text(metric_text), "opensre-demo")

    def test_uptime(self, shaped: dict) -> None:
        assert shaped["uptime_seconds"] == pytest.approx(23.614)

    def test_heap_excludes_unbounded(self, shaped: dict) -> None:
        jvm = shaped["jvm"]
        assert jvm["heap_max_bytes"] == pytest.approx(46942650368.0)
        assert jvm["heap_used_pct"] == pytest.approx(0.3, abs=0.05)
        assert jvm["threads_live"] == 48.0
        assert jvm["gc_pause_count"] == 8.0
        assert jvm["gc_pause_seconds_total"] == pytest.approx(0.079)
        assert jvm["java_version"] == "21.0.12.1+1-LTS"

    def test_db_pool(self, shaped: dict) -> None:
        assert shaped["db_pool"][0] == {
            "datasource": "default",
            "active": 0,
            "available": 3,
            "awaiting": 0,
            "max_used": 3,
        }

    def test_http(self, shaped: dict) -> None:
        http = shaped["http"]
        assert http["by_status_class"]["5xx"] == 1
        assert http["top_5xx"][0]["uri"] == "/admin/realms/{realm}/events"

    def test_cluster_size(self, shaped: dict) -> None:
        assert shaped["cluster_size"] == 1

    def test_realm_user_events(self, shaped: dict) -> None:
        realm_events = shaped["realm_user_events"]
        assert realm_events["present"] is True
        assert realm_events["login_success"] == 1
        assert realm_events["login_errors"] == 7
        assert realm_events["login_errors_by_reason"]["invalid_user_credentials"] == 3
        assert realm_events["client_login_errors"] == 3
        assert realm_events["lockouts"] == 1

    def test_empty_samples(self) -> None:
        shaped = shape_metrics([], "x")
        assert shaped["uptime_seconds"] is None
        assert shaped["cluster_size"] is None
        assert shaped["worker_pool_rejected_total"] is None
        assert shaped["db_pool"] == []
        assert shaped["metric_names_seen"] == 0
        assert shaped["realm_user_events"]["present"] is False

    def test_other_realm_not_present(self, metric_text: str) -> None:
        shaped = shape_metrics(parse_prometheus_text(metric_text), "other-realm")
        assert shaped["realm_user_events"]["present"] is False
