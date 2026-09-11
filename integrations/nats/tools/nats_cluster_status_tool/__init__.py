"""NATS Cluster Status Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from integrations.nats import (
    NatsConfig,
    get_cluster_status,
    nats_extract_params,
    nats_is_available,
)


def _map_get_nats_cluster_status(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite route counts, peer counts and the JetStream meta leader."""
    if not output.get("available"):
        return
    if not output.get("clustered"):
        record_evidence_entry(
            evidence,
            source="get_nats_cluster_status",
            label="NATS Cluster Status",
            summary=f"standalone server {output.get('server_name', '')} (no cluster)",
        )
        return
    cluster = output.get("cluster") or {}
    routes = output.get("routes") or {}
    route_summary = routes.get("summary") or {}
    meta = output.get("jetstream_meta") or {}
    summary = (
        f"cluster {cluster.get('name', '')}: {route_summary.get('count', 0)} route(s) "
        f"to {len(route_summary.get('peers', []))} peer(s), "
        f"meta leader {meta.get('leader', '')} of {meta.get('cluster_size', 0)}"
    )
    record_evidence_entry(
        evidence,
        source="get_nats_cluster_status",
        label="NATS Cluster Status",
        summary=summary,
    )


@tool(
    name="get_nats_cluster_status",
    description="Return cluster topology from one nats-server: cluster name and peer URLs, route connections per peer with pending bytes and RTT, gateway and leaf-node counts, and the JetStream meta group leader, size and replica currency. Reports clustered false for a standalone server.",
    source="nats",
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Confirming every node of a NATS cluster is routed and the JetStream meta leader is elected",
        "Seeing per-peer route health with pending bytes and RTT",
        "Distinguishing a clustered server from a standalone one",
    ],
    is_available=nats_is_available,
    injected_params=("url", "username", "password", "verify_ssl"),
    extract_params=nats_extract_params,
    evidence_mapper=_map_get_nats_cluster_status,
)
def get_nats_cluster_status(
    url: str,
    username: str = "",
    password: str = "",
    verify_ssl: bool = True,
) -> dict[str, Any]:
    """Return cluster topology from one nats-server."""
    config = NatsConfig(
        url=url,
        username=username,
        password=password,
        verify_ssl=verify_ssl,
    )
    return get_cluster_status(config)
