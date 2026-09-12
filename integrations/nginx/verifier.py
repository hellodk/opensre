"""nginx integration verifier."""

from __future__ import annotations

from integrations.nginx import build_nginx_config, validate_nginx_config
from integrations.verification import register_validation_verifier

verify_nginx = register_validation_verifier(
    "nginx",
    build_config=build_nginx_config,
    validate_config=validate_nginx_config,
)
