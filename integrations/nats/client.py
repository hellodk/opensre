"""HTTP client for the nats-server monitoring endpoint."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus
from typing import Any

import httpx

from integrations.nats.config import NatsConfig


class FetchErrorKind(StrEnum):
    """Machine-readable cause of a failed monitoring-endpoint fetch."""

    TRANSPORT = "transport"
    WRONG_PORT = "wrong_port"
    AUTH = "auth"
    NOT_FOUND = "not_found"
    HTTP = "http"
    BODY = "body"


@dataclass(frozen=True)
class FetchError:
    """Why a monitoring-endpoint fetch failed."""

    kind: FetchErrorKind
    message: str
    status: int | None = None


@dataclass(frozen=True)
class FetchResult:
    """A successful monitoring-endpoint fetch."""

    status: int
    payload: Any


def build_client(config: NatsConfig) -> httpx.Client:
    """Build an authenticated httpx client for the monitoring endpoint."""
    auth = httpx.BasicAuth(config.username, config.password) if config.has_auth else None
    return httpx.Client(
        base_url=config.url,
        timeout=float(config.timeout_seconds),
        verify=config.verify_ssl,
        auth=auth,
        headers={"Accept": "application/json"},
    )


def get_json(
    client: httpx.Client,
    config: NatsConfig,
    path: str,
    params: dict[str, Any] | None = None,
    *,
    accept: tuple[HTTPStatus, ...] = (HTTPStatus.OK,),
) -> tuple[FetchResult | None, FetchError | None]:
    """GET a monitoring-endpoint path; never raises for HTTP/transport problems."""
    try:
        response = client.get(path, params=params)
    except httpx.RemoteProtocolError:
        return None, FetchError(
            kind=FetchErrorKind.WRONG_PORT,
            message=(
                f"NATS monitoring endpoint {config.url} did not answer HTTP; "
                "the URL probably points at the client port (4222) instead of "
                "the monitoring port (default 8222)."
            ),
        )
    except httpx.RequestError as err:
        return None, FetchError(
            kind=FetchErrorKind.TRANSPORT,
            message=f"NATS monitoring request failed: {err}",
        )

    if response.status_code in accept:
        try:
            return FetchResult(status=response.status_code, payload=response.json()), None
        except ValueError:
            return None, FetchError(
                kind=FetchErrorKind.BODY,
                message=(
                    f"NATS monitoring endpoint returned a non-JSON body for {path}; "
                    "check that NATS_MONITOR_URL is the HTTP monitoring port "
                    "(default 8222), not a client or proxy port."
                ),
                status=response.status_code,
            )
    if response.status_code in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
        return None, FetchError(
            kind=FetchErrorKind.AUTH,
            message=(
                f"NATS monitoring endpoint {config.url} rejected {path} with HTTP "
                f"{response.status_code}. nats-server has no authentication on the "
                "monitoring port, so check the proxy credentials "
                "(NATS_MONITOR_USERNAME / NATS_MONITOR_PASSWORD)."
            ),
            status=response.status_code,
        )
    if response.status_code == HTTPStatus.NOT_FOUND:
        return None, FetchError(
            kind=FetchErrorKind.NOT_FOUND,
            message=(
                f"NATS monitoring endpoint returned 404 for {path}: {response.text.strip()[:120]}"
            ),
            status=response.status_code,
        )
    if response.status_code == HTTPStatus.BAD_REQUEST:
        return None, FetchError(
            kind=FetchErrorKind.HTTP,
            message=(f"NATS monitoring endpoint rejected {path}: {response.text.strip()[:200]}"),
            status=response.status_code,
        )
    return None, FetchError(
        kind=FetchErrorKind.HTTP,
        message=(
            f"NATS monitoring endpoint returned HTTP {response.status_code} "
            f"for {path}: {response.text.strip()[:200]}"
        ),
        status=response.status_code,
    )
