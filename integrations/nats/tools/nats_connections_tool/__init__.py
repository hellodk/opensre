"""NATS Connections Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from integrations.nats import (
    NatsConfig,
    get_connections,
    nats_extract_params,
    nats_is_available,
)


def _map_get_nats_connections(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite connection counts, pending bytes and slow-consumer closures."""
    if not output.get("available"):
        return
    summary_data = output.get("summary") or {}
    summary = (
        f"{output.get('returned', 0)} of {output.get('total', 0)} "
        f"{output.get('state', '')} connection(s), "
        f"{summary_data.get('pending_bytes_total', 0)} pending bytes"
    )
    if summary_data.get("slow_consumer_closures", 0) > 0:
        summary += f", {summary_data['slow_consumer_closures']} slow-consumer closure(s)"
    top = summary_data.get("top_pending") or []
    if top:
        first = top[0]
        summary += (
            f", top pending: {first.get('name') or first.get('cid')} ({first.get('pending_bytes')})"
        )
    record_evidence_entry(
        evidence,
        source="get_nats_connections",
        label="NATS Connections",
        summary=summary,
    )


@tool(
    name="get_nats_connections",
    description="List client connections on one nats-server sorted by pending bytes (default), subscriptions, message or byte counters, idle time or RTT, with per-connection name, language, IP, subscriptions and pending bytes. State open (default), closed (with close reasons such as slow consumer) or all. Summarises pending bytes, close reasons and the oldest idle connection.",
    source="nats",
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Finding which client is a slow consumer or is holding pending bytes",
        "Listing who is connected to a NATS server and what each client subscribes to",
        "Reviewing why connections closed, grouped by close reason",
    ],
    is_available=nats_is_available,
    injected_params=("url", "username", "password", "verify_ssl"),
    extract_params=nats_extract_params,
    evidence_mapper=_map_get_nats_connections,
)
def get_nats_connections(
    url: str,
    username: str = "",
    password: str = "",
    verify_ssl: bool = True,
    sort: str = "pending",
    state: str = "open",
    limit: int = 25,
) -> dict[str, Any]:
    """List client connections on one nats-server."""
    config = NatsConfig(
        url=url,
        username=username,
        password=password,
        verify_ssl=verify_ssl,
    )
    return get_connections(config, sort=sort, state=state, limit=limit)
