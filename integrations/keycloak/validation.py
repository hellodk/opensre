"""Keycloak connectivity validation: token plus a realm read."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import integrations.keycloak.client as keycloak_client
from integrations._validation_helpers import report_validation_failure
from integrations.keycloak.admin_api import shape_realm
from integrations.keycloak.config import KeycloakConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KeycloakValidationResult:
    """Result of validating a Keycloak integration."""

    ok: bool
    detail: str


def validate_keycloak_config(config: KeycloakConfig) -> KeycloakValidationResult:
    """Validate Keycloak reachability with a token fetch plus a realm read."""
    if not config.is_configured:
        return KeycloakValidationResult(
            ok=False,
            detail="Keycloak url, realm, client_id and client_secret are required.",
        )
    try:
        with keycloak_client.build_client(config) as client:
            token, token_err = keycloak_client.fetch_token(client, config)
            if token_err is not None:
                return KeycloakValidationResult(ok=False, detail=token_err.message)
            assert token is not None
            payload, realm_err = keycloak_client.admin_get(
                client, token, config, config.admin_realm_path
            )
            if realm_err is not None:
                return KeycloakValidationResult(ok=False, detail=realm_err.message)
            if not isinstance(payload, dict):
                return KeycloakValidationResult(
                    ok=False,
                    detail=f"Unexpected {config.admin_realm_path} response.",
                )
            realm = shape_realm(payload)
            detail = (
                f"Keycloak realm '{config.realm}' reachable at {config.url} "
                f"as client '{config.client_id}' "
                f"(realm enabled={realm['enabled']}, "
                f"brute-force protection={'on' if realm['brute_force_protected'] else 'off'}, "
                f"user events={'on' if realm['events_enabled'] else 'off'}, "
                f"admin events={'on' if realm['admin_events_enabled'] else 'off'})"
            )
            if not config.has_management_url:
                return KeycloakValidationResult(
                    ok=True,
                    detail=detail + "; management URL not set (health/metrics unavailable)",
                )
            mgmt = keycloak_client.build_management_client(config)
            with mgmt:
                ready, ready_err = keycloak_client.mgmt_get_json(mgmt, "/health/ready")
            if ready_err is not None:
                return KeycloakValidationResult(
                    ok=True,
                    detail=detail + f"; management URL {config.management_url} not reachable "
                    f"({ready_err.message})",
                )
            status = ready.get("status", "unknown") if isinstance(ready, dict) else "unknown"
            return KeycloakValidationResult(
                ok=True,
                detail=detail + f"; management health {status} at {config.management_url}",
            )
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="keycloak",
            method="validate_keycloak_config",
        )
        return KeycloakValidationResult(ok=False, detail=f"Keycloak connection failed: {err}")
