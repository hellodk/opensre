"""Keycloak connection settings and credential resolution."""

from __future__ import annotations

import logging
import os
from typing import Any

from pydantic import Field, ValidationInfo, field_validator, model_validator

from config.constants.keycloak import (
    KEYCLOAK_AUTH_REALM_ENV,
    KEYCLOAK_CLIENT_ID_ENV,
    KEYCLOAK_CLIENT_SECRET_ENV,
    KEYCLOAK_MANAGEMENT_URL_ENV,
    KEYCLOAK_REALM_ENV,
    KEYCLOAK_TIMEOUT_SECONDS_ENV,
    KEYCLOAK_URL_ENV,
    KEYCLOAK_VERIFY_SSL_ENV,
)
from config.llm_credentials import resolve_env_credential
from config.strict_config import StrictConfigModel
from infrastructure.text.coercion import safe_int
from integrations._validation_helpers import report_classify_failure

logger = logging.getLogger(__name__)

DEFAULT_KEYCLOAK_TIMEOUT_SECONDS = 10
_TRUTHY = ("true", "1", "yes")
_ALLOWED_SCHEMES = ("http://", "https://")


class KeycloakConfig(StrictConfigModel):
    """Normalized Keycloak Admin REST API connection settings."""

    url: str = ""
    management_url: str = ""
    realm: str = ""
    auth_realm: str = ""
    client_id: str = ""
    client_secret: str = ""
    verify_ssl: bool = True
    timeout_seconds: int = Field(default=DEFAULT_KEYCLOAK_TIMEOUT_SECONDS, gt=0)
    integration_id: str = ""

    @field_validator("*", mode="before")
    @classmethod
    def _strip_string_values(  # type: ignore[override]
        cls, value: Any, info: ValidationInfo
    ) -> Any:
        # Shadow StrictConfigModel's blanket strip: whitespace in the client
        # secret is significant and must survive validation untouched.
        if info.field_name == "client_secret":
            return value
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("url", "management_url", mode="before")
    @classmethod
    def _normalize_url(cls, value: Any) -> str:
        raw = str(value or "").strip().rstrip("/")
        if raw and not raw.startswith(_ALLOWED_SCHEMES):
            raise ValueError("Keycloak URL must start with http:// or https://")
        return raw

    @field_validator("realm", "auth_realm", "client_id", mode="before")
    @classmethod
    def _normalize_name(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("client_secret", mode="before")
    @classmethod
    def _normalize_secret(cls, value: Any) -> str:
        # Do NOT strip secrets — leading/trailing whitespace is significant.
        return str(value or "")

    @field_validator("verify_ssl", mode="before")
    @classmethod
    def _normalize_verify_ssl(cls, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in _TRUTHY

    @field_validator("timeout_seconds", mode="before")
    @classmethod
    def _normalize_timeout(cls, value: Any) -> int:
        return safe_int(value, DEFAULT_KEYCLOAK_TIMEOUT_SECONDS)

    @model_validator(mode="after")
    def _default_auth_realm(self) -> KeycloakConfig:
        if not self.auth_realm:
            self.auth_realm = self.realm
        return self

    @property
    def is_configured(self) -> bool:
        """True when the four required connection values are all present."""
        return bool(self.url and self.realm and self.client_id and self.client_secret)

    @property
    def token_path(self) -> str:
        """OpenID Connect token endpoint path for the auth realm."""
        return f"/realms/{self.auth_realm}/protocol/openid-connect/token"

    @property
    def admin_realm_path(self) -> str:
        """Admin REST API base path for the target realm."""
        return f"/admin/realms/{self.realm}"

    @property
    def has_management_url(self) -> bool:
        """True when the :9000 health/metrics interface is configured."""
        return bool(self.management_url)


def build_keycloak_config(raw: dict[str, Any] | None) -> KeycloakConfig:
    """Build a normalized Keycloak config object from env/store data."""
    return KeycloakConfig.model_validate(raw or {})


def keycloak_config_from_env() -> KeycloakConfig | None:
    """Load a Keycloak config from env vars."""
    url = os.getenv(KEYCLOAK_URL_ENV, "").strip()
    realm = os.getenv(KEYCLOAK_REALM_ENV, "").strip()
    client_id = os.getenv(KEYCLOAK_CLIENT_ID_ENV, "").strip()
    if not url or not realm or not client_id:
        return None
    client_secret = resolve_env_credential(KEYCLOAK_CLIENT_SECRET_ENV) or ""
    if not client_secret:
        return None
    return build_keycloak_config(
        {
            "url": url,
            "management_url": os.getenv(KEYCLOAK_MANAGEMENT_URL_ENV, "").strip(),
            "realm": realm,
            "auth_realm": os.getenv(KEYCLOAK_AUTH_REALM_ENV, "").strip(),
            "client_id": client_id,
            "client_secret": client_secret,
            "verify_ssl": os.getenv(KEYCLOAK_VERIFY_SSL_ENV, "true").strip().lower() in _TRUTHY,
            "timeout_seconds": safe_int(
                os.getenv(KEYCLOAK_TIMEOUT_SECONDS_ENV, str(DEFAULT_KEYCLOAK_TIMEOUT_SECONDS)),
                DEFAULT_KEYCLOAK_TIMEOUT_SECONDS,
            ),
        }
    )


def keycloak_is_available(sources: dict[str, dict]) -> bool:
    """Check if Keycloak integration credentials are present."""
    kc = sources.get("keycloak", {})
    return bool(
        kc.get("url") and kc.get("realm") and kc.get("client_id") and kc.get("client_secret")
    )


def keycloak_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract Keycloak credentials from resolved integrations."""
    kc = sources.get("keycloak", {})
    return {
        "url": kc.get("url", ""),
        "management_url": kc.get("management_url", ""),
        "realm": kc.get("realm", ""),
        "auth_realm": kc.get("auth_realm", ""),
        "client_id": kc.get("client_id", ""),
        "client_secret": kc.get("client_secret", ""),
        "verify_ssl": kc.get("verify_ssl", True),
    }


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[KeycloakConfig | None, str | None]:
    """Classify a store record into a Keycloak config when fully configured."""
    try:
        cfg = build_keycloak_config(
            {
                "url": credentials.get("url", ""),
                "management_url": credentials.get("management_url", ""),
                "realm": credentials.get("realm", ""),
                "auth_realm": credentials.get("auth_realm", ""),
                "client_id": credentials.get("client_id", ""),
                "client_secret": credentials.get("client_secret", ""),
                "verify_ssl": credentials.get("verify_ssl", True),
                "timeout_seconds": credentials.get(
                    "timeout_seconds", DEFAULT_KEYCLOAK_TIMEOUT_SECONDS
                ),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="keycloak", record_id=record_id)
        return None, None
    if cfg.is_configured:
        return cfg, "keycloak"
    return None, None
