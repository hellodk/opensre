"""Aerospike integration verifier."""

from __future__ import annotations

from integrations.aerospike import build_aerospike_config, validate_aerospike_config
from integrations.verification import register_validation_verifier

verify_aerospike = register_validation_verifier(
    "aerospike",
    build_config=build_aerospike_config,
    validate_config=validate_aerospike_config,
)
