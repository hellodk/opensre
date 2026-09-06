"""YugabyteDB tools must not expose ``port`` to the LLM.

``port`` is resolved from integration config (env/store) like credentials.
When the model can set it, it clobbers the resolved value with the schema
default (e.g. 5433) and the tool connects to the wrong endpoint — observed
live when an investigation agent passed ``{"port": 5433}`` while the tunnel
ran on 15433.
"""

from __future__ import annotations

import inspect

import pytest

from integrations.yugabytedb.tools.yugabytedb_cluster_status_tool import (
    get_yugabytedb_cluster_status,
)
from integrations.yugabytedb.tools.yugabytedb_current_queries_tool import (
    get_yugabytedb_current_queries,
)
from integrations.yugabytedb.tools.yugabytedb_server_status_tool import (
    get_yugabytedb_server_status,
)
from integrations.yugabytedb.tools.yugabytedb_slow_queries_tool import (
    get_yugabytedb_slow_queries,
)
from integrations.yugabytedb.tools.yugabytedb_table_stats_tool import (
    get_yugabytedb_table_stats,
)

_YB_TOOLS = [
    get_yugabytedb_cluster_status,
    get_yugabytedb_current_queries,
    get_yugabytedb_server_status,
    get_yugabytedb_slow_queries,
    get_yugabytedb_table_stats,
]


@pytest.mark.parametrize("fn", _YB_TOOLS, ids=lambda fn: fn.__name__)
def test_port_not_exposed_to_llm(fn) -> None:
    rt = fn.__opensre_registered_tool__
    assert "port" in rt.injected_params
    properties = rt.public_input_schema.get("properties") or {}
    assert "port" not in properties


@pytest.mark.parametrize("fn", _YB_TOOLS, ids=lambda fn: fn.__name__)
def test_port_still_callable_directly(fn) -> None:
    """``port`` remains a real parameter (framework fills it from config);
    only its visibility to the LLM changes."""
    assert "port" in inspect.signature(fn).parameters
