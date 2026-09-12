"""Unit tests for NATS monitoring shapers over captured fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from integrations.nats.monitoring import (
    shape_connection,
    shape_consumer,
    shape_gatewayz,
    shape_healthz,
    shape_jetstream_varz,
    shape_jsz,
    shape_leafz,
    shape_route,
    shape_stream,
    shape_subsz,
    shape_varz,
    summarize_connections,
    summarize_routes,
    summarize_subscriptions,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str) -> Any:
    path = FIXTURES / name
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return json.loads(text)
    return text


def _stream_detail(name: str, server: str = "n1") -> tuple[dict[str, Any], str]:
    payload = load_fixture("jsz_streams_consumers_config.json")
    for account in payload.get("account_details", []):
        for stream in account.get("stream_detail", []):
            if stream["name"] == name:
                return stream, account.get("name", "")
    raise AssertionError(f"stream {name} not found")


def _consumer_detail(stream_name: str, consumer_name: str) -> tuple[dict[str, Any], int]:
    payload = load_fixture("jsz_streams_consumers_config.json")
    for account in payload.get("account_details", []):
        for stream in account.get("stream_detail", []):
            if stream["name"] != stream_name:
                continue
            last_seq = int(stream.get("state", {}).get("last_seq", 0))
            for consumer in stream.get("consumer_detail", []):
                if consumer["name"] == consumer_name:
                    return consumer, last_seq
    raise AssertionError(f"consumer {stream_name}/{consumer_name} not found")


class TestShapeVarz:
    def test_clustered(self) -> None:
        shaped = shape_varz(load_fixture("varz.json"))
        assert shaped["server_name"] == "n1"
        assert shaped["version"] == "2.14.6"
        assert shaped["connections"] == 1
        assert shaped["total_connections"] == 20
        assert shaped["max_connections"] == 65536
        assert shaped["routes"] == 8
        assert shaped["subscriptions"] == 311
        assert shaped["slow_consumers"] == 0
        assert shaped["in_msgs"] == 19946
        assert shaped["mem_bytes"] == 26021888
        assert shaped["cores"] == 20
        assert shaped["max_payload"] == 1048576
        assert shaped["write_deadline_seconds"] == 10.0
        assert shaped["ping_interval_seconds"] == 120.0
        assert shaped["uptime_seconds"] == 112.0
        assert shaped["clustered"] is True
        assert shaped["cluster"]["name"] == "opensre-demo"
        assert shaped["cluster"]["pool_size"] == 3
        assert len(shaped["cluster"]["urls"]) == 3
        assert shaped["http_req_stats"]["/varz"] == 2

    def test_standalone(self) -> None:
        shaped = shape_varz(load_fixture("varz_solo.json"))
        assert shaped["clustered"] is False
        assert shaped["cluster"] is None
        assert shaped["connections"] == 0
        assert shaped["subscriptions"] == 60
        assert shaped["server_name"] == "solo"


class TestShapeJetstreamVarz:
    def test_enabled(self) -> None:
        shaped = shape_jetstream_varz(load_fixture("varz.json"))
        assert shaped["enabled"] is True
        assert shaped["stats"]["storage"] == 33150
        assert shaped["stats"]["reserved_storage"] == 10485760
        assert shaped["stats"]["api"] == {"level": 4, "total": 14, "errors": 4}
        assert shaped["meta"]["leader"] == "n1"
        assert shaped["meta"]["cluster_size"] == 3
        assert [r["name"] for r in shaped["meta"]["replicas"]] == ["n2", "n3"]
        assert shaped["config"]["store_dir"] == "/tmp/js/jetstream"
        assert shaped["config"]["sync_interval_seconds"] == 120.0

    def test_disabled(self) -> None:
        shaped = shape_jetstream_varz(load_fixture("varz_solo.json"))
        assert shaped["enabled"] is False
        assert shaped["meta"] is None


class TestShapeHealthz:
    def test_ok(self) -> None:
        shaped = shape_healthz(200, load_fixture("healthz.json"))
        assert shaped["ok"] is True
        assert shaped["status"] == "ok"
        assert shaped["error"] is None

    def test_unavailable(self) -> None:
        shaped = shape_healthz(503, {"status": "unavailable", "error": "x"})
        assert shaped["ok"] is False
        assert shaped["error"] == "x"


class TestShapeConnection:
    def test_open(self) -> None:
        payload = load_fixture("connz_sort_subs_detail.json")
        shaped = shape_connection(payload["connections"][0])
        assert shaped["cid"] == 51
        assert shaped["name"] == "NATS CLI Version 0.4.0"
        assert shaped["lang"] == "go"
        assert shaped["subscriptions"] == 1
        assert shaped["subscriptions_list"] == ["telemetry.>"]
        assert shaped["out_msgs"] == 16000
        assert shaped["closed"] is False
        assert shaped["slow_consumer"] is False
        assert shaped["rtt_ms"] == pytest.approx(0.133)
        assert shaped["idle_seconds"] == 0.0
        assert shaped["uptime_seconds"] == 105.0

    def test_closed(self) -> None:
        payload = load_fixture("connz_state_closed.json")
        reasons = set()
        for entry in payload["connections"]:
            shaped = shape_connection(entry)
            assert shaped["closed"] is True
            assert isinstance(shaped["stop"], str)
            reasons.add(shaped["reason"])
        assert reasons == {"Client Closed", "Protocol Violation"}

    def test_slow_consumer_reason(self) -> None:
        shaped = shape_connection({"reason": "Slow Consumer (Write Deadline)"})
        assert shaped["slow_consumer"] is True

    def test_summarize_closed(self) -> None:
        payload = load_fixture("connz_state_closed.json")
        summary = summarize_connections([shape_connection(c) for c in payload["connections"]])
        assert summary["total"] == 19
        assert summary["closed"] == 19
        assert summary["open"] == 0
        assert summary["closed_by_reason"] == {
            "Client Closed": 18,
            "Protocol Violation": 1,
        }
        assert summary["slow_consumer_closures"] == 0
        assert summary["top_pending"] == []

    def test_summarize_open(self) -> None:
        payload = load_fixture("connz.json")
        summary = summarize_connections([shape_connection(c) for c in payload["connections"]])
        assert summary["by_kind"] == {"Client": 1}
        assert summary["by_lang"] == {"go": 1}
        assert summary["oldest_idle"]["cid"] == 51


class TestShapeSubsz:
    def test_detail(self) -> None:
        shaped = shape_subsz(load_fixture("subsz_detail.json"))
        assert shaped["num_subscriptions"] == 311
        assert shaped["listed"] == 111
        assert shaped["cache_hit_rate"] == pytest.approx(0.43916, abs=0.001)
        assert shaped["max_fanout"] == 3

    def test_summary(self) -> None:
        shaped = shape_subsz(load_fixture("subsz_detail.json"))
        summary = summarize_subscriptions(shaped["subscriptions"])
        assert summary["by_account"] == {"$SYS": 97, "$G": 14}
        assert summary["system_subscriptions"] == 97
        assert summary["application_subscriptions"] == 14
        assert summary["top_by_msgs"][0]["subject"] == "telemetry.>"
        assert summary["top_by_msgs"][0]["msgs"] == 16000
        for entry in summary["top_by_msgs"]:
            assert not entry["subject"].startswith("$SYS.")
            assert not entry["subject"].startswith("$NRG.")
            assert not entry["subject"].startswith("$JS.")

    def test_nomatch(self) -> None:
        shaped = shape_subsz(load_fixture("subsz_test_nomatch.json"))
        assert shaped["subscriptions"] == []
        assert shaped["listed"] == 0

    def test_match(self) -> None:
        shaped = shape_subsz(load_fixture("subsz_test_match.json"))
        assert len(shaped["subscriptions"]) == 1
        assert shaped["subscriptions"][0]["cid"] == 51


class TestShapeJsz:
    def test_cluster(self) -> None:
        shaped = shape_jsz(load_fixture("jsz.json"))
        assert shaped["enabled"] is True
        assert shaped["streams"] == 2
        assert shaped["consumers"] == 3
        assert shaped["messages"] == 625
        assert shaped["bytes"] == 33150
        assert shaped["api"]["errors"] == 4
        assert shaped["meta_cluster"]["leader"] == "n1"
        assert shaped["meta_cluster"]["cluster_size"] == 3
        assert len(shaped["meta_cluster"]["replicas"]) == 2

    def test_follower_has_no_replicas(self) -> None:
        shaped = shape_jsz(load_fixture("jsz_n2.json"))
        assert shaped["meta_cluster"]["replicas"] == []

    def test_disabled(self) -> None:
        shaped = shape_jsz(load_fixture("jsz_solo.json"))
        assert shaped["enabled"] is False
        assert shaped["streams"] == 0
        assert shaped["meta_cluster"] is None


class TestShapeStream:
    def test_orders(self) -> None:
        detail, account = _stream_detail("ORDERS")
        shaped = shape_stream(detail, account=account, server_name="n1")
        assert shaped["messages"] == 600
        assert shaped["bytes"] == 31984
        assert shaped["last_seq"] == 600
        assert shaped["num_subjects"] == 2
        assert shaped["num_deleted"] == 0
        assert shaped["consumer_count"] == 2
        assert shaped["config"]["subjects"] == ["orders.>"]
        assert shaped["config"]["retention"] == "limits"
        assert shaped["config"]["num_replicas"] == 3
        assert shaped["config"]["max_msgs"] == 10000
        assert shaped["config"]["max_bytes"] == 10485760
        assert shaped["config"]["max_age_seconds"] == 86400.0
        assert shaped["utilisation"]["msgs_pct"] == 6.0
        assert shaped["utilisation"]["bytes_pct"] == 0.3
        assert shaped["cluster"]["leader"] == "n3"
        assert shaped["cluster"]["raft_group"] == "S-R3F-VJA9dCiS"
        assert shaped["authoritative"] is False
        assert shaped["replicas_not_current"] == 1

    def test_jobs_workqueue(self) -> None:
        detail, account = _stream_detail("JOBS")
        shaped = shape_stream(detail, account=account, server_name="n1")
        assert shaped["config"]["retention"] == "workqueue"
        assert shaped["config"]["max_msgs"] == -1
        assert shaped["utilisation"] == {"msgs_pct": None, "bytes_pct": None}
        assert shaped["messages"] == 25

    def test_events_single_replica(self) -> None:
        payload = load_fixture("jsz_n3_streams_consumers.json")
        found = None
        account_name = ""
        for account in payload.get("account_details", []):
            for stream in account.get("stream_detail", []):
                if stream["name"] == "EVENTS":
                    found = stream
                    account_name = account.get("name", "")
        assert found is not None
        shaped = shape_stream(found, account=account_name, server_name="n3")
        assert shaped["config"] is None
        assert shaped["cluster"]["raft_group"] is None
        assert shaped["cluster"]["replicas"] == []
        assert shaped["authoritative"] is True
        assert shaped["messages"] == 40

    def test_orders_authoritative_on_leader(self) -> None:
        payload = load_fixture("jsz_n3_streams_consumers.json")
        for account in payload.get("account_details", []):
            for stream in account.get("stream_detail", []):
                if stream["name"] == "ORDERS":
                    shaped = shape_stream(stream, account=account.get("name", ""), server_name="n3")
                    assert shaped["authoritative"] is True
                    assert shaped["replicas_not_current"] == 0
                    return
        raise AssertionError("ORDERS not found on n3 fixture")


class TestShapeConsumer:
    def test_billing(self) -> None:
        detail, last_seq = _consumer_detail("ORDERS", "billing")
        shaped = shape_consumer(detail, stream_last_seq=last_seq, server_name="n1")
        assert shaped["lag"] == 530
        assert shaped["num_pending"] == 330
        assert shaped["num_ack_pending"] == 20
        assert shaped["authoritative"] is True
        assert shaped["flags"] == ["backlog", "ack_pending"]
        assert shaped["config"]["filter_subject"] == "orders.created"
        assert shaped["config"]["ack_wait_seconds"] == 30.0
        assert shaped["config"]["max_deliver"] == 5
        assert shaped["config"]["paused"] is False

    def test_worker(self) -> None:
        detail, last_seq = _consumer_detail("JOBS", "worker")
        shaped = shape_consumer(detail, stream_last_seq=last_seq, server_name="n1")
        assert shaped["lag"] == 23
        assert shaped["num_redelivered"] == 2
        assert shaped["flags"] == ["ack_pending", "redeliveries"]
        assert shaped["authoritative"] is False

    def test_audit(self) -> None:
        detail, last_seq = _consumer_detail("ORDERS", "audit")
        shaped = shape_consumer(detail, stream_last_seq=last_seq, server_name="n1")
        assert shaped["lag"] == 600
        assert shaped["flags"] == []
        assert shaped["config"]["ack_policy"] == "none"
        assert shaped["config"]["ack_wait_seconds"] is None
        assert shaped["config"]["max_deliver"] == -1

    def test_stale_never_delivered(self) -> None:
        payload = load_fixture("jsz_n3_streams_consumers.json")
        for account in payload.get("account_details", []):
            for stream in account.get("stream_detail", []):
                if stream["name"] != "EVENTS":
                    continue
                last_seq = int(stream.get("state", {}).get("last_seq", 0))
                for consumer in stream.get("consumer_detail", []):
                    if consumer["name"] == "stale":
                        shaped = shape_consumer(
                            consumer,
                            stream_last_seq=last_seq,
                            server_name="n3",
                        )
                        assert shaped["config"] is None
                        assert shaped["flags"] == ["backlog", "never_delivered"]
                        assert shaped["lag"] == 40
                        assert shaped["authoritative"] is True
                        return
        raise AssertionError("stale consumer not found")

    def test_paused_flag(self) -> None:
        detail, last_seq = _consumer_detail("ORDERS", "billing")
        future = "2999-01-01T00:00:00Z"
        detail = {
            **detail,
            "config": {**(detail.get("config") or {}), "pause_until": future},
        }
        shaped = shape_consumer(detail, stream_last_seq=last_seq, server_name="n1")
        assert "paused" in shaped["flags"]


class TestShapeRoutes:
    def test_route(self) -> None:
        payload = load_fixture("routez.json")
        shaped = shape_route(payload["routes"][0])
        assert shaped["remote_name"] == "n2"
        assert shaped["rtt_ms"] == pytest.approx(0.122)
        assert shaped["pending_size"] == 0
        assert shaped["compression"] == "off"
        assert shaped["did_solicit"] is True

    def test_summary(self) -> None:
        summary = summarize_routes([shape_route(r) for r in load_fixture("routez.json")["routes"]])
        assert summary["count"] == 8
        assert [p["remote_name"] for p in summary["peers"]] == ["n2", "n3"]
        for peer in summary["peers"]:
            assert peer["connections"] == 4
            assert peer["pending_size_total"] == 0
        assert summary["subscriptions_total"] == 200

    def test_solo(self) -> None:
        summary = summarize_routes(
            [shape_route(r) for r in load_fixture("routez_solo.json")["routes"]]
        )
        assert summary["count"] == 0
        assert summary["peers"] == []


class TestShapeGatewaysLeafs:
    def test_gatewayz(self) -> None:
        shaped = shape_gatewayz(load_fixture("gatewayz.json"))
        assert shaped["outbound_count"] == 0
        assert shaped["inbound_count"] == 0

    def test_leafz(self) -> None:
        shaped = shape_leafz(load_fixture("leafz.json"))
        assert shaped["count"] == 0
