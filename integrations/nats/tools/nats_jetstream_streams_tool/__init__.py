"""NATS JetStream Streams Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from integrations.nats import (
    NatsConfig,
    get_jetstream_streams,
    nats_extract_params,
    nats_is_available,
)


def _map_get_nats_jetstream_streams(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite stream counts, message totals and limit pressure."""
    if not output.get("available"):
        return
    server_name = output.get("server_name", "")
    if not output.get("jetstream_enabled"):
        summary = f"JetStream disabled on {server_name}"
        record_evidence_entry(
            evidence,
            source="get_nats_jetstream_streams",
            label="NATS JetStream Streams",
            summary=summary,
        )
        return
    totals = output.get("totals") or {}
    streams = output.get("streams") or []
    summary = (
        f"{len(streams)} stream(s) on {server_name}: "
        f"{totals.get('messages', 0)} messages, {totals.get('bytes', 0)} bytes"
    )
    hottest_name = ""
    hottest_pct = 0.0
    for stream in streams:
        utilisation = stream.get("utilisation") or {}
        pct = utilisation.get("msgs_pct")
        if pct is not None and pct >= 80 and pct > hottest_pct:
            hottest_name = str(stream.get("name", ""))
            hottest_pct = float(pct)
    if hottest_name:
        summary += f", {hottest_name} at {hottest_pct}% of max_msgs"
    record_evidence_entry(
        evidence,
        source="get_nats_jetstream_streams",
        label="NATS JetStream Streams",
        summary=summary,
    )


@tool(
    name="get_nats_jetstream_streams",
    description="List JetStream streams visible on one nats-server: message and byte counts, first and last sequence, subjects, retention, storage, replicas, limit utilisation, RAFT leader and replica currency. Optionally filter to one stream. Reports when JetStream is disabled. The view is per server; counters are authoritative only for streams led by the queried server.",
    source="nats",
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Checking whether a stream is close to its message or byte limit",
        "Seeing which server leads each stream and whether replicas are current",
        "Confirming a stream exists with the expected subjects and retention",
    ],
    is_available=nats_is_available,
    injected_params=("url", "username", "password", "verify_ssl"),
    extract_params=nats_extract_params,
    evidence_mapper=_map_get_nats_jetstream_streams,
)
def get_nats_jetstream_streams(
    url: str,
    username: str = "",
    password: str = "",
    verify_ssl: bool = True,
    stream: str = "",
) -> dict[str, Any]:
    """List JetStream streams visible on one nats-server."""
    config = NatsConfig(
        url=url,
        username=username,
        password=password,
        verify_ssl=verify_ssl,
    )
    return get_jetstream_streams(config, stream=stream)
