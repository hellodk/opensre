"""Keycloak Client Sessions Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from integrations.keycloak import (
    KeycloakConfig,
    get_client_sessions,
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


def _map_get_keycloak_client_sessions(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite client/session totals and the busiest client."""
    if not output.get("available"):
        return
    totals = output.get("totals") or {}
    summary = (
        f"{totals.get('clients', 0)} client(s), "
        f"{totals.get('active_sessions', 0)} active session(s)"
    )
    clients = output.get("clients") or []
    if clients and clients[0].get("active_sessions"):
        summary += f", busiest: {clients[0]['client_id']} ({clients[0]['active_sessions']})"
    record_evidence_entry(
        evidence,
        source="get_keycloak_client_sessions",
        label="Keycloak Client Sessions",
        summary=summary,
    )


@tool(
    name="get_keycloak_client_sessions",
    description="Return every client in a Keycloak realm with its enabled flag, client type (public, confidential, bearer-only, service account), enabled flows and current active and offline session counts. Optionally filter to one client id.",
    source="keycloak",
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Seeing which clients currently hold active user sessions",
        "Checking whether a client is public or confidential before rotating its secret",
        "Listing service-account clients provisioned in a realm",
    ],
    is_available=keycloak_is_available,
    injected_params=_KEYCLOAK_INJECTED,
    extract_params=keycloak_extract_params,
    evidence_mapper=_map_get_keycloak_client_sessions,
)
def get_keycloak_client_sessions(
    url: str,
    realm: str,
    client_id: str,
    client_secret: str,
    management_url: str = "",
    auth_realm: str = "",
    verify_ssl: bool = True,
    filter_client_id: str = "",
) -> dict[str, Any]:
    """Return every client merged with its session counts."""
    config = KeycloakConfig(
        url=url,
        management_url=management_url,
        realm=realm,
        auth_realm=auth_realm,
        client_id=client_id,
        client_secret=client_secret,
        verify_ssl=verify_ssl,
    )
    return get_client_sessions(config, client_id=filter_client_id)
