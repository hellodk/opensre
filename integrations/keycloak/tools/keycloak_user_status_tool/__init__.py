"""Keycloak User Status Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from integrations.keycloak import (
    KeycloakConfig,
    get_user_status,
    keycloak_extract_params,
    keycloak_is_available,
)

_KEYCLOAK_INJECTED = (
    "url",
    "management_url",
    "realm",
    "auth_realm",
    "client_id",
    "client_secret",
    "verify_ssl",
)


def _map_get_keycloak_user_status(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the login diagnosis for one user."""
    if not output.get("available"):
        return
    if output.get("found") is False:
        record_evidence_entry(
            evidence,
            source="get_keycloak_user_status",
            label="Keycloak User Status",
            summary=f"user {output.get('username')} not found in realm {output.get('realm')}",
        )
        return
    diagnosis = output.get("diagnosis") or []
    user = output.get("user") or {}
    summary = f"user {user.get('username')}: {'; '.join(diagnosis) or 'no problems found'}"
    record_evidence_entry(
        evidence,
        source="get_keycloak_user_status",
        label="Keycloak User Status",
        summary=summary,
    )


@tool(
    name="get_keycloak_user_status",
    description="Look up one Keycloak user by username or email and return account state (enabled, email verified, required actions, TOTP), brute-force lockout state (locked until, failure count, last failing IP), active sessions and recent login events, plus a short diagnosis list explaining why the user cannot log in.",
    source="keycloak",
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Investigating why a user cannot log in to an application behind Keycloak",
        "Checking whether an account is locked by brute-force protection and until when",
        "Seeing a user's pending required actions and active sessions",
    ],
    is_available=keycloak_is_available,
    injected_params=_KEYCLOAK_INJECTED,
    extract_params=keycloak_extract_params,
    evidence_mapper=_map_get_keycloak_user_status,
)
def get_keycloak_user_status(
    url: str,
    realm: str,
    client_id: str,
    client_secret: str,
    username: str,
    management_url: str = "",
    auth_realm: str = "",
    verify_ssl: bool = True,
) -> dict[str, Any]:
    """Return account, lockout, session and recent-event state for one user."""
    config = KeycloakConfig(
        url=url,
        management_url=management_url,
        realm=realm,
        auth_realm=auth_realm,
        client_id=client_id,
        client_secret=client_secret,
        verify_ssl=verify_ssl,
    )
    return get_user_status(config, username)
