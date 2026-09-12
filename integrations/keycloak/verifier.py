"""Keycloak integration verifier."""

from __future__ import annotations

from integrations.keycloak.config import build_keycloak_config
from integrations.keycloak.validation import validate_keycloak_config
from integrations.verification import register_validation_verifier

verify_keycloak = register_validation_verifier(
    "keycloak",
    build_config=build_keycloak_config,
    validate_config=validate_keycloak_config,
)
