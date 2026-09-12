"""Shared Keycloak integration helpers.

Read-only diagnostics for Keycloak identity realms via the Admin REST API
(authenticated with a confidential service-account client) plus the :9000
management health/metrics interface. Keycloak 25 or newer is required.
"""

from __future__ import annotations

from integrations.keycloak.config import (
    DEFAULT_KEYCLOAK_TIMEOUT_SECONDS,
    KeycloakConfig,
    build_keycloak_config,
    classify,
    keycloak_config_from_env,
    keycloak_extract_params,
    keycloak_is_available,
)
from integrations.keycloak.diagnostics import (
    ADMIN_EVENTS_DISABLED_HINT,
    ALLOWED_USER_EVENT_TYPES,
    DEFAULT_MAX_EVENTS,
    EVENTS_DISABLED_HINT,
    MANAGEMENT_URL_UNSET,
    MAX_EVENTS,
    get_admin_events,
    get_client_sessions,
    get_login_failures,
    get_realm_overview,
    get_server_status,
    get_user_status,
)
from integrations.keycloak.validation import (
    KeycloakValidationResult,
    validate_keycloak_config,
)

__all__ = [
    "ADMIN_EVENTS_DISABLED_HINT",
    "ALLOWED_USER_EVENT_TYPES",
    "DEFAULT_KEYCLOAK_TIMEOUT_SECONDS",
    "DEFAULT_MAX_EVENTS",
    "EVENTS_DISABLED_HINT",
    "KeycloakConfig",
    "KeycloakValidationResult",
    "MANAGEMENT_URL_UNSET",
    "MAX_EVENTS",
    "build_keycloak_config",
    "classify",
    "get_admin_events",
    "get_client_sessions",
    "get_login_failures",
    "get_realm_overview",
    "get_server_status",
    "get_user_status",
    "keycloak_config_from_env",
    "keycloak_extract_params",
    "keycloak_is_available",
    "validate_keycloak_config",
]
