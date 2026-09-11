"""Pure shapers over Keycloak Admin REST API JSON payloads."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from infrastructure.text.coercion import safe_int

TOP_N = 10


def iso_from_ms(value: Any) -> str | None:
    """Format epoch milliseconds as ISO-8601, or None when missing."""
    try:
        millis = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not millis:
        return None
    return datetime.fromtimestamp(millis / 1000, tz=UTC).isoformat()


def iso_from_s(value: Any) -> str | None:
    """Format epoch seconds as ISO-8601, or None when missing."""
    try:
        seconds = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not seconds:
        return None
    return datetime.fromtimestamp(seconds, tz=UTC).isoformat()


def shape_server_info(payload: dict[str, Any]) -> dict[str, Any]:
    """Shape GET /admin/serverinfo into version, runtime and feature counts."""
    system = payload.get("systemInfo") or {}
    memory = payload.get("memoryInfo") or {}
    cpu = payload.get("cpuInfo") or {}
    profile = payload.get("profileInfo") or {}
    features = payload.get("features") or []
    version = system.get("version") if isinstance(system, dict) else None
    os_name = system.get("osName", "") if isinstance(system, dict) else ""
    os_arch = system.get("osArchitecture", "") if isinstance(system, dict) else ""
    by_type: dict[str, int] = {}
    enabled = 0
    for feature in features:
        if not isinstance(feature, dict):
            continue
        by_type[str(feature.get("type", ""))] = by_type.get(str(feature.get("type", "")), 0) + 1
        if feature.get("enabled"):
            enabled += 1
    memory_shape = None
    if memory:
        total = safe_int(memory.get("total"), 0)
        used = safe_int(memory.get("used"), 0)
        memory_shape = {
            "total_bytes": total,
            "used_bytes": used,
            "free_pct": safe_int(memory.get("freePercentage"), 0),
        }
    processor_count = safe_int(cpu.get("processorCount"), 0) or None if cpu else None
    return {
        "version": str(version) if version else None,
        "version_visible": bool(system),
        "server_time": system.get("serverTime") if isinstance(system, dict) else None,
        "uptime": system.get("uptime") if isinstance(system, dict) else None,
        "uptime_ms": safe_int(system.get("uptimeMillis"), 0)
        if isinstance(system, dict) and system.get("uptimeMillis") is not None
        else None,
        "java_version": system.get("javaVersion") if isinstance(system, dict) else None,
        "os": f"{os_name} {os_arch}".strip() or None,
        "memory": memory_shape,
        "processor_count": processor_count,
        "profile": str(profile.get("name", "")),
        "features_enabled": enabled,
        "features_total": len(features),
        "features_enabled_by_type": by_type,
        "disabled_features_count": len(profile.get("disabledFeatures") or []),
    }


def shape_realm(payload: dict[str, Any]) -> dict[str, Any]:
    """Shape GET /admin/realms/{realm} into operational settings."""
    return {
        "realm_id": payload.get("id", ""),
        "realm": payload.get("realm", ""),
        "display_name": payload.get("displayName", ""),
        "enabled": bool(payload.get("enabled", False)),
        "ssl_required": payload.get("sslRequired", ""),
        "brute_force_protected": bool(payload.get("bruteForceProtected", False)),
        "permanent_lockout": bool(payload.get("permanentLockout", False)),
        "max_temporary_lockouts": safe_int(payload.get("maxTemporaryLockouts"), 0),
        "brute_force_strategy": payload.get("bruteForceStrategy", ""),
        "max_failure_wait_seconds": safe_int(payload.get("maxFailureWaitSeconds"), 0),
        "failure_factor": safe_int(payload.get("failureFactor"), 0),
        "wait_increment_seconds": safe_int(payload.get("waitIncrementSeconds"), 0),
        "quick_login_check_ms": safe_int(payload.get("quickLoginCheckMilliSeconds"), 0),
        "minimum_quick_login_wait_seconds": safe_int(
            payload.get("minimumQuickLoginWaitSeconds"), 0
        ),
        "max_delta_time_seconds": safe_int(payload.get("maxDeltaTimeSeconds"), 0),
        "events_enabled": bool(payload.get("eventsEnabled", False)),
        "events_expiration_seconds": safe_int(payload["eventsExpiration"], 0)
        if payload.get("eventsExpiration") is not None
        else None,
        "events_listeners": list(payload.get("eventsListeners") or []),
        "admin_events_enabled": bool(payload.get("adminEventsEnabled", False)),
        "admin_events_details_enabled": bool(payload.get("adminEventsDetailsEnabled", False)),
        "access_token_lifespan_seconds": safe_int(payload.get("accessTokenLifespan"), 0),
        "sso_session_idle_seconds": safe_int(payload.get("ssoSessionIdleTimeout"), 0),
        "sso_session_max_seconds": safe_int(payload.get("ssoSessionMaxLifespan"), 0),
        "offline_session_idle_seconds": safe_int(payload.get("offlineSessionIdleTimeout"), 0),
        "registration_allowed": bool(payload.get("registrationAllowed", False)),
        "login_with_email_allowed": bool(payload.get("loginWithEmailAllowed", False)),
        "verify_email": bool(payload.get("verifyEmail", False)),
        "reset_password_allowed": bool(payload.get("resetPasswordAllowed", False)),
        "remember_me": bool(payload.get("rememberMe", False)),
        "not_before": safe_int(payload.get("notBefore"), 0),
        "default_signature_algorithm": payload.get("defaultSignatureAlgorithm", ""),
        "otp_policy_type": payload.get("otpPolicyType", ""),
        "user_managed_access_allowed": bool(payload.get("userManagedAccessAllowed", False)),
    }


def shape_clients(payload: list[Any]) -> list[dict[str, Any]]:
    """Shape GET /clients?briefRepresentation=true into sorted client dicts."""
    clients = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        public = bool(entry.get("publicClient", False))
        bearer = bool(entry.get("bearerOnly", False))
        clients.append(
            {
                "id": entry.get("id", ""),
                "client_id": entry.get("clientId", ""),
                "name": entry.get("name", ""),
                "enabled": bool(entry.get("enabled", False)),
                "public_client": public,
                "service_accounts_enabled": bool(entry.get("serviceAccountsEnabled", False)),
                "bearer_only": bearer,
                "protocol": entry.get("protocol", ""),
                "standard_flow": bool(entry.get("standardFlowEnabled", False)),
                "implicit_flow": bool(entry.get("implicitFlowEnabled", False)),
                "direct_access_grants": bool(entry.get("directAccessGrantsEnabled", False)),
                "authenticator_type": entry.get("clientAuthenticatorType", ""),
            }
        )
    clients.sort(key=lambda c: c["client_id"])
    return clients


def summarize_clients(clients: list[dict[str, Any]]) -> dict[str, int]:
    """Count clients by type."""
    return {
        "total": len(clients),
        "enabled": sum(1 for c in clients if c["enabled"]),
        "public": sum(1 for c in clients if c["public_client"]),
        "confidential": sum(1 for c in clients if not c["public_client"] and not c["bearer_only"]),
        "service_accounts": sum(1 for c in clients if c["service_accounts_enabled"]),
        "bearer_only": sum(1 for c in clients if c["bearer_only"]),
    }


def shape_session_stats(payload: list[Any]) -> dict[str, dict[str, Any]]:
    """Shape GET /client-session-stats (string counts) keyed by client id."""
    stats: dict[str, dict[str, Any]] = {}
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        client_id = str(entry.get("clientId", ""))
        if not client_id:
            continue
        stats[client_id] = {
            "active": safe_int(entry.get("active"), 0),
            "offline": safe_int(entry.get("offline"), 0),
            "id": entry.get("id", ""),
        }
    return stats


def shape_events_config(payload: dict[str, Any]) -> dict[str, Any]:
    """Shape GET /events/config into event-storage settings."""
    return {
        "events_enabled": bool(payload.get("eventsEnabled", False)),
        "events_expiration_seconds": safe_int(payload["eventsExpiration"], 0)
        if payload.get("eventsExpiration") is not None
        else None,
        "events_listeners": list(payload.get("eventsListeners") or []),
        "enabled_event_types_count": len(payload.get("enabledEventTypes") or []),
        "admin_events_enabled": bool(payload.get("adminEventsEnabled", False)),
        "admin_events_details_enabled": bool(payload.get("adminEventsDetailsEnabled", False)),
    }


def shape_user_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Shape one GET /events element into a stable event dict."""
    details = payload.get("details") or {}
    if not isinstance(details, dict):
        details = {}
    time_ms = safe_int(payload.get("time"), 0)
    return {
        "id": payload.get("id", ""),
        "time": iso_from_ms(payload.get("time")),
        "time_ms": time_ms,
        "type": payload.get("type", ""),
        "client_id": payload.get("clientId", ""),
        "user_id": payload.get("userId"),
        "username": details.get("username") or None,
        "ip_address": payload.get("ipAddress", ""),
        "error": payload.get("error") or None,
        "reason": details.get("reason") or None,
        "grant_type": details.get("grant_type") or None,
    }


def _top_counts(values: list[str | None], *, key_name: str) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for value in values:
        if value is None:
            continue
        counts[value] = counts.get(value, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [{key_name: name, "count": count} for name, count in ranked[:TOP_N]]


def summarize_user_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize shaped user events with error/user/ip/client breakdowns."""
    by_error: dict[str, int] = {}
    for event in events:
        error = event.get("error") or "none"
        by_error[error] = by_error.get(error, 0) + 1
    times = [e.get("time_ms", 0) for e in events if e.get("time_ms")]
    return {
        "total": len(events),
        "by_error": by_error,
        "by_username": _top_counts([e.get("username") for e in events], key_name="username"),
        "by_ip": _top_counts([e.get("ip_address") or None for e in events], key_name="ip_address"),
        "by_client": _top_counts(
            [e.get("client_id") or None for e in events], key_name="client_id"
        ),
        "lockouts": sum(1 for e in events if e.get("error") == "user_temporarily_disabled"),
        "unknown_users": sum(1 for e in events if e.get("error") == "user_not_found"),
        "disabled_users": sum(1 for e in events if e.get("error") == "user_disabled"),
        "first_time": iso_from_ms(min(times)) if times else None,
        "last_time": iso_from_ms(max(times)) if times else None,
    }


def shape_admin_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Shape one GET /admin-events element, dropping the representation body."""
    auth = payload.get("authDetails") or {}
    if not isinstance(auth, dict):
        auth = {}
    time_ms = safe_int(payload.get("time"), 0)
    return {
        "id": payload.get("id", ""),
        "time": iso_from_ms(payload.get("time")),
        "time_ms": time_ms,
        "operation_type": payload.get("operationType", ""),
        "resource_type": payload.get("resourceType", ""),
        "resource_path": payload.get("resourcePath", ""),
        "actor_user_id": auth.get("userId", ""),
        "actor_client_id": auth.get("clientId", ""),
        "actor_realm_id": auth.get("realmId", ""),
        "ip_address": auth.get("ipAddress", ""),
        "has_representation": bool(payload.get("representation")),
    }


def summarize_admin_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize shaped admin events by operation, resource type and actor."""
    by_operation: dict[str, int] = {}
    by_resource_type: dict[str, int] = {}
    for event in events:
        operation = event.get("operation_type", "")
        resource = event.get("resource_type", "")
        by_operation[operation] = by_operation.get(operation, 0) + 1
        by_resource_type[resource] = by_resource_type.get(resource, 0) + 1
    times = [e.get("time_ms", 0) for e in events if e.get("time_ms")]
    return {
        "total": len(events),
        "by_operation": by_operation,
        "by_resource_type": by_resource_type,
        "by_actor": _top_counts(
            [e.get("actor_user_id") or None for e in events],
            key_name="actor_user_id",
        ),
        "first_time": iso_from_ms(min(times)) if times else None,
        "last_time": iso_from_ms(max(times)) if times else None,
    }


def shape_user(payload: dict[str, Any]) -> dict[str, Any]:
    """Shape one GET /users element into account state."""
    return {
        "id": payload.get("id", ""),
        "username": payload.get("username", ""),
        "first_name": payload.get("firstName", ""),
        "last_name": payload.get("lastName", ""),
        "email": payload.get("email", ""),
        "email_verified": bool(payload.get("emailVerified", False)),
        "enabled": bool(payload.get("enabled", False)),
        "created_at": iso_from_ms(payload.get("createdTimestamp")),
        "totp": bool(payload.get("totp", False)),
        "required_actions": list(payload.get("requiredActions") or []),
        "not_before": safe_int(payload.get("notBefore"), 0),
    }


def shape_brute_force(payload: dict[str, Any]) -> dict[str, Any]:
    """Shape GET /attack-detection/brute-force/users/{id} into lockout state."""
    not_before = safe_int(payload.get("failedLoginNotBefore"), 0)
    last_failure = safe_int(payload.get("lastFailure"), 0)
    last_ip = payload.get("lastIPFailure", "")
    return {
        "locked": bool(payload.get("disabled", False)),
        "failures": safe_int(payload.get("numFailures"), 0),
        "secondary_auth_failures": safe_int(payload.get("numSecondaryAuthFailures"), 0),
        "temporary_lockouts": safe_int(payload.get("numTemporaryLockouts"), 0),
        "locked_until": iso_from_s(not_before) if not_before > 0 else None,
        "last_failure": iso_from_ms(last_failure) if last_failure > 0 else None,
        "last_failure_ip": None if last_ip in ("", "n/a") else last_ip,
    }


def shape_user_session(payload: dict[str, Any]) -> dict[str, Any]:
    """Shape one GET /users/{id}/sessions element into session state."""
    clients = payload.get("clients") or {}
    names = sorted(str(name) for name in clients.values()) if isinstance(clients, dict) else []
    return {
        "id": payload.get("id", ""),
        "ip_address": payload.get("ipAddress", ""),
        "start": iso_from_ms(payload.get("start")),
        "last_access": iso_from_ms(payload.get("lastAccess")),
        "remember_me": bool(payload.get("rememberMe", False)),
        "clients": names,
    }
