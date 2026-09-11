"""NATS integration verifier."""

from __future__ import annotations

from integrations.nats import build_nats_config, validate_nats_config
from integrations.verification import register_validation_verifier

verify_nats = register_validation_verifier(
    "nats",
    build_config=build_nats_config,
    validate_config=validate_nats_config,
)
