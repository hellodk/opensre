"""Shared YugabyteDB integration helpers.

Provides configuration, connectivity validation, and read-only diagnostic
queries for YugabyteDB (YSQL) instances. All operations are production-safe:
read-only, timeouts enforced, result sizes capped.

YugabyteDB's YSQL API is PostgreSQL-wire-compatible, so this module mirrors
``integrations/postgresql/__init__.py`` closely and reuses the same
``psycopg2-binary`` dependency. Two YugabyteDB-specific defaults differ from
PostgreSQL: the YSQL port is ``5433`` (vs. ``5432``) and the default user is
``yugabyte`` (vs. ``postgres``).

Every diagnostic query also stamps its result with ``connected_node`` (from
``inet_server_addr()``). Because YugabyteDB is a distributed cluster of
YB-TServer nodes, ``host`` frequently points at a load-balancer VIP or
round-robin DNS name rather than a single node — two tool calls in the same
investigation can silently land on two different nodes, each returning a
coherent-looking but mutually inconsistent per-node slice (``pg_stat_*``
views are per-node on YugabyteDB, not cluster-wide aggregates). Stamping the
node lets an investigator tell whether two calls actually saw the same node.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from pydantic import Field, field_validator

from core.tool_framework.utils.tool_availability import tool_unavailable
from integrations._relational import (
    RelationalConfigBase,
    env_int,
    env_str,
    resolve_stored_or_env_config,
)
from integrations._validation_helpers import report_classify_failure, report_validation_failure

logger = logging.getLogger(__name__)

DEFAULT_YUGABYTEDB_PORT = 5433
DEFAULT_YUGABYTEDB_USER = "yugabyte"
DEFAULT_YUGABYTEDB_SSL_MODE = "prefer"  # prefer, require, disable
DEFAULT_YUGABYTEDB_TIMEOUT_SECONDS = 10.0
DEFAULT_YUGABYTEDB_MAX_RESULTS = 50

# YSQL's `SELECT version()` returns a compound string of the form
# `PostgreSQL 11.2-YB-2.20.0.0-b0 on x86_64-pc-linux-gnu, compiled by gcc ...`
# — the PG-compat version and the YB build version are concatenated with
# `-YB-` inside a single whitespace-delimited token, so PostgreSQL's
# `version_info.split()[1]` extraction would yield the mangled compound
# token `11.2-YB-2.20.0.0-b0`, not a clean version number. This pattern pulls
# the PG-compat version, the YB version, and the build number apart.
_VERSION_PATTERN = re.compile(r"PostgreSQL ([\d.]+)-YB-([\d.]+\.\d+)-b(\d+)")


class YugabyteDBConfig(RelationalConfigBase):
    """Normalized YugabyteDB (YSQL) connection settings."""

    host: str = ""
    port: int = DEFAULT_YUGABYTEDB_PORT
    database: str = ""
    username: str = DEFAULT_YUGABYTEDB_USER
    password: str = ""
    ssl_mode: str = DEFAULT_YUGABYTEDB_SSL_MODE  # prefer, require, disable
    timeout_seconds: float = Field(default=DEFAULT_YUGABYTEDB_TIMEOUT_SECONDS, gt=0)
    max_results: int = Field(default=DEFAULT_YUGABYTEDB_MAX_RESULTS, gt=0, le=200)
    integration_id: str = ""

    @field_validator("username", mode="before")
    @classmethod
    def _normalize_username(cls, value: Any) -> str:  # type: ignore[override]
        normalized = str(value or DEFAULT_YUGABYTEDB_USER).strip()
        return normalized or DEFAULT_YUGABYTEDB_USER

    @field_validator("ssl_mode", mode="before")
    @classmethod
    def _normalize_ssl_mode(cls, value: Any) -> str:
        normalized = str(value or DEFAULT_YUGABYTEDB_SSL_MODE).strip()
        return normalized or DEFAULT_YUGABYTEDB_SSL_MODE

    @property
    def is_configured(self) -> bool:
        return bool(self.host and self.database)


@dataclass(frozen=True)
class YugabyteDBValidationResult:
    """Result of validating a YugabyteDB integration."""

    ok: bool
    detail: str


def build_yugabytedb_config(raw: dict[str, Any] | None) -> YugabyteDBConfig:
    """Build a normalized YugabyteDB config object from env/store data."""
    return YugabyteDBConfig.model_validate(raw or {})


def yugabytedb_config_from_env() -> YugabyteDBConfig | None:
    """Load a YugabyteDB config from env vars."""
    host = env_str("YUGABYTEDB_HOST")
    database = env_str("YUGABYTEDB_DATABASE")
    if not host or not database:
        return None
    return build_yugabytedb_config(
        {
            "host": host,
            "port": env_int("YUGABYTEDB_PORT", DEFAULT_YUGABYTEDB_PORT),
            "database": database,
            "username": env_str("YUGABYTEDB_USERNAME", DEFAULT_YUGABYTEDB_USER),
            "password": os.getenv("YUGABYTEDB_PASSWORD", ""),
            "ssl_mode": env_str("YUGABYTEDB_SSL_MODE", DEFAULT_YUGABYTEDB_SSL_MODE),
        }
    )


def resolve_yugabytedb_config(
    host: str, database: str, port: int = DEFAULT_YUGABYTEDB_PORT
) -> YugabyteDBConfig:
    """Build a config for the given host/database, resolving credentials from store or env.

    The LLM supplies only identifying params (host, database, port).
    Credentials (username, password, ssl_mode) are resolved from the stored
    integration or environment variables so they never appear in tool signatures.
    """
    return resolve_stored_or_env_config(
        "yugabytedb",
        host=host,
        database=database,
        port=port,
        build_config=build_yugabytedb_config,
        env_loader=yugabytedb_config_from_env,
        extra_from_credentials=lambda credentials: {
            "username": credentials.get("username", DEFAULT_YUGABYTEDB_USER),
            "password": credentials.get("password", ""),
            "ssl_mode": credentials.get("ssl_mode", DEFAULT_YUGABYTEDB_SSL_MODE),
        },
        extra_from_env=lambda config: {
            "username": config.username,
            "password": config.password,
            "ssl_mode": config.ssl_mode,
        },
    )


def _get_connection(config: YugabyteDBConfig) -> Any:
    """Create a psycopg2 connection from config. Caller must close.

    Plain ``psycopg2`` pointed at a specific host, not YugabyteDB's
    topology-aware "smart driver", is deliberate: smart drivers load-balance
    connections across nodes, which is actively harmful for a diagnostic tool
    that wants a stable, identifiable node per call (see ``connected_node``
    stamping on every query function below).
    """
    try:
        import psycopg2  # type: ignore[import-untyped]
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "psycopg2 is not installed. Install it with: pip install psycopg2-binary"
        ) from exc

    return psycopg2.connect(
        host=config.host,
        port=config.port,
        database=config.database,
        user=config.username,
        password=config.password,
        sslmode=config.ssl_mode,
        connect_timeout=int(config.timeout_seconds),
        options=f"-c statement_timeout={int(config.timeout_seconds * 1000)}ms",
        application_name="opensre",
    )


def _extract_yugabytedb_version(version_info: str | None) -> str:
    """Parse the YB build version out of YSQL's compound ``version()`` string.

    Returns ``"unknown"`` if ``version_info`` is empty or does not match the
    expected ``PostgreSQL <pg-version>-YB-<yb-version>-b<build>`` shape (e.g.
    the connected server is not actually YugabyteDB).
    """
    if not version_info:
        return "unknown"
    match = _VERSION_PATTERN.search(version_info)
    if not match:
        return "unknown"
    pg_version, yb_version, build = match.groups()
    return f"{yb_version} (PG {pg_version} compat, build {build})"


def _get_connected_node(cursor: Any) -> str:
    """Return the address of the YB-TServer node this connection landed on.

    ``host`` frequently points at a load-balancer VIP or round-robin DNS name
    rather than a single node, so tool responses are stamped with this value
    to let an investigator tell whether two calls in the same investigation
    actually saw the same node.
    """
    cursor.execute("SELECT inet_server_addr()::text")
    row = cursor.fetchone()
    node = row[0] if row else None
    return node or "unknown"


def validate_yugabytedb_config(config: YugabyteDBConfig) -> YugabyteDBValidationResult:
    """Validate YugabyteDB connectivity with a lightweight query."""
    if not config.host:
        return YugabyteDBValidationResult(ok=False, detail="YugabyteDB host is required.")
    if not config.database:
        return YugabyteDBValidationResult(ok=False, detail="YugabyteDB database is required.")

    try:
        conn = _get_connection(config)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT version()")
            version_info = cursor.fetchone()[0]
            cursor.close()

            version = _extract_yugabytedb_version(version_info)

            return YugabyteDBValidationResult(
                ok=True,
                detail=(f"Connected to YugabyteDB {version}; target database: {config.database}."),
            )
        finally:
            conn.close()
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="yugabytedb",
            method="validate_yugabytedb_config",
        )
        return YugabyteDBValidationResult(ok=False, detail=f"YugabyteDB connection failed: {err}")


def yugabytedb_is_available(sources: dict[str, dict]) -> bool:
    """Check if YugabyteDB integration identifying params are present."""
    yb = sources.get("yugabytedb", {})
    return bool(yb.get("host") and yb.get("database"))


def yugabytedb_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract YugabyteDB identifying params (host, database, port) from resolved integrations.

    Credentials (username, password, ssl_mode) are resolved internally by
    ``resolve_yugabytedb_config`` from the integration store or environment, so
    they never appear in tool signatures and are never seen by the LLM.
    """
    yb = sources.get("yugabytedb", {})
    return {
        "host": str(yb.get("host", "")).strip(),
        "database": str(yb.get("database", "")).strip(),
        "port": int(yb.get("port") or DEFAULT_YUGABYTEDB_PORT),
    }


def get_server_status(config: YugabyteDBConfig) -> dict[str, Any]:
    """Retrieve server status (connections, databases, cache hit ratio).

    Read-only: queries system views pg_stat_database and pg_stat_activity.

    These views are populated per-node on YugabyteDB: every YB-TServer runs
    its own local YSQL backend processes with no cluster-wide aggregation, so
    this reflects only the node this connection landed on, not the full
    cluster (this includes ``max_connections``, which comes from
    ``pg_settings`` but is equally per-node).
    """
    if not config.is_configured:
        return tool_unavailable("yugabytedb", "Not configured.")

    try:
        conn = _get_connection(config)
        try:
            cursor = conn.cursor()

            connected_node = _get_connected_node(cursor)

            cursor.execute("SELECT version()")
            version_info = cursor.fetchone()[0]
            version = _extract_yugabytedb_version(version_info)

            cursor.execute("""
                SELECT
                    count(*) as total_connections,
                    count(*) FILTER (WHERE state = 'active') as active_connections,
                    count(*) FILTER (WHERE state = 'idle') as idle_connections,
                    max(max_conn.setting::int) as max_connections
                FROM pg_stat_activity, (SELECT setting FROM pg_settings WHERE name = 'max_connections') max_conn
            """)
            conn_stats = cursor.fetchone()

            cursor.execute("""
                SELECT
                    numbackends,
                    xact_commit,
                    xact_rollback,
                    blks_read,
                    blks_hit,
                    tup_returned,
                    tup_fetched,
                    tup_inserted,
                    tup_updated,
                    tup_deleted
                FROM pg_stat_database
                WHERE datname = current_database()
            """)
            db_stats = cursor.fetchone()

            # Calculate cache hit ratio
            cache_hit_ratio = 0.0
            if db_stats and db_stats[3] + db_stats[4] > 0:  # blks_read + blks_hit > 0
                cache_hit_ratio = round((db_stats[4] / (db_stats[3] + db_stats[4])) * 100, 2)

            cursor.close()
            return {
                "source": "yugabytedb",
                "available": True,
                "version": version,
                "connected_node": connected_node,
                "connections": {
                    "total": conn_stats[0] if conn_stats else 0,
                    "active": conn_stats[1] if conn_stats else 0,
                    "idle": conn_stats[2] if conn_stats else 0,
                    "max_connections": conn_stats[3] if conn_stats else 0,
                },
                "database_stats": {
                    "backends": db_stats[0] if db_stats else 0,
                    "transactions": {
                        "committed": db_stats[1] if db_stats else 0,
                        "rolled_back": db_stats[2] if db_stats else 0,
                    },
                    "cache_hit_ratio_percent": cache_hit_ratio,
                    "tuples": {
                        "returned": db_stats[5] if db_stats else 0,
                        "fetched": db_stats[6] if db_stats else 0,
                        "inserted": db_stats[7] if db_stats else 0,
                        "updated": db_stats[8] if db_stats else 0,
                        "deleted": db_stats[9] if db_stats else 0,
                    },
                },
                "note": (
                    "Connection, transaction, and cache-hit metrics reflect only "
                    "the YB-TServer node this connection landed on (connected_node), "
                    "not a cluster-wide aggregate."
                ),
            }
        finally:
            conn.close()
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="yugabytedb",
            method="get_server_status",
        )
        return tool_unavailable("yugabytedb", str(err))


def get_current_queries(
    config: YugabyteDBConfig,
    threshold_seconds: int = 1,
) -> dict[str, Any]:
    """Retrieve currently running queries above a duration threshold.

    Read-only: queries pg_stat_activity system view.
    Results are capped at config.max_results.

    Per-node caveat: a query running on a different YB-TServer than the one
    this connection landed on (see ``connected_node``) will not show up.
    """
    if not config.is_configured:
        return tool_unavailable("yugabytedb", "Not configured.")

    try:
        conn = _get_connection(config)
        try:
            cursor = conn.cursor()

            connected_node = _get_connected_node(cursor)

            cursor.execute(
                """
                SELECT
                    pid,
                    usename,
                    application_name,
                    client_addr::text,
                    state,
                    query_start,
                    extract(epoch from (now() - query_start))::int as duration_seconds,
                    wait_event_type,
                    wait_event,
                    left(query, 500) as query_truncated
                FROM pg_stat_activity
                WHERE state = 'active'
                    AND query_start IS NOT NULL
                    AND extract(epoch from (now() - query_start)) >= %s
                    AND pid != pg_backend_pid()
                ORDER BY query_start ASC
                LIMIT %s
            """,
                (threshold_seconds, config.max_results),
            )

            queries = []
            for row in cursor.fetchall():
                queries.append(
                    {
                        "pid": row[0],
                        "username": row[1],
                        "application_name": row[2] or "",
                        "client_addr": row[3] or "local",
                        "state": row[4],
                        "query_start": str(row[5]),
                        "duration_seconds": row[6],
                        "wait_event_type": row[7] or "",
                        "wait_event": row[8] or "",
                        "query_truncated": row[9] or "",
                    }
                )

            cursor.close()
            return {
                "source": "yugabytedb",
                "available": True,
                "connected_node": connected_node,
                "threshold_seconds": threshold_seconds,
                "total_queries": len(queries),
                "queries": queries,
                "note": (
                    "Reflects only queries visible on the YB-TServer node this "
                    "connection landed on (connected_node), not the full cluster."
                ),
            }
        finally:
            conn.close()
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="yugabytedb",
            method="get_current_queries",
        )
        return tool_unavailable("yugabytedb", str(err))


def get_table_stats(
    config: YugabyteDBConfig,
    schema_name: str = "public",
) -> dict[str, Any]:
    """Retrieve table statistics (size, row counts, index usage).

    Read-only: queries pg_stat_user_tables and pg_class system views.
    Results capped at config.max_results.

    YugabyteDB's DocDB storage is an LSM tree, not PostgreSQL's heap, so it
    has no autovacuum daemon: ``n_dead_tup`` typically reads ``0`` and
    ``last_vacuum``/``last_autovacuum``/``last_analyze`` typically read
    ``NULL``. The fields are kept for schema parity with the PostgreSQL tool;
    do not treat them as actionable maintenance signals.
    """
    if not config.is_configured:
        return tool_unavailable("yugabytedb", "Not configured.")

    try:
        conn = _get_connection(config)
        try:
            cursor = conn.cursor()

            connected_node = _get_connected_node(cursor)

            cursor.execute(
                """
                SELECT
                    schemaname,
                    relname,
                    n_tup_ins,
                    n_tup_upd,
                    n_tup_del,
                    n_live_tup,
                    n_dead_tup,
                    seq_scan,
                    seq_tup_read,
                    idx_scan,
                    idx_tup_fetch,
                    last_vacuum,
                    last_autovacuum,
                    last_analyze,
                    last_autoanalyze,
                    pg_total_relation_size(t.relid) as total_size_bytes,
                    pg_relation_size(t.relid) as table_size_bytes,
                    pg_indexes_size(t.relid) as indexes_size_bytes
                FROM pg_stat_user_tables t
                WHERE schemaname = %s
                ORDER BY pg_total_relation_size(t.relid) DESC
                LIMIT %s
            """,
                (schema_name, config.max_results),
            )

            tables = []
            for row in cursor.fetchall():
                # Calculate index usage ratio
                index_usage = 0.0
                total_scans = (row[7] or 0) + (row[9] or 0)  # seq_scan + idx_scan
                if total_scans > 0:
                    index_usage = round(((row[9] or 0) / total_scans) * 100, 2)

                tables.append(
                    {
                        "schema": row[0],
                        "table_name": row[1],
                        "tuples": {
                            "inserted": row[2] or 0,
                            "updated": row[3] or 0,
                            "deleted": row[4] or 0,
                            "live": row[5] or 0,
                            "dead": row[6] or 0,
                        },
                        "scans": {
                            "sequential": row[7] or 0,
                            "sequential_tuples": row[8] or 0,
                            "index": row[9] or 0,
                            "index_tuples": row[10] or 0,
                            "index_usage_percent": index_usage,
                        },
                        "maintenance": {
                            "last_vacuum": str(row[11]) if row[11] else None,
                            "last_autovacuum": str(row[12]) if row[12] else None,
                            "last_analyze": str(row[13]) if row[13] else None,
                            "last_autoanalyze": str(row[14]) if row[14] else None,
                        },
                        "size": {
                            "total_bytes": row[15] or 0,
                            "table_bytes": row[16] or 0,
                            "indexes_bytes": row[17] or 0,
                            "total_mb": round((row[15] or 0) / 1024 / 1024, 2),
                        },
                    }
                )

            cursor.close()
            return {
                "source": "yugabytedb",
                "available": True,
                "connected_node": connected_node,
                "schema": schema_name,
                "total_tables": len(tables),
                "tables": tables,
                "note": (
                    "YugabyteDB's DocDB storage engine does not run PostgreSQL's "
                    "autovacuum daemon: n_dead_tup, last_vacuum, last_autovacuum, "
                    "and last_analyze typically read 0/NULL and are not actionable "
                    "maintenance signals. Table size fields reflect the values "
                    "reported by pg_total_relation_size()/pg_relation_size() as-is."
                ),
            }
        finally:
            conn.close()
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="yugabytedb",
            method="get_table_stats",
        )
        return tool_unavailable("yugabytedb", str(err))


def get_cluster_status(config: YugabyteDBConfig) -> dict[str, Any]:
    """List live YugabyteDB YB-TServer nodes and their placement.

    Read-only: queries the ``yb_servers()`` YSQL function, the documented,
    supported mechanism for topology discovery over a plain YSQL connection.

    ``yb_servers()`` never lists YB-Master nodes — only YB-TServers (the
    nodes that accept YSQL connections; masters never do). ``node_type``
    distinguishes "primary cluster" vs. "read replica cluster" nodes in a
    multi-region read-replica deployment, not master vs. tserver.

    Tablet-level replication lag and leader/follower status are not
    observable over a SQL connection (that requires yb-admin or the
    YB-Master/YB-TServer HTTP UIs, which this integration does not have
    access to) and are explicitly out of scope.
    """
    if not config.is_configured:
        return tool_unavailable("yugabytedb", "Not configured.")

    try:
        conn = _get_connection(config)
        try:
            cursor = conn.cursor()

            connected_node = _get_connected_node(cursor)

            cursor.execute("SELECT * FROM yb_servers()")

            nodes = []
            for row in cursor.fetchall():
                nodes.append(
                    {
                        "host": row[0],
                        "port": row[1],
                        "num_connections": row[2],
                        "node_type": row[3],
                        "cloud": row[4],
                        "region": row[5],
                        "zone": row[6],
                        "public_ip": row[7],
                        "uuid": row[8],
                    }
                )

            cursor.close()
            return {
                "source": "yugabytedb",
                "available": True,
                "node_count": len(nodes),
                "nodes": nodes,
                "connected_node": connected_node,
                "note": (
                    "Reflects live YB-TServer nodes visible to yb_servers(). "
                    "Tablet-level replication lag and leader/follower status are "
                    "not observable over a SQL connection and are out of scope "
                    "for this integration."
                ),
            }
        finally:
            conn.close()
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="yugabytedb",
            method="get_cluster_status",
        )
        return tool_unavailable("yugabytedb", str(err))


def get_slow_queries(
    config: YugabyteDBConfig,
    threshold_ms: int = 1000,
    limit: int | None = None,
) -> dict[str, Any]:
    """Retrieve slow query statistics from pg_stat_statements.

    Read-only: queries pg_stat_statements extension view.
    Results capped at config.max_results.

    pg_stat_statements is a first-class, drop-in-compatible extension on
    YugabyteDB and is commonly enabled by default via
    shared_preload_libraries, unlike vanilla PostgreSQL.
    """
    if not config.is_configured:
        return tool_unavailable("yugabytedb", "Not configured.")

    effective_limit = min(limit or config.max_results, config.max_results)

    try:
        conn = _get_connection(config)
        try:
            cursor = conn.cursor()

            connected_node = _get_connected_node(cursor)

            cursor.execute("""
                SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'
            """)

            if not cursor.fetchone():
                cursor.close()
                return {
                    "source": "yugabytedb",
                    "available": True,
                    "connected_node": connected_node,
                    "extension_available": False,
                    "note": (
                        "pg_stat_statements extension is not installed. "
                        "Install it with CREATE EXTENSION pg_stat_statements; "
                        "and add 'pg_stat_statements' to shared_preload_libraries."
                    ),
                    "queries": [],
                }

            cursor.execute(
                """
                SELECT
                    queryid,
                    left(query, 500) as query_truncated,
                    calls,
                    round(total_exec_time::numeric, 3) as total_time_ms,
                    round(mean_exec_time::numeric, 3) as mean_time_ms,
                    round(min_exec_time::numeric, 3) as min_time_ms,
                    round(max_exec_time::numeric, 3) as max_time_ms,
                    round(stddev_exec_time::numeric, 3) as stddev_time_ms,
                    rows as total_rows,
                    100.0 * shared_blks_hit / nullif(shared_blks_hit + shared_blks_read, 0) as hit_percent
                FROM pg_stat_statements
                WHERE mean_exec_time >= %s
                ORDER BY mean_exec_time DESC
                LIMIT %s
            """,
                (threshold_ms, effective_limit),
            )

            queries = []
            for row in cursor.fetchall():
                queries.append(
                    {
                        "queryid": str(row[0]) if row[0] else "",
                        "query_truncated": row[1] or "",
                        "calls": row[2],
                        "total_time_ms": row[3],
                        "mean_time_ms": row[4],
                        "min_time_ms": row[5],
                        "max_time_ms": row[6],
                        "stddev_time_ms": row[7],
                        "total_rows": row[8],
                        "cache_hit_percent": round(row[9] or 0, 2),
                    }
                )

            cursor.close()
            return {
                "source": "yugabytedb",
                "available": True,
                "connected_node": connected_node,
                "extension_available": True,
                "threshold_ms": threshold_ms,
                "total_queries": len(queries),
                "queries": queries,
            }
        finally:
            conn.close()
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="yugabytedb",
            method="get_slow_queries",
        )
        return tool_unavailable("yugabytedb", str(err))


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[YugabyteDBConfig | None, str | None]:
    try:
        cfg = build_yugabytedb_config(
            {
                "host": credentials.get("host", ""),
                "port": credentials.get("port", DEFAULT_YUGABYTEDB_PORT),
                "database": credentials.get("database", ""),
                "username": credentials.get("username", DEFAULT_YUGABYTEDB_USER),
                "password": credentials.get("password", ""),
                "ssl_mode": credentials.get("ssl_mode", DEFAULT_YUGABYTEDB_SSL_MODE),
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="yugabytedb", record_id=record_id)
        return None, None
    if cfg.host and cfg.database:
        return cfg, "yugabytedb"
    return None, None
