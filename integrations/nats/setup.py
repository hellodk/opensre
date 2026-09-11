"""What NATS needs before it is considered configured."""

from __future__ import annotations

from config.constants.nats import (
    NATS_MONITOR_PASSWORD_ENV,
    NATS_MONITOR_URL_ENV,
    NATS_MONITOR_USERNAME_ENV,
    NATS_VERIFY_SSL_ENV,
)
from integrations.nats.verifier import verify_nats
from integrations.setup_flow import IntegrationSetupSpec, SetupField

URL_FIELD = "url"
USERNAME_FIELD = "username"
PASSWORD_FIELD = "password"
VERIFY_SSL_FIELD = "verify_ssl"

NATS_SETUP = IntegrationSetupSpec(
    service="nats",
    fields=(
        SetupField(
            name=URL_FIELD,
            label="Monitoring URL",
            prompt="nats-server HTTP monitoring URL (e.g. http://nats.example.net:8222)",
            env_var=NATS_MONITOR_URL_ENV,
        ),
        SetupField(
            name=USERNAME_FIELD,
            label="Proxy username",
            prompt="Basic-auth username if a proxy protects the monitoring port (leave blank otherwise)",
            env_var=NATS_MONITOR_USERNAME_ENV,
            required=False,
        ),
        SetupField(
            name=PASSWORD_FIELD,
            label="Proxy password",
            prompt="Basic-auth password for that proxy",
            env_var=NATS_MONITOR_PASSWORD_ENV,
            secret=True,
            required=False,
        ),
        SetupField(
            name=VERIFY_SSL_FIELD,
            label="Verify TLS certificate",
            prompt="Verify the TLS certificate? (true/false)",
            env_var=NATS_VERIFY_SSL_ENV,
            default="true",
            required=False,
        ),
    ),
    verify=verify_nats,
)

__all__ = [
    "NATS_SETUP",
    "PASSWORD_FIELD",
    "URL_FIELD",
    "USERNAME_FIELD",
    "VERIFY_SSL_FIELD",
]
