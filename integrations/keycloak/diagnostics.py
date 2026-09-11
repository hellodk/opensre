"""Read-only Keycloak diagnostics: one token per call, clients always closed."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx

import integrations.keycloak.client as keycloak_client
from core.tool_framework.utils import tool_unavailable
from integrations._validation_helpers import report_validation_failure
from integrations.keycloak.admin_api import (
    shape_admin_event,
    shape_brute_force,
    shape_clients,
    shape_events_config,
    shape_realm,
    shape_server_info,
    shape_session_stats,
    shape_user,
    shape_user_event,
    shape_user_session,
    summarize_admin_events,
    summarize_clients,
    summarize_user_events,
)
from integrations.keycloak.client import FetchError
from integrations.keycloak.config import KeycloakConfig
from integrations.keycloak.metrics import parse_prometheus_text, shape_metrics

logger = logging.getLogger(__name__)

DEFAULT_MAX_EVENTS = 100
MAX_EVENTS = 500
RECENT_USER_EVENTS = 20
ALLOWED_USER_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "LOGIN_ERROR",
        "CLIENT_LOGIN_ERROR",
        "IDENTITY_PROVIDER_LOGIN_ERROR",
        "CODE_TO_TOKEN_ERROR",
        "RESET_PASSWORD_ERROR",
        "LOGOUT_ERROR",
        "USER_DISABLED_BY_TEMPORARY_LOCKOUT",
        "USER_DISABLED_BY_PERMANENT_LOCKOUT",
    }
)
EVENTS_DISABLED_HINT = (
    "User events are disabled for realm '{realm}'. Enable them in the admin console under "
    "Realm settings > Events > User events settings (Save events), then retry."
)
ADMIN_EVENTS_DISABLED_HINT = (
    "Admin events are disabled for realm '{realm}'. Enable them under "
    "Realm settings > Events > Admin events settings (Save events), then retry."
)
MANAGEMENT_URL_UNSET = "KEYCLOAK_MANAGEMENT_URL is not set; health and metrics were not read."

_NOT_CONFIGURED = (
    "Keycloak is not configured (url, realm, client_id and client_secret are required)."
)


def _error(message: str, **extra: Any) -> dict[str, Any]:
    return tool_unavailable("keycloak", message, **extra)


def clamp_max_events(value: int | None) -> int:
    """Clamp a requested event page size into [1, MAX_EVENTS]."""
    if value is None or value <= 0:
        return DEFAULT_MAX_EVENTS
    return min(value, MAX_EVENTS)


@contextmanager
def _open_session(
    config: KeycloakConfig,
) -> Iterator[tuple[httpx.Client, str] | FetchError]:
    """Open a client and fetch one service-account token for this call.

    Yields ``(client, token)`` on success or the ``FetchError`` when the
    token fetch fails; the client is always closed on exit.
    """
    with keycloak_client.build_client(config) as client:
        token, err = keycloak_client.fetch_token(client, config)
        if err is not None:
            yield err
        elif token is None:
            yield FetchError(
                keycloak_client.FetchErrorKind.BODY,
                "Keycloak token response has no access_token.",
            )
        else:
            yield client, token


def _shape_health(payload: Any) -> dict[str, Any]:
    checks = payload.get("checks") or [] if isinstance(payload, dict) else []
    shaped_checks = [
        {"name": str(check.get("name", "")), "status": str(check.get("status", ""))}
        for check in checks
        if isinstance(check, dict)
    ]
    database_status = next(
        (check["status"] for check in shaped_checks if "database" in check["name"].lower()),
        None,
    )
    return {
        "status": payload.get("status", "") if isinstance(payload, dict) else "",
        "checks": shaped_checks,
        "database_status": database_status,
    }


def get_server_status(config: KeycloakConfig) -> dict[str, Any]:
    """Return server info plus management health/metrics when configured."""
    if not config.is_configured:
        return _error(_NOT_CONFIGURED)
    try:
        with _open_session(config) as session:
            if isinstance(session, FetchError):
                return _error(session.message, error_kind=session.kind)
            client, token = session
            payload, err = keycloak_client.admin_get(client, token, config, "/admin/serverinfo")
            if err is not None:
                return _error(err.message, error_kind=err.kind)
            if not isinstance(payload, dict):
                return _error("Unexpected /admin/serverinfo response.")
            info = shape_server_info(payload)
        if not config.has_management_url:
            management: dict[str, Any] = {
                "configured": False,
                "reachable": False,
                "url": "",
                "error": MANAGEMENT_URL_UNSET,
                "health": None,
                "liveness": None,
                "metrics": None,
            }
        else:
            management = {
                "configured": True,
                "reachable": False,
                "url": config.management_url,
                "errors": [],
                "health": None,
                "liveness": None,
                "metrics": None,
            }
            mgmt = keycloak_client.build_management_client(config)
            with mgmt:
                ready, ready_err = keycloak_client.mgmt_get_json(mgmt, "/health/ready")
                if ready_err is not None:
                    management["errors"].append(ready_err.message)
                else:
                    management["health"] = _shape_health(ready)
                live, live_err = keycloak_client.mgmt_get_json(mgmt, "/health/live")
                if live_err is not None:
                    management["errors"].append(live_err.message)
                else:
                    management["liveness"] = _shape_health(live)
                text, text_err = keycloak_client.mgmt_get_text(mgmt, "/metrics")
                if text_err is not None:
                    management["errors"].append(text_err.message)
                elif text is not None:
                    management["metrics"] = shape_metrics(parse_prometheus_text(text), config.realm)
            management["reachable"] = (
                management["health"] is not None
                or management["liveness"] is not None
                or management["metrics"] is not None
            )
        result: dict[str, Any] = {
            "source": "keycloak",
            "available": True,
            "url": config.url,
            "realm": config.realm,
            **info,
            "management": management,
        }
        if not info["version_visible"]:
            result["version_note"] = (
                "Keycloak version is visible only to master-realm administrators; "
                "the configured service account is realm-scoped."
            )
        return result
    except Exception as err:
        report_validation_failure(
            err, logger=logger, integration="keycloak", method="get_server_status"
        )
        return _error(str(err))


def get_realm_overview(config: KeycloakConfig) -> dict[str, Any]:
    """Return realm settings plus user, client and session counts."""
    if not config.is_configured:
        return _error(_NOT_CONFIGURED)
    try:
        with _open_session(config) as session:
            if isinstance(session, FetchError):
                return _error(session.message, error_kind=session.kind)
            client, token = session
            payload, err = keycloak_client.admin_get(client, token, config, config.admin_realm_path)
            if err is not None:
                return _error(err.message, error_kind=err.kind)
            if not isinstance(payload, dict):
                return _error(f"Unexpected {config.admin_realm_path} response.")
            realm = shape_realm(payload)
            warnings: list[str] = []
            users_total: int | None
            count_payload, count_err = keycloak_client.admin_get(
                client, token, config, config.admin_realm_path + "/users/count"
            )
            if count_err is not None:
                warnings.append(count_err.message)
                users_total = None
            else:
                users_total = count_payload if isinstance(count_payload, int) else None
            clients_summary: dict[str, int] | None = None
            clients_payload, clients_err = keycloak_client.admin_get(
                client,
                token,
                config,
                config.admin_realm_path + "/clients",
                params={"briefRepresentation": "true"},
            )
            if clients_err is not None:
                warnings.append(clients_err.message)
            elif isinstance(clients_payload, list):
                clients_summary = summarize_clients(shape_clients(clients_payload))
            else:
                warnings.append(f"Unexpected {config.admin_realm_path}/clients response.")
            sessions = {"active": 0, "offline": 0, "clients_with_sessions": 0}
            stats_payload, stats_err = keycloak_client.admin_get(
                client, token, config, config.admin_realm_path + "/client-session-stats"
            )
            if stats_err is not None:
                warnings.append(stats_err.message)
            elif isinstance(stats_payload, list):
                stats = shape_session_stats(stats_payload)
                sessions = {
                    "active": sum(s["active"] for s in stats.values()),
                    "offline": sum(s["offline"] for s in stats.values()),
                    "clients_with_sessions": len(stats),
                }
            else:
                warnings.append(
                    f"Unexpected {config.admin_realm_path}/client-session-stats response."
                )
            return {
                "source": "keycloak",
                "available": True,
                "url": config.url,
                **realm,
                "users_total": users_total,
                "clients": clients_summary,
                "sessions": sessions,
                "warnings": warnings,
            }
    except Exception as err:
        report_validation_failure(
            err, logger=logger, integration="keycloak", method="get_realm_overview"
        )
        return _error(str(err))


def get_login_failures(
    config: KeycloakConfig,
    event_type: str = "LOGIN_ERROR",
    max_events: int | None = DEFAULT_MAX_EVENTS,
    username: str = "",
) -> dict[str, Any]:
    """Return recent login failure events with per-error/user/ip/client counts."""
    normalized_type = event_type.strip().upper()
    if normalized_type not in ALLOWED_USER_EVENT_TYPES:
        return _error(
            f"Unsupported event_type '{normalized_type}'. "
            f"Allowed: {sorted(ALLOWED_USER_EVENT_TYPES)}"
        )
    if not config.is_configured:
        return _error(_NOT_CONFIGURED)
    try:
        with _open_session(config) as session:
            if isinstance(session, FetchError):
                return _error(session.message, error_kind=session.kind)
            client, token = session
            config_payload, config_err = keycloak_client.admin_get(
                client, token, config, config.admin_realm_path + "/events/config"
            )
            if config_err is not None:
                return _error(config_err.message, error_kind=config_err.kind)
            if not isinstance(config_payload, dict):
                return _error(f"Unexpected {config.admin_realm_path}/events/config response.")
            events_config = shape_events_config(config_payload)
            if not events_config["events_enabled"]:
                return {
                    "source": "keycloak",
                    "available": True,
                    "realm": config.realm,
                    "events_enabled": False,
                    "event_type": normalized_type,
                    "events": [],
                    "summary": summarize_user_events([]),
                    "hint": EVENTS_DISABLED_HINT.format(realm=config.realm),
                }
            limit = clamp_max_events(max_events)
            params: dict[str, Any] = {"type": normalized_type, "max": limit}
            wanted = username.strip()
            found_user = True
            if wanted:
                users_payload, users_err = keycloak_client.admin_get(
                    client,
                    token,
                    config,
                    config.admin_realm_path + "/users",
                    params={"username": wanted, "exact": "true"},
                )
                if users_err is not None:
                    return _error(users_err.message, error_kind=users_err.kind)
                if not isinstance(users_payload, list):
                    return _error(f"Unexpected {config.admin_realm_path}/users response.")
                if not users_payload:
                    return {
                        "source": "keycloak",
                        "available": True,
                        "realm": config.realm,
                        "events_enabled": True,
                        "found_user": False,
                        "username": wanted,
                        "event_type": normalized_type,
                        "max_events": limit,
                        "events": [],
                        "summary": summarize_user_events([]),
                    }
                params["user"] = users_payload[0].get("id", "")
            events_payload, events_err = keycloak_client.admin_get(
                client, token, config, config.admin_realm_path + "/events", params=params
            )
            if events_err is not None:
                return _error(events_err.message, error_kind=events_err.kind)
            events = [shape_user_event(e) for e in (events_payload or []) if isinstance(e, dict)]
            result: dict[str, Any] = {
                "source": "keycloak",
                "available": True,
                "realm": config.realm,
                "events_enabled": True,
                "event_type": normalized_type,
                "max_events": limit,
                "username": wanted,
                "events": events,
                "summary": summarize_user_events(events),
            }
            if wanted:
                result["found_user"] = found_user
            return result
    except Exception as err:
        report_validation_failure(
            err, logger=logger, integration="keycloak", method="get_login_failures"
        )
        return _error(str(err))


def get_admin_events(
    config: KeycloakConfig,
    max_events: int | None = DEFAULT_MAX_EVENTS,
    resource_type: str = "",
    operation_type: str = "",
) -> dict[str, Any]:
    """Return recent admin events, filtered client-side by type/operation."""
    if not config.is_configured:
        return _error(_NOT_CONFIGURED)
    try:
        with _open_session(config) as session:
            if isinstance(session, FetchError):
                return _error(session.message, error_kind=session.kind)
            client, token = session
            config_payload, config_err = keycloak_client.admin_get(
                client, token, config, config.admin_realm_path + "/events/config"
            )
            if config_err is not None:
                return _error(config_err.message, error_kind=config_err.kind)
            if not isinstance(config_payload, dict):
                return _error(f"Unexpected {config.admin_realm_path}/events/config response.")
            events_config = shape_events_config(config_payload)
            if not events_config["admin_events_enabled"]:
                return {
                    "source": "keycloak",
                    "available": True,
                    "realm": config.realm,
                    "admin_events_enabled": False,
                    "events": [],
                    "summary": summarize_admin_events([]),
                    "hint": ADMIN_EVENTS_DISABLED_HINT.format(realm=config.realm),
                }
            limit = clamp_max_events(max_events)
            payload, err = keycloak_client.admin_get(
                client,
                token,
                config,
                config.admin_realm_path + "/admin-events",
                params={"max": limit},
            )
            if err is not None:
                return _error(err.message, error_kind=err.kind)
            page = [shape_admin_event(e) for e in (payload or []) if isinstance(e, dict)]
            wanted_resource = resource_type.strip().upper()
            wanted_operation = operation_type.strip().upper()
            events = [
                e
                for e in page
                if (not wanted_resource or e["resource_type"] == wanted_resource)
                and (not wanted_operation or e["operation_type"] == wanted_operation)
            ]
            return {
                "source": "keycloak",
                "available": True,
                "realm": config.realm,
                "admin_events_enabled": True,
                "details_enabled": events_config["admin_events_details_enabled"],
                "max_events": limit,
                "filters": {
                    "resource_type": wanted_resource,
                    "operation_type": wanted_operation,
                },
                "events": events,
                "summary": summarize_admin_events(events),
                "fetched": len(page),
            }
    except Exception as err:
        report_validation_failure(
            err, logger=logger, integration="keycloak", method="get_admin_events"
        )
        return _error(str(err))


def get_client_sessions(config: KeycloakConfig, client_id: str = "") -> dict[str, Any]:
    """Return every client merged with its active/offline session counts."""
    if not config.is_configured:
        return _error(_NOT_CONFIGURED)
    try:
        with _open_session(config) as session:
            if isinstance(session, FetchError):
                return _error(session.message, error_kind=session.kind)
            client, token = session
            clients_payload, clients_err = keycloak_client.admin_get(
                client,
                token,
                config,
                config.admin_realm_path + "/clients",
                params={"briefRepresentation": "true"},
            )
            if clients_err is not None:
                return _error(clients_err.message, error_kind=clients_err.kind)
            stats_payload, stats_err = keycloak_client.admin_get(
                client, token, config, config.admin_realm_path + "/client-session-stats"
            )
            if stats_err is not None:
                return _error(stats_err.message, error_kind=stats_err.kind)
            shaped = shape_clients(clients_payload if isinstance(clients_payload, list) else [])
            stats = shape_session_stats(stats_payload if isinstance(stats_payload, list) else [])
            merged = []
            for entry in shaped:
                entry_stats = stats.get(entry["client_id"], {"active": 0, "offline": 0})
                merged.append(
                    {
                        **entry,
                        "active_sessions": entry_stats["active"],
                        "offline_sessions": entry_stats["offline"],
                    }
                )
            merged.sort(key=lambda e: (-e["active_sessions"], e["client_id"]))
            totals = {
                "clients": len(merged),
                "active_sessions": sum(e["active_sessions"] for e in merged),
                "offline_sessions": sum(e["offline_sessions"] for e in merged),
                "clients_with_sessions": sum(
                    1 for e in merged if e["active_sessions"] or e["offline_sessions"]
                ),
            }
            wanted = client_id.strip()
            if wanted:
                matched = [e for e in merged if e["client_id"] == wanted]
                if not matched:
                    return {
                        "source": "keycloak",
                        "available": True,
                        "realm": config.realm,
                        "filter": wanted,
                        "clients": [],
                        "totals": totals,
                        "warning": (f"client '{wanted}' not found in realm '{config.realm}'"),
                    }
                merged = matched
            return {
                "source": "keycloak",
                "available": True,
                "realm": config.realm,
                "filter": wanted,
                "clients": merged,
                "totals": totals,
            }
    except Exception as err:
        report_validation_failure(
            err, logger=logger, integration="keycloak", method="get_client_sessions"
        )
        return _error(str(err))


def _distinct_errors_chronological(events: list[dict[str, Any]]) -> list[str]:
    """Distinct error names in the order they first occurred (oldest first)."""
    seen: list[str] = []
    for event in events:
        error = event.get("error")
        if error and error not in seen:
            seen.append(error)
    return seen


def get_user_status(config: KeycloakConfig, username: str) -> dict[str, Any]:
    """Look up one user and explain why they cannot log in, if anything."""
    wanted = username.strip()
    if not wanted:
        return _error("username is required.")
    if not config.is_configured:
        return _error(_NOT_CONFIGURED)
    try:
        with _open_session(config) as session:
            if isinstance(session, FetchError):
                return _error(session.message, error_kind=session.kind)
            client, token = session
            users_payload, users_err = keycloak_client.admin_get(
                client,
                token,
                config,
                config.admin_realm_path + "/users",
                params={"username": wanted, "exact": "true"},
            )
            if users_err is not None:
                return _error(users_err.message, error_kind=users_err.kind)
            if not isinstance(users_payload, list):
                return _error(f"Unexpected {config.admin_realm_path}/users response.")
            if not users_payload and "@" in wanted:
                users_payload, users_err = keycloak_client.admin_get(
                    client,
                    token,
                    config,
                    config.admin_realm_path + "/users",
                    params={"email": wanted, "exact": "true"},
                )
                if users_err is not None:
                    return _error(users_err.message, error_kind=users_err.kind)
                if not isinstance(users_payload, list):
                    return _error(f"Unexpected {config.admin_realm_path}/users response.")
            if not users_payload:
                return {
                    "source": "keycloak",
                    "available": True,
                    "found": False,
                    "username": wanted,
                    "realm": config.realm,
                }
            user = shape_user(users_payload[0])
            warnings: list[str] = []
            lockout: dict[str, Any] | None = None
            brute_payload, brute_err = keycloak_client.admin_get(
                client,
                token,
                config,
                config.admin_realm_path + f"/attack-detection/brute-force/users/{user['id']}",
            )
            if brute_err is not None:
                warnings.append(brute_err.message)
            elif not isinstance(brute_payload, dict):
                warnings.append(f"Unexpected {config.admin_realm_path}/attack-detection response.")
            else:
                lockout = shape_brute_force(brute_payload)
            sessions_payload, sessions_err = keycloak_client.admin_get(
                client,
                token,
                config,
                config.admin_realm_path + f"/users/{user['id']}/sessions",
            )
            sessions: dict[str, Any]
            if sessions_err is not None:
                warnings.append(sessions_err.message)
                sessions = {"count": 0, "sessions": []}
            else:
                shaped_sessions = [
                    shape_user_session(s) for s in (sessions_payload or []) if isinstance(s, dict)
                ]
                sessions = {"count": len(shaped_sessions), "sessions": shaped_sessions}
            config_payload, config_err = keycloak_client.admin_get(
                client, token, config, config.admin_realm_path + "/events/config"
            )
            events_enabled = False
            recent_events: list[dict[str, Any]] = []
            if config_err is not None:
                warnings.append(config_err.message)
            elif not isinstance(config_payload, dict):
                warnings.append(f"Unexpected {config.admin_realm_path}/events/config response.")
            else:
                events_enabled = bool(shape_events_config(config_payload)["events_enabled"])
                if events_enabled:
                    recent_payload, recent_err = keycloak_client.admin_get(
                        client,
                        token,
                        config,
                        config.admin_realm_path + "/events",
                        params={"user": user["id"], "max": RECENT_USER_EVENTS},
                    )
                    if recent_err is not None:
                        warnings.append(recent_err.message)
                    else:
                        recent_events = [
                            shape_user_event(e)
                            for e in (recent_payload or [])
                            if isinstance(e, dict)
                        ]
            diagnosis: list[str] = []
            if not user["enabled"]:
                diagnosis.append("account disabled")
            if lockout and lockout["locked"]:
                diagnosis.append(
                    f"temporarily locked by brute-force protection until {lockout['locked_until']}"
                )
            if lockout and lockout["failures"] > 0:
                diagnosis.append(
                    f"{lockout['failures']} failed login(s), last from "
                    f"{lockout['last_failure_ip']} at {lockout['last_failure']}"
                )
            if user["required_actions"]:
                diagnosis.append(f"required actions pending: {', '.join(user['required_actions'])}")
            if not user["email_verified"]:
                diagnosis.append("email not verified")
            if sessions["count"] == 0:
                diagnosis.append("no active sessions")
            recent_errors = _distinct_errors_chronological(list(reversed(recent_events)))
            if recent_errors:
                diagnosis.append(f"recent login errors: {', '.join(recent_errors)}")
            return {
                "source": "keycloak",
                "available": True,
                "found": True,
                "realm": config.realm,
                "user": user,
                "lockout": lockout,
                "sessions": sessions,
                "events_enabled": events_enabled,
                "recent_events": recent_events,
                "diagnosis": diagnosis,
                "warnings": warnings,
            }
    except Exception as err:
        report_validation_failure(
            err, logger=logger, integration="keycloak", method="get_user_status"
        )
        return _error(str(err))
