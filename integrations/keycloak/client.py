"""HTTP transport for the Keycloak Admin REST API and management interface.

Every helper returns ``(data, FetchError | None)`` and never raises for HTTP
or transport problems. The secret is never included in any message.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus
from typing import Any

import httpx

from integrations.keycloak.config import KeycloakConfig

logger = logging.getLogger(__name__)


class FetchErrorKind(StrEnum):
    """Machine-readable cause of a failed Keycloak request."""

    TRANSPORT = "transport"
    AUTH = "auth"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    HTTP = "http"
    BODY = "body"


@dataclass(frozen=True)
class FetchError:
    """A failed Keycloak request with a stable kind and safe message."""

    kind: FetchErrorKind
    message: str


def build_client(config: KeycloakConfig) -> httpx.Client:
    """Build an httpx client for the Keycloak Admin REST API."""
    return httpx.Client(
        base_url=config.url,
        timeout=float(config.timeout_seconds),
        verify=config.verify_ssl,
        headers={"Accept": "application/json"},
    )


def build_management_client(config: KeycloakConfig) -> httpx.Client:
    """Build an httpx client for the :9000 health/metrics interface."""
    return httpx.Client(
        base_url=config.management_url,
        timeout=float(config.timeout_seconds),
        verify=config.verify_ssl,
        headers={"Accept": "application/json, text/plain;q=0.9"},
    )


def _error_body_text(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:120]
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, str) and error:
            return error
    return response.text[:120]


def fetch_token(
    client: httpx.Client, config: KeycloakConfig
) -> tuple[str | None, FetchError | None]:
    """Fetch a service-account token via the client_credentials grant."""
    try:
        response = client.post(
            config.token_path,
            data={
                "grant_type": "client_credentials",
                "client_id": config.client_id,
                "client_secret": config.client_secret,
            },
        )
    except httpx.RequestError as err:
        return None, FetchError(FetchErrorKind.TRANSPORT, f"Keycloak request failed: {err}")
    if response.status_code == HTTPStatus.UNAUTHORIZED:
        try:
            body = response.json()
        except ValueError:
            body = {}
        error = body.get("error", "") if isinstance(body, dict) else ""
        description = body.get("error_description", "") if isinstance(body, dict) else ""
        if description == "Public client not allowed to retrieve service account":
            return None, FetchError(
                FetchErrorKind.AUTH,
                f"Keycloak client '{config.client_id}' is a public client; "
                "enable Client authentication and Service accounts roles on it.",
            )
        return None, FetchError(
            FetchErrorKind.AUTH,
            f"Keycloak rejected client '{config.client_id}' in realm "
            f"'{config.auth_realm}': {description or error} "
            "(check KEYCLOAK_CLIENT_ID / KEYCLOAK_CLIENT_SECRET).",
        )
    if response.status_code == HTTPStatus.NOT_FOUND:
        return None, FetchError(
            FetchErrorKind.NOT_FOUND,
            f"Keycloak realm '{config.auth_realm}' does not exist at {config.url}.",
        )
    if response.status_code >= HTTPStatus.BAD_REQUEST:
        return None, FetchError(
            FetchErrorKind.HTTP,
            f"Keycloak token endpoint returned HTTP {response.status_code}: {response.text[:200]}",
        )
    try:
        payload = response.json()
    except ValueError:
        return None, FetchError(FetchErrorKind.BODY, "Keycloak token response has no access_token.")
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not token:
        return None, FetchError(FetchErrorKind.BODY, "Keycloak token response has no access_token.")
    return str(token), None


def _map_status_error(response: httpx.Response, config: KeycloakConfig, path: str) -> FetchError:
    if response.status_code == HTTPStatus.UNAUTHORIZED:
        return FetchError(
            FetchErrorKind.AUTH,
            f"Keycloak rejected the access token for {path}.",
        )
    if response.status_code == HTTPStatus.FORBIDDEN:
        return FetchError(
            FetchErrorKind.FORBIDDEN,
            f"Keycloak denied {path}: the service account of client "
            f"'{config.client_id}' needs the realm-management roles view-realm, "
            f"view-users, view-clients and view-events in realm '{config.realm}'.",
        )
    if response.status_code == HTTPStatus.NOT_FOUND:
        return FetchError(
            FetchErrorKind.NOT_FOUND,
            f"Keycloak returned 404 for {path}: {_error_body_text(response)}",
        )
    return FetchError(
        FetchErrorKind.HTTP,
        f"Keycloak returned HTTP {response.status_code} for {path}: {response.text[:200]}",
    )


def admin_get(
    client: httpx.Client,
    token: str,
    config: KeycloakConfig,
    path: str,
    params: dict[str, Any] | None = None,
) -> tuple[Any | None, FetchError | None]:
    """GET an Admin REST API path with the service-account bearer token."""
    try:
        response = client.get(path, headers={"Authorization": f"Bearer {token}"}, params=params)
    except httpx.RequestError as err:
        return None, FetchError(FetchErrorKind.TRANSPORT, f"Keycloak request failed: {err}")
    if response.status_code >= HTTPStatus.BAD_REQUEST:
        return None, _map_status_error(response, config, path)
    try:
        return response.json(), None
    except ValueError:
        return None, FetchError(
            FetchErrorKind.BODY, f"Keycloak returned a non-JSON body for {path}"
        )


def mgmt_get_json(client: httpx.Client, path: str) -> tuple[Any | None, FetchError | None]:
    """GET a JSON management endpoint (no auth)."""
    try:
        response = client.get(path)
    except httpx.RequestError as err:
        return None, FetchError(FetchErrorKind.TRANSPORT, f"Keycloak request failed: {err}")
    if response.status_code >= HTTPStatus.BAD_REQUEST:
        return None, FetchError(
            FetchErrorKind.HTTP,
            f"Keycloak returned HTTP {response.status_code} for {path}: {response.text[:200]}",
        )
    try:
        return response.json(), None
    except ValueError:
        return None, FetchError(
            FetchErrorKind.BODY, f"Keycloak returned a non-JSON body for {path}"
        )


def mgmt_get_text(client: httpx.Client, path: str) -> tuple[str | None, FetchError | None]:
    """GET a text management endpoint such as /metrics (no auth)."""
    try:
        response = client.get(path)
    except httpx.RequestError as err:
        return None, FetchError(FetchErrorKind.TRANSPORT, f"Keycloak request failed: {err}")
    if response.status_code >= HTTPStatus.BAD_REQUEST:
        return None, FetchError(
            FetchErrorKind.HTTP,
            f"Keycloak returned HTTP {response.status_code} for {path}: {response.text[:200]}",
        )
    return response.text, None
