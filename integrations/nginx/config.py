"""nginx connection settings and credential resolution."""

from __future__ import annotations

import logging
import os
from typing import Any

from pydantic import Field, field_validator

from config.constants.nginx import (
    NGINX_ACCESS_LOG_PATH_ENV,
    NGINX_API_PATH_ENV,
    NGINX_ERROR_LOG_PATH_ENV,
    NGINX_HOST_ENV,
    NGINX_PASSWORD_ENV,
    NGINX_PORT_ENV,
    NGINX_SSL_ENV,
    NGINX_STUB_STATUS_PATH_ENV,
    NGINX_TIMEOUT_SECONDS_ENV,
    NGINX_USERNAME_ENV,
    NGINX_VERIFY_SSL_ENV,
)
from config.llm_credentials import resolve_env_credential
from config.strict_config import StrictConfigModel
from infrastructure.text.coercion import safe_int
from integrations._validation_helpers import report_classify_failure

logger = logging.getLogger(__name__)

DEFAULT_NGINX_PORT = 80
DEFAULT_NGINX_STUB_STATUS_PATH = "/nginx_status"
DEFAULT_NGINX_API_PATH = "/api"
DEFAULT_NGINX_ACCESS_LOG_PATH = "/var/log/nginx/access.log"
DEFAULT_NGINX_ERROR_LOG_PATH = "/var/log/nginx/error.log"
DEFAULT_NGINX_TIMEOUT_SECONDS = 10
MIN_PORT = 1
MAX_PORT = 65535
_TRUTHY = ("true", "1", "yes")


class NginxConfig(StrictConfigModel):
    """Normalized nginx connection settings."""

    host: str = ""
    port: int = Field(default=DEFAULT_NGINX_PORT, ge=MIN_PORT, le=MAX_PORT)
    ssl: bool = False
    verify_ssl: bool = True
    username: str = ""
    password: str = ""
    stub_status_path: str = DEFAULT_NGINX_STUB_STATUS_PATH
    api_path: str = DEFAULT_NGINX_API_PATH
    access_log_path: str = DEFAULT_NGINX_ACCESS_LOG_PATH
    error_log_path: str = DEFAULT_NGINX_ERROR_LOG_PATH
    timeout_seconds: int = Field(default=DEFAULT_NGINX_TIMEOUT_SECONDS, gt=0)
    integration_id: str = ""

    @field_validator("*", mode="before")
    @classmethod
    def _strip_string_values(cls, value: Any) -> Any:
        # Disable the base-class blanket strip: passwords must keep
        # leading/trailing whitespace, so each field normalizes explicitly.
        return value

    @field_validator("host", mode="before")
    @classmethod
    def _normalize_host(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("username", mode="before")
    @classmethod
    def _normalize_username(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("password", mode="before")
    @classmethod
    def _normalize_password(cls, value: Any) -> str:
        # Do NOT strip passwords — leading/trailing whitespace is valid.
        return str(value or "")

    @field_validator("port", mode="before")
    @classmethod
    def _normalize_port(cls, value: Any) -> int:
        return safe_int(value, DEFAULT_NGINX_PORT)

    @field_validator("stub_status_path", mode="before")
    @classmethod
    def _normalize_stub_status_path(cls, value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return DEFAULT_NGINX_STUB_STATUS_PATH
        if not raw.startswith("/"):
            raw = f"/{raw}"
        return raw.rstrip("/") or DEFAULT_NGINX_STUB_STATUS_PATH

    @field_validator("api_path", mode="before")
    @classmethod
    def _normalize_api_path(cls, value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return DEFAULT_NGINX_API_PATH
        if not raw.startswith("/"):
            raw = f"/{raw}"
        return raw.rstrip("/") or DEFAULT_NGINX_API_PATH

    @field_validator("access_log_path", mode="before")
    @classmethod
    def _normalize_access_log_path(cls, value: Any) -> str:
        return str(value or "").strip() or DEFAULT_NGINX_ACCESS_LOG_PATH

    @field_validator("error_log_path", mode="before")
    @classmethod
    def _normalize_error_log_path(cls, value: Any) -> str:
        return str(value or "").strip() or DEFAULT_NGINX_ERROR_LOG_PATH

    @property
    def is_configured(self) -> bool:
        return bool(self.host)

    @property
    def base_url(self) -> str:
        scheme = "https" if self.ssl else "http"
        return f"{scheme}://{self.host}:{self.port}"

    @property
    def auth(self) -> tuple[str, str] | None:
        if not self.username:
            return None
        return (self.username, self.password)


def build_nginx_config(raw: dict[str, Any] | None) -> NginxConfig:
    """Build a normalized nginx config object from env/store data."""
    return NginxConfig.model_validate(raw or {})


def nginx_config_from_env() -> NginxConfig | None:
    """Load an nginx config from env vars."""
    host = os.getenv(NGINX_HOST_ENV, "").strip()
    if not host:
        return None
    return build_nginx_config(
        {
            "host": host,
            "port": os.getenv(NGINX_PORT_ENV, str(DEFAULT_NGINX_PORT)).strip(),
            "ssl": os.getenv(NGINX_SSL_ENV, "false").strip().lower() in _TRUTHY,
            "verify_ssl": os.getenv(NGINX_VERIFY_SSL_ENV, "true").strip().lower() in _TRUTHY,
            "username": os.getenv(NGINX_USERNAME_ENV, "").strip(),
            "password": resolve_env_credential(NGINX_PASSWORD_ENV) or "",
            "stub_status_path": os.getenv(
                NGINX_STUB_STATUS_PATH_ENV, DEFAULT_NGINX_STUB_STATUS_PATH
            ).strip(),
            "api_path": os.getenv(NGINX_API_PATH_ENV, DEFAULT_NGINX_API_PATH).strip(),
            "access_log_path": os.getenv(
                NGINX_ACCESS_LOG_PATH_ENV, DEFAULT_NGINX_ACCESS_LOG_PATH
            ).strip(),
            "error_log_path": os.getenv(
                NGINX_ERROR_LOG_PATH_ENV, DEFAULT_NGINX_ERROR_LOG_PATH
            ).strip(),
            "timeout_seconds": os.getenv(
                NGINX_TIMEOUT_SECONDS_ENV, str(DEFAULT_NGINX_TIMEOUT_SECONDS)
            ).strip(),
        }
    )


def nginx_is_available(sources: dict[str, dict]) -> bool:
    """Check if nginx integration credentials are present."""
    return bool(sources.get("nginx", {}).get("host"))


def nginx_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract nginx connection params from resolved integrations."""
    nginx = sources.get("nginx", {})
    verify_ssl = nginx.get("verify_ssl", True)
    return {
        "host": str(nginx.get("host", "")),
        "port": safe_int(nginx.get("port", DEFAULT_NGINX_PORT), DEFAULT_NGINX_PORT),
        "ssl": bool(nginx.get("ssl", False)),
        "verify_ssl": True if verify_ssl is None else bool(verify_ssl),
        "username": str(nginx.get("username", "")),
        "password": str(nginx.get("password", "")),
        "stub_status_path": str(
            nginx.get("stub_status_path", DEFAULT_NGINX_STUB_STATUS_PATH)
            or DEFAULT_NGINX_STUB_STATUS_PATH
        ),
        "api_path": str(nginx.get("api_path", DEFAULT_NGINX_API_PATH) or DEFAULT_NGINX_API_PATH),
        "access_log_path": str(
            nginx.get("access_log_path", DEFAULT_NGINX_ACCESS_LOG_PATH)
            or DEFAULT_NGINX_ACCESS_LOG_PATH
        ),
        "error_log_path": str(
            nginx.get("error_log_path", DEFAULT_NGINX_ERROR_LOG_PATH)
            or DEFAULT_NGINX_ERROR_LOG_PATH
        ),
    }


def classify(credentials: dict[str, Any], record_id: str) -> tuple[NginxConfig | None, str | None]:
    try:
        cfg = build_nginx_config(
            {
                "host": credentials.get("host", ""),
                "port": credentials.get("port", DEFAULT_NGINX_PORT),
                "ssl": credentials.get("ssl", False),
                "verify_ssl": credentials.get("verify_ssl", True),
                "username": credentials.get("username", ""),
                "password": credentials.get("password", ""),
                "stub_status_path": credentials.get(
                    "stub_status_path", DEFAULT_NGINX_STUB_STATUS_PATH
                ),
                "api_path": credentials.get("api_path", DEFAULT_NGINX_API_PATH),
                "access_log_path": credentials.get(
                    "access_log_path", DEFAULT_NGINX_ACCESS_LOG_PATH
                ),
                "error_log_path": credentials.get("error_log_path", DEFAULT_NGINX_ERROR_LOG_PATH),
                "timeout_seconds": credentials.get(
                    "timeout_seconds", DEFAULT_NGINX_TIMEOUT_SECONDS
                ),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="nginx", record_id=record_id)
        return None, None
    if cfg.host:
        return cfg, "nginx"
    return None, None
