"""Aerospike Namespace Stats Tool."""

from typing import Any

from core.tool_framework.tool_decorator import tool
from integrations.aerospike import (
    AerospikeConfig,
    aerospike_extract_params,
    aerospike_is_available,
    get_namespace_stats,
)


@tool(
    name="get_aerospike_namespace_stats",
    description=(
        "List Aerospike namespaces and retrieve per-namespace storage, memory, and "
        "object-count statistics for the connected node. These figures are "
        "single-node/local, not cluster-wide aggregates."
    ),
    source="aerospike",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Check per-namespace object counts and memory/device usage during a capacity "
        "or stop-writes incident.",
        "Confirm a namespace exists and inspect its replication factor and stop-writes state.",
    ],
    outputs={
        "scope": "Always 'single_node' — figures reflect only the connected node's "
        "local partition share, not a cluster-wide total.",
        "namespaces": "Per-namespace objects, memory/device used bytes, replication "
        "factor, and stop_writes flag.",
        "truncated": "True when more namespaces exist than the response includes.",
    },
    is_available=aerospike_is_available,
    injected_params=("host",),
    extract_params=aerospike_extract_params,
)
def get_aerospike_namespace_stats(
    host: str,
    port: int = 3000,
    username: str = "",
    password: str = "",
    namespace: str = "",
) -> dict[str, Any]:
    """Fetch namespace stats from an Aerospike node via ``asinfo``."""
    config = AerospikeConfig(host=host, port=port, username=username, password=password)
    return get_namespace_stats(config, namespace=namespace)
