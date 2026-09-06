"""What YugabyteDB needs before it is considered configured.

``ssl_mode`` used to be gathered via a CLI select menu; every other field was
always asked. Treating it as a defaulted text field keeps the same credentials
without a branching prompt feature.
"""

from __future__ import annotations

from config.constants.yugabytedb import (
    YUGABYTEDB_DATABASE_ENV,
    YUGABYTEDB_HOST_ENV,
    YUGABYTEDB_PASSWORD_ENV,
    YUGABYTEDB_PORT_ENV,
    YUGABYTEDB_SSL_MODE_ENV,
    YUGABYTEDB_USERNAME_ENV,
)
from integrations.setup_flow import IntegrationSetupSpec, SetupField
from integrations.yugabytedb import DEFAULT_YUGABYTEDB_SSL_MODE, DEFAULT_YUGABYTEDB_USER
from integrations.yugabytedb.verifier import verify_yugabytedb

HOST_FIELD = "host"
PORT_FIELD = "port"
DATABASE_FIELD = "database"
USERNAME_FIELD = "username"
PASSWORD_FIELD = "password"
SSL_MODE_FIELD = "ssl_mode"

YUGABYTEDB_SETUP = IntegrationSetupSpec(
    service="yugabytedb",
    fields=(
        SetupField(
            name=HOST_FIELD,
            label="Host",
            prompt="Host (e.g. localhost or yugabytedb.example.com)",
            env_var=YUGABYTEDB_HOST_ENV,
        ),
        SetupField(
            name=DATABASE_FIELD,
            label="Database name",
            env_var=YUGABYTEDB_DATABASE_ENV,
        ),
        SetupField(
            name=PORT_FIELD,
            label="Port",
            env_var=YUGABYTEDB_PORT_ENV,
            default="5433",
        ),
        SetupField(
            name=USERNAME_FIELD,
            label="Username",
            env_var=YUGABYTEDB_USERNAME_ENV,
            default=DEFAULT_YUGABYTEDB_USER,
        ),
        SetupField(
            name=PASSWORD_FIELD,
            label="Password",
            env_var=YUGABYTEDB_PASSWORD_ENV,
            secret=True,
            required=False,
        ),
        SetupField(
            name=SSL_MODE_FIELD,
            label="SSL mode",
            prompt="SSL mode (prefer, require, or disable)",
            env_var=YUGABYTEDB_SSL_MODE_ENV,
            default=DEFAULT_YUGABYTEDB_SSL_MODE,
        ),
    ),
    verify=verify_yugabytedb,
)

__all__ = [
    "DATABASE_FIELD",
    "HOST_FIELD",
    "PASSWORD_FIELD",
    "PORT_FIELD",
    "SSL_MODE_FIELD",
    "USERNAME_FIELD",
    "YUGABYTEDB_SETUP",
]
