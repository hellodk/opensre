"""YugabyteDB Table Stats Tool."""

from typing import Any

from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.sql_wrapper import call_db_tool_with_default_db_warning
from integrations.yugabytedb import (
    get_table_stats,
    resolve_yugabytedb_config,
    yugabytedb_extract_params,
    yugabytedb_is_available,
)


@tool(
    name="get_yugabytedb_table_stats",
    description=(
        "Retrieve YugabyteDB table statistics including size, row counts, index usage, "
        "and maintenance info. YugabyteDB's DocDB storage does not use PostgreSQL's "
        "autovacuum, so maintenance timestamps are typically NULL."
    ),
    source="yugabytedb",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Identifying large tables or rapid table growth during storage incidents",
        "Analyzing table scan patterns and index usage efficiency",
        "Checking table row/tuple counts for a given schema",
    ],
    is_available=yugabytedb_is_available,
    injected_params=("host",),
    extract_params=yugabytedb_extract_params,
)
def get_yugabytedb_table_stats(
    host: str,
    database: str | None = None,
    schema_name: str = "public",
    port: int = 5433,
) -> dict[str, Any]:
    """Fetch table statistics for a specific schema (default 'public')."""
    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="yugabyte",
        config_resolver=resolve_yugabytedb_config,
        resolver_kwargs={"host": host, "port": port},
        db_caller=lambda config: get_table_stats(config, schema_name=schema_name),
    )
