"""Aerospike Latency Tool."""

from typing import Any

from core.tool_framework.tool_decorator import tool
from integrations.aerospike import (
    AerospikeConfig,
    aerospike_extract_params,
    aerospike_is_available,
    get_latency,
)


@tool(
    name="get_aerospike_latency",
    description=(
        "Retrieve Aerospike latency histograms (read/write/udf/query buckets) for "
        "recent time windows."
    ),
    source="aerospike",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Assess Aerospike request latency during an incident: percentage of ops "
        "exceeding 1ms/8ms/64ms thresholds per operation type.",
    ],
    outputs={
        "histograms": "Per-operation-type latency buckets when the response is "
        "recognized; omitted when the response degrades to raw text.",
        "raw": "Unparsed asinfo output, present only when the response could not be structured.",
    },
    is_available=aerospike_is_available,
    injected_params=("host",),
    extract_params=aerospike_extract_params,
)
def get_aerospike_latency(
    host: str,
    port: int = 3000,
    username: str = "",
    password: str = "",
) -> dict[str, Any]:
    """Fetch latency histograms from an Aerospike node via ``asinfo``."""
    config = AerospikeConfig(host=host, port=port, username=username, password=password)
    return get_latency(config)
