"""YugabyteDB integration verifier."""

from __future__ import annotations

from integrations.verification import register_validation_verifier
from integrations.yugabytedb import build_yugabytedb_config, validate_yugabytedb_config

verify_yugabytedb = register_validation_verifier(
    "yugabytedb",
    build_config=build_yugabytedb_config,
    validate_config=validate_yugabytedb_config,
)
