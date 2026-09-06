# YugabyteDB Integration — Architecture & Implementation Plan

Status: **planning only — no implementation code written**. This document is the design
artifact required before any ticket/branch is opened, per the repo's `AGENTS.md` and the
`docs/adding-tools-and-integrations.md` definition-of-done. It is written to be reviewed by
a YugabyteDB domain expert before implementation starts (see §8).

**Revision note (2026-08-09):** corrections from both review sections below have been
folded into the plan above; this is now the implementation-ready version.

## 0. Summary

Add a `yugabytedb` integration that mirrors the existing `integrations/postgresql/`
integration file-for-file. YugabyteDB's YSQL API is PostgreSQL-wire-compatible, so the same
`psycopg2-binary` dependency already declared in `pyproject.toml` (used today by
`integrations/postgresql/` and `integrations/mariadb`-adjacent relational integrations) is
reused as-is — **no new dependency**. Default port `5433` (YugabyteDB's YSQL default, vs.
PostgreSQL's `5432`), default user `yugabyte` (vs. PostgreSQL's `postgres`).

Five tools are planned. Four are near-identical ports of existing PostgreSQL tools (they
query the same `pg_stat_*` system views, or `pg_stat_statements`, because YSQL exposes
them), and one (`get_yugabytedb_cluster_status`) is new and specific to Yugabyte's
distributed, tablet/RAFT-based architecture — this is the one this document flags most
heavily for expert review. `pg_locks`/lock-status is explicitly **not** part of this
integration, in v1 or as future work — see §4.3.

---

## 1. Architecture overview

The request flow is identical in shape to `integrations/postgresql/`, substituting the
Yugabyte config/connection/query layer. One inline diagram:

```
LLM tool call (investigation or chat surface)
        │  e.g. get_yugabytedb_server_status(host="yb.prod.internal", database="app_db")
        ▼
tools/.../yugabytedb_*_tool/__init__.py   (@tool-decorated function)
        │  injected_params=("host",) → host is overridden by extract_params, never LLM-controlled
        │  calls call_db_tool_with_default_db_warning(...)
        ▼
integrations/yugabytedb/__init__.py: resolve_yugabytedb_config(host, database, port)
        │  resolve_stored_or_env_config("yugabytedb", ...)
        │  1. integration store (~/.opensre/integrations.json) — preferred
        │  2. env vars (YUGABYTEDB_*) via yugabytedb_config_from_env()
        │  3. identifiers only (host/database/port), no credentials
        ▼
YugabyteDBConfig (pydantic, RelationalConfigBase)
        ▼
integrations/yugabytedb/__init__.py: _get_connection(config)
        │  psycopg2.connect(host, port=5433, database, user="yugabyte", password,
        │                    sslmode, connect_timeout, statement_timeout, application_name)
        ▼
YSQL system view query (pg_stat_activity / pg_stat_database / pg_stat_user_tables /
                         yb_servers() — see §3 for which view backs which tool)
        │  read-only, capped at config.max_results, statement_timeout enforced
        ▼
Normalized dict result: {"source": "yugabytedb", "available": true, ...}
        │  on failure: tool_unavailable("yugabytedb", str(err)) — never raises to the LLM
        ▼
Investigation evidence / chat tool response
```

Config resolution, connection lifecycle, and error handling are byte-for-byte the same
pattern as PostgreSQL (`integrations/_relational.py`'s `RelationalConfigBase`,
`resolve_stored_or_env_config`, and `env_int`/`env_str` helpers are reused directly — no
new shared helper is needed). The only genuinely new logic is the cluster-status tool's
query and result shape (§3).

---

## 2. Config

### 2.1 `integrations/yugabytedb/__init__.py` — `YugabyteDBConfig`

Mirrors `integrations/postgresql/__init__.py:PostgreSQLConfig` (lines 35–62) exactly,
extending `RelationalConfigBase` from `integrations/_relational.py`:

```python
DEFAULT_YUGABYTEDB_PORT = 5433
DEFAULT_YUGABYTEDB_USER = "yugabyte"
DEFAULT_YUGABYTEDB_SSL_MODE = "prefer"          # verify against real cluster — see §8
DEFAULT_YUGABYTEDB_TIMEOUT_SECONDS = 10.0
DEFAULT_YUGABYTEDB_MAX_RESULTS = 50


class YugabyteDBConfig(RelationalConfigBase):
    """Normalized YugabyteDB (YSQL) connection settings."""

    host: str = ""
    port: int = DEFAULT_YUGABYTEDB_PORT
    database: str = ""
    username: str = DEFAULT_YUGABYTEDB_USER
    password: str = ""
    ssl_mode: str = DEFAULT_YUGABYTEDB_SSL_MODE          # prefer, require, disable
    timeout_seconds: float = Field(default=DEFAULT_YUGABYTEDB_TIMEOUT_SECONDS, gt=0)
    max_results: int = Field(default=DEFAULT_YUGABYTEDB_MAX_RESULTS, gt=0, le=200)
    integration_id: str = ""

    @field_validator("username", mode="before")
    @classmethod
    def _normalize_username(cls, value: Any) -> str:
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
```

`RelationalConfigBase` already supplies `host`/`database`/`username` `field_validator`s
(`mode="before", check_fields=False`), so `YugabyteDBConfig` only needs the two
Yugabyte-specific default overrides (`username`, `ssl_mode`) — same shape as
`PostgreSQLConfig`.

Same-file functions to port 1:1 from `integrations/postgresql/__init__.py`:

| PostgreSQL function | YugabyteDB equivalent | Change |
| --- | --- | --- |
| `build_postgresql_config` | `build_yugabytedb_config` | rename only |
| `postgresql_config_from_env` | `yugabytedb_config_from_env` | env var names + defaults |
| `resolve_postgresql_config` | `resolve_yugabytedb_config` | service string `"yugabytedb"` |
| `_get_connection` | `_get_connection` | same psycopg2 call, `application_name="opensre"` unchanged |
| `validate_postgresql_config` / `PostgreSQLValidationResult` | `validate_yugabytedb_config` / `YugabyteDBValidationResult` | `SELECT version()` still works, but **must not reuse the PostgreSQL `version_info.split()[1]` extraction verbatim**. YSQL's `version()` returns a compound string of the form `PostgreSQL 11.2-YB-2.20.0.0-b0 on x86_64-pc-linux-gnu, compiled by gcc ...` — the PG-compat version and the YB build version are concatenated with `-YB-` inside a single whitespace-delimited token, so `.split()[1]` yields the mangled token `11.2-YB-2.20.0.0-b0`, not a clean version number. `validate_yugabytedb_config` needs a dedicated regex instead, e.g. `PostgreSQL ([\d.]+)-YB-([\d.]+\.\d+)-b(\d+)`, to pull out the PG-compat version, the YB version (the `YB-X.Y.Z.W` segment), and the build number as separate fields. This format has been stable since at least the 2.x series, but confirm the live string against a real cluster before finalizing the regex (build suffix/platform string can vary by build). |
| `postgresql_is_available` | `yugabytedb_is_available` | rename only |
| `postgresql_extract_params` | `yugabytedb_extract_params` | rename only |
| `classify` | `classify` | rename only |

### 2.2 `config/constants/yugabytedb.py`

New leaf module under `config/constants/` (per `AGENTS.md` Code Style — shared env-var
names never live inline in a feature module), mirroring `config/constants/postgresql.py`:

```python
"""YugabyteDB environment variable names."""

from __future__ import annotations

YUGABYTEDB_HOST_ENV = "YUGABYTEDB_HOST"
YUGABYTEDB_PORT_ENV = "YUGABYTEDB_PORT"
YUGABYTEDB_DATABASE_ENV = "YUGABYTEDB_DATABASE"
YUGABYTEDB_USERNAME_ENV = "YUGABYTEDB_USERNAME"
YUGABYTEDB_PASSWORD_ENV = "YUGABYTEDB_PASSWORD"
YUGABYTEDB_SSL_MODE_ENV = "YUGABYTEDB_SSL_MODE"

__all__ = [
    "YUGABYTEDB_DATABASE_ENV",
    "YUGABYTEDB_HOST_ENV",
    "YUGABYTEDB_PASSWORD_ENV",
    "YUGABYTEDB_PORT_ENV",
    "YUGABYTEDB_SSL_MODE_ENV",
    "YUGABYTEDB_USERNAME_ENV",
]
```

Re-export via `config/constants/__init__.py` alongside the existing `postgresql` exports
(check the existing `__init__.py` re-export block and add the `yugabytedb` names next to
it — same pattern used for every other constants module).

---

## 3. Tool surface

Five tools, one package each under `integrations/yugabytedb/tools/`, following the exact
`@tool(...)` decorator shape of `integrations/postgresql/tools/postgresql_server_status_tool/__init__.py`.
All five:

- `source="yugabytedb"`, `surfaces=("investigation", "chat")`
- `injected_params=("host",)`, `extract_params=yugabytedb_extract_params`,
  `is_available=yugabytedb_is_available`
- go through `call_db_tool_with_default_db_warning` (`core/tool_framework/utils/sql_wrapper.py`)
  with `default_db_name="yugabyte"` (Yugabyte's default database is `yugabyte`, not
  `postgres` — this is the one default-DB-name change from the PostgreSQL wrapper calls)
- every tool's result includes which node the connection landed on (`SELECT
  inet_server_addr()`, or the matching row from `yb_servers()`), added as a `connected_node`
  field in each tool's output. **Why this matters**: if `host` resolves to a load-balancer
  VIP or round-robin DNS in front of multiple YB-TServers (a common production topology),
  two separate tool calls in the *same investigation* can silently land on two different
  nodes, each returning a coherent-looking but mutually inconsistent per-node slice (§3.1's
  per-node caveat makes this worse, not better). Stamping the node lets an investigator tell
  whether two tool calls in one investigation actually saw the same node. `docs/yugabytedb.mdx`
  must also note that pointing `host` at a load balancer rather than a specific node
  produces results that vary call-to-call.

### 3.1 `get_yugabytedb_server_status`

- **Description**: "Retrieve YugabyteDB (YSQL) server metrics including connections,
  transactions, cache hit ratio, and database statistics."
- **input_schema**: `host: str` (injected), `database: str | None`, `port: int = 5433`
- **Query**: identical to PostgreSQL's `get_server_status` — `pg_stat_database` +
  `pg_stat_activity` + `pg_settings` (`max_connections`). YSQL exposes these views because
  the YSQL query layer is built on a PostgreSQL fork; connection/transaction/cache-hit
  counters are tracked per-node the same way.
- **Difference from PostgreSQL tool**: **verify during implementation** whether
  `pg_stat_database` counters on YugabyteDB reflect only the local YB-TServer's postgres
  process the connection landed on, or a cluster-wide aggregate. In vanilla PostgreSQL
  there is one server process; in YugabyteDB every YB-TServer runs its own YSQL process,
  so a single connection's `pg_stat_database` view is very likely **per-node, not
  cluster-wide**. If confirmed, the tool's docstring/description and `docs/yugabytedb.mdx`
  must say explicitly "metrics reflect the node you connected to, not the full cluster" —
  do not silently present per-node numbers as if they were cluster totals. Flagged in §8.

### 3.2 `get_yugabytedb_current_queries`

- **Description**: "Retrieve currently running YugabyteDB queries above a duration
  threshold."
- **input_schema**: `host: str` (injected), `database: str | None`, `port: int = 5433`,
  `threshold_seconds: int = 1`
- **Query**: identical to PostgreSQL's `get_current_queries` — `pg_stat_activity` filtered
  on `state = 'active'` and `query_start` age, excluding `pg_backend_pid()`. This view is
  part of the YSQL Postgres-compatible layer and works the same way. Same per-node caveat
  as §3.1 applies — a query running on a *different* YB-TServer than the one this
  connection landed on will not show up. Note this explicitly in the tool description.

### 3.3 `get_yugabytedb_table_stats`

- **Description**: "Retrieve YugabyteDB table statistics including size, row counts, index
  usage, and maintenance info."
- **input_schema**: `host: str` (injected), `database: str | None`, `port: int = 5433`,
  `schema_name: str = "public"`
- **Query**: identical shape to PostgreSQL's `get_table_stats` — `pg_stat_user_tables` +
  `pg_total_relation_size` / `pg_relation_size` / `pg_indexes_size`. **Verify during
  implementation**: YugabyteDB stores table data across tablets (shards) that may live on
  multiple YB-TServer nodes, and `n_live_tup`/`n_dead_tup`/`seq_scan`/`idx_scan` counters in
  `pg_stat_user_tables` are known in the Yugabyte ecosystem to be less reliable than on
  vanilla PostgreSQL (Yugabyte's own docs note that some traditional autovacuum-era stats
  don't map cleanly onto its LSM-tree storage engine, since it doesn't use PostgreSQL's
  heap/MVCC dead-tuple model the same way). Also verify whether `pg_total_relation_size`
  returns local (this node's replica) or cluster-wide (all tablet replicas summed) bytes —
  this materially changes what "table size" means in the tool's output and must be stated
  correctly in `docs/yugabytedb.mdx` rather than assumed. Do not claim vacuum/analyze
  timestamps (`last_vacuum`, `last_autovacuum`) are meaningful without checking — Yugabyte
  does not use PostgreSQL's autovacuum daemon for its LSM-based storage, so these columns
  may always read `NULL`. If confirmed NULL, keep the fields in the output shape (schema
  parity with the PostgreSQL tool) but add a `note` field saying autovacuum-era timestamps
  do not apply to YugabyteDB's storage engine.

### 3.4 `get_yugabytedb_cluster_status` — new, not a PostgreSQL port

This is the tool with no direct PostgreSQL equivalent, because YugabyteDB has no
primary/streaming-replica topology reachable via `pg_stat_replication` in the way
PostgreSQL does. Its replication is intra-cluster, tablet-level, RAFT-consensus based
across YB-TServer nodes (and cross-universe replication, xCluster, is a separate
mechanism that likely is not queryable via plain YSQL SQL at all). What follows is what is
**believed** to be correct based on public YugabyteDB documentation knowledge, explicitly
marked where it needs verification against a real cluster:

- **What is queryable over a normal YSQL/psycopg2 connection**: `yb_servers()` — a YSQL
  function returning one row per live YB-TServer node, with columns `host`, `port`,
  `num_connections`, `node_type`, `cloud`, `region`, `zone`, `public_ip`, `uuid`. This is
  the recommended, documented way for a PG-wire client (e.g. a smart driver or a health
  check) to discover cluster topology without needing `yb-admin` or cluster-internal
  access. `yb_servers()` **never lists YB-Master nodes — it only ever lists YB-TServers**
  (the nodes that actually accept YSQL connections; masters never do). `node_type`
  correspondingly does **not** distinguish master vs. tserver — it distinguishes
  "primary cluster" nodes from "read replica cluster" nodes in a multi-region
  read-replica deployment (values like `primary`/`read_replica`). Confirmed against domain
  review; column list is high-confidence but **verify exact column list and types against
  a real cluster during implementation** — column names/count have changed across
  YugabyteDB versions.
- **What is *not* believed queryable over a plain SQL connection** (out of scope for this
  integration, and should be explicitly cut rather than half-implemented): tablet-level
  leader/follower assignment, per-tablet RAFT replication lag, under-replicated tablet
  counts, and YB-Master node health/role generally (masters are never visible via
  `yb_servers()` or any other plain-YSQL query). These are normally surfaced by `yb-admin`
  (a cluster-internal CLI) or the YB-Master HTTP UI (`:7000`) / YB-TServer HTTP UI
  (`:9000`), none of which this integration has access to — it only has a YSQL
  (`psycopg2`) connection to a database, the same access model as the `postgresql`
  integration. **Do not attempt to add yb-admin subprocess calls or scrape the master/
  tserver HTTP endpoints as part of this integration** — that would require a different
  credential/connectivity model (host+admin-port reachability, not a DB connection) and is
  out of scope; if cluster-internal tablet/replication-lag visibility is wanted later, it
  should be a **separate** integration (e.g. `yugabytedb_admin` or folded into a future
  HTTP-based monitoring integration), not bolted onto this YSQL-only one.
- **Is there a `pg_stat_replication`-equivalent for tablet lag reachable via SQL?**
  Unconfirmed. YugabyteDB does expose some internal system tables/views prefixed `yb_` and
  a `pg_stat_replication`-shaped view may or may not return meaningful rows on YSQL (it
  likely exists as a vestigial PostgreSQL-compatibility view but returns empty, since
  YugabyteDB's replication is not WAL-streaming based). **This must be checked against a
  real cluster before deciding whether `get_yugabytedb_cluster_status` should also probe
  `pg_stat_replication` (and document that it will almost certainly return zero rows) or
  omit that query entirely.** Recommendation for the first implementation: **omit** the
  `pg_stat_replication` probe and scope the tool to `yb_servers()` output only, with a
  `note` field stating that tablet-level replication/leader lag is not observable via this
  integration and would require `yb-admin`/master-UI access.

Proposed tool spec (scoped to what is confidently queryable):

- **Name**: `get_yugabytedb_cluster_status`
- **Description**: "List live YugabyteDB YB-TServer nodes and their placement
  (cloud/region/zone) via `yb_servers()`. Does not report tablet-level replication lag —
  that requires yb-admin/cluster-internal access this integration does not have."
- **input_schema**: `host: str` (injected), `database: str | None`, `port: int = 5433`
- **Query**: `SELECT * FROM yb_servers()`
- **Result shape** (mirrors the other tools' `{"source": "yugabytedb", "available": true, ...}` envelope):

```python
{
    "source": "yugabytedb",
    "available": True,
    "node_count": len(nodes),
    "nodes": [
        {
            "host": ...,
            "port": ...,
            "cloud": ...,
            "region": ...,
            "zone": ...,
            "node_type": ...,          # "primary" vs "read_replica" — NOT master vs tserver;
                                        # yb_servers() only ever lists YB-TServers, never masters
            "num_connections": ...,
        },
        ...
    ],
    "connected_node": ...,             # inet_server_addr() (or matching yb_servers() row) —
                                        # see §3's node-identity-stamping note
    "note": (
        "Reflects live YB-TServer nodes visible to yb_servers(). Tablet-level "
        "replication lag and leader/follower status are not observable over a "
        "SQL connection and are out of scope for this integration."
    ),
}
```

- **Failure handling**: same `tool_unavailable("yugabytedb", str(err))` pattern. If
  `yb_servers()` does not exist on the connected server (e.g. someone points this
  integration at a vanilla PostgreSQL instance by mistake), the query raises
  `psycopg2.errors.UndefinedFunction`; the existing `try/except Exception` +
  `report_validation_failure` + `tool_unavailable` wrapper already handles this generically
  — no special-case code needed, but the resulting error message should be readable enough
  that "this doesn't look like a YugabyteDB server" is inferable. Consider (implementation
  detail, not required) a version-string check reusing whatever `validate_yugabytedb_config`
  finds for `version()`, to give a clearer "connected server does not appear to be
  YugabyteDB" note.

### 3.5 `get_yugabytedb_slow_queries` — ships in v1, mirrors the PostgreSQL slow-queries tool

`pg_stat_statements` is a first-class, drop-in-compatible extension on YugabyteDB — same
name and largely the same column set as PostgreSQL — and it is commonly enabled by default
via `shared_preload_libraries` in YugabyteDB's default YSQL configuration (unlike vanilla
PostgreSQL, where it must be opted into). This is a strong enough precedent to ship as a
fifth v1 tool rather than deferred follow-up work.

- **Description**: "Retrieve slow YugabyteDB queries from `pg_stat_statements` extension,
  ranked by mean execution time."
- **input_schema**: `host: str` (injected), `database: str | None`, `threshold_ms: int =
  1000`, `port: int = 5433`
- **Query and extension-availability pattern**: mirror
  `integrations/postgresql/__init__.py:get_slow_queries` (lines 484 onward) exactly,
  including its "check first, degrade gracefully" shape:
  1. Check `SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'` before running
     the real query.
  2. If the extension is not installed, return `{"source": "yugabytedb", "available":
     True, "extension_available": False, "note": "...", "queries": []}` — **do not** raise
     or return `tool_unavailable`; a missing extension is a normal, expected state to
     surface gracefully, not a connection failure.
  3. If installed, run the same `queryid` / `left(query, 500)` / `calls` /
     `total_exec_time` / `mean_exec_time` / `min_exec_time` / `max_exec_time` /
     `stddev_exec_time` / `rows` / cache-hit-percent query, filtered on
     `mean_exec_time >= %s`, ordered by `mean_exec_time DESC`, capped at
     `config.max_results`.
- **Tool wrapper**: mirror
  `integrations/postgresql/tools/postgresql_slow_queries_tool/__init__.py` file-for-file —
  same `PostgreSQLSlowQueriesInput`/`Output` → `YugabyteDBSlowQueriesInput`/`Output` model
  shapes, same `call_db_tool_with_default_db_warning(default_db_name="yugabyte", ...)`
  wrapper call, `source_id="yugabytedb_pg_stat_statements"`.
- **Verify during implementation, not blocking v1**: column parity against the exact target
  PostgreSQL-compat version — `pg_stat_statements`'s column set has changed across PG major
  versions, and YSQL pins to PG 11 compatibility as of most current releases, which is a
  smaller column set than a recent vanilla PostgreSQL. If a column is missing on the target
  cluster, the extension-availability check still protects the tool (it degrades to
  `extension_available: false` rather than raising) but the query itself may need a
  version-conditional column list — flag this as a follow-up if discovered, not a blocker
  for the first PR.

---

## 4. File-by-file plan

One-for-one mirror of the `integrations/postgresql/` file list, plus every touchpoint file.

### 4.1 New files

| File | Mirrors | Notes |
| --- | --- | --- |
| `integrations/yugabytedb/__init__.py` | `integrations/postgresql/__init__.py` | `YugabyteDBConfig`, `build_yugabytedb_config`, `yugabytedb_config_from_env`, `resolve_yugabytedb_config`, `_get_connection`, `validate_yugabytedb_config`, `YugabyteDBValidationResult`, `yugabytedb_is_available`, `yugabytedb_extract_params`, `get_server_status`, `get_current_queries`, `get_table_stats`, `get_cluster_status`, `get_slow_queries`, `classify`. §3 scopes out `get_replication_status`/`get_lock_status` entirely — see §4.3 for why. |
| `integrations/yugabytedb/setup.py` | `integrations/postgresql/setup.py` | `YUGABYTEDB_SETUP = IntegrationSetupSpec(service="yugabytedb", fields=(...), verify=verify_yugabytedb)`. Fields: `host`, `database`, `port` (default `"5433"`), `username` (default `DEFAULT_YUGABYTEDB_USER`), `password` (secret, not required), `ssl_mode` (default `DEFAULT_YUGABYTEDB_SSL_MODE`) |
| `integrations/yugabytedb/verifier.py` | `integrations/postgresql/verifier.py` | `verify_yugabytedb = register_validation_verifier("yugabytedb", build_config=build_yugabytedb_config, validate_config=validate_yugabytedb_config)` (helper lives at `integrations/verification/validation.py:register_validation_verifier`) |
| `integrations/yugabytedb/tools/__init__.py` | `integrations/postgresql/tools/__init__.py` | discovery marker package |
| `integrations/yugabytedb/tools/yugabytedb_server_status_tool/__init__.py` | `integrations/postgresql/tools/postgresql_server_status_tool/__init__.py` | §3.1 |
| `integrations/yugabytedb/tools/yugabytedb_current_queries_tool/__init__.py` | `integrations/postgresql/tools/postgresql_current_queries_tool/__init__.py` | §3.2 |
| `integrations/yugabytedb/tools/yugabytedb_table_stats_tool/__init__.py` | `integrations/postgresql/tools/postgresql_table_stats_tool/__init__.py` | §3.3 |
| `integrations/yugabytedb/tools/yugabytedb_cluster_status_tool/__init__.py` | *(no PostgreSQL equivalent)* | §3.4 |
| `integrations/yugabytedb/tools/yugabytedb_slow_queries_tool/__init__.py` | `integrations/postgresql/tools/postgresql_slow_queries_tool/__init__.py` | §3.5 |
| `config/constants/yugabytedb.py` | `config/constants/postgresql.py` | §2.2 |
| `docs/yugabytedb.mdx` | `docs/postgresql.mdx` | §7 |

### 4.2 Modified touchpoint files

**`integrations/registry.py`** — add an `IntegrationSpec` entry next to `postgresql` (lines
109–124 today); pick unused `setup_order`/`verify_order` values by scanning the existing
table (do not reuse `19`/`13`, which `postgresql` already owns):

```python
IntegrationSpec(
    service="yugabytedb",
    aliases=("yugabyte", "ysql"),
    has_verifier=True,
    direct_effective=True,
    setup_order=<next free int>,
    verify_order=<next free int>,
),
```

**`integrations/effective_models.py`** — add one field to `EffectiveIntegrations` (line 52
today, alongside `postgresql: EffectiveIntegrationEntry | None = None`):

```python
yugabytedb: EffectiveIntegrationEntry | None = None
```

**`integrations/_catalog_impl.py`** — three touchpoints, mirroring the `postgresql` blocks
exactly:

1. Import block (~line 121, alongside the `from config.constants.postgresql import (...)`
   block):
   ```python
   from config.constants.yugabytedb import (
       YUGABYTEDB_DATABASE_ENV,
       YUGABYTEDB_HOST_ENV,
       YUGABYTEDB_PASSWORD_ENV,
       YUGABYTEDB_PORT_ENV,
       YUGABYTEDB_SSL_MODE_ENV,
       YUGABYTEDB_USERNAME_ENV,
   )
   ```
2. Classifier imports + registration (~lines 243–244 and ~418, alongside
   `from integrations.postgresql import build_postgresql_config` /
   `from integrations.postgresql import classify as _classify_postgresql` and the
   `"postgresql": _classify_postgresql,` entry in the classifier dispatch dict):
   ```python
   from integrations.yugabytedb import build_yugabytedb_config
   from integrations.yugabytedb import classify as _classify_yugabytedb
   ...
   "yugabytedb": _classify_yugabytedb,
   ```
3. Env-loader block (~lines 866–884 today, directly mirroring the `postgresql_host` /
   `postgresql_database` block):
   ```python
   yugabytedb_host = os.getenv(YUGABYTEDB_HOST_ENV, "").strip()
   yugabytedb_database = os.getenv(YUGABYTEDB_DATABASE_ENV, "").strip()
   if yugabytedb_host and yugabytedb_database:
       yugabytedb_config = build_yugabytedb_config(
           {
               "host": yugabytedb_host,
               "port": int(_yb_port)
               if (_yb_port := os.getenv(YUGABYTEDB_PORT_ENV, "").strip()) and _yb_port.isdigit()
               else 5433,
               "database": yugabytedb_database,
               "username": os.getenv(YUGABYTEDB_USERNAME_ENV, "yugabyte").strip() or "yugabyte",
               "password": resolve_env_credential(YUGABYTEDB_PASSWORD_ENV),
               "ssl_mode": os.getenv(YUGABYTEDB_SSL_MODE_ENV, "prefer").strip() or "prefer",
           }
       )
       integrations.append(
           _active_env_record(
               "yugabytedb",
               yugabytedb_config.model_dump(exclude={"integration_id"}),
           )
       )
   ```
   Note `resolve_env_credential(YUGABYTEDB_PASSWORD_ENV)` — not bare `os.getenv` — per the
   credential-resolution contract (§5).

**`integrations/cli.py`** — two touchpoints mirroring `postgresql`:

1. `_setup_yugabytedb()` function (~line 633, alongside `_setup_postgresql`):
   ```python
   def _setup_yugabytedb() -> None:
       from integrations.yugabytedb.setup import YUGABYTEDB_SETUP

       _run_spec_setup(YUGABYTEDB_SETUP)
   ```
2. Dispatch table entry (~line 733, alongside `"postgresql": _setup_postgresql,`):
   ```python
   "yugabytedb": _setup_yugabytedb,
   ```

**`integrations/alert_source_catalog.py`** — two touchpoints in the two tables:

1. `_ROUTING_TABLE` (line 47, alongside `"postgresql": routing(("postgresql",), ("postgresql",)),`):
   ```python
   "yugabytedb": routing(("yugabytedb",), ("yugabytedb",)),
   ```
2. `_ALIASES_TABLE` (line 99, alongside `"postgresql": ("postgres", "postgresql", "psql", *DB_KEYWORDS),`):
   ```python
   "yugabytedb": ("yugabytedb", "yugabyte", "ysql", *DB_KEYWORDS),
   ```

**`core/domain/diagnosis/alignment.py`** — add `"yugabytedb"` and `"yugabyte"` to the
`_GROUP_SIGNALS[GROUP_DATABASE]` keyword tuple (lines 17–29 today):

```python
_GROUP_SIGNALS: dict[str, tuple[str, ...]] = {
    GROUP_DATABASE: (
        "postgres",
        "postgresql",
        "mysql",
        "mariadb",
        "yugabytedb",
        "yugabyte",
        "redis",
        "connection pool",
        "max_connections",
        "replication lag",
        "slow query",
        "sql database",
    ),
    ...
```

This tuple feeds `detect_category_text_mismatch` / `apply_category_alignment_adjustments`,
which catch a root-cause category/text mismatch during investigation reporting —
`mariadb`/`postgres`/`postgresql` are already present, confirming this is a real,
maintained per-vendor list, not dead code. `AGENTS.md`'s "Changing the investigation
pipeline" section names `core/domain/` as the home for "alert source mapping, tool
planning, category alignment, correlation scoring," and this file is exactly that. (By
contrast, `core/domain/alerts/alert_source.py` and
`tools/investigation/stages/gather_evidence/prompt.py` — the two other files
`docs/adding-tools-and-integrations.md` §3 calls out for "investigation wiring" — do not
currently reference `postgresql` at all, so no change is needed there.)

**`docs/docs.json`** — add `"yugabytedb"` to the `"Data and workflow systems"` group's
`pages` array (`navigation.tabs[2].groups[5]`). Confirmed live and alphabetically sorted:
`airflow, azure-sql, clickhouse, dagster, elasticsearch, kafka, mariadb, mongodb,
mongodb-atlas, mysql, openclaw, opensearch, postgresql, prefect, rabbitmq, rds, redis,
snowflake, supabase, temporal` — insert `"yugabytedb"` as the new last entry, after
`"temporal"`.

**`.env.example`** — add the six `YUGABYTEDB_*` keys with the same comment style as the
existing `# PostgreSQL` block (confirmed at lines 412–418: `POSTGRESQL_HOST`, `_PORT=5432`,
`_DATABASE`, `_USERNAME=postgres`, `_PASSWORD`, `_SSL_MODE=prefer`) — mirror this shape
exactly, substituting the `5433`/`yugabyte` defaults from §2.1.

### 4.3 Explicitly out of scope for the first implementation

Do **not** port these PostgreSQL tools 1:1, and say so in the PR description rather than
silently omitting them:

- `get_postgresql_replication_status` → no direct port. Superseded by (and folded into
  the intent of) `get_yugabytedb_cluster_status`'s node listing; true replication-lag
  reporting is out of scope per §3.4.
- `get_postgresql_lock_status` (`pg_locks` blocking-query join) → **not planned, in v1 or
  as future work — do not port `pg_locks` as-is even in a later PR.** YugabyteDB's lock
  manager is fundamentally distributed: locks for a single distributed transaction can be
  held across nodes in DocDB, not in one backend's local lock table, so the PostgreSQL
  tool's self-join query (matching `blocking_locks` to `blocked_locks` on
  `relation`/`page`/`tuple`/`virtualxid`/`transactionid`) does not reliably reconstruct
  cross-node blocking chains — `pg_locks` on YSQL mostly reflects local PG-layer locks
  (relation/advisory locks), not the distributed row-level locks DocDB actually enforces. A
  straight port would run without error but **silently under-report cross-node blocking**,
  which is worse than omitting the tool entirely. The real equivalent is YugabyteDB's
  purpose-built `yb_lock_status()` SQL function (shipped alongside the "Wait-on-Conflict"
  concurrency control feature, roughly the 2.18–2.20 release era). If a lock-status tool is
  ever wanted, it must be built as a new, separately-ticketed `get_yugabytedb_lock_status`
  on `yb_lock_status()` — never as a port of the PostgreSQL `pg_locks` self-join.

(`get_postgresql_slow_queries` is **not** on this out-of-scope list — see §3.5, which ships
it as a fifth v1 tool.)

---

## 5. Credential resolution

Per `docs/adding-tools-and-integrations.md` §2 "Credential resolution" contract table:

| Field | Keyring-eligible? | Write path | Read path |
| --- | --- | --- | --- |
| `host` | No | store / `.env` | store → plain `os.getenv(YUGABYTEDB_HOST_ENV)` |
| `port` | No | store / `.env` | store → plain `os.getenv(YUGABYTEDB_PORT_ENV)` |
| `database` | No | store / `.env` | store → plain `os.getenv(YUGABYTEDB_DATABASE_ENV)` |
| `username` | No | store / `.env` | store → plain `os.getenv(YUGABYTEDB_USERNAME_ENV)` |
| `ssl_mode` | No | store / `.env` | store → plain `os.getenv(YUGABYTEDB_SSL_MODE_ENV)` |
| `password` | **Yes** (`*_PASSWORD` pattern) | store, or keyring via `sync_env_secret` | `resolve_env_credential(YUGABYTEDB_PASSWORD_ENV)` (env first, then keyring) — **never** bare `os.getenv` |

This is identical to the PostgreSQL contract — `POSTGRESQL_PASSWORD` is the only
keyring-eligible field there too (confirmed at `integrations/_catalog_impl.py:877`:
`"password": resolve_env_credential(POSTGRESQL_PASSWORD_ENV)`).

**Mechanism, corrected**: `SetupField(secret=True)` on the `password` field in
`YUGABYTEDB_SETUP` does **not**, by itself, route the wizard write to the keyring —
`SetupField.secret` only controls input masking (whether the collection surface masks the
field while it is typed; `integrations/setup_flow.py` lines 128–129). The actual
keyring-vs-`.env` routing happens in `_persist_env`
(`integrations/setup_flow.py:288-292`), which calls
`is_sensitive_env_key(field.env_var)` — **independent of `field.secret`** — to decide
between `sync_env_secret` and a plain `.env` write. `is_sensitive_env_key`
(`config/env_file.py:95-103`) pattern-matches on the env var name's terminal token
(`password`, `token`, `key`, `secret`, …). `YUGABYTEDB_PASSWORD` is routed to the keyring
because its name ends in `_PASSWORD`, not because `secret=True` is set on the
`SetupField`. The `password` field should still set `secret=True` in `YUGABYTEDB_SETUP`
(for correct input masking during the wizard prompt), and the conclusion above — password
is the only keyring-eligible field — is unchanged; only the stated *reason* was wrong. No
special handling is needed beyond copying the `postgresql` setup spec shape.

---

## 6. Test plan

Mirrors `tests/integrations/test_postgresql.py` and `tests/tools/test_postgresql_*.py`
found via `find tests -iname "*postgresql*"` (also cross-checked against the parallel
`tests/integrations/test_mariadb.py` / `test_mariadb_integration.py` pair — this repo
sometimes splits integration tests into a `test_<name>.py` config-only file and a
`test_<name>_integration.py` file for classify/env-loader coverage; follow whichever
convention `test_postgresql.py` alone establishes since PostgreSQL doesn't split them).

### 6.1 `tests/integrations/test_yugabytedb.py`

Mirror `tests/integrations/test_postgresql.py` class-for-class:

- `TestYugabyteDBConfig`
  - `test_defaults` — assert `port == 5433`, `username == "yugabyte"`, `ssl_mode == "prefer"`, `timeout_seconds == 10.0`, `max_results == 50`
  - `test_is_configured_with_host_and_database`
  - `test_is_configured_without_host`
  - `test_is_configured_without_database`
  - `test_is_configured_without_host_and_database`
  - `test_normalize_host_strips_whitespace`
  - `test_normalize_empty_host`
  - `test_normalize_database_strips_whitespace`
  - `test_normalize_empty_database`
  - `test_normalize_username_default` — empty username falls back to `"yugabyte"`
  - `test_normalize_ssl_mode_default`
  - `test_custom_values`
- `TestBuildYugabyteDBConfig`
  - `test_from_dict`, `test_from_none`, `test_from_empty_dict`
- `TestYugabyteDBConfigFromEnv`
  - `test_returns_none_without_host`
  - `test_returns_none_without_database`
  - `test_returns_config_with_host_and_database` — set all six `YUGABYTEDB_*` env vars,
    assert round-trip
- `TestYugabyteDBValidationResult`
  - `test_ok_result`, `test_error_result`

### 6.2 `tests/tools/test_yugabytedb_server_status_tool.py`

Mirror `tests/tools/test_postgresql_server_status_tool.py`:

- `TestYugabyteDBServerStatusToolContract(BaseToolContract)` — `get_tool_under_test`
  returns `get_yugabytedb_server_status.__opensre_registered_tool__`
- `test_metadata` — `rt.name == "get_yugabytedb_server_status"`, `rt.source == "yugabytedb"`
- `test_run_happy_path` — patch `integrations.yugabytedb.tools.yugabytedb_server_status_tool.get_server_status`
  with a fake result dict, assert passthrough fields
- `test_run_error_propagated` — patch to return `{"source": "yugabytedb", "available": False, "error": "connection timeout"}`, assert `"error" in result`

### 6.3 `tests/tools/test_yugabytedb_current_queries_tool.py` and `test_yugabytedb_table_stats_tool.py`

Same contract/happy-path/error-path structure as §6.2, mirroring
`test_postgresql_current_queries_tool.py` and `test_postgresql_table_stats_tool.py`
respectively — one connection-refused-style failure test and one auth-failure-style
failure test per tool (patch the underlying `get_*` function to return
`tool_unavailable("yugabytedb", "...")`-shaped dicts for each), per the "each tool's
failure path — connection refused / auth failure" requirement.

### 6.3a `tests/tools/test_yugabytedb_slow_queries_tool.py`

Mirror `tests/tools/test_postgresql_slow_queries_tool.py` (§3.5's tool). Same
contract/happy-path/error-path structure as §6.2, plus one test specific to the
extension-availability check:

- `test_metadata` — `rt.name == "get_yugabytedb_slow_queries"`, `rt.source == "yugabytedb"`
- `test_run_happy_path` — patch `get_slow_queries` with a fake queries list, assert
  passthrough
- `test_run_extension_not_available` — patch `get_slow_queries` to return
  `{"source": "yugabytedb", "available": True, "extension_available": False, "queries":
  []}`, assert the tool surfaces `extension_available: False` gracefully rather than
  treating it as an error
- `test_run_error_propagated` — generic connection-failure path, same shape as §6.2/6.3

### 6.4 `tests/tools/test_yugabytedb_cluster_status_tool.py` — new, needs a fixture

This tool has no PostgreSQL precedent, so it needs its own fixture-based test proving the
`yb_servers()` output shape is parsed correctly:

- `test_metadata` — `rt.name == "get_yugabytedb_cluster_status"`, `rt.source == "yugabytedb"`
- `test_run_happy_path_with_yb_servers_fixture` — patch
  `integrations.yugabytedb.tools.yugabytedb_cluster_status_tool.get_cluster_status` (or,
  if testing one layer deeper, patch `_get_connection`/cursor to return a realistic
  `yb_servers()` row tuple) with a **fixture** representing a real 3-node RF=3 cluster
  response — e.g. three rows with distinct `host`/`region`/`zone` values across
  `us-east-1a`/`us-east-1b`/`us-east-1c` — and assert `node_count == 3` and each node's
  `region`/`zone` round-trips correctly. Mark this fixture explicitly in a comment as
  "shape believed correct from YugabyteDB docs, not verified against a live cluster" until
  an implementer confirms it against `yb_servers()` on an actual YugabyteDB instance (§8).
- `test_run_undefined_function_error` — simulate `psycopg2.errors.UndefinedFunction` (the
  "pointed at a non-Yugabyte Postgres server" case) and assert the tool returns a
  `tool_unavailable` envelope rather than raising.
- `test_run_error_propagated` — generic connection-failure path, same shape as §6.2/6.3.

### 6.5 `tests/e2e/yugabytedb/` (optional for first PR, required before merge per §5 final gate)

Mirror `tests/e2e/postgresql/test_postgresql_e2e.py` and `postgresql_alert.json` — an
alert-routing scenario proving `alert_source_catalog.py`'s `yugabytedb` routing entry
(§4.2) causes the investigation pipeline to seed the `yugabytedb` tool source. Needed for
the "Final gate (new integrations)" requirement in `docs/adding-tools-and-integrations.md`
(§1, "E2E or synthetic test added").

### 6.6 Registry/discovery test — no new test file needed

Per the definition-of-done checklist ("A registry/discovery test proves the tool is
visible on the expected surface(s)"): this coverage already exists generically and needs
no new test file. `tests/integrations/test_registry.py`
(`test_registry_declares_each_service_once`,
`test_registry_supported_lists_are_derived_from_specs`, `test_every_setup_spec_has_handler`)
and `tests/integrations/test_registry_invariants.py` (`test_setup_orders_are_unique`,
`test_verify_orders_are_unique`) are generic, structural tests driven entirely off
`INTEGRATION_SPECS` — a correctly-added `yugabytedb` `IntegrationSpec` (§4.2) passes them
automatically. `tests/tools/test_registry.py` is likewise a generic tool-discovery suite;
its one hardcoded per-tool-name list (`_V2_TOOL_CONTRACT_NAMES`) is a closed, opt-in legacy
contract set that includes only `get_postgresql_slow_queries`, not every postgresql tool —
the new yugabytedb tools (including `get_yugabytedb_slow_queries`) are not expected to join
it. No action item for the implementer here beyond landing the registry entry from §4.2.

### 6.7 Test scope note for implementers

Per this repo's subagent dispatch rules: run only the new test files during development —
`pytest tests/integrations/test_yugabytedb.py tests/tools/test_yugabytedb_*.py -q` — the
full suite (`make test` / `pytest tests/unit/ -q`) is the merge gate, run once by the user
per the "No Work Without a Ticket" global instruction's test-gate step.

---

## 7. Docs

### 7.1 `docs/yugabytedb.mdx` — outline mirroring `docs/postgresql.mdx`

```
---
title: "YugabyteDB"
description: "Connect YugabyteDB so OpenSRE can diagnose database issues during investigations"
---

(intro paragraph — YSQL diagnostics: server health, current queries, table stats, cluster
topology)

## Prerequisites
- YugabyteDB version requirement — no known hard minimum version for the core tools; if a
  future `yb_lock_status()`-based lock tool is ever added (not planned — see §4.3), that
  would require a 2.18+ era release
- Network access from OpenSRE to the YSQL port (default 5433)
- A read-only YSQL user with access to system views
- **Point `host` at a specific node, not a load balancer/VIP, if possible** — see the
  node-pinning note under "Investigation tools" below

## Setup
### Option 1: Interactive CLI (opensre integrations setup)
### Option 2: Environment variables
  YUGABYTEDB_HOST / YUGABYTEDB_PORT (default 5433) / YUGABYTEDB_DATABASE /
  YUGABYTEDB_USERNAME (default yugabyte) / YUGABYTEDB_PASSWORD / YUGABYTEDB_SSL_MODE
### Option 3: Persistent store (~/.opensre/integrations.json example)

## Creating a read-only user
  GRANT pg_monitor TO <readonly_user>; — YSQL's predefined roles (pg_monitor,
  pg_read_all_stats, pg_read_all_settings) are inherited intact from the PostgreSQL 11
  fork YSQL is built on and grant the same pg_stat_activity/pg_stat_user_tables
  visibility as PostgreSQL

## Investigation tools
### Server status  — note the per-node-not-cluster-wide caveat from §3.1 (this also covers
  max_connections, which comes from pg_settings but is equally per-node)
### Current queries — note the per-node caveat from §3.2
### Table statistics — note the LSM/dead-tuple and local-vs-cluster-size caveats from §3.3
### Cluster status  — explain yb_servers(), and explicitly state that tablet-level
  replication lag is NOT covered (link back to why, in plain language: that data lives in
  yb-admin/master UI, which this integration does not have access to). Also note
  yb_servers() lists YB-TServer nodes only — never masters — and that node_type
  distinguishes primary-cluster vs. read-replica nodes, not master vs. tserver.
### Slow queries    — pg_stat_statements-based (§3.5); note it degrades gracefully with
  extension_available: false if the extension isn't installed/enabled on the target cluster
### Node pinning     — a short standalone note: every tool response includes the node it
  connected to (connected_node, from inet_server_addr()). If YUGABYTEDB_HOST points at a
  load-balancer/VIP rather than a specific node IP, separate tool calls within the same
  investigation can land on different nodes and return mutually inconsistent per-node
  slices (see §3.1's per-node caveat) — use connected_node to check whether two calls in
  one investigation actually saw the same node.
### Lock status      — explicitly not offered by this integration. pg_locks is not ported
  (its self-join assumes single-node lock semantics that don't hold on YugabyteDB's
  distributed lock manager); YugabyteDB's yb_lock_status() would be the correct primitive
  if this is ever revisited, but no such tool exists today.

## Verify
  opensre integrations verify yugabytedb

## Troubleshooting
  (mirror postgresql's table: connection refused, auth failed, SSL error, permission
  denied, plus two YugabyteDB-specific rows: "yb_servers() returns 'function does not
  exist'" → target is not actually a YugabyteDB server; and "results look inconsistent
  between tool calls in the same investigation" → host is likely pointed at a load
  balancer/VIP, check each result's connected_node field)

## Security best practices
  (mirror postgresql's section; also recommend the read-only role set
  default_transaction_read_only = on as defense in depth, since this integration only
  ever issues SELECT statements)
```

### 7.2 `docs/docs.json` registration

Add `"yugabytedb"` to the `"Data and workflow systems"` group `pages` array — see §4.2.

---

## 8. Risks / open questions for YugabyteDB expert review

The original 11 items below were this document's pre-review open questions. The Yugabyte
Domain Expert Review (below) has since resolved most of them with a stated confidence
level; those corrections are already folded into §2–§7 above. This section is kept as a
record of what was asked and what is still genuinely open — do not re-litigate the
resolved items without new evidence, but the two still-open items (3 and 9) and the
residual verification in items 1 and 5 still need a real-cluster check before merge.

1. **`version()` string shape — RESOLVED, regex now specified in §2.1.** YSQL's `version()`
   returns `PostgreSQL 11.2-YB-2.20.0.0-b0 on ...`; PostgreSQL's `.split()[1]` would yield
   the mangled compound token `11.2-YB-2.20.0.0-b0`, not a clean version. §2.1 now
   specifies a dedicated regex (`PostgreSQL ([\d.]+)-YB-([\d.]+\.\d+)-b(\d+)`) instead.
   Residual verification: confirm the live string against a real cluster before finalizing
   the regex — the `-b<N>` build suffix and platform string can vary by build.

2. **Per-node vs. cluster-wide semantics of `pg_stat_database`, `pg_stat_activity`,
   `pg_stat_user_tables` — RESOLVED, CORRECT, high confidence.** Every YB-TServer runs its
   own local backend processes; these views are per-node with no cluster-wide
   aggregation and no `yb_`-prefixed aggregate replacement. The "per-node caveat" language
   throughout §3 and §7.1 is accurate and must ship as written, not softened.

3. **`pg_total_relation_size()` / `pg_relation_size()` semantics under RF>1 replication —
   STILL OPEN, lowest-confidence item in the whole plan.** Lean cluster-wide-across-tablets
   (not per-node) and likely per-copy (not multiplied by replication factor), but
   confidence is genuinely low. **Run the smoke test before publishing any size claim in
   `docs/yugabytedb.mdx`:** create a table on an RF=3 cluster, insert a known payload,
   compare `pg_total_relation_size()` against the YB-Master "Tables" HTTP UI's reported SST
   size for that table, and against that value × 3 — this tells you whether the SQL
   function is per-copy or RF-multiplied. (§3.3)

4. **`n_dead_tup`/`last_vacuum`/`last_autovacuum` validity — RESOLVED, CORRECT.** DocDB is
   an LSM tree with no PostgreSQL-style autovacuum; `n_dead_tup` is typically `0`,
   `last_vacuum`/`last_autovacuum`/`last_analyze` typically `NULL`. §3.3's "keep the
   fields for schema parity, add a `note`" approach is confirmed correct — do not remove
   the fields.

5. **`yb_servers()` column list and master/tserver distinction — RESOLVED, CORRECT with
   one fix already applied.** Column list (`host`, `port`, `num_connections`, `node_type`,
   `cloud`, `region`, `zone`, `public_ip`, `uuid`) matches documented behavior.
   `yb_servers()` **never lists YB-Master nodes — only YB-TServers**; `node_type`
   distinguishes primary-cluster vs. read-replica-cluster nodes, not master vs. tserver.
   §3.4's result-shape comment and description have been corrected accordingly. Residual
   verification: exact column list/types have changed across YugabyteDB versions, so
   still confirm against a real cluster during implementation; the §6.4 fixture stays
   marked unverified until then.

6. **`pg_stat_replication` on YSQL — RESOLVED, omit exactly as planned.** It is a
   vestigial PostgreSQL-compatibility view that returns zero rows on a standalone
   YugabyteDB cluster (replication is RAFT-based at the tablet/DocDB layer, not WAL
   streaming). §3.4's decision to omit the probe rather than run-and-document-empty is
   confirmed correct.

7. **`pg_stat_statements` — RESOLVED, ships in v1 as a fifth tool.** It is a first-class,
   drop-in-compatible extension on YugabyteDB, commonly enabled by default via
   `shared_preload_libraries` (unlike vanilla PostgreSQL). `get_yugabytedb_slow_queries`
   now ships in the same PR — see §3.5. Residual verification: confirm column parity
   against the target PostgreSQL-compat version (YSQL pins to PG 11 compatibility as of
   most current releases, a smaller column set than recent vanilla PostgreSQL); the
   extension-availability check in §3.5 protects the tool either way.

8. **`pg_locks` distributed-transaction semantics — RESOLVED, not planned at all.**
   YugabyteDB's lock manager is distributed; the PostgreSQL self-join
   (`relation`/`page`/`tuple`/`virtualxid`/`transactionid`) does not reliably reconstruct
   cross-node blocking chains and would silently under-report distributed blocking if
   ported as-is. §4.3 now states explicitly: `pg_locks` is not planned, in v1 or as future
   work; the correct primitive if this is ever revisited is `yb_lock_status()`, requiring a
   new, separately-ticketed tool, not a port of the PostgreSQL self-join.

9. **Default `ssl_mode` for managed YugabyteDB (Yugabyte Cloud / YugabyteDB Managed)
   deployments — STILL OPEN, no strong verdict possible without knowing the target user
   base.** YugabyteDB Managed generally expects `sslmode=verify-full` with the cluster's
   CA certificate, but `prefer` still connects there (opportunistic TLS negotiation).
   Decision: leave the *default* in `YugabyteDBConfig` at `"prefer"` (parity with
   PostgreSQL, don't guess a breaking change) while documenting the managed-cloud
   `verify-full` recommendation in `docs/yugabytedb.mdx`'s Setup section. (§2.1)

10. **Minimum supported YugabyteDB version — STILL OPEN, but scoped.** `yb_servers()` and
    the core `pg_stat_*` compatibility views have been stable for a long time, so a version
    floor is unlikely to be the risk for the four core tools; the risk is
    version-*dependent behavior* (item 5's `node_type` semantics, item 1's exact
    `version()` string, and `yb_lock_status()` availability if a lock tool is ever added,
    which needs a 2.18+ era release). `docs/yugabytedb.mdx`'s Prerequisites section (§7.1)
    now states this explicitly rather than asserting an unverified number.

11. **`pg_monitor`-equivalent role for the read-only setup user — RESOLVED, works
    identically to PostgreSQL.** YSQL's predefined roles (`pg_monitor`,
    `pg_read_all_stats`, `pg_read_all_settings`) are inherited intact from the PostgreSQL
    11 fork YSQL is built on. `GRANT pg_monitor TO <readonly_user>;` can be published in
    `docs/yugabytedb.mdx` verbatim, as §7.1 now does.

Only items 3 and 9 (plus the residual verification noted in items 1 and 5) remain
genuinely open. The plan intentionally scopes `get_yugabytedb_cluster_status` to only the
`yb_servers()` query and keeps `pg_locks` out of scope entirely, rather than shipping
unverified SQL against a
production database diagnostic surface.

---

## Yugabyte Domain Expert Review

Reviewed against general knowledge of YugabyteDB's YSQL/DocDB architecture, not against a
live cluster. Every verdict below is stated with an explicit confidence level; anything
marked UNCERTAIN still needs the real-cluster smoke test this document already calls for —
this review does not replace that step, it narrows what the smoke test needs to check.

### A. Verdicts on the 11 flagged questions (§8)

**1. `version()` string shape — UNCERTAIN, but here is the known shape to design against.**
YSQL's `SELECT version()` returns something of the form:
`PostgreSQL 11.2-YB-2.20.0.0-b0 on x86_64-pc-linux-gnu, compiled by gcc ...`. PostgreSQL's
`version_info.split()[1]` extraction will **not** raise, but it will yield the compound
token `11.2-YB-2.20.0.0-b0`, not a clean version number — the PG-compat version and the YB
build version are concatenated with `-YB-` inside what PostgreSQL treats as a single
whitespace-delimited word. `validate_yugabytedb_config` needs its own parse, e.g. a regex
like `PostgreSQL ([\d.]+)-YB-([\d.]+\.\d+)-b(\d+)` to pull out PG-compat version, YB
version, and build number as three separate fields. This format has been stable since at
least the 2.x series but the exact `-b<N>` build suffix and platform string can vary by
build — confirm the live string before hard-coding the regex.

**2. Per-node vs. cluster-wide semantics of `pg_stat_database`/`pg_stat_activity`/
`pg_stat_user_tables` — CORRECT, high confidence.** Every YB-TServer runs its own embedded
PostgreSQL-derived query layer with its own local backend processes, and these views are
populated from that local process's in-memory stats collector exactly as in vanilla
PostgreSQL — there is no cluster-wide aggregation step. A connection to node A's
`pg_stat_activity` will never show a query running on node B. There is no
`yb_`-prefixed cluster-aggregate replacement for these three views; cluster-wide
visibility for connections/queries/table activity requires either querying every node
individually (fan-out, which this YSQL-only integration cannot do without a node list —
though `yb_servers()` from §3.4 could seed that fan-out in a *future* PR) or using the
YB-TServer Prometheus metrics endpoint (`:9000/metrics`), which is out of scope here. The
plan's "per-node, not cluster-wide" caveat is accurate and must ship in the tool
descriptions and `docs/yugabytedb.mdx` exactly as planned — do not soften it.

**3. `pg_total_relation_size()` / `pg_relation_size()` under RF>1 — UNCERTAIN, lean
cluster-wide-per-copy.** YSQL reimplements these functions to sum SST file sizes across
*all* tablets of the relation (tablets are typically spread across multiple YB-TServers),
not just tablets whose leader happens to be the node the connection landed on — so this
is **not** simply "whatever fraction of the table lives on this one node," unlike
`pg_stat_user_tables`. Best available recollection is that the reported size reflects one
copy of the data (i.e. not multiplied by replication factor) rather than the sum of all RF
replicas' storage — but this has evolved across YB releases and confidence here is
genuinely low. **Real-cluster smoke test**: create a table on an RF=3 cluster, insert a
known payload, compare `pg_total_relation_size()` against (a) the sum of SST file sizes
reported by the YB-Master "Tables" HTTP UI page for that table, and (b) that value × 3. If
the SQL function matches (a) and not (b), size is per-copy (RF-independent) as suspected;
if it matches (b), the doc's "table size" language needs an explicit RF caveat.

**4. `n_dead_tup`/`last_vacuum`/`last_autovacuum` — CORRECT.** YugabyteDB's DocDB storage
is an LSM tree (RocksDB-based), not PostgreSQL's heap, so it has no MVCC dead-tuple
bloat in the PostgreSQL sense and does not run PostgreSQL's autovacuum daemon against
these tables. `n_dead_tup` is typically `0`, and `last_vacuum`/`last_autovacuum`/
`last_analyze` are typically `NULL` (compaction happens automatically as a DocDB/RocksDB
background process not reflected in these catalog columns). One nuance worth noting: newer
YugabyteDB releases (2.18+ era) added a PostgreSQL-autovacuum-*compatible* wrapper for
certain TTL/retention workflows, but it still doesn't populate these particular columns
the way vanilla PostgreSQL's autovacuum does — the plan's "keep the fields for schema
parity, add a `note`" approach is the right call, don't remove the fields.

**5. `yb_servers()` column list — CORRECT (with one refinement).** The documented columns
match what's proposed: `host`, `port`, `num_connections`, `node_type`, `cloud`, `region`,
`zone`, `public_ip`, `uuid`. Refinement on `node_type`: it does **not** distinguish
YB-Master from YB-TServer — `yb_servers()` only ever lists YB-TServers (the nodes that can
actually accept YSQL connections; masters never do). `node_type` instead distinguishes
"primary cluster" nodes from "read replica cluster" nodes in a multi-region read-replica
deployment (values like `primary`/`read_replica`), which is a different axis than the plan's
comment ("verify: master vs tserver distinction, if any") implies. Update that comment in
§3.4's result-shape code sample so a reader doesn't go looking for a master/tserver flag
that isn't there.

**6. `pg_stat_replication` on YSQL — CORRECT, recommend omit exactly as planned.**
YugabyteDB's intra-cluster replication is RAFT-based at the tablet/DocDB layer, not
PostgreSQL WAL streaming, so `pg_stat_replication` is a vestigial compatibility view that
returns zero rows on a standalone YugabyteDB cluster. (It could theoretically return rows
in an xCluster/2DC replication setup depending on version, but that's a different
mechanism than what a diagnostic probe on the target cluster's own YSQL connection would
see.) The plan's decision to omit the probe rather than run-and-document-empty is correct
— a query that always returns nothing is dead weight, not a caveat-documented feature.

**7. `pg_stat_statements` — CORRECT that it exists, but the plan is more cautious than it
needs to be.** YugabyteDB supports `pg_stat_statements` as a first-class, drop-in-compatible
extension with the *same* name and largely the same column set as PostgreSQL, and it is
commonly enabled by default via `shared_preload_libraries` in YugabyteDB's default YSQL
configuration (unlike vanilla PostgreSQL, where it must be opted into). Given that, this is
very likely safe to port as a fifth tool in the *same* PR rather than deferred — recommend
downgrading this from "defer" to "port as-is, verify the extension is present via
`SHOW shared_preload_libraries` or a `pg_extension` check in `is_available`, and fail soft
with `tool_unavailable` if it isn't enabled on a given cluster." Do still verify column
parity against the exact target PostgreSQL-compat version, since `pg_stat_statements`'
column set has changed across PG major versions and YSQL pins to PG 11 compatibility as of
most current releases, which is a different (smaller) column set than a recent vanilla
PostgreSQL.

**8. `pg_locks` distributed-transaction semantics — CORRECT concern, and there is a better
tool than porting the query as-is.** YugabyteDB's lock manager is fundamentally different:
distributed transactions can hold row-level locks that live in DocDB across multiple
tablets/nodes, not in a single backend's local lock table, so PostgreSQL's
`pg_locks` self-join (matching `blocking_locks` to `blocked_locks` on
`relation`/`page`/`tuple`/`virtualxid`/`transactionid`) does not reliably reconstruct
cross-node blocking chains — `pg_locks` on YSQL mostly reflects local PG-layer
locks (relation/advisory locks), not the distributed row-level locks DocDB is actually
enforcing. YugabyteDB introduced a purpose-built `yb_lock_status()` SQL function
(shipped alongside the "Wait-on-Conflict" concurrency control feature, roughly the
2.18–2.20 release era) specifically because `pg_locks` was inadequate for this. Recommend
the ticketed follow-up build `get_yugabytedb_lock_status` on `yb_lock_status()` instead of
attempting to port the PostgreSQL `pg_locks` self-join — porting the self-join as-is would
likely produce a tool that runs without error but silently under-reports distributed
blocking, which is worse than omitting it. Verify `yb_lock_status()`'s availability and
signature against the target minimum supported version (ties to item 10) before
committing to it.

**9. Default `ssl_mode` for managed deployments — CORRECT to flag, no strong verdict
possible without knowing the target user base.** Self-hosted YugabyteDB clusters commonly
run without TLS in dev/test and with it in production (operator-controlled, `prefer` is a
reasonable non-breaking default either way). YugabyteDB Managed (Aeon) enforces TLS and
generally expects `sslmode=verify-full` with the cluster's CA certificate — `prefer` would
still connect there (it negotiates TLS opportunistically) but `verify-full` is what
Yugabyte's own managed-cloud connection strings document, so leaving the *default* at
`prefer` (matching PostgreSQL) while documenting the managed-cloud recommendation in
`docs/yugabytedb.mdx`'s Setup section is the safer choice — don't silently change the
default away from parity with the PostgreSQL integration on a guess.

**10. Minimum supported version — UNCERTAIN, no confident number to give.** `yb_servers()`
and the core `pg_stat_*` compatibility views have been stable for a long time (well before
any currently-supported YugabyteDB release), so version floor is unlikely to be the risk —
the risk is version-*dependent behavior* (item 5's `node_type` semantics, item 8's
`yb_lock_status()` availability, item 1's exact `version()` string). Recommend the
Prerequisites section state "no known hard minimum version for the core tools; the cluster
status tool's `yb_lock_status()`-based lock tool, if added later, requires a 2.18+ era
release" rather than asserting a number this document has no basis for.

**11. `pg_monitor`-equivalent role — CORRECT, works the same as PostgreSQL.** YSQL's
predefined roles (`pg_monitor`, `pg_read_all_stats`, `pg_read_all_settings`, etc.) are
inherited intact from the PostgreSQL 11 fork YSQL is built on and grant the same
`pg_stat_activity`/`pg_stat_user_tables` visibility. `GRANT pg_monitor TO <readonly_user>;`
can be published in `docs/yugabytedb.mdx` verbatim, same as the PostgreSQL doc.

### B. Direct answers to the six review questions

1. **`pg_stat_database`/`pg_stat_activity`/`pg_stat_user_tables` scope**: per-node, not
   cluster-wide (see A.2). `get_yugabytedb_server_status` and `get_yugabytedb_current_queries`
   give a single-node slice, not a cluster picture — the plan's caveat language is correct
   and load-bearing, not decorative.
2. **`pg_total_relation_size()` scope**: believed cluster-wide across all tablets of the
   relation (not per-node), and believed *not* multiplied by replication factor — but this
   is the lowest-confidence item in this review (A.3) and needs the smoke test described
   there before the docs assert it either way.
3. **Vacuum/dead-tuple columns**: effectively dead weight on YugabyteDB — `n_dead_tup`
   reads `0`, `last_vacuum`/`last_autovacuum` read `NULL`. Keep the fields for schema
   parity with the PostgreSQL tool plus a `note`, exactly as the plan proposes; do not
   present them as actionable maintenance signals.
4. **`yb_servers()` fitness for a topology tool over plain YSQL**: yes, it is the right
   and only function reachable this way — it's the documented, supported mechanism smart
   drivers use for topology discovery. It lists YB-TServers only (never masters), and its
   `node_type` column distinguishes primary-cluster vs. read-replica-cluster nodes, not
   master vs. tserver role — correct the code comment in §3.4 (A.5).
5. **Cut-scope calls**: `replication_status` cut — correct, nothing to port.
   `slow_queries` (`pg_stat_statements`) deferred — too cautious; recommend porting it in
   the first PR (A.7). `lock_status` (`pg_locks`) deferred — correct call, but the right
   fix is not "port `pg_locks` later," it's "build on `yb_lock_status()` instead" (A.8).
6. **Missed YSQL-specific gotchas** (see §C below) — the plan's design (fresh short-lived
   `psycopg2` connections per tool call) is largely fine, but the *hostname* given to each
   connection matters more than the plan credits, and there's no explicit read-only session
   guard.

### C. Gotchas the plan did not consider

- **Connecting through a load balancer/VIP/round-robin DNS makes the per-node caveat
  worse than described.** If `host` resolves to a load-balancer VIP or a round-robin DNS
  name in front of multiple YB-TServers (a common production topology, and exactly what
  YugabyteDB's own "smart drivers" are designed to abstract over), then two separate tool
  calls in the *same investigation* — e.g. `get_yugabytedb_server_status` followed by
  `get_yugabytedb_current_queries` — can silently land on two *different* nodes, each
  giving a coherent-looking but mutually inconsistent per-node slice. An investigator
  reading both outputs together could reasonably (and wrongly) treat them as describing
  one server. Recommend: have each tool's result include the connected node's identity
  (`SELECT inet_server_addr()` or the host from `yb_servers()` matching this connection)
  so evidence is stamped with which node it came from, and note in `docs/yugabytedb.mdx`
  that pointing `host` at a load balancer, rather than a specific node IP, produces
  results that vary call-to-call.
- **No read-only session guard.** The plan relies on the tools only issuing `SELECT`
  statements, but nothing sets `default_transaction_read_only = on` (or connects as a
  role restricted via `ALTER ROLE ... SET default_transaction_read_only = on`) at the
  session level. YSQL honors this GUC identically to PostgreSQL. Given this integration's
  entire premise is "read-only diagnostic queries," setting it explicitly after connect
  (or documenting it as a requirement on the recommended read-only role) is cheap defense
  in depth against a future tool accidentally issuing a write, and costs nothing on the
  YSQL side.
- **`max_connections` in `get_yugabytedb_server_status` is also per-node, not a cluster
  total** — same root cause as A.2, but easy to miss since it comes from `pg_settings`
  rather than `pg_stat_*`. Fold it into the same per-node caveat rather than treating it
  as a separate fact.
- **Catalog-cache staleness is a non-issue for this design, worth noting as a positive.**
  YSQL's YB-specific catalog-version mechanism can produce "Catalog Version Mismatch: A DDL
  occurred while processing this query. Try Again" errors on **long-lived** connections
  that straddle a concurrent DDL change elsewhere in the cluster. Because this design opens
  a fresh `psycopg2` connection per tool call and closes it immediately after, each call
  gets a clean catalog cache and this failure mode is structurally avoided — no action
  needed, but worth a one-line note in `docs/yugabytedb.mdx`'s troubleshooting table in
  case a user asks why long-lived connections elsewhere behave differently.
- **YugabyteDB's own smart driver (`yb_psycopg2`/YugabyteDB JDBC/Go smart drivers) is
  deliberately *not* what this integration should use, and the plan is right not to reach
  for it** — smart drivers do topology-aware connection load balancing across nodes, which
  is actively harmful for a diagnostic tool that wants a stable, identifiable node per
  call. Plain `psycopg2` pointed at a specific host is the correct choice; just make that
  reasoning explicit in `docs/yugabytedb.mdx` so a future contributor doesn't "upgrade" to
  the smart driver and reintroduce the load-balancing inconsistency described above.

### D. Summary — what should change before implementation

1. Fix the `version()` parser to expect the `PG-version-YB-yb-version-bN` compound format,
   not a bare version token (A.1).
2. Keep the per-node caveats in §3.1–3.3 exactly as written — they are correct, not just
   defensively worded (A.2).
3. Treat `pg_total_relation_size()` cluster/RF semantics as the single lowest-confidence
   item in the whole plan; run the smoke test in A.3 before publishing any size claim in
   docs (A.3).
4. Correct the `node_type` comment in §3.4 — it's primary-vs-read-replica, not
   master-vs-tserver, and `yb_servers()` never lists masters (A.5).
5. Reconsider deferring `pg_stat_statements` — it's a supported, commonly-default-enabled
   extension and is a good candidate to ship in the first PR rather than a follow-up (A.7).
6. Do not port `pg_locks` as-is even in a later PR; target `yb_lock_status()` instead for
   any future lock-status tool (A.8).
7. Add: node-identity stamping per tool response, and an explicit read-only session guard
   — neither was in the original plan (§C).

Everything else in the plan — the overall architecture, the `RelationalConfigBase` reuse,
the credential-resolution table, the decision to omit `pg_stat_replication`, and the
`yb_servers()`-only scoping of the cluster-status tool — is sound and matches how
YugabyteDB actually behaves.

---

## OpenSRE Architecture Review

Reviewed against `AGENTS.md`, `docs/adding-tools-and-integrations.md`, `docs/NAMING.md`,
`integrations/_relational.py`, `integrations/postgresql/` (full package),
`integrations/registry.py`, `integrations/effective_models.py`, `integrations/_catalog_impl.py`,
`integrations/cli.py`, `integrations/alert_source_catalog.py`, `integrations/_verifiers_loader.py`,
`integrations/setup_flow.py`, `config/env_file.py`, `config/constants/postgresql.py` /
`config/constants/__init__.py`, `docs/docs.json`, and the existing `tests/integrations/` /
`tests/tools/` / `tests/e2e/postgresql/` precedent. This is an architecture/conventions review
only — YugabyteDB-domain correctness (§8 of this doc) is out of scope here.

### Verdict

The file-by-file plan in §4.1/§4.2 is structurally sound and, for every touchpoint it lists,
matches the current `postgresql` precedent line-for-line (verified by reading each named file
at the cited line numbers, not just skimming). The credential-resolution table in §5 is
correct in its conclusion. There is exactly **one missed touchpoint** that must be added before
implementation, one **materially wrong explanation** (conclusion still correct) in §5 worth
fixing so the next implementer doesn't cite it as the mechanism, and a few small
precision/completeness gaps in §6 (tests) and §7.2 (docs) that should be tightened. Nothing
found here blocks starting implementation once the missing touchpoint is added.

### 1. Missed touchpoint: `core/domain/diagnosis/alignment.py` — **must add**

`core/domain/diagnosis/alignment.py` defines `_GROUP_SIGNALS[GROUP_DATABASE]`, a tuple of
vendor keyword signals used by `detect_category_text_mismatch` /
`apply_category_alignment_adjustments` to catch a root-cause category/text mismatch during
investigation reporting:

```python
_GROUP_SIGNALS: dict[str, tuple[str, ...]] = {
    GROUP_DATABASE: (
        "postgres",
        "postgresql",
        "mysql",
        "mariadb",
        "redis",
        ...
```

`mariadb` is already present here alongside `postgres`/`postgresql`, confirming this is a real,
maintained per-vendor list, not dead code. §4 of the design doc (file-by-file plan) does not
mention this file at all — an omission the design doc's own research method should have
caught, since `AGENTS.md`'s "Changing the investigation pipeline" section explicitly names
`core/domain/` as the place for "alert source mapping, tool planning, **category alignment**,
correlation scoring," and `core/domain/diagnosis/alignment.py` is exactly that file. Add
`"yugabytedb"` and `"yugabyte"` to `_GROUP_SIGNALS[GROUP_DATABASE]` in the same PR.

(`core/domain/alerts/alert_source.py` and
`tools/investigation/stages/gather_evidence/prompt.py` — the two other files
`docs/adding-tools-and-integrations.md` §3 calls out for "investigation wiring" — do **not**
currently reference `postgresql` at all, so the design doc's silence on them is correct, not an
omission.)

### 2. Credential resolution (§5) — conclusion correct, mechanism description wrong

The table's conclusion — `password` is the only keyring-eligible field, all others go through
store/`.env` via plain `os.getenv` — is correct and matches `POSTGRESQL_PASSWORD`'s handling at
`integrations/_catalog_impl.py:877` exactly, and `resolve_env_credential(YUGABYTEDB_PASSWORD_ENV)`
is the right call for the env-loader block in §4.2.

But the doc's supporting claim is wrong: *"`SetupField(secret=True)` on the `password` field in
`YUGABYTEDB_SETUP` routes the wizard write through `sync_env_secret` automatically."* Reading
`integrations/setup_flow.py`, `SetupField.secret` only controls input masking ("Whether
collection surfaces should mask this field while it is typed" — line 128-129). The actual
keyring-vs-`.env` routing happens in `_persist_env` (`integrations/setup_flow.py:288-292`),
which calls `is_sensitive_env_key(field.env_var)` — **independent of `field.secret`** — to
decide `sync_env_secret` vs. plain `.env`. `is_sensitive_env_key` (`config/env_file.py:95-103`)
pattern-matches on the env var name's terminal token (`password`, `token`, `key`, `secret`, …).
`YUGABYTEDB_PASSWORD` is routed to the keyring because its name ends in `_PASSWORD`, not because
`secret=True` is set on the `SetupField`. The plan's `YUGABYTEDB_SETUP.fields` entry for
`password` (§4.1 table row) is still correct as specified — just fix the reasoning in §5 before
an implementer cites it as how the mechanism works elsewhere.

### 3. `docs/docs.json` registration — plan is correct, now verified

Confirmed live: the `"Data and workflow systems"` group's `pages` array
(`navigation.tabs[2].groups[5]`) is
`[airflow, azure-sql, clickhouse, dagster, elasticsearch, kafka, mariadb, mongodb,
mongodb-atlas, mysql, openclaw, opensearch, postgresql, prefect, rabbitmq, rds, redis,
snowflake, supabase, temporal]` — alphabetically sorted, `temporal` last. §4.2/§7.2's plan to
append `"yugabytedb"` after `"temporal"` is correct. This closes the "doc unreachable in
Mintlify nav" footgun from `AGENTS.md` §3 — the plan already accounts for it.

### 4. Test plan (§6) vs. `docs/adding-tools-and-integrations.md` §5 checklist

- Unit tests for config/normalization (§6.1), tool contract tests (§6.2/§6.3), runtime
  success/failure tests, and a realistic fixture test (§6.4, `yb_servers()` fixture) are all
  present and correctly mirror `tests/integrations/test_postgresql.py`'s class shape (confirmed
  by reading it: `TestPostgreSQLConfig`, `TestBuildPostgreSQLConfig`,
  `TestPostgreSQLConfigFromEnv`, `TestPostgreSQLValidationResult` — the plan's §6.1 list matches
  this exactly, including the "does not split into a `_integration.py` file" call, which is
  correct: `postgresql` has no `test_postgresql_integration.py` counterpart, unlike `mariadb`).
- §6.5 (e2e) is correctly modeled on `tests/e2e/postgresql/test_postgresql_e2e.py` +
  `postgresql_alert.json`, both of which exist and exercise the exact `alert_source_catalog.py`
  routing entry the plan adds in §4.2.
- §6.6 ("registry/discovery test... not located during this research pass") undersells what
  already exists and should be corrected rather than left as an open TODO for the implementer:
  `tests/integrations/test_registry.py` (`test_registry_declares_each_service_once`,
  `test_registry_supported_lists_are_derived_from_specs`, `test_every_setup_spec_has_handler`)
  and `tests/integrations/test_registry_invariants.py` (`test_setup_orders_are_unique`,
  `test_verify_orders_are_unique`) are **generic, structural** tests driven off
  `INTEGRATION_SPECS` — a correctly-added `yugabytedb` `IntegrationSpec` (§4.2) passes them
  automatically, no new test code needed. `tests/tools/test_registry.py` is likewise a generic
  tool-discovery suite; the one hardcoded per-tool-name list it contains
  (`_V2_TOOL_CONTRACT_NAMES`) is a closed, opt-in legacy contract set that includes only
  `get_postgresql_slow_queries`, not every postgresql tool — the new yugabytedb tools are not
  expected to join it. Net: §6.6 can be simplified to "no dedicated registry test needed; the
  existing structural suite covers it once the registry entry lands," which also removes an
  unnecessary "must be found/extended" action item for the implementer.

### 5. `YugabyteDBConfig` vs. `RelationalConfigBase` (§2.1)

`RelationalConfigBase` (`integrations/_relational.py`) supplies `host`/`database`/`username`
`field_validator`s only (`mode="before", check_fields=False`) and nothing else — no default
fields, no `is_configured`, no port/password/ssl_mode handling. `PostgreSQLConfig` adds
`port`, `database`, `username` (with a default-override validator), `password`, `ssl_mode`
(with a default-override validator), `timeout_seconds`, `max_results`, `integration_id`, and
`is_configured`. The doc's proposed `YugabyteDBConfig` (§2.1) reproduces this field set exactly
with only the two Yugabyte-specific default overrides (`username` default `"yugabyte"`,
`ssl_mode` default unchanged at `"prefer"`, flagged for expert review) — no redundant fields,
nothing `resolve_stored_or_env_config` needs is missing. This part of the plan is correct as
written.

### 6. File placement / naming — no violations found

- Every new file in §4.1 lands under `integrations/yugabytedb/...`, matching the "Domain /
  provider / vendor module" row of `AGENTS.md`'s File placement table — no vendor-specific logic
  proposed for a shared file.
- `config/constants/yugabytedb.py` is correctly a new leaf module under `config/constants/`, not
  inlined into a feature module or added to `config/config.py` — matches the Code Style rule and
  the existing `config/constants/postgresql.py` shape (`__all__` alphabetized, `from __future__
  import annotations`, plain string constants) exactly.
- Verifier discovery needs no manual wiring: `integrations/_verifiers_loader.py` auto-imports
  `integrations.<name>.verifier` for every package under `integrations/` via `pkgutil` — so
  `integrations/yugabytedb/verifier.py` (§4.1) is sufficient on its own; the doc doesn't claim
  otherwise, but it's worth the implementer knowing there is no central loader list to edit.
- `integrations/harness_adapters.py` and `tools/harness_adapters.py` (named in `AGENTS.md`'s
  repo map as part of the harness port wiring) do **not** reference `postgresql` at all — the
  design doc's silence on them is correct.
- No `Protocol` bodies, no new `raise NotImplementedError`/`...` stubs are introduced by this
  plan — the docstring-only-body rule doesn't apply here since nothing in §2–§4 defines a new
  `Protocol`.
- `.env.example`'s `# PostgreSQL` block (confirmed at lines 412-418: `POSTGRESQL_HOST`,
  `_PORT=5432`, `_DATABASE`, `_USERNAME=postgres`, `_PASSWORD`, `_SSL_MODE=prefer`) matches
  the shape §4.2 describes; the doc's own caveat ("not read in this research pass — confirm
  during implementation") is now resolved — it matches, no surprises.

### Summary of required changes before implementation

1. **Add** `"yugabytedb"` / `"yugabyte"` to `_GROUP_SIGNALS[GROUP_DATABASE]` in
   `core/domain/diagnosis/alignment.py` — new §4.2 touchpoint, currently missing from the plan.
2. **Fix** §5's explanation of why `password` routes to the keyring: it's
   `is_sensitive_env_key(field.env_var)` matching the `_PASSWORD` suffix in
   `integrations/setup_flow.py:_persist_env` / `config/env_file.py`, not `SetupField(secret=True)`
   (that flag only controls input masking). The credential table's conclusion needs no change.
3. **Simplify** §6.6: the registry/discovery coverage already exists generically in
   `tests/integrations/test_registry.py` and `test_registry_invariants.py`; no new test file is
   needed there, so drop the "must be found/extended" action item.

Everything else in §2–§4.2, §5's table, and §6's test list is conventions-compliant and
accurately mirrors the `postgresql` precedent.
