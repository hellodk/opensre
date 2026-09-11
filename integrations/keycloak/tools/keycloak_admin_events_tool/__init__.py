"""Keycloak Admin Events Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from integrations.keycloak import (
    KeycloakConfig,
    get_admin_events,
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


def _map_get_keycloak_admin_events(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite admin-event totals and the top operation/resource pair."""
    if not output.get("available"):
        return
    if output.get("admin_events_enabled") is False:
        record_evidence_entry(
            evidence,
            source="get_keycloak_admin_events",
            label="Keycloak Admin Events",
            summary=f"admin events disabled on realm {output.get('realm')}",
        )
        return
    summary_data = output.get("summary") or {}
    summary = f"{summary_data.get('total', 0)} admin event(s)"
    by_operation = summary_data.get("by_operation") or {}
    by_resource = summary_data.get("by_resource_type") or {}
    if by_operation and by_resource:
        top_operation = max(by_operation.items(), key=lambda item: (item[1], item[0]))
        top_resource = max(by_resource.items(), key=lambda item: (item[1], item[0]))
        summary += f", top: {top_operation[0]} {top_resource[0]}"
    record_evidence_entry(
        evidence,
        source="get_keycloak_admin_events",
        label="Keycloak Admin Events",
        summary=summary,
    )


@tool(
    name="get_keycloak_admin_events",
    description="Return recent Keycloak admin events (who changed what: operation type, resource type and path, acting user/client IDs, IP) with counts by operation, resource type and actor. Filter by resource type or operation type. Requires admin events to be enabled on the realm; reports when they are not.",
    source="keycloak",
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Auditing who changed a client or role in a realm",
        "Listing recently created users after an unexpected access grant",
        "Checking which operators modified realm settings before an outage",
    ],
    is_available=keycloak_is_available,
    injected_params=_KEYCLOAK_INJECTED,
    extract_params=keycloak_extract_params,
    evidence_mapper=_map_get_keycloak_admin_events,
)
def get_keycloak_admin_events(
    url: str,
    realm: str,
    client_id: str,
    client_secret: str,
    management_url: str = "",
    auth_realm: str = "",
    verify_ssl: bool = True,
    max_events: int = 100,
    resource_type: str = "",
    operation_type: str = "",
) -> dict[str, Any]:
    """Return recent admin events with operation/resource/actor counts."""
    config = KeycloakConfig(
        url=url,
        management_url=management_url,
        realm=realm,
        auth_realm=auth_realm,
        client_id=client_id,
        client_secret=client_secret,
        verify_ssl=verify_ssl,
    )
    return get_admin_events(
        config,
        max_events=max_events,
        resource_type=resource_type,
        operation_type=operation_type,
    )
