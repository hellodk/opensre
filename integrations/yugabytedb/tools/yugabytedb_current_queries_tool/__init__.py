"""YugabyteDB Current Queries Tool."""

from typing import Any

from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.sql_wrapper import call_db_tool_with_default_db_warning
from integrations.yugabytedb import (
    get_current_queries,
    resolve_yugabytedb_config,
    yugabytedb_extract_params,
    yugabytedb_is_available,
)


@tool(
    name="get_yugabytedb_current_queries",
    description=(
        "Retrieve currently executing YugabyteDB queries above a specific duration "
        "threshold. Reflects only queries visible on the YB-TServer node the "
        "connection landed on, not the full cluster."
    ),
    source="yugabytedb",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Identifying long-running queries that may be causing performance issues",
        "Investigating slow or stuck queries during incidents",
        "Finding resource-intensive queries correlating with alert timeframes",
    ],
    is_available=yugabytedb_is_available,
    injected_params=("host",),
    extract_params=yugabytedb_extract_params,
)
def get_yugabytedb_current_queries(
    host: str,
    database: str | None = None,
    threshold_seconds: int = 1,
    port: int = 5433,
) -> dict[str, Any]:
    """Fetch currently running queries above the threshold (default 1 second)."""
    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="yugabyte",
        config_resolver=resolve_yugabytedb_config,
        resolver_kwargs={"host": host, "port": port},
        db_caller=lambda config: get_current_queries(config, threshold_seconds=threshold_seconds),
    )
