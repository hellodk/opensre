"""nginx connectivity validation (stub_status + Plus API probes)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import integrations.nginx.client as nginx_client
from integrations._validation_helpers import report_validation_failure
from integrations.nginx.config import NginxConfig
from integrations.nginx.plus_api import detect_api_version, fetch_plus, shape_nginx_info
from integrations.nginx.stub_status import parse_stub_status

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NginxValidationResult:
    """Result of validating an nginx integration."""

    ok: bool
    detail: str


def validate_nginx_config(config: NginxConfig) -> NginxValidationResult:
    """Validate nginx reachability with stub_status then Plus API probes."""
    if not config.host:
        return NginxValidationResult(ok=False, detail="nginx host is required.")
    try:
        with nginx_client.build_client(config) as client:
            stub_resp, stub_err = nginx_client.fetch_text(client, config.stub_status_path)
            if stub_err is not None and stub_err.kind in (
                nginx_client.FetchErrorKind.AUTH,
                nginx_client.FetchErrorKind.TRANSPORT,
            ):
                return NginxValidationResult(ok=False, detail=stub_err.message)
            stub_ok = (
                stub_err is None
                and stub_resp is not None
                and parse_stub_status(stub_resp.text) is not None
            )
            if stub_err is None and stub_resp is not None and not stub_ok:
                stub_reason = (
                    f"stub_status at {config.stub_status_path} returned an unrecognised body"
                )
            elif stub_err is not None:
                stub_reason = stub_err.message
            else:
                stub_reason = f"stub_status at {config.stub_status_path} returned no data"
            stub_version = (
                nginx_client.nginx_version_from_server_header(stub_resp.server_header)
                if stub_ok and stub_resp is not None
                else "unknown"
            )

            api_version, api_err = detect_api_version(client, config.api_path)
            if api_err is not None:
                if api_err.kind in (
                    nginx_client.FetchErrorKind.AUTH,
                    nginx_client.FetchErrorKind.TRANSPORT,
                ):
                    return NginxValidationResult(ok=False, detail=api_err.message)
                if stub_ok:
                    return NginxValidationResult(
                        ok=True,
                        detail=(
                            f"nginx {stub_version} at {config.base_url}; "
                            f"stub_status OK at {config.stub_status_path}; "
                            f"NGINX Plus API not found at {config.api_path} "
                            f"(open-source edition assumed)."
                        ),
                    )
                return NginxValidationResult(
                    ok=False,
                    detail=(
                        f"Neither stub_status at {config.stub_status_path} "
                        f"({stub_reason}) nor the NGINX Plus API at "
                        f"{config.api_path} ({api_err.message}) answered on "
                        f"{config.base_url}."
                    ),
                )
            assert api_version is not None
            info, info_err = fetch_plus(client, config.api_path, api_version, "nginx")
            if info_err is not None:
                return NginxValidationResult(ok=False, detail=info_err.message)
            shaped = shape_nginx_info(info if isinstance(info, dict) else {})
            plus_detail = (
                f"NGINX Plus {shaped['build']} (nginx/{shaped['version']}, "
                f"API v{api_version}) at {config.base_url}"
            )
            if stub_ok:
                return NginxValidationResult(
                    ok=True,
                    detail=(
                        f"{plus_detail}; stub_status also available at {config.stub_status_path}."
                    ),
                )
            return NginxValidationResult(
                ok=True,
                detail=(f"{plus_detail}; stub_status not exposed at {config.stub_status_path}."),
            )
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="nginx",
            method="validate_nginx_config",
        )
        return NginxValidationResult(ok=False, detail=f"nginx connection failed: {err}")
