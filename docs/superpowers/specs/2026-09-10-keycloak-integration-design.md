# Keycloak Integration — Architecture & Implementation Spec

Status: **design only — no implementation code has been written.** This
document is the complete brief for whoever implements the integration. It is
written so the implementer does not need to explore the repo: every file to
create or modify is named, every contract is spelled out, and every payload
shape comes from fixtures captured against a real Keycloak 26.7.3. The
fixture files, and the scripts that produced them, are committed next to
this spec under `docs/superpowers/specs/keycloak/` (see §8); the implementer
copies the fixtures into the test tree.

- Ticket: https://github.com/hellodk/opensre/issues/4 (PR must say `Closes #4`)
- Branch: `feat/keycloak-integration`, checked out as a git worktree at
  `../opensre-keycloak` (sibling of the main checkout) with its own `.venv`
  created by `uv sync`. Commit there. Base commit: `55627a7c9` (`main`).
  All line numbers below refer to that commit.
- Process rules that apply (from the repo owner's global instructions):
  1. Write the failing tests first (§9), run them, confirm they fail for the
     right reason, then implement (§3–§8), then make them pass.
  2. Never put any AI/assistant attribution in commits, PR title/body, or
     comments. Commit as the repo owner would: subject, why-body, `Closes #4`.
  3. Before pushing run, from the worktree: `make lint`, `make format-check`,
     `make typecheck`, `make test-scope`. Then `gh pr checks --watch`.
  4. The repo owner runs the full suite before merge; the implementer runs
     only the keycloak test files plus `tests/tools/test_telemetry.py`.
  5. No installs on the host. Docker containers are fine (§12).

## 0. Decisions already made (do not re-open)

1. **One integration, `keycloak`, Keycloak 25 or newer only.** The
   management interface on port 9000 (`/health`, `/metrics`) and the
   `KC_BOOTSTRAP_ADMIN_*` bootstrap exist only from 25. Older servers are out
   of scope; the verifier does not try to detect them.
2. **Authentication is a confidential client with service accounts
   enabled, grant `client_credentials`.** No admin username/password, no
   `admin-cli` password grant, no offline tokens. The service-account user
   needs the `realm-management` client roles `view-realm`, `view-users`,
   `view-clients`, `view-events` in the target realm (verified: with exactly
   those four roles every read in §7 returns 200 and a `PUT` returns 403).
3. **One token per tool call.** The token is fetched at the start of each
   `get_*` function and discarded (the fixture token lifespan is 300 s and a
   tool call finishes in well under that). No caching, no refresh logic.
4. **Transport is `httpx.Client`**, exactly as `integrations/rabbitmq/`
   does. Two base URLs: `url` (Keycloak HTTP, Admin REST API) and the
   optional `management_url` (health + metrics). No new dependencies.
5. **Six read-only tools**, all `ToolSurface.CHAT`, all returning structured
   `{"source": "keycloak", "available": False, "error": ...}` instead of
   raising. No writes, ever (no session logout, no user unlock).
6. **Every connection parameter is injected** (resolved from the
   integration store or env). The LLM can never set the URL, realm, client
   or secret. Mirrors `tests/tools/test_aerospike_tools_port_injection.py`.
7. **Event-type values sent to Keycloak come from an allowlist.** An unknown
   `type` makes Keycloak answer HTTP 500 `unknown_error` (fixture
   `events_bad_type.json`), so the tool validates before sending.
8. **Server-side filters for admin events are not used.** Their query
   parameter names were not verified against 26.7.3 in this session; the
   tool filters the fetched page client-side. Server-side filtering is a
   follow-up ticket if it is ever needed.
9. **Version is best-effort.** `/admin/serverinfo` returns `systemInfo`
   (which carries `version`) only to master-realm administrators. A
   realm-scoped service account gets HTTP 200 without `systemInfo`,
   `memoryInfo` or `cpuInfo` (verified: fixtures `serverinfo_sa.json` vs
   `serverinfo_admin.json`). The server-status tool reports `version: null`
   with `version_visible: false` in that case and explains why.
10. Package layout follows `AGENTS.md` ("keep `__init__.py` a facade"):
    logic lives in focused modules, `__init__.py` only re-exports.
11. New docs navigation group **"Identity"** in `docs/docs.json` (no
    existing group fits an identity provider).

## 1. Architecture

### 1.1 Request flow

```
tool fn (integrations/keycloak/tools/<name>_tool/__init__.py)
  -> builds KeycloakConfig from injected params
  -> calls one get_* function in integrations/keycloak/diagnostics.py
       -> client.build_client(config)               (httpx.Client, base_url = config.url)
       -> client.fetch_token(client, config)        POST /realms/{auth_realm}/protocol/openid-connect/token
       -> client.admin_get(client, token, path, params)   GET /admin/realms/{realm}/...
            -> admin_api.shape_* (pure functions over the JSON)
       -> (server status only) client.build_management_client(config)
            -> client.mgmt_get(...) GET /health/ready, /health/live, /metrics
            -> metrics.parse_prometheus_text + metrics.shape_metrics
  -> returns evidence dict {"source": "keycloak", "available": bool, ...}
```

### 1.2 Endpoints used (all verified live against 26.7.3, see `fixtures/index.json`)

| Purpose | Method + path | Fixture |
| --- | --- | --- |
| Token | `POST /realms/{auth_realm}/protocol/openid-connect/token` (form: `grant_type=client_credentials`, `client_id`, `client_secret`) | `token_client_credentials.json` |
| Server info | `GET /admin/serverinfo` | `serverinfo_sa.json`, `serverinfo_admin.json` |
| Realm | `GET /admin/realms/{realm}` | `realm.json` |
| User count | `GET /admin/realms/{realm}/users/count` → bare integer | `users_count.json` |
| Clients | `GET /admin/realms/{realm}/clients?briefRepresentation=true` | `clients_brief.json` |
| Session stats | `GET /admin/realms/{realm}/client-session-stats` | `client_session_stats.json` |
| Events config | `GET /admin/realms/{realm}/events/config` | `events_config.json`, `master_events_config.json` |
| User events | `GET /admin/realms/{realm}/events?type=LOGIN_ERROR&max=100[&first=N][&user={id}]` | `events_login_error.json`, `events_login_error_paged.json`, `events_for_carol.json`, `events_all.json` |
| Admin events | `GET /admin/realms/{realm}/admin-events?max=100[&first=N]` | `admin_events.json`, `admin_events_paged.json` |
| User lookup | `GET /admin/realms/{realm}/users?username={u}&exact=true` / `?email={e}&exact=true` | `user_search_alice.json`, `user_search_by_email.json`, `user_search_nobody.json` |
| Brute force | `GET /admin/realms/{realm}/attack-detection/brute-force/users/{id}` | `brute_force_carol.json`, `brute_force_alice.json` |
| User sessions | `GET /admin/realms/{realm}/users/{id}/sessions` | `user_sessions_alice.json`, `user_sessions_carol.json` |
| Health | `GET {management_url}/health/ready`, `/health/live` | `mgmt_health_ready.json`, `mgmt_health_live.json` |
| Metrics | `GET {management_url}/metrics` (Prometheus text) | `mgmt_metrics.txt` |

`/health` on the **main** port answers 404
`{"error": "Unable to find matching target resource method"}`
(`mgmt_health_on_main_port.json`) — that is why `management_url` is a
separate setting.

### 1.3 Failure modes the client must map explicitly

`client.py` returns `(data, FetchError | None)`; it never raises for HTTP or
transport problems. Real bodies are quoted from the fixtures.

Token endpoint (`fetch_token`):

| Condition | Real body | `FetchError.kind` | message |
| --- | --- | --- | --- |
| `httpx.RequestError` | — | `transport` | `Keycloak request failed: {err}` |
| 401, `error` = `unauthorized_client` or `invalid_client` | `{"error":"unauthorized_client","error_description":"Invalid client or Invalid client credentials"}` (bad secret), `{"error":"invalid_client", ...same description}` (unknown client id) | `auth` | `Keycloak rejected client '{client_id}' in realm '{auth_realm}': {error_description} (check KEYCLOAK_CLIENT_ID / KEYCLOAK_CLIENT_SECRET).` |
| 401, `error_description` = `Public client not allowed to retrieve service account` | `{"error":"unauthorized_client","error_description":"Public client not allowed to retrieve service account"}` | `auth` | `Keycloak client '{client_id}' is a public client; enable Client authentication and Service accounts roles on it.` |
| 404 | `{"error":"Realm does not exist"}` | `not_found` | `Keycloak realm '{auth_realm}' does not exist at {url}.` |
| other ≥ 400 | | `http` | `Keycloak token endpoint returned HTTP {code}: {text[:200]}` |
| 200 but no `access_token` key | | `body` | `Keycloak token response has no access_token.` |

The secret must never appear in any message, log line or evidence dict.

Admin API (`admin_get`) and management (`mgmt_get`):

| Condition | Real body | `FetchError.kind` | message |
| --- | --- | --- | --- |
| `httpx.RequestError` | — | `transport` | `Keycloak request failed: {err}` |
| 401 | `{"error":"HTTP 401 Unauthorized"}` | `auth` | `Keycloak rejected the access token for {path}.` |
| 403 | `{"error":"HTTP 403 Forbidden"}` | `forbidden` | `Keycloak denied {path}: the service account of client '{client_id}' needs the realm-management roles view-realm, view-users, view-clients and view-events in realm '{realm}'.` |
| 404 | `{"error":"Realm not found."}` / `{"error":"User not found"}` | `not_found` | `Keycloak returned 404 for {path}: {body.error or text[:120]}` |
| other ≥ 400 | 500 `{"error":"unknown_error","error_description":"For more on this error consult the server log."}` | `http` | `Keycloak returned HTTP {code} for {path}: {text[:200]}` |
| body not JSON when JSON expected | | `body` | `Keycloak returned a non-JSON body for {path}` |

Use `http.HTTPStatus` constants everywhere (never numeric literals), in
source and tests.

## 2. Config

### 2.1 `config/constants/keycloak.py` (new)

```python
"""Keycloak environment variable names."""

from __future__ import annotations

KEYCLOAK_URL_ENV = "KEYCLOAK_URL"
KEYCLOAK_MANAGEMENT_URL_ENV = "KEYCLOAK_MANAGEMENT_URL"
KEYCLOAK_REALM_ENV = "KEYCLOAK_REALM"
KEYCLOAK_AUTH_REALM_ENV = "KEYCLOAK_AUTH_REALM"
KEYCLOAK_CLIENT_ID_ENV = "KEYCLOAK_CLIENT_ID"
KEYCLOAK_CLIENT_SECRET_ENV = "KEYCLOAK_CLIENT_SECRET"
KEYCLOAK_VERIFY_SSL_ENV = "KEYCLOAK_VERIFY_SSL"
KEYCLOAK_TIMEOUT_SECONDS_ENV = "KEYCLOAK_TIMEOUT_SECONDS"

__all__ = [  # keep sorted
    "KEYCLOAK_AUTH_REALM_ENV",
    "KEYCLOAK_CLIENT_ID_ENV",
    "KEYCLOAK_CLIENT_SECRET_ENV",
    "KEYCLOAK_MANAGEMENT_URL_ENV",
    "KEYCLOAK_REALM_ENV",
    "KEYCLOAK_TIMEOUT_SECONDS_ENV",
    "KEYCLOAK_URL_ENV",
    "KEYCLOAK_VERIFY_SSL_ENV",
]
```

Re-export from `config/constants/__init__.py`: add the import block between
the `from config.constants.kafka import (...)` block (lines 182–188) and the
`from config.constants.kubernetes import (...)` block (line 189), and the
eight names into `__all__` after `"KAFKA_SECURITY_PROTOCOL_ENV",` (line 647)
and before the first `"KUBECONFIG_*"` entry. `KEYCLOAK_CLIENT_SECRET` lands
in the keyring tier automatically: `config.env_key_sensitivity.is_sensitive_env_key`
treats a terminal `secret` token as sensitive (line 22 of that module).

### 2.2 `integrations/keycloak/config.py` (new) — `KeycloakConfig`

```python
"""Keycloak connection settings and credential resolution."""

DEFAULT_KEYCLOAK_TIMEOUT_SECONDS = 10
_TRUTHY = ("true", "1", "yes")
_ALLOWED_SCHEMES = ("http://", "https://")


class KeycloakConfig(StrictConfigModel):
    url: str = ""
    management_url: str = ""
    realm: str = ""
    auth_realm: str = ""
    client_id: str = ""
    client_secret: str = ""
    verify_ssl: bool = True
    timeout_seconds: int = Field(default=DEFAULT_KEYCLOAK_TIMEOUT_SECONDS, gt=0)
    integration_id: str = ""
```

Validators (all `mode="before"`, same style as `RabbitMQConfig` in
`integrations/rabbitmq/__init__.py` lines 52–110):

- `url`, `management_url`: `str(value or "").strip().rstrip("/")`; when
  non-empty and not starting with one of `_ALLOWED_SCHEMES` raise
  `ValueError("Keycloak URL must start with http:// or https://")`.
- `realm`, `auth_realm`, `client_id`: `str(value or "").strip()`.
- `client_secret`: `str(value or "")` — **never strip**.
- `verify_ssl`: bool passthrough; strings via `.strip().lower() in _TRUTHY`.
- `timeout_seconds`: `safe_int(value, DEFAULT_KEYCLOAK_TIMEOUT_SECONDS)`.
- A `model_validator(mode="after")` sets `auth_realm = realm` when
  `auth_realm` is empty.

Properties:

- `is_configured -> bool`: all of `url`, `realm`, `client_id`,
  `client_secret` non-empty.
- `token_path -> str`: `f"/realms/{auth_realm}/protocol/openid-connect/token"`.
- `admin_realm_path -> str`: `f"/admin/realms/{realm}"`.
- `has_management_url -> bool`: `bool(self.management_url)`.

Functions in the same module:

```python
def build_keycloak_config(raw: dict[str, Any] | None) -> KeycloakConfig:
    return KeycloakConfig.model_validate(raw or {})

def keycloak_config_from_env() -> KeycloakConfig | None:
    # url, realm and client_id required; returns None when any is empty.
    # client_secret via config.llm_credentials.resolve_env_credential(KEYCLOAK_CLIENT_SECRET_ENV) or "";
    # returns None when the secret is empty too.
    # verify_ssl via os.getenv(..., "true").strip().lower() in _TRUTHY;
    # timeout via safe_int(os.getenv(..., "10"), 10); the rest via os.getenv(<ENV>, "").strip().

def keycloak_is_available(sources: dict[str, dict]) -> bool:
    kc = sources.get("keycloak", {})
    return bool(kc.get("url") and kc.get("realm") and kc.get("client_id") and kc.get("client_secret"))

def keycloak_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    # returns url, management_url, realm, auth_realm, client_id, client_secret, verify_ssl
    # (defaults "", "", "", "", "", "", True) — the injected kwargs for every tool.

def classify(credentials: dict[str, Any], record_id: str) -> tuple[KeycloakConfig | None, str | None]:
    # identical shape to integrations/rabbitmq/__init__.py classify() (lines 575–596):
    # build_keycloak_config({... every field from credentials.get(...) with defaults ...,
    # "integration_id": record_id}); on exception
    # report_classify_failure(exc, logger=logger, integration="keycloak", record_id=record_id)
    # and return (None, None); return (cfg, "keycloak") only when cfg.is_configured.
```

No entry in `integrations/config_models.py` (rabbitmq precedent).

## 3. `integrations/keycloak/client.py` (new)

```python
class FetchErrorKind(StrEnum):
    TRANSPORT = "transport"; AUTH = "auth"; FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"; HTTP = "http"; BODY = "body"

@dataclass(frozen=True)
class FetchError:
    kind: FetchErrorKind
    message: str

def build_client(config: KeycloakConfig) -> httpx.Client:
    return httpx.Client(base_url=config.url, timeout=float(config.timeout_seconds),
                        verify=config.verify_ssl, headers={"Accept": "application/json"})

def build_management_client(config: KeycloakConfig) -> httpx.Client:
    # same, base_url=config.management_url, Accept "application/json, text/plain;q=0.9"

def fetch_token(client: httpx.Client, config: KeycloakConfig) -> tuple[str | None, FetchError | None]
    # POST config.token_path, data={"grant_type": "client_credentials",
    # "client_id": config.client_id, "client_secret": config.client_secret}.
    # Mapping per §1.3 (token table). Returns the access_token string.

def admin_get(client: httpx.Client, token: str, config: KeycloakConfig, path: str,
              params: dict[str, Any] | None = None) -> tuple[Any | None, FetchError | None]
    # GET path with headers={"Authorization": f"Bearer {token}"}; mapping per §1.3 (admin table).
    # `path` is relative to config.url (e.g. config.admin_realm_path + "/events").

def mgmt_get_json(client: httpx.Client, path: str) -> tuple[Any | None, FetchError | None]
def mgmt_get_text(client: httpx.Client, path: str) -> tuple[str | None, FetchError | None]
    # management endpoints carry no auth; same status mapping.
```

`diagnostics.py` must call `keycloak_client.build_client(config)` and
`keycloak_client.build_management_client(config)` through the module object
(`from integrations.keycloak import client as keycloak_client`) so tests can
`monkeypatch.setattr(keycloak_client, "build_client", ...)` and swap in an
`httpx.MockTransport` — the same technique as `_mock_transport` /
`patched_client` in `tests/integrations/test_rabbitmq.py` lines 37–68.

`params` must be passed to httpx as a list of tuples when a key repeats;
this spec never repeats a key, so a dict is fine.

## 4. `integrations/keycloak/admin_api.py` (new) — shapers

Pure functions over the JSON. Every numeric read is
`safe_int(x.get(k), 0)`-style. Timestamps: Keycloak emits epoch
**milliseconds** in events/sessions/users (`time`, `start`, `lastAccess`,
`createdTimestamp`, `lastFailure`) and epoch **seconds** in
`failedLoginNotBefore`. Helper:

```python
def iso_from_ms(value: Any) -> str | None   # None when value is falsy/unparseable
def iso_from_s(value: Any) -> str | None
    # datetime.fromtimestamp(x, tz=timezone.utc).isoformat()
```

### 4.1 `shape_server_info(payload)` from `/admin/serverinfo`

Realm-scoped service account (verified, `serverinfo_sa.json`): top-level
keys are `profileInfo, features, cryptoInfo, themes, socialProviders,
identityProviders, providers, protocolMapperTypes, builtinProtocolMappers,
clientInstallations, componentTypes, passwordPolicies, enums,
parameterizedScopeTypes`. **No `systemInfo`, `memoryInfo`, `cpuInfo`.**
Master admin additionally gets (`serverinfo_admin.json`):

```json
"systemInfo": {"version": "26.7.3", "serverTime": "Thu Sep 10 18:11:50 GMT 2026",
  "uptime": "0 days, 0 hours, 0 minutes, 21 seconds", "uptimeMillis": 21844,
  "javaVersion": "21.0.12.1", "javaVendor": "Red Hat, Inc.", "javaVm": "OpenJDK 64-Bit Server VM",
  "javaVmVersion": "21.0.12.1+1-LTS", "javaRuntime": "OpenJDK Runtime Environment",
  "javaHome": "/usr/lib/jvm/java-21-openjdk-21.0.12.1.1-1.2.el9.x86_64", "osName": "Linux",
  "osArchitecture": "amd64", "osVersion": "7.0.0-31-generic", "fileEncoding": "UTF-8",
  "userName": "keycloak", "userDir": "/", "userTimezone": "GMT", "userLocale": "en_US"},
"memoryInfo": {"total": 46942650368, "totalFormated": "44768 MB", "used": 279484104,
  "usedFormated": "266 MB", "free": 46663166264, "freePercentage": 99, "freeFormated": "44501 MB"},
"cpuInfo": {"processorCount": 20}
```

`profileInfo` (both): `{"name": "default", "disabledFeatures": [...38 names...],
"previewFeatures": [...], "experimentalFeatures": [...]}`. `features` (both):
list of 73 objects
`{"name": "PASSKEYS", "label": "Passkeys", "type": "DEFAULT", "dependencies": ["WEB_AUTHN"], "enabled": true}`
(34 enabled in the fixture).

→
```
{"version": str | None, "version_visible": bool,          # systemInfo present?
 "server_time": str | None, "uptime": str | None, "uptime_ms": int | None,
 "java_version": str | None, "os": str | None,            # f"{osName} {osArchitecture}"
 "memory": {"total_bytes", "used_bytes", "free_pct"} | None,
 "processor_count": int | None,
 "profile": str,                                           # profileInfo.name, "" if absent
 "features_enabled": int, "features_total": int,
 "features_enabled_by_type": {"DEFAULT": n, "PREVIEW": n, ...},   # from feature["type"]
 "disabled_features_count": int}                            # len(profileInfo.disabledFeatures)
```

### 4.2 `shape_realm(payload)` from `/admin/realms/{realm}`

106 keys in the fixture; consume only these (all verified present):

```json
{"id": "64084eb3-...", "realm": "opensre-demo", "displayName": "OpenSRE demo", "enabled": true,
 "sslRequired": "external", "bruteForceProtected": true, "permanentLockout": false,
 "maxTemporaryLockouts": 0, "bruteForceStrategy": "MULTIPLE", "maxFailureWaitSeconds": 900,
 "failureFactor": 3, "waitIncrementSeconds": 60, "quickLoginCheckMilliSeconds": 1000,
 "minimumQuickLoginWaitSeconds": 60, "maxDeltaTimeSeconds": 43200,
 "eventsEnabled": true, "eventsExpiration": 604800, "eventsListeners": ["jboss-logging"],
 "adminEventsEnabled": true, "adminEventsDetailsEnabled": true,
 "accessTokenLifespan": 300, "ssoSessionIdleTimeout": 1800, "ssoSessionMaxLifespan": 36000,
 "offlineSessionIdleTimeout": 2592000, "registrationAllowed": false, "loginWithEmailAllowed": true,
 "verifyEmail": false, "resetPasswordAllowed": false, "rememberMe": false, "notBefore": 0,
 "defaultSignatureAlgorithm": "RS256", "otpPolicyType": "totp", "userManagedAccessAllowed": false}
```

`keycloakVersion`, `identityProviders`, `requiredActions`, `passwordPolicy`
are **absent** from this GET (verified) — do not read them. `eventsExpiration`
may be absent when never set (it is absent in `master_events_config.json`) →
`None`.

→ snake_case dict with the same names (`realm_id`, `realm`, `display_name`,
`enabled`, `ssl_required`, `brute_force_protected`, `permanent_lockout`,
`max_temporary_lockouts`, `brute_force_strategy`, `max_failure_wait_seconds`,
`failure_factor`, `wait_increment_seconds`, `quick_login_check_ms`,
`minimum_quick_login_wait_seconds`, `max_delta_time_seconds`,
`events_enabled`, `events_expiration_seconds`, `events_listeners`,
`admin_events_enabled`, `admin_events_details_enabled`,
`access_token_lifespan_seconds`, `sso_session_idle_seconds`,
`sso_session_max_seconds`, `offline_session_idle_seconds`,
`registration_allowed`, `login_with_email_allowed`, `verify_email`,
`reset_password_allowed`, `remember_me`, `not_before`,
`default_signature_algorithm`, `otp_policy_type`, `user_managed_access_allowed`).

### 4.3 `shape_clients(payload)` from `/clients?briefRepresentation=true`

List; per client consume `id, clientId, name, enabled, publicClient,
serviceAccountsEnabled, bearerOnly, protocol, standardFlowEnabled,
implicitFlowEnabled, directAccessGrantsEnabled, clientAuthenticatorType`
(all verified; `name` can be `"${client_account}"` for built-ins — keep as is).

→ list of `{"id", "client_id", "name", "enabled", "public_client",
"service_accounts_enabled", "bearer_only", "protocol", "standard_flow",
"implicit_flow", "direct_access_grants", "authenticator_type"}` sorted by
`client_id`, plus `summarize_clients(clients) -> {"total", "enabled",
"public", "confidential", "service_accounts", "bearer_only"}`
(`confidential = not public_client and not bearer_only`).

### 4.4 `shape_session_stats(payload)` from `/client-session-stats`

Verified shape — **counts are strings**:
```json
[{"offline": "0", "clientId": "demo-app", "active": "1", "id": "5c4cf4bb-..."}]
```
→ `{client_id: {"active": int, "offline": int, "id": str}}` via `safe_int`.
Clients with zero sessions are **absent** from this list.

### 4.5 `shape_events_config(payload)` from `/events/config`

```json
{"eventsEnabled": true, "eventsExpiration": 604800, "eventsListeners": ["jboss-logging"],
 "enabledEventTypes": ["LOGIN", "LOGIN_ERROR", ... 100+ ...],
 "adminEventsEnabled": true, "adminEventsDetailsEnabled": true}
```
→ `{"events_enabled", "events_expiration_seconds" (None when absent),
"events_listeners", "enabled_event_types_count", "admin_events_enabled",
"admin_events_details_enabled"}`.

### 4.6 `shape_user_event(payload)` from `/events`

Verified event shapes (`events_login_error.json`, `events_all.json`):

```json
{"id": "c0b25058-...", "time": 1789063904502, "type": "LOGIN_ERROR",
 "realmId": "64084eb3-...", "clientId": "demo-app", "userId": "ab876504-...",
 "ipAddress": "172.17.0.1", "error": "invalid_user_credentials",
 "details": {"auth_method": "openid-connect", "grant_type": "password",
             "client_auth_method": "client-secret", "username": "carol"}}
```
```json
{"id": "5b4a6d62-...", "time": 1789063906790, "type": "LOGIN_ERROR", "realmId": "...",
 "clientId": "demo-app", "ipAddress": "172.17.0.1", "error": "user_not_found",
 "details": {"auth_method": "openid-connect", "grant_type": "password",
             "client_auth_method": "client-secret", "username": "mallory"}}
```
```json
{"id": "e7a7304a-...", "time": 1789063910219, "type": "CLIENT_LOGIN_ERROR", "realmId": "...",
 "clientId": "demo-app", "ipAddress": "172.17.0.1", "error": "invalid_client",
 "details": {"reason": "Public client not allowed to retrieve service account",
             "grant_type": "client_credentials", "client_auth_method": "client-secret"}}
```

`userId` is absent for unknown users; `details` keys vary; `error` is
absent on success events (`type: "LOGIN"`). Newest first, as returned.

→ `{"id", "time": iso, "time_ms": int, "type", "client_id", "user_id" (None
when absent), "username": details.username or None, "ip_address",
"error": error or None, "reason": details.reason or None,
"grant_type": details.grant_type or None}`.

Error vocabulary seen live (use in tests and docs, do not hardcode a
closed set in code): `invalid_user_credentials`, `user_not_found`,
`user_disabled`, `user_temporarily_disabled`, `resolve_required_actions`
(details.reason `"Account is not fully set up"`), `invalid_client`,
`client_not_found`, `invalid_client_credentials`.

`summarize_user_events(events) -> dict`:
```
{"total": int,
 "by_error": {error: count},              # None error counted under "none"
 "by_username": [{"username", "count"}],  # TOP_N desc; None skipped
 "by_ip": [{"ip_address", "count"}],      # TOP_N
 "by_client": [{"client_id", "count"}],   # TOP_N
 "lockouts": int,                         # error == "user_temporarily_disabled"
 "unknown_users": int,                    # error == "user_not_found"
 "disabled_users": int,                   # error == "user_disabled"
 "first_time": iso | None, "last_time": iso | None}   # oldest / newest
```
`TOP_N = 10`.

### 4.7 `shape_admin_event(payload)` from `/admin-events`

Verified (`admin_events.json`):
```json
{"id": "a0949a49-...", "time": 1789063901044, "realmId": "64084eb3-...",
 "authDetails": {"realmId": "6b3818ed-...", "clientId": "c04a73b5-...", "userId": "669210bc-...",
                 "ipAddress": "172.17.0.1"},
 "operationType": "CREATE", "resourceType": "USER", "resourcePath": "users/d1779eaf-...",
 "representation": "{\"username\":\"dave\", ... }"}
```
Operation/resource pairs seen: `CREATE USER`, `CREATE CLIENT`,
`CREATE CLIENT_ROLE_MAPPING`. `representation` is a JSON **string**, present
only when `adminEventsDetailsEnabled`; `authDetails.userId` and `clientId`
are UUIDs (not names).

→ `{"id", "time", "time_ms", "operation_type", "resource_type",
"resource_path", "actor_user_id", "actor_client_id", "actor_realm_id",
"ip_address", "has_representation": bool}`. Never include the
representation body itself (it can carry PII / secrets).

`summarize_admin_events(events) -> {"total", "by_operation": {op: n},
"by_resource_type": {rt: n}, "by_actor": [{"actor_user_id", "count"}],
"first_time", "last_time"}`.

### 4.8 `shape_user(payload)` from `/users?...&exact=true` (list element)

```json
{"id": "d1779eaf-...", "username": "dave", "firstName": "Dave", "lastName": "Pending",
 "email": "dave@example.com", "emailVerified": false, "enabled": true,
 "createdTimestamp": 1789063901016, "totp": false, "disableableCredentialTypes": [],
 "requiredActions": ["VERIFY_EMAIL", "UPDATE_PASSWORD"], "notBefore": 0,
 "access": {"manage": false}}
```
`firstName`/`lastName`/`email` may be absent. → `{"id", "username",
"first_name", "last_name", "email", "email_verified", "enabled",
"created_at": iso, "totp", "required_actions": list, "not_before"}`.

### 4.9 `shape_brute_force(payload)` from `/attack-detection/brute-force/users/{id}`

Locked (`brute_force_carol.json`):
`{"failedLoginNotBefore": 1789063964, "numFailures": 3, "numTemporaryLockouts": 1,
"disabled": true, "numSecondaryAuthFailures": 0, "lastIPFailure": "172.17.0.1", "lastFailure": 1789063904504}`
Clean (`brute_force_alice.json`):
`{"failedLoginNotBefore": 0, "numFailures": 0, "numTemporaryLockouts": 0, "disabled": false,
"numSecondaryAuthFailures": 0, "lastIPFailure": "n/a", "lastFailure": 0}`

→ `{"locked": disabled, "failures": numFailures, "secondary_auth_failures",
"temporary_lockouts": numTemporaryLockouts,
"locked_until": iso_from_s(failedLoginNotBefore) when > 0 else None,
"last_failure": iso_from_ms(lastFailure) when > 0 else None,
"last_failure_ip": None when "n/a" or empty}`.

### 4.10 `shape_user_session(payload)` from `/users/{id}/sessions`

```json
{"id": "jJ81xmdndgGmcD3KEaGURgGD", "username": "alice", "userId": "cf7afc15-...",
 "ipAddress": "172.17.0.1", "start": 1789063901000, "lastAccess": 1789063901000,
 "rememberMe": false, "clients": {"5c4cf4bb-...": "demo-app"}, "transientUser": false}
```
→ `{"id", "ip_address", "start": iso, "last_access": iso, "remember_me",
"clients": sorted(values of clients)}`.

## 5. `integrations/keycloak/metrics.py` (new) — Prometheus text parser

There is no Prometheus text parser in the repo (checked at `55627a7c9`);
this module adds a minimal one. Real lines from `mgmt_metrics.txt`
(1351 lines, 165 metric names, `KC_METRICS_ENABLED=true` +
`KC_EVENT_METRICS_USER_ENABLED=true`):

```
# TYPE jvm_memory_used_bytes gauge
# HELP jvm_memory_used_bytes The amount of used memory
jvm_memory_used_bytes{area="heap",id="G1 Old Gen"} 9.3513592E7
jvm_memory_max_bytes{area="heap",id="G1 Eden Space"} -1.0
jvm_threads_live_threads 48.0
agroal_active_count{datasource="default"} 0.0
agroal_available_count{datasource="default"} 3.0
agroal_awaiting_count{datasource="default"} 0.0
agroal_max_used_count{datasource="default"} 3.0
http_server_active_requests{server_port="8080",url_scheme="http"} 0.0
http_server_active_requests{server_port="9000",url_scheme="http"} 1.0
http_server_requests_seconds_count{method="POST",outcome="CLIENT_ERROR",status="401",uri="/realms/{realm}/protocol/{protocol}/token"} 3.0
http_server_requests_seconds_count{method="GET",outcome="SERVER_ERROR",status="500",uri="/admin/realms/{realm}/events"} 1.0
process_uptime_seconds 23.614
process_cpu_usage 0.0
system_cpu_usage 0.0
vendor_cluster_size{cache_manager="DefaultCacheManager",node="11ba5445f1d7-41371"} 1.0
worker_pool_rejected_total{pool_name="vert.x-worker-thread",pool_type="worker"} 0.0
jvm_gc_pause_seconds_count{action="end of minor GC",cause="G1 Evacuation Pause",gc="G1 Young Generation"} 6.0
jvm_gc_pause_seconds_sum{action="end of minor GC",cause="G1 Evacuation Pause",gc="G1 Young Generation"} 0.055
keycloak_user_events_total{error="invalid_user_credentials",event="login",realm="opensre-demo"} 3.0
keycloak_user_events_total{error="",event="login",realm="opensre-demo"} 1.0
keycloak_user_events_total{error="",event="user_disabled_by_temporary_lockout",realm="opensre-demo"} 1.0
keycloak_user_events_total{error="client_not_found",event="client_login",realm="opensre-demo"} 1.0
jvm_info_total{runtime="OpenJDK Runtime Environment",vendor="Red Hat, Inc.",version="21.0.12.1+1-LTS"} 1.0
```

(`http_server_active_requests` is labelled per listener port — sum it.)
Note the exponent floats (`9.3513592E7`), the `-1.0` sentinel for
"unbounded", label values with spaces, and the `keycloak_user_events_total`
family that only exists with `KC_EVENT_METRICS_USER_ENABLED=true`. There is
no Keycloak version metric.

```python
_SAMPLE_RE = re.compile(r"^(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)(?:\{(?P<labels>.*)\})?\s+(?P<value>\S+)")
_LABEL_RE = re.compile(r'(?P<key>[A-Za-z_][A-Za-z0-9_]*)="(?P<value>(?:[^"\\]|\\.)*)"')

@dataclass(frozen=True)
class Sample:
    name: str
    labels: dict[str, str]     # use a tuple-of-pairs internally if hashing is needed
    value: float

def parse_prometheus_text(text: str) -> list[Sample]
    # skip blank and '#' lines; skip lines whose value is not a float
    # (NaN/+Inf parse via float(); keep them); never raise.
def shape_metrics(samples: list[Sample], realm: str) -> dict[str, Any]
```

`shape_metrics` output (every value `None` when its family is absent):

```
{"uptime_seconds": float,                                    # process_uptime_seconds
 "cpu": {"process_usage": float, "system_usage": float},
 "jvm": {"heap_used_bytes": sum(jvm_memory_used_bytes, area="heap"),
         "heap_max_bytes": sum(jvm_memory_max_bytes, area="heap", value >= 0) or None,
         "heap_used_pct": round(100*used/max, 1) or None,
         "threads_live": jvm_threads_live_threads,
         "gc_pause_count": sum(jvm_gc_pause_seconds_count),
         "gc_pause_seconds_total": round(sum(jvm_gc_pause_seconds_sum), 3),
         "java_version": jvm_info_total label version or None},
 "db_pool": [{"datasource", "active", "available", "awaiting", "max_used"}],   # per agroal datasource label
 "http": {"active_requests": sum(http_server_active_requests),
          "requests_total": sum(http_server_requests_seconds_count),
          "by_status_class": {"2xx": n, "3xx": n, "4xx": n, "5xx": n, "other": n},   # from label status
          "top_5xx": [{"uri", "method", "status", "count"}]},                          # TOP_N by count desc
 "cluster_size": vendor_cluster_size (first sample) or None,
 "worker_pool_rejected_total": sum(worker_pool_rejected_total),
 "realm_user_events": {                                       # keycloak_user_events_total with label realm == realm
     "present": bool,
     "login_success": count(event="login", error=""),
     "login_errors": count(event="login", error!=""),
     "login_errors_by_reason": {error: count},
     "client_login_errors": count(event="client_login", error!=""),
     "lockouts": count(event in ("user_disabled_by_temporary_lockout", "user_disabled_by_permanent_lockout"))},
 "metric_names_seen": int}
```

## 6. `integrations/keycloak/diagnostics.py` (new) — the `get_*` functions

Every function: `if not config.is_configured: return _error("Keycloak is not configured (url, realm, client_id and client_secret are required).")`;
wrap the body in `try/except Exception as err` →
`report_validation_failure(err, logger=logger, integration="keycloak", method="<fn name>")`
then `return _error(str(err))`, where
`_error(msg, **extra) = tool_unavailable("keycloak", msg, **extra)`.

Shared prologue `_open_session(config) -> tuple[httpx.Client, str] | FetchError`:
`client = keycloak_client.build_client(config)`; `token, err = fetch_token(...)`;
on error close the client and return the error. Every `get_*` returns
`_error(err.message, error_kind=err.kind)` when the prologue fails. Use the
client as a context manager so it is always closed.

Constants:

```python
DEFAULT_MAX_EVENTS = 100
MAX_EVENTS = 500
RECENT_USER_EVENTS = 20
ALLOWED_USER_EVENT_TYPES: frozenset[str] = frozenset({
    "LOGIN_ERROR", "CLIENT_LOGIN_ERROR", "IDENTITY_PROVIDER_LOGIN_ERROR",
    "CODE_TO_TOKEN_ERROR", "RESET_PASSWORD_ERROR", "LOGOUT_ERROR",
    "USER_DISABLED_BY_TEMPORARY_LOCKOUT", "USER_DISABLED_BY_PERMANENT_LOCKOUT",
})   # every name verified present in events_config.json enabledEventTypes
EVENTS_DISABLED_HINT = (
    "User events are disabled for realm '{realm}'. Enable them in the admin console under "
    "Realm settings > Events > User events settings (Save events), then retry."
)
ADMIN_EVENTS_DISABLED_HINT = (
    "Admin events are disabled for realm '{realm}'. Enable them under "
    "Realm settings > Events > Admin events settings (Save events), then retry."
)
MANAGEMENT_URL_UNSET = "KEYCLOAK_MANAGEMENT_URL is not set; health and metrics were not read."
```

```python
def get_server_status(config) -> dict
def get_realm_overview(config) -> dict
def get_login_failures(config, event_type: str = "LOGIN_ERROR", max_events: int = DEFAULT_MAX_EVENTS, username: str = "") -> dict
def get_admin_events(config, max_events: int = DEFAULT_MAX_EVENTS, resource_type: str = "", operation_type: str = "") -> dict
def get_client_sessions(config, client_id: str = "") -> dict
def get_user_status(config, username: str) -> dict
def clamp_max_events(value: int | None) -> int    # None/<=0 → DEFAULT_MAX_EVENTS, > MAX_EVENTS → MAX_EVENTS
```

**`get_server_status`**:
1. Prologue. `admin_get("/admin/serverinfo")` → on error `_error(...)`.
   `info = shape_server_info(payload)`.
2. `management`: when `not config.has_management_url` →
   `{"configured": False, "reachable": False, "url": "", "error": MANAGEMENT_URL_UNSET, "health": None, "liveness": None, "metrics": None}`.
   Otherwise open `build_management_client`, fetch `/health/ready` →
   `health = {"status": payload.status, "checks": [{"name", "status"}], "database_status": status of the check whose name contains "database" (case-insensitive) or None}`;
   `/health/live` → same shape; `/metrics` → `shape_metrics(parse_prometheus_text(text), config.realm)`.
   Any of the three failing sets that key to `None` and appends
   `err.message` to `management["errors"]`; `reachable` is `True` when at
   least one succeeded.
3. Return `{"source": "keycloak", "available": True, "url": config.url, "realm": config.realm, **info, "management": management}`.
   Add `"version_note": "Keycloak version is visible only to master-realm administrators; the configured service account is realm-scoped."`
   when `version_visible` is False.

Verified health bodies: ready →
`{"status":"UP","checks":[{"name":"Graceful Shutdown","status":"UP"},{"name":"Keycloak Initialized","status":"UP"},{"name":"Keycloak database connections async health check","status":"UP"}]}`;
live → `{"status":"UP","checks":[]}`.

**`get_realm_overview`**: prologue; `GET {admin_realm_path}` → on error
`_error`; `realm = shape_realm(...)`. Then best-effort (each failure appends
`err.message` to `warnings`, value → `None`): `GET .../users/count` (bare
int, e.g. `4`) → `users_total`; `GET .../clients?briefRepresentation=true` →
`clients = summarize_clients(...)`; `GET .../client-session-stats` →
`sessions = {"active": sum, "offline": sum, "clients_with_sessions": len}`.
Return `{"source", "available": True, "url", **realm, "users_total", "clients", "sessions", "warnings": [...]}`.

**`get_login_failures`**:
1. `event_type = event_type.strip().upper()`; if not in
   `ALLOWED_USER_EVENT_TYPES` → `_error(f"Unsupported event_type '{event_type}'. Allowed: {sorted list}")`
   **without** any network call.
2. Prologue; `GET .../events/config` → if `events_enabled` is False →
   `{"source", "available": True, "events_enabled": False, "event_type", "events": [], "summary": summarize_user_events([]), "hint": EVENTS_DISABLED_HINT.format(realm=...)}`.
3. `params = {"type": event_type, "max": clamp_max_events(max_events)}`. If
   `username` (stripped) is non-empty: resolve via `GET .../users?username=..&exact=true`;
   empty list → return `{"available": True, "events_enabled": True, "found_user": False, "username", "events": [], ...}`;
   otherwise `params["user"] = user["id"]` and set `found_user: True`.
4. `GET .../events` → shape each → `{"source", "available": True, "events_enabled": True, "event_type", "max_events": n, "username", "events": [...], "summary": summarize_user_events(events)}`.

**`get_admin_events`**: prologue; `GET .../events/config` → if
`admin_events_enabled` is False → `{"available": True, "admin_events_enabled": False, "events": [], "summary": ..., "hint": ADMIN_EVENTS_DISABLED_HINT}`.
Else `GET .../admin-events?max=clamp(...)`, shape, then filter client-side
by `resource_type.upper()` / `operation_type.upper()` when given (exact
match). Return `{"source", "available": True, "admin_events_enabled": True, "details_enabled": bool, "max_events", "filters": {"resource_type", "operation_type"}, "events", "summary"}`.
`events` reports the filtered list; `summary.total` is the filtered count;
add `"fetched": len(page)`.

**`get_client_sessions`**: prologue; `GET .../clients?briefRepresentation=true`
→ on error `_error`; `GET .../client-session-stats` → on error `_error`.
Merge: every client gets `active_sessions` / `offline_sessions` from the
stats map (0 when absent). `client_id` filter (exact) keeps one client; if
nothing matches → `available: True`, `clients: []`,
`warning: f"client '{client_id}' not found in realm '{realm}'"`.
Return `{"source", "available": True, "realm", "filter": client_id, "clients": [...], "totals": {"clients", "active_sessions", "offline_sessions", "clients_with_sessions"}}`
sorted by `active_sessions` desc then `client_id`.

**`get_user_status`**:
1. `username = username.strip()`; empty → `_error("username is required.")` (no network).
2. Prologue; `GET .../users?username={username}&exact=true`; if empty and
   `"@" in username` → `GET .../users?email={username}&exact=true`
   (`user_search_by_email.json` shows the email path returns the same
   representation). Still empty → `{"source", "available": True, "found": False, "username", "realm"}`.
3. `user = shape_user(list[0])`. Then best-effort with `warnings`:
   `GET .../attack-detection/brute-force/users/{id}` → `lockout`;
   `GET .../users/{id}/sessions` → `sessions = {"count", "sessions": [...]}`;
   `GET .../events/config` and, when `events_enabled`,
   `GET .../events?user={id}&max=RECENT_USER_EVENTS` → `recent_events` (shaped) else `[]` with `events_enabled: False`.
4. `diagnosis: list[str]`, in this order, only when true:
   `"account disabled"` (not enabled); `"temporarily locked by brute-force protection until {locked_until}"` (lockout.locked);
   `"{n} failed login(s), last from {ip} at {t}"` (failures > 0);
   `"required actions pending: {', '.join(required_actions)}"`;
   `"email not verified"`; `"no active sessions"` (count == 0);
   `"recent login errors: {comma-joined distinct errors}"` (from recent_events with error).
   Empty list means nothing is wrong.
5. Return `{"source", "available": True, "found": True, "realm", "user", "lockout", "sessions", "events_enabled", "recent_events", "diagnosis", "warnings"}`.

## 7. Facade, verifier, setup, tools

### 7.1 `integrations/keycloak/__init__.py` (new, facade only)

Docstring + imports + `__all__`. Re-export: `KeycloakConfig`,
`KeycloakValidationResult`, `DEFAULT_KEYCLOAK_TIMEOUT_SECONDS`,
`DEFAULT_MAX_EVENTS`, `MAX_EVENTS`, `ALLOWED_USER_EVENT_TYPES`,
`build_keycloak_config`, `keycloak_config_from_env`, `keycloak_is_available`,
`keycloak_extract_params`, `classify`, `validate_keycloak_config`,
`get_server_status`, `get_realm_overview`, `get_login_failures`,
`get_admin_events`, `get_client_sessions`, `get_user_status`. Tools import
**only** from this facade (`tests/shared/test_tool_api_border.py` enforces it).

### 7.2 `integrations/keycloak/validation.py` (new)

```python
@dataclass(frozen=True)
class KeycloakValidationResult:
    ok: bool
    detail: str

def validate_keycloak_config(config: KeycloakConfig) -> KeycloakValidationResult
```

- not configured → `(False, "Keycloak url, realm, client_id and client_secret are required.")`.
- `fetch_token` error → `(False, err.message)` (the §1.3 token messages
  already name the cause; the secret is never echoed).
- `GET {admin_realm_path}` error → `(False, err.message)`. The 403 message
  names the four roles; the 404 message (`Realm not found.`) names the realm.
- success → detail
  `Keycloak realm '{realm}' reachable at {url} as client '{client_id}' (realm enabled={enabled}, brute-force protection={on|off}, user events={on|off}, admin events={on|off})`.
- management (does **not** affect `ok`): if `has_management_url`, probe
  `/health/ready`; append `; management health {status} at {management_url}`
  on success or `; management URL {management_url} not reachable ({err.message})`
  on failure. If unset append `; management URL not set (health/metrics unavailable)`.
- Unexpected exception → `report_validation_failure(..., integration="keycloak", method="validate_keycloak_config")`
  and `(False, f"Keycloak connection failed: {err}")`.

### 7.3 `integrations/keycloak/verifier.py` (new)

```python
verify_keycloak = register_validation_verifier(
    "keycloak", build_config=build_keycloak_config, validate_config=validate_keycloak_config,
)
```
(`register_validation_verifier` signature: `integrations/verification/validation.py` line 60.)

### 7.4 `integrations/keycloak/setup.py` (new)

`KEYCLOAK_SETUP = IntegrationSetupSpec(service="keycloak", fields=(...), verify=verify_keycloak)`
with fields, in this order (template: `integrations/aerospike/setup.py`;
`SetupField` at `integrations/setup_flow.py` line 100):

| name | label | prompt | env_var | default | required | secret |
| --- | --- | --- | --- | --- | --- | --- |
| url | Server URL | `Keycloak base URL (e.g. https://sso.example.net)` | KEYCLOAK_URL_ENV | | yes | |
| realm | Realm | `Realm to diagnose (e.g. myapp)` | KEYCLOAK_REALM_ENV | | yes | |
| client_id | Client ID | `Confidential client with service accounts enabled (e.g. opensre)` | KEYCLOAK_CLIENT_ID_ENV | | yes | |
| client_secret | Client secret | `Client secret (Clients > opensre > Credentials)` | KEYCLOAK_CLIENT_SECRET_ENV | | yes | yes |
| auth_realm | Token realm | `Realm the client lives in (leave blank if same as Realm)` | KEYCLOAK_AUTH_REALM_ENV | | no | |
| management_url | Management URL | `Management interface URL for health/metrics (e.g. http://sso.example.net:9000; leave blank to skip)` | KEYCLOAK_MANAGEMENT_URL_ENV | | no | |
| verify_ssl | Verify TLS certificate | `Verify the TLS certificate? (true/false)` | KEYCLOAK_VERIFY_SSL_ENV | `true` | | |

Export the `*_FIELD` name constants and `KEYCLOAK_SETUP` in `__all__`.

### 7.5 Tools — `integrations/keycloak/tools/<pkg>/__init__.py` (six new packages)

`integrations/keycloak/tools/__init__.py` is an empty facade (rabbitmq
precedent: empty file). Template for every tool:
`integrations/rabbitmq/tools/rabbitmq_node_health_tool/__init__.py`.
Shared decorator values: `source="keycloak"`, `surfaces=(ToolSurface.CHAT,)`,
`is_available=keycloak_is_available`, `extract_params=keycloak_extract_params`,
`injected_params=_KEYCLOAK_INJECTED` where

```python
_KEYCLOAK_INJECTED = ("url", "management_url", "realm", "auth_realm", "client_id", "client_secret", "verify_ssl")
```

Every tool function takes those seven as keyword params (`url: str`,
`realm: str`, `client_id: str`, `client_secret: str` first, then
`management_url: str = ""`, `auth_realm: str = ""`, `verify_ssl: bool = True`),
builds `KeycloakConfig(...)`, and delegates. LLM-visible params per tool:

| package | tool name | LLM params | delegates to |
| --- | --- | --- | --- |
| `keycloak_server_status_tool` | `get_keycloak_server_status` | none | `get_server_status` |
| `keycloak_realm_overview_tool` | `get_keycloak_realm_overview` | none | `get_realm_overview` |
| `keycloak_login_failures_tool` | `get_keycloak_login_failures` | `event_type: str = "LOGIN_ERROR"`, `max_events: int = 100`, `username: str = ""` | `get_login_failures` |
| `keycloak_admin_events_tool` | `get_keycloak_admin_events` | `max_events: int = 100`, `resource_type: str = ""`, `operation_type: str = ""` | `get_admin_events` |
| `keycloak_client_sessions_tool` | `get_keycloak_client_sessions` | `client_id: str = ""` | `get_client_sessions` |
| `keycloak_user_status_tool` | `get_keycloak_user_status` | `username: str` (required) | `get_user_status` |

Note the name clash on `client_id`: the injected connection parameter is
`client_id` (the service-account client). The sessions tool's LLM-visible
filter must therefore be named **`filter_client_id`** in the tool signature
and mapped to `get_client_sessions(config, client_id=filter_client_id)`.
The table above lists the LLM-facing name; use `filter_client_id`.

Descriptions (use verbatim; keep each under ~400 chars; no implicit string
concatenation inside list displays — extract long `use_cases` strings to
module constants if a line exceeds 100 chars):

- `get_keycloak_server_status`: "Return Keycloak server status: readiness and liveness checks including the database check, JVM heap and GC, database connection pool usage, HTTP request counts by status class with the top 5xx endpoints, cluster size, and per-realm login success/failure counters from the metrics endpoint. Version is shown only when the service account is a master-realm admin."
- `get_keycloak_realm_overview`: "Return a Keycloak realm's operational settings and counts: enabled flag, SSL requirement, brute-force protection parameters (failure factor, lockout waits), user and admin event settings, token and session lifespans, total users, client mix (public/confidential/service-account) and active/offline session totals."
- `get_keycloak_login_failures`: "Return recent Keycloak login failure events for a realm (LOGIN_ERROR by default; other error event types selectable) with per-error, per-user, per-IP and per-client counts, lockout and unknown-user tallies. Optionally scope to one username. Requires user events to be enabled on the realm; reports when they are not."
- `get_keycloak_admin_events`: "Return recent Keycloak admin events (who changed what: operation type, resource type and path, acting user/client IDs, IP) with counts by operation, resource type and actor. Filter by resource type or operation type. Requires admin events to be enabled on the realm; reports when they are not."
- `get_keycloak_client_sessions`: "Return every client in a Keycloak realm with its enabled flag, client type (public, confidential, bearer-only, service account), enabled flows and current active and offline session counts. Optionally filter to one client id."
- `get_keycloak_user_status`: "Look up one Keycloak user by username or email and return account state (enabled, email verified, required actions, TOTP), brute-force lockout state (locked until, failure count, last failing IP), active sessions and recent login events, plus a short diagnosis list explaining why the user cannot log in."

`use_cases` (3 per tool) — write them to match the descriptions; include
"Investigating why a user cannot log in to an application behind Keycloak"
on the user-status and login-failures tools, "Checking whether Keycloak is
healthy and its database connection pool is saturated" on server status,
and "Auditing who changed a client or role in a realm" on admin events.

Evidence mappers (`record_evidence_entry(evidence, source=<tool name>,
label=<Title>, summary=...)`, return early when `not output.get("available")`):

- server status: `Keycloak {version or "version hidden"}: ready {health.status or "unknown"}, heap {heap_used_pct}%, db pool {active}/{active+available}` (omit pieces whose value is `None`) + `, {5xx} 5xx responses` when > 0.
- realm overview: `realm {realm}: {users_total} users, {clients.total} clients, {sessions.active} active sessions` + `, brute-force protection off` when `brute_force_protected` is False + `, user events off` when disabled.
- login failures: `{summary.total} {event_type} event(s)` + `, {lockouts} lockout(s)` when > 0 + `, top: {by_error first key} ({count})`; when `events_enabled` is False: `user events disabled on realm {realm}`.
- admin events: `{summary.total} admin event(s)` + `, top: {op} {resource_type}`; disabled → `admin events disabled on realm {realm}`.
- client sessions: `{totals.clients} client(s), {totals.active_sessions} active session(s)` + `, busiest: {client_id} ({n})` for the first client when `n > 0`.
- user status: `user {username}: {'; '.join(diagnosis) or 'no problems found'}`; `found` False → `user {username} not found in realm {realm}`.

No `SKILL.md` (per `docs/adding-tools-and-integrations.md` §"Skill guidance",
one-per-vendor stubs are discouraged; the descriptions carry the guidance).

## 8. Fixtures (committed with this spec)

`docs/superpowers/specs/keycloak/fixtures/` holds 43 files captured on
2026-09-10 from `quay.io/keycloak/keycloak:26.7.3` in dev mode with the §12
provisioning. `index.json` maps every file to its method, URL, status and
content-type. Redactions/trims: `token_client_credentials.json` has
`access_token` replaced by `<redacted>`; `serverinfo_sa.json` and
`serverinfo_admin.json` are trimmed to `profileInfo`, the first 6 of 73
`features`, and (admin only) `systemInfo`/`memoryInfo`/`cpuInfo` — the
`_note` key says so. Everything else is verbatim. No JWTs are present.
**Implementer step:** copy the whole directory to
`tests/integrations/keycloak/fixtures/` (that is where `load_fixture` in §9
reads from) and add `tests/integrations/keycloak/__init__.py`. Leave the
copy under `docs/` untouched; it is the record of what the server said.

The capture is reproducible: `docs/superpowers/specs/keycloak/capture/run.sh`
starts the pinned container and `capture/capture.py` (run from the repo root
with `uv run python docs/superpowers/specs/keycloak/capture/capture.py`)
provisions the realm, generates the events and writes every payload plus
`index.json` into a `fixtures/` directory next to the script. §12 is the
prose version of what that script does.

Fixture inventory by purpose:

| Purpose | Files |
| --- | --- |
| token OK / errors | `token_client_credentials.json`, `token_error_bad_secret.json` (401), `token_error_unknown_client.json` (401), `token_error_public_client.json` (401), `token_error_unknown_realm.json` (404) |
| admin API errors | `realm_unauthorized.json` (401), `realm_forbidden_master.json` (403, SA scoped to another realm), `realm_not_found.json` (404), `sa_write_forbidden.json` (403 on PUT), `user_get_bogus_id.json` (404 `User not found`), `events_bad_type.json` (500) |
| server / realm | `serverinfo_sa.json`, `serverinfo_admin.json`, `realm.json`, `users_count.json` (`4`), `clients_brief.json` (8 clients), `client_session_stats.json`, `events_config.json`, `master_events_config.json` (events off) |
| events | `events_all.json` (13), `events_login_error.json` (7), `events_login_error_paged.json` (first=2&max=2 → 2), `events_for_carol.json` (5), `master_events.json` (`[]`), `admin_events.json` (7), `admin_events_paged.json` (3) |
| users | `user_search_alice.json`, `user_search_by_email.json` (carol), `user_search_nobody.json` (`[]`), `user_search_prefix.json`, `user_get_dave.json` (required actions), `brute_force_carol.json` (locked), `brute_force_alice.json` (clean), `user_sessions_alice.json` (1), `user_sessions_carol.json` (`[]`) |
| management | `mgmt_health.json`, `mgmt_health_ready.json`, `mgmt_health_live.json`, `mgmt_health_started.json`, `mgmt_metrics.txt`, `mgmt_health_on_main_port.json` (404 on the main port), `openid_configuration_missing_realm.json` (404) |

Users in the fixture realm: `alice` (ok, one session), `bob` (disabled),
`carol` (locked after 3 wrong passwords; `failureFactor` 3), `dave`
(`VERIFY_EMAIL`, `UPDATE_PASSWORD` pending); unknown user `mallory` produced
the `user_not_found` event.

## 9. Test plan (write these first; they must fail before §3–§8 exist)

Transport mocking: a `_mock_transport(routes: dict[str, httpx.Response | str | dict])`
helper keyed by **path** (query string ignored; tests that need to assert
query params inspect `request.url.params` inside a custom handler) plus a
`patched_client` fixture that monkeypatches both
`integrations.keycloak.client.build_client` and
`build_management_client` to return `httpx.Client(base_url=..., transport=httpx.MockTransport(handler))`
— copy the shape from `tests/integrations/test_rabbitmq.py` lines 37–68.
Unrouted paths return 404 `{"error": "Realm not found."}`. A `_token_ok`
route for `/realms/opensre-demo/protocol/openid-connect/token` returning
`token_client_credentials.json` (with any string as `access_token`) is the
default in every happy-path test. A `load_fixture(name)` helper reads from
`tests/integrations/keycloak/fixtures/` (`json` for `.json`, text for `.txt`).

### 9.1 `tests/integrations/test_keycloak.py`

- `TestKeycloakConfig`: defaults (verify_ssl True, timeout 10, auth_realm
  falls back to realm); normalization (url trailing `/` stripped, realm/
  client_id stripped, secret **not** stripped); scheme validation rejects
  `sso.example.net` (no scheme) via `pydantic.ValidationError`;
  `is_configured` false when any of the four is missing; `token_path` and
  `admin_realm_path`; `safe_int` fallback for `timeout_seconds="abc"` → 10.
- `TestKeycloakEnv`: `keycloak_config_from_env` returns None without
  `KEYCLOAK_URL`, without `KEYCLOAK_CLIENT_SECRET`; loads every var
  (`monkeypatch.setenv` for all eight); reads the secret through
  `resolve_env_credential` (patch `integrations.keycloak.config.resolve_env_credential`,
  assert called once with `"KEYCLOAK_CLIENT_SECRET"`); `KEYCLOAK_VERIFY_SSL=no` → False.
- `TestKeycloakExtractParams` / `test_is_available`: full dict; `{}` → not
  available; missing secret → not available.
- `TestClassify`: via `integrations.catalog.classify_integrations` with a
  store record → resolved `"keycloak"` entry has url/realm/client_id/secret;
  record without secret → skipped.
- `TestClient`: `fetch_token` for each §1.3 token row using the fixture
  bodies (bad secret → `AUTH` and the message contains `client_id` and
  **not** the secret string; public client → message mentions "public
  client"; 404 → `NOT_FOUND` naming the realm; `httpx.ConnectError` via a
  raising handler → `TRANSPORT`; 200 without `access_token` → `BODY`).
  `admin_get` rows: 401 → `AUTH`, 403 → `FORBIDDEN` (message lists
  `view-realm`, `view-users`, `view-clients`, `view-events`), 404 → message
  contains `Realm not found.`, 500 `events_bad_type.json` → `HTTP`, non-JSON
  200 → `BODY`. Assert the `Authorization: Bearer <token>` header is sent.
- `TestShapers` (`admin_api.py`): `shape_server_info` on `serverinfo_sa.json`
  → `version is None`, `version_visible is False`, `features_total == 6`
  (trimmed fixture), `profile == "default"`; on `serverinfo_admin.json` →
  `version == "26.7.3"`, `uptime_ms == 21844`, `processor_count == 20`,
  `memory["free_pct"] == 99`. `shape_realm(realm.json)` → `failure_factor == 3`,
  `brute_force_protected is True`, `events_enabled is True`,
  `access_token_lifespan_seconds == 300`; a payload without `eventsExpiration`
  → `None`. `shape_clients` + `summarize_clients` on `clients_brief.json` →
  `total == 8`, `public == 5`, `bearer_only == 2`, one `service_accounts` (`opensre`), `demo-app` has
  `direct_access_grants is True`. `shape_session_stats` → `{"demo-app": {"active": 1, "offline": 0, ...}}`
  from string counts. `shape_user_event` on the three §4.6 payloads
  (username from details; `user_id is None` for `mallory`; `reason` for the
  client event). `summarize_user_events(events_login_error.json)` →
  `total == 7`, `by_error == {"resolve_required_actions": 1, "user_disabled": 1, "user_not_found": 1, "user_temporarily_disabled": 1, "invalid_user_credentials": 3}`,
  `lockouts == 1`, `unknown_users == 1`, `disabled_users == 1`,
  `by_username[0] == {"username": "carol", "count": 4}`.
  `shape_admin_event` + `summarize_admin_events(admin_events.json)` →
  `total == 7`, `by_operation == {"CREATE": 7}`, `by_resource_type` has
  `USER: 4, CLIENT: 2, CLIENT_ROLE_MAPPING: 1`, `has_representation is True`,
  and the output never contains the string `"username"` from the
  representation. `shape_user(user_get_dave.json)` → `required_actions == ["VERIFY_EMAIL", "UPDATE_PASSWORD"]`,
  `email_verified is False`, `created_at` starts with `2026-`.
  `shape_brute_force(carol)` → `locked is True`, `failures == 3`,
  `locked_until` is an ISO string, `last_failure_ip == "172.17.0.1"`;
  `(alice)` → `locked is False`, `locked_until is None`, `last_failure_ip is None`.
  `shape_user_session(user_sessions_alice.json[0])` → `clients == ["demo-app"]`.
- `TestValidate`: success detail contains realm, url, client id and
  `brute-force protection=on`; bad secret → `ok is False` and the secret is
  absent from `detail`; 403 on realm → detail names `view-realm`; 404 realm
  → detail contains `Realm not found.`; management unset → detail contains
  `management URL not set` and `ok is True`; management refused → `ok is True`
  and detail contains `not reachable`; connection refused on token → `ok is False`.

### 9.2 `tests/integrations/keycloak/test_metrics.py`

- `parse_prometheus_text(mgmt_metrics.txt)` → no exception; sample count
  equals the number of non-comment non-blank lines in the fixture; a sample
  `jvm_memory_used_bytes{area="heap",id="G1 Old Gen"}` has value
  `93513592.0`; `-1.0` parsed; labels with spaces/commas inside quotes
  (`cause="G1 Evacuation Pause"`, `gc="G1 Young Generation"`) round-trip;
  a garbage line is skipped, not raised.
- `shape_metrics(samples, "opensre-demo")` → `uptime_seconds == 23.614`;
  `jvm.heap_max_bytes` excludes the `-1.0` sentinels; `db_pool[0] == {"datasource": "default", "active": 0, "available": 3, "awaiting": 0, "max_used": 3}`;
  `http.by_status_class["5xx"] == 1` and `http.top_5xx[0]["uri"] == "/admin/realms/{realm}/events"`;
  `cluster_size == 1`; `realm_user_events` → `present is True`,
  `login_success == 1`, `login_errors == 7`,
  `login_errors_by_reason["invalid_user_credentials"] == 3`,
  `client_login_errors == 3`, `lockouts == 1`; `shape_metrics([], "x")` →
  every top-level value `None`/empty and `realm_user_events.present is False`;
  `shape_metrics(samples, "other-realm")` → `present is False`
  (only `master` and `opensre-demo` appear in the fixture).

### 9.3 `tests/integrations/keycloak/test_diagnostics.py`

Through `patched_client` with fixture routes:

- `get_server_status`: routes token + `/admin/serverinfo` (sa fixture) +
  management `/health/ready`, `/health/live`, `/metrics` → `available is True`,
  `version is None`, `version_note` present, `management.reachable is True`,
  `management.health.database_status == "UP"`, `management.metrics.db_pool`
  non-empty. With `management_url=""` → `management.configured is False`,
  `error == MANAGEMENT_URL_UNSET`, still `available is True`. With a
  management handler raising `httpx.ConnectError` → `reachable is False`,
  `errors` non-empty, `available is True`. Token 401 → `available is False`,
  `error_kind == "auth"`. Serverinfo 403 → `available is False`, message
  names the roles.
- `get_realm_overview`: full routes → `users_total == 4`, `clients.total == 8`,
  `sessions.active == 1`, `warnings == []`; `/users/count` 403 →
  `users_total is None`, one warning, `available is True`.
- `get_login_failures`: default → `summary.total == 7`, `events_enabled is True`;
  `event_type="bogus"` → `available is False` and **no** request was made
  (assert via a counting handler); `event_type="login_error"` (lower case)
  accepted; `max_events=9999` → request carries `max=500`; `max_events=0` →
  `max=100`; `username="carol"` → request carries `user=<carol id>` and
  `found_user is True`; `username="nobody"` → `found_user is False`,
  `events == []`; `master_events_config.json` route → `events_enabled is False`,
  `hint` mentions `Realm settings`.
- `get_admin_events`: → `summary.total == 7`; `resource_type="user"` →
  `total == 4`, `fetched == 7`; `operation_type="DELETE"` → `total == 0`;
  admin events disabled → `admin_events_enabled is False` + hint.
- `get_client_sessions`: → `totals.clients == 8`, `totals.active_sessions == 1`,
  first client is `demo-app` with `active_sessions == 1`; `client_id="demo-app"`
  → one client; `client_id="ghost"` → `clients == []`, `warning` names it.
- `get_user_status`: `alice` → `found is True`, `lockout.locked is False`,
  `sessions.count == 1`, `diagnosis == []`; `carol` (routes: search by
  username → `user_search_by_email.json` body, brute force carol, sessions
  carol, events for carol) → `diagnosis[0]` starts with
  `temporarily locked`, contains `3 failed login(s)`, `no active sessions`,
  `recent login errors: invalid_user_credentials, user_temporarily_disabled`
  (order = distinct in newest-first order); `dave` → diagnosis contains
  `required actions pending: VERIFY_EMAIL, UPDATE_PASSWORD` and
  `email not verified`; `carol@example.com` → username search returns
  `[]`, email search returns carol → `found is True`; `nobody` →
  `found is False`; `username=""` → `available is False`, no request;
  events disabled → `events_enabled is False`, `recent_events == []`,
  still `found is True`.

### 9.4 Per-tool tests — `tests/tools/test_keycloak_<name>_tool.py` (six files)

Each mirrors `tests/tools/test_aerospike_node_status_tool.py`:
`BaseToolContract` subclass (`tests/tools/conftest.py` line 252);
`test_metadata` (name, `source == "keycloak"`,
`injected_params == _KEYCLOAK_INJECTED`, LLM-visible schema properties
exactly the table in §7.5 — `filter_client_id` for the sessions tool,
`username` required for user status); `test_run_happy_path` patching the
`get_*` symbol on the tool module; one full-path test through
`patched_client` asserting the shaped output; one unavailable case (token
401 → `available is False`, error names the client id and not the secret).

### 9.5 `tests/tools/test_keycloak_tools_port_injection.py`

Parametrize over the six tool functions; for **each** name in
`_KEYCLOAK_INJECTED` assert it is in `rt.injected_params`, absent from
`rt.public_input_schema["properties"]`, and still present in
`inspect.signature(fn).parameters`. Also assert `client_secret` never
appears in `str(rt.public_input_schema)`.

### 9.6 `tests/e2e/keycloak/__init__.py` + `tests/e2e/keycloak/test_keycloak_e2e.py`

Mirror `tests/e2e/aerospike/test_aerospike_e2e.py` (classes at lines 26,
64, 86, 113, 174): store resolution via `classify_integrations`; invalid
record skipped; `keycloak_is_available` / `keycloak_extract_params`;
`verify_integrations(service="keycloak")` structure (with `patched_client`
→ `status in ("passed", "missing")`); all six modules importable; all six
names present in `get_registered_tools("chat")` filtered by
`source == "keycloak"` (clear the registry cache before/after, as the
aerospike test does); one full tool path per tool.

### 9.7 Existing files

- `tests/tools/test_telemetry.py`: add the six tool names, sorted, to
  `_TOOLS_WITHOUT_DELIBERATE_CATCH` between `"get_kafka_topic_health",`
  (line 979) and `"get_lambda_configuration",` (line 980). Read the comment
  at lines 852–866 first; if that block's rules say a tool with a
  `report_validation_failure` catch in its helper belongs in
  `_MIGRATED_TOOL_NAMES` instead, follow the block, not this sentence.
- `.github/ci/test_scope_rules.py`: add, before the `posthog_mcp` rule at
  line 256:
  ```python
  PathRule(
      "integrations/keycloak/",
      (
          "tests/integrations/test_keycloak.py",
          "tests/integrations/keycloak/test_diagnostics.py",
          "tests/integrations/keycloak/test_metrics.py",
          "tests/tools/test_keycloak_admin_events_tool.py",
          "tests/tools/test_keycloak_client_sessions_tool.py",
          "tests/tools/test_keycloak_login_failures_tool.py",
          "tests/tools/test_keycloak_realm_overview_tool.py",
          "tests/tools/test_keycloak_server_status_tool.py",
          "tests/tools/test_keycloak_tools_port_injection.py",
          "tests/tools/test_keycloak_user_status_tool.py",
          "tests/tools/test_telemetry.py",
      ),
  ),
  ```
- `.github/ci/pytest-file-durations.json`: no entry required.

## 10. Wiring — file by file (exact insertion points at `55627a7c9`)

| File | Change |
| --- | --- |
| `config/constants/__init__.py` | §2.1 import block between `kafka` (lines 182–188) and `kubernetes` (line 189); eight `__all__` entries after `"KAFKA_SECURITY_PROTOCOL_ENV",` (line 647). |
| `integrations/registry.py` | After the aerospike `IntegrationSpec` (lines 171–177) add `IntegrationSpec(service="keycloak", has_verifier=True, direct_effective=True, setup_order=47, verify_order=62)`. Both numbers verified unused at this commit (setup uses 0–45, 51–55, plus 46 reserved by the nginx branch; verify uses 0–60, 99, 100, plus 61 reserved by nginx). The tuple closes at line 478. |
| `integrations/_catalog_impl.py` | Imports between `from integrations.jira import classify as _classify_jira` (line 325) and `from integrations.kubernetes import ...` (line 326): `from integrations.keycloak import classify as _classify_keycloak` and `from integrations.keycloak import keycloak_config_from_env`. Classifier map: `"keycloak": _classify_keycloak,` between `"jira"` (line 530) and `"kubernetes"` (line 551). Env loader after the aerospike block ending line 1144: `keycloak_config = keycloak_config_from_env()` / `if keycloak_config: integrations.append(_active_env_record("keycloak", keycloak_config.model_dump(exclude={"integration_id"})))`. |
| `integrations/cli.py` | After `_setup_aerospike` (lines 561–564): `def _setup_keycloak() -> None:` importing `KEYCLOAK_SETUP` from `integrations.keycloak.setup` and calling `_run_spec_setup(KEYCLOAK_SETUP)`. Dispatch map: `"keycloak": _setup_keycloak,` immediately before `"kubernetes": _setup_kubernetes,` (line 837). |
| `integrations/effective_models.py` | `keycloak: EffectiveIntegrationEntry | None = None` after `gitlab` (line 50). |
| `integrations/alert_source_catalog.py` | Routing (after `"aerospike"` line 52): `"keycloak": routing(("keycloak",), ("keycloak",)),`. Keywords (after `"kafka": ("kafka",),` line 122): `"keycloak": ("keycloak", "sso", "oidc", "openid", "login failed", "locked out", "realm"),`. |
| `tools/registry_discovery.py` | `"integrations.keycloak.tools",` between `integrations.kafka.tools` (line 58) and `integrations.kubernetes.tools` (line 59). |
| `.env.example` | New block after the Kafka block (ends line 578, blank line 579) and before `# ClickHouse`: `# Keycloak (Admin REST API via a service-account client; management URL is the :9000 health/metrics interface)` then `KEYCLOAK_URL=`, `KEYCLOAK_REALM=`, `KEYCLOAK_CLIENT_ID=`, `KEYCLOAK_CLIENT_SECRET=`, `KEYCLOAK_AUTH_REALM=`, `KEYCLOAK_MANAGEMENT_URL=`, `KEYCLOAK_VERIFY_SSL=true`, `KEYCLOAK_TIMEOUT_SECONDS=10`. |
| `docs/docs.json` | New group after the `Workflows` group (its object closes with `}` at line 269 — add a comma after that brace): `{"group": "Identity", "expanded": false, "pages": ["keycloak"]}`. |
| `docs/keycloak.mdx` | New page, §11. |

## 11. `docs/keycloak.mdx` outline (mirror `docs/aerospike.mdx` headings: Prerequisites, Setup with Options 1–3, Tools, Verify, Known limitations, Troubleshooting, Security best practices)

Frontmatter title "Keycloak", description "Connect Keycloak so OpenSRE can
check server health, realm settings, login failures, lockouts and admin
changes during investigations". Sections:

- Intro: one paragraph — read-only Admin REST API + management health/
  metrics, Keycloak 25 or newer.
- **Prerequisites**: exact console steps to create the client — Clients >
  Create client > Client ID `opensre`, Client authentication **On**,
  Service accounts roles **On**; then Clients > opensre > Service accounts
  roles > Assign role > filter by clients > `realm-management` > tick
  `view-realm`, `view-users`, `view-clients`, `view-events`; copy the secret
  from Credentials. State that a client in the `master` realm with the same
  roles for the target realm works too (`KEYCLOAK_AUTH_REALM=master`).
  Management interface: `KC_HEALTH_ENABLED=true`, `KC_METRICS_ENABLED=true`
  (and `KC_EVENT_METRICS_USER_ENABLED=true` for per-realm login counters),
  reachable on port 9000 by default; the main port answers 404 on `/health`.
  Events: Realm settings > Events > enable user events and admin events or
  the two event tools report them disabled.
- **Setup** Options 1–3 exactly like aerospike with the eight env vars in a
  table (required: URL, realm, client id, secret).
- **Tools** six subsections, one sentence each; an `<Info>` block: version
  is visible only to master-realm admins; the sessions counts come from
  `client-session-stats` and exclude clients with zero sessions until merged.
- **Verify** with the expected `passed` detail from §7.2 (one line).
- **Known limitations**: read-only (no unlock/logout); one realm per
  configured integration; admin-event filters apply to the fetched page
  only; no LDAP/federation diagnostics; Keycloak 24 and older unsupported.
- **Troubleshooting** table: `Invalid client or Invalid client credentials`
  → wrong id/secret; `Public client not allowed to retrieve service
  account` → enable client authentication + service accounts; `HTTP 403
  Forbidden` → assign the four roles to the service-account user;
  `Realm does not exist` → `KEYCLOAK_AUTH_REALM` wrong; `Realm not found.`
  → `KEYCLOAK_REALM` wrong; health/metrics "not reachable" → management URL
  / `KC_HEALTH_ENABLED`; events empty → events disabled or expired
  (`eventsExpiration`).
- **Security best practices**: the four view roles only, never
  `realm-admin`; rotate the secret; secret lives in the keyring tier;
  restrict the management port to the OpenSRE host.

Per `AGENTS.md` docs rule: every sentence must change what the reader does.

## 12. Live verification (do this; report results in the PR)

Docker only, no host installs, image pinned:

```bash
docker run -d --rm --name opensre-keycloak-live \
  -p 127.0.0.1:18080:8080 -p 127.0.0.1:19000:9000 \
  -e KC_BOOTSTRAP_ADMIN_USERNAME=admin -e KC_BOOTSTRAP_ADMIN_PASSWORD=admin \
  -e KC_HEALTH_ENABLED=true -e KC_METRICS_ENABLED=true \
  -e KC_EVENT_METRICS_USER_ENABLED=true \
  quay.io/keycloak/keycloak:26.7.3 start-dev
until curl -fs http://127.0.0.1:19000/health/ready >/dev/null; do sleep 2; done
```

Provision with the Admin API (bootstrap admin token from
`POST /realms/master/protocol/openid-connect/token` with
`grant_type=password&client_id=admin-cli&username=admin&password=admin`),
all against `http://127.0.0.1:18080/admin/realms`:

1. `POST /admin/realms` `{"realm":"opensre-demo","enabled":true,"eventsEnabled":true,"adminEventsEnabled":true,"adminEventsDetailsEnabled":true,"bruteForceProtected":true,"failureFactor":3,"sslRequired":"external"}` → 201.
2. `POST .../opensre-demo/clients` `{"clientId":"opensre","publicClient":false,"serviceAccountsEnabled":true,"secret":"opensre-service-secret","standardFlowEnabled":false}` → 201.
3. `GET .../clients/{opensre uuid}/service-account-user` → SA user id;
   `GET .../clients?clientId=realm-management` → rm uuid;
   `GET .../clients/{rm uuid}/roles` → pick `view-realm`, `view-users`,
   `view-clients`, `view-events`;
   `POST .../users/{sa id}/role-mappings/clients/{rm uuid}` with those four
   role objects → 204.
4. `POST .../clients` `{"clientId":"demo-app","publicClient":true,"directAccessGrantsEnabled":true}` → 201.
5. Users (`POST .../users`, each with `credentials:[{"type":"password","value":"<pw>","temporary":false}]`):
   `alice` enabled; `bob` `enabled:false`; `carol` enabled; `dave` with
   `"requiredActions":["VERIFY_EMAIL","UPDATE_PASSWORD"]`.
6. Generate events with `POST /realms/opensre-demo/protocol/openid-connect/token`
   (`grant_type=password&client_id=demo-app`): alice correct; carol wrong
   password ×4 (4th returns `user_temporarily_disabled`); `mallory`;
   `bob`; `dave`.

Then:

```bash
KEYCLOAK_URL=http://127.0.0.1:18080 KEYCLOAK_REALM=opensre-demo KEYCLOAK_CLIENT_ID=opensre \
KEYCLOAK_CLIENT_SECRET=opensre-service-secret KEYCLOAK_MANAGEMENT_URL=http://127.0.0.1:19000 \
uv run opensre integrations verify keycloak
docker stop opensre-keycloak-live
```

Expect verify `passed` with `brute-force protection=on, user events=on,
admin events=on; management health UP at http://127.0.0.1:19000`;
`get_keycloak_user_status` for `carol` → locked with 3 failures;
`get_keycloak_login_failures` → 7 events, 1 lockout; `get_keycloak_server_status`
→ `version: null`, `version_visible: false`, `management.reachable: true`,
`realm_user_events.login_errors == 7`.

## 13. Definition of done

- [ ] All §9 tests written first, seen failing, then passing.
- [ ] `make lint`, `make format-check`, `make typecheck`, `make test-scope` green in the worktree.
- [ ] §12 live check done; outputs pasted in the PR (secret redacted).
- [ ] `docs/keycloak.mdx` + `docs.json` Identity group present; `.env.example` block present.
- [ ] `uv run opensre integrations setup keycloak` and `verify keycloak` work end to end.
- [ ] `KEYCLOAK_CLIENT_SECRET` never appears in any evidence dict, log line, verify detail or test output.
- [ ] PR body follows `.github/PULL_REQUEST_TEMPLATE.md`, references `Closes #4`, no AI attribution anywhere.
- [ ] `gh pr checks --watch` green; after merge, `main` CI monitored per `AGENTS.md`.

## 14. Risks / open questions

1. **Version hidden for realm-scoped clients.** Accepted (decision 9). Users
   who want the version configure a master-realm client and
   `KEYCLOAK_AUTH_REALM=master`. Nothing else in the tools depends on it.
2. **Event retention.** `eventsExpiration` (604800 s in the fixture, absent
   when never set) silently bounds what the login-failures tool can see;
   the realm overview reports it so the caller can tell.
3. **Large realms.** `/users/count` is one integer; `/clients` is fetched
   without paging (a realm with thousands of clients would return a large
   page). Acceptable for v1; add `first/max` paging to `get_client_sessions`
   in a follow-up if it bites.
4. **Client-side admin-event filters** (decision 8) only see the fetched
   page; `fetched` vs `summary.total` makes that visible.
5. **`keycloak_user_events_total`** exists only with
   `KC_EVENT_METRICS_USER_ENABLED=true`; `realm_user_events.present` is
   `False` otherwise and the docs say which flag to set.
6. **Clustered deployments.** `management_url` points at one node; health
   and metrics describe that node only. `cluster_size` from
   `vendor_cluster_size` tells the caller how many nodes exist.
