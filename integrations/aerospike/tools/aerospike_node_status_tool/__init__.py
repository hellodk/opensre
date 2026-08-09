"""Aerospike Node Status Tool."""

from typing import Any

from core.tool_framework.tool_decorator import tool
from integrations.aerospike import (
    AerospikeConfig,
    aerospike_extract_params,
    aerospike_is_available,
    get_node_status,
)


@tool(
    name="get_aerospike_node_status",
    description=(
        "Retrieve Aerospike node health: uptime, cluster size, cluster key, and node status."
    ),
    source="aerospike",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Assess Aerospike node health during an incident: liveness, uptime, and cluster size.",
        "Check cluster_integrity and cluster_key to spot a split cluster or a node "
        "that dropped out of quorum.",
    ],
    outputs={
        "node_status": "The node's info-protocol status string (typically 'ok').",
        "cluster_size": "Number of nodes the connected node believes are in the cluster.",
        "cluster_key": "Cluster membership fingerprint; differs across a split cluster.",
        "uptime_seconds": "Seconds since the node process started.",
        "cluster_integrity": "Whether the node reports the cluster as fully migrated/healthy.",
    },
    is_available=aerospike_is_available,
    injected_params=("host",),
    extract_params=aerospike_extract_params,
)
def get_aerospike_node_status(
    host: str,
    port: int = 3000,
    username: str = "",
    password: str = "",
) -> dict[str, Any]:
    """Fetch node health from an Aerospike node via ``asinfo``."""
    config = AerospikeConfig(host=host, port=port, username=username, password=password)
    return get_node_status(config)
