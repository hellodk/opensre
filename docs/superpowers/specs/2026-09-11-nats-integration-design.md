# NATS Integration — Architecture & Implementation Spec

Status: **design only — no implementation code has been written.** This
document is the complete brief for whoever implements the integration. It is
written so the implementer does not need to explore the repo: every file to
create or modify is named, every contract is spelled out, and every payload
shape comes from fixtures captured against a real nats-server 2.14.6
(three-node JetStream cluster plus one standalone server). The fixture files,
and the scripts that produced them, are committed next to this spec under
`docs/superpowers/specs/nats/` (see §8); the implementer copies the fixtures
into the test tree.

- Ticket: https://github.com/hellodk/opensre/issues/5 (PR must say `Closes #5`)
- Branch: `feat/nats-integration`, checked out as a git worktree at
  `../opensre-nats` (sibling of the main checkout) with its own `.venv`
  created by `uv sync`. Commit there. Base commit: `55627a7c9` (`main`).
  All line numbers below refer to that commit.
- Process rules that apply (from the repo owner's global instructions):
  1. Write the failing tests first (§9), run them, confirm they fail for the
     right reason, then implement (§3–§7), then make them pass.
  2. Never put any AI/assistant attribution in commits, PR title/body, or
     comments. Commit as the repo owner would: subject, why-body, `Closes #5`.
  3. Before pushing run, from the worktree: `make lint`, `make format-check`,
     `make typecheck`, `make test-scope`. Then `gh pr checks --watch`.
  4. The repo owner runs the full suite before merge; the implementer runs
     only the nats test files plus `tests/tools/test_telemetry.py`.
  5. No installs on the host. Docker containers are fine (§12).

## 0. Decisions already made (do not re-open)

1. **One integration, `nats`, nats-server 2.10 or newer.** The only surface
   is the HTTP monitoring endpoint (`-m 8222` / `http_port`). No NATS wire
   protocol, no `nats-py`, no JetStream API requests. Nothing on this
   surface can publish, consume, or change server state.
2. **Transport is `httpx.Client`**, exactly as `integrations/rabbitmq/`
   does. One base URL (`url`, the monitoring endpoint). No new dependencies.
3. **Authentication is optional HTTP basic auth** (`username` / `password`)
   for a reverse proxy in front of the monitoring port. nats-server itself
   has no authentication on that port (verified: every fixture was fetched
   without credentials). `password` is the only secret.
4. **Six read-only tools**, all `ToolSurface.CHAT`, all returning structured
   `{"source": "nats", "available": False, "error": ...}` instead of
   raising.
5. **Every connection parameter is injected** (resolved from the integration
   store or env). The LLM can never set the URL or credentials. Mirrors
   `tests/tools/test_aerospike_tools_port_injection.py`.
6. **`/jsz` is a per-server view, and the tools say so.** Verified: `n1`
   reports 2 streams and 3 consumers while `n3` reports 3 streams and 4
   consumers, because the R1 stream `EVENTS` has its only replica on `n3`
   (fixtures `jsz.json` vs `jsz_n3_streams_consumers.json`). Cluster-wide
   aggregation would need the `$SYS` account over the wire protocol and is
   out of scope. Both JetStream tools report `view: "server"` plus the
   answering server's name and add `PER_SERVER_NOTE` when the server is
   clustered.
7. **Consumer counters are authoritative only from the consumer's RAFT
   leader.** Verified: `billing` shows `num_pending: 330` from its leader
   `n1` and `num_pending: 0` from follower `n3`; `audit` shows `0` from `n1`
   and `600` from its leader `n3`. Likewise replica `current`/`active`
   fields are only meaningful from the group leader (`n1`'s view of
   `ORDERS` lists `n2` as `current: false, active: 0`; the leader `n3` lists
   both peers as `current: true`). Every stream and consumer entry
   therefore carries `authoritative: bool` (`cluster.leader ==
   server_name`) and the tools sort authoritative entries first.
8. **`stream` and `consumer` query parameters on `/jsz` are ignored by the
   server.** Verified: `/jsz?streams=true&consumers=true&stream=ORDERS`
   still returns `JOBS` (`jsz_stream_filter.json`); `&consumer=billing`
   still returns `audit`. Filtering is client-side.
9. **`/subsz` `total` counts listed entries, not subscriptions.** Verified:
   `subsz.json` (no `subs=true`) has `num_subscriptions: 311` and
   `total: 0`; `subsz_detail.json` has `total: 111` with 111 entries in
   `subscriptions_list`. Use `num_subscriptions` for the server total and
   the list length for what was listed; never treat `total` as the
   subscription count. The `test=<subject>` parameter only produces a match
   list together with `subs=true`, and the `subscriptions_list` key is
   **absent** when nothing matches (`subsz_test_nomatch.json`).
10. **A 503 from `/healthz` is data, not an error.** The verifier and the
    server-status tool read the body (`{"status": "unavailable", "error":
    "..."}` per the nats-server source; **not captured live** — every
    fixture returned 200 `{"status":"ok"}`, including the JetStream-only
    variants on a server with JetStream disabled). Treat any non-200 JSON
    body from `/healthz` as an unhealthy report; treat a non-JSON body as an
    error.
11. Package layout follows `AGENTS.md` ("keep `__init__.py` a facade"):
    logic lives in focused modules, `__init__.py` only re-exports.
12. Docs navigation: `"nats"` joins the existing **Workflows** group in
    `docs/docs.json`, where `kafka` and `rabbitmq` already live. The
    "Messaging" group is chat transports (Slack, Telegram) and does not fit.

## 1. Architecture

### 1.1 Request flow

```
tool fn (integrations/nats/tools/<name>_tool/__init__.py)
  -> builds NatsConfig from injected params
  -> calls one get_* function in integrations/nats/diagnostics.py
       -> client.build_client(config)            (httpx.Client, base_url = config.url)
       -> client.get_json(client, path, params)  GET /varz, /healthz, /connz, /subsz, /jsz, /routez, /gatewayz, /leafz
            -> monitoring.shape_* (pure functions over the JSON)
  -> returns evidence dict {"source": "nats", "available": bool, ...}
```

### 1.2 Endpoints used (all verified live against 2.14.6, see `fixtures/index.json`)

| Purpose | Path | Fixture |
| --- | --- | --- |
| Server variables | `GET /varz` | `varz.json` (n1, clustered), `varz_n2.json`, `varz_solo.json` (standalone, JetStream off) |
| Health | `GET /healthz` (also `?js-enabled-only=true`, `?js-server-only=true`) | `healthz.json`, `healthz_js_enabled_only.json`, `healthz_js_server_only.json`, `healthz_solo.json`, `healthz_solo_js_enabled_only.json` — all `{"status":"ok"}` |
| Connections | `GET /connz?sort={sort}&state={state}&limit={n}&subs=true` | `connz.json`, `connz_sort_pending.json`, `connz_sort_subs_detail.json`, `connz_sort_msgs_from_limit.json`, `connz_state_closed.json`, `connz_state_all.json`, `connz_n2.json`, `connz_solo.json` |
| Subscriptions | `GET /subsz?subs=true[&test={subject}]` | `subsz.json`, `subsz_detail.json`, `subsz_test_match.json`, `subsz_test_nomatch.json`, `subsz_n2.json` |
| JetStream | `GET /jsz?streams=true&consumers=true&config=true` | `jsz.json`, `jsz_streams.json`, `jsz_streams_consumers.json`, `jsz_streams_consumers_config.json`, `jsz_n2.json`, `jsz_n3_streams_consumers.json`, `jsz_leader_only.json`, `jsz_solo.json`, `jsz_solo_streams.json`, `jsz_stream_filter.json`, `jsz_stream_filter_unknown.json`, `jsz_consumer_filter.json` |
| Routes | `GET /routez` | `routez.json` (8 routes), `routez_subs.json`, `routez_n3.json`, `routez_solo.json` (0 routes) |
| Gateways | `GET /gatewayz` | `gatewayz.json`, `gatewayz_solo.json` (both empty maps) |
| Leaf nodes | `GET /leafz` | `leafz.json`, `leafz_solo.json` (both `leafnodes: 0`) |
| Errors | `GET /nonexistent` → 404 text; `GET /connz?sort=bogus` → 400 text; `GET /` → 200 HTML | `not_found.txt`, `connz_bad_sort.txt`, `connz_bad_state.txt`, `root.txt` |
| Wrong port | `GET /varz` against the client port 4222 | `wrong_port_client_4222.txt` (raw `INFO {...}` line; httpx raises `RemoteProtocolError`) |

`accountz.json` and `accstatz.json` are captured for reference only; no
tool reads them (out of scope in the ticket).

### 1.3 Failure modes the client must map explicitly

`client.py` returns `(result, FetchError | None)`; it never raises for HTTP
or transport problems. Real bodies are quoted from the fixtures; every
error body from nats-server is `text/plain`, never JSON.

| Condition | Real body / exception | `FetchErrorKind` | message |
| --- | --- | --- | --- |
| `httpx.RemoteProtocolError` | `Server disconnected without sending a response.` (verified against the client port 4222) | `wrong_port` | `NATS monitoring endpoint {url} did not answer HTTP; the URL probably points at the client port (4222) instead of the monitoring port (default 8222).` |
| any other `httpx.RequestError` | `[Errno 111] Connection refused` (`httpx.ConnectError`), timeouts | `transport` | `NATS monitoring request failed: {err}` |
| 401 / 403 | (from a proxy; not produced by nats-server) | `auth` | `NATS monitoring endpoint {url} rejected {path} with HTTP {code}. nats-server has no authentication on the monitoring port, so check the proxy credentials (NATS_MONITOR_USERNAME / NATS_MONITOR_PASSWORD).` |
| 404 | `404 page not found\n` | `not_found` | `NATS monitoring endpoint returned 404 for {path}: {text.strip()[:120]}` |
| 400 | `invalid sorting option: bogus` / `Error decoding state for bogus` | `http` | `NATS monitoring endpoint rejected {path}: {text.strip()[:200]}` |
| other ≥ 400 not in `accept` | | `http` | `NATS monitoring endpoint returned HTTP {code} for {path}: {text.strip()[:200]}` |
| status in `accept` but body not JSON | `GET /` → `<html lang="en">...` | `body` | `NATS monitoring endpoint returned a non-JSON body for {path}; check that NATS_MONITOR_URL is the HTTP monitoring port (default 8222), not a client or proxy port.` |

Use `http.HTTPStatus` constants everywhere (never numeric literals), in
source and tests.

## 2. Config

### 2.1 `config/constants/nats.py` (new)

```python
"""NATS environment variable names."""

from __future__ import annotations

NATS_MONITOR_URL_ENV = "NATS_MONITOR_URL"
NATS_MONITOR_USERNAME_ENV = "NATS_MONITOR_USERNAME"
NATS_MONITOR_PASSWORD_ENV = "NATS_MONITOR_PASSWORD"
NATS_VERIFY_SSL_ENV = "NATS_VERIFY_SSL"
NATS_TIMEOUT_SECONDS_ENV = "NATS_TIMEOUT_SECONDS"

__all__ = [  # keep sorted
    "NATS_MONITOR_PASSWORD_ENV",
    "NATS_MONITOR_URL_ENV",
    "NATS_MONITOR_USERNAME_ENV",
    "NATS_TIMEOUT_SECONDS_ENV",
    "NATS_VERIFY_SSL_ENV",
]
```

Re-export from `config/constants/__init__.py`: add the import block between
the `from config.constants.mysql import (...)` block (lines 229–236) and the
`from config.constants.new_relic import (...)` block (line 237), and the
five names into `__all__` after `"MYSQL_USERNAME_ENV",` (line 677) and
before `"NEW_RELIC_ACCOUNT_ID_ENV",` (line 678). `NATS_MONITOR_PASSWORD`
lands in the keyring tier automatically:
`config.env_key_sensitivity.is_sensitive_env_key` treats a terminal
`password` token as sensitive (verified: `_SENSITIVE_TERMINAL_TOKENS` in
that module, line 23).

### 2.2 `integrations/nats/config.py` (new) — `NatsConfig`

```python
"""NATS monitoring-endpoint connection settings and credential resolution."""

DEFAULT_NATS_TIMEOUT_SECONDS = 10
DEFAULT_NATS_MONITOR_PORT = 8222   # documentation/default hint only; never appended to url
_TRUTHY = ("true", "1", "yes")
_ALLOWED_SCHEMES = ("http://", "https://")


class NatsConfig(StrictConfigModel):
    url: str = ""
    username: str = ""
    password: str = ""
    verify_ssl: bool = True
    timeout_seconds: int = Field(default=DEFAULT_NATS_TIMEOUT_SECONDS, gt=0)
    integration_id: str = ""
```

Validators (all `mode="before"`, same style as `RabbitMQConfig` in
`integrations/rabbitmq/__init__.py` lines 52–110):

- `url`: `str(value or "").strip().rstrip("/")`; when non-empty and not
  starting with one of `_ALLOWED_SCHEMES` raise
  `ValueError("NATS monitoring URL must start with http:// or https://")`.
- `username`: `str(value or "").strip()`.
- `password`: `str(value or "")` — **never strip**.
- `verify_ssl`: bool passthrough; strings via `.strip().lower() in _TRUTHY`.
- `timeout_seconds`: `safe_int(value, DEFAULT_NATS_TIMEOUT_SECONDS)`.

Properties:

- `is_configured -> bool`: `bool(self.url)`.
- `has_auth -> bool`: `bool(self.username)`.

Functions in the same module:

```python
def build_nats_config(raw: dict[str, Any] | None) -> NatsConfig:
    return NatsConfig.model_validate(raw or {})

def nats_config_from_env() -> NatsConfig | None:
    # url required; returns None when NATS_MONITOR_URL is empty.
    # password via config.llm_credentials.resolve_env_credential(NATS_MONITOR_PASSWORD_ENV) or "";
    # username via os.getenv(NATS_MONITOR_USERNAME_ENV, "").strip();
    # verify_ssl via os.getenv(NATS_VERIFY_SSL_ENV, "true").strip().lower() in _TRUTHY;
    # timeout via safe_int(os.getenv(NATS_TIMEOUT_SECONDS_ENV, "10"), 10).

def nats_is_available(sources: dict[str, dict]) -> bool:
    return bool(sources.get("nats", {}).get("url"))

def nats_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    # returns url, username, password, verify_ssl (defaults "", "", "", True)
    # — the injected kwargs for every tool.

def classify(credentials: dict[str, Any], record_id: str) -> tuple[NatsConfig | None, str | None]:
    # identical shape to integrations/rabbitmq/__init__.py classify() (lines 575–596):
    # build_nats_config({"url", "username", "password", "verify_ssl", "timeout_seconds"
    # from credentials.get(...) with defaults, "integration_id": record_id}); on exception
    # report_classify_failure(exc, logger=logger, integration="nats", record_id=record_id)
    # and return (None, None); return (cfg, "nats") only when cfg.is_configured.
```

No entry in `integrations/config_models.py` (rabbitmq precedent).

## 3. `integrations/nats/client.py` (new)

```python
class FetchErrorKind(StrEnum):
    TRANSPORT = "transport"; WRONG_PORT = "wrong_port"; AUTH = "auth"
    NOT_FOUND = "not_found"; HTTP = "http"; BODY = "body"

@dataclass(frozen=True)
class FetchError:
    kind: FetchErrorKind
    message: str
    status: int | None = None

@dataclass(frozen=True)
class FetchResult:
    status: int
    payload: Any

def build_client(config: NatsConfig) -> httpx.Client:
    auth = httpx.BasicAuth(config.username, config.password) if config.has_auth else None
    return httpx.Client(base_url=config.url, timeout=float(config.timeout_seconds),
                        verify=config.verify_ssl, auth=auth,
                        headers={"Accept": "application/json"})

def get_json(client: httpx.Client, config: NatsConfig, path: str,
             params: dict[str, Any] | None = None, *,
             accept: tuple[HTTPStatus, ...] = (HTTPStatus.OK,)) -> tuple[FetchResult | None, FetchError | None]
    # GET path; mapping per §1.3. A status in `accept` with a JSON body returns FetchResult.
    # A status in `accept` with a non-JSON body → BODY. Anything else → the §1.3 row.
    # `config` is only used for `config.url` in messages.
```

`diagnostics.py` must call `nats_client.build_client(config)` through the
module object (`from integrations.nats import client as nats_client`) so
tests can `monkeypatch.setattr(nats_client, "build_client", ...)` and swap
in an `httpx.MockTransport` — the same technique as `_mock_transport` /
`patched_client` in `tests/integrations/test_rabbitmq.py` lines 37–68.

`params` never repeats a key in this spec, so a dict is fine. Boolean query
values must be sent as the strings `"true"`/`"false"` (nats-server parses
`subs=true`, `streams=true`; Python's `True` would serialise as `True`).
Build params with string values only.

## 4. `integrations/nats/monitoring.py` (new) — shapers

Pure functions over the JSON. Missing keys → `None` (or `0` for counters,
`[]` for lists) via `.get`; never `KeyError`. Two helpers live here too:

```python
_GO_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)(ns|µs|us|ms|s|m|h)")

def parse_go_duration(value: str | None) -> float | None:
    # "133µs" -> 0.000133; "1m45s" -> 105.0; "1m52s" -> 112.0; "0s" -> 0.0; None/"" -> None.
    # Sum every (number, unit) pair; unknown text -> None.

def ns_to_seconds(value: int | float | None) -> float | None:
    # 30000000000 -> 30.0; None -> None; negative stays negative (max_age -1 means unlimited).
```

Verified inputs for `parse_go_duration`: `"133µs"`, `"122µs"`, `"1m52s"`,
`"1m45s"`, `"0s"` (all present in the fixtures; JSON stores `µ` as
`\u00b5`, so the parser must accept the decoded character).

### 4.1 `shape_varz(payload)` from `/varz`

```python
{
  "server_id": "ND3Y24GF3MIAUZ7HYLY3NN673WAO3WIY7ZTS6RNDYFWUUFY2UBEZ5R6G",
  "server_name": "n1", "version": "2.14.6", "go": "go1.26.7", "git_commit": "1aa10f9",
  "host": "0.0.0.0", "port": 4222, "http_port": 8222,
  "start": "2026-09-11T15:55:05.622901764Z", "now": "...", "uptime": "1m52s",
  "uptime_seconds": 112.0,                       # parse_go_duration(uptime)
  "cores": 20, "gomaxprocs": ..., "cpu_pct": 1, "mem_bytes": 26021888,
  "connections": 1, "total_connections": 20, "max_connections": 65536,
  "routes": 8, "remotes": 2, "leafnodes": 0, "subscriptions": 311,
  "in_msgs": 19946, "out_msgs": 19944, "in_bytes": 292716, "out_bytes": 299222,
  "slow_consumers": 0,
  "slow_consumer_stats": {"clients": 0, "routes": 0, "gateways": 0, "leafs": 0},
  "stale_connections": 0, "stalled_clients": 0,
  "max_payload": 1048576, "max_pending": 67108864, "max_control_line": 4096,
  "write_deadline_seconds": 10.0,                # ns_to_seconds(write_deadline)
  "ping_interval_seconds": 120.0, "ping_max": 2,
  "system_account": "$SYS", "config_load_time": "...",
  "cluster": {"name": "opensre-demo", "port": 6222, "urls": ["opensre-nats-n1:6222", "opensre-nats-n2:6222", "opensre-nats-n3:6222"], "pool_size": 3},   # None when varz.cluster == {}
  "clustered": True,                              # bool(payload.get("cluster"))
  "http_req_stats": {"/varz": 2, ...},
}
```

`varz_solo.json`: `cluster: {}` → `"cluster": None, "clustered": False`;
`jetstream: {}`; `connections: 0`, `total_connections: 0`, `routes: 0`,
`subscriptions: 60`, `server_name: "solo"`.

### 4.2 `shape_jetstream_varz(payload)` from `/varz` `jetstream` block

```python
{
  "enabled": True,                                # bool(payload.get("jetstream"))
  "config": {"max_memory": 50264146944, "max_storage": 120282688512, "store_dir": "/tmp/js/jetstream", "sync_interval_seconds": 120.0, "strict": True},
  "stats": {"memory": 0, "storage": 33150, "reserved_memory": 0, "reserved_storage": 10485760, "accounts": 1, "ha_assets": 6,
            "api": {"level": 4, "total": 14, "errors": 4}},
  "meta": {"name": "opensre-demo", "leader": "n1", "cluster_size": 3, "pending": 0,
           "replicas": [{"name": "n2", "current": True, "active_ms": 94.4}, {"name": "n3", "current": True, "active_ms": 94.4}]},   # replicas absent on followers -> []
}
```

Disabled (`varz_solo.json`): `{"enabled": False, "config": None, "stats": None, "meta": None}`.
`active` is nanoseconds; report `active_ms = round(active / 1e6, 1)`.

### 4.3 `shape_healthz(status, payload)` from `/healthz`

`{"status_code": 200, "status": "ok", "error": None, "ok": True}`; `ok` is
`status_code == HTTPStatus.OK and status == "ok"`. A 503 body carries
`status` and `error` (per nats-server source; not captured, see decision 10).

### 4.4 `shape_connection(payload)` from `/connz` `connections[]`

Open connection (`connz.json`):

```python
{
  "cid": 51, "kind": "Client", "type": "nats", "name": "NATS CLI Version 0.4.0", "lang": "go", "version": "1.51.0",
  "ip": "172.20.0.6", "port": 34282, "start": "...", "last_activity": "...",
  "uptime": "1m45s", "uptime_seconds": 105.0, "idle": "0s", "idle_seconds": 0.0, "rtt": "133µs", "rtt_ms": 0.133,
  "pending_bytes": 0, "in_msgs": 0, "out_msgs": 16000, "in_bytes": 0, "out_bytes": ...,
  "subscriptions": 1, "subscriptions_list": ["telemetry.>"],   # [] when the key is absent (no subs=true)
  "closed": False, "stop": None, "reason": None, "slow_consumer": False,
}
```

Closed connection (`connz_state_closed.json`): `"closed": True`, `"stop":
"2026-...Z"`, `"reason": "Client Closed"` or `"Protocol Violation"` (both
verified; the `Protocol Violation` entry is the HTTP GET that produced
`wrong_port_client_4222.txt`). `slow_consumer` is
`reason.lower().startswith("slow consumer")` — the reason strings for slow
consumers (`Slow Consumer (Write Deadline)`, `Slow Consumer (Pending
Bytes)`) come from the nats-server source and were **not** produced live.

`summarize_connections(connections)`:

```python
{
  "total": len, "open": n, "closed": n,
  "by_kind": {"Client": 1}, "by_lang": {"go": 1},
  "pending_bytes_total": 0, "subscriptions_total": 1,
  "slow_consumer_closures": 0,
  "closed_by_reason": {"Client Closed": 18, "Protocol Violation": 1},   # connz_state_closed.json
  "top_pending": [ {"cid", "name", "ip", "pending_bytes", "subscriptions"} ] (TOP_N, pending_bytes desc, only > 0),
  "oldest_idle": {"cid", "name", "idle_seconds"} | None    # max idle_seconds among open
}
```

### 4.5 `shape_subsz(payload)` from `/subsz?subs=true`

```python
{
  "num_subscriptions": 311, "num_cache": 99, "num_inserts": 400, "num_removes": 89,
  "num_matches": 526, "cache_hit_rate": 0.4391634980988593, "max_fanout": 3, "avg_fanout": 1.8484848484848484,
  "subscriptions": [ {"subject": "telemetry.>", "account": "$G", "sid": "1", "msgs": 16000, "cid": 51, "queue": None} ... ],
  "listed": 111,                                  # len(subscriptions_list); payload["total"] is 0 without subs=true and 111 with it (decision 9)
}
```

Queue-group subscriptions carry `qgroup` in the raw entry (verified present
on some `$SYS` entries in `subsz_detail.json`); map it to `queue`.
`summarize_subscriptions(subs)` → `{"by_account": {"$SYS": 97, "$G": 14},
"system_subscriptions": 97, "application_subscriptions": 14, "top_by_msgs":
[first TOP_N sorted by msgs desc, excluding subjects starting with "$SYS."
or "$NRG." or "$JS."]}`. `subsz_detail.json` top application subject is
`telemetry.>` with `msgs: 16000`.

### 4.6 `shape_jsz(payload)` from `/jsz`

```python
{
  "enabled": True,                                # not payload.get("disabled", False)
  "server_id": "...", "streams": 2, "consumers": 3, "messages": 625, "bytes": 33150,
  "memory": 0, "storage": 33150, "reserved_memory": 0, "reserved_storage": 10485760,
  "accounts": 1, "ha_assets": 6, "api": {"level": 4, "total": 14, "errors": 4},
  "config": {"max_memory": ..., "max_storage": ..., "store_dir": "/tmp/js/jetstream"},
  "meta_cluster": {"name": "opensre-demo", "leader": "n1", "cluster_size": 3, "pending": 0, "replicas": [...] or []},   # None when absent (standalone)
}
```

`jsz_solo.json`: `disabled: true`, `streams: 0`, no `meta_cluster` →
`enabled: False`, `meta_cluster: None`.

### 4.7 `shape_stream(detail, *, server_name)` from `account_details[].stream_detail[]`

```python
{
  "name": "ORDERS", "account": "$G", "created": "2026-09-11T15:55:07.251899391Z",
  "messages": 600, "bytes": 31984, "first_seq": 1, "last_seq": 600,
  "first_ts": "...", "last_ts": "...", "num_subjects": 2, "num_deleted": 0, "consumer_count": 2,
  "config": {"subjects": ["orders.>"], "retention": "limits", "storage": "file", "num_replicas": 3,
             "max_msgs": 10000, "max_bytes": 10485760, "max_age_seconds": 86400.0, "max_msg_size": -1,
             "max_consumers": -1, "discard": "old", "max_msgs_per_subject": -1},   # None when config=true was not requested
  "utilisation": {"msgs_pct": 6.0, "bytes_pct": 0.3},   # messages/max_msgs*100, bytes/max_bytes*100, rounded 1 dp; None when the limit is <= 0
  "cluster": {"name": "opensre-demo", "leader": "n3", "raft_group": "S-R3F-VJA9dCiS", "leader_since": None,
              "replicas": [{"name": "n2", "current": False, "active_ms": 0.0}, {"name": "n3", "current": True, "active_ms": ...}]},
  "authoritative": False,                         # cluster.leader == server_name ("n1" here, leader is "n3")
  "replicas_not_current": 1,                      # count of replicas with current False; only meaningful when authoritative
}
```

Verified values: `JOBS` has `retention: "workqueue"`, `max_msgs: -1`,
`max_bytes: -1`, `max_age: 0`, `messages: 25`, `last_seq: 25`. `EVENTS`
(only on `n3`, R1) has `cluster: {"name": "opensre-demo", "leader": "n3"}`
with no `raft_group` and no `replicas` → `replicas: []`, `raft_group:
None`. `state.num_deleted` is absent in every fixture → default `0`.

### 4.8 `shape_consumer(detail, *, stream_last_seq, server_name)` from `stream_detail[].consumer_detail[]`

```python
{
  "name": "billing", "stream": "ORDERS", "created": "...",
  "delivered": {"consumer_seq": 70, "stream_seq": 70, "last_active": "2026-09-11T15:55:11.846495325Z"},
  "ack_floor": {"consumer_seq": 50, "stream_seq": 50, "last_active": "..."},
  "num_pending": 330, "num_ack_pending": 20, "num_redelivered": 0, "num_waiting": 0,
  "lag": 530,                                     # max(stream_last_seq - delivered.stream_seq, 0) = 600 - 70
  "config": {"durable_name": "billing", "deliver_policy": "all", "ack_policy": "explicit", "ack_wait_seconds": 30.0,
             "max_deliver": 5, "filter_subject": "orders.created", "filter_subjects": [], "max_ack_pending": 1000,
             "max_waiting": 512, "replay_policy": "instant", "num_replicas": 0, "paused": False},   # None without config=true
  "cluster": {"name": "opensre-demo", "leader": "n1", "raft_group": "C-R3F-UDfxZC1Y", "replicas": [...]},
  "authoritative": True,                          # leader "n1" == server_name "n1"
  "flags": ["backlog", "ack_pending"],
}
```

`flags`, in this order, only when true: `"backlog"` (`num_pending > 0`),
`"ack_pending"` (`num_ack_pending > 0`), `"redeliveries"`
(`num_redelivered > 0`), `"never_delivered"` (`delivered.consumer_seq == 0`
and `num_pending > 0`), `"paused"` (config present and `pause_until` is
not the zero time `0001-01-01T00:00:00Z` and is in the future). `paused`
is `False` for every fixture (`pause_until` is the zero time on all four).

Verified per-consumer values from `jsz_streams_consumers_config.json` (n1):

| consumer | stream | delivered.stream_seq | num_pending | num_ack_pending | num_redelivered | leader | authoritative on n1 | lag |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| billing | ORDERS | 70 | 330 | 20 | 0 | n1 | True | 530 |
| audit | ORDERS | 0 | 0 | 0 | 0 | n3 | False | 600 |
| worker | JOBS | 2 | 0 | 1 | 2 | n2 | False | 23 |

From `jsz_n3_streams_consumers.json` (n3, no config): `stale`/`EVENTS`
`num_pending: 40`, delivered 0 → flags `["backlog", "never_delivered"]`,
leader `n3` → authoritative; `audit` `num_pending: 600` (authoritative on
n3); `billing` `num_pending: 0` (not authoritative on n3 — decision 7).
`ack_policy: "none"` on `audit`, `max_deliver: -1`, no `ack_wait` key →
`ack_wait_seconds: None`.

### 4.9 `shape_route(payload)` from `/routez` `routes[]`

```python
{"rid": 8, "remote_name": "n2", "remote_id": "NCTS5...", "ip": "172.20.0.3", "port": 49874,
 "start": "...", "uptime": "1m52s", "uptime_seconds": 112.0, "idle": "1m52s", "idle_seconds": 112.0, "rtt": "122µs", "rtt_ms": 0.122,
 "pending_size": 0, "in_msgs": 0, "out_msgs": 0, "in_bytes": 0, "out_bytes": 0,
 "subscriptions": 0, "compression": "off", "did_solicit": True, "is_configured": True}
```

`summarize_routes(routes)` → `{"count": 8, "peers": [{"remote_name": "n2",
"connections": 4, "pending_size_total": 0, "subscriptions_total": n,
"rtt_ms_max": ...}, {"remote_name": "n3", ...}], "pending_size_total": 0,
"subscriptions_total": 200}` sorted by `remote_name`. Verified:
`routez.json` has 8 routes, 4 to `n2` and 4 to `n3` (`pool_size: 3` plus
one account route each), `subscriptions` summing to 200, all
`pending_size: 0`. `routez_solo.json` → `count: 0, peers: []`.

### 4.10 `shape_gatewayz(payload)` / `shape_leafz(payload)`

`{"outbound": {name: {...}}, "inbound": {name: [...]}, "outbound_count": 0,
"inbound_count": 0}` from `outbound_gateways` / `inbound_gateways` (both
`{}` in every fixture) and `{"count": 0, "leafs": []}` from `leafnodes` /
`leafs` (both empty in every fixture). Pass nested entries through
unshaped; no fixture exercises them.

`TOP_N = 10`.

## 5. `integrations/nats/diagnostics.py` (new) — the `get_*` functions

Every function: `if not config.is_configured: return _error("NATS is not configured (NATS_MONITOR_URL is required).")`;
wrap the body in `try/except Exception as err` →
`report_validation_failure(err, logger=logger, integration="nats", method="<fn name>")`
then `return _error(str(err))`, where
`_error(msg, **extra) = tool_unavailable("nats", msg, **extra)`. On a
`FetchError` return `_error(err.message, error_kind=err.kind)`. Open the
client with `nats_client.build_client(config)` as a context manager so it is
always closed.

Constants:

```python
DEFAULT_CONNECTION_LIMIT = 25
MAX_CONNECTION_LIMIT = 1024          # the server's own default `limit` (verified: connz.json limit 1024)
ALLOWED_CONNZ_SORT: frozenset[str] = frozenset({
    "cid", "start", "subs", "pending", "msgs_to", "msgs_from", "bytes_to", "bytes_from",
    "last", "idle", "uptime", "stop", "reason", "rtt",
})   # all 14 verified live (HTTP 200); "stop" and "reason" answer 400 unless state=closed
CLOSED_ONLY_SORTS: frozenset[str] = frozenset({"stop", "reason"})
ALLOWED_CONNZ_STATE: frozenset[str] = frozenset({"open", "closed", "all"})   # all three verified live
JETSTREAM_DISABLED_HINT = (
    "JetStream is disabled on server '{server_name}'. Start nats-server with -js (or jetstream {{}} in the "
    "config) or point NATS_MONITOR_URL at a JetStream-enabled server."
)
PER_SERVER_NOTE = (
    "/jsz reports only the streams and consumers that have a replica on server '{server_name}'; "
    "counters are authoritative only for assets whose RAFT leader is this server. Query each "
    "cluster member's monitoring port for the full picture."
)
```

```python
def get_server_status(config) -> dict
def get_connections(config, sort: str = "pending", state: str = "open", limit: int = DEFAULT_CONNECTION_LIMIT) -> dict
def get_subscriptions(config, subject: str = "") -> dict
def get_jetstream_streams(config, stream: str = "") -> dict
def get_jetstream_consumers(config, stream: str = "", consumer: str = "") -> dict
def get_cluster_status(config) -> dict
def clamp_limit(value: int | None) -> int    # None/<=0 → DEFAULT_CONNECTION_LIMIT, > MAX → MAX
```

**`get_server_status`**:
1. `GET /varz` → on error `_error`. `server = shape_varz(payload)`,
   `jetstream = shape_jetstream_varz(payload)`.
2. `GET /healthz` with `accept=(HTTPStatus.OK, HTTPStatus.SERVICE_UNAVAILABLE)`
   → `health = shape_healthz(result.status, result.payload)`; on error
   `health = None` and append `err.message` to `warnings`.
3. Return `{"source": "nats", "available": True, "url": config.url, "server": server, "jetstream": jetstream, "health": health, "warnings": [...]}`.

**`get_connections`**:
1. `sort = sort.strip().lower() or "pending"`; not in `ALLOWED_CONNZ_SORT` →
   `_error(f"Unsupported sort '{sort}'. Allowed: {sorted list}")` without
   a network call. Same for `state` against `ALLOWED_CONNZ_STATE`. When
   `sort in CLOSED_ONLY_SORTS and state != "closed"` →
   `_error(f"sort '{sort}' is only valid with state='closed'.")` (verified:
   the server answers 400 `sort by stop only valid on closed connections`).
   `limit = clamp_limit(limit)`.
2. `GET /connz` with `{"sort": sort, "state": state, "limit": str(limit), "subs": "true"}`
   → on error `_error`. `connections = [shape_connection(c) for c in payload["connections"]]`.
3. `GET /varz` best-effort → `server_name`, `slow_consumers`,
   `max_connections`, `connections` (current count); failure → `None`s and
   a warning.
4. Return `{"source", "available": True, "url", "server_name", "sort", "state", "limit", "total": payload["total"], "returned": payload["num_connections"], "connections": [...], "summary": summarize_connections(connections), "slow_consumers", "max_connections", "current_connections", "warnings"}`.
   With `connz_state_closed.json`: `total == 19`, `returned == 19`,
   `summary.closed == 19`, `summary.closed_by_reason == {"Client Closed": 18, "Protocol Violation": 1}`.

**`get_subscriptions`**:
1. `subject = subject.strip()`. `params = {"subs": "true"}`; when `subject`
   add `"test": subject`.
2. `GET /subsz` → on error `_error`. `shaped = shape_subsz(payload)`.
3. Without `subject`: return `{"source", "available": True, "url", "subject": "", "stats": {every scalar from shaped except subscriptions}, "listed": shaped.listed, "summary": summarize_subscriptions(shaped.subscriptions), "subscriptions": shaped.subscriptions[:TOP_N sorted by msgs desc]}`.
4. With `subject`: return `{..., "subject": subject, "matches": shaped.subscriptions, "match_count": len}`.
   `subsz_test_match.json` → one match (`telemetry.>`, `cid 51`, `msgs 16000`);
   `subsz_test_nomatch.json` → `match_count == 0`, `matches == []`.

**`get_jetstream_streams`**:
1. `GET /varz` → on error `_error`; `server_name`, `clustered`, `meta_leader` (from `jetstream.meta.leader` or `None`).
2. `GET /jsz` with `{"streams": "true", "config": "true"}` → on error `_error`.
   `js = shape_jsz(payload)`. If not `js.enabled` → return
   `{"source", "available": True, "url", "server_name", "jetstream_enabled": False, "streams": [], "hint": JETSTREAM_DISABLED_HINT.format(...)}`.
3. Flatten `account_details[].stream_detail[]` → `shape_stream(detail, server_name=server_name)`
   with `account` set from the enclosing `account_details[].name`.
   `stream = stream.strip()`; when non-empty keep exact-name matches only
   (case-sensitive); no match → `streams: []` and
   `warning: f"stream '{stream}' not found on server '{server_name}'"`.
4. Sort: authoritative first, then `messages` desc, then name. Return
   `{"source", "available": True, "url", "server_name", "view": "server", "clustered", "meta_leader", "jetstream_enabled": True, "filter": stream, "totals": {"streams": js.streams, "consumers": js.consumers, "messages": js.messages, "bytes": js.bytes, "storage_bytes": js.storage, "memory_bytes": js.memory, "api_errors": js.api.errors}, "streams": [...], "note": PER_SERVER_NOTE.format(...) if clustered else None, "warnings"}`.
   From `jsz_streams_consumers_config.json` + `varz.json`: 2 streams
   (`ORDERS`, `JOBS`), neither authoritative on `n1` (both led by `n3`),
   `totals.messages == 625`, `totals.api_errors == 4`.

**`get_jetstream_consumers`**:
1. Same `/varz` prologue. `GET /jsz` with `{"streams": "true", "consumers": "true", "config": "true"}`; disabled → same shape as above with `consumers: []`.
2. For every stream detail, for every `consumer_detail[]` →
   `shape_consumer(detail, stream_last_seq=stream.state.last_seq, server_name=server_name)`.
   Client-side filters: `stream` exact on `stream_name`, `consumer` exact on
   `name`; no match → empty list + warning naming the filter.
3. Sort: authoritative first, then `num_pending + num_ack_pending` desc, then
   `stream`, `name`. `summary = {"consumers": n, "authoritative": n, "with_backlog": n, "with_ack_pending": n, "with_redeliveries": n, "never_delivered": n, "pending_total": sum(num_pending), "ack_pending_total": sum(num_ack_pending)}`
   over the returned (filtered) list.
4. Return `{"source", "available": True, "url", "server_name", "view": "server", "clustered", "meta_leader", "jetstream_enabled": True, "filters": {"stream", "consumer"}, "consumers": [...], "summary", "note", "warnings"}`.
   From the n1 fixture: 3 consumers, `billing` first (authoritative, 350),
   `summary.pending_total == 330`, `summary.ack_pending_total == 21`,
   `summary.with_redeliveries == 1`, `summary.authoritative == 1`.

**`get_cluster_status`**:
1. `GET /varz` → on error `_error`. `server = shape_varz(payload)`, `jetstream = shape_jetstream_varz(payload)`.
2. Best-effort (each failure → `None` + warning): `GET /routez` →
   `routes = {"summary": summarize_routes(shaped), "routes": shaped}`;
   `GET /gatewayz` → `gateways = shape_gatewayz`; `GET /leafz` →
   `leafnodes = shape_leafz`; `GET /jsz` (no params) → `meta = shape_jsz(...).meta_cluster`.
3. `clustered = server.clustered`. `is_meta_leader = meta is not None and meta.leader == server.server_name`.
4. Return `{"source", "available": True, "url", "server_name", "clustered", "cluster": server.cluster, "routes", "gateways", "leafnodes", "jetstream_meta": meta, "is_meta_leader", "jetstream_enabled": jetstream.enabled, "warnings"}`.
   Clustered fixtures (`varz.json`, `routez.json`, `gatewayz.json`, `leafz.json`, `jsz.json`):
   `clustered is True`, `cluster.name == "opensre-demo"`, `routes.summary.count == 8`,
   peers `n2` and `n3` with 4 connections each, `jetstream_meta.leader == "n1"`,
   `jetstream_meta.cluster_size == 3`, `is_meta_leader is True`.
   Standalone fixtures (`varz_solo.json`, `routez_solo.json`, `gatewayz_solo.json`, `leafz_solo.json`, `jsz_solo.json`):
   `clustered is False`, `cluster is None`, `routes.summary.count == 0`,
   `jetstream_meta is None`, `is_meta_leader is False`, `jetstream_enabled is False`.

## 6. Facade, validation, verifier, setup

### 6.1 `integrations/nats/__init__.py` (new, facade only)

Docstring + imports + `__all__`. Re-export: `NatsConfig`,
`NatsValidationResult`, `DEFAULT_NATS_TIMEOUT_SECONDS`,
`DEFAULT_CONNECTION_LIMIT`, `MAX_CONNECTION_LIMIT`, `ALLOWED_CONNZ_SORT`, `CLOSED_ONLY_SORTS`,
`ALLOWED_CONNZ_STATE`, `build_nats_config`, `nats_config_from_env`,
`nats_is_available`, `nats_extract_params`, `classify`,
`validate_nats_config`, `get_server_status`, `get_connections`,
`get_subscriptions`, `get_jetstream_streams`, `get_jetstream_consumers`,
`get_cluster_status`. Tools import **only** from this facade
(`tests/shared/test_tool_api_border.py` enforces it).

### 6.2 `integrations/nats/validation.py` (new)

```python
@dataclass(frozen=True)
class NatsValidationResult:
    ok: bool
    detail: str

def validate_nats_config(config: NatsConfig) -> NatsValidationResult
```

- not configured → `(False, "NATS monitoring URL is required (NATS_MONITOR_URL).")`.
- `GET /healthz` with `accept=(OK, SERVICE_UNAVAILABLE)`: fetch error →
  `(False, err.message)` (the §1.3 messages already name the cause; the
  password is never echoed). Status 503 → `(False, f"NATS server at {url} reports {status}: {error}")`.
- `GET /varz` error → `(False, err.message)`.
- success → detail
  `nats-server {version} '{server_name}' reachable at {url} (uptime {uptime}, {connections} client connections, JetStream {enabled|disabled}, cluster {cluster.name|standalone})`.
  With `varz.json`: `nats-server 2.14.6 'n1' reachable at ... (uptime 1m52s, 1 client connections, JetStream enabled, cluster opensre-demo)`;
  with `varz_solo.json`: `... 'solo' ... (uptime ..., 0 client connections, JetStream disabled, cluster standalone)`.
- Unexpected exception → `report_validation_failure(..., integration="nats", method="validate_nats_config")`
  and `(False, f"NATS connection failed: {err}")`.

### 6.3 `integrations/nats/verifier.py` (new)

```python
verify_nats = register_validation_verifier(
    "nats", build_config=build_nats_config, validate_config=validate_nats_config,
)
```
(`register_validation_verifier` signature: `integrations/verification/validation.py` line 60.)

### 6.4 `integrations/nats/setup.py` (new)

`NATS_SETUP = IntegrationSetupSpec(service="nats", fields=(...), verify=verify_nats)`
with fields, in this order (template: `integrations/aerospike/setup.py`;
`SetupField` at `integrations/setup_flow.py` line 100):

| name | label | prompt | env_var | default | required | secret |
| --- | --- | --- | --- | --- | --- | --- |
| url | Monitoring URL | `nats-server HTTP monitoring URL (e.g. http://nats.example.net:8222)` | NATS_MONITOR_URL_ENV | | yes | |
| username | Proxy username | `Basic-auth username if a proxy protects the monitoring port (leave blank otherwise)` | NATS_MONITOR_USERNAME_ENV | | no | |
| password | Proxy password | `Basic-auth password for that proxy` | NATS_MONITOR_PASSWORD_ENV | | no | yes |
| verify_ssl | Verify TLS certificate | `Verify the TLS certificate? (true/false)` | NATS_VERIFY_SSL_ENV | `true` | | |

Export the `*_FIELD` name constants and `NATS_SETUP` in `__all__`.

## 7. Tools — `integrations/nats/tools/<pkg>/__init__.py` (six new packages)

`integrations/nats/tools/__init__.py` is an empty facade (rabbitmq
precedent: empty file). Template for every tool:
`integrations/rabbitmq/tools/rabbitmq_node_health_tool/__init__.py`.
Shared decorator values: `source="nats"`, `surfaces=(ToolSurface.CHAT,)`,
`is_available=nats_is_available`, `extract_params=nats_extract_params`,
`injected_params=_NATS_INJECTED` where

```python
_NATS_INJECTED = ("url", "username", "password", "verify_ssl")
```

Every tool function takes those four as keyword params (`url: str` first,
then `username: str = ""`, `password: str = ""`, `verify_ssl: bool = True`),
builds `NatsConfig(...)`, and delegates. LLM-visible params per tool:

| package | tool name | LLM params | delegates to |
| --- | --- | --- | --- |
| `nats_server_status_tool` | `get_nats_server_status` | none | `get_server_status` |
| `nats_connections_tool` | `get_nats_connections` | `sort: str = "pending"`, `state: str = "open"`, `limit: int = 25` | `get_connections` |
| `nats_subscriptions_tool` | `get_nats_subscriptions` | `subject: str = ""` | `get_subscriptions` |
| `nats_jetstream_streams_tool` | `get_nats_jetstream_streams` | `stream: str = ""` | `get_jetstream_streams` |
| `nats_jetstream_consumers_tool` | `get_nats_jetstream_consumers` | `stream: str = ""`, `consumer: str = ""` | `get_jetstream_consumers` |
| `nats_cluster_status_tool` | `get_nats_cluster_status` | none | `get_cluster_status` |

No name clashes with the injected params.

Descriptions (use verbatim; keep each under ~400 chars; no implicit string
concatenation inside list displays — extract long `use_cases` strings to
module constants if a line exceeds 100 chars):

- `get_nats_server_status`: "Return one nats-server's status from its monitoring endpoint: version, uptime, health check result, client connection counts against the limit, slow consumers, stale and stalled clients, message and byte throughput, memory and CPU, max payload, cluster name, and JetStream storage usage, API error count and meta-group leader."
- `get_nats_connections`: "List client connections on one nats-server sorted by pending bytes (default), subscriptions, message or byte counters, idle time or RTT, with per-connection name, language, IP, subscriptions and pending bytes. State open (default), closed (with close reasons such as slow consumer) or all. Summarises pending bytes, close reasons and the oldest idle connection."
- `get_nats_subscriptions`: "Return subscription statistics for one nats-server (total, sublist cache hit rate, fan-out) and the busiest application subjects by message count. With a subject, list only the subscriptions whose pattern matches it, to check whether anyone is listening on that subject."
- `get_nats_jetstream_streams`: "List JetStream streams visible on one nats-server: message and byte counts, first and last sequence, subjects, retention, storage, replicas, limit utilisation, RAFT leader and replica currency. Optionally filter to one stream. Reports when JetStream is disabled. The view is per server; counters are authoritative only for streams led by the queried server."
- `get_nats_jetstream_consumers`: "List JetStream consumers visible on one nats-server with backlog (num_pending), lag behind the stream, unacknowledged deliveries, redeliveries, waiting pull requests, ack policy, filter subject and RAFT leader, flagged for backlog, ack-pending, redeliveries and never-delivered. Filter by stream or consumer. Counters are authoritative only from the consumer's leader."
- `get_nats_cluster_status`: "Return cluster topology from one nats-server: cluster name and peer URLs, route connections per peer with pending bytes and RTT, gateway and leaf-node counts, and the JetStream meta group leader, size and replica currency. Reports clustered false for a standalone server."

`use_cases` (3 per tool) — write them to match the descriptions; include
"Checking whether a NATS server is up and whether it is near its connection
limit" on server status, "Finding which client is a slow consumer or is
holding pending bytes" on connections, "Confirming a service is subscribed
to the subject it should consume" on subscriptions, "Explaining why a
JetStream consumer is falling behind or redelivering" on consumers,
"Checking whether a stream is close to its message or byte limit" on
streams, and "Confirming every node of a NATS cluster is routed and the
JetStream meta leader is elected" on cluster status.

Evidence mappers (`record_evidence_entry(evidence, source=<tool name>,
label=<Title>, summary=...)`, return early when `not output.get("available")`):

- server status: `nats-server {version} {server_name}: health {health.status or "unknown"}, {connections}/{max_connections} connections, {slow_consumers} slow consumers` + `, JetStream {storage_bytes} bytes stored, meta leader {leader}` when enabled.
- connections: `{returned} of {total} {state} connection(s), {summary.pending_bytes_total} pending bytes` + `, {slow_consumer_closures} slow-consumer closure(s)` when > 0 + `, top pending: {name or cid} ({pending_bytes})` for the first `top_pending` entry.
- subscriptions: without subject `{stats.num_subscriptions} subscription(s), cache hit rate {cache_hit_rate:.0%}, busiest: {subject} ({msgs})`; with subject `{match_count} subscription(s) match {subject}`.
- jetstream streams: `{len(streams)} stream(s) on {server_name}: {totals.messages} messages, {totals.bytes} bytes` + `, {name} at {msgs_pct}% of max_msgs` for the highest utilisation when ≥ 80; disabled → `JetStream disabled on {server_name}`.
- jetstream consumers: `{summary.consumers} consumer(s) on {server_name}: {pending_total} pending, {ack_pending_total} unacked` + `, {with_redeliveries} redelivering` when > 0 + `, worst: {stream}/{name} ({num_pending} pending)` for the first entry.
- cluster status: `cluster {cluster.name}: {routes.summary.count} route(s) to {len(peers)} peer(s), meta leader {leader} of {cluster_size}`; standalone → `standalone server {server_name} (no cluster)`.

No `SKILL.md` (per `docs/adding-tools-and-integrations.md` §"Skill guidance",
one-per-vendor stubs are discouraged; the descriptions carry the guidance).

## 8. Fixtures (committed with this spec)

`docs/superpowers/specs/nats/fixtures/` holds 49 files (48 payloads plus
`index.json`) captured on 2026-09-11 from `nats:2.14.6-alpine`: a
three-node JetStream cluster `opensre-demo` (`n1`, `n2`, `n3`) and a
standalone server `solo` without JetStream, provisioned by
`natsio/nats-box:0.19.7` as §12 describes. `index.json` maps every file to
its URL, status and content-type. Everything is verbatim; nothing was
redacted because the monitoring endpoint carries no credentials (server
IDs, container IPs and `NATS CLI` client names are all that identify
anything). **Implementer step:** copy the whole directory to
`tests/integrations/nats/fixtures/` (that is where `load_fixture` in §9
reads from) and add `tests/integrations/nats/__init__.py`. Leave the copy
under `docs/` untouched; it is the record of what the server said.

The capture is reproducible: `docs/superpowers/specs/nats/capture/run.sh`
starts the pinned containers and provisions streams, consumers, backlog and
long-lived subscribers; `capture/capture.py` (run from the repo root with
`uv run python docs/superpowers/specs/nats/capture/capture.py`, standard
library only) writes every payload plus `index.json` into a `fixtures/`
directory next to the script. §12 is the prose version of what the scripts
do.

Fixture inventory by purpose:

| Purpose | Files |
| --- | --- |
| errors | `not_found.txt` (404), `connz_bad_sort.txt` (400), `connz_bad_state.txt` (400), `root.txt` (200 HTML), `wrong_port_client_4222.txt` (raw NATS `INFO` line) |
| server | `varz.json` (n1), `varz_n2.json`, `varz_solo.json`, five `healthz*.json` (all ok) |
| connections | `connz.json` (1 open), `connz_sort_pending.json`, `connz_sort_subs_detail.json` (with `subscriptions_list`), `connz_sort_msgs_from_limit.json` (`limit: 2`), `connz_state_closed.json` (19 closed: 18 `Client Closed`, 1 `Protocol Violation`), `connz_state_all.json` (20), `connz_n2.json`, `connz_solo.json` (0) |
| subscriptions | `subsz.json`, `subsz_detail.json` (111 listed, `$SYS` 97 / `$G` 14), `subsz_test_match.json` (1 match), `subsz_test_nomatch.json` (no list), `subsz_n2.json` |
| jetstream | `jsz.json` (n1: 2 streams, 3 consumers, 625 msgs), `jsz_streams.json`, `jsz_streams_consumers.json`, `jsz_streams_consumers_config.json` (with `config`), `jsz_n2.json` (follower; `meta_cluster` without `replicas`), `jsz_n3_streams_consumers.json` (3 streams, 4 consumers, 665 msgs), `jsz_leader_only.json`, `jsz_solo.json` / `jsz_solo_streams.json` (`disabled: true`), `jsz_stream_filter.json` / `jsz_stream_filter_unknown.json` / `jsz_consumer_filter.json` (filters ignored) |
| cluster | `routez.json` (8), `routez_subs.json`, `routez_n3.json` (8), `routez_solo.json` (0), `gatewayz.json`, `gatewayz_solo.json`, `leafz.json`, `leafz_solo.json` |
| reference only | `accountz.json`, `accstatz.json` |

Streams in the fixture cluster: `ORDERS` (R3, file, limits 10000 msgs /
10 MiB / 24 h, 600 messages on `orders.created` ×400 and `orders.shipped`
×200), `EVENTS` (R1 memory, 40 messages, only on `n3`), `JOBS` (R3 file
work-queue, 25 messages). Consumers: `ORDERS/billing` (pull, explicit ack,
filter `orders.created`, 50 acked then 20 fetched without ack),
`ORDERS/audit` (ack none, never fetched), `JOBS/worker` (5 fetched and
nak'd → 2 redelivered, 1 ack pending), `EVENTS/stale` (never fetched, 40
pending).

## 9. Test plan (write these first; they must fail before §3–§7 exist)

Transport mocking: a `_mock_transport(routes: dict[str, httpx.Response | str | dict])`
helper keyed by **path** (query string ignored; tests that need to assert
query params inspect `request.url.params` inside a custom handler) plus a
`patched_client` fixture that monkeypatches
`integrations.nats.client.build_client` to return
`httpx.Client(base_url=..., transport=httpx.MockTransport(handler))` — copy
the shape from `tests/integrations/test_rabbitmq.py` lines 37–68. Unrouted
paths return 404 text `404 page not found\n` with
`content-type: text/plain; charset=utf-8`. A `load_fixture(name)` helper
reads from `tests/integrations/nats/fixtures/` (`json` for `.json`, text
for `.txt`). Convenience route sets: `CLUSTER_ROUTES` (`/varz` →
`varz.json`, `/healthz` → `healthz.json`, `/connz` →
`connz_sort_subs_detail.json`, `/subsz` → `subsz_detail.json`, `/jsz` →
`jsz_streams_consumers_config.json`, `/routez` → `routez.json`,
`/gatewayz` → `gatewayz.json`, `/leafz` → `leafz.json`) and `SOLO_ROUTES`
(the `_solo` files, `/jsz` → `jsz_solo_streams.json`).

### 9.1 `tests/integrations/test_nats.py`

- `TestNatsConfig`: defaults (verify_ssl True, timeout 10, `has_auth`
  False); normalization (url trailing `/` stripped, username stripped,
  password **not** stripped); scheme validation rejects
  `nats.example.net:8222` (no scheme) via `pydantic.ValidationError`;
  `is_configured` false without url; `safe_int` fallback for
  `timeout_seconds="abc"` → 10; `NATS_VERIFY_SSL=no` → False.
- `TestNatsEnv`: `nats_config_from_env` returns None without
  `NATS_MONITOR_URL`; loads every var (`monkeypatch.setenv` for all five);
  reads the password through `resolve_env_credential` (patch
  `integrations.nats.config.resolve_env_credential`, assert called once with
  `"NATS_MONITOR_PASSWORD"`).
- `TestNatsExtractParams` / `test_is_available`: full dict; `{}` → not
  available; url only → available.
- `TestClassify`: via `integrations.catalog.classify_integrations` with a
  store record → resolved `"nats"` entry has url; record without url →
  skipped.
- `TestClient`: each §1.3 row — a handler raising
  `httpx.RemoteProtocolError("Server disconnected without sending a response.")`
  → `WRONG_PORT` and the message mentions `8222`; `httpx.ConnectError` →
  `TRANSPORT`; 401 and 403 → `AUTH` and the message mentions
  `NATS_MONITOR_USERNAME`; 404 with `not_found.txt` → `NOT_FOUND` and message
  contains `404 page not found`; 400 with `connz_bad_sort.txt` → `HTTP` and
  message contains `invalid sorting option: bogus`; 500 → `HTTP`; 200 with
  `root.txt` (`text/html`) → `BODY` and message mentions `8222`; 503 JSON
  with `accept=(OK, SERVICE_UNAVAILABLE)` → `FetchResult.status == 503`;
  503 without it → `HTTP`. Assert basic auth: with `username="u",
  password="p"` the request carries an `Authorization` header starting
  with `Basic `; without a username no `Authorization` header is sent.
  Assert `params={"subs": "true"}` arrives as `subs=true`.
- `TestHelpers`: `parse_go_duration` on `"133µs"` → `0.000133` (approx),
  `"1m52s"` → `112.0`, `"0s"` → `0.0`, `""` → `None`, `"weird"` → `None`;
  `ns_to_seconds(30000000000)` → `30.0`, `(-1)` → `-1.0`, `(None)` → `None`.
- `TestValidate`: `CLUSTER_ROUTES` → `ok is True`, detail contains
  `2.14.6`, `'n1'`, `JetStream enabled`, `cluster opensre-demo`;
  `SOLO_ROUTES` → detail contains `JetStream disabled` and `standalone`;
  healthz 503 `{"status": "unavailable", "error": "JetStream not current"}`
  → `ok is False` and detail contains `unavailable` and `JetStream not
  current`; connection refused → `ok is False`; wrong port → detail
  mentions `8222`; 401 → `ok is False` and the password string is absent
  from `detail`.

### 9.2 `tests/integrations/nats/test_monitoring.py`

- `shape_varz(varz.json)` → `server_name == "n1"`, `version == "2.14.6"`,
  `connections == 1`, `total_connections == 20`, `max_connections == 65536`,
  `routes == 8`, `subscriptions == 311`, `slow_consumers == 0`,
  `in_msgs == 19946`, `mem_bytes == 26021888`, `cores == 20`,
  `max_payload == 1048576`, `write_deadline_seconds == 10.0`,
  `ping_interval_seconds == 120.0`, `uptime_seconds == 112.0`,
  `clustered is True`, `cluster["name"] == "opensre-demo"`,
  `cluster["pool_size"] == 3`, `len(cluster["urls"]) == 3`,
  `http_req_stats["/varz"] == 2`. `shape_varz(varz_solo.json)` →
  `clustered is False`, `cluster is None`, `connections == 0`,
  `subscriptions == 60`, `server_name == "solo"`.
- `shape_jetstream_varz(varz.json)` → `enabled is True`,
  `stats["storage"] == 33150`, `stats["reserved_storage"] == 10485760`,
  `stats["api"] == {"level": 4, "total": 14, "errors": 4}`,
  `meta["leader"] == "n1"`, `meta["cluster_size"] == 3`,
  `[r["name"] for r in meta["replicas"]] == ["n2", "n3"]`,
  `config["store_dir"] == "/tmp/js/jetstream"`,
  `config["sync_interval_seconds"] == 120.0`. On `varz_solo.json` →
  `enabled is False`, `meta is None`.
- `shape_healthz(200, healthz.json)` → `ok is True`, `status == "ok"`,
  `error is None`; `shape_healthz(503, {"status": "unavailable", "error": "x"})`
  → `ok is False`, `error == "x"`.
- `shape_connection(connz_sort_subs_detail.json["connections"][0])` →
  `cid == 51`, `name == "NATS CLI Version 0.4.0"`, `lang == "go"`,
  `subscriptions == 1`, `subscriptions_list == ["telemetry.>"]`,
  `out_msgs == 16000`, `closed is False`, `slow_consumer is False`,
  `rtt_ms` approx `0.133`, `idle_seconds == 0.0`, `uptime_seconds == 105.0`. On a closed entry from
  `connz_state_closed.json` → `closed is True`, `stop` is a string,
  `reason in {"Client Closed", "Protocol Violation"}`. On a synthetic entry
  with `reason: "Slow Consumer (Write Deadline)"` → `slow_consumer is True`.
  `summarize_connections` over `connz_state_closed.json` → `total == 19`,
  `closed == 19`, `open == 0`,
  `closed_by_reason == {"Client Closed": 18, "Protocol Violation": 1}`,
  `slow_consumer_closures == 0`, `top_pending == []`; over `connz.json` →
  `by_kind == {"Client": 1}`, `by_lang == {"go": 1}`,
  `oldest_idle["cid"] == 51`.
- `shape_subsz(subsz_detail.json)` → `num_subscriptions == 311`,
  `listed == 111`, `cache_hit_rate` approx `0.439`, `max_fanout == 3`;
  `summarize_subscriptions` → `by_account == {"$SYS": 97, "$G": 14}`,
  `system_subscriptions == 97`, `application_subscriptions == 14`,
  `top_by_msgs[0]["subject"] == "telemetry.>"` with `msgs == 16000` and no
  `$SYS.`/`$NRG.`/`$JS.` subject in `top_by_msgs`.
  `shape_subsz(subsz_test_nomatch.json)` → `subscriptions == []`,
  `listed == 0`; `shape_subsz(subsz_test_match.json)` → one entry, `cid == 51`.
- `shape_jsz(jsz.json)` → `enabled is True`, `streams == 2`,
  `consumers == 3`, `messages == 625`, `bytes == 33150`,
  `api["errors"] == 4`, `meta_cluster["leader"] == "n1"`,
  `meta_cluster["cluster_size"] == 3`, `len(meta_cluster["replicas"]) == 2`;
  `shape_jsz(jsz_n2.json)` → `meta_cluster["replicas"] == []`;
  `shape_jsz(jsz_solo.json)` → `enabled is False`, `streams == 0`,
  `meta_cluster is None`.
- `shape_stream` on `ORDERS` from `jsz_streams_consumers_config.json`
  with `server_name="n1"` → `messages == 600`, `bytes == 31984`,
  `last_seq == 600`, `num_subjects == 2`, `num_deleted == 0`,
  `consumer_count == 2`, `config["subjects"] == ["orders.>"]`,
  `config["retention"] == "limits"`, `config["num_replicas"] == 3`,
  `config["max_msgs"] == 10000`, `config["max_bytes"] == 10485760`,
  `config["max_age_seconds"] == 86400.0`, `utilisation["msgs_pct"] == 6.0`,
  `utilisation["bytes_pct"] == 0.3`, `cluster["leader"] == "n3"`,
  `cluster["raft_group"] == "S-R3F-VJA9dCiS"`, `authoritative is False`,
  `replicas_not_current == 1`. `JOBS` → `config["retention"] == "workqueue"`,
  `config["max_msgs"] == -1`, `utilisation == {"msgs_pct": None, "bytes_pct": None}`,
  `messages == 25`. `EVENTS` from `jsz_n3_streams_consumers.json` with
  `server_name="n3"` → `config is None`, `cluster["raft_group"] is None`,
  `cluster["replicas"] == []`, `authoritative is True`, `messages == 40`.
  Same stream (`ORDERS`) from the n3 fixture with `server_name="n3"` →
  `authoritative is True`, `replicas_not_current == 0`.
- `shape_consumer` table from §4.8: `billing` (n1, `stream_last_seq=600`)
  → `lag == 530`, `num_pending == 330`, `num_ack_pending == 20`,
  `authoritative is True`, `flags == ["backlog", "ack_pending"]`,
  `config["filter_subject"] == "orders.created"`,
  `config["ack_wait_seconds"] == 30.0`, `config["max_deliver"] == 5`,
  `config["paused"] is False`; `worker` (`stream_last_seq=25`) →
  `lag == 23`, `num_redelivered == 2`, `flags == ["ack_pending", "redeliveries"]`,
  `authoritative is False`; `audit` → `lag == 600`, `flags == []`,
  `config["ack_policy"] == "none"`, `config["ack_wait_seconds"] is None`,
  `config["max_deliver"] == -1`; `stale` from the n3 fixture
  (`stream_last_seq=40`, `server_name="n3"`) → `config is None`,
  `flags == ["backlog", "never_delivered"]`, `lag == 40`,
  `authoritative is True`. A synthetic consumer with `pause_until` one day
  in the future → `"paused"` in `flags`.
- `shape_route(routez.json["routes"][0])` → `remote_name == "n2"`,
  `rtt_ms` approx `0.122`, `pending_size == 0`, `compression == "off"`,
  `did_solicit is True`; `summarize_routes(routez.json)` → `count == 8`,
  `[p["remote_name"] for p in peers] == ["n2", "n3"]`, each
  `connections == 4`, `pending_size_total == 0`, `subscriptions_total == 200`;
  `summarize_routes(routez_solo.json)` → `count == 0`, `peers == []`.
- `shape_gatewayz(gatewayz.json)` → `outbound_count == 0`,
  `inbound_count == 0`; `shape_leafz(leafz.json)` → `count == 0`.

### 9.3 `tests/integrations/nats/test_diagnostics.py`

With `patched_client`:

- `get_server_status` on `CLUSTER_ROUTES` → `available is True`,
  `server["version"] == "2.14.6"`, `health["ok"] is True`,
  `jetstream["enabled"] is True`, `warnings == []`; `/healthz` 503 JSON →
  `available is True`, `health["ok"] is False`, `health["error"]` set;
  `/healthz` connection error → `health is None`, one warning; `/varz` 404
  → `available is False`, `error_kind == "not_found"`; unconfigured → error
  without network.
- `get_connections`: bad sort → error mentioning `Allowed` and no request
  made (custom handler asserts never called); bad state likewise;
  `sort="stop", state="open"` → error mentioning `closed` and no request;
  `sort="stop", state="closed"` → request made;
  `limit=0` → `limit == 25`, `limit=5000` → `1024`; query params arrive as
  `sort=pending&state=open&limit=25&subs=true`; closed-state fixture →
  `total == 19`, `summary["closed_by_reason"]["Protocol Violation"] == 1`;
  `/varz` failure → `slow_consumers is None` and a warning while
  `available is True`.
- `get_subscriptions`: no subject → `subject == ""`, `listed == 111`,
  `summary["application_subscriptions"] == 14`, `len(subscriptions) <= 10`,
  no `test` param sent; `subject="telemetry.cpu"` → `test=telemetry.cpu`
  sent with `subs=true`, `match_count == 1`; nomatch fixture →
  `match_count == 0`, `matches == []`.
- `get_jetstream_streams`: cluster → `jetstream_enabled is True`,
  `view == "server"`, `server_name == "n1"`, `clustered is True`,
  `meta_leader == "n1"`, `[s["name"] for s in streams] == ["ORDERS", "JOBS"]`
  (both non-authoritative, sorted by messages desc), `totals["messages"] == 625`,
  `totals["api_errors"] == 4`, `note` contains `n1`; `stream="JOBS"` → one
  stream; `stream="NOPE"` → `streams == []` and warning contains `NOPE`;
  params sent are `streams=true&config=true`; solo → `jetstream_enabled is False`,
  `hint` contains `-js`, `note is None`.
- `get_jetstream_consumers`: cluster → 3 consumers, first is
  `ORDERS/billing` with `authoritative is True`,
  `summary == {"consumers": 3, "authoritative": 1, "with_backlog": 1, "with_ack_pending": 2, "with_redeliveries": 1, "never_delivered": 0, "pending_total": 330, "ack_pending_total": 21}`;
  `stream="JOBS"` → one consumer `worker`; `consumer="billing"` → one;
  `consumer="nope"` → empty + warning; params sent are
  `streams=true&consumers=true&config=true`; solo → `jetstream_enabled is False`,
  `consumers == []`.
- `get_cluster_status`: cluster → the §5 clustered assertions; solo → the
  standalone assertions; `/routez` failure → `routes is None` and a warning
  while `available is True`.

### 9.4 Per-tool tests — `tests/tools/test_nats_<name>_tool.py` (six files)

Template: `tests/tools/test_aerospike_node_status_tool.py` and
`BaseToolContract` (`tests/tools/conftest.py` line 252). Each file:
registry metadata (name, `source == "nats"`, `ToolSurface.CHAT` only,
`injected_params == _NATS_INJECTED`, LLM-visible schema properties exactly
the table in §7); happy path through the tool function with
`patched_client` on the fixture routes asserting two or three key values
from §9.3; the evidence mapper summary string on the happy path and the
early return on `available: False`; and one degraded case per tool
(server status: healthz 503; connections: bad sort; subscriptions: nomatch;
streams and consumers: JetStream disabled; cluster: standalone).

### 9.5 `tests/tools/test_nats_tools_port_injection.py`

Parametrize over the six tool functions; for **each** name in
`_NATS_INJECTED` assert it is in `rt.injected_params`, absent from
`rt.public_input_schema["properties"]`, and still present in
`inspect.signature(fn).parameters`. Also assert `password` never appears in
`str(rt.public_input_schema)`.

### 9.6 `tests/e2e/nats/__init__.py` + `tests/e2e/nats/test_nats_e2e.py`

Mirror `tests/e2e/aerospike/test_aerospike_e2e.py` (classes at lines 26,
64, 86, 113, 174): store resolution via `classify_integrations`; invalid
record skipped; `nats_is_available` / `nats_extract_params`;
`verify_integrations(service="nats")` structure (with `patched_client` →
`status in ("passed", "missing")`); all six modules importable; all six
names present in `get_registered_tools("chat")` filtered by
`source == "nats"` (clear the registry cache before/after, as the aerospike
test does); one full tool path per tool.

### 9.7 Existing files

- `tests/tools/test_telemetry.py`: add the six tool names, sorted
  (`get_nats_cluster_status`, `get_nats_connections`,
  `get_nats_jetstream_consumers`, `get_nats_jetstream_streams`,
  `get_nats_server_status`, `get_nats_subscriptions`), to
  `_TOOLS_WITHOUT_DELIBERATE_CATCH` between `"get_mysql_table_stats",`
  (line 1002) and `"get_pods_on_node",` (line 1003). Read the comment at
  lines 852–866 first; if that block's rules say a tool with a
  `report_validation_failure` catch in its helper belongs in
  `_MIGRATED_TOOL_NAMES` instead, follow the block, not this sentence.
- `.github/ci/test_scope_rules.py`: add, before the `posthog_mcp` rule at
  line 256:
  ```python
  PathRule(
      "integrations/nats/",
      (
          "tests/integrations/test_nats.py",
          "tests/integrations/nats/test_diagnostics.py",
          "tests/integrations/nats/test_monitoring.py",
          "tests/tools/test_nats_cluster_status_tool.py",
          "tests/tools/test_nats_connections_tool.py",
          "tests/tools/test_nats_jetstream_consumers_tool.py",
          "tests/tools/test_nats_jetstream_streams_tool.py",
          "tests/tools/test_nats_server_status_tool.py",
          "tests/tools/test_nats_subscriptions_tool.py",
          "tests/tools/test_nats_tools_port_injection.py",
          "tests/tools/test_telemetry.py",
      ),
  ),
  ```
- `.github/ci/pytest-file-durations.json`: no entry required.

## 10. Wiring — file by file (exact insertion points at `55627a7c9`)

| File | Change |
| --- | --- |
| `config/constants/__init__.py` | §2.1 import block between `mysql` (lines 229–236) and `new_relic` (line 237); five `__all__` entries after `"MYSQL_USERNAME_ENV",` (line 677). |
| `integrations/registry.py` | After the `new_relic` `IntegrationSpec` (lines 459–467) add `IntegrationSpec(service="nats", aliases=("nats.io", "jetstream"), has_verifier=True, direct_effective=True, setup_order=48, verify_order=63)`. Both numbers verified unused at this commit (setup uses 0–45, 51–55, plus 46 reserved by the nginx branch and 47 by the keycloak branch; verify uses 0–60, 99, 100, plus 61 nginx and 62 keycloak). Placing it after `new_relic` rather than after `aerospike` avoids a merge conflict with those two branches. |
| `integrations/_catalog_impl.py` | Imports after `from integrations.mysql import classify as _classify_mysql` (line 334) and before `from integrations.new_relic import classify ...` (line 335): `from integrations.nats import classify as _classify_nats` and `from integrations.nats import nats_config_from_env`. Classifier map: `"nats": _classify_nats,` after `"rabbitmq": _classify_rabbitmq,` (line 545). Env loader after the rabbitmq block (its `except` ends at line 1777, blank line 1778) and before `try:\n    rds_config = rds_config_from_env()` (line 1779): `nats_config = nats_config_from_env()` / `if nats_config: integrations.append(_active_env_record("nats", nats_config.model_dump(exclude={"integration_id"})))`. |
| `integrations/cli.py` | After `_setup_mysql` (lines 719–722): `def _setup_nats() -> None:` importing `NATS_SETUP` from `integrations.nats.setup` and calling `_run_spec_setup(NATS_SETUP)`. Dispatch map: `"nats": _setup_nats,` immediately after `"mysql": _setup_mysql,` (line 830). |
| `integrations/effective_models.py` | `nats: EffectiveIntegrationEntry | None = None` after `mysql` (line 77). |
| `integrations/alert_source_catalog.py` | Routing (after `"rabbitmq"` line 56): `"nats": routing(("nats",), ("nats",)),`. Keywords (after `"rabbitmq": ("rabbitmq", "amqp"),` line 123): `"nats": ("nats", "jetstream", "nats-server", "slow consumer"),`. |
| `tools/registry_discovery.py` | `"integrations.nats.tools",` between `integrations.mysql.tools` (line 63) and `integrations.new_relic.tools` (line 64). |
| `.env.example` | New block after the RabbitMQ block (ends line 565, blank line 566) and before `# Better Stack Telemetry` (line 567): `# NATS (HTTP monitoring endpoint, default port 8222; username/password only for a proxy in front of it)` then `NATS_MONITOR_URL=`, `NATS_MONITOR_USERNAME=`, `NATS_MONITOR_PASSWORD=`, `NATS_VERIFY_SSL=true`, `NATS_TIMEOUT_SECONDS=10`, blank line. |
| `docs/docs.json` | `"nats",` between `"kafka",` (line 264) and `"prefect",` (line 265) in the `Workflows` group. |
| `docs/nats.mdx` | New page, §11. |

## 11. `docs/nats.mdx` outline (mirror `docs/rabbitmq.mdx` headings: Overview, Prerequisites, Setup with Options 1–2, Credentials, Tools, Verify, Troubleshooting, Security)

- Overview: what the tools answer; monitoring endpoint only; nats-server 2.10+.
- Prerequisites: start nats-server with `-m 8222` (or `http_port: 8222` /
  `https_port`), reachable from where opensre runs; for JetStream tools
  `-js`. State that the endpoint is unauthenticated by design and should sit
  on a private network or behind a proxy.
- Setup Option 1 (env): the five variables with the `.env.example` block.
  Option 2 (`opensre integrations setup nats`).
- Credentials: only needed for a proxy; how basic auth is sent.
- Tools: the six-row table from §7 with one line each, plus the two
  gotchas users must know — the per-server JetStream view (query each
  node) and that consumer counters are authoritative only from the
  consumer's leader (the output says which).
- Verify: `opensre integrations verify nats`, expected detail string.
- Troubleshooting: "did not answer HTTP" → client port; "non-JSON body" →
  wrong service/proxy; 401/403 → proxy credentials; JetStream disabled hint;
  empty stream list on a clustered server → look at another node.
- Security: read-only; password stored in the keyring tier; never expose
  8222 publicly.

Every sentence must change what the reader does (`AGENTS.md` docs rule).

## 12. Live verification (do this; report results in the PR)

Docker only, no host installs, images pinned. From the worktree root:

```bash
bash docs/superpowers/specs/nats/capture/run.sh
```

That script (idempotent; removes containers of the same name first)
creates the `opensre-nats` network, starts `n1`/`n2`/`n3`
(`nats:2.14.6-alpine`, `--js --cluster_name opensre-demo --routes ...`,
monitoring on `127.0.0.1:18222`–`18224`) and `solo` (no JetStream,
`127.0.0.1:18225`), waits for a JetStream meta leader, then with
`natsio/nats-box:0.19.7` creates streams `ORDERS` (R3, limits), `EVENTS`
(R1, memory), `JOBS` (R3, work queue), consumers `billing`, `audit`,
`worker`, `stale`, publishes 600 + 40 + 25 messages, fetches 50 acked + 20
unacked on `billing`, naks 5 on `worker`, and leaves a container publishing
`telemetry.cpu` with two subscribers.

Then:

```bash
NATS_MONITOR_URL=http://127.0.0.1:18222 uv run opensre integrations verify nats
NATS_MONITOR_URL=http://127.0.0.1:18225 uv run opensre integrations verify nats
```

Expect the first to pass with `nats-server 2.14.6 'n1' ... JetStream
enabled, cluster opensre-demo` and the second with `JetStream disabled,
cluster standalone`. Then exercise the tools against `18222` and `18224`:
`get_nats_jetstream_consumers` on `n1` shows `billing` authoritative with
330 pending; on `n3` it shows `audit` authoritative with 600 pending and
`stale` with 40; `get_nats_cluster_status` on any node shows 8 routes and
meta leader `n1` (or whichever node won the election in that run).

Tear down and clean up:

```bash
docker rm -f opensre-nats-n1 opensre-nats-n2 opensre-nats-n3 opensre-nats-solo opensre-nats-pub
docker network rm opensre-nats
docker image rm nats:2.14.6-alpine natsio/nats-box:0.19.7
```

## 13. Definition of done

- [ ] All §9 tests written first, seen failing, then passing.
- [ ] `make lint`, `make format-check`, `make typecheck`, `make test-scope` green in the worktree.
- [ ] §12 live check done; outputs pasted in the PR.
- [ ] `docs/nats.mdx` + `docs.json` entry present; `.env.example` block present.
- [ ] `uv run opensre integrations setup nats` and `verify nats` work end to end.
- [ ] `NATS_MONITOR_PASSWORD` never appears in any evidence dict, log line, verify detail or test output.
- [ ] PR body follows `.github/PULL_REQUEST_TEMPLATE.md`, references `Closes #5`, no AI attribution anywhere.
- [ ] `gh pr checks --watch` green; after merge, `main` CI monitored per `AGENTS.md`.

## 14. Risks / open questions

1. **Per-server JetStream view** (decision 6). Accepted for v1; the note
   in every JetStream response tells the caller to query other nodes. A
   cluster-wide view needs the wire protocol and is a separate ticket.
2. **Follower counters** (decision 7). `authoritative` makes it visible;
   the docs say to read the leader's value. Nothing hides the follower
   numbers, so a reader who ignores the flag can be misled.
3. **`/healthz` 503 shape not captured.** The `status`/`error` keys come
   from the nats-server source. If a real 503 body differs, `shape_healthz`
   still reports `ok: False` with `status_code: 503`, so nothing breaks;
   only the message text would be poorer.
4. **Slow-consumer close reasons not captured.** The prefix match
   `slow consumer` is from the source; `varz.slow_consumers` and
   `slow_consumer_stats` are the reliable counters and are always
   reported.
5. **`ALLOWED_CONNZ_SORT`** was verified value by value against 2.14.6;
   an older or newer server that rejects one answers 400 `invalid sorting
   option: ...` (fixture `connz_bad_sort.txt`), which maps cleanly to an
   `http` error, so a drift in the allowlist is visible, not silent.
6. **Large servers.** `/connz` is capped at 1024 by the server default
   and the tool clamps `limit` to that; `/subsz?subs=true` returns every
   subscription (111 here, potentially tens of thousands) and the tool
   keeps only `TOP_N` after summarising. `/jsz?streams=true&consumers=true&config=true`
   is unpaged; a server with thousands of consumers returns a large body.
   Acceptable for v1; `offset`/`limit` exist on `/jsz` for a follow-up.
7. **Accounts.** Everything is flattened across `account_details`; the
   `account` field on each stream keeps multi-account servers readable.
   `/accountz` and `/accstatz` are out of scope.
