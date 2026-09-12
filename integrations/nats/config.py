"""NATS monitoring-endpoint connection settings and credential resolution."""

from __future__ import annotations

import logging
import os
from typing import Any

from pydantic import Field, ValidationInfo, field_validator

from config.constants.nats import (
    NATS_MONITOR_PASSWORD_ENV,
    NATS_MONITOR_URL_ENV,
    NATS_MONITOR_USERNAME_ENV,
    NATS_TIMEOUT_SECONDS_ENV,
    NATS_VERIFY_SSL_ENV,
)
from config.llm_credentials import resolve_env_credential
from config.strict_config import StrictConfigModel
from infrastructure.text.coercion import safe_int
from integrations._validation_helpers import report_classify_failure

logger = logging.getLogger(__name__)

DEFAULT_NATS_TIMEOUT_SECONDS = 10
DEFAULT_NATS_MONITOR_PORT = 8222
_TRUTHY = ("true", "1", "yes")
_ALLOWED_SCHEMES = ("http://", "https://")


class NatsConfig(StrictConfigModel):
    """Normalized NATS monitoring-endpoint connection settings."""

    url: str = ""
    username: str = ""
    password: str = ""
    verify_ssl: bool = True
    timeout_seconds: int = Field(default=DEFAULT_NATS_TIMEOUT_SECONDS, gt=0)
    integration_id: str = ""

    @field_validator("*", mode="before")
    @classmethod
    def _strip_string_values(cls, value: Any, info: ValidationInfo) -> Any:  # type: ignore[override]
        # The base model strips every string, but a password must survive
        # verbatim: leading/trailing whitespace is valid credential content.
        if info.field_name == "password":
            return value
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("url", mode="before")
    @classmethod
    def _normalize_url(cls, value: Any) -> str:
        raw = str(value or "").strip().rstrip("/")
        if raw and not raw.startswith(_ALLOWED_SCHEMES):
            raise ValueError("NATS monitoring URL must start with http:// or https://")
        return raw

    @field_validator("username", mode="before")
    @classmethod
    def _normalize_username(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("password", mode="before")
    @classmethod
    def _normalize_password(cls, value: Any) -> str:
        # Do NOT strip passwords — leading/trailing whitespace is valid.
        return str(value or "")

    @field_validator("verify_ssl", mode="before")
    @classmethod
    def _normalize_verify_ssl(cls, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in _TRUTHY

    @field_validator("timeout_seconds", mode="before")
    @classmethod
    def _normalize_timeout_seconds(cls, value: Any) -> int:
        return safe_int(value, DEFAULT_NATS_TIMEOUT_SECONDS)

    @property
    def is_configured(self) -> bool:
        return bool(self.url)

    @property
    def has_auth(self) -> bool:
        return bool(self.username)


def build_nats_config(raw: dict[str, Any] | None) -> NatsConfig:
    """Build a normalized NATS config object from env/store data."""
    return NatsConfig.model_validate(raw or {})


def nats_config_from_env() -> NatsConfig | None:
    """Load a NATS config from env vars."""
    url = os.getenv(NATS_MONITOR_URL_ENV, "").strip()
    if not url:
        return None
    return build_nats_config(
        {
            "url": url,
            "username": os.getenv(NATS_MONITOR_USERNAME_ENV, "").strip(),
            "password": resolve_env_credential(NATS_MONITOR_PASSWORD_ENV) or "",
            "verify_ssl": os.getenv(NATS_VERIFY_SSL_ENV, "true").strip(),
            "timeout_seconds": os.getenv(
                NATS_TIMEOUT_SECONDS_ENV, str(DEFAULT_NATS_TIMEOUT_SECONDS)
            ).strip(),
        }
    )


def nats_is_available(sources: dict[str, dict]) -> bool:
    """Check if NATS integration credentials are present."""
    return bool(sources.get("nats", {}).get("url"))


def nats_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract NATS credentials from resolved integrations."""
    nats = sources.get("nats", {})
    return {
        "url": nats.get("url", ""),
        "username": nats.get("username", ""),
        "password": nats.get("password", ""),
        "verify_ssl": nats.get("verify_ssl", True),
    }


def classify(credentials: dict[str, Any], record_id: str) -> tuple[NatsConfig | None, str | None]:
    try:
        cfg = build_nats_config(
            {
                "url": credentials.get("url", ""),
                "username": credentials.get("username", ""),
                "password": credentials.get("password", ""),
                "verify_ssl": credentials.get("verify_ssl", True),
                "timeout_seconds": credentials.get("timeout_seconds", DEFAULT_NATS_TIMEOUT_SECONDS),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="nats", record_id=record_id)
        return None, None
    if cfg.is_configured:
        return cfg, "nats"
    return None, None
