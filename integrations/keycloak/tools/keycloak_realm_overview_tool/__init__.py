"""Keycloak Realm Overview Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from integrations.keycloak import (
    KeycloakConfig,
    get_realm_overview,
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


def _map_get_keycloak_realm_overview(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite user/client/session counts plus disabled protections."""
    if not output.get("available"):
        return
    clients = output.get("clients") or {}
    sessions = output.get("sessions") or {}
    summary = (
        f"realm {output.get('realm')}: {output.get('users_total')} users, "
        f"{clients.get('total')} clients, {sessions.get('active')} active sessions"
    )
    if output.get("brute_force_protected") is False:
        summary += ", brute-force protection off"
    if output.get("events_enabled") is False:
        summary += ", user events off"
    record_evidence_entry(
        evidence,
        source="get_keycloak_realm_overview",
        label="Keycloak Realm Overview",
        summary=summary,
    )


@tool(
    name="get_keycloak_realm_overview",
    description="Return a Keycloak realm's operational settings and counts: enabled flag, SSL requirement, brute-force protection parameters (failure factor, lockout waits), user and admin event settings, token and session lifespans, total users, client mix (public/confidential/service-account) and active/offline session totals.",
    source="keycloak",
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Checking a realm's brute-force protection settings during a lockout investigation",
        "Seeing how many users and clients a realm holds before a change window",
        "Confirming user and admin event recording is enabled on a realm",
    ],
    is_available=keycloak_is_available,
    injected_params=_KEYCLOAK_INJECTED,
    extract_params=keycloak_extract_params,
    evidence_mapper=_map_get_keycloak_realm_overview,
)
def get_keycloak_realm_overview(
    url: str,
    realm: str,
    client_id: str,
    client_secret: str,
    management_url: str = "",
    auth_realm: str = "",
    verify_ssl: bool = True,
) -> dict[str, Any]:
    """Return realm settings, user/client counts and session totals."""
    config = KeycloakConfig(
        url=url,
        management_url=management_url,
        realm=realm,
        auth_realm=auth_realm,
        client_id=client_id,
        client_secret=client_secret,
        verify_ssl=verify_ssl,
    )
    return get_realm_overview(config)
