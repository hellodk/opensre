"""End-to-end registry discovery tests for the YugabyteDB tools.

Guards against the integration package existing but never being registered:
``integrations.yugabytedb.tools`` must appear in ``INTEGRATION_TOOL_PACKAGES``
so the chat surface actually sees the tools.
"""

from __future__ import annotations

from tools.registry import get_registered_tools

_YUGABYTEDB_TOOLS = (
    "get_yugabytedb_cluster_status",
    "get_yugabytedb_current_queries",
    "get_yugabytedb_server_status",
    "get_yugabytedb_slow_queries",
    "get_yugabytedb_table_stats",
)


class TestYugabyteDBRegistryDiscovery:
    def test_all_tools_registered_for_chat(self):
        names = {tool.name for tool in get_registered_tools("chat")}
        missing = [name for name in _YUGABYTEDB_TOOLS if name not in names]
        assert missing == [], f"Tools not discovered by the registry: {missing}"
