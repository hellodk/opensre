# Aerospike Integration — Architecture & Implementation Plan

Status: **design only — no implementation code has been written**. This
document is the spec an Aerospike domain expert should review before any
code is written, with particular attention to §1 (client architecture) and
§9 (risks/open questions).

**Revision note (2026-08-09):** the client architecture below was revised
after review — see the pivot from raw-socket to `asinfo` CLI wrapper. The
two review sections at the end of this document (`## Aerospike Domain
Expert Review`, `## OpenSRE Architecture Review`) were written against the
original raw-socket draft; their protocol-framing findings are superseded
by this revision (there is no wire-protocol code left to review — `asinfo`
owns framing/auth/TLS), but their namespace-stats-scope,
registry-slot-collision, and test-strategy findings still apply and have
been folded into the sections above. Read the reviews for historical
context, not as the current design.

## 0. Why this integration, and the key design decision

Aerospike is a common low-latency key-value store in SRE stacks (session
storage, feature flags, real-time bidding, ad-tech counters). OpenSRE has no
Aerospike integration today.

The official `aerospike` PyPI package ships a native C-extension
(`aerospike-*.whl` built against `libaerospike-client`, itself linked against
OpenSSL/liblua). Per `AGENTS.md` → "Local system — absolute no-install rule"
and the repo's "no new system packages" constraint, we cannot guarantee that
extension is buildable/installable in every deploy target this CLI runs in
(constrained containers, air-gapped hosts, `pip`-only environments without a
C toolchain). This rules out the official client as a hard dependency.

**Design decision (revised 2026-08-09):** implement a **subprocess wrapper
around `asinfo`** — Aerospike's own official info-protocol CLI tool, shipped
in the `aerospike-tools` package alongside every Aerospike install. An
earlier draft of this design proposed a hand-rolled pure-Python raw-socket
client against the plaintext info protocol (TCP, default port 3000) to avoid
depending on the native-extension `aerospike` PyPI client. That raw-socket
approach was reviewed and **rejected**: `asinfo` already correctly handles
proto framing, the bcrypt/fixed-salt auth handshake, TLS, and version
differences across the info-protocol grammar — none of that is protocol
knowledge opensre should own or maintain. `asinfo <command>` writes the
exact same `name\tvalue`-shaped plaintext to stdout that a raw socket read
would have produced, so the response-parsing logic barely changes; only the
transport layer changes (subprocess instead of a raw socket). `asinfo` is a
plain CLI binary with no C-extension linkage into the opensre Python
process itself, so it does not reintroduce the "native extension in the
deploy target" risk that ruled out the official `aerospike` client — the
dependency moves from "a compiled Python extension `pip install`s" to "a
binary must be present on `PATH`" (see §1 and §9 for how that's handled and
what it costs). It does **not** give us record-level KV access (`get`/`put`
on user data) — that requires the binary transaction protocol, which is out
of scope for a diagnostics/investigation tool anyway (OpenSRE's Redis/Mongo
integrations are similarly limited to server/cluster diagnostics, not user
data access).

Consequence of this decision: **all tools in this integration are
cluster/node/namespace *diagnostics*, never record reads.** This matches the
existing integration pattern (Redis's tools are `INFO`, `SLOWLOG`,
`CLIENT LIST`, `LATENCY` — never `GET`/`SCAN` of user *values*, only key
metadata) and keeps the integration safely read-only with no risk of
touching customer data.

## 1. Architecture overview

### 1.1 Request flow

```
tool call (get_aerospike_node_status, etc.)
  -> extract_params() resolves AerospikeConfig from integration store/env
  -> client.py resolves the `asinfo` binary (PATH, or an explicit override)
  -> client.py runs `asinfo -h <host> -p <port> -v <command>` as a subprocess
     (adds -U/-P when username/password are configured; -U/-P are the whole
     v1 auth story — see §3.3)
  -> subprocess.run(..., capture_output=True, timeout=config.timeout_seconds)
  -> non-zero exit code / stderr -> mapped to a distinct failure mode (§1.3)
  -> stdout decoded as the response payload — the same "name\tvalue" text
     (or, for a single -v command, just the value) a raw info-protocol read
     would have produced
  -> parses the semicolon/"key=value" grammar inside the value, same as
     before the transport change
  -> tool function normalizes the parsed dict into the tool's JSON response
     shape
```

No client library, no persistent session, no connection reuse across
calls — every tool call is a fresh `asinfo` subprocess invocation, matching
`integrations/redis`'s "create client, use, close" per-call pattern
(`integrations/redis/__init__.py` lines 113–132, 199–203) at the same
granularity, and structurally mirroring `integrations/helm/client.py`'s
CLI-subprocess wrapper (binary resolution via `shutil.which`/config
override, a single `_run()` seam, `subprocess.TimeoutExpired`/`OSError`
mapped to distinct error results) more closely than it mirrors Redis, since
Redis wraps a Python library and this integration wraps a CLI binary the
same way Helm's does.

### 1.2 Command surface and response grammar

`asinfo -v <command>` is invoked once per info command needed (or once with
multiple `\n`-joined commands via `-v "statistics\nnamespaces"` — `asinfo`
supports the same multi-command batching the info protocol itself does).
The commands this integration issues are unchanged from the original
raw-socket design, with one correction folded in from the domain-expert
review (§"Aerospike Domain Expert Review" below): `latencies:` (plural),
not `latency:`, is the primary command for current server versions.

- `status` — bare string (`ok`), no delimiter structure.
- `statistics` — `key=value;key=value;...;` semicolon-separated pairs
  (trailing semicolon commonly present; trailing empty segment ignored by
  the parser).
- `namespaces` — bare semicolon-separated list of namespace names
  (`ns1;ns2;ns3`), no `=` inside.
- `namespace/<ns>` — same `key=value;...;` grammar as `statistics`.
- `latencies:` — the modern (server ≥ ~4.9), stable, documented
  latency-histogram command; takes optional params
  (`latencies:hist={<namespace>}-read`, `latencies:back=600`) rather than
  being parameter-less. Older servers exposing only the legacy `latency:`
  command are handled by the same defensive, degrade-to-raw-text parser
  (§3, §4.3) — this was never really "one command's grammar," it's two
  different commands across a version boundary, which is a stronger reason
  to keep the defensive parsing than earlier drafts gave credit for.

`asinfo`'s stdout for these commands is textually identical to what a raw
info-protocol socket read would have returned — there is no proto header to
strip (that framing is entirely internal to `asinfo`'s own connection to
the server) and no byte-level packing left in this design at all. The
parser therefore only has to handle:
1. **Split stdout on `\n`**, then split each non-empty line on the first
   `\t` into `(command_name, value)` when multiple commands were requested
   in one invocation (`asinfo -v "cmd1\ncmd2"` echoes back
   `cmd1\tvalue1\ncmd2\tvalue2\n`, matching the raw-protocol shape);
   for a single-command invocation, `asinfo` prints the bare value with no
   `name\t` prefix, so the parser must handle both shapes (see §3).
2. **Parse `value` per the command's own grammar**, which is
   command-specific — same three parser functions as originally designed
   (`_parse_semicolon_kv`, `_parse_semicolon_list`, `_parse_latency`), now
   living in `integrations/aerospike/__init__.py` unchanged in behavior,
   only their input source (subprocess stdout, not a socket read) differs.

This mirrors how `integrations/redis/__init__.py` layers `_get_client()` /
raw command execution underneath command-specific normalization functions
(`get_server_info`, `get_slowlog`, `get_replication`, …) — the transport
and per-tool normalization are separate concerns, separately testable.

### 1.3 Failure modes the client must handle explicitly

- **`asinfo` binary not found on `PATH`** — `shutil.which("asinfo")` (or a
  configured override path) returns `None`. This is a distinct failure mode
  from a connection failure: it means the `aerospike-tools` package isn't
  installed on the host/container, not that the cluster is unreachable.
  Map to `available: False` with a message pointing at the `asinfo`
  prerequisite (§8.1), mirroring `HelmClient._resolved_helm_path()` /
  `ProbeResult.missing(...)` in `integrations/helm/client.py`
  (`_resolved_helm_path`, `probe_access`).
- **Non-zero exit code, `asinfo` ran but couldn't connect** — `asinfo`
  exits non-zero and writes a connection-refused/timeout/DNS message to
  stderr when it cannot reach `host:port` at all. Surface stderr's text
  (bounded/truncated, never raw-dumped past a sane length) alongside a
  generic `available: False`, mirroring `_redis_error()`
  (`integrations/redis/__init__.py` lines 667–694). This must be
  distinguished from the next case (see §9 — the exact exit-code/stderr
  shape for "can't connect" vs. "connected but command errored" needs
  confirming against a real `asinfo` before the mapping is finalized).
- **Non-zero exit code, `asinfo` connected but the command itself
  errored** — e.g. an invalid `namespace/<ns>` for a namespace that doesn't
  exist, or (once auth lands) a rejected credential. Different stderr text
  than the connection-failure case; the client must not conflate the two
  into one generic "unavailable" message where avoidable — see §9.
- **Subprocess timeout** — `subprocess.TimeoutExpired` when `asinfo`
  doesn't return within `config.timeout_seconds`. Treat as a distinguishable
  timeout error, not a silent partial result, mirroring
  `HelmClient._run()`'s `except subprocess.TimeoutExpired` handling.
- **Malformed/unexpected stdout** — a line with no `\t` where one was
  expected, an unparseable `key=value` segment, or output that doesn't match
  either of the two shapes described in §1.2 (bare value vs. `name\tvalue`
  lines). Defensive: `asinfo`'s output format is expected to be identical
  to a raw info-protocol response, but this is a "should be true, confirm
  it" claim (see §9), not a load-bearing assumption the parser should trust
  blindly.
- **Auth required but not supplied, or auth failure** — with `-U`/`-P`
  wired in (§3.3), this is now a much smaller residual risk than the
  original raw-socket design's auth handling would have been, but a
  security-enabled cluster with no/wrong credentials still needs to surface
  as a clear, distinct error (not "empty response") so an operator knows to
  check credentials rather than assume the namespace is empty.

## 2. Config

### 2.1 `config/constants/aerospike.py` (new file)

Mirrors `config/constants/redis.py` exactly in shape:

```python
"""Aerospike environment variable names."""

from __future__ import annotations

AEROSPIKE_HOST_ENV = "AEROSPIKE_HOST"
AEROSPIKE_PORT_ENV = "AEROSPIKE_PORT"
AEROSPIKE_USERNAME_ENV = "AEROSPIKE_USERNAME"
AEROSPIKE_PASSWORD_ENV = "AEROSPIKE_PASSWORD"
AEROSPIKE_TIMEOUT_SECONDS_ENV = "AEROSPIKE_TIMEOUT_SECONDS"
AEROSPIKE_TLS_ENV = "AEROSPIKE_TLS"

__all__ = [
    "AEROSPIKE_HOST_ENV",
    "AEROSPIKE_PASSWORD_ENV",
    "AEROSPIKE_PORT_ENV",
    "AEROSPIKE_TIMEOUT_SECONDS_ENV",
    "AEROSPIKE_TLS_ENV",
    "AEROSPIKE_USERNAME_ENV",
]
```

`AEROSPIKE_PASSWORD_ENV` matches the `*_PASSWORD` keyring-eligible suffix
(`is_sensitive_env_key`, `config/env_file.py`) automatically — no special
casing needed, same as Redis's `REDIS_PASSWORD_ENV`.

`tls_enabled` is included in the config model (§2.2) for forward
compatibility but is **explicitly out of scope for v1 tools** — see §9. With
the CLI-wrapper architecture, adding TLS later is just wiring
`--tls-enable` and the related `asinfo` TLS flags (`--tls-name`,
`--tls-cafile`/`--tls-capath`, etc. — `asinfo --help` enumerates the exact
set) into the argument list built in `client.py`, not a from-scratch TLS
implementation the way it would have been on top of a raw socket
(`ssl.wrap_socket`/`ssl.SSLContext`). TLS certificate material (client
cert/key paths, CA bundle) is still *not* modeled in v1 and would need its
own follow-up design if TLS is required by a target cluster.

### 2.2 `integrations/aerospike/__init__.py` (new file) — `AerospikeConfig`

Follows `RedisConfig` (`integrations/redis/__init__.py` lines 47–77)
exactly: extends `StrictConfigModel` (not the relational
`integrations/config_models.py` relational base — Aerospike, like Redis, is
a non-relational store with no `database`/schema concept beyond
"namespace", which is resolved per-call, not per-connection).

```python
class AerospikeConfig(StrictConfigModel):
    """Normalized Aerospike connection settings."""

    host: str = ""
    port: int = Field(default=DEFAULT_AEROSPIKE_PORT, ge=1, le=65535)  # 3000
    username: str = ""
    password: str = ""
    timeout_seconds: float = Field(default=DEFAULT_AEROSPIKE_TIMEOUT_SECONDS, gt=0)
    tls_enabled: bool = False
    max_results: int = Field(default=DEFAULT_AEROSPIKE_MAX_RESULTS, gt=0, le=200)
    integration_id: str = ""

    @field_validator("host", mode="before")
    @classmethod
    def _normalize_host(cls, value: Any) -> str: ...
        # identical shape to RedisConfig._normalize_host

    @field_validator("username", mode="before")
    @classmethod
    def _normalize_username(cls, value: Any) -> str: ...

    @field_validator("password", mode="before")
    @classmethod
    def _normalize_password(cls, value: Any) -> str: ...

    @property
    def is_configured(self) -> bool:
        return bool(self.host)
```

Named constants alongside (mirrors Redis's module-level constants at
`integrations/redis/__init__.py` lines 35–44):

```python
DEFAULT_AEROSPIKE_PORT = 3000
DEFAULT_AEROSPIKE_TIMEOUT_SECONDS = 5.0
DEFAULT_AEROSPIKE_MAX_RESULTS = 50   # cap for namespace lists / latency buckets returned
```

`username`/`password` are optional — most Aerospike deployments run with
security disabled on trusted internal networks (mirrors Redis's
`requirepass`-optional model), but the fields exist so an
Aerospike Enterprise cluster with security enabled *can* supply them (see
§9 for why the auth path itself is not implemented in v1).

`build_aerospike_config(raw)`, `aerospike_config_from_env()`,
`validate_aerospike_config(config)`, `aerospike_is_available(sources)`,
`aerospike_extract_params(sources)`, and `classify(credentials, record_id)`
all mirror the Redis equivalents 1:1 (same function names, same signatures,
substituting the Aerospike env constants and config type). No structural
deviation from the Redis pattern is needed here.

`AerospikeValidationResult` (frozen dataclass, `ok: bool`, `detail: str`)
mirrors `RedisValidationResult`.

### 2.3 `integrations/config_models.py` — `AerospikeIntegrationConfig`

New class alongside `RedisIntegrationConfig` (currently lines 600–613),
same shape:

```python
class AerospikeIntegrationConfig(StrictConfigModel):
    """Normalized Aerospike credentials used by resolution and verification flows."""

    host: str
    port: int = 3000
    username: str = ""
    password: str = ""
    timeout_seconds: float = 5.0
    tls_enabled: bool = False
    integration_id: str = ""

    _normalize_host = field_validator("host", mode="before")(normalize_str())
    _normalize_username = field_validator("username", mode="before")(normalize_str())
    _normalize_password = field_validator("password", mode="before")(normalize_str())
```

## 3. Client — `integrations/aerospike/client.py` (new file)

This is the `asinfo` subprocess wrapper — the only file in the integration
that touches `subprocess` directly. Per the file-placement rules (AGENTS.md
"File placement"), all transport-specific logic lives here, not inlined
into tool bodies or `integrations/aerospike/__init__.py`'s normalization
functions. Structurally this mirrors `integrations/helm/client.py`
(`HelmClient`) more closely than anything in `integrations/redis`: binary
resolution via `shutil.which` (with a config-supplied override path),
a single `_run()` subprocess seam, and `subprocess.TimeoutExpired`/`OSError`
mapped to distinct, named failure results rather than left to propagate.

### 3.1 Constants (module-level, named — no magic numbers per Code Style)

```python
_DEFAULT_ASINFO_BIN = "asinfo"
_ASINFO_NOT_FOUND_EXIT_CODE = 127   # mirrors HelmClient._run()'s convention
_ASINFO_TIMEOUT_EXIT_CODE = 124     # mirrors HelmClient._run()'s convention
```

### 3.2 Functions (illustrative signatures — implementation deferred)

```python
def _resolved_asinfo_path(config: AerospikeConfig) -> str | None:
    """Resolve the asinfo binary: config override path if set and present,
    else PATH lookup via shutil.which(_DEFAULT_ASINFO_BIN). Mirrors
    HelmClient._resolved_helm_path()."""

def _build_args(config: AerospikeConfig, commands: Sequence[str]) -> list[str]:
    """Build the asinfo argv: -h <host> -p <port> -v "<cmd1>\\n<cmd2>...",
    plus -U <username> -P <password> when both are set (§3.3), plus
    --tls-* flags when config.tls_enabled is set (deferred — see §9, not
    wired in v1 even though the flag exists on the config model)."""

def send_info_commands(
    config: AerospikeConfig,
    commands: Sequence[str],
) -> dict[str, str]:
    """Run asinfo with the given commands, return {command_name: raw_value_string}.

    Caller-facing entrypoint — the *only* function
    integrations/aerospike/__init__.py's per-tool data functions call.

    subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
    errors="replace", timeout=config.timeout_seconds, check=False,
    env=os.environ.copy()) — same shape as HelmClient._run(). No process
    reuse, no pooling: one subprocess per call, matching the "fresh
    connection per call" behavior the raw-socket design already committed
    to (§1.1) and mirroring integrations/redis's per-call client pattern.

    Raises (or returns a client-level error result, exact error-carrying
    shape TBD at implementation time — see §1.3 for the failure modes this
    must distinguish) rather than silently returning partial/empty data on
    a missing binary, non-zero exit, or timeout.
    """
```

`send_info_commands` returns the raw, unparsed `{command_name:
value_string}` mapping (or a single raw string when only one command was
requested and `asinfo` printed a bare value with no `name\t` prefix — see
§1.2). Per-command grammar parsing (`key=value;` vs bare `;`-list vs
`latencies:`'s shape) lives in `integrations/aerospike/__init__.py` next to
the tool-facing normalization functions, one function per grammar —
**unchanged from the original design**, since this is exactly the part of
the design the domain-expert review confirmed as correct (see "Aerospike
Domain Expert Review" → "Sanity-check" #2 below):

```python
def _parse_semicolon_kv(raw: str) -> dict[str, str]:
    """Parse 'k1=v1;k2=v2;' into a dict, ignoring a trailing empty segment."""

def _parse_semicolon_list(raw: str) -> list[str]:
    """Parse 'a;b;c' into ['a', 'b', 'c'], dropping empty segments."""

def _parse_latency(raw: str) -> dict[str, Any]:
    """Best-effort parse of the `latencies:` (or legacy `latency:`) payload;
    degrades to {"raw": raw} on any shape this parser doesn't recognize
    rather than raising — see §9, this command's exact grammar across
    server versions is the least certain part of this design."""
```

### 3.3 Auth — `-U`/`-P` flags to `asinfo` (a real improvement over v1's
original scope)

`send_info_commands` passes `config.username`/`config.password` to `asinfo`
as `-U <username> -P <password>` whenever both are set. This is a
meaningfully cheaper v1 auth story than the raw-socket design could have
offered: the domain-expert review found that basic auth on a raw socket
would have required implementing Aerospike's `type=2` admin-protocol
handshake plus a bcrypt-with-fixed-salt credential hash — real protocol
work, and one that risked reintroducing a native-extension dependency
(`bcrypt`) into the exact design this whole integration exists to avoid.
`asinfo` already implements that handshake internally; wrapping it in `-U`/
`-P` is close to free by comparison. **Auth is therefore in scope for v1**
(unlike the original raw-socket plan, which deferred it entirely) — see §9
for what's still open (mainly: confirming `asinfo`'s exact
exit-code/stderr shape on an auth failure so the client can surface it as a
distinct error rather than a generic connection failure).

If `username`/`password` are unset, `asinfo` is invoked without `-U`/`-P`,
which works as before against unauthenticated/trusted-network clusters
(security disabled). TLS (`--tls-*` flags) remains deferred to a follow-up
per §2.1/§9 — not because it's hard with the CLI wrapper (it's a small flag
addition), but because this plan doesn't want to assert an untested claim
about the target deployment's TLS listener configuration (see the domain
review's TLS verdict below: TLS commonly uses a **separate port**, not the
same port with an opportunistic handshake).

## 4. Tool surface

Three tools, all read-only, all under
`integrations/aerospike/tools/<tool_name>_tool/__init__.py`, following the
`redis_server_info_tool` package shape (single-file tool package —
`core.tool_framework.tool_decorator.tool` decorator, no class needed since
none of these need side effects, approval, or multi-file complexity beyond
what the shared `client.py`/`__init__.py` already factor out).

### 4.1 `get_aerospike_node_status`

- **Description:** "Retrieve Aerospike node health: uptime, cluster size,
  cluster key, and node status."
- **`input_schema`:** `{host?, port?}` — all connection fields are
  `injected_params` (resolved from the integration, not model-supplied),
  matching Redis's `injected_params=("host",)` pattern
  (`integrations/redis/tools/redis_server_info_tool/__init__.py` line 34).
- **Info commands issued:** `status` (single bare-string liveness check) and
  `statistics` (the structured `key=value;` payload — for
  `cluster_size`, `cluster_key`, `uptime`, `cluster_integrity`,
  `cluster_clock_skew_stop_writes_sec`, and similar fields commonly present
  in Aerospike `statistics` output).
- **Normalization:** `_parse_semicolon_kv(raw["statistics"])` into a typed
  dict; `status` value surfaced as `"ok"`/other string, with an explicit
  `node_status` field (not just truthy/falsy) so a degraded-but-responding
  node is visible rather than collapsed into "available: true".
- **Response shape (illustrative):**
  ```json
  {
    "source": "aerospike",
    "available": true,
    "node_status": "ok",
    "cluster_size": 3,
    "cluster_key": "...",
    "uptime_seconds": 48213,
    "cluster_integrity": true
  }
  ```
- **Exact field names surfaced by `statistics` vary by server version** —
  the parser must not `KeyError` on a missing field; use `.get(...)` with
  sensible defaults, same defensive style as `get_server_info`'s
  `info.get("redis_version", "")` throughout.

### 4.2 `get_aerospike_namespace_stats`

- **Description:** "List Aerospike namespaces and retrieve per-namespace
  storage, memory, and object-count statistics for the connected node.
  **These figures are single-node/local, not cluster-wide aggregates** —
  see the caveat below."
- **`input_schema`:** `{host?, port?, namespace?: str}` — optional
  `namespace` filter; when omitted, all namespaces are enumerated and
  capped at `config.max_results` (mirrors Redis's `max_results` cap
  pattern used in `get_client_list`/`scan_keys`).
- **Info commands issued:** `namespaces` first (bare `;`-list →
  `_parse_semicolon_list`), then one `namespace/<ns>` call **per
  namespace** (`key=value;` → `_parse_semicolon_kv`) — either as separate
  `asinfo` invocations or batched into one multi-command `-v` argument
  (`-v "namespace/ns1\nnamespace/ns2"`) per §1.2's multi-command batching.
  Batching into a single `asinfo` invocation is preferred (fewer
  subprocess spawns, and the domain-expert review confirmed there is no
  documented commands-per-request limit relevant at this scale) — flagged
  as an implementation choice, not a correctness requirement.
- **Normalization per namespace:** object count, memory used
  (bytes + human-readable if the server reports one), device/disk used
  bytes, replication factor, stop-writes/read-only flags — again via
  `.get(...)` with defaults, since exact key names differ across Aerospike
  storage engines (in-memory vs SSD-backed namespaces expose different
  stat keys).
- **Cluster-scope caveat (must ship in the docstring and tool description,
  not just this design doc):** `asinfo -h <host> -p <port>` talks to
  exactly **one** node — the transport change from raw socket to CLI
  wrapper does not change this, `asinfo` has the same single-node scope a
  raw socket connection would have had. A node's `statistics` response
  (`cluster_size`, `cluster_key`, `cluster_integrity`) genuinely is a
  cluster-wide value reported identically by every node, so reading it from
  one node (as `get_aerospike_node_status` does) is fine. But a node's
  `namespace/<ns>` response (`objects`, `memory_used_bytes`,
  `device_used_bytes`, and similar) reflects **only that node's local
  partition share** of the namespace — in a multi-node cluster each node
  holds roughly `1/N` of the master partitions (plus replicas, reported
  separately in some fields). This tool does **not** fan out to every
  cluster member and sum; v1 explicitly documents the limitation rather
  than implementing multi-node aggregation (fan-out is a reasonable
  follow-up, discoverable via the `peers`/`services`-style info commands
  from the first connection, but out of scope here). The response JSON
  should carry this as an explicit field/note (e.g. a `"scope":
  "single_node"` marker or an inline note in the response), not leave it
  implicit the way the original raw-socket draft did.
- **Response shape (illustrative):**
  ```json
  {
    "source": "aerospike",
    "available": true,
    "scope": "single_node",
    "namespace_count": 2,
    "namespaces": {
      "test": {
        "objects": 10432,
        "memory_used_bytes": 5242880,
        "device_used_bytes": 0,
        "replication_factor": 2,
        "stop_writes": false
      }
    },
    "truncated": false
  }
  ```
  `truncated: true` when `namespace_count > config.max_results` and the
  `namespaces` dict was capped — same "surface truncation explicitly"
  requirement as §4 of `docs/adding-tools-and-integrations.md`. `"scope":
  "single_node"` is the explicit cluster-scope caveat from above, surfaced
  in the response itself rather than only in docs/docstrings.

### 4.3 `get_aerospike_latency`

- **Description:** "Retrieve Aerospike latency histograms (read/write/udf/
  query buckets) for recent time windows."
- **`input_schema`:** `{host?, port?}`.
- **Info command issued:** `latencies:` (plural — the modern, currently
  supported command; see §1.2). Falls back to the legacy `latency:` only
  when `latencies:` isn't supported by the target server version — exact
  fallback trigger (unsupported-command error vs. explicit version check)
  is an implementation detail deferred to coding time, not a design
  decision this doc needs to lock down.
- **Normalization:** delegated to `_parse_latency`, which — per §1.2/§9 —
  is the least certain grammar in this plan, now doubly so because it must
  handle **two different commands across a server-version boundary**
  (`latencies:`'s structured, parameterized shape vs. legacy `latency:`'s
  older shape) rather than one command with uncertain grammar. The tool's
  response must degrade gracefully: if `_parse_latency` cannot confidently
  structure the payload, return the raw text under a `raw` key alongside
  `available: true` (data was fetched, just not fully parsed) rather than
  failing the tool call outright. This is a deliberate defensive design
  choice given the grammar uncertainty flagged in §9.
- **Response shape (illustrative, structured case):**
  ```json
  {
    "source": "aerospike",
    "available": true,
    "histograms": {
      "read": [{"time_window_sec": 1, "ops_per_sec": 120, "over_1ms_pct": 0.4}],
      "write": [...]
    }
  }
  ```
- **Response shape (degraded/unparsed case):**
  ```json
  {"source": "aerospike", "available": true, "raw": "...", "parse_warning": "..."}
  ```

### 4.4 Shared error handling — `_aerospike_error(err, method)`

Mirrors `_redis_error()` (`integrations/redis/__init__.py` lines 667–694):
classify the failure modes `client.py` can produce (§1.3 — binary not
found, connection failure, command-level failure, subprocess timeout,
unparseable output) into friendly `tool_unavailable(...)` messages;
anything unexpected goes through `report_validation_failure` for Sentry
visibility, same as Redis.

## 5. File-by-file plan

| File | Action | Mirrors |
| --- | --- | --- |
| `config/constants/aerospike.py` | create | `config/constants/redis.py` |
| `config/constants/__init__.py` | modify | add the `AEROSPIKE_*_ENV` import block + `__all__` entries, alphabetically before `alertmanager` — see §5.0 |
| `integrations/aerospike/__init__.py` | create | `integrations/redis/__init__.py` |
| `integrations/aerospike/client.py` | create | `integrations/helm/client.py` (CLI-subprocess wrapper — binary resolution, single `_run()` seam, distinct exit-code/timeout handling; not the redis `redis-py`-wrapping pattern) |
| `integrations/aerospike/setup.py` | create | `integrations/redis/setup.py` |
| `integrations/aerospike/verifier.py` | create | `integrations/redis/verifier.py` |
| `integrations/aerospike/tools/aerospike_node_status_tool/__init__.py` | create | `integrations/redis/tools/redis_server_info_tool/__init__.py` |
| `integrations/aerospike/tools/aerospike_namespace_stats_tool/__init__.py` | create | same pattern |
| `integrations/aerospike/tools/aerospike_latency_tool/__init__.py` | create | same pattern |
| `integrations/config_models.py` | modify | add `AerospikeIntegrationConfig` next to `RedisIntegrationConfig` (~line 600) |
| `integrations/registry.py` | modify | add `IntegrationSpec(service="aerospike", ...)` entry, see §5.1 |
| `integrations/effective_models.py` | modify | add `aerospike: EffectiveIntegrationEntry | None = None` field (~line 34, alongside `redis`) |
| `integrations/_catalog_impl.py` | modify | add classifier import + `_CLASSIFIERS["aerospike"]` entry, plus an `aerospike_config_from_env()`-driven env-loading block, see §5.2 |
| `integrations/cli.py` | modify | add `_setup_aerospike()` function + `"aerospike": _setup_aerospike` dispatch entry, see §5.3 |
| `integrations/alert_source_catalog.py` | modify | add `"aerospike": routing(("aerospike",), ("aerospike",))` to `_ROUTING_TABLE` and `"aerospike": ("aerospike", "namespace")` to the keyword table (no `"as"` — confirmed substring-collision bug), see §5.4 |
| `docs/aerospike.mdx` | create | `docs/redis.mdx` |
| `docs/docs.json` | modify | register `"aerospike"` in the integrations nav list (alongside the existing `"redis"` entry at line 220) |
| `.env.example` | modify | add `AEROSPIKE_HOST`, `AEROSPIKE_PORT`, `AEROSPIKE_USERNAME`, `AEROSPIKE_PASSWORD` (commented, matching Redis's block) |
| `tests/integrations/test_aerospike.py` | create | `tests/integrations/test_redis.py` |
| `tests/tools/test_aerospike_node_status_tool.py` | create | `tests/tools/test_redis_server_info_tool.py` |
| `tests/tools/test_aerospike_namespace_stats_tool.py` | create | same pattern |
| `tests/tools/test_aerospike_latency_tool.py` | create | same pattern |
| `tests/integrations/aerospike/test_client.py` | create | `integrations/helm`'s subprocess test pattern — mocks `subprocess.run`, not a real `asinfo`/socket (see §7) |
| `tests/e2e/aerospike/` | create | `tests/e2e/redis/` — mocks `send_info_commands` (or the subprocess-invocation seam) directly, no container/live server needed (see §7.5) |

### 5.0 `config/constants/__init__.py` — re-export the new env constants

Per `AGENTS.md` Code Style ("re-exported via `config/constants/__init__.py`")
and confirmed against the redis precedent, which imports and re-exports all
six `REDIS_*_ENV` names. `config/constants/aerospike.py`'s six names get the
identical treatment — added alphabetically (before the existing
`config.constants.alertmanager` import block, since `aerospike` sorts
first):

```python
from config.constants.aerospike import (
    AEROSPIKE_HOST_ENV,
    AEROSPIKE_PASSWORD_ENV,
    AEROSPIKE_PORT_ENV,
    AEROSPIKE_TIMEOUT_SECONDS_ENV,
    AEROSPIKE_TLS_ENV,
    AEROSPIKE_USERNAME_ENV,
)
```

plus the matching six entries in `__all__`. Without this, the new
constants are unreachable through the canonical re-export path every other
integration uses — this touchpoint was missing from the original
raw-socket draft and is folded in here per the architecture review.

### 5.1 `integrations/registry.py` — new spec entry

Inserted alongside the existing `redis` entry (currently lines 154–161).
**Verified against the current file** (not assumed by incrementing from
redis's own numbers, which the architecture review found doesn't work —
neighbors aren't contiguous): every `setup_order` value 0–43 and 51 is
taken (`pagerduty` uses `setup_order=31, verify_order=42` — the values the
original draft proposed, which would have collided); every `verify_order`
value 0–57 and 99 (`supabase`) is taken. `44` and `58` are free as of this
revision:

```python
IntegrationSpec(
    service="aerospike",
    has_verifier=True,
    direct_effective=True,
    setup_order=44,
    verify_order=58,
),
```

Re-verify both numbers against `integrations/registry.py` at
implementation time in case other integrations have landed in the
meantime — these are not uniqueness-enforced by any check, so a collision
won't crash anything, but it will silently interleave Aerospike into
another integration's menu/verify-order slot.

No `aliases` (Aerospike has no common alternate product name the way
Redis/Valkey does).

### 5.2 `integrations/_catalog_impl.py` — env-loading + classifier wiring

Two edits, both directly adjacent to the existing `redis` blocks:

**(a) Import** (next to line 255–256):
```python
from integrations.aerospike import classify as _classify_aerospike
from integrations.aerospike import aerospike_config_from_env
```

**(b) Classifier registration** (next to line 417 in `_CLASSIFIERS`):
```python
"aerospike": _classify_aerospike,
```

**(c) Env-loading block** (next to the redis block, currently lines
857–864, inside the same function that builds `integrations: list[...]`
from process env at startup):
```python
aerospike_config = aerospike_config_from_env()
if aerospike_config:
    integrations.append(
        _active_env_record(
            "aerospike",
            aerospike_config.model_dump(exclude={"integration_id"}),
        )
    )
```

### 5.3 `integrations/cli.py` — setup dispatch

**(a) New setup function** (next to `_setup_redis`, currently lines
504–507):
```python
def _setup_aerospike() -> None:
    from integrations.aerospike.setup import AEROSPIKE_SETUP

    _run_spec_setup(AEROSPIKE_SETUP)
```

**(b) Dispatch table entry** (next to `"redis": _setup_redis,` at line 735):
```python
"aerospike": _setup_aerospike,
```

### 5.4 `integrations/alert_source_catalog.py` — routing + keywords

**(a) Routing table** (next to the `"redis"` entry at line 51):
```python
"aerospike": routing(("aerospike",), ("aerospike",)),
```
Single-source routing (relevance == seed), same shape as Redis — no
secondary/relevance-only sources make sense for Aerospike the way
`cloudtrail`/`ec2` support `eks` alerts.

**(b) Keyword table** (next to the `"redis": ("redis", "cache"),` entry at
line 103):
```python
"aerospike": ("aerospike", "namespace"),
```
An earlier draft of this table included `"as"` (Aerospike's own CLI/log
shorthand — `asinfo`, `asadm`, `asd` the daemon process name) as a third
keyword. **Confirmed dropped, not a judgment call.** The architecture
review verified `relevant_sources_for_alert()`
(`core/domain/alerts/alert_source.py`) does substring matching against a
lowercased, space-joined alert-text blob — `if any(keyword in text for
keyword in keywords)` — not whole-token matching. A two-character keyword
`"as"` matches inside "**as**k", "cl**as**s", "incre**as**e",
"dat**as**et", "t**as**k", and any other alert text containing that
substring, so it would fire on nearly every alert. Ship with only
`("aerospike", "namespace")`.

## 6. Credential resolution

Per `docs/adding-tools-and-integrations.md` §2 "Credential resolution"
contract table:

| Field | Env var | Keyring-eligible? | Write path | Read path |
| --- | --- | --- | --- | --- |
| `host` | `AEROSPIKE_HOST` | No | `sync_env_values` → `.env` | plain `os.getenv` |
| `port` | `AEROSPIKE_PORT` | No | `sync_env_values` → `.env` | plain `os.getenv` |
| `username` | `AEROSPIKE_USERNAME` | No | `sync_env_values` → `.env` | plain `os.getenv` |
| `password` | `AEROSPIKE_PASSWORD` | **Yes** (`*_PASSWORD` suffix) | `sync_env_secret` → keyring | `resolve_env_credential` (env → keyring) |
| `timeout_seconds` | not exposed as a setup field in v1; config-model default only | n/a | n/a | n/a |
| `tls_enabled` | not exposed as a setup field in v1 (see §9) | n/a | n/a | n/a |

This is enforced automatically by `is_sensitive_env_key`
(`config/env_file.py`) matching the `*_PASSWORD` suffix — no special-casing
needed in `SetupField`, identical to how `REDIS_PASSWORD_ENV` is declared
`secret=True` in `integrations/redis/setup.py` line 51–57. The `AEROSPIKE_SETUP`
spec (`integrations/aerospike/setup.py`, new file) mirrors `REDIS_SETUP`
1:1: `HOST_FIELD` (required), `PORT_FIELD` (default `"3000"`),
`USERNAME_FIELD` (optional, prompt notes "leave blank unless security is
enabled"), `PASSWORD_FIELD` (optional, `secret=True`), and `verify=
verify_aerospike`.

`aerospike_config_from_env()` reads `password` via
`resolve_env_credential(AEROSPIKE_PASSWORD_ENV)`, never bare `os.getenv`,
matching the hard rule in the credential-resolution contract and Redis's
own `redis_config_from_env()` (line 106).

Unlike the original raw-socket draft (where `username`/`password` were
modeled but explicitly unused — §3.3 old), the CLI-wrapper design actually
**consumes** these fields: `client.py`'s `_build_args()` passes them to
`asinfo` as `-U`/`-P` whenever both are set (§3.3). The credential-flow
plumbing above is unchanged, but it is no longer forward-compatible
scaffolding — it is live in v1.

## 7. Test plan

Mirrors `tests/integrations/test_redis.py`'s class-per-concern layout
(`TestRedisConfig`, `TestRedisBuild`, `TestRedisExtractParams`,
`TestRedisValidation`, per-tool test classes, `TestNewToolErrorHandling`,
`TestResolveIntegrations`).

### 7.1 `tests/integrations/aerospike/test_client.py` (new — highest priority)

This is the file that matters most: the response-grammar parser is still
the highest bug-risk *parsing* code in the integration, and — with the
transport now a subprocess instead of a raw socket — the transport layer
itself (`_resolved_asinfo_path`, `_build_args`, `send_info_commands`) is
also fully testable in isolation by mocking `subprocess.run`, the same way
`integrations/helm`'s tests mock Helm's subprocess seam. No live `asinfo`
binary and no network are needed for any test in this file.

- `test_resolved_asinfo_path_uses_path_lookup` — `shutil.which` monkeypatched
  to return a path; asserts it's used.
- `test_resolved_asinfo_path_returns_none_when_binary_missing` —
  `shutil.which` monkeypatched to return `None`; asserts `None`.
- `test_build_args_includes_host_and_port` — asserts `-h`/`-p`/`-v` are
  present with the expected values for `["statistics"]`.
- `test_build_args_joins_multiple_commands_with_newline` — same, for
  `["namespaces", "namespace/test"]` → single `-v "namespaces\nnamespace/test"`.
- `test_build_args_adds_username_password_flags_when_both_set` — asserts
  `-U`/`-P` appear only when both `config.username` and `config.password`
  are non-empty, and are absent when either is unset (§3.3).
- `test_send_info_commands_invokes_subprocess_with_timeout` — mock
  `subprocess.run`, assert `timeout=config.timeout_seconds` is passed
  through and the returned dict/string matches the mocked stdout.
- `test_send_info_commands_raises_or_errors_on_binary_not_found` — mock
  `shutil.which` to return `None`; assert the distinct "binary not found"
  failure mode (§1.3), not a generic connection error.
- `test_send_info_commands_errors_on_nonzero_exit` — mock `subprocess.run`
  to return a non-zero `returncode` with stderr text; assert it's surfaced
  as a distinguishable error, not silently treated as empty data.
- `test_send_info_commands_errors_on_timeout` — mock `subprocess.run` to
  raise `subprocess.TimeoutExpired`; assert the distinct timeout failure
  mode (§1.3), matching `HelmClient._run()`'s
  `except subprocess.TimeoutExpired` handling.
- `test_parse_semicolon_kv_realistic_statistics_fixture` — realistic
  `statistics` value fixture, e.g.
  `"cluster_size=3;cluster_key=BB9;uptime=48213;cluster_integrity=true;"`
  → assert every key parses, including the **trailing semicolon** (must not
  produce a spurious empty-string key).
- `test_parse_semicolon_kv_handles_value_without_trailing_semicolon` —
  fixture with no trailing `;`.
- `test_parse_semicolon_list_realistic_namespaces_fixture` — `"test;bar"` →
  `["test", "bar"]`; and a single-namespace fixture `"test"` (no `;` at
  all) → `["test"]` (must not require a delimiter to be present).
- `test_parse_semicolon_kv_ignores_malformed_segment` — a fixture with one
  segment missing `=` (defensive: real clusters sometimes emit odd
  diagnostic strings in edge fields) — parser must skip that segment, not
  raise or drop the whole result.
- `test_parse_latency_degrades_on_unrecognized_shape` — feed the parser
  something that is *not* the assumed `latencies:`/`latency:` grammar and
  assert it returns the `{"raw": ...}` degraded shape rather than raising —
  this test encodes the version-boundary uncertainty flagged in §9 as an
  actual regression guard.
- `test_parse_multi_command_stdout_splits_on_name_tab_value` — realistic
  multi-command `asinfo -v "statistics\nnamespaces"` stdout fixture
  (`"statistics\t...;\nnamespaces\ttest;bar\n"`); assert it splits into the
  per-command raw-value dict.
- `test_parse_single_command_stdout_has_no_name_prefix` — realistic
  single-command `asinfo -v status` stdout fixture (bare `ok\n`, no
  `status\t` prefix); assert the parser handles both stdout shapes
  described in §1.2.

### 7.2 `tests/integrations/test_aerospike.py`

- `TestAerospikeConfig` — host/username/password whitespace stripping
  (mirrors `TestRedisConfig`), `is_configured` true/false, port bounds
  (`ge=1, le=65535`) rejection.
- `TestAerospikeBuild` — `build_aerospike_config(None)` returns a valid
  empty config; `aerospike_config_from_env()` returns `None` when
  `AEROSPIKE_HOST` unset, returns a populated config when set, and reads
  the password via `resolve_env_credential` (monkeypatched, asserting it
  is *not* read via bare `os.getenv`).
- `TestAerospikeExtractParams` — `aerospike_extract_params(sources)` shape.
- `TestAerospikeValidation` — `validate_aerospike_config`: success path
  (fake `send_info_commands` returning a `status` value), connection-refused
  path, timeout path, malformed-response path — each asserting the
  `AerospikeValidationResult.ok`/`detail` shape, mirroring
  `TestRedisValidation`.
- `TestResolveIntegrations` — `classify()` unit tests: valid credentials →
  `(AerospikeIntegrationConfig, "aerospike")`; missing host →
  `(None, None)`; malformed input → `(None, None)` with
  `report_classify_failure` invoked (mirrors the Redis equivalent class).

### 7.3 Per-tool test files

`tests/tools/test_aerospike_node_status_tool.py`,
`test_aerospike_namespace_stats_tool.py`, `test_aerospike_latency_tool.py`
— each mirroring `test_redis_server_info_tool.py`'s shape:
- success path with a realistic fixture response
- `is_available`/`extract_params` wiring test
- `injected_params` override test (host from integration wins over any
  model-supplied value, per the credential-resolution contract's tools
  rule)
- failure paths: `asinfo` binary not found, connection failure, auth
  failure (see §9 — with `-U`/`-P` wired in, this test should assert
  whatever distinction the implementation can actually make between
  "couldn't connect" and "connected but credentials were rejected" based
  on `asinfo`'s exit code/stderr; if that distinction turns out not to be
  reliably parseable, document that as the *current* v1 behavior rather
  than assuming it up front), subprocess timeout, malformed/truncated
  response (reuses `client.py`'s fixtures via a monkeypatched
  `send_info_commands`)
- `get_aerospike_namespace_stats` additionally needs a
  `test_namespace_stats_truncates_when_exceeding_max_results` case.

### 7.4 Registry/discovery test

A test (in `tests/integrations/test_aerospike.py` or a dedicated
`tests/tools/test_tool_registry_aerospike.py`, matching whatever pattern
the existing Redis registry-discovery test uses) proving all three tools
are discovered by the tool registry and appear with
`surfaces=("investigation", "chat")`.

### 7.5 `tests/e2e/aerospike/` — mocks at the transport boundary, no live server

**Confirmed by the architecture review, not deferred as an open
question.** `tests/e2e/redis/test_redis_e2e.py` (339 lines) never starts a
live Redis instance or container — every test patches the client
construction seam directly (`@patch("integrations.redis._get_client")`
returning a `MagicMock`) and asserts on config resolution, verification,
and tool-path wiring against that mock. There is no Docker/container
fixture anywhere in the redis e2e path.

`tests/e2e/aerospike/` follows the same pattern: mock
`integrations.aerospike.client.send_info_commands` (or, for tests that
want to exercise `client.py`'s own subprocess handling, mock
`subprocess.run` directly) with fixture stdout/stderr/returncode values,
and assert on config resolution, verification, and tool-path wiring
against that mock — exactly like the Redis e2e suite does. **No real or
containerized Aerospike server is needed for v1's e2e suite**; this
removes what the original raw-socket draft flagged as an open question
(§9 used to list "e2e/live-cluster test infrastructure" as unresolved —
it no longer is).

## 8. Docs

### 8.1 `docs/aerospike.mdx` outline (mirrors `docs/redis.mdx`, plus a
binary-prerequisite note in the style of `docs/helm.mdx`'s "Helm 3
installed and on `PATH`" bullet and `docs/llm-providers.mdx`'s "CLI
providers (subprocess)" section)

```
---
title: "Aerospike"
description: "Connect Aerospike so OpenSRE can diagnose node, namespace, and latency issues during investigations"
---

<intro paragraph — what this integration diagnoses, and the explicit note
 that this integration is read-only diagnostics run through the `asinfo`
 CLI, not record-level access>

## Prerequisites
- **`asinfo`** (from the `aerospike-tools` package) installed and on
  `PATH` in the environment running OpenSRE — this is an **external binary
  dependency**, not a Python package; OpenSRE detects it on `PATH` the same
  way it detects Helm (`docs/helm.mdx`) or a CLI-backed LLM provider
  (`docs/llm-providers.mdx` → "CLI providers (subprocess)"). Install via
  the OS package matching your platform (e.g. the `aerospike-tools`
  `.deb`/`.rpm`/Homebrew package from Aerospike's own download page) — it
  ships `asinfo`/`asadm` together.
- Aerospike Community or Enterprise Edition, any version exposing the
  standard info protocol on port 3000 (or a custom port)
- Network access from the OpenSRE environment to the Aerospike node(s)
- Security-enabled clusters: supply `AEROSPIKE_USERNAME`/
  `AEROSPIKE_PASSWORD` — passed to `asinfo` as `-U`/`-P` (§3.3). TLS is not
  yet supported — see "Known limitations" below.

## Setup
### Option 1: Interactive CLI
opensre integrations setup   (select Aerospike)

### Option 2: Environment variables
AEROSPIKE_HOST=
AEROSPIKE_PORT=3000
AEROSPIKE_USERNAME=
AEROSPIKE_PASSWORD=

| Variable | Default | Description |  (same table shape as redis.mdx lines 37-44)

### Option 3: Persistent store
(same JSON example shape as redis.mdx, service: "aerospike")

## Tools
- get_aerospike_node_status
- get_aerospike_namespace_stats — note per-namespace figures are
  single-node/local, not cluster-wide aggregates (§4.2)
- get_aerospike_latency
(one subsection each: what it returns, example output)

## Known limitations
- Requires the `asinfo` binary (`aerospike-tools` package) on `PATH` —
  see Prerequisites above
- `get_aerospike_namespace_stats` reports the connected node's local
  figures, not cluster-wide totals (§4.2)
- Record-level (KV) access is intentionally out of scope — this
  integration only surfaces cluster/node/namespace diagnostics
- TLS not yet supported — TLS-enabled Aerospike clusters commonly use a
  **separate listener port** from the plaintext service port (e.g.
  plaintext `3000` + TLS `4333`), so this is not simply a config flag flip
  once added (§9)
```

**Deploy note (Dockerfile/deploy docs):** any container image or deploy
target running this integration must install `aerospike-tools` alongside
the existing OS package set — mirroring how `docs/llm-providers.mdx`
documents CLI-backed LLM providers as an external binary the runtime must
have available, not a `pip`-installed dependency. This is a deploy-time
package addition (e.g. an `apt-get install aerospike-tools` line in the
Dockerfile's existing package-install step), not something this design doc
implements — flagged here so the implementation PR doesn't ship
`client.py` without the corresponding Dockerfile/deploy-docs update.

### 8.2 `docs/docs.json` registration

Add `"aerospike"` to the integrations nav array, alongside the existing
`"redis"` entry (line 220) — insert alphabetically near the top of that
list (it currently reads `..., "azure-sql", "clickhouse", "dagster",
"elasticsearch", "kafka", ...` — `"aerospike"` sorts before `"azure-sql"`).

## 9. Risks / open questions (for Aerospike domain expert review)

**Revised 2026-08-09.** The original raw-socket draft's byte-framing and
protocol-uncertainty risks (proto header layout, bcrypt/fixed-salt auth
handshake byte-level correctness) are **moot** — `asinfo` owns all of that
internally now, and this design has no wire-protocol code left to get
wrong. What remains open is specific to the CLI-wrapper transport and to a
couple of findings from the two review sections below that are still
unresolved design questions rather than settled corrections.

1. **`asinfo` binary discovery / `PATH` handling.** The resolution order
   (config-supplied override path, then `PATH` lookup via `shutil.which`)
   is modeled on `HelmClient._resolved_helm_path()`, but hasn't been
   validated against how `asinfo` itself expects to be invoked (e.g.
   whether it needs to run from a particular working directory, whether it
   respects a config file that could conflict with explicit CLI flags).
   **Action requested / confirm at implementation time:** run `asinfo
   --help` and a real `asinfo -h <host> -p 3000 -v statistics` invocation
   to confirm the flag set assumed in §3.2 (`-h`, `-p`, `-v`, `-U`, `-P`)
   is exactly right and that no additional required flags exist.

2. **Subprocess timeout behavior under real network conditions.**
   `config.timeout_seconds` (default 5s, §2.2) is passed straight to
   `subprocess.run(timeout=...)`. This kills the `asinfo` process on
   timeout but the exact signal/cleanup behavior (does `asinfo` leave a
   half-open TCP connection to the server if killed mid-request?) hasn't
   been verified. Low risk given `asinfo` is a short-lived CLI invocation,
   but worth a real smoke test before treating the default as final for
   slow/degraded clusters.

3. **stderr/exit-code handling — distinguishing "can't connect" from
   "connected but the command errored."** §1.3 calls for these to be
   surfaced as distinct failure modes (a connection failure means "check
   network/host/port," a command-level failure might mean "check
   credentials" or "namespace doesn't exist"), but this plan does not have
   a verified table of `asinfo`'s exit codes/stderr text for each case
   (connection refused, DNS failure, auth rejected, unknown command,
   nonexistent namespace). **Action requested:** capture real `asinfo`
   output for each of these failure cases (a deliberately wrong host, a
   deliberately wrong port, a bad `namespace/<ns>` name, and — if a
   security-enabled test cluster is available — a bad `-U`/`-P`) so
   `client.py`'s error classification (§4.4) can be built against ground
   truth instead of guessed exit-code ranges.

4. **Output-parsing parity between `asinfo` stdout and a raw info-protocol
   response.** This design's core assumption is that `asinfo -v <command>`
   writes the exact same `name\tvalue` (or bare-value, for a single
   command) text a raw socket read of the info protocol would have
   produced. This is expected to be true — `asinfo` is understood to be a
   thin client over the same protocol — but has not been confirmed with a
   side-by-side capture. **Action requested:** worth a one-time smoke test
   comparing `asinfo -v "statistics\nnamespaces"` stdout against a packet
   capture of the equivalent raw info-protocol exchange, to confirm there's
   no `asinfo`-specific reformatting (e.g. added whitespace, reordered
   fields, a wrapping prefix/suffix) that the parser needs to account for.

5. **`latencies:`/`latency:` command grammar across server versions.**
   Unchanged from the original draft's concern, now scoped correctly per
   the domain-expert review: target `latencies:` (plural) as the primary
   command for current server versions, fall back to legacy `latency:` +
   the degrade-to-raw-text parser for older servers. §4.3 and §7.1's
   `test_parse_latency_degrades_on_unrecognized_shape` make this safe to
   get wrong initially. **Action requested:** a real `latencies:` response
   sample (or the target deployment's server version) would let this tool
   return properly structured data instead of the degraded fallback for
   the common case.

6. **TLS.** `tls_enabled` is modeled in the config (§2.2) but not wired
   into `client.py` in v1. With the CLI wrapper this is now just adding
   `--tls-enable` and related `asinfo` flags (`--tls-name`,
   `--tls-cafile`/`--tls-capath`) to the argument list — mechanically
   simple, unlike the from-scratch `ssl.SSLContext` work the raw-socket
   design would have needed. Deferred anyway because TLS-enabled Aerospike
   clusters commonly use a **separate listener port** from the plaintext
   service port, which this design's single `AEROSPIKE_PORT` field doesn't
   yet model — see the domain review's TLS verdict below.

7. **Multi-command batching for `get_aerospike_namespace_stats`.** §4.2
   proposes batching all `namespace/<ns>` requests into one `asinfo -v
   "namespace/ns1\nnamespace/ns2..."` invocation rather than one
   subprocess per namespace. The domain-expert review confirmed this is
   safe and standard practice (`asadm` does the same). No open question
   here; kept in this list only as a pointer to §4.2's cluster-scope
   caveat, which is the more important thing to get right for this tool.

None of the above blocks writing the config/registry/CLI wiring (§2, §5,
§6) or the response-shape/tool-metadata design (§4) — those don't touch
`asinfo` at all and follow the Redis pattern closely enough to be low-risk.
The risk is concentrated in `client.py`'s subprocess-invocation and
error-classification logic (§1, §3) and in `_parse_latency`'s
version-boundary handling (§4.3) — both should get a real smoke test
against an `asinfo` binary and a live (or containerized) Aerospike node
before shipping, even though no test in the automated suite (§7) requires
one.

## Aerospike Domain Expert Review

Reviewed by: technical review pass focused exclusively on Aerospike
wire-protocol/behavior claims in §1, §3, §4, and §9 above. No other files in
the repo were read for this review; this is not a code review.

### Verdicts on the §9 open questions

**1. Proto header byte layout — CORRECT.** `version = 2`, `type = 1` for an
info request, `size` as a big-endian 48-bit unsigned payload length packed
into the low 48 bits of the same 8-byte big-endian word, is the correct
layout. This matches the shared `as_proto` header used by every Aerospike
client generation (C, Go, Python, Java) for **all** protocol families on
that TCP port, not just info: `PROTO_TYPE_INFO = 1`, `PROTO_TYPE_SECURITY
(admin/auth) = 2`, `PROTO_TYPE_AS_MSG (binary transaction protocol) = 3`.
One useful fact the doc doesn't state explicitly but which validates its
own design: **info, admin/auth, and the binary transaction protocol all
share the same TCP port** (`3000` by default) — they are multiplexed
purely by the `type` byte in this shared header, not by a separate
listener. So `AEROSPIKE_PORT` doubling as "the" port for this integration
is correct and will still be correct if auth (§9 item 2) or TLS (§9 item
4) are added later on the same port.

Despite being confident in the layout, the doc is right to keep the
tcpdump/`socat -x` verification step as a mandatory pre-implementation gate
— not because the layout is likely wrong, but because the failure mode of
a wrong header (client hangs waiting for bytes the server never sends) is
too expensive to risk on "probably right." Keep §9 item 1 as a hard gate.

**2. Authentication handshake — the "defer to v1 follow-up" call is
correct, but the doc understates how much work the follow-up actually is.**
Security-enabled clusters authenticate over the **same port**, using proto
`type = 2` (admin/security), not `type = 1`. The admin message body is a
different, binary, field-tagged format — not newline/tab text like info:
one op-code byte (`AUTHENTICATE`, or `LOGIN` on newer server versions),
a field count, then `(field-id, length, value)` tuples for `USER` and
`CREDENTIAL`. Two details that materially affect a Python-stdlib-only
implementation:

- **Credentials are not sent as plaintext or a simple digest.** The
  client must bcrypt-hash the password client-side using a **fixed,
  publicly known salt** before sending it as the `CREDENTIAL` field
  (every open-source Aerospike client hardcodes the same salt constant for
  this — the client computes the same bcrypt hash the server would, so the
  expensive bcrypt work happens once and matches on comparison). This
  means basic auth is **not** "send username/password" — it requires a
  bcrypt implementation, and bcrypt is normally provided by the `bcrypt`
  PyPI package, which itself ships a compiled (Rust/C) extension. That
  reintroduces almost the same "native extension in the deploy target"
  risk this whole design was built to avoid by not using the official
  `aerospike` client. A pure-Python bcrypt fallback exists but is slow and
  non-standard; either way this is a real dependency decision, not a free
  add-on.
- **Newer server versions (roughly 4.9+) added a `LOGIN` op that returns a
  session token** (with a TTL) instead of requiring a fresh bcrypt
  handshake on every connection. This is where the design's "fresh
  TCP connection per tool call, no pooling" choice (§1.1, §3.2) starts to
  hurt once auth lands: without connection/session reuse, every future
  authenticated tool call would either re-pay the bcrypt cost per call
  (server-side CPU, since bcrypt is deliberately slow) or need to persist
  and reuse a session token across the stateless per-call design, which is
  a structural change to `client.py`, not an additive one.

Net: deferring auth to v1 is still the right call, but the design doc
should not describe it as purely additive (§9 item 2's last sentence). The
config fields (`username`/`password`) are additive; the *transport* work
(type=2 admin protocol, bcrypt+fixed-salt, optional session-token reuse)
is a second protocol implementation, comparable in size to the info-protocol
client itself, and the "one-shot connection" architecture will need
revisiting to make it performant. Flag this explicitly before scoping the
follow-up.

**3. `latency:` command grammar — INCOMPLETE, not just uncertain.** The
plan should not treat `latency:` as the target command for "recent server
versions." Aerospike replaced its latency-histogram subsystem around
server 4.9, and the modern info command is **`latencies:`** (plural), not
`latency:`. `latency:` is the legacy command and its grammar is the one
that has shifted across versions and is genuinely awkward to parse
reliably; `latencies:` is the stable, documented, currently-supported
command (it's what `asadm`'s `show latency` uses today) and takes
optional params (e.g. `latencies:hist={<namespace>}-read`,
`latencies:back=600`) rather than being parameter-less. **Recommendation:**
target `latencies:` explicitly as the primary command for server versions
that support it, keep the degrade-to-raw-text fallback for older servers
still only exposing `latency:`, and don't present this as one grammar
question — it's actually "two different commands across a version
boundary," which is a stronger reason to keep the defensive
`_parse_latency` design than the doc currently gives credit for.

**4. TLS — reasonable to defer implementation, but document one gotcha even
in v1.** Aerospike TLS is very commonly configured with a **separate
listener port** from the plaintext service port (e.g. plaintext `3000` +
TLS `4333`, both configurable via `network.service.tls-port` /
`network.service.port`), not the same port with an opportunistic TLS
handshake layered on top. So "wrap the existing socket in
`ssl.SSLContext`" is necessary but not sufficient — a real TLS-enabled
deployment also needs a distinct port (and typically a `tls-name` value
used for certificate hostname validation, since Aerospike is a clustered
system with per-node TLS identity). This doesn't change the "defer to
follow-up" decision, but §8.1's "Known limitations" doc text should say
"not yet supported" rather than implying it's a small follow-up — add a
note that TLS clusters commonly use a different port than the one
configured here, so operators don't assume flipping `AEROSPIKE_TLS=true`
against port 3000 will ever work without further config surface.

**5. Multi-command batching — CORRECT, low risk.** Aerospike's info
protocol is designed for exactly this — `asadm` itself routinely batches
many info commands into a single request/response round trip. There is no
publicly documented hard limit relevant at the scale this tool would ever
hit (a namespace count in the tens, capped at `config.max_results` per the
plan). Batching `namespace/<ns>` calls is safe as designed; no change
needed.

**6. `"as"` keyword collision risk — not a protocol question, but the
doc's own instinct is correct.** Recommend dropping `"as"` from the
keyword table unless the matcher is confirmed to do whole-token matching.
Out of scope for this protocol review to verify the matcher's behavior.

**7. e2e test infra — not a protocol question.** Using an official
`aerospike/aerospike-server` (or the separate `aerospike/aerospike-server-community`)
Docker image for a containerized CI fixture is standard practice and is
what most third-party Aerospike client test suites do; it ships with
security disabled by default, which conveniently matches this plan's v1
scope (unauthenticated only). Confirm the image tag pin (per the repo's
container-versioning rule) when this file is written — not a protocol
concern.

### Sanity-check of the rest of the plan's Aerospike-specific claims

**1. Wire protocol framing — CORRECT**, per the header-layout verdict
above. The request/response shape (fixed 8-byte header, then exactly
`size` bytes of payload, no trailing sentinel, loop `recv()` until `size`
bytes are read) is the right mental model and matches how every info-only
client (including `asinfo` itself) talks to the server. The doc's
`_recv_exact` loop-until-N-bytes design is the correct defensive pattern
for this framing.

**2. Response grammar — CORRECT for `statistics`, `namespace/<ns>`,
`namespaces`, and `status`.** `key=value;`-repeated for `statistics` and
`namespace/<ns>` is right, including the common trailing semicolon that
must not produce a spurious empty key. `namespaces` as a bare
`;`-separated list of namespace names (no `=`) is right. `status` as a
bare string (`ok`) with no delimiter structure is right. The one grammar
claim in the doc that needs correcting is `latency:` — see verdict #3
above: the doc should target `latencies:` as primary, not treat `latency:`
as the forward-looking command name.

**3. Authentication — see verdict #2 above.** Scoping v1 to
unauthenticated clusters is the right call; basic auth is **not** cheap
enough to bundle into v1 given the bcrypt+fixed-salt requirement and the
tension it creates with the "no native extension" goal that motivated this
whole raw-socket design in the first place.

**4. `latency:` — see verdict #3 above.** Target `latencies:` explicitly
for current server versions; keep `latency:` + the raw-text degrade path
only as a fallback for older servers.

**5. TLS — see verdict #4 above.** Deferring is fine; document the
separate-port reality now so the "Known limitations" section doesn't
undersell the gap.

**6. Missing gotchas:**

- **Namespace stats are per-node, not cluster-wide — this is the biggest
  gap in the plan and isn't mentioned anywhere in §4.2 or §9.** A single
  node's `statistics` response (`cluster_size`, `cluster_key`,
  `cluster_integrity`) genuinely is a cluster-wide value reported
  identically by every node, so reading it from one node is fine. But a
  single node's `namespace/<ns>` response (`objects`/`memory_used_bytes`/
  `device_used_bytes` and similar) reflects **only that node's local
  partition share** of the namespace, not the cluster-wide total — in a
  multi-node cluster each node holds roughly `1/N` of the master
  partitions (plus replicas, which the server also reports separately in
  some fields, e.g. master vs. replica object counts). §4.2's illustrative
  response shape (`"objects": 10432`) will silently read as a cluster-wide
  object count to anyone consuming the tool's output, when on a multi-node
  cluster it's actually just one node's local share. Either (a) document
  explicitly that these figures are single-node/local, not cluster
  aggregates, or (b) fan out to every cluster member (discoverable via the
  `peers`/`services`-style info commands from the first connection) and
  sum. Given this is a v1 read-only diagnostics tool, (a) — document the
  limitation — is the pragmatic choice, but it must be stated; right now
  the design silently implies single-node figures are the whole picture.
- **Connection reuse vs. one-shot — fine as designed.** A fresh TCP
  connection per tool call for unauthenticated info commands matches how
  `asinfo` itself operates and carries no real inefficiency at info-protocol
  scale (this is a lightweight text handshake, not a per-record operation).
  No change needed for v1. This will need revisiting once auth/session
  tokens are added (see verdict #2).
- **Timeout behavior under load — the design's approach (fixed
  `socket.settimeout`, treat a timeout/short read as an error rather than
  a silent partial result) is the right one.** The info port on a real
  node is served by its own lightweight service thread, separate from the
  transaction/query threads that handle client KV traffic, so it typically
  stays responsive even when the node is under heavy read/write load or in
  a `stop-writes` state — but it is not immune to slowness during a
  cluster-wide event (e.g. rebalancing after a node join/leave, or a fully
  saturated node running out of file descriptors). A single fixed
  `timeout_seconds` (default 5s) with an explicit "treat timeout as a
  distinguishable error, not empty data" is appropriate for a diagnostics
  tool and needs no change.

### Summary

Must fix before implementation: (1) `latency:` should become `latencies:`
as the primary target command for current server versions, with
`latency:` kept only as an older-version fallback; (2) §4.2 must state
explicitly that per-namespace figures are single-node/local, not
cluster-wide aggregates, or add multi-node fan-out — as written it's
silently misleading. Confirm-before-coding, unchanged from the doc's own
flag: the proto header layout (very likely correct as reconstructed, but
still worth a real packet capture given the hang-on-wrong-byte risk).
Reasonable as scoped: deferring auth (though the "additive follow-up"
framing undersells the bcrypt/fixed-salt/session-token work involved,
which also reintroduces a native-extension dependency question) and
deferring TLS (document the separate-port gotcha now). Fine as designed:
wire framing, `statistics`/`namespace/<ns>`/`namespaces`/`status` grammar,
multi-command batching, one-shot connections, and the timeout strategy.

## OpenSRE Architecture Review

Reviewed against `AGENTS.md`, `docs/adding-tools-and-integrations.md`,
`docs/NAMING.md`, `config/strict_config.py`, and the live `redis` precedent
(`integrations/registry.py`, `integrations/effective_models.py`,
`integrations/_catalog_impl.py`, `integrations/cli.py`,
`integrations/alert_source_catalog.py`, `integrations/redis/`,
`config/constants/redis.py`, `config/constants/__init__.py`,
`tests/e2e/redis/test_redis_e2e.py`, `docs/docs.json`). This is a
conventions/structure review only — wire-protocol correctness is the
Aerospike Domain Expert Review section above.

### Must fix before implementation

**1. `setup_order=31` and `verify_order=42` (§5.1) both collide with an
existing entry — `pagerduty` already uses `setup_order=31,
verify_order=42`** (`integrations/registry.py`, its `IntegrationSpec` block
for `service="pagerduty"`). A scripted scan of every `setup_order=`/
`verify_order=` value in the file confirms `31` and `42` are both taken;
neither is "the next free slot after redis's 30/41" as the doc claims —
redis's neighbors (`groundcover=35/46`, `betterstack=2/18`, etc.) are not
contiguous with redis's own numbers, so "next free" can't be assumed by
incrementing. These fields aren't uniqueness-enforced (`sorted(..., key=...)`
just ties on insertion order), so a collision won't crash anything, but it
silently interleaves Aerospike into pagerduty's menu/verify-order slot,
which is exactly the kind of thing that should be deliberate, not
accidental. Genuinely free values as of this review: any `setup_order`
in `{44, 45, 46, 47, 48, 49, 50, 52, 53, ...}` and any `verify_order`
≥ `58` (up to `99`, which `supabase` currently uses as a high-water mark).
Recommend `setup_order=44, verify_order=58` (or re-scan at implementation
time if more integrations have landed by then).

**2. Missing touchpoint: `config/constants/__init__.py` is not in the §5
file-by-file table.** Per `AGENTS.md` Code Style, env-var constants belong
in a `config/constants/` leaf module "re-exported via
`config/constants/__init__.py`." Confirmed against the redis precedent —
`config/constants/__init__.py` imports and re-exports all six
`REDIS_*_ENV` names (import block + `__all__` entries). The design's new
`config/constants/aerospike.py` (§2.1) needs the identical treatment: an
import block and six `__all__` entries added to
`config/constants/__init__.py`. As written, the plan would leave the new
constants unreachable through the canonical re-export path that every
other integration uses.

**3. §9 item 7 (e2e/live-cluster test infra) is answered by evidence
already in the repo — it should not ship as an open question.**
`tests/e2e/redis/test_redis_e2e.py` (339 lines) never starts a live Redis
instance or container. Every test in it patches the client-construction
seam directly — `@patch("integrations.redis._get_client")` returning a
`MagicMock` — and asserts on config resolution, verification, and
tool-path wiring against that mock. There is no Docker/container fixture
anywhere in the redis e2e path. So "mirrors `tests/e2e/redis/`" does not
actually require a containerized Aerospike Community Edition image — the
correct mirror is patching `send_info_commands` (or the socket-level
`_recv_exact`/`_read_response` functions) the same way redis patches
`_get_client`, with fixture byte/text payloads driving the tool paths.
Recommend rewriting §5's `tests/e2e/aerospike/` row and §7.5/§9-item-7 to
drop the "needs a real or containerized server" framing and instead
describe a mock-at-the-transport-boundary e2e test, consistent with what
`tests/e2e/redis/` actually does — this removes one of the doc's flagged
open questions entirely rather than deferring it.

**4. The `"as"` keyword removal (§5.4b / §9 item 6) is not a "judgment
call" — it is a confirmed bug if shipped as originally proposed, and the
doc's own recommendation to drop it is correct and should be treated as
required.** `relevant_sources_for_alert()` in
`core/domain/alerts/alert_source.py` does substring matching, not
whole-token matching: `if any(keyword in text for keyword in keywords)`
against `collect_alert_text(state)` (a lowercased, space-joined blob of
alert name/message/raw-alert fields). A two-character keyword `"as"` would
match inside "**as**k", "cl**as**s", "incre**as**e", "dat**as**et",
"t**as**k", and any other alert text containing that substring — it would
fire on nearly every alert. Ship with only `("aerospike", "namespace")` as
proposed as the fallback; do not gate this on further confirmation, the
matcher is confirmed substring-based.

### Already conventions-compliant (verified, not just plausible)

- **`AerospikeConfig(StrictConfigModel)` (§2.2) is the right base class.**
  Matches `RedisConfig` exactly — both are non-relational stores with no
  `database`/schema concept at the connection level, so neither uses
  `integrations/config_models.py`'s relational base. Field shape (`host`,
  `port` with `Field(ge=1, le=65535)`, `username`, `password`,
  `timeout_seconds`, `max_results`, `integration_id`) mirrors
  `RedisConfig` (`integrations/redis/__init__.py` lines 47–77) field for
  field, including the same `_normalize_*` validator pattern.
- **Credential resolution table (§6) is correct against
  `config/env_file.py`'s actual `is_sensitive_env_key` logic.**
  `_SENSITIVE_TERMINAL_TOKENS` matches on the underscore-terminal token
  (`password`, `secret`, `token`, `key`, `apikey`, `credential`,
  `credentials`), not substring — confirmed `AEROSPIKE_USERNAME` (terminal
  `username`) and `AEROSPIKE_HOST`/`AEROSPIKE_PORT` do **not** false-positive
  into keyring eligibility, and `AEROSPIKE_PASSWORD` (terminal `password`)
  correctly does. This exactly matches `REDIS_PASSWORD_ENV`'s handling
  (`config/constants/redis.py` + `integrations/redis/__init__.py` line 106,
  `resolve_env_credential(REDIS_PASSWORD_ENV)`), and the design's
  `aerospike_config_from_env()` plan correctly commits to the same
  `resolve_env_credential` call rather than bare `os.getenv` for the
  password field.
- **`integrations/aerospike/client.py` as a separate file from
  `__init__.py` is the right split, not a deviation from the redis
  precedent — the two integrations aren't structurally comparable here.**
  Redis has **no** `client.py` because `_get_client()` is a 20-line
  wrapper that hands off all transport/protocol handling to the `redis-py`
  library (`integrations/redis/__init__.py` lines 113–132) — there is no
  "transport layer" to separate out, it's a constructor call. Aerospike has
  no library at all; `client.py` *is* the transport layer (proto framing,
  `recv()`-loop byte assembly, header packing/unpacking) — this is exactly
  the case `docs/adding-tools-and-integrations.md` §2's file list
  anticipates with `integrations/<name>/client.py` — "a dedicated API
  client, **when the integration makes direct remote calls**." Redis
  doesn't make direct remote calls (the library does); Aerospike's
  `client.py` does. Keeping per-command grammar parsing
  (`_parse_semicolon_kv`, etc.) in `__init__.py` next to the tool-facing
  normalization functions, and keeping only proto-framing/socket I/O in
  `client.py`, also correctly follows the "transport and per-tool
  normalization are separate concerns, separately testable" split the doc
  itself calls out.
- **`injected_params=("host",)` (§4.1) is correct** — verified against
  `integrations/redis/tools/redis_server_info_tool/__init__.py` line 34:
  redis's tools inject only `host`, not the full credential set, and the
  design's tool signatures (`host`, `port`, `username`, `password`, ...)
  match the same shape as `get_redis_server_info`'s parameter list.
- **`AerospikeIntegrationConfig` (§2.3) correctly omits `max_results`**,
  matching `RedisIntegrationConfig` (`integrations/config_models.py` lines
  600–613) — `max_results` is a tool-side response cap that belongs on the
  `*Config` model (`AerospikeConfig`/`RedisConfig`), not the
  store/verification-facing `*IntegrationConfig`. Correctly mirrored.
- **`docs/docs.json` alphabetical placement (§8.2) is correct** — the
  actual "Data and workflow systems" `pages` array reads `airflow,
  azure-sql, clickhouse, dagster, elasticsearch, kafka, mariadb, mongodb,
  mongodb-atlas, mysql, openclaw, opensearch, postgresql, prefect,
  rabbitmq, rds, redis, snowflake, supabase, temporal` — `"aerospike"`
  sorts immediately after `"airflow"` and before `"azure-sql"`, exactly as
  the doc states. This item is correctly *not* missing from the plan
  (unlike #2 above) — good catch on the doc's part, since a doc page not
  registered in `docs.json` is a named repo footgun in `AGENTS.md`.
- **Single-file tool packages under
  `integrations/aerospike/tools/<name>_tool/__init__.py` (§4) match the
  redis precedent exactly** — every redis tool package
  (`redis_server_info_tool/`, `redis_slowlog_tool/`, etc.) is a single
  `__init__.py` with `@tool(...)` metadata plus a thin wrapper function
  that builds a config object and delegates to `integrations/redis/`'s
  normalization functions. No sibling files needed there because — as
  with redis — validation/transport/parsing already live in
  `integrations/aerospike/{client.py,__init__.py}`, leaving each tool file
  as pure metadata + delegation, consistent with
  `docs/adding-tools-and-integrations.md` §1's "tool packages must be
  substantive… unless the concerns are already factored out."
  `integrations/redis/tools/__init__.py` is empty (pure discovery
  namespace) — the design correctly does not propose registering tools
  manually anywhere.
- **`docs/NAMING.md` is out of scope for this design** — its glossary and
  `{domain}_{role}.py` rule are explicitly scoped to `core/` (its own
  first line: "Naming conventions for `core/`"). Nothing in this plan
  touches `core/`, so there's no naming-convention violation to check
  there; the file-naming pattern this design does follow
  (`client.py`/`setup.py`/`verifier.py` as role-named siblings) is an
  `integrations/`-package convention already established by redis, not a
  NAMING.md-governed one, and it's followed correctly.
- **Test plan (§7) satisfies `docs/adding-tools-and-integrations.md` §5
  and correctly prioritizes fixture-based parser tests.** All five
  required categories are present: config/normalization unit tests
  (§7.2), tool contract tests (§7.3), a registry/discovery test (§7.4),
  runtime success+failure tests (§7.1 `send_info_commands` close-on-error,
  §7.3 connection-refused/auth/malformed paths), and realistic fixture
  tests (§7.1's `statistics`/`namespaces` fixtures, explicitly including
  the trailing-semicolon and no-trailing-semicolon edge cases). §7.1 is
  correctly called out as "highest priority" ahead of the other test
  files — appropriate given `client.py` is both the highest-bug-risk code
  (per the task's own framing) and, per the doc's own observation, the
  most network-independent code to test (pure fixture-in/dict-out, no
  socket needed for the parser-level tests).

### Minor / non-blocking

- `AEROSPIKE_TLS_ENV` is declared in `config/constants/aerospike.py`
  (§2.1) with no consumer anywhere in v1 (`tls_enabled` isn't wired into
  `client.py`, and there's no `TLS_FIELD` in the proposed
  `AEROSPIKE_SETUP` spec in §6). Not a violation of any stated rule (the
  doc explains the forward-compatibility intent explicitly), but worth a
  one-line note in the PR description so a reviewer doesn't flag it as
  dead code — `vulture`-style dead-constant scans won't catch an unused
  *env var name* the way they would an unused function, but a human
  reviewer skimming `config/constants/aerospike.py` will reasonably ask
  why it's there.
