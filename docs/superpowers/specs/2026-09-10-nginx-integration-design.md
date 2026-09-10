# nginx Integration — Architecture & Implementation Spec

Status: **design only — no implementation code has been written.** This
document is the complete brief for whoever implements the integration. It is
written so the implementer does not need to explore the repo: every file to
create or modify is named, every contract is spelled out, and every fixture
is embedded verbatim from a real nginx.

- Ticket: https://github.com/hellodk/opensre/issues/3 (PR must say `Closes #3`)
- Branch: `feat/nginx-integration` already exists, checked out as a git
  worktree at `../opensre-nginx` (sibling of this checkout) with its own
  `.venv` created by `uv sync`. Commit there. Base commit: `55627a7c9`
  (`main`). All line numbers below refer to that commit.
- Process rules that apply (from the repo owner's global instructions):
  1. Write the failing tests first (§9), run them, confirm they fail for the
     right reason, then implement (§3–§8), then make them pass.
  2. Never put any AI/assistant attribution in commits, PR title/body, or
     comments. Commit as the repo owner would: subject, why-body, `Closes #3`.
  3. Before pushing run, from the worktree: `make lint`, `make format-check`,
     `make typecheck`, `make test-scope`. Then `gh pr checks --watch`.
  4. The repo owner runs the full suite before merge; the implementer runs
     only the nginx test files plus `tests/tools/test_telemetry.py`.
  5. No installs on the host. Docker containers are fine (§10).

## 0. Decisions already made (do not re-open)

1. **One integration, `nginx`, covers both editions.** Open-source nginx is
   read via `ngx_http_stub_status_module`; NGINX Plus via its REST API. There
   is no "edition" config field: tools probe and report which edition
   answered.
2. **Transport is `httpx.Client`**, exactly as `integrations/rabbitmq/`
   does. No new dependencies.
3. **Logs are read from local files only** (`access.log`, `error.log`),
   bounded tail from the end. No SSH (no precedent in the repo, new
   dependency). nginx-in-Kubernetes is already covered by the existing
   `kubernetes_get_pod_logs` tool; log backends by their own integrations.
   Tool guidance points there.
4. **Six read-only tools**, all `ToolSurface.CHAT`, all returning structured
   `{"source": "nginx", "available": False, "error": ...}` instead of
   raising.
5. **Plus API version is autodetected** from `GET <api_path>/`, which returns
   a JSON list of supported versions; use the highest.
6. **Every connection/path parameter is injected** (resolved from the
   integration store or env). The LLM can never set host, port, credentials
   or log paths. This mirrors `tests/tools/test_aerospike_tools_port_injection.py`
   and extends it to all connection params.
7. Package layout follows `AGENTS.md` ("keep `__init__.py` a facade"):
   logic lives in focused modules, `__init__.py` only re-exports.

## 1. Architecture

### 1.1 Request flow

```
tool fn (integrations/nginx/tools/<name>_tool/__init__.py)
  -> builds NginxConfig from injected params
  -> calls one get_* function in integrations/nginx/diagnostics.py
       -> HTTP tools: client.build_client(config) -> client.fetch_text / fetch_json
            -> stub_status.parse_stub_status(text)          (OSS)
            -> plus_api.detect_api_version + plus_api.shape_*  (Plus)
       -> log tools: logs.open_log(path) -> logs.tail_lines -> logs.parse_* -> logs.summarize_*
  -> returns evidence dict {"source": "nginx", "available": bool, ...}
```

### 1.2 Edition detection

| Probe | Result | Meaning |
| --- | --- | --- |
| `GET {stub_status_path}` | 200 + parseable body | OSS stub_status available (also present on Plus if configured) |
| `GET {stub_status_path}` | 404 / 403 / body not parseable | stub_status not exposed |
| `GET {api_path}/` | 200 + JSON list of ints | NGINX Plus, API version = max(list) |
| `GET {api_path}/` | 404 | not Plus (or API not enabled) |
| either probe | 401 | credentials wrong → stop, report auth failure |
| either probe | transport error (refused, DNS, timeout) | stop, report |

`version` for OSS comes from the `Server` response header
(`nginx/1.27.5` → `1.27.5`). When `server_tokens off` hides it, the header
is just `nginx` → report `"unknown"`. Plus reports `version` and `build`
from `/nginx`.

### 1.3 Failure modes the client must map explicitly

`client.py` returns `(data, FetchError | None)`; it never raises for HTTP or
transport problems:

| Condition | `FetchError.kind` | message |
| --- | --- | --- |
| `httpx.RequestError` | `transport` | `nginx request failed: {err}` |
| HTTP 401 | `auth` | `nginx authentication failed (check username/password).` |
| HTTP 403 | `forbidden` | `nginx denied access to {path} (check allow/deny rules).` |
| HTTP 404 | `not_found` | `nginx endpoint not found: {path}` |
| other ≥ 400 | `http` | `nginx returned HTTP {code} for {path}: {text[:200]}` |
| `fetch_json` body not JSON | `body` | `nginx returned a non-JSON body for {path}` |

Use `http.HTTPStatus` constants everywhere (never numeric literals), in
source and tests.

## 2. Config

### 2.1 `config/constants/nginx.py` (new)

```python
"""nginx environment variable names."""

from __future__ import annotations

NGINX_HOST_ENV = "NGINX_HOST"
NGINX_PORT_ENV = "NGINX_PORT"
NGINX_SSL_ENV = "NGINX_SSL"
NGINX_VERIFY_SSL_ENV = "NGINX_VERIFY_SSL"
NGINX_USERNAME_ENV = "NGINX_USERNAME"
NGINX_PASSWORD_ENV = "NGINX_PASSWORD"
NGINX_STUB_STATUS_PATH_ENV = "NGINX_STUB_STATUS_PATH"
NGINX_API_PATH_ENV = "NGINX_API_PATH"
NGINX_ACCESS_LOG_PATH_ENV = "NGINX_ACCESS_LOG_PATH"
NGINX_ERROR_LOG_PATH_ENV = "NGINX_ERROR_LOG_PATH"
NGINX_TIMEOUT_SECONDS_ENV = "NGINX_TIMEOUT_SECONDS"

__all__ = [  # keep sorted
    "NGINX_ACCESS_LOG_PATH_ENV",
    "NGINX_API_PATH_ENV",
    "NGINX_ERROR_LOG_PATH_ENV",
    "NGINX_HOST_ENV",
    "NGINX_PASSWORD_ENV",
    "NGINX_PORT_ENV",
    "NGINX_SSL_ENV",
    "NGINX_STUB_STATUS_PATH_ENV",
    "NGINX_TIMEOUT_SECONDS_ENV",
    "NGINX_USERNAME_ENV",
    "NGINX_VERIFY_SSL_ENV",
]
```

Re-export from `config/constants/__init__.py`: add the import block
alphabetically after the `from config.constants.new_relic import (...)`
block (line 237) and the eleven names into `__all__` in sorted position
(the `NEW_RELIC_*` entries start at line 678; `NGINX_*` sorts right after
`NEW_RELIC_*`). `NGINX_PASSWORD` lands in the keyring tier automatically
because `config.env_file.is_sensitive_env_key` routes `*_PASSWORD` there.

### 2.2 `integrations/nginx/config.py` (new) — `NginxConfig`

```python
"""nginx connection settings and credential resolution."""

DEFAULT_NGINX_PORT = 80
DEFAULT_NGINX_STUB_STATUS_PATH = "/nginx_status"
DEFAULT_NGINX_API_PATH = "/api"
DEFAULT_NGINX_ACCESS_LOG_PATH = "/var/log/nginx/access.log"
DEFAULT_NGINX_ERROR_LOG_PATH = "/var/log/nginx/error.log"
DEFAULT_NGINX_TIMEOUT_SECONDS = 10
MIN_PORT = 1
MAX_PORT = 65535
_TRUTHY = ("true", "1", "yes")


class NginxConfig(StrictConfigModel):
    host: str = ""
    port: int = Field(default=DEFAULT_NGINX_PORT, ge=MIN_PORT, le=MAX_PORT)
    ssl: bool = False
    verify_ssl: bool = True
    username: str = ""
    password: str = ""
    stub_status_path: str = DEFAULT_NGINX_STUB_STATUS_PATH
    api_path: str = DEFAULT_NGINX_API_PATH
    access_log_path: str = DEFAULT_NGINX_ACCESS_LOG_PATH
    error_log_path: str = DEFAULT_NGINX_ERROR_LOG_PATH
    timeout_seconds: int = Field(default=DEFAULT_NGINX_TIMEOUT_SECONDS, gt=0)
    integration_id: str = ""
```

Validators (all `mode="before"`, same style as `RabbitMQConfig` in
`integrations/rabbitmq/__init__.py`):

- `host`, `username`: `str(value or "").strip()`.
- `password`: `str(value or "")` — **never strip**.
- `port`: `safe_int(value, DEFAULT_NGINX_PORT)` (from
  `infrastructure.text.coercion`), then the `Field` bounds reject 0/70000.
- `stub_status_path`, `api_path`: strip; empty → default; ensure leading
  `/`; strip trailing `/` (so `api_path + "/"` is the version-list URL).
- `access_log_path`, `error_log_path`: strip; empty → default. No other
  validation — the path is operator-supplied config, never LLM input.

Properties:

- `is_configured -> bool`: `bool(self.host)`.
- `base_url -> str`: `f"{'https' if self.ssl else 'http'}://{self.host}:{self.port}"`.
- `auth -> tuple[str, str] | None`: `(username, password)` when `username`
  is non-empty, else `None`.

Functions in the same module:

```python
def build_nginx_config(raw: dict[str, Any] | None) -> NginxConfig:
    return NginxConfig.model_validate(raw or {})

def nginx_config_from_env() -> NginxConfig | None:
    # host required; returns None when NGINX_HOST is empty.
    # port via safe_int; ssl/verify_ssl via .strip().lower() in _TRUTHY
    # (verify_ssl default "true"); password via
    # config.llm_credentials.resolve_env_credential(NGINX_PASSWORD_ENV) or "";
    # every other field via os.getenv(<ENV>, <default>).strip().

def nginx_is_available(sources: dict[str, dict]) -> bool:
    return bool(sources.get("nginx", {}).get("host"))

def nginx_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    # returns every NginxConfig field except integration_id/timeout_seconds,
    # defaults applied, as the injected kwargs for every tool.

def classify(credentials: dict[str, Any], record_id: str) -> tuple[NginxConfig | None, str | None]:
    # identical shape to integrations/rabbitmq/__init__.py classify():
    # build_nginx_config({... every field from credentials.get(...) with
    # defaults ..., "integration_id": record_id}); on exception
    # report_classify_failure(exc, logger=logger, integration="nginx",
    # record_id=record_id) and return (None, None); return (cfg, "nginx")
    # only when cfg.host is set.
```

No entry in `integrations/config_models.py` (rabbitmq precedent; aerospike's
`AerospikeIntegrationConfig` there is the other style — do not add both).

## 3. `integrations/nginx/client.py` (new)

```python
class FetchErrorKind(StrEnum):
    TRANSPORT = "transport"; AUTH = "auth"; FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"; HTTP = "http"; BODY = "body"

@dataclass(frozen=True)
class FetchError:
    kind: FetchErrorKind
    message: str

@dataclass(frozen=True)
class TextResponse:
    text: str
    server_header: str   # response.headers.get("server", "")

def build_client(config: NginxConfig) -> httpx.Client:
    return httpx.Client(base_url=config.base_url, auth=config.auth,
                        timeout=float(config.timeout_seconds), verify=config.verify_ssl,
                        headers={"Accept": "application/json, text/plain;q=0.9, */*;q=0.1"})

def fetch_text(client: httpx.Client, path: str) -> tuple[TextResponse | None, FetchError | None]
def fetch_json(client: httpx.Client, path: str) -> tuple[Any | None, FetchError | None]
def nginx_version_from_server_header(value: str) -> str
    # "nginx/1.27.5" -> "1.27.5"; "nginx" or "" -> "unknown"; "openresty/1.25.3.1" -> "1.25.3.1"
```

Status mapping per §1.3. `diagnostics.py` must call
`nginx_client.build_client(config)` through the module object
(`from integrations.nginx import client as nginx_client`) so tests can
`monkeypatch.setattr(nginx_client, "build_client", ...)` and swap in an
`httpx.MockTransport` — the same technique as `_mock_transport` /
`patched_client` in `tests/integrations/test_rabbitmq.py` lines 37–68.

## 4. `integrations/nginx/stub_status.py` (new)

Real body captured from `nginx:1.27.5` (trailing spaces are real; `$` marks
line ends):

```
Active connections: 1 $
server accepts handled requests$
 7 7 7 $
Reading: 0 Writing: 1 Waiting: 0 $
```

```python
_ACTIVE_RE = re.compile(r"Active connections:\s*(\d+)")
_COUNTERS_RE = re.compile(r"server accepts handled requests\s*\n\s*(\d+)\s+(\d+)\s+(\d+)")
_STATES_RE = re.compile(r"Reading:\s*(\d+)\s+Writing:\s*(\d+)\s+Waiting:\s*(\d+)")

@dataclass(frozen=True)
class StubStatus:
    active_connections: int
    accepts: int
    handled: int
    requests: int
    reading: int
    writing: int
    waiting: int

    @property
    def dropped(self) -> int:  # nginx docs: handled == accepts unless a resource limit was hit
        return max(self.accepts - self.handled, 0)

def parse_stub_status(text: str) -> StubStatus | None:
    # None when any of the three regexes fails to match (e.g. an HTML 200
    # page from a catch-all location, or a truncated body).
```

## 5. `integrations/nginx/plus_api.py` (new)

Payload shapes below were fetched live from the public NGINX Plus demo
(`https://demo.nginx.com/api/`, API v9, `nginx-plus-r37.0.2`) on
2026-09-09. Field names are therefore verified, not recalled.

```python
def detect_api_version(client, api_path) -> tuple[int | None, FetchError | None]
    # GET f"{api_path}/" -> [1,2,...,9]; return max(int items). A 200 with a
    # non-list body is FetchErrorKind.BODY.
def plus_path(api_path: str, version: int, endpoint: str) -> str
    # f"{api_path}/{version}/{endpoint}"
def fetch_plus(client, api_path, version, endpoint) -> tuple[Any | None, FetchError | None]
```

Shapers (pure functions over the JSON; every numeric read is
`safe_int(x.get(k), 0)`-style — the Plus API can emit `null`, see the
rabbitmq comment about `message_stats` nulls):

**`shape_nginx_info(payload)`** from `/nginx`:
```json
{"version":"1.29.8","build":"nginx-plus-r37.0.2","address":"18.193.151.235","generation":1,
 "load_timestamp":"2026-08-21T15:05:05.865Z","timestamp":"2026-09-09T20:10:23.595Z","pid":708,"ppid":707}
```
→ `{"version","build","address","pid","generation","load_timestamp","timestamp"}`.

**`shape_connections(payload)`** from `/connections`:
`{"accepted":12629085,"dropped":0,"active":3,"idle":9}` → same four keys.

**`shape_requests(payload)`** from `/http/requests`:
`{"total":64798700,"current":1}` → same two keys.

**`shape_server_zones(payload, zone_filter="")`** from `/http/server_zones`
(object keyed by zone name):
```json
{"hg.nginx.org": {"processing":0,"requests":55331,
  "responses":{"1xx":0,"2xx":55330,"3xx":0,"4xx":0,"5xx":0,"codes":{"200":55330},"total":55330},
  "discarded":1,"received":2904825,"sent":7176625425,
  "ssl":{"handshakes":27666,"session_reuses":0,"handshakes_failed":0, "...": "..."}}}
```
→ list of
`{"zone","processing","requests","responses_1xx","responses_2xx","responses_3xx","responses_4xx","responses_5xx","responses_total","error_rate_pct","discarded","received_bytes","sent_bytes","ssl_handshakes_failed"}`
sorted by `responses_5xx` desc then `zone`. `error_rate_pct` =
`round(100 * 5xx / total, 2)` or `0.0` when total is 0. `ssl` may be absent
→ `ssl_handshakes_failed = 0`. `zone_filter` keeps only the exact-name match.

**`shape_upstreams(payload, upstream_filter="")`** from `/http/upstreams`
(object keyed by upstream name; each has `peers`, `keepalive`, `zombies`,
`zone`). Peer shape (fields we consume):
```json
{"id":0,"server":"10.0.0.41:8084","name":"10.0.0.41:8084","backup":false,"weight":1,"state":"up",
 "active":0,"requests":2829379,"header_time":34,"response_time":34,
 "responses":{"1xx":0,"2xx":2659557,"3xx":35,"4xx":169693,"5xx":0,"codes":{"200":2659557},"total":2829285},
 "sent":1006696663,"received":8916770340,"fails":0,"unavail":0,
 "health_checks":{"checks":1609501,"fails":1,"unhealthy":1,"last_passed":true},
 "downtime":1024,"selected":"2026-09-09T20:10:21Z"}
```
→ list of
`{"upstream","zone","keepalive","zombies","peers_total","peers_up","peers_down","peers":[...]}`
where each peer is
`{"id","server","name","backup","weight","state","active","requests","responses_5xx","responses_total","fails","unavail","health_checks","health_check_fails","health_check_unhealthy","health_check_last_passed","header_time_ms","response_time_ms","downtime_ms","selected"}`.
`peers_up` counts `state == "up"`; `peers_down` counts states in
`("down", "unavail", "unhealthy", "checking")` (Plus states: `up`, `draining`,
`down`, `unavail`, `checking`, `unhealthy`). `health_checks` may be absent
(no active checks configured) → zeros and `last_passed = None`. Upstreams
sort by `peers_down` desc then name; peers keep API order.

**`shape_caches(payload)`** from `/http/caches`:
```json
{"http_cache":{"size":65536,"max_size":67108864,"cold":false,
 "hit":{"responses":55177,"bytes":7141787440},"stale":{"responses":0,"bytes":0},
 "updating":{"responses":0,"bytes":0},"revalidated":{"responses":0,"bytes":0},
 "miss":{"responses":0,"bytes":0,"responses_written":0,"bytes_written":0},
 "expired":{"responses":153,"bytes":19804824,"responses_written":153,"bytes_written":19804824},
 "bypass":{"responses":0,"bytes":0,"responses_written":0,"bytes_written":0}}}
```
→ list of
`{"cache","size_bytes","max_size_bytes","utilization_pct","cold","hit_responses","miss_responses","expired_responses","stale_responses","updating_responses","revalidated_responses","bypass_responses","hit_ratio_pct"}`.
`utilization_pct` = `round(100 * size / max_size, 2)` or `None` when
`max_size` is 0/absent (unbounded cache). `hit_ratio_pct` =
`round(100 * hit / (hit + miss + expired + bypass), 2)` or `0.0` when the
denominator is 0.

## 6. `integrations/nginx/logs.py` (new)

```python
DEFAULT_LOG_TAIL_LINES = 200
MAX_LOG_TAIL_LINES = 2000
_TAIL_CHUNK_BYTES = 64 * 1024
_MAX_TAIL_BYTES = 8 * 1024 * 1024
ERROR_LOG_LEVELS: tuple[str, ...] = ("debug", "info", "notice", "warn", "error", "crit", "alert", "emerg")
DEFAULT_MIN_ERROR_LEVEL = "warn"
TOP_N = 10

def open_log(path: str) -> tuple[Path | None, str | None]
    # Path(path); error messages: "log file not found: {path}",
    # "log path is not a regular file: {path}", "log file not readable: {path} ({exc})".
    # Note: on the official Docker image /var/log/nginx/*.log are symlinks to
    # /dev/stdout|stderr — Path.is_file() is False there → "not a regular file".
def tail_lines(path: Path, max_lines: int) -> list[str]
    # Open "rb", seek to end, read backwards in _TAIL_CHUNK_BYTES chunks until
    # max_lines+1 newlines seen or _MAX_TAIL_BYTES consumed or BOF. Decode
    # utf-8 errors="replace". Drop a trailing empty line. Return oldest→newest.
    # Never call read() without a bound.
def clamp_lines(lines: int | None) -> int   # None/<=0 → DEFAULT, > MAX → MAX
```

### 6.1 error.log

Real lines from `nginx:1.27.5`:

```
2026/09/09 20:10:53 [notice] 1#1: using the "epoll" event method
2026/09/09 20:10:53 [notice] 1#1: nginx/1.27.5
2026/09/09 20:10:53 [notice] 1#1: start worker process 21
2026/09/09 20:10:55 [error] 25#25: *5 connect() failed (111: Connection refused) while connecting to upstream, client: 172.17.0.1, server: , request: "GET /api/orders HTTP/1.1", upstream: "http://127.0.0.1:9/api/orders", host: "127.0.0.1:18080"
```

```python
ERROR_LINE_RE = re.compile(
    r"^(?P<timestamp>\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}) "
    r"\[(?P<level>[a-z]+)\] (?P<pid>\d+)#(?P<tid>\d+): (?:\*(?P<connection>\d+) )?(?P<rest>.*)$"
)
_CONTEXT_RE = re.compile(r', (?P<key>client|server|request|upstream|host|referrer): (?P<value>"[^"]*"|[^,]*)')

@dataclass(frozen=True)
class ErrorLogEntry:
    timestamp: str; level: str; pid: int; tid: int; connection: int | None
    message: str            # `rest` up to the first ", client: " (or whole rest)
    client: str; server: str; request: str; upstream: str; host: str   # "" when absent, quotes stripped

def parse_error_log_line(line: str) -> ErrorLogEntry | None
def summarize_error_log(lines, *, min_level, contains) -> dict[str, Any]
```

`summarize_error_log` output:
```
{"lines_read": int, "unparsed_lines": int, "matched": int,
 "min_level": str, "contains": str,
 "level_counts": {"error": 2, "notice": 22, ...},   # counted BEFORE filtering, all parsed lines
 "top_messages": [{"message": str, "count": int, "level": str}],  # after filtering, TOP_N
 "entries": [ErrorLogEntry as dict, ...]}          # after filtering, newest LAST, capped at lines requested
```
Level filter: keep entries whose `ERROR_LOG_LEVELS.index(level) >= index(min_level)`;
unknown `min_level` → treat as `DEFAULT_MIN_ERROR_LEVEL` and report the
effective value. `contains` is a case-insensitive substring over the raw line.
Unparsed lines (continuation lines of a multi-line message, garbage) are
counted, never raise.

### 6.2 access.log

Real lines (combined format, plus a trailing `$request_time` from a custom
`log_format`):

```
172.17.0.1 - - [09/Sep/2026:20:10:55 +0000] "GET / HTTP/1.1" 200 3 "-" "Mozilla/5.0 fixture" 0.000
172.17.0.1 - - [09/Sep/2026:20:10:55 +0000] "GET /api/orders HTTP/1.1" 502 157 "-" "curl/8.5.0" 0.000
172.17.0.1 - - [09/Sep/2026:20:10:55 +0000] "POST /api/orders HTTP/1.1" 502 157 "-" "curl/8.5.0" 0.000
172.17.0.1 - - [09/Sep/2026:20:10:55 +0000] "HEAD / HTTP/1.1" 200 0 "-" "curl/8.5.0" 0.000
```
Standard combined lines are identical minus the trailing float.

```python
ACCESS_LINE_RE = re.compile(
    r'^(?P<remote_addr>\S+) - (?P<remote_user>\S+) \[(?P<time_local>[^\]]+)\] '
    r'"(?P<request>[^"]*)" (?P<status>\d{3}) (?P<body_bytes_sent>\d+|-) '
    r'"(?P<referer>[^"]*)" "(?P<user_agent>[^"]*)"(?: (?P<request_time>\d+\.\d+))?'
)

@dataclass(frozen=True)
class AccessLogEntry:
    remote_addr: str; remote_user: str; time_local: str
    method: str; path: str; protocol: str     # from request; path has the query string removed;
                                              # malformed request ("-" or fewer than 3 tokens) -> method "", path=request, protocol ""
    status: int; body_bytes_sent: int; referer: str; user_agent: str
    request_time: float | None

def parse_access_log_line(line: str) -> AccessLogEntry | None
def summarize_access_log(lines) -> dict[str, Any]
```

`summarize_access_log` output:
```
{"lines_read": int, "unparsed_lines": int, "parsed": int,
 "status_classes": {"2xx": n, "3xx": n, "4xx": n, "5xx": n, "other": n},
 "status_codes": [{"status": 502, "count": n}],            # TOP_N desc
 "methods": {"GET": n, "POST": n},
 "top_paths": [{"path": str, "count": int}],               # TOP_N
 "top_paths_5xx": [{"path": str, "count": int}],           # TOP_N, only status >= 500
 "top_paths_4xx": [{"path": str, "count": int}],           # TOP_N
 "top_client_ips": [{"remote_addr": str, "count": int}],   # TOP_N
 "top_user_agents": [{"user_agent": str, "count": int}],   # TOP_N
 "bytes_sent_total": int,
 "request_time": {"present": bool, "p50": float|None, "p95": float|None, "max": float|None,
                  "slowest": [{"path","method","status","request_time"}]}}  # TOP_N by request_time desc
 "first_timestamp": str, "last_timestamp": str}            # time_local of first/last parsed line
```
Percentiles: sort the floats, `p = values[min(len-1, int(round(q*(len-1))))]`.
`present` is False and the rest `None`/`[]` when no line carried a
`request_time`.

## 7. `integrations/nginx/diagnostics.py` (new) — the `get_*` functions

Every function: `if not config.is_configured: return _error("Not configured.")`;
wrap the body in `try/except Exception as err` →
`report_validation_failure(err, logger=logger, integration="nginx", method="<fn name>")`
then `return _error(str(err))`, where
`_error(msg, **extra) = tool_unavailable("nginx", msg, **extra)`.

```python
PLUS_REQUIRED_MESSAGE = (
    "NGINX Plus API not found at {api_path} — this tool requires NGINX Plus. "
    "Open-source nginx exposes only stub_status; use get_nginx_server_status instead."
)

def get_server_status(config) -> dict
def get_upstream_health(config, upstream: str = "") -> dict
def get_server_zones(config, zone: str = "") -> dict
def get_cache_status(config) -> dict
def get_error_log(config, lines: int | None = None, min_level: str = DEFAULT_MIN_ERROR_LEVEL, contains: str = "") -> dict
def get_access_log_summary(config, lines: int | None = None) -> dict
```

**`get_server_status`** algorithm:
1. `fetch_text(stub_status_path)`. On `auth`/`transport` error → return
   `_error(err.message)` immediately. On `not_found`/`forbidden`/`http`, or
   200 with `parse_stub_status(...) is None` → remember the reason, go to 2.
   On parse success → return
   ```
   {"source":"nginx","available":True,"edition":"oss","status_source":"stub_status",
    "version": nginx_version_from_server_header(resp.server_header), "api_version": None,
    "connections":{"active":a,"accepted":acc,"handled":h,"dropped":acc-h,"reading":r,"writing":w,"waiting":wt,"idle":None},
    "requests":{"total":req,"current":None}}
   ```
   with `edition` upgraded to `"plus"` (and `api_version`, `version`, `build`
   filled from `/nginx`) if a **cheap** follow-up `detect_api_version`
   succeeds; ignore any error from that follow-up.
2. `detect_api_version`. On `not_found` → `_error("Neither stub_status at
   {stub_status_path} ({reason}) nor the NGINX Plus API at {api_path}
   answered on {base_url}. Enable `stub_status` (open-source) or the `api`
   directive (NGINX Plus).")`. On any other error → `_error(err.message)`.
3. Fetch `/nginx`, `/connections`, `/http/requests` → 
   ```
   {"source":"nginx","available":True,"edition":"plus","status_source":"plus_api",
    "version":info.version,"build":info.build,"api_version":v,
    "connections":{"active","accepted","handled":None,"dropped","reading":None,"writing":None,"waiting":None,"idle"},
    "requests":{"total","current"}}
   ```

**Plus-only functions**: `detect_api_version`; on `not_found` →
`_error(PLUS_REQUIRED_MESSAGE.format(api_path=config.api_path), edition="oss")`;
other errors → `_error(err.message)`. Then fetch the endpoint and shape.
Returns:
- upstreams: `{"source","available":True,"api_version","upstreams":[...],"upstreams_total","peers_down_total","filter":upstream}`;
  when `upstream` names a missing upstream → `available: True`,
  `upstreams: []`, plus `"warning": "upstream {name} not found"`.
- zones: `{"source","available":True,"api_version","zones":[...],"zones_total","responses_5xx_total","filter":zone}`.
- caches: `{"source","available":True,"api_version","caches":[...],"caches_total"}`.

**Log functions**: `open_log(path)` error → `_error(message, path=path)`.
Otherwise `tail_lines(path, clamp_lines(lines))` → summarize → merge
`{"source":"nginx","available":True,"path":path,"lines_requested":n}`.

## 8. Facade, verifier, setup, tools

### 8.1 `integrations/nginx/__init__.py` (new, facade only)

Docstring + imports + `__all__`. Re-export: `NginxConfig`,
`NginxValidationResult`, `DEFAULT_NGINX_PORT`, `DEFAULT_NGINX_STUB_STATUS_PATH`,
`DEFAULT_NGINX_API_PATH`, `DEFAULT_NGINX_ACCESS_LOG_PATH`,
`DEFAULT_NGINX_ERROR_LOG_PATH`, `DEFAULT_LOG_TAIL_LINES`, `MAX_LOG_TAIL_LINES`,
`ERROR_LOG_LEVELS`, `build_nginx_config`, `nginx_config_from_env`,
`nginx_is_available`, `nginx_extract_params`, `classify`,
`validate_nginx_config`, `get_server_status`, `get_upstream_health`,
`get_server_zones`, `get_cache_status`, `get_error_log`,
`get_access_log_summary`. Tools import **only** from this facade
(`tests/shared/test_tool_api_border.py` enforces it).

### 8.2 `integrations/nginx/validation.py` (new)

```python
@dataclass(frozen=True)
class NginxValidationResult:
    ok: bool
    detail: str

def validate_nginx_config(config: NginxConfig) -> NginxValidationResult
```
- no host → `(False, "nginx host is required.")`
- probe stub_status then Plus exactly as §1.2; `auth`/`transport` on the
  first probe → fail with that message.
- detail strings:
  - both: `NGINX Plus {build} (nginx/{version}, API v{n}) at {base_url}; stub_status also available at {stub_status_path}.`
  - stub only: `nginx {version} at {base_url}; stub_status OK at {stub_status_path}; NGINX Plus API not found at {api_path} (open-source edition assumed).`
  - Plus only: `NGINX Plus {build} (nginx/{version}, API v{n}) at {base_url}; stub_status not exposed at {stub_status_path}.`
  - neither: `(False, "Neither stub_status at {p} ({reason}) nor the NGINX Plus API at {api} ({reason}) answered on {base_url}.")`
- Unexpected exception → `report_validation_failure(..., integration="nginx", method="validate_nginx_config")`
  and `(False, f"nginx connection failed: {err}")`.
- Log paths are **not** probed by the verifier (opensre may run on a
  different host from the logs; the log tools report that per call).

### 8.3 `integrations/nginx/verifier.py` (new)

```python
verify_nginx = register_validation_verifier(
    "nginx", build_config=build_nginx_config, validate_config=validate_nginx_config,
)
```

### 8.4 `integrations/nginx/setup.py` (new)

`NGINX_SETUP = IntegrationSetupSpec(service="nginx", fields=(...), verify=verify_nginx)`
with fields, in this order (template: `integrations/aerospike/setup.py`):

| name | label | prompt | env_var | default | required | secret |
| --- | --- | --- | --- | --- | --- | --- |
| host | Host | `Host (e.g. localhost or nginx.example.net)` | NGINX_HOST_ENV | | yes | |
| port | Port | | NGINX_PORT_ENV | `80` | | |
| ssl | Use HTTPS | `Use HTTPS to reach nginx? (true/false)` | NGINX_SSL_ENV | `false` | | |
| verify_ssl | Verify TLS certificate | `Verify the TLS certificate? (true/false)` | NGINX_VERIFY_SSL_ENV | `true` | | |
| stub_status_path | stub_status path | `stub_status location (open-source nginx)` | NGINX_STUB_STATUS_PATH_ENV | `/nginx_status` | | |
| api_path | Plus API path | `NGINX Plus API location (ignored on open-source nginx)` | NGINX_API_PATH_ENV | `/api` | | |
| username | Username | `Basic-auth username (leave blank if the status endpoints are open)` | NGINX_USERNAME_ENV | | no | |
| password | Password | `Basic-auth password (leave blank if the status endpoints are open)` | NGINX_PASSWORD_ENV | | no | yes |
| access_log_path | Access log path | `Local access.log path (leave default if logs are not on this host)` | NGINX_ACCESS_LOG_PATH_ENV | `/var/log/nginx/access.log` | | |
| error_log_path | Error log path | `Local error.log path` | NGINX_ERROR_LOG_PATH_ENV | `/var/log/nginx/error.log` | | |

Export the `*_FIELD` name constants and `NGINX_SETUP` in `__all__`.

### 8.5 Tools — `integrations/nginx/tools/<pkg>/__init__.py` (six new packages)

`integrations/nginx/tools/__init__.py` is an empty facade (rabbitmq
precedent: empty file). Template for every tool:
`integrations/rabbitmq/tools/rabbitmq_node_health_tool/__init__.py`.

Shared decorator values: `source="nginx"`, `surfaces=(ToolSurface.CHAT,)`,
`is_available=nginx_is_available`, `extract_params=nginx_extract_params`,
`injected_params=_NGINX_INJECTED` where

```python
_NGINX_INJECTED = ("host", "port", "ssl", "verify_ssl", "username", "password",
                   "stub_status_path", "api_path", "access_log_path", "error_log_path")
```

Every tool function takes those ten as keyword params (`host: str` first,
the rest with the `NginxConfig` defaults), builds `NginxConfig(...)`, and
delegates. LLM-visible params are listed per tool.

| package | tool name | LLM params | delegates to |
| --- | --- | --- | --- |
| `nginx_server_status_tool` | `get_nginx_server_status` | none | `get_server_status` |
| `nginx_upstream_health_tool` | `get_nginx_upstream_health` | `upstream: str = ""` | `get_upstream_health` |
| `nginx_server_zones_tool` | `get_nginx_server_zones` | `zone: str = ""` | `get_server_zones` |
| `nginx_cache_status_tool` | `get_nginx_cache_status` | none | `get_cache_status` |
| `nginx_error_log_tool` | `get_nginx_error_log` | `lines: int = 200`, `min_level: str = "warn"`, `contains: str = ""` | `get_error_log` |
| `nginx_access_log_summary_tool` | `get_nginx_access_log_summary` | `lines: int = 200` | `get_access_log_summary` |

Descriptions (use verbatim; keep each under ~400 chars, no implicit string
concatenation inside list displays — extract long `use_cases` strings to
module constants if a line exceeds 100 chars):

- `get_nginx_server_status`: "Return nginx edition (open-source or Plus), version, and live connection/request counters: active, accepted, handled, dropped connections; reading/writing/waiting (stub_status) or idle (Plus); total requests. Works on any nginx exposing stub_status or the NGINX Plus API."
- `get_nginx_upstream_health`: "Return NGINX Plus upstream health: per upstream and per peer state (up/down/unavail/unhealthy), fails, unavailability count, health-check failures, 5xx responses and response times. Requires NGINX Plus; returns available=false on open-source nginx."
- `get_nginx_server_zones`: "Return NGINX Plus per-server-zone traffic: in-flight requests, 1xx–5xx response counts, error rate, discarded requests and bytes in/out, sorted by 5xx count. Requires NGINX Plus; returns available=false on open-source nginx."
- `get_nginx_cache_status`: "Return NGINX Plus content-cache status per cache zone: size vs max size, cold flag, hit/miss/expired/stale/bypass counts and hit ratio. Requires NGINX Plus; returns available=false on open-source nginx."
- `get_nginx_error_log`: "Tail the local nginx error.log (bounded, from the end) and return parsed entries with level, pid, client, request, upstream and host context, plus per-level counts and the most repeated messages. Reads a file on the host running OpenSRE; for nginx in Kubernetes use kubernetes_get_pod_logs instead."
- `get_nginx_access_log_summary`: "Tail the local nginx access.log (bounded, from the end) and summarize it: status-class distribution, top paths by 5xx/4xx, top client IPs and user agents, bytes sent, and request-time percentiles when the log format includes $request_time. Reads a file on the host running OpenSRE; for nginx in Kubernetes use kubernetes_get_pod_logs instead."

`use_cases` (3 per tool) — write them to match the descriptions; include
"Investigating 502/504 Bad Gateway from an nginx reverse proxy" on the
upstream and error-log tools and "Checking whether nginx is dropping
connections (accepts != handled)" on server status.

Evidence mappers (`record_evidence_entry(evidence, source=<tool name>,
label=<Title>, summary=...)`, return early when `not output.get("available")`):
- server status: `nginx {version} ({edition}): {active} active connections, {total} total requests` + `, {dropped} dropped` when dropped > 0.
- upstream health: `{upstreams_total} upstream(s), {peers_down_total} peer(s) down` + `: {names of first 3 down peers}`.
- server zones: `{zones_total} zone(s), {responses_5xx_total} 5xx responses` + `, worst: {zone} ({error_rate_pct}%)` for the first zone when its 5xx > 0.
- cache: `{caches_total} cache(s)` + per cache `{name} {hit_ratio_pct}% hit ratio` for the first two.
- error log: `{matched} entries at {min_level}+ in last {lines_read} lines` + `, top: {top_messages[0].message[:80]}`.
- access log: `{parsed} requests: {5xx} 5xx, {4xx} 4xx` + `, p95 {p95}s` when present.

No `SKILL.md`: per `docs/adding-tools-and-integrations.md` §"Skill guidance"
the Kubernetes pointer fits in the description, and one-per-vendor stubs are
explicitly discouraged.

## 9. Test plan (write these first; they must fail before §3–§8 exist)

Fixtures: create `tests/integrations/nginx/fixtures/` with
`stub_status.txt` (the §4 body, including trailing spaces),
`error.log` (all 24 lines from the container run: the 22 `[notice]` startup
lines plus the two `[error]` lines in §6.1 — reproduce with §10 if you want
the full file), `access.log` (the 8 lines the container produced: three
`GET /` 200 with `Mozilla/5.0 fixture`, `GET /missing` 200, `GET /api/orders`
502, `POST /api/orders` 502, `GET /nginx_status` 200, `HEAD / HTTP/1.1` 200,
all with trailing `0.000`), `access_combined.log` (same lines without the
trailing float), and `plus/` JSON files holding the §5 payloads verbatim
(`root.json`, `nginx.json`, `connections.json`, `requests.json`,
`server_zones.json`, `upstreams.json`, `caches.json`). Add
`tests/integrations/nginx/__init__.py`.

Transport mocking: a `_mock_transport(routes: dict[str, httpx.Response | str | dict])`
helper plus a `patched_client` fixture that monkeypatches
`integrations.nginx.client.build_client` to return
`httpx.Client(base_url=config.base_url, transport=httpx.MockTransport(handler))`
— copy the shape from `tests/integrations/test_rabbitmq.py` lines 37–68.
Unrouted paths return 404. Give the mock a `Server: nginx/1.27.5` header on
the stub_status route.

### 9.1 `tests/integrations/test_nginx.py`

- `TestNginxConfig`: defaults (port 80, paths, timeout 10, verify_ssl True);
  normalization (host/username stripped, password not stripped, api_path
  `/api/` → `/api`, `api` → `/api`, empty path → default); `is_configured`;
  `base_url` http/https; `auth` None when username empty; port bounds
  rejected (0, 70000) via `pydantic.ValidationError`; `safe_int` fallback
  for `port="abc"` → 80.
- `TestNginxEnv`: `nginx_config_from_env` returns None without `NGINX_HOST`;
  loads every var (`monkeypatch.setenv` for all eleven); reads the password
  through `resolve_env_credential` (patch
  `integrations.nginx.config.resolve_env_credential`, assert called once with
  `"NGINX_PASSWORD"`); `NGINX_SSL=1` → True, `NGINX_VERIFY_SSL=no` → False.
- `TestNginxExtractParams` / `test_is_available`: full dict with defaults;
  `{}` → not available.
- `TestClassify`: via `integrations.catalog.classify_integrations` with a
  store record (host + custom port + username/password + paths) → resolved
  `"nginx"` entry has those values; empty host → skipped.
- `TestStubStatusParser`: fixture → exact seven numbers and `dropped == 0`;
  `accepts=10, handled=8` synthetic body → `dropped == 2`; HTML body → None;
  truncated body (first two lines only) → None; body with `\r\n` line ends
  → parses.
- `TestClient`: each §1.3 row through `fetch_text`/`fetch_json` (401, 403,
  404, 500, `httpx.ConnectError` via a handler that raises, non-JSON 200);
  `nginx_version_from_server_header` for `nginx/1.27.5`, `nginx`, `""`,
  `openresty/1.25.3.1`.
- `TestPlusApi`: `detect_api_version` returns 9 for `[1,...,9]`, `BODY`
  error for `{"not":"a list"}`, `NOT_FOUND` for 404; every `shape_*` against
  the fixture JSON asserting the derived fields (`error_rate_pct`,
  `peers_up`/`peers_down`, `utilization_pct`, `hit_ratio_pct`), `null`
  numeric fields coerced to 0, absent `health_checks`/`ssl` tolerated,
  `zone`/`upstream` filters.
- `TestValidate`: the four detail outcomes of §8.2 plus 401 → fail,
  connection refused → fail, no host → fail. Assert the detail contains the
  edition words and paths.

### 9.2 `tests/integrations/nginx/test_logs.py`

- `tail_lines`: `tmp_path` file with 5000 lines → `max_lines=200` returns the
  last 200 in order; file smaller than requested → all lines; empty file →
  `[]`; file without trailing newline → last partial line included; a 20 MiB
  file of one-byte lines → returns at most `MAX_LOG_TAIL_LINES` and reads at
  most `_MAX_TAIL_BYTES` (assert via the returned count, and patch
  `_TAIL_CHUNK_BYTES` small to exercise the multi-chunk loop).
- `open_log`: missing → "not found"; directory → "not a regular file";
  unreadable (chmod 000, skip if running as root) → "not readable".
- `parse_error_log_line`: the `[error]` fixture line → every field
  (`connection == 5`, `client == "172.17.0.1"`, `server == ""`,
  `request == "GET /api/orders HTTP/1.1"`, `upstream == "http://127.0.0.1:9/api/orders"`,
  `host == "127.0.0.1:18080"`, `message == "connect() failed (111: Connection refused) while connecting to upstream"`);
  a `[notice]` line without `*cid` → `connection is None`; garbage → None.
- `summarize_error_log`: fixture with default `min_level="warn"` → `matched == 2`,
  `level_counts == {"notice": 22, "error": 2}`, `top_messages[0]["count"] == 2`;
  `min_level="notice"` → 24; `contains="POST"` → 1; unknown level → falls
  back to warn and reports it; one garbage line appended → `unparsed_lines == 1`.
- `parse_access_log_line`: timed line → `request_time == 0.0`, `method/path/protocol`
  split, `status == 502`, `body_bytes_sent == 157`; combined line → `request_time is None`;
  `"-" 400 0` malformed request → `method == ""`; path `/a?b=1` → `/a`; garbage → None.
- `summarize_access_log`: timed fixture → `status_classes == {"2xx": 6, "3xx": 0, "4xx": 0, "5xx": 2, "other": 0}`,
  `top_paths_5xx == [{"path": "/api/orders", "count": 2}]`, `methods == {"GET": 6, "POST": 1, "HEAD": 1}`,
  `request_time["present"] is True`; combined fixture → `present is False`;
  `bytes_sent_total == 3+3+3+3+157+157+97+0`.

### 9.3 Per-tool tests — `tests/tools/test_nginx_<name>_tool.py` (six files)

Each mirrors `tests/tools/test_aerospike_node_status_tool.py`:
`BaseToolContract` subclass; `test_metadata` (name, `source == "nginx"`,
`injected_params == _NGINX_INJECTED`, LLM-visible schema properties exactly
the table in §8.5); `test_run_happy_path` patching the `get_*` symbol on the
tool module; one full-path test through `patched_client` (or `tmp_path` log
files for the log tools) asserting the shaped output; one unavailable case
(Plus tools: 404 on `/api/` → `available is False`, `"NGINX Plus"` in error;
log tools: missing file → `available is False`, path in error; server
status: neither endpoint → error names both paths). Log tools also assert
`lines=99999` is clamped to `MAX_LOG_TAIL_LINES` and `lines=0` to the default.

### 9.4 `tests/tools/test_nginx_tools_port_injection.py`

Parametrize over the six tool functions; for **each** name in
`_NGINX_INJECTED` assert it is in `rt.injected_params`, absent from
`rt.public_input_schema["properties"]`, and still present in
`inspect.signature(fn).parameters`. Also assert the LLM-visible properties
equal the §8.5 table.

### 9.5 `tests/e2e/nginx/__init__.py` + `tests/e2e/nginx/test_nginx_e2e.py`

Mirror `tests/e2e/aerospike/test_aerospike_e2e.py`: store resolution via
`classify_integrations`; invalid record skipped; `nginx_is_available` /
`nginx_extract_params`; `verify_integrations(service="nginx")` structure
(with `patched_client` routing stub_status → `status in ("passed", "missing")`);
all six modules importable; all six names present in
`get_registered_tools("chat")` filtered by `source == "nginx"` (clear the
registry cache before/after, as the aerospike test does); one full tool
path per tool.

### 9.6 Existing files

- `tests/tools/test_telemetry.py`: add the six tool names, sorted, to
  `_TOOLS_WITHOUT_DELIBERATE_CATCH` (the aerospike entries sit at lines
  949–951; `get_nginx_*` sorts after `get_new_relic_*`/before
  `get_opensearch_*` — find the neighbours with grep). Read the comment at
  lines 852–866 first; if that block's rules say a tool with a
  `report_validation_failure` catch in its helper belongs elsewhere, follow
  the block, not this sentence.
- `.github/ci/test_scope_rules.py`: add, before the `posthog_mcp` rule at
  line 256:
  ```python
  PathRule(
      "integrations/nginx/",
      (
          "tests/integrations/test_nginx.py",
          "tests/integrations/nginx/test_logs.py",
          "tests/tools/test_nginx_access_log_summary_tool.py",
          "tests/tools/test_nginx_cache_status_tool.py",
          "tests/tools/test_nginx_error_log_tool.py",
          "tests/tools/test_nginx_server_status_tool.py",
          "tests/tools/test_nginx_server_zones_tool.py",
          "tests/tools/test_nginx_tools_port_injection.py",
          "tests/tools/test_nginx_upstream_health_tool.py",
          "tests/tools/test_telemetry.py",
      ),
  ),
  ```
- `.github/ci/pytest-file-durations.json`: no entry required (aerospike has
  none; the splitter tolerates unknown files).

## 10. Wiring — file by file (exact insertion points at `55627a7c9`)

| File | Change |
| --- | --- |
| `config/constants/__init__.py` | §2.1 import block after line 237's `new_relic` block; eleven `__all__` entries after the `NEW_RELIC_*` group (~line 678+). |
| `integrations/registry.py` | After the aerospike `IntegrationSpec` (lines 171–177) add `IntegrationSpec(service="nginx", has_verifier=True, direct_effective=True, setup_order=46, verify_order=61)`. Both numbers verified unused at this commit (setup uses 0–45, 51–55; verify uses 0–60, 99, 100). |
| `integrations/_catalog_impl.py` | Imports next to lines 264–265: `from integrations.nginx import classify as _classify_nginx` and `from integrations.nginx import nginx_config_from_env` (alphabetical among the vendor imports). Classifier map (line 521 area): `"nginx": _classify_nginx,`. Env loader after the aerospike block ending line 1144: `nginx_config = nginx_config_from_env()` / `if nginx_config: integrations.append(_active_env_record("nginx", nginx_config.model_dump(exclude={"integration_id"})))`. |
| `integrations/cli.py` | After `_setup_aerospike` (line 564): `def _setup_nginx() -> None:` importing `NGINX_SETUP` from `integrations.nginx.setup` and calling `_run_spec_setup(NGINX_SETUP)`. Dispatch map: `"nginx": _setup_nginx,` after line 832. |
| `integrations/effective_models.py` | `nginx: EffectiveIntegrationEntry | None = None` after `rabbitmq` (line 47). |
| `integrations/alert_source_catalog.py` | Routing (line 52 area): `"nginx": routing(("nginx",), ("nginx",)),`. Keywords (line 117 area): `"nginx": ("nginx", "openresty", "bad gateway", "gateway timeout"),`. |
| `tools/registry_discovery.py` | `"integrations.nginx.tools",` between `new_relic` (line 64) and `openobserve` (line 65). |
| `.env.example` | New block before `# RabbitMQ management API` (line 558): `# nginx (stub_status on open-source nginx, REST API on NGINX Plus; log paths are local files)` then the eleven vars with the §2.2 defaults and empty `NGINX_HOST`/`NGINX_USERNAME`/`NGINX_PASSWORD`. |
| `docs/docs.json` | `"nginx",` after `"kubernetes",` (line 189, Cloud group). |
| `docs/nginx.mdx` | New page, §11. |

## 11. `docs/nginx.mdx` outline (mirror `docs/aerospike.mdx`)

Frontmatter title "nginx", description "Connect nginx or NGINX Plus so
OpenSRE can read connection counters, upstream health, and local logs
during investigations". Sections: intro (one paragraph: what is read, both
editions, read-only); **Prerequisites** (`stub_status` location for
open-source with the exact `location = /nginx_status { stub_status; allow
<opensre ip>; deny all; }` snippet; `api` directive for Plus; network reach;
optional basic auth; logs only when OpenSRE runs on the nginx host or has
the directory mounted); **Setup** Options 1–3 exactly like aerospike with
the eleven env vars in a table; **Tools** six subsections, one sentence
each plus an `<Info>` block stating the Plus-only tools return
`available: false` on open-source nginx and the log tools read local files
only, pointing at the Kubernetes pod-logs tool; **Verify** with the
stub-only expected output; **Known limitations** (no SSH, Plus stream/
resolver/limit endpoints not covered, log formats other than combined
(+`$request_time`) count as unparsed, Docker image logs are stdout symlinks);
**Troubleshooting** table (404 on stub_status → add the location; 403 →
allow/deny; Plus tools unavailable → open-source edition; "not a regular
file" → container symlink, use `docker logs`/pod logs); **Security best
practices** (restrict status endpoints by IP or basic auth, read-only user,
secrets in `.env`).

Per `AGENTS.md` docs rule: every sentence must change what the reader does.

## 12. Live verification (do this; report results in the PR)

**Open-source path** (Docker, no host installs). Config file:

```nginx
upstream dead_backend { server 127.0.0.1:9; }
log_format timed '$remote_addr - $remote_user [$time_local] "$request" $status $body_bytes_sent "$http_referer" "$http_user_agent" $request_time';
server {
    listen 80;
    access_log /var/log/nginx/access.log timed;
    location = /nginx_status { stub_status; }
    location / { return 200 "ok\n"; }
    location /api/ { proxy_pass http://dead_backend; }
}
```

```bash
docker run -d --rm --name opensre-nginx-live -p 127.0.0.1:18080:80 \
  -v "$PWD/default.conf:/etc/nginx/conf.d/default.conf:ro" nginx:1.27.5
curl -s http://127.0.0.1:18080/ >/dev/null; curl -s http://127.0.0.1:18080/api/orders >/dev/null
# logs: the image symlinks /var/log/nginx/*.log to stdout/stderr, so
# `docker exec cat` BLOCKS. Use docker logs and write to files instead:
docker logs opensre-nginx-live >/tmp/nginx-access.log 2>/tmp/nginx-error.log
NGINX_HOST=127.0.0.1 NGINX_PORT=18080 NGINX_ACCESS_LOG_PATH=/tmp/nginx-access.log \
NGINX_ERROR_LOG_PATH=/tmp/nginx-error.log uv run opensre integrations verify nginx
docker stop opensre-nginx-live
```
Expect verify `passed` with the stub-only detail, `get_nginx_server_status`
edition `oss` version `1.27.5`, and the log tools returning the §6 numbers.
(The access log file will also contain the entrypoint's
`/docker-entrypoint.sh: ...` lines — they must show up as `unparsed_lines`,
not errors.)

**NGINX Plus path**: there is no free Plus image. Use the public read-only
demo: `NGINX_HOST=demo.nginx.com NGINX_PORT=443 NGINX_SSL=true` — its
`/api/` answers (v9, `nginx-plus-r37.0.2` on 2026-09-09) and `/nginx_status`
returns 404, which exercises the "Plus only" branch of the verifier and all
three Plus tools against live data. Do not hammer it; one verify plus one
call per tool.

## 13. Definition of done

- [ ] All §9 tests written first, seen failing, then passing.
- [ ] `make lint`, `make format-check`, `make typecheck`, `make test-scope` green in the worktree.
- [ ] §12 open-source and Plus live checks done; outputs pasted in the PR.
- [ ] `docs/nginx.mdx` + `docs.json` entry present; `.env.example` block present.
- [ ] `uv run opensre integrations setup nginx` and `verify nginx` work end to end.
- [ ] PR body follows `.github/PULL_REQUEST_TEMPLATE.md`, references `Closes #3`, no AI attribution anywhere.
- [ ] `gh pr checks --watch` green; after merge, `main` CI monitored per `AGENTS.md`.

## 14. Risks / open questions

1. **`stub_status` behind a catch-all `location /`** returns 200 HTML →
   parser returns None → the code falls through to the Plus probe and then
   reports "neither answered" with the stub reason `body not recognised`.
   Acceptable; the docs troubleshooting row covers it.
2. **Access log formats**: only combined (+ optional trailing
   `$request_time`) is parsed. JSON or custom formats count as unparsed.
   Extending is a follow-up ticket, not this one.
3. **Log files larger than 8 MiB tail window** with very long lines could
   return fewer than `lines` entries; the response reports `lines_read`, so
   the caller can see it.
4. **Plus `state` vocabulary** beyond the six documented values would count
   as neither up nor down; `peers_total` still reports them.
