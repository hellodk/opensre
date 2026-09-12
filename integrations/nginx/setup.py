"""What nginx needs before it is considered configured."""

from __future__ import annotations

from config.constants.nginx import (
    NGINX_ACCESS_LOG_PATH_ENV,
    NGINX_API_PATH_ENV,
    NGINX_ERROR_LOG_PATH_ENV,
    NGINX_HOST_ENV,
    NGINX_PASSWORD_ENV,
    NGINX_PORT_ENV,
    NGINX_SSL_ENV,
    NGINX_STUB_STATUS_PATH_ENV,
    NGINX_USERNAME_ENV,
    NGINX_VERIFY_SSL_ENV,
)
from integrations.nginx.verifier import verify_nginx
from integrations.setup_flow import IntegrationSetupSpec, SetupField

HOST_FIELD = "host"
PORT_FIELD = "port"
SSL_FIELD = "ssl"
VERIFY_SSL_FIELD = "verify_ssl"
STUB_STATUS_PATH_FIELD = "stub_status_path"
API_PATH_FIELD = "api_path"
USERNAME_FIELD = "username"
PASSWORD_FIELD = "password"
ACCESS_LOG_PATH_FIELD = "access_log_path"
ERROR_LOG_PATH_FIELD = "error_log_path"

NGINX_SETUP = IntegrationSetupSpec(
    service="nginx",
    fields=(
        SetupField(
            name=HOST_FIELD,
            label="Host",
            prompt="Host (e.g. localhost or nginx.example.net)",
            env_var=NGINX_HOST_ENV,
        ),
        SetupField(
            name=PORT_FIELD,
            label="Port",
            env_var=NGINX_PORT_ENV,
            default="80",
        ),
        SetupField(
            name=SSL_FIELD,
            label="Use HTTPS",
            prompt="Use HTTPS to reach nginx? (true/false)",
            env_var=NGINX_SSL_ENV,
            default="false",
        ),
        SetupField(
            name=VERIFY_SSL_FIELD,
            label="Verify TLS certificate",
            prompt="Verify the TLS certificate? (true/false)",
            env_var=NGINX_VERIFY_SSL_ENV,
            default="true",
        ),
        SetupField(
            name=STUB_STATUS_PATH_FIELD,
            label="stub_status path",
            prompt="stub_status location (open-source nginx)",
            env_var=NGINX_STUB_STATUS_PATH_ENV,
            default="/nginx_status",
        ),
        SetupField(
            name=API_PATH_FIELD,
            label="Plus API path",
            prompt="NGINX Plus API location (ignored on open-source nginx)",
            env_var=NGINX_API_PATH_ENV,
            default="/api",
        ),
        SetupField(
            name=USERNAME_FIELD,
            label="Username",
            prompt="Basic-auth username (leave blank if the status endpoints are open)",
            env_var=NGINX_USERNAME_ENV,
            required=False,
        ),
        SetupField(
            name=PASSWORD_FIELD,
            label="Password",
            prompt="Basic-auth password (leave blank if the status endpoints are open)",
            env_var=NGINX_PASSWORD_ENV,
            required=False,
            secret=True,
        ),
        SetupField(
            name=ACCESS_LOG_PATH_FIELD,
            label="Access log path",
            prompt="Local access.log path (leave default if logs are not on this host)",
            env_var=NGINX_ACCESS_LOG_PATH_ENV,
            default="/var/log/nginx/access.log",
        ),
        SetupField(
            name=ERROR_LOG_PATH_FIELD,
            label="Error log path",
            prompt="Local error.log path",
            env_var=NGINX_ERROR_LOG_PATH_ENV,
            default="/var/log/nginx/error.log",
        ),
    ),
    verify=verify_nginx,
)

__all__ = [
    "ACCESS_LOG_PATH_FIELD",
    "API_PATH_FIELD",
    "ERROR_LOG_PATH_FIELD",
    "HOST_FIELD",
    "NGINX_SETUP",
    "PASSWORD_FIELD",
    "PORT_FIELD",
    "SSL_FIELD",
    "STUB_STATUS_PATH_FIELD",
    "USERNAME_FIELD",
    "VERIFY_SSL_FIELD",
]
