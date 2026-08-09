"""YugabyteDB Cluster Status Tool."""

from typing import Any

from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.sql_wrapper import call_db_tool_with_default_db_warning
from integrations.yugabytedb import (
    get_cluster_status,
    resolve_yugabytedb_config,
    yugabytedb_extract_params,
    yugabytedb_is_available,
)


@tool(
    name="get_yugabytedb_cluster_status",
    description=(
        "List live YugabyteDB YB-TServer nodes and their placement (cloud/region/zone) "
        "via yb_servers(). Does not report tablet-level replication lag — that requires "
        "yb-admin/cluster-internal access this integration does not have."
    ),
    source="yugabytedb",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Discovering live YugabyteDB YB-TServer nodes and their cloud/region/zone placement",
        "Checking whether separate tool calls in an investigation landed on the same node",
        "Confirming cluster topology during a database incident",
    ],
    is_available=yugabytedb_is_available,
    injected_params=("host",),
    extract_params=yugabytedb_extract_params,
)
def get_yugabytedb_cluster_status(
    host: str,
    database: str | None = None,
    port: int = 5433,
) -> dict[str, Any]:
    """Fetch live YB-TServer node topology from a YugabyteDB cluster."""
    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="yugabyte",
        config_resolver=resolve_yugabytedb_config,
        resolver_kwargs={"host": host, "port": port},
        db_caller=get_cluster_status,
    )
