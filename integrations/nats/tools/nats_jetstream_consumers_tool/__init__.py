"""NATS JetStream Consumers Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from integrations.nats import (
    NatsConfig,
    get_jetstream_consumers,
    nats_extract_params,
    nats_is_available,
)


def _map_get_nats_jetstream_consumers(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite consumer backlog, unacked deliveries and the worst backlog."""
    if not output.get("available"):
        return
    server_name = output.get("server_name", "")
    if not output.get("jetstream_enabled", True):
        record_evidence_entry(
            evidence,
            source="get_nats_jetstream_consumers",
            label="NATS JetStream Consumers",
            summary=f"JetStream disabled on {server_name}",
        )
        return
    summary_data = output.get("summary") or {}
    consumers = output.get("consumers") or []
    summary = (
        f"{summary_data.get('consumers', len(consumers))} consumer(s) "
        f"on {server_name}: {summary_data.get('pending_total', 0)} pending, "
        f"{summary_data.get('ack_pending_total', 0)} unacked"
    )
    if summary_data.get("with_redeliveries", 0) > 0:
        summary += f", {summary_data['with_redeliveries']} redelivering"
    if consumers:
        worst = consumers[0]
        summary += (
            f", worst: {worst.get('stream')}/{worst.get('name')} "
            f"({worst.get('num_pending')} pending)"
        )
    record_evidence_entry(
        evidence,
        source="get_nats_jetstream_consumers",
        label="NATS JetStream Consumers",
        summary=summary,
    )


@tool(
    name="get_nats_jetstream_consumers",
    description="List JetStream consumers visible on one nats-server with backlog (num_pending), lag behind the stream, unacknowledged deliveries, redeliveries, waiting pull requests, ack policy, filter subject and RAFT leader, flagged for backlog, ack-pending, redeliveries and never-delivered. Filter by stream or consumer. Counters are authoritative only from the consumer's leader.",
    source="nats",
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Explaining why a JetStream consumer is falling behind or redelivering",
        "Seeing unacknowledged deliveries and waiting pull requests per consumer",
        "Confirming which server leads each consumer before trusting its counters",
    ],
    is_available=nats_is_available,
    injected_params=("url", "username", "password", "verify_ssl"),
    extract_params=nats_extract_params,
    evidence_mapper=_map_get_nats_jetstream_consumers,
)
def get_nats_jetstream_consumers(
    url: str,
    username: str = "",
    password: str = "",
    verify_ssl: bool = True,
    stream: str = "",
    consumer: str = "",
) -> dict[str, Any]:
    """List JetStream consumers visible on one nats-server."""
    config = NatsConfig(
        url=url,
        username=username,
        password=password,
        verify_ssl=verify_ssl,
    )
    return get_jetstream_consumers(config, stream=stream, consumer=consumer)
