"""What Aerospike needs before it is considered configured."""

from __future__ import annotations

from config.constants.aerospike import (
    AEROSPIKE_HOST_ENV,
    AEROSPIKE_PASSWORD_ENV,
    AEROSPIKE_PORT_ENV,
    AEROSPIKE_USERNAME_ENV,
)
from integrations.aerospike.verifier import verify_aerospike
from integrations.setup_flow import IntegrationSetupSpec, SetupField

HOST_FIELD = "host"
PORT_FIELD = "port"
USERNAME_FIELD = "username"
PASSWORD_FIELD = "password"

AEROSPIKE_SETUP = IntegrationSetupSpec(
    service="aerospike",
    fields=(
        SetupField(
            name=HOST_FIELD,
            label="Host",
            prompt="Host (e.g. localhost or aerospike.example.net)",
            env_var=AEROSPIKE_HOST_ENV,
        ),
        SetupField(
            name=PORT_FIELD,
            label="Port",
            env_var=AEROSPIKE_PORT_ENV,
            default="3000",
        ),
        SetupField(
            name=USERNAME_FIELD,
            label="Username",
            prompt="Username (leave blank unless security is enabled)",
            env_var=AEROSPIKE_USERNAME_ENV,
            required=False,
        ),
        SetupField(
            name=PASSWORD_FIELD,
            label="Password",
            prompt="Password (leave blank unless security is enabled)",
            env_var=AEROSPIKE_PASSWORD_ENV,
            secret=True,
            required=False,
        ),
    ),
    verify=verify_aerospike,
)

__all__ = [
    "AEROSPIKE_SETUP",
    "HOST_FIELD",
    "PASSWORD_FIELD",
    "PORT_FIELD",
    "USERNAME_FIELD",
]
