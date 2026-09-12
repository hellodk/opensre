"""HTTP transport for the nginx integration (stub_status + Plus API)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus
from typing import Any

import httpx

from integrations.nginx.config import NginxConfig


class FetchErrorKind(StrEnum):
    """Machine-readable failure classes for nginx HTTP fetches."""

    TRANSPORT = "transport"
    AUTH = "auth"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    HTTP = "http"
    BODY = "body"


@dataclass(frozen=True)
class FetchError:
    """A failed nginx HTTP fetch that callers report instead of raising."""

    kind: FetchErrorKind
    message: str


@dataclass(frozen=True)
class TextResponse:
    """A plain-text nginx response plus its Server header."""

    text: str
    server_header: str


def build_client(config: NginxConfig) -> httpx.Client:
    """Build an authenticated httpx client for stub_status and the Plus API."""
    return httpx.Client(
        base_url=config.base_url,
        auth=config.auth,
        timeout=float(config.timeout_seconds),
        verify=config.verify_ssl,
        headers={"Accept": "application/json, text/plain;q=0.9, */*;q=0.1"},
    )


def _map_status(path: str, response: httpx.Response) -> FetchError | None:
    """Map an HTTP error status to a FetchError, or None when successful."""
    if response.status_code == HTTPStatus.UNAUTHORIZED:
        return FetchError(
            FetchErrorKind.AUTH,
            "nginx authentication failed (check username/password).",
        )
    if response.status_code == HTTPStatus.FORBIDDEN:
        return FetchError(
            FetchErrorKind.FORBIDDEN,
            f"nginx denied access to {path} (check allow/deny rules).",
        )
    if response.status_code == HTTPStatus.NOT_FOUND:
        return FetchError(FetchErrorKind.NOT_FOUND, f"nginx endpoint not found: {path}")
    if response.status_code >= HTTPStatus.BAD_REQUEST:
        return FetchError(
            FetchErrorKind.HTTP,
            f"nginx returned HTTP {response.status_code} for {path}: {response.text[:200]}",
        )
    return None


def fetch_text(client: httpx.Client, path: str) -> tuple[TextResponse | None, FetchError | None]:
    """Fetch a plain-text endpoint; return (data, error)."""
    try:
        response = client.get(path)
    except httpx.RequestError as err:
        return None, FetchError(FetchErrorKind.TRANSPORT, f"nginx request failed: {err}")
    mapped = _map_status(path, response)
    if mapped is not None:
        return None, mapped
    return (
        TextResponse(text=response.text, server_header=response.headers.get("server", "")),
        None,
    )


def fetch_json(client: httpx.Client, path: str) -> tuple[Any | None, FetchError | None]:
    """Fetch a JSON endpoint; return (data, error)."""
    try:
        response = client.get(path)
    except httpx.RequestError as err:
        return None, FetchError(FetchErrorKind.TRANSPORT, f"nginx request failed: {err}")
    mapped = _map_status(path, response)
    if mapped is not None:
        return None, mapped
    try:
        return response.json(), None
    except ValueError:
        return None, FetchError(FetchErrorKind.BODY, f"nginx returned a non-JSON body for {path}")


def nginx_version_from_server_header(value: str) -> str:
    """Extract the version from a Server response header.

    ``nginx/1.27.5`` becomes ``1.27.5``; a bare ``nginx`` (server_tokens off)
    or an empty header becomes ``unknown``.
    """
    text = (value or "").strip()
    _product, sep, version = text.partition("/")
    if not sep:
        return "unknown"
    token = version.split()[0] if version.split() else ""
    return token or "unknown"
