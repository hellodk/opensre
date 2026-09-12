"""Keycloak Server Status Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from integrations.keycloak import (
    KeycloakConfig,
    get_server_status,
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


def _map_get_keycloak_server_status(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite readiness, heap pressure, pool saturation and 5xx counts."""
    if not output.get("available"):
        return
    version = output.get("version") or "version hidden"
    management = output.get("management") or {}
    health = management.get("health") or {}
    summary = f"Keycloak {version}: ready {health.get('status') or 'unknown'}"
    metrics = management.get("metrics") or {}
    jvm = metrics.get("jvm") or {}
    if jvm.get("heap_used_pct") is not None:
        summary += f", heap {jvm['heap_used_pct']}%"
    pools = metrics.get("db_pool") or []
    if pools:
        pool = pools[0]
        summary += f", db pool {pool['active']}/{pool['active'] + pool['available']}"
    http = metrics.get("http") or {}
    five_xx = (http.get("by_status_class") or {}).get("5xx", 0)
    if five_xx:
        summary += f", {five_xx:g} 5xx responses"
    record_evidence_entry(
        evidence,
        source="get_keycloak_server_status",
        label="Keycloak Server Status",
        summary=summary,
    )


@tool(
    name="get_keycloak_server_status",
    description="Return Keycloak server status: readiness and liveness checks including the database check, JVM heap and GC, database connection pool usage, HTTP request counts by status class with the top 5xx endpoints, cluster size, and per-realm login success/failure counters from the metrics endpoint. Version is shown only when the service account is a master-realm admin.",
    source="keycloak",
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Checking whether Keycloak is healthy and its database connection pool is saturated",
        "Seeing per-realm login success and failure counters during an authentication outage",
        "Finding which admin endpoints return 5xx errors under load",
    ],
    is_available=keycloak_is_available,
    injected_params=_KEYCLOAK_INJECTED,
    extract_params=keycloak_extract_params,
    evidence_mapper=_map_get_keycloak_server_status,
)
def get_keycloak_server_status(
    url: str,
    realm: str,
    client_id: str,
    client_secret: str,
    management_url: str = "",
    auth_realm: str = "",
    verify_ssl: bool = True,
) -> dict[str, Any]:
    """Return readiness, JVM, pool, HTTP and login-counter diagnostics."""
    config = KeycloakConfig(
        url=url,
        management_url=management_url,
        realm=realm,
        auth_realm=auth_realm,
        client_id=client_id,
        client_secret=client_secret,
        verify_ssl=verify_ssl,
    )
    return get_server_status(config)
