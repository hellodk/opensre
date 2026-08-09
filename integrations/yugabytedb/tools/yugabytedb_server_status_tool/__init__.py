"""YugabyteDB Server Status Tool."""

from typing import Any

from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.sql_wrapper import call_db_tool_with_default_db_warning
from integrations.yugabytedb import (
    get_server_status,
    resolve_yugabytedb_config,
    yugabytedb_extract_params,
    yugabytedb_is_available,
)


@tool(
    name="get_yugabytedb_server_status",
    description=(
        "Retrieve YugabyteDB (YSQL) server metrics including connections, transactions, "
        "cache hit ratio, and database statistics. Metrics reflect the YB-TServer node "
        "the connection landed on, not the full cluster."
    ),
    source="yugabytedb",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Checking YugabyteDB server health during an incident",
        "Identifying connection saturation or exhaustion issues",
        "Reviewing transaction rates and cache efficiency metrics",
    ],
    is_available=yugabytedb_is_available,
    injected_params=("host",),
    extract_params=yugabytedb_extract_params,
)
def get_yugabytedb_server_status(
    host: str,
    database: str | None = None,
    port: int = 5433,
) -> dict[str, Any]:
    """Fetch server status metrics from a YugabyteDB instance."""
    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="yugabyte",
        config_resolver=resolve_yugabytedb_config,
        resolver_kwargs={"host": host, "port": port},
        db_caller=get_server_status,
    )
