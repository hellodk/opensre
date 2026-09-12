"""Keycloak Login Failures Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from integrations.keycloak import (
    KeycloakConfig,
    get_login_failures,
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


def _map_get_keycloak_login_failures(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite failure totals, lockouts and the top error."""
    if not output.get("available"):
        return
    if output.get("events_enabled") is False:
        record_evidence_entry(
            evidence,
            source="get_keycloak_login_failures",
            label="Keycloak Login Failures",
            summary=f"user events disabled on realm {output.get('realm')}",
        )
        return
    summary_data = output.get("summary") or {}
    summary = f"{summary_data.get('total', 0)} {output.get('event_type')} event(s)"
    if summary_data.get("lockouts"):
        summary += f", {summary_data['lockouts']} lockout(s)"
    by_error = summary_data.get("by_error") or {}
    if by_error:
        top_error = next(iter(by_error.items()))
        summary += f", top: {top_error[0]} ({top_error[1]})"
    record_evidence_entry(
        evidence,
        source="get_keycloak_login_failures",
        label="Keycloak Login Failures",
        summary=summary,
    )


@tool(
    name="get_keycloak_login_failures",
    description="Return recent Keycloak login failure events for a realm (LOGIN_ERROR by default; other error event types selectable) with per-error, per-user, per-IP and per-client counts, lockout and unknown-user tallies. Optionally scope to one username. Requires user events to be enabled on the realm; reports when they are not.",
    source="keycloak",
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Investigating why a user cannot log in to an application behind Keycloak",
        "Spotting credential-stuffing bursts by source IP across recent login errors",
        "Counting temporary lockouts after raising the brute-force failure factor",
    ],
    is_available=keycloak_is_available,
    injected_params=_KEYCLOAK_INJECTED,
    extract_params=keycloak_extract_params,
    evidence_mapper=_map_get_keycloak_login_failures,
)
def get_keycloak_login_failures(
    url: str,
    realm: str,
    client_id: str,
    client_secret: str,
    management_url: str = "",
    auth_realm: str = "",
    verify_ssl: bool = True,
    event_type: str = "LOGIN_ERROR",
    max_events: int = 100,
    username: str = "",
) -> dict[str, Any]:
    """Return recent login failure events with failure breakdowns."""
    config = KeycloakConfig(
        url=url,
        management_url=management_url,
        realm=realm,
        auth_realm=auth_realm,
        client_id=client_id,
        client_secret=client_secret,
        verify_ssl=verify_ssl,
    )
    return get_login_failures(
        config, event_type=event_type, max_events=max_events, username=username
    )
