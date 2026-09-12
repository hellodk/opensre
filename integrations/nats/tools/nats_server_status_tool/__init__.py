"""NATS Server Status Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from integrations.nats import (
    NatsConfig,
    get_server_status,
    nats_extract_params,
    nats_is_available,
)


def _map_get_nats_server_status(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite version, health, connection counts and JetStream usage."""
    if not output.get("available"):
        return
    server = output.get("server") or {}
    health = output.get("health") or {}
    jetstream = output.get("jetstream") or {}
    summary = (
        f"nats-server {server.get('version', '')} {server.get('server_name', '')}: "
        f"health {health.get('status') or 'unknown'}, "
        f"{server.get('connections', 0)}/{server.get('max_connections', 0)} connections, "
        f"{server.get('slow_consumers', 0)} slow consumers"
    )
    if jetstream.get("enabled"):
        stats = jetstream.get("stats") or {}
        meta = jetstream.get("meta") or {}
        summary += (
            f", JetStream {stats.get('storage', 0)} bytes stored, "
            f"meta leader {meta.get('leader', '')}"
        )
    record_evidence_entry(
        evidence,
        source="get_nats_server_status",
        label="NATS Server Status",
        summary=summary,
    )


@tool(
    name="get_nats_server_status",
    description="Return one nats-server's status from its monitoring endpoint: version, uptime, health check result, client connection counts against the limit, slow consumers, stale and stalled clients, message and byte throughput, memory and CPU, max payload, cluster name, and JetStream storage usage, API error count and meta-group leader.",
    source="nats",
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Checking whether a NATS server is up and whether it is near its connection limit",
        "Seeing message and byte throughput plus memory and CPU during an incident",
        "Confirming JetStream is enabled and which server leads the meta group",
    ],
    is_available=nats_is_available,
    injected_params=("url", "username", "password", "verify_ssl"),
    extract_params=nats_extract_params,
    evidence_mapper=_map_get_nats_server_status,
)
def get_nats_server_status(
    url: str,
    username: str = "",
    password: str = "",
    verify_ssl: bool = True,
) -> dict[str, Any]:
    """Return one nats-server's status from its monitoring endpoint."""
    config = NatsConfig(
        url=url,
        username=username,
        password=password,
        verify_ssl=verify_ssl,
    )
    return get_server_status(config)
