"""Aerospike tools must not expose ``port`` to the LLM.

``port`` is resolved from integration config (env/store) like credentials.
When the model can set it, it can clobber the resolved value with the schema
default and the tool connects to the wrong endpoint (mirrors the live
YugabyteDB incident where the agent passed ``{"port": <default>}``).
"""

from __future__ import annotations

import inspect

import pytest

from integrations.aerospike.tools.aerospike_latency_tool import get_aerospike_latency
from integrations.aerospike.tools.aerospike_namespace_stats_tool import (
    get_aerospike_namespace_stats,
)
from integrations.aerospike.tools.aerospike_node_status_tool import (
    get_aerospike_node_status,
)

_AS_TOOLS = [
    get_aerospike_latency,
    get_aerospike_namespace_stats,
    get_aerospike_node_status,
]


@pytest.mark.parametrize("fn", _AS_TOOLS, ids=lambda fn: fn.__name__)
def test_port_not_exposed_to_llm(fn) -> None:
    rt = fn.__opensre_registered_tool__
    assert "port" in rt.injected_params
    properties = rt.public_input_schema.get("properties") or {}
    assert "port" not in properties


@pytest.mark.parametrize("fn", _AS_TOOLS, ids=lambda fn: fn.__name__)
def test_port_still_callable_directly(fn) -> None:
    """``port`` remains a real parameter (framework fills it from config);
    only its visibility to the LLM changes."""
    assert "port" in inspect.signature(fn).parameters
